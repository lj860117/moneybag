"""LLM 网关限流：拒绝原因诚实 + 突发窗口按用户隔离

背景（生产实测缺陷）
====================
`LLMGateway._check_limits()` 有**两个**返回 False 的理由（日限 / 突发限流），
但三处调用点（call_sync / stream_sync / call_multimodal）的熔断日志只打印日限：

    [LLM_GATEWAY] ⚠️ 熔断！daily=10/100

生产实际触发的是**突发限流**（daily 才 10/100，日限根本没到）。日志把运维
引向完全不相关的日限，几乎不可能定位。本项目的铁律是：

    **任何"拒绝/降级"都必须给出真实原因；报一个不是原因的原因，等同于撒谎。**

同时，旧实现的突发窗口 `self._burst_window` 挂在网关单例上，是**进程级全局**：
任一来源（另一个用户、一个脚本/测试）在 5 分钟内发 >10 次 LLM 调用，就会让
**所有用户**的对话降级成固定话术。本文件锁死修复后的两条行为：

1. 拒绝原因必须真实（burst 就说 burst，daily 就说 daily），日志同时给出两个
   闸门的真实数字，不能只写 daily=。
2. 突发窗口按 user_id 分桶：用户 A 耗尽额度不得殃及用户 B。
   日限 `DAILY_LIMIT` 仍保持**全局**（那是总成本闸门，语义不变）。
"""
from __future__ import annotations

import infra.llm.gateway as gwmod
from infra.llm.gateway import LLMGateway


def _fresh_gateway() -> LLMGateway:
    """每个用例用独立实例，避免污染进程级单例。"""
    return LLMGateway()


# --------------------------------------------------------------------------
# 1. 原因诚实：突发额度先耗尽（日限未达）→ 原因必须是 burst，不是 daily
# --------------------------------------------------------------------------

def test_refusal_reason_is_burst_when_burst_exhausted_not_daily():
    gw = _fresh_gateway()
    uid = "user-A"

    for _ in range(gwmod.BURST_LIMIT):
        assert gw.pre_check(user_id=uid) is True

    # 关键前提：日限远未到，只有突发限流触发
    assert gw._daily_count == gwmod.BURST_LIMIT
    assert gw._daily_count < gwmod.DAILY_LIMIT, "本用例前提是日限未达"

    assert gw._limit_refusal_reason(uid) == "burst"
    assert gw._limit_refusal_reason(uid) != "daily"


# --------------------------------------------------------------------------
# 2. 原因诚实：日限先耗尽 → 原因必须是 daily
# --------------------------------------------------------------------------

def test_refusal_reason_is_daily_when_daily_exhausted(monkeypatch):
    gw = _fresh_gateway()
    monkeypatch.setattr(gwmod, "DAILY_LIMIT", 2)
    monkeypatch.setattr(gwmod, "BURST_LIMIT", 50)  # 排除突发限流干扰

    assert gw.pre_check(user_id="u1") is True
    assert gw.pre_check(user_id="u2") is True
    assert gw._limit_refusal_reason("u3") == "daily"


def test_daily_limit_stays_global_across_users(monkeypatch):
    """日限是全局成本闸门，不能被按用户分桶改掉语义。"""
    gw = _fresh_gateway()
    monkeypatch.setattr(gwmod, "DAILY_LIMIT", 2)
    monkeypatch.setattr(gwmod, "BURST_LIMIT", 50)

    assert gw.pre_check(user_id="u1") is True
    assert gw.pre_check(user_id="u2") is True
    # 第三个**不同**用户同样被拒 —— 证明日限仍是全局共享
    assert gw.pre_check(user_id="u3") is False
    assert gw._limit_refusal_reason("u3") == "daily"


# --------------------------------------------------------------------------
# 3. 日志不误导：必须同时含突发与日限的真实数字，且点名真实原因
# --------------------------------------------------------------------------

def test_burst_refusal_log_contains_both_real_numbers(capsys):
    gw = _fresh_gateway()
    uid = "user-log"
    for _ in range(gwmod.BURST_LIMIT):
        gw.pre_check(user_id=uid)

    capsys.readouterr()  # 丢弃前面的输出，只看这次熔断
    result = gw.call_sync("你好", user_id=uid, module="test")
    out = capsys.readouterr().out

    assert result["source"] == "rate_limited"
    assert "熔断" in out
    # 点名真实原因（burst），而不是只写 daily=
    assert "突发" in out, f"熔断日志未点名突发限流：{out!r}"
    # 两个闸门的真实数字都在：daily 未达 + burst 已满
    assert f"daily={gw._daily_count}/{gwmod.DAILY_LIMIT}" in out, out
    assert f"burst={gwmod.BURST_LIMIT}/{gwmod.BURST_LIMIT}" in out, out


def test_daily_refusal_log_names_daily_not_burst(monkeypatch, capsys):
    gw = _fresh_gateway()
    monkeypatch.setattr(gwmod, "DAILY_LIMIT", 1)
    monkeypatch.setattr(gwmod, "BURST_LIMIT", 50)

    gw.pre_check(user_id="u1")  # 用掉唯一一次日限配额
    capsys.readouterr()
    result = gw.call_sync("你好", user_id="u1", module="test")
    out = capsys.readouterr().out

    assert result["source"] == "rate_limited"
    assert "日限" in out, out
    assert "突发" not in out, f"日限熔断却点名了突发：{out!r}"
    assert f"daily={gw._daily_count}/{gwmod.DAILY_LIMIT}" in out, out
    assert f"burst={gw._burst_used('u1')}/{gwmod.BURST_LIMIT}" in out, out


# --------------------------------------------------------------------------
# 4. 按用户隔离（本次核心行为变更）：A 耗尽不得殃及 B
# --------------------------------------------------------------------------

def test_burst_exhaustion_isolated_per_user():
    gw = _fresh_gateway()
    for _ in range(gwmod.BURST_LIMIT):
        assert gw.pre_check(user_id="A") is True

    # A 被自己的突发额度拒掉
    assert gw.pre_check(user_id="A") is False
    assert gw._limit_refusal_reason("A") == "burst"
    # 但 B 完全不受影响 —— 旧实现这里是 False（进程级全局窗口）
    assert gw.pre_check(user_id="B") is True
    assert gw._limit_refusal_reason("B") is None


def test_call_sync_rate_limited_for_offending_user_only():
    """端到端：A 耗尽后 call_sync 对 A 降级；B 的限流闸门仍放行。

    这里只验证"限流闸门"本身（不触发真实 HTTP）：A 的调用在限流检查处即返回
    rate_limited（发生在任何网络请求之前），B 的拒绝原因必须是 None。
    """
    gw = _fresh_gateway()
    for _ in range(gwmod.BURST_LIMIT):
        gw.pre_check(user_id="A")

    blocked = gw.call_sync("你好", user_id="A", module="test")
    assert blocked["source"] == "rate_limited"
    assert blocked["fallback"] is True

    # B 不被限流闸门拒绝（原因 None 且有可用配额）
    assert gw._limit_refusal_reason("B") is None
    assert gw._check_limits("B") is True


# --------------------------------------------------------------------------
# 5. 反空转：确认守卫确实打到熔断路径，而不是"闸门空转仍显绿"
# --------------------------------------------------------------------------

def test_guard_actually_trips_on_burst(capsys):
    gw = _fresh_gateway()
    uid = "user-trip"
    for _ in range(gwmod.BURST_LIMIT):
        gw.pre_check(user_id=uid)

    reason = gw._limit_refusal_reason(uid)
    assert reason is not None, "闸门未触发，测试是空转的"
    assert reason == "burst"

    # 通过公开入口再次确认：真的被拒且返回的是降级态（内容为空，非编造答案）
    capsys.readouterr()
    result = gw.call_sync("你好", user_id=uid, module="test")
    assert result["content"] == ""
    assert result["source"] == "rate_limited"


def test_limits_contract_unchanged():
    """_check_limits() 对外契约不变：返回 bool，通过时消耗一次配额。"""
    gw = _fresh_gateway()
    assert gw._check_limits(user_id="u") is True
    assert gw._daily_count == 1
    # 无参调用仍需可用（旧调用点兼容），落入共享哨兵桶
    assert gw._check_limits() is True


def test_missing_user_id_falls_into_shared_sentinel_bucket(monkeypatch):
    gw = _fresh_gateway()
    monkeypatch.setattr(gwmod, "BURST_LIMIT", 2)
    for _ in range(2):
        assert gw.pre_check() is True
    # 不传 uid 的调用共享同一个哨兵桶：后面的无 uid 调用被限
    assert gw.pre_check() is False
    assert gwmod._SHARED_BURST_BUCKET in gw._burst_windows


def test_burst_bucket_count_is_capped(monkeypatch):
    """桶数量上限保护：不能因 user_id 无界增长把内存撑爆。"""
    gw = _fresh_gateway()
    monkeypatch.setattr(gwmod, "BURST_BUCKET_MAX", 3)
    for i in range(3):
        gw.pre_check(user_id=f"u{i}")
    # 第 4 个用户触发上限：无过期桶可清 → 归入共享哨兵桶
    gw.pre_check(user_id="u-extra")
    assert gwmod._SHARED_BURST_BUCKET in gw._burst_windows
    assert len(gw._burst_windows) <= 4

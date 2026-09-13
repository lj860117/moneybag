#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试进程「永不真实推送企微」守卫（FIX 2026-09-13）

事故（真实发生，2026-09-13 19:35）：
  backend/tests/test_chat_model_routing.py 用 fake httpx 造出 HTTP 402
  （`_FakeResponse(402, {"error": "doubao quota exceeded"})`），gateway 回退
  分支把它当真实失败交给 `services.llm_quota_alert.maybe_alert_quota`；
  而 402 在 classify 里是**硬判定**直落 P0 现金欠费，于是
  「💳 豆包（火山引擎 ARK）余额告警（确证欠费信号）」被推到了用户企微。
  **豆包实际没欠费**（生产 Key 直连 ARK 实测 HTTP 200）—— 这是一条纯由测试
  mock 造出来的假告警。决定性指纹：告警显示「错误码 -」，因为这个 mock 的
  `error` 是字符串不是 dict，`_extract_error_code()` 解析不出 code。

本文件锁死两道拦截，并配故障注入（摘掉任一道都必须转红）：
  L1 入口短路 —— services/llm_quota_alert.py 的 maybe_alert_quota：
     测试环境 + 发送函数仍是生产实现 ⇒ 只记日志，不推送、不写去重状态。
  L2 网络出口 —— backend/tests/conftest.py 的 autouse fixture：
     卡死 wxwork_push._http_client 的 get/post，任何调用方都发不出去。

⚠️ L1 的位置也是本文件的守卫对象（2026-09-13 QA 复核提出）：
  短路必须排在「P1/P2 免打扰窗口判断」**之前**。排在之后的话，23:00–08:00
  之间跑测试时 P1/P2 会先被窗口 `return` 掉，守卫压根不执行 —— 用例变绿只是
  因为「没走到推送」，典型**空转的绿**。对应用例
  `test_guard_runs_before_quiet_hours_window_check`（把窗口钉死为 False，
  不靠挂钟时间，任何时刻跑都必须有 `[QUOTA_ALERT][TEST_MODE_BLOCKED]`）。
  生产语义零变化：短路唯一触发条件是 `_in_test_mode()`，生产进程恒 False。

⚠️ 全程离线：本文件任何用例都不得触达真实企微。需要"生产发送函数"时一律用
   `_install_production_like_sender()` 伪装，**绝不换回真实函数**。
"""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import llm_quota_alert as qa  # noqa: E402
from services import wxwork_push as wp  # noqa: E402

# 事故原样的输入：mock 造出来的 402，error 是字符串 → 错误码解析为空
MOCK_402_BODY = '{"error": "doubao quota exceeded"}'
# 对照组：带硬信号的真欠费（P0）。P0 不受免打扰窗口限制，用来证明守卫在
# 「任何优先级、任何时刻」都执行，而不只是碰巧走到的那条分支。
P0_ARREARS_BODY = '{"error": {"code": "PaymentRequired", "message": "Payment Required"}}'


def _load_monitor_module():
    """按文件路径加载 scripts/llm_balance_monitor.py（scripts 不是包）。

    与 test_llm_quota_alert_classify.py 里的同名工具保持一致。
    """
    import importlib.util

    path = os.path.join(os.path.dirname(__file__), "..", "scripts",
                        "llm_balance_monitor.py")
    spec = importlib.util.spec_from_file_location("_kd_llm_balance_monitor_guard", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001 - 环境缺依赖时交给调用方 skip
        return None
    return module


def _install_production_like_sender(monkeypatch, calls: list):
    """把一个 spy **伪装成生产发送函数**装进 services.wxwork_push。

    为什么必须伪装（`__module__ = "services.wxwork_push"`）：
      L1 的判据就是「发送函数是否定义在生产模块里」。不伪装的话 spy 会被当成
      测试假件放行 —— 这条用例就永远绿，等于没写（闸门空转仍显绿）。

    为什么**绝不**在这里换回真实函数：
      一旦拦截被摘掉，断言失败的代价就是一条真实告警打到用户企微 —— 正是本次
      事故的原貌。伪装成生产实现的 spy 既能让守卫转红，又把最坏结果锁在进程内。
    """
    def _spy(uid, content, title=""):
        calls.append({"uid": uid, "title": title, "content": content})
        return {"ok": True, "data": {"errcode": 0}}

    _spy.__module__ = "services.wxwork_push"
    monkeypatch.setattr(wp, "send_daily_report_to", _spy, raising=True)
    monkeypatch.setattr(wp, "is_configured", lambda: True, raising=True)
    return _spy


# ============================================================
# L1：入口短路
# ============================================================
def test_maybe_alert_quota_never_calls_production_sender_in_test_mode(monkeypatch, capsys):
    """测试环境调用 maybe_alert_quota(doubao, 402, ...) 不得触碰生产发送函数。

    反空转：`calls == []` 只说明"没推"，也可能是"压根没走到推送那一步"（比如
    被免打扰窗口提前 return）。所以额外断言拦截日志真的打出来了 —— 守卫确实
    执行过，绿得有凭据。
    """
    calls = []
    _install_production_like_sender(monkeypatch, calls)

    qa.maybe_alert_quota(
        "doubao", 402, MOCK_402_BODY,
        model="doubao-seed-2-1-turbo-260628", module="chat",
    )

    assert calls == [], f"测试环境发出了真实推送：{calls}"
    out = capsys.readouterr().out
    assert "[QUOTA_ALERT][TEST_MODE_BLOCKED]" in out, \
        f"没推不等于被拦：守卫没执行（看日志才知道它走的是哪条 return）：{out}"


def test_blocked_alert_is_observable_in_logs(monkeypatch, capsys):
    """被拦下的告警必须打印日志，且带 alert_type / provider / model / module。

    静默丢弃是本项目最忌讳的「闸门空转仍显绿」—— 拦了却没人知道，下次出事
    还是查不到。
    """
    calls = []
    _install_production_like_sender(monkeypatch, calls)

    qa.maybe_alert_quota("doubao", 402, MOCK_402_BODY, model="m", module="chat")

    out = capsys.readouterr().out
    assert "[QUOTA_ALERT][TEST_MODE_BLOCKED]" in out, f"未打印拦截日志：{out}"
    # 只锁"日志里有 alert_type 且带真实值"，**不锁**它到底是 P0 还是 P2：
    #   本文件守的是"拦截"，告警等级由分类决定（test_llm_quota_alert_classify.py
    #   负责）。锁死具体 alert_type 会让每次分类调整都误伤拦截守卫。
    for token in (
        "alert_type=doubao_",
        "provider=doubao",
        "model=m",
        "module=chat",
        "status=402",
    ):
        assert token in out, f"拦截日志缺少 {token}：{out}"
    assert calls == []


def test_blocked_alert_writes_no_dedupe_state(monkeypatch, tmp_path, capsys):
    """短路时不写去重状态文件。

    ALERT_STATE_FILE 依赖 DATA_DIR，测试下被 conftest 隔离到临时目录 —— 写进去
    的状态永远进不了生产 /opt/moneybag/data，等于去重彻底失效（每跑一次测试就
    重推一条假告警）。所以短路分支干脆不读也不写。
    """
    state_file = tmp_path / "llm_alert_state.json"
    monkeypatch.setattr(qa, "ALERT_STATE_FILE", state_file, raising=True)

    calls = []
    _install_production_like_sender(monkeypatch, calls)
    qa.maybe_alert_quota("doubao", 402, MOCK_402_BODY, model="m", module="t")

    assert not state_file.exists(), "测试环境不应写出去重状态文件"
    assert calls == []
    # 反空转：状态文件没被写，也可能是"提前 return 了"而不是"被短路了"
    out = capsys.readouterr().out
    assert "[QUOTA_ALERT][TEST_MODE_BLOCKED]" in out, \
        f"守卫未执行，这条用例会变成空转的绿：{out}"


def test_test_mode_survives_env_removal(monkeypatch):
    """单个用例删掉环境变量也撤不掉测试模式（import 期已固化）。

    对应 conftest 里 DATA_DIR 那个 `if not os.environ.get("DATA_DIR")` 逃逸口的
    同一类教训：防护不能靠"调用方不会去动它"来保证。
    """
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("MONEYBAG_TEST_MODE", raising=False)
    assert qa._in_test_mode() is True


def test_production_semantics_unchanged_when_not_in_test_mode(monkeypatch, tmp_path):
    """非测试环境（显式 MONEYBAG_TEST_MODE=0）发货路径必须原样保留。

    成对用例（M15）：只写"注入后必须红"会让守卫越收越紧、误杀合法路径。
    这条锁住「短路只在测试环境生效」，防止哪天把生产告警一起短路掉。

    ⚠️ 必须自带状态文件隔离：本用例真的会走到 _mark_sent_today，写进共享
    状态文件会把后面同键的用例静默去重掉（顺序依赖的假红）。
    """
    calls = []
    monkeypatch.setattr(qa, "ALERT_STATE_FILE", tmp_path / "state.json", raising=True)
    # 事故原物现在是 P2，会走免打扰窗口判据 —— 固定成白天，避免用例随时间变脸
    monkeypatch.setattr(qa, "_in_push_window", lambda: True, raising=True)
    _install_production_like_sender(monkeypatch, calls)
    monkeypatch.setenv("MONEYBAG_TEST_MODE", "0")

    qa.maybe_alert_quota("doubao", 402, MOCK_402_BODY, model="m", module="t")

    assert len(calls) == 2, f"非测试环境应照常推给 2 个人：{calls}"


def test_legitimate_fake_sender_still_reaches_push_path(monkeypatch, tmp_path):
    """守卫不得误杀既有的「用假 sender 演练推送」合法用例。

    这类用例（test_llm_quota_alert_classify.py 里的 P0 半夜也推、P2 白天才推）
    是验证推送门禁的唯一手段；拦截判据只认「生产实现」，不认这些假件。
    """
    pushed = []
    monkeypatch.setattr(qa, "ALERT_STATE_FILE", tmp_path / "state.json", raising=True)
    monkeypatch.setattr(qa, "_in_push_window", lambda: True, raising=True)
    monkeypatch.setattr(wp, "is_configured", lambda: True, raising=True)
    monkeypatch.setattr(
        wp, "send_daily_report_to",
        lambda uid, content, title="": pushed.append((uid, title)),
        raising=True,
    )

    qa.maybe_alert_quota("doubao", 402, MOCK_402_BODY, model="m", module="t")

    assert len(pushed) == 2, f"合法假 sender 应照常被调到：{pushed}"


# ============================================================
# L1 的位置：守卫不能只在白天生效（时间炸弹回归，QA 2026-09-13 提出）
# ============================================================
def test_guard_fires_for_p0_when_window_is_closed(monkeypatch, capsys):
    """P0 这条支路也要盖：窗口钉死为「关」，守卫照样执行。

    下面「顺序契约」那组用例用 `_assert_alert_is_p1_or_p2()` 把输入限死在
    P1/P2（因为只有它们受窗口判断约束，注入才有意义）。这里补对照组 P0：
    P0 本身不受窗口限制，但守卫同样必须在推送之前拦下它 —— 证明守卫不是"只在
    某条分支上碰巧生效"。
    """
    alert_type, _, _ = qa.classify_llm_error_detail("doubao", 402, P0_ARREARS_BODY)
    assert qa.alert_priority(alert_type) == "P0", \
        f"注入前提失效：{alert_type} 不是 P0，这条用例会空转"

    calls = []
    _install_production_like_sender(monkeypatch, calls)
    monkeypatch.setattr(qa, "_in_push_window", lambda: False, raising=True)

    qa.maybe_alert_quota("doubao", 402, P0_ARREARS_BODY, model="m", module="chat")

    out = capsys.readouterr().out
    assert "[QUOTA_ALERT][TEST_MODE_BLOCKED]" in out, f"P0 未被守卫拦下：{out}"
    assert calls == [], f"P0 在测试环境发出了真实推送：{calls}"


def test_monitor_push_alert_also_blocked_in_test_mode(monkeypatch):
    """第二条出口：scripts/llm_balance_monitor.py::_push_alert 同样不得真推。

    它**不走** maybe_alert_quota（自己直接 `from services.wxwork_push import
    send_daily_report_to`），所以 maybe_alert_quota 那道短路管不到它 ——
    当天日志里 `model="m"` 的 [doubao_rate_limited|m] 就是这条链路的痕迹。
    """
    monitor = _load_monitor_module()
    if monitor is None:
        pytest.skip("llm_balance_monitor 无法加载（缺少依赖）")

    calls = []
    _install_production_like_sender(monkeypatch, calls)

    assert monitor._push_alert("测试告警标题", "正文") is False, \
        "测试环境应返回 False（未发送，不消费当日去重额度）"
    assert calls == []


# ============================================================
# L1 的**顺序契约**（FIX 2026-09-13 补位：守卫不能依赖"恰好在白天跑测试"）
# ============================================================
# maybe_alert_quota 里的判定顺序被锁死为：
#     P3 return → 测试环境短路 → P1/P2 推送窗口判断 → 当日去重 → 推送
#
# 短路原本排在「P1/P2 窗口判断」**之后**，缺陷是：凌晨 23:00–08:00 跑测试时，
# P1/P2 告警先被窗口判断 `return` 掉，短路**根本没机会执行** —— 用例显绿只是
# 因为压根没走到推送（空转的绿），守卫在凌晨整个失效。HTTP 出网为空是窗口挡
# 的，不是守卫拦的（QA 实测指纹：输出 QUIET_HOURS_DEFERRED 而非
# TEST_MODE_BLOCKED）。
#
# 下面用故障注入把这个顺序钉死，并成对锁住「生产语义必须零变化」。
P3_BODY = '{"error":{"code":"RateLimitReached","message":"rate limit"}}'


def _assert_alert_is_p1_or_p2(body: str = MOCK_402_BODY) -> str:
    """反空转前置断言：本组用例只在告警确为 P1/P2 时才有意义。

    P0 不走窗口判断、P3 直接 return —— 这两类输入下"窗口外"这个注入条件根本
    不生效，用例会变成"没异常就算过"的空转绿。
    """
    alert_type, _, _ = qa.classify_llm_error_detail("doubao", 402, body)
    priority = qa.alert_priority(alert_type)
    assert priority in ("P1", "P2"), (
        f"注入前提失效：{alert_type} 是 {priority}，窗口判断对它不适用 —— "
        f"这条用例已经空转，请换一个 P1/P2 的输入"
    )
    return priority


def test_guard_runs_before_quiet_hours_window_check(monkeypatch, capsys, tmp_path):
    """注入 A：P1/P2 + 强制窗口外 + 测试模式 ⇒ 必须 TEST_MODE_BLOCKED。

    短路若退回窗口判断之后，这里会输出 QUIET_HOURS_DEFERRED —— 那正是本次要
    修的缺陷（凌晨跑测试时守卫空转）。所以两条标记都断言：一条必须出现，另一
    条必须**不**出现，缺一个断言这条用例就能在缺陷版本上显绿。
    """
    priority = _assert_alert_is_p1_or_p2()

    calls = []
    monkeypatch.setattr(qa, "ALERT_STATE_FILE", tmp_path / "state.json", raising=True)
    monkeypatch.setattr(qa, "_in_push_window", lambda: False, raising=True)
    monkeypatch.delenv("MONEYBAG_TEST_MODE", raising=False)
    _install_production_like_sender(monkeypatch, calls)

    qa.maybe_alert_quota("doubao", 402, MOCK_402_BODY, model="m", module="t")

    out = capsys.readouterr().out
    assert "[QUOTA_ALERT][TEST_MODE_BLOCKED]" in out, (
        f"窗口外（模拟凌晨）测试环境未走守卫，守卫已空转（{priority}）：{out}"
    )
    assert "[QUOTA_ALERT][QUIET_HOURS_DEFERRED]" not in out, (
        f"P1/P2 被窗口判断先 return 了 —— 短路没排在窗口判断之前：{out}"
    )
    assert calls == [], f"测试环境发出了真实推送：{calls}"
    assert not (tmp_path / "state.json").exists(), "短路时不应写出去重状态文件"


def test_guard_does_not_reach_push_path_outside_window(monkeypatch, capsys, tmp_path):
    """注入 A 的补强：窗口外被拦时，日志里不得出现"已推送"痕迹。

    与上一条是同一场景的两个面：上一条证明"守卫执行了"，这一条证明"守卫执行
    完就停了"（没有继续落到推送分支）。
    """
    _assert_alert_is_p1_or_p2()

    calls = []
    monkeypatch.setattr(qa, "ALERT_STATE_FILE", tmp_path / "state.json", raising=True)
    monkeypatch.setattr(qa, "_in_push_window", lambda: False, raising=True)
    monkeypatch.delenv("MONEYBAG_TEST_MODE", raising=False)
    _install_production_like_sender(monkeypatch, calls)

    qa.maybe_alert_quota("doubao", 402, MOCK_402_BODY, model="m", module="t")

    out = capsys.readouterr().out
    assert "✅ 已推送告警" not in out, f"测试环境竟然走到了推送分支：{out}"
    assert calls == []


def test_production_quiet_hours_semantics_unchanged(monkeypatch, capsys, tmp_path):
    """注入 B（进程内）：非测试环境 + 窗口外 ⇒ 仍必须是 QUIET_HOURS_DEFERRED。

    这是 QA 与主理人的分歧点：挪动短路会不会顺手改掉"P1/P2 夜里不推"的生产
    语义？答案是不会 —— 短路的唯一触发条件 `_in_test_mode()` 在生产进程
    （uvicorn）恒为 False。本用例用显式 `MONEYBAG_TEST_MODE=0` 把当前 pytest
    进程切成"生产等价物"，断言窗口判断照旧 return。
    """
    _assert_alert_is_p1_or_p2()

    calls = []
    monkeypatch.setattr(qa, "ALERT_STATE_FILE", tmp_path / "state.json", raising=True)
    monkeypatch.setattr(qa, "_in_push_window", lambda: False, raising=True)
    _install_production_like_sender(monkeypatch, calls)
    monkeypatch.setenv("MONEYBAG_TEST_MODE", "0")
    # 反空转：注入前提必须是"确实不在测试模式"，否则这条用例等于没注入
    assert qa._in_test_mode() is False, "注入失效：仍在测试模式，这条用例会空转"

    qa.maybe_alert_quota("doubao", 402, MOCK_402_BODY, model="m", module="t")

    out = capsys.readouterr().out
    assert "[QUOTA_ALERT][QUIET_HOURS_DEFERRED]" in out, (
        f"生产语义被改坏了：夜里本该由窗口判断拦下，实际输出 {out}"
    )
    assert "[QUOTA_ALERT][TEST_MODE_BLOCKED]" not in out, (
        f"生产路径被短路误伤了（P1/P2 夜里不推的语义已被替换）：{out}"
    )
    assert calls == []


# 真正"非 pytest 进程"的注入 B：另起一个干净解释器，不设 MONEYBAG_TEST_MODE，
# sys.modules 里也没有 pytest —— 这是生产进程（uvicorn）最贴近的复现。
_PROD_SIM_SCRIPT = """
import os, sys

sys.path.insert(0, __BACKEND_DIR__)
from services import llm_quota_alert as qa

print("PROBE_TEST_MODE=" + str(qa._in_test_mode()))
print("PROBE_PYTEST_IN_SYS_MODULES=" + str("pytest" in sys.modules))

alert_type, _, _ = qa.classify_llm_error_detail("doubao", 402, MOCK_BODY)
print("PROBE_PRIORITY=" + str(qa.alert_priority(alert_type)))

qa._in_push_window = lambda: False
qa.maybe_alert_quota("doubao", 402, MOCK_BODY, model="m", module="t")
"""


def test_production_process_outside_window_still_defers(tmp_path):
    """注入 B（真子进程）：非 pytest 进程 + 窗口外 ⇒ 仍 QUIET_HOURS_DEFERRED。

    进程内那条用 `MONEYBAG_TEST_MODE=0` 模拟生产，终究还是"同一个 pytest 进程
    里改开关"。这条再补一层：另起一个干净解释器，环境变量里既没有
    PYTEST_CURRENT_TEST 也没有 MONEYBAG_TEST_MODE，sys.modules 里也没有
    pytest —— 与 uvicorn 下的生产进程同构。它必须仍然输出
    QUIET_HOURS_DEFERRED，证明短路前移对生产路径**零影响**。
    """
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    script = _PROD_SIM_SCRIPT.replace("__BACKEND_DIR__", repr(backend_dir)).replace(
        "MOCK_BODY", repr(MOCK_402_BODY)
    )

    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("PYTHONPATH", "PYTEST_CURRENT_TEST", "MONEYBAG_TEST_MODE")
    }
    # 状态文件隔离：绝不让子进程碰到生产 /opt/moneybag/data
    env["DATA_DIR"] = str(tmp_path / "data")
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=backend_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = proc.stdout + proc.stderr

    assert proc.returncode == 0, f"子进程异常退出：{out}"
    # 反空转：子进程必须真的是"生产同构"，否则这条用例就是换皮的空转
    assert "PROBE_TEST_MODE=False" in out, f"子进程仍在测试模式，注入失效：{out}"
    assert "PROBE_PYTEST_IN_SYS_MODULES=False" in out, f"子进程里有 pytest：{out}"
    assert "PROBE_PRIORITY=P" in out and "PROBE_PRIORITY=P3" not in out, (
        f"注入前提失效：告警不是 P1/P2，窗口判断对它不适用：{out}"
    )
    assert "[QUOTA_ALERT][QUIET_HOURS_DEFERRED]" in out, (
        f"生产语义被改坏了：非 pytest 进程夜里本该由窗口判断拦下，实际输出 {out}"
    )
    assert "[QUOTA_ALERT][TEST_MODE_BLOCKED]" not in out, (
        f"生产路径被短路误伤了：{out}"
    )


def test_p3_still_short_circuits_before_guard(monkeypatch, capsys, tmp_path):
    """顺序契约的第一环：P3 永远优先于守卫（限流只落日志，连守卫都不惊动）。

    新顺序是「P3 return → 守卫 → 窗口 → 去重」，第一环也要钉死：否则哪天有人
    把守卫再往上挪一格，P3 就会变成"测试环境被守卫拦下"，日志语义失真。
    """
    calls = []
    monkeypatch.setattr(qa, "ALERT_STATE_FILE", tmp_path / "state.json", raising=True)
    _install_production_like_sender(monkeypatch, calls)

    qa.maybe_alert_quota("doubao", 429, P3_BODY, model="m", module="t")

    out = capsys.readouterr().out
    assert "[QUOTA_ALERT][NO_PUSH:P3]" in out, f"P3 未走只落日志分支：{out}"
    assert "[QUOTA_ALERT][TEST_MODE_BLOCKED]" not in out, (
        f"P3 不该惊动测试环境守卫（顺序被挪错了）：{out}"
    )
    assert calls == []


# ============================================================
# L2：网络出口拦截（反空转断言 —— 确认 conftest fixture 真的生效）
# ============================================================
def test_conftest_blocker_is_active_on_http_egress(wecom_push_sink):
    """反空转：确认 conftest 的 HTTP 出口拦截真的装上了。

    如果 _block_real_wecom_push 这个 autouse fixture 被 skip/删掉，这里必须红 ——
    否则整套守卫就是"锁没挂门"（M15：比锁坏了更危险）。
    """
    with pytest.raises(RuntimeError) as exc:
        wp._http_client.get(
            "https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid=CID&corpsecret=SEC"
        )

    assert "[conftest]" in str(exc.value)
    assert wecom_push_sink, "被拦下的请求必须留下记录，静默丢弃 = 闸门空转"
    # ⚠️ 只记 path，绝不回显 corpid / corpsecret / access_token
    assert wecom_push_sink[0]["method"] == "GET"
    assert wecom_push_sink[0]["path"].endswith("/cgi-bin/gettoken")
    assert "corpsecret" not in wecom_push_sink[0]["path"]


def test_conftest_blocker_stops_real_send_even_without_entry_guard(
    monkeypatch, wecom_push_sink,
):
    """第二层独立生效：绕过入口短路直调真实发送函数，也必须被 HTTP 出口拦下。

    这条刻意**不**伪装 sender —— 走的是 wxwork_push 真实代码路径，验证即便 L1
    哪天失效，L2 也能把请求挡在进程内。返回值必须与"企微未配置"一致（not-ok），
    不能抛意外异常炸掉调用方。
    """
    monkeypatch.setattr(wp, "is_configured", lambda: True, raising=True)

    result = wp.send_text("守卫自测：这条消息绝不能外发", user_id="LeiJiang")

    assert result.get("ok") is False, f"应被拦下并返回 not-ok，实际 {result}"
    assert wecom_push_sink, "必须留下拦截记录"
    assert any(item["method"] == "GET" for item in wecom_push_sink), \
        "取 token 的 GET 必须被拦在出口"

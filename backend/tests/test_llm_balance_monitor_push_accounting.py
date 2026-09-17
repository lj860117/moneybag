#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/llm_balance_monitor.py 的「推送记账」守卫（与 llm_quota_alert W2 同源）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
缺陷
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
`_push_alert()` 旧实现 `send_daily_report_to(uid, ...)` **完全不看返回值**，
异常也只 LOG.warning 吞掉，然后无条件 `LOG.info("✅ 已推送")` + `return True`。
而调用方 `_emit_alert()` 正是拿这个 True 去 `_dedupe_mark_sent()`
（→ `services.llm_quota_alert.mark_alert_sent_today`）的。于是：

    「企微返回 ok=False 未送达」和「send 抛异常」都会消费当日去重额度
    ⇒ 用户一条没收到，系统却认为今天已经通知过了 ⇒ 告警当天永久丢失。

与 services/llm_quota_alert.py 的 W2 是同一个缺陷的两处副本。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
修后的语义（本文件钉死的对象）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- 至少送达 1 个收件人 → 返回 True → 记当日去重（当天不再推）
- 全部未送达（ok=False 或抛异常）→ 返回 False → **不记**去重，额度留着
- 假绿日志（`✅ 已推送`）只在真送达时打；送达判定复用
  `services.llm_quota_alert.push_delivered` —— 两条链路共用一个口径

⚠️ 这里**故意不验 30 分钟节流**：llm_quota_alert 挂在每次 LLM 失败上（会风暴），
本脚本的 cron 是 `5 8 * * *`（每天 8:05 一次），加节流只会让它更容易漏。
失败不记账就够了，别把节流逻辑照搬过来 —— 这条用注释钉住，防止后人"对齐"。

反空转凭据：每条用例都先断言 send **确实被调用过**；`_push_alert` 的返回值
断言必须配合 `_emit_alert` 的端到端用例（只断言返回值，验不到"有没有真的
拿它去记账"）。

⚠️ 全程离线：发送器一律换成进程内假件，任何用例都不得触达真实企微。
"""
import importlib.util
import logging
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

from services import llm_quota_alert as qa  # noqa: E402
from services import wxwork_push as wp  # noqa: E402

# P2（模型未开通）：不是 P3，能过 `_emit_alert` 里的优先级门禁 —— 用 P3 的话
# 用例会变成"被优先级提前 return"的空转绿。
ALERT_TYPE = "doubao_model_not_open"
TITLE = "测试告警标题"
CONTENT = "测试告警正文"


def _load_monitor(monkeypatch, tmp_path):
    """按文件路径加载 scripts/llm_balance_monitor.py（scripts 不是包）。

    LOG_DIR 指到 tmp：模块级会 mkdir + 挂 FileHandler，默认落在 backend/logs/
    （仓库目录）—— 测试不该往仓库里吐日志。
    """
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    path = os.path.join(
        os.path.dirname(__file__), "..", "scripts", "llm_balance_monitor.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_kd_llm_balance_monitor_acct", path
    )
    assert spec is not None and spec.loader is not None, "monitor 脚本定位失败"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install_scripted_sender(monkeypatch, pushed, outcomes):
    """按调用顺序编排每次 send 的返回值（或要抛的异常）。

    用完后默认返回 `{"ok": True}`：多推了会以"不该出现的成功"暴露，不会悄悄变绿。
    """
    def _fake(uid, content, title=""):
        pushed.append({"uid": uid, "title": title, "content": content})
        outcome = outcomes.pop(0) if outcomes else {"ok": True}
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(wp, "is_configured", lambda: True, raising=True)
    monkeypatch.setattr(wp, "send_daily_report_to", _fake, raising=True)


class _Env:
    def __init__(self, monitor, pushed, state_file):
        self.monitor = monitor
        self.pushed = pushed
        self.state_file = state_file

    def read_state(self) -> dict:
        import json

        return json.loads(self.state_file.read_text(encoding="utf-8"))

    def emit(self):
        self.monitor._emit_alert(ALERT_TYPE, TITLE, CONTENT, do_push=True)


@pytest.fixture
def env(monkeypatch, tmp_path):
    """统一的监视器测试环境：隔离状态文件 + 预留假发送器 + 反空转前置断言。"""
    monitor = _load_monitor(monkeypatch, tmp_path)
    state_file = tmp_path / "llm_alert_state.json"
    monkeypatch.setattr(qa, "ALERT_STATE_FILE", state_file, raising=True)

    pushed = []
    _install_scripted_sender(monkeypatch, pushed, [])

    # —— 反空转：入口守卫必须放行，否则所有"没推送/没记账"的断言都是空的 ——
    assert qa._in_test_mode() is True, "本组用例跑在测试进程里，前提成立"
    assert qa._is_production_sender(wp.send_daily_report_to) is False, (
        "假发送器被判成了生产实现 ⇒ 测试环境短路会拦下调用，"
        "本文件所有用例将变成空转的绿"
    )
    # 优先级门禁：ALERT_TYPE 不能是 P3，否则 _emit_alert 提前 return，用例空转
    assert monitor._priority_of(ALERT_TYPE) != "P3", \
        f"注入前提失效：{ALERT_TYPE} 是 P3，_emit_alert 不会走到推送"

    return _Env(monitor, pushed, state_file)


# ============================================================
# _push_alert 的返回值语义
# ============================================================
def test_all_failed_returns_false_and_logs_no_success(env, monkeypatch, caplog):
    """全部未送达：返回 False，且**不得**打「✅ 已推送」（假绿日志）。"""
    caplog.set_level(logging.INFO)
    _install_scripted_sender(monkeypatch, env.pushed, [{"ok": False}] * 4)

    assert env.monitor._push_alert(TITLE, CONTENT) is False

    # 反空转：send 真的被调用过（否则"返回 False"可能只是压根没走到推送）
    assert len(env.pushed) == len(env.monitor._ALERT_RECIPIENTS), \
        f"反空转：应尝试推给每个收件人：{env.pushed}"
    assert "✅" not in caplog.text, f"没送达却打了成功日志（假绿）：{caplog.text}"
    assert "[PUSH_FAILED]" in caplog.text, f"全失败必须留痕：{caplog.text}"


def test_exception_from_sender_returns_false(env, monkeypatch, caplog):
    """send 抛异常同样算未送达。"""
    caplog.set_level(logging.INFO)
    _install_scripted_sender(
        monkeypatch, env.pushed, [RuntimeError("boom")] * 4
    )

    assert env.monitor._push_alert(TITLE, CONTENT) is False

    assert len(env.pushed) == len(env.monitor._ALERT_RECIPIENTS), "反空转：应尝试过"
    assert "✅" not in caplog.text, f"抛异常却打了成功日志：{caplog.text}"


def test_partial_success_returns_true(env, monkeypatch, caplog):
    """第一个失败、第二个成功 ⇒ 返回 True（只要有一个人收到就算送达）。"""
    caplog.set_level(logging.INFO)
    _install_scripted_sender(
        monkeypatch, env.pushed, [{"ok": False}, {"ok": True}]
    )

    assert env.monitor._push_alert(TITLE, CONTENT) is True

    assert len(env.pushed) == 2, f"反空转：应尝试 2 次：{env.pushed}"
    assert "✅" in caplog.text, f"送达了就该打成功日志：{caplog.text}"
    assert "[PUSH_PARTIAL]" in caplog.text, \
        f"部分失败必须显式标注（不能装作全部成功）：{caplog.text}"


def test_unknown_return_value_counts_as_delivered(env, monkeypatch):
    """返回值不是 {"ok": ...}（含 None）时保守当送达。

    与 llm_quota_alert 的口径保持一致：既有测试假件多为返回 None 的 lambda，
    判成失败会让它们的用例退化成"没记账"的空转绿 —— 守卫不能靠改坏别人的
    合法路径来生效。
    """
    monkeypatch.setattr(wp, "is_configured", lambda: True, raising=True)
    monkeypatch.setattr(
        wp, "send_daily_report_to",
        lambda uid, content, title="": env.pushed.append({"uid": uid}),
        raising=True,
    )

    assert env.monitor._push_alert(TITLE, CONTENT) is True
    assert len(env.pushed) == len(env.monitor._ALERT_RECIPIENTS), "反空转：应尝试过"


def test_monitor_shares_delivery_verdict_with_quota_alert(env, monkeypatch):
    """送达判定必须复用 llm_quota_alert.push_delivered，不得各判各的。

    两条链路（事前巡检 / 事后告警）共用一个状态文件，若"什么算送达"有两套答案，
    就会出现同一条告警一边记账一边不记账，去重状态随之错位。
    """
    real = qa.push_delivered
    seen = []

    def _spy(result):
        seen.append(result)
        return real(result)

    monkeypatch.setattr(qa, "push_delivered", _spy, raising=True)
    _install_scripted_sender(monkeypatch, env.pushed, [{"ok": True}] * 4)

    assert env.monitor._push_alert(TITLE, CONTENT) is True

    assert len(seen) == len(env.monitor._ALERT_RECIPIENTS), (
        f"monitor 没有走共享的 push_delivered（自己在另判一套？）：{seen}"
    )


# ============================================================
# 端到端：_emit_alert 是否真的拿返回值决定记不记账
# ============================================================
def test_emit_alert_does_not_consume_quota_when_push_failed(env, monkeypatch, caplog):
    """★ 核心：全失败 ⇒ 不记当日去重（旧实现在这里把告警丢了）。"""
    caplog.set_level(logging.INFO)
    _install_scripted_sender(
        monkeypatch, env.pushed,
        [{"ok": False}, {"ok": False}, {"ok": False}, {"ok": True}],
    )

    env.emit()  # 第 1 次：全失败

    assert len(env.pushed) == 2, f"反空转：应尝试推给 2 个人：{env.pushed}"
    assert qa.was_alert_sent_today(ALERT_TYPE) is False, \
        "全失败不得消费当日去重额度（旧实现正是这里把告警丢了）"
    assert not env.state_file.exists(), "没送达就不该写出去重状态文件"

    env.emit()  # 第 2 次：部分成功

    assert len(env.pushed) == 4, f"第 2 次应继续尝试（不记账 = 还有机会）：{env.pushed}"
    assert qa.was_alert_sent_today(ALERT_TYPE) is True, "送达后应记当日去重"
    assert env.read_state()[ALERT_TYPE] == date.today().isoformat()

    env.emit()  # 第 3 次：当天已推过

    assert len(env.pushed) == 4, "已送达 ⇒ 当天不再推（原行为不变）"
    assert "今日已推送过" in caplog.text, f"应打印去重跳过日志：{caplog.text}"


def test_emit_alert_marks_sent_today_on_first_success(env, monkeypatch):
    """对照组：第一次就送达 ⇒ 当天不再推（防止守卫越收越紧、误杀正常路径）。"""
    _install_scripted_sender(monkeypatch, env.pushed, [{"ok": True}] * 4)

    env.emit()
    env.emit()

    assert len(env.pushed) == 2, f"已送达后当天不应再推：{env.pushed}"
    assert qa.was_alert_sent_today(ALERT_TYPE) is True


def test_emit_alert_respects_priority_gate(env, monkeypatch):
    """P3（限流）永不推送，也不记账 —— 优先级门禁不能被这次改动碰坏。"""
    p3_type = "doubao_rate_limited"
    assert env.monitor._priority_of(p3_type) == "P3", "注入前提失效：不是 P3"
    _install_scripted_sender(monkeypatch, env.pushed, [{"ok": True}] * 4)

    env.monitor._emit_alert(p3_type, TITLE, CONTENT, do_push=True)

    assert env.pushed == [], f"P3 不得推送：{env.pushed}"
    assert qa.was_alert_sent_today(p3_type) is False, "P3 也不得记账"

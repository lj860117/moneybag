#!/usr/bin/env python3
"""v9.9.30 LLM 出口接线（shadow 守卫）+ 月度金额预算闸门 —— 回归 + 故障注入。

背景（审计结论 A / C）：
  A. red_team_audit.py / chat_guard.py 全仓零生产 import，只在 CI / skeleton
     测试里被 import 过；文档却写着"拦截率 >99%"（28 条自选语料算出来的）。
     → 现在接在 gateway 的三个真实出口，默认 **shadow**（真实跑、真实计数、
       默认不拦），enforce 由 LLM_OUTPUT_GUARD_ENFORCE 控制。
  C. TOKEN_BUDGET.monthly_budget_rmb / on_exceed / max_input_per_call 全仓零引用，
     金额闸门"声明了但没人拦"。
     → 现在接成两级真实分支，数据源是既有 data/llm_usage/ 不可信？不，是既有记账。

故障注入（摘掉判据必须转红）：
  - 删掉 `_shadow_audit_output` 调用 → outputs_audited 计数不再增长 → 红；
  - 月度金额人为推超限 → 必须走降级/拒绝分支（本文件断言具体动作）。
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

import config
import infra.llm.gateway as gwmod
from infra.llm.gateway import LLMGateway

NORMAL_CONTENT = "市场偏弱，建议先别追高，注意控制风险。"
# 命中 red_team_audit 的"推荐/建议买入 + 个股名"正则
VIOLATING_CONTENT = "建议买入贵州茅台，现在正是好时机。"


class _FakeResponse:
    status_code = 200

    def __init__(self, content: str) -> None:
        self._content = content
        self.text = content

    def json(self) -> dict:
        return {
            "choices": [{
                "message": {"content": self._content, "reasoning_content": ""},
                "finish_reason": "stop",
            }],
            "usage": {"total_tokens": 12, "prompt_tokens": 8, "completion_tokens": 4},
            "model": "deepseek-v4-flash",
        }


class _FakeClient:
    """替代 httpx.Client：记录请求体，绝不发真实网络请求。"""

    captured: list[dict] = []
    content: str = NORMAL_CONTENT

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *args) -> bool:
        return False

    def post(self, url, headers=None, json=None):  # noqa: A002 – httpx 参数名
        type(self).captured.append(json or {})
        return _FakeResponse(type(self).content)


@pytest.fixture
def fake_http(monkeypatch):
    """注入假 httpx.Client + 假 key；默认关闭 enforce（shadow 模式）。"""
    _FakeClient.captured = []
    _FakeClient.content = NORMAL_CONTENT
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    monkeypatch.setenv("LLM_API_KEY", "test-key-not-real")
    monkeypatch.delenv(gwmod.GUARD_ENFORCE_ENV, raising=False)
    return _FakeClient


def _force_monthly_spend(gw: LLMGateway, spend: float, days: int = 5) -> None:
    """把月度金额汇总**人为推到超限**（避免依赖真实 data/ 目录）。"""
    gw._monthly_spend_rmb = lambda now=None: (spend, days)  # type: ignore[method-assign]


# ==========================================================================
# 任务 A：red_team_audit 在真实出口被真实调用（不是空转）
# ==========================================================================

def test_red_team_audit_counter_increases_on_real_exit(fake_http):
    """真实出口审计必须发生：计数器 delta >= 1。删掉接线即转红。"""
    gw = LLMGateway()
    before = gwmod.get_output_guard_stats()["outputs_audited"]

    res = gw.call_sync("用一句话点评今天的市场", user_id="u-a1",
                       module="close_review", max_tokens=200)

    assert res["source"] == "ai"
    after = gwmod.get_output_guard_stats()["outputs_audited"]
    assert after == before + 1, "red_team_audit 未在真实出口执行（空转）"


def test_shadow_mode_records_violation_but_does_not_alter_content(fake_http):
    """shadow：命中违规要**记数**，但内容一字不改（不误杀）。"""
    _FakeClient.content = VIOLATING_CONTENT
    gw = LLMGateway()
    before = gwmod.get_output_guard_stats()["output_violations"]

    res = gw.call_sync("给出你的操作建议", user_id="u-a2",
                       module="close_review", max_tokens=200)

    assert res["source"] == "ai"
    assert res["content"] == VIOLATING_CONTENT, "shadow 模式不应改动内容"
    assert gwmod.get_output_guard_stats()["output_violations"] == before + 1


def test_enforce_mode_blocks_violating_content(fake_http, monkeypatch):
    """enforce 开：违规输出不得交给调用方，且不写缓存。"""
    monkeypatch.setenv(gwmod.GUARD_ENFORCE_ENV, "1")
    _FakeClient.content = VIOLATING_CONTENT
    gw = LLMGateway()
    before = gwmod.get_output_guard_stats()["enforced_blocks"]

    res = gw.call_sync("给出你的操作建议", user_id="u-a3",
                       module="close_review", max_tokens=200)

    assert res["source"] == "red_team_blocked"
    assert res["content"] == ""
    assert res["fallback"] is True
    assert gwmod.get_output_guard_stats()["enforced_blocks"] == before + 1


def test_clean_content_passes_even_when_enforce_on(fake_http, monkeypatch):
    monkeypatch.setenv(gwmod.GUARD_ENFORCE_ENV, "1")
    _FakeClient.content = NORMAL_CONTENT
    gw = LLMGateway()

    res = gw.call_sync("用一句话点评今天的市场", user_id="u-a4",
                       module="close_review", max_tokens=200)

    assert res["source"] == "ai"
    assert res["content"] == NORMAL_CONTENT


def test_chat_guard_action_seeking_detected_on_chat_module(fake_http):
    """chat_guard.check_action_seeking 也接在真实出口上（chat 系模块）。"""
    gw = LLMGateway()
    before = gwmod.get_output_guard_stats()["chat_action_seeking"]

    res = gw.call_sync("我该怎么办？", user_id="u-a5", module="chat", max_tokens=200)

    assert res["source"] == "ai"
    assert gwmod.get_output_guard_stats()["chat_action_seeking"] == before + 1


def test_chat_guard_skipped_for_non_chat_module(fake_http):
    """非 chat 模块不该被诱导检测波及（作用域正确）。"""
    gw = LLMGateway()
    before = gwmod.get_output_guard_stats()["chat_prompts_checked"]

    gw.call_sync("我该怎么办？", user_id="u-a6", module="close_review", max_tokens=200)

    assert gwmod.get_output_guard_stats()["chat_prompts_checked"] == before


def test_guard_failure_never_breaks_llm_call(fake_http, monkeypatch):
    """守卫自身异常必须被吞掉，主链路照常返回。"""
    import infra.llm.red_team_audit as rta

    def _boom(_text):
        raise RuntimeError("guard exploded")

    monkeypatch.setattr(rta, "audit_response", _boom)
    gw = LLMGateway()
    before = gwmod.get_output_guard_stats()["guard_errors"]

    res = gw.call_sync("用一句话点评今天的市场", user_id="u-a7",
                       module="close_review", max_tokens=200)

    assert res["source"] == "ai"
    assert res["content"] == NORMAL_CONTENT
    assert gwmod.get_output_guard_stats()["guard_errors"] == before + 1


# ==========================================================================
# 任务 C：月度金额预算闸门真的拦停
# ==========================================================================

def test_monthly_budget_exceeded_degrades_without_network(fake_http):
    """月度 100% 超限 + on_exceed=degrade → 不调用 LLM，返回规则兜底态。"""
    gw = LLMGateway()
    _force_monthly_spend(gw, 999.0)

    res = gw.call_sync("点评一下今天的持仓", user_id="u-c1",
                       module="close_review", max_tokens=800)

    assert res["source"] == "budget_exceeded"
    assert res["fallback"] is True
    assert res["content"] == ""
    assert _FakeClient.captured == [], "超限后仍然发出了请求 → 闸门没生效"


def test_monthly_critical_degrades_by_constraining_output(fake_http):
    """月度 ≥ critical(90%) → 降级档 A：关 thinking + 压输出上限。"""
    gw = LLMGateway()
    _force_monthly_spend(gw, 28.0)  # 28/30 ≈ 93% ≥ 90%

    res = gw.call_sync("点评一下今天的持仓", user_id="u-c2",
                       module="close_review", max_tokens=800)

    assert res["source"] == "ai"
    assert res["budget_degraded"] is True
    body = _FakeClient.captured[-1]
    assert body["max_tokens"] == gwmod.DEGRADED_MAX_TOKENS
    assert body.get("thinking") == {"type": "disabled"}


def test_hard_stop_refuses(fake_http, monkeypatch):
    monkeypatch.setitem(config.TOKEN_BUDGET, "on_exceed", "hard_stop")
    gw = LLMGateway()
    _force_monthly_spend(gw, 999.0)

    res = gw.call_sync("点评一下今天的持仓", user_id="u-c3",
                       module="close_review", max_tokens=800)

    assert res["source"] == "budget_hard_stop"
    assert _FakeClient.captured == []


def test_warn_only_does_not_block(fake_http, monkeypatch):
    monkeypatch.setitem(config.TOKEN_BUDGET, "on_exceed", "warn_only")
    gw = LLMGateway()
    _force_monthly_spend(gw, 999.0)

    res = gw.call_sync("点评一下今天的持仓", user_id="u-c4",
                       module="close_review", max_tokens=800)

    assert res["source"] == "ai", "warn_only 只告警，不得拦停"
    assert len(_FakeClient.captured) == 1


def test_max_input_per_call_is_enforced(fake_http, monkeypatch):
    monkeypatch.setitem(config.TOKEN_BUDGET, "max_input_per_call", 10)
    gw = LLMGateway()

    res = gw.call_sync("这是一段明显超过十个 token 估算上限的输入文本内容",
                       user_id="u-c5", module="close_review", max_tokens=200)

    assert res["source"] == "input_over_budget"
    assert _FakeClient.captured == []


def test_below_budget_does_not_degrade(fake_http):
    """故障注入对照：金额未超限时必须放行（否则判据本身是坏的）。"""
    gw = LLMGateway()
    _force_monthly_spend(gw, 0.07)  # 贴近线上真实值

    res = gw.call_sync("点评一下今天的持仓", user_id="u-c6",
                       module="close_review", max_tokens=800)

    assert res["source"] == "ai"
    assert res["budget_degraded"] is False


def test_monthly_spend_reads_real_usage_files_and_scopes_month():
    """金额数据源 = 既有 data/llm_usage/YYYY-MM-DD.json，且不得跨月累加。"""
    usage_dir = Path(config.DATA_DIR) / "llm_usage"
    usage_dir.mkdir(parents=True, exist_ok=True)
    this_month = date.today().strftime("%Y-%m")
    prev_month = (date.today().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")

    (usage_dir / f"{this_month}-01.json").write_text(
        json.dumps({"cost_rmb": 1.25}), encoding="utf-8")
    (usage_dir / f"{this_month}-02.json").write_text(
        json.dumps({"cost_rmb": 0.75}), encoding="utf-8")
    # 上月文件：绝不能计入本月
    (usage_dir / f"{prev_month}-15.json").write_text(
        json.dumps({"cost_rmb": 100.0}), encoding="utf-8")

    gw = LLMGateway()
    spend, days = gw._monthly_spend_rmb()

    assert spend >= 2.0, f"未读到真实记账数据：{spend}"
    assert spend < 50.0, "跨月累加了 → 月度闸门会提前误触发"
    assert days >= 2


def test_check_budget_reports_monthly_truth():
    gw = LLMGateway()
    status = gw.check_budget()

    assert "monthly" in status
    assert status["monthly"]["source"] == "data/llm_usage/*.json"
    assert status["monthly"]["budget_rmb"] == config.TOKEN_BUDGET["monthly_budget_rmb"]
    assert status["on_exceed"] == config.TOKEN_BUDGET["on_exceed"]


def test_check_budget_exposes_real_guard_counts(fake_http):
    """健康检查必须给出守卫的**真实计数**（0 就说明空转，骗不了人）。"""
    gw = LLMGateway()
    before = gw.check_budget()["output_guard"]["outputs_audited"]

    gw.call_sync("用一句话点评今天的市场", user_id="u-c7",
                 module="close_review", max_tokens=200)

    after = gw.check_budget()["output_guard"]["outputs_audited"]
    assert after == before + 1
    assert gw.check_budget()["output_guard"]["enforce"] is False

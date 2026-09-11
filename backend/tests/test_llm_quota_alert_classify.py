#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
llm_quota_alert 错误分类回归测试（FIX 2026-09-12 豆包误报）

背景：旧 classify_llm_error 只要错误体里出现 "balance" / "insufficient" 就判成
「豆包账户余额已用尽」并推 P0 告警。用户控制台实测现金余额还有 ¥100，是误报。
火山引擎 ARK 至少有 4 种语义完全不同的错误都会带这些词：
  推理点用完 / 模型未开通 / 限流 / 账户真欠费。

本文件锁死分类边界，防止以后再退化回「凭子串猜」。

⚠️ 全程离线：任何用例都不得真的调用企微接口。需要验证推送行为时一律
   monkeypatch services.wxwork_push 的 is_configured / send_daily_report_to。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import llm_quota_alert as qa  # noqa: E402


# ============================================================
# 线上真实样本（2026-09-12 01:30 CST，服务器用生产 Key 直连 ARK 抓到的原文）
# ============================================================
ARK_MODEL_NOT_OPEN_BODY = (
    '{"error":{"code":"ModelNotOpen","message":"Your account 2127949875 has not '
    'activated the model doubao-seed-2-1-turbo-260628. Please activate the model '
    'service in the Ark Console. Request id: 02178914782456091be90b70b1bfc3059428bd07e108b7e89a414",'
    '"param":"","type":"Not Found"}}'
)
ARK_INSUFFICIENT_BALANCE_BODY = (
    '{"error":{"code":"InsufficientBalance","message":"Insufficient balance",'
    '"type":"Forbidden"}}'
)
ARK_FREE_TOKENS_BODY = (
    '{"error":{"code":"FreeTokensExhausted","message":"insufficient balance: '
    'free tokens of this model are used up","type":"Forbidden"}}'
)
ARK_RATE_LIMIT_BODY = (
    '{"error":{"code":"RateLimitExceeded","message":"Requests rate limit exceeded"}}'
)
ARK_INVALID_KEY_BODY = (
    '{"error":{"code":"InvalidApiKey","message":"invalid api key"}}'
)
DEEPSEEK_402_BODY = (
    '{"error":{"message":"Insufficient balance","type":"unknown_error","code":"402"}}'
)


# ============================================================
# 分类：现金欠费（P0，必须硬信号）
# ============================================================
def test_doubao_real_arrears_by_code():
    """ARK 明确返回 InsufficientBalance 错误码 → P0 现金欠费。"""
    assert qa.classify_llm_error("doubao", 403, ARK_INSUFFICIENT_BALANCE_BODY) == \
        "doubao_balance_exhausted"


def test_doubao_http_402_is_arrears():
    """HTTP 402 Payment Required 本身就是硬信号。"""
    assert qa.classify_llm_error("doubao", 402, "Payment Required") == \
        "doubao_balance_exhausted"


def test_doubao_chinese_arrears_word():
    """中文「欠费」字样 → 现金欠费。"""
    assert qa.classify_llm_error("doubao", 400, "账户已欠费，请充值") == \
        "doubao_balance_exhausted"


def test_deepseek_402_is_arrears():
    assert qa.classify_llm_error("deepseek", 402, DEEPSEEK_402_BODY) == \
        "deepseek_balance_exhausted"


# ============================================================
# 分类：推理点 / 免费额度用完（P2，非现金）
# ============================================================
def test_free_tokens_exhausted_is_not_cash_arrears():
    """核心回归点：带 'insufficient balance' 但语义是免费 tokens 用完。

    旧逻辑在这里会判成 doubao_balance_exhausted 并推「账户余额已用尽」，
    而用户现金余额其实还有钱 —— 这正是 2026-09-12 误报的形态。
    """
    assert qa.classify_llm_error("doubao", 400, ARK_FREE_TOKENS_BODY) == \
        "doubao_quota_exhausted"


def test_bare_insufficient_balance_without_hard_signal_is_not_p0():
    """无错误码、无 402、无欠费字样，只有模糊英文 → 不判 P0。"""
    got = qa.classify_llm_error("doubao", 400, "insufficient balance")
    assert got == "doubao_quota_exhausted"
    assert qa.ALERT_PRIORITY[got] != "P0"


def test_chinese_inference_points_exhausted():
    """中文「推理点」→ 额度类，不是现金欠费。"""
    assert qa.classify_llm_error("doubao", 400, "您的推理点已用完，请购买推理点包") == \
        "doubao_quota_exhausted"


# ============================================================
# 分类：模型未开通（P2，充值无用）
# ============================================================
def test_live_model_not_open_sample():
    """线上真实 404 ModelNotOpen 样本（原样贴入）。

    旧逻辑：404 不满足任何分支 → 返回 None → 完全静默，没人知道豆包降级链是断的。
    新逻辑：判为 model_not_open，白天推送并说明「充值无用」。
    """
    assert qa.classify_llm_error("doubao", 404, ARK_MODEL_NOT_OPEN_BODY) == \
        "doubao_model_not_open"


def test_model_not_open_not_misread_as_arrears():
    got = qa.classify_llm_error("doubao", 404, ARK_MODEL_NOT_OPEN_BODY)
    assert got != "doubao_balance_exhausted"
    title, content = qa.build_alert_message(
        got, "doubao", 404, "ModelNotOpen",
        qa._snippet(ARK_MODEL_NOT_OPEN_BODY),
        model="doubao-seed-2-1-turbo-260628",
    )
    assert "不是欠费" in title
    assert "不是余额问题" in content
    assert "充值解决不了" in content


# ============================================================
# 分类：限流（P3，永不推送）
# ============================================================
def test_rate_limit_is_p3_and_never_pushed():
    assert qa.classify_llm_error("doubao", 429, ARK_RATE_LIMIT_BODY) == \
        "doubao_rate_limited"
    assert qa.ALERT_PRIORITY["doubao_rate_limited"] == "P3"


def test_rate_limit_does_not_push(monkeypatch):
    """限流不得推送（哪怕企微已配置）。"""
    pushed = []
    monkeypatch.setattr(qa, "_in_push_window", lambda: True, raising=False)
    _install_fake_wxwork(monkeypatch, pushed)
    qa.maybe_alert_quota("doubao", 429, ARK_RATE_LIMIT_BODY, model="m", module="t")
    assert pushed == []


# ============================================================
# 分类：鉴权失败（P1）
# ============================================================
def test_invalid_api_key_is_auth_failed():
    assert qa.classify_llm_error("doubao", 401, ARK_INVALID_KEY_BODY) == \
        "doubao_auth_failed"


def test_auth_failed_not_misread_as_arrears():
    """401 + 含 'balance' 的响应不该被吞成欠费。"""
    body = '{"error":{"code":"InvalidApiKey","message":"invalid api key, balance check skipped"}}'
    assert qa.classify_llm_error("doubao", 401, body) == "doubao_auth_failed"


# ============================================================
# 分类：网络异常 / 无法识别
# ============================================================
def test_network_error_status_zero_no_alert():
    """网络异常 status=0 + 空错误体 → 不告警（旧逻辑也不会，锁住行为）。"""
    assert qa.classify_llm_error("doubao", 0, "") is None


def test_unknown_provider_no_alert():
    assert qa.classify_llm_error("qwen", 402, "Insufficient balance") is None


def test_plain_5xx_no_alert():
    """5xx 服务端错误不该被归因成钱的问题。"""
    assert qa.classify_llm_error("doubao", 500, "internal server error") is None


# ============================================================
# 文案诚实性
# ============================================================
def test_quota_alert_copy_does_not_claim_cash_exhausted():
    """额度告警文案必须明确否认现金欠费，不能再说「账户余额已用尽」。"""
    title, content = qa.build_alert_message(
        "doubao_quota_exhausted", "doubao", 400, "FreeTokensExhausted",
        qa._snippet(ARK_FREE_TOKENS_BODY),
        model="doubao-seed-2-1-turbo-260628", module="chat",
    )
    assert "已用尽" not in content
    assert "未确认为现金欠费" in content
    assert "推理点" in content


def test_alert_copy_carries_evidence_and_source():
    """文案必须带 HTTP 状态码 / 错误码 / 错误体片段 / 模型 / 来源模块。"""
    title, content = qa.build_alert_message(
        "doubao_balance_exhausted", "doubao", 402, "PaymentRequired",
        "Payment Required",
        model="doubao-seed-2-1-pro-260628", module="night_worker",
    )
    assert "HTTP 402" in content
    assert "错误码 PaymentRequired" in content
    assert "model=doubao-seed-2-1-pro-260628" in content
    assert "module=night_worker" in content
    assert "Payment Required" in content


# ============================================================
# 推送门禁：优先级 + 免打扰时段 + 去重
# ============================================================
def _install_fake_wxwork(monkeypatch, pushed: list):
    """把 wxwork_push 换成假的，记录所有推送，绝不打真实接口。"""
    import types

    fake = types.SimpleNamespace(
        is_configured=lambda: True,
        send_daily_report_to=lambda uid, content, title="": pushed.append((uid, title, content)),
    )
    fake.__name__ = "wxwork_push"
    monkeypatch.setitem(sys.modules, "services.wxwork_push", fake)


def test_p0_pushes_even_at_night(monkeypatch):
    """真欠费（P0）半夜也要推。"""
    pushed = []
    monkeypatch.setattr(qa, "_in_push_window", lambda: False, raising=False)
    _install_fake_wxwork(monkeypatch, pushed)
    qa.maybe_alert_quota("doubao", 402, "Payment Required", model="m", module="t")
    assert len(pushed) == 2  # LeiJiang + BuLuoGeLi


def test_p2_deferred_at_night(monkeypatch):
    """P2（模型未开通/额度）凌晨不推——01:13 误报的正是这一类。"""
    pushed = []
    monkeypatch.setattr(qa, "_in_push_window", lambda: False, raising=False)
    _install_fake_wxwork(monkeypatch, pushed)
    qa.maybe_alert_quota("doubao", 404, ARK_MODEL_NOT_OPEN_BODY,
                         model="doubao-seed-2-1-turbo-260628", module="t")
    assert pushed == []


def test_p2_pushes_in_daytime(monkeypatch):
    """白天窗口内 P2 正常推送。"""
    pushed = []
    monkeypatch.setattr(qa, "_in_push_window", lambda: True, raising=False)
    _install_fake_wxwork(monkeypatch, pushed)
    qa.maybe_alert_quota("doubao", 404, ARK_MODEL_NOT_OPEN_BODY,
                         model="doubao-seed-2-1-turbo-260628", module="t")
    assert len(pushed) == 2
    assert "模型未开通" in pushed[0][1]


def test_dedupe_is_per_model(monkeypatch, tmp_path):
    """同一 alert_type 下 turbo 与 pro 各自独立，互不屏蔽。"""
    monkeypatch.setattr(qa, "ALERT_STATE_FILE", tmp_path / "state.json", raising=False)
    monkeypatch.setattr(qa, "_in_push_window", lambda: True, raising=False)
    pushed = []
    _install_fake_wxwork(monkeypatch, pushed)

    qa.maybe_alert_quota("doubao", 404, ARK_MODEL_NOT_OPEN_BODY,
                         model="doubao-seed-2-1-turbo-260628", module="t")
    assert len(pushed) == 2

    # 同一模型再报 → 当日去重，不再推
    qa.maybe_alert_quota("doubao", 404, ARK_MODEL_NOT_OPEN_BODY,
                         model="doubao-seed-2-1-turbo-260628", module="t")
    assert len(pushed) == 2

    # 换一个模型 → 是另一件事，应该继续推
    qa.maybe_alert_quota("doubao", 404, ARK_MODEL_NOT_OPEN_BODY,
                         model="doubao-seed-2-1-pro-260628", module="t")
    assert len(pushed) == 4


# ============================================================
# 错误码提取
# ============================================================
@pytest.mark.parametrize("body,expected", [
    (ARK_MODEL_NOT_OPEN_BODY, "ModelNotOpen"),
    (ARK_INSUFFICIENT_BALANCE_BODY, "InsufficientBalance"),
    (ARK_FREE_TOKENS_BODY, "FreeTokensExhausted"),
    ("not a json at all", ""),
    ("", ""),
])
def test_extract_error_code(body, expected):
    assert qa._extract_error_code(body) == expected


# ============================================================
# 真实 _in_push_window() —— 直接测小时运算，不再 mock 掉函数本身
# ============================================================
# QA 故障注入 INJ-4a 证明：把 _in_push_window() 内部条件取反后，上面 27 个用例
# 依然全绿 —— 因为所有窗口用例都用 monkeypatch 把**真实函数替换掉了**，
# 真实小时运算一行都没测到。下面这组用例只固定 time.localtime()，
# 绝不替换 _in_push_window，因此条件一旦被改坏，它们必须变红。
def _freeze_hour(monkeypatch, hour: int) -> None:
    """把 time.localtime() 固定到当天指定小时（其余字段给合法占位值）。"""
    import time as _time

    monkeypatch.setattr(
        qa.time,
        "localtime",
        lambda: _time.struct_time((2026, 9, 12, hour, 0, 0, 5, 255, 0)),
    )


@pytest.mark.parametrize("hour,expected", [
    (0, False),    # 午夜，窗口前
    (7, False),    # 左边界前 1 小时
    (8, True),     # 左边界（含）
    (12, True),    # 窗口正中
    (22, True),    # 右边界前 1 小时
    (23, False),   # 右边界（不含）
])
def test_in_push_window_real_boundaries(monkeypatch, hour, expected):
    """真实小时运算的边界：08:00 含、23:00 不含。"""
    monkeypatch.setattr(qa, "PUSH_WINDOW_START_HOUR", 8, raising=False)
    monkeypatch.setattr(qa, "PUSH_WINDOW_END_HOUR", 23, raising=False)
    _freeze_hour(monkeypatch, hour)
    assert qa._in_push_window() is expected


def test_in_push_window_real_honors_env_window(monkeypatch):
    """窗口不是写死的 8/23：env 改成 10-12 后真实函数必须跟着变。"""
    monkeypatch.setattr(qa, "PUSH_WINDOW_START_HOUR", 10, raising=False)
    monkeypatch.setattr(qa, "PUSH_WINDOW_END_HOUR", 12, raising=False)
    _freeze_hour(monkeypatch, 9)
    assert qa._in_push_window() is False
    _freeze_hour(monkeypatch, 10)
    assert qa._in_push_window() is True
    _freeze_hour(monkeypatch, 12)
    assert qa._in_push_window() is False


# ============================================================
# env 读取加固（QA-1：非法值不得让 FastAPI 起不来）
# ============================================================
@pytest.mark.parametrize("raw,expected", [
    ("", 8),      # 空串（.env 里最常见的形态）→ 默认
    ("   ", 8),   # 全空白 → 默认
    ("abc", 8),   # 非数字 → 默认（旧代码在这里抛 ValueError，全站起不来）
    ("25", 8),    # 越界：>24 → 默认（否则窗口恒 False，P1/P2 永久静默）
    ("-1", 8),    # 越界：负数 → 默认
    ("9", 9),     # 合法值照常生效
    (" 9 ", 9),   # 允许两端空白
    ("0", 0),     # 0 是合法的小时
])
def test_read_hour_env_falls_back_on_bad_value(monkeypatch, raw, expected):
    """非法 env 一律静默回退默认值，绝不抛异常。"""
    monkeypatch.setenv("QUOTA_ALERT_PUSH_START_HOUR", raw)
    assert qa._read_hour_env("QUOTA_ALERT_PUSH_START_HOUR", 8) == expected


# ============================================================
# 文案断言补漏（INJ-6）
# ============================================================
def test_quota_copy_warns_do_not_recharge():
    """额度类文案必须明确劝阻充值——只锁「未确认为现金欠费」还不够。

    用户收到的误报正是被引导去充值的，光说「未确认」仍然可能让人去充。
    """
    _, content = qa.build_alert_message(
        "doubao_quota_exhausted", "doubao", 400, "FreeTokensExhausted",
        "insufficient balance", model="m", module="t",
    )
    assert "这不是欠费告警，先别急着充值" in content
    assert "未确认为现金欠费" in content


# ============================================================
# 错误码提取正则兜底（INJ-9）
# ============================================================
@pytest.mark.parametrize("body,expected", [
    ('failed: {"code":"CustomCode"} occurred', "CustomCode"),
    ('HTTP 400 {"code":"BadRequest","message":"x"}', "BadRequest"),
    ("not a json at all", ""),
    ("", ""),
])
def test_extract_error_code_regex_fallback(body, expected):
    """外层不是合法 JSON、但内含 "code":"xxx" 时走正则兜底。"""
    assert qa._extract_error_code(body) == expected


# ============================================================
# 主动巡检（llm_balance_monitor）优先级门禁（QA-2）
# ============================================================
def _load_monitor_module():
    """按文件路径加载 scripts/llm_balance_monitor.py（scripts 不是包）。"""
    import importlib.util

    scripts_dir = os.path.join(os.path.dirname(__file__), "..", "scripts")
    path = os.path.join(scripts_dir, "llm_balance_monitor.py")
    spec = importlib.util.spec_from_file_location("_kd_llm_balance_monitor", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001 - 环境缺依赖时跳过，不算失败
        return None
    return module


def test_monitor_p3_never_pushes(monkeypatch):
    """主动巡检可以绕过免打扰窗口，但**不能绕过优先级**：P3 永不推送。"""
    monitor = _load_monitor_module()
    if monitor is None:
        pytest.skip("llm_balance_monitor 无法加载（缺少依赖）")

    called = []
    monkeypatch.setattr(monitor, "_push_alert",
                        lambda title, content: called.append(title) or True,
                        raising=False)
    monkeypatch.setattr(monitor, "_dedupe_mark_sent", lambda key: None, raising=False)

    # 限流 = P3：即使 do_push=True 也必须被优先级门禁挡住
    monitor._emit_alert("doubao_rate_limited|m", "限流", "content", do_push=True)
    assert called == []

    # 模型未开通 = P2：不是 P3，允许推送
    monitor._emit_alert("doubao_model_not_open|m", "未开通", "content", do_push=True)
    assert len(called) == 1

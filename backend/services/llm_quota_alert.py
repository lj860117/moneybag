"""
LLM 配额/余额告警
- 按「错误码 + HTTP 状态码 + 语义关键词」精确分类，不再凭子串猜
- 现金欠费（P0）才立刻推送；推理点/模型未开通（P2）只在白天推；
  限流（P3）只落日志，绝不推送
- 告警文案必须带原始状态码 / 错误码 / 错误体片段 / 触发来源，让人能自查
- 同种告警（按 alert_type + 模型）一天只推一次（文件去重）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX 2026-09-12：豆包「账户余额已用尽」误报
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
旧判定（第 52-83 行）是这样的::

    if "余额不足" in error_msg or "insufficient" in err_lower or "balance" in err_lower:
        return "doubao_balance_exhausted"
    if "arrearage" in err_lower or "arrears" in err_lower or "欠费" in error_msg:
        return "doubao_balance_exhausted"

问题：只要错误体里出现 "balance" / "insufficient" 这三个字母就判成
「账户余额耗尽」，且完全不看 status_code。而火山引擎 ARK 至少有三种语义
完全不同、但都会带这些词的错误：

  1. 推理点 / 免费 tokens 用完      → 现金余额可能还有钱，买推理点包即可
  2. 模型未开通 / 未购买            → 充值完全无用，要去控制台开通模型
  3. 限流 / 参数错误 / 模型不存在    → 瞬时或配置问题，不是钱的问题

实测证据（2026-09-12 01:30 CST，服务器用生产 Key 直连 ARK）：
  • doubao-seed-2-1-pro-260628    → HTTP 200（账户可用，没欠费）
  • doubao-seed-2-1-turbo-260628  → HTTP 404
    {"error":{"code":"ModelNotOpen","message":"Your account 2127949875 has not
     activated the model doubao-seed-2-1-turbo-260628. Please activate the model
     service in the Ark Console. ...","type":"Not Found"}}
    —— 是「模型未开通」，不是欠费；用户控制台现金余额 ¥100 也印证了这点。

现在改成：**只有硬信号（HTTP 402 / ARK 明确的欠费错误码 / 明确的欠费字样）
才判 P0 现金欠费**；"insufficient balance" 这种模糊英文降级为 P2 额度告警，
文案里明确写「未确认为现金欠费，别急着充值」。
"""
from __future__ import annotations

import config
import json
import os
import re
import time
from datetime import date
from pathlib import Path
from typing import Optional

DATA_DIR = Path(config.DATA_DIR)
ALERT_STATE_FILE = DATA_DIR / "llm_alert_state.json"

# ============================================================
# 推送窗口与优先级
# ============================================================
# P0（确证欠费）随时推——真欠费就是要半夜叫醒人。
# P1/P2（Key 异常 / 模型未开通 / 额度用完）只在白天推：这类问题是持续性的，
#   凌晨 01:13 推一条「已用尽」既吵醒人又给错处置建议（让人去充钱）。
# P3（限流）永不推送——瞬时抖动，推了就是噪音。
#
# ⚠️ env 读取必须加固（FIX 2026-09-12 QA-1）：
# 本模块被 main.py:162 的 startup 事件 import，模块级 `int(os.environ.get(...))`
# 一旦拿到非法值（"abc"、空串——.env 里最常见的形态）就会抛 ValueError，
# **整个 FastAPI 起不来**。这种「一设 env 就全站挂」的失败模式不值得赌。
# 另外 START=25 / END=0 会让 _in_push_window() 恒 False → 所有 P1/P2 永久
# 静默且日志毫无异常，是更隐蔽的失效模式，必须用范围校验堵掉。
# 策略：非法值一律静默回退默认 8/23 + 打印明确警告（带变量名和非法值）。
_DEFAULT_PUSH_START_HOUR = 8
_DEFAULT_PUSH_END_HOUR = 23


def _read_hour_env(name: str, default: int) -> int:
    """读取小时类环境变量，非法值回退默认值并告警（绝不抛异常）。"""
    raw = os.environ.get(name, "")
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        print(f"[QUOTA_ALERT][CONFIG] ⚠️ 环境变量 {name}={raw!r} 不是整数，"
              f"回退默认值 {default}")
        return default
    if not (0 <= value <= 24):
        print(f"[QUOTA_ALERT][CONFIG] ⚠️ 环境变量 {name}={raw!r} 超出 0~24，"
              f"回退默认值 {default}")
        return default
    return value


PUSH_WINDOW_START_HOUR = _read_hour_env(
    "QUOTA_ALERT_PUSH_START_HOUR", _DEFAULT_PUSH_START_HOUR
)
PUSH_WINDOW_END_HOUR = _read_hour_env(
    "QUOTA_ALERT_PUSH_END_HOUR", _DEFAULT_PUSH_END_HOUR
)

# 关系校验：START 必须严格小于 END，否则窗口恒 False → P1/P2 永久静默
if not (0 <= PUSH_WINDOW_START_HOUR < PUSH_WINDOW_END_HOUR <= 24):
    print(f"[QUOTA_ALERT][CONFIG] ⚠️ 推送窗口非法 "
          f"({PUSH_WINDOW_START_HOUR}→{PUSH_WINDOW_END_HOUR})，"
          f"要求 0 <= START < END <= 24；整组回退默认 "
          f"{_DEFAULT_PUSH_START_HOUR}-{_DEFAULT_PUSH_END_HOUR}")
    PUSH_WINDOW_START_HOUR = _DEFAULT_PUSH_START_HOUR
    PUSH_WINDOW_END_HOUR = _DEFAULT_PUSH_END_HOUR

ALERT_PRIORITY: dict[str, str] = {
    # ---- P0：确证现金欠费 ----
    "deepseek_balance_exhausted": "P0",
    "doubao_balance_exhausted": "P0",
    # ---- P1：鉴权/Key 问题（配置错，不是没钱）----
    "deepseek_auth_failed": "P1",
    "doubao_auth_failed": "P1",
    # ---- P2：额度/模型未开通（非现金，处置动作是「开通/买包」不是「充值」）----
    "deepseek_quota_exhausted": "P2",
    "doubao_quota_exhausted": "P2",
    "deepseek_model_not_open": "P2",
    "doubao_model_not_open": "P2",
    # ---- P3：限流，只记日志 ----
    "deepseek_rate_limited": "P3",
    "doubao_rate_limited": "P3",
}

_PROVIDER_LABEL = {
    "deepseek": "DeepSeek",
    "doubao": "豆包（火山引擎 ARK）",
}
_PROVIDER_CONSOLE = {
    "deepseek": "https://platform.deepseek.com/",
    "doubao": "https://console.volcengine.com/ark",
}

# ============================================================
# 错误码 / 语义关键词表
# ============================================================
# 模型未开通：ARK 实测返回 404 + code=ModelNotOpen
_MODEL_NOT_OPEN_CODES = frozenset({
    "ModelNotOpen", "ModelNotSupported", "ModelNotExist",
    "ModelNotFound", "InvalidModel", "EndpointNotOpen", "ModelNotActivated",
})
_MODEL_NOT_OPEN_HINTS = (
    "has not activated the model",   # ARK 英文原文
    "model not open",
    "未开通", "未激活", "模型不存在", "模型未找到",
)

# 限流：瞬时抖动，绝不推送
_RATE_LIMIT_CODES = frozenset({
    "RateLimitExceeded", "Throttling", "TooManyRequests",
    "RequestsThrottled", "RateLimit", "FlowLimitExceeded",
})
_RATE_LIMIT_HINTS = (
    "rate limit", "ratelimit", "too many requests",
    "限流", "qps", "tpm limit", "rpm limit", "concurrency limit",
)

# 鉴权失败：Key 错/没权限，不是没钱
_AUTH_CODES = frozenset({
    "InvalidApiKey", "InvalidApikey", "AuthenticationError", "AccessDenied",
    "Unauthorized", "PermissionDenied", "SignatureDoesNotMatch", "Forbidden",
})
_AUTH_HINTS = (
    "invalid api key", "api key is invalid", "unauthorized",
    "authentication failed", "authentication error",
    "鉴权失败", "密钥错误", "apikey无效", "无权限",
)

# 现金欠费「硬信号」：必须命中这里之一才判 P0
_ARREARS_CODES = frozenset({
    "InsufficientBalance", "AccountArrears", "Arrearage", "AccountOverdue",
    "OverduePayment", "PaymentRequired", "InsufficientFund", "BalanceNotEnough",
    "AccountInArrears",
})
_ARREARS_HINTS = (
    "arrearage", "arrears", "overdue",
    "欠费", "账户余额不足", "余额不足", "已欠费",
)

# 推理点 / 免费额度 / 资源包用完：非现金，处置动作不是「充值」
_QUOTA_CODES = frozenset({
    "FreeTokensExhausted", "QuotaExceeded", "InsufficientQuota",
    "TokenQuotaExhausted", "ExceededQuota", "ResourcePackExhausted",
    "TokensExhausted",
})
_QUOTA_HINTS = (
    "推理点", "免费额度", "免费 tokens", "free tokens", "资源包",
    "quota", "insufficient balance", "insufficient_balance",
)

_ERR_CODE_RE = re.compile(r'"code"\s*:\s*"([A-Za-z0-9_.]+)"')


# ============================================================
# 告警状态（按 alert_type + 模型去重）
# ============================================================
def _load_state() -> dict:
    """读取告警状态：{dedupe_key: last_sent_date}"""
    if not ALERT_STATE_FILE.exists():
        return {}
    try:
        return json.loads(ALERT_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict):
    try:
        ALERT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ALERT_STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception as e:
        print(f"[QUOTA_ALERT] save state failed: {e}")


def _was_sent_today(dedupe_key: str) -> bool:
    """检查今天是否已推送过此类告警"""
    state = _load_state()
    return state.get(dedupe_key) == date.today().isoformat()


def _mark_sent_today(dedupe_key: str):
    state = _load_state()
    state[dedupe_key] = date.today().isoformat()
    _save_state(state)


def was_alert_sent_today(dedupe_key: str) -> bool:
    """【对外】今天是否已推送过该告警键（供 llm_balance_monitor 复用）。

    dedupe_key 约定为 "alert_type" 或 "alert_type|model"，与 maybe_alert_quota
    内部使用的键同构，因此两条链路共享同一个状态文件即可，无需各存一份。
    """
    return _was_sent_today(dedupe_key)


def mark_alert_sent_today(dedupe_key: str) -> None:
    """【对外】标记该告警键今天已推送（供 llm_balance_monitor 复用）。"""
    _mark_sent_today(dedupe_key)


# ============================================================
# 错误解析
# ============================================================
def _extract_error_code(error_msg: str) -> str:
    """从错误体里提取厂商错误码（ARK / OpenAI 兼容格式）。

    支持两种形态：
      {"error": {"code": "ModelNotOpen", "message": "..."}}
      {"code": "xxx", "message": "..."}
    非 JSON 或没有 code 字段时退化为正则兜底，取不到就返回空串。
    """
    text = (error_msg or "").strip()
    if not text:
        return ""
    try:
        data = json.loads(text)
    except Exception:
        data = None
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            code = err.get("code")
            if isinstance(code, str) and code.strip():
                return code.strip()
        code = data.get("code")
        if isinstance(code, str) and code.strip():
            return code.strip()
    matched = _ERR_CODE_RE.search(text)
    return matched.group(1) if matched else ""


def _snippet(error_msg: str, limit: int = 200) -> str:
    """把错误体压成单行片段，供告警文案引用（保留原文，方便自查）。"""
    text = " ".join((error_msg or "").split())
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def alert_priority(alert_type: str) -> str:
    """返回告警优先级（P0/P1/P2/P3），未知类型保守按 P2。

    抽成函数是为了让「主动监控」（llm_balance_monitor.py）和「事后告警」
    （本文件的 maybe_alert_quota）共用同一张优先级表，不各判各的。
    """
    return ALERT_PRIORITY.get(alert_type or "", "P2")


def _in_push_window() -> bool:
    """当前是否处于允许推送 P1/P2 的时段（默认 08:00-23:00）。"""
    return PUSH_WINDOW_START_HOUR <= time.localtime().tm_hour < PUSH_WINDOW_END_HOUR


# ============================================================
# 分类
# ============================================================
def classify_llm_error_detail(
    provider: str,
    status_code: int,
    error_msg: str,
) -> tuple[Optional[str], str, str]:
    """精确分类 LLM 错误。

    判定顺序（先硬后软，避免模糊子串抢先命中）：
      1. 模型未开通  2. 限流  3. 鉴权失败  4. 现金欠费（硬信号）  5. 额度用完

    Args:
        provider: "deepseek" 或 "doubao"
        status_code: HTTP 状态码；网络异常/拿不到响应时传 0
        error_msg: 原始错误体（可能是 JSON 字符串，也可能是异常文本）

    Returns:
        (alert_type | None, error_code, snippet)
        alert_type 为 None 表示「识别不出、不需要告警」。
    """
    provider = (provider or "").strip().lower()
    raw = error_msg or ""
    err_lower = raw.lower()
    code = _extract_error_code(raw)
    status = status_code if isinstance(status_code, int) else 0

    if provider not in ("deepseek", "doubao"):
        return None, code, _snippet(raw)

    # 1) 模型未开通 / 未购买 —— 不是钱的问题，充值无用
    if code in _MODEL_NOT_OPEN_CODES:
        return f"{provider}_model_not_open", code, _snippet(raw)
    if any(h in err_lower for h in _MODEL_NOT_OPEN_HINTS):
        return f"{provider}_model_not_open", code, _snippet(raw)
    if status == 404 and "model" in err_lower:
        return f"{provider}_model_not_open", code, _snippet(raw)

    # 2) 限流 —— 瞬时抖动，只记日志
    if status == 429 or code in _RATE_LIMIT_CODES:
        return f"{provider}_rate_limited", code, _snippet(raw)
    if any(h in err_lower for h in _RATE_LIMIT_HINTS):
        return f"{provider}_rate_limited", code, _snippet(raw)

    # 3) 鉴权 / Key 失效 —— 配置问题，不是没钱
    #    注意：不再用裸子串 "key"/"auth" 判定（旧逻辑会把含 "keyword" 的
    #    任何 403 都吞成鉴权失败），改为错误码或明确短语。
    if status == 401 or code in _AUTH_CODES:
        return f"{provider}_auth_failed", code, _snippet(raw)
    if any(h in err_lower for h in _AUTH_HINTS):
        return f"{provider}_auth_failed", code, _snippet(raw)

    # 4) 现金欠费 —— 必须有硬信号才判 P0
    if status == 402 or code in _ARREARS_CODES:
        return f"{provider}_balance_exhausted", code, _snippet(raw)
    if any(h in err_lower for h in _ARREARS_HINTS):
        return f"{provider}_balance_exhausted", code, _snippet(raw)
    # DeepSeek 官方 402 的正文是 "Insufficient balance" / "Payment Required"
    if provider == "deepseek":
        if "payment required" in err_lower:
            return "deepseek_balance_exhausted", code, _snippet(raw)
        if "insufficient" in err_lower and "balance" in err_lower:
            return "deepseek_balance_exhausted", code, _snippet(raw)

    # 5) 推理点 / 免费额度 / 资源包用完 —— 非现金，处置是「买包」不是「充值」
    #    ⚠️ "insufficient balance" 落在这里：ARK 的推理点不足也这么报，
    #       2026-09-12 的误报就是把这句当成了现金欠费。
    if status == 403 or code in _QUOTA_CODES:
        return f"{provider}_quota_exhausted", code, _snippet(raw)
    if any(h in err_lower for h in _QUOTA_HINTS):
        return f"{provider}_quota_exhausted", code, _snippet(raw)

    return None, code, _snippet(raw)


def classify_llm_error(provider: str, status_code: int, error_msg: str) -> Optional[str]:
    """向后兼容的薄封装：只返回告警类型。

    保留原签名，避免破坏其它调用方/旧测试。
    """
    alert_type, _code, _snippet_text = classify_llm_error_detail(
        provider, status_code, error_msg
    )
    return alert_type


# ============================================================
# 文案构造
# ============================================================
def _footer(provider: str, status_code: int, error_code: str,
            model: str, module: str) -> str:
    """告警末尾的证据行：让人一眼看到「到底是什么错、从哪来的」。"""
    parts = [f"provider={provider}"]
    if model:
        parts.append(f"model={model}")
    if module:
        parts.append(f"module={module}")
    parts.append(f"HTTP {status_code}")
    parts.append(f"错误码 {error_code or '-'}")
    parts.append(time.strftime("%m-%d %H:%M"))
    return "—— " + " · ".join(parts)


def build_alert_message(
    alert_type: str,
    provider: str,
    status_code: int,
    error_code: str,
    snippet: str,
    model: str = "",
    module: str = "",
) -> tuple[str, str]:
    """根据告警类型生成 (title, content)。文案必须诚实区分语义。"""
    label = _PROVIDER_LABEL.get(provider, provider)
    console = _PROVIDER_CONSOLE.get(provider, "")
    foot = _footer(provider, status_code, error_code, model, module)

    if alert_type.endswith("_balance_exhausted"):
        return (
            f"💳 {label} 余额告警（确证欠费信号）",
            f"**❗ {label} 返回「现金余额/欠费」确证信号**\n\n"
            f"判定依据：HTTP {status_code} · 错误码 {error_code or '-'}\n"
            f"（硬信号之一：402 / 欠费错误码 / 明确欠费字样）\n\n"
            f"影响：主路径失败时降级目标也会失败，AI 功能可能整体不可用\n\n"
            f"原始错误片段：\n{snippet}\n\n"
            f"{foot}\n\n"
            f"建议：前往 {console} 核对现金余额并充值。",
        )

    if alert_type.endswith("_quota_exhausted"):
        return (
            f"⚠️ {label} 额度告警（疑似推理点/免费额度，非现金欠费）",
            f"**⚠️ {label} 疑似「推理点 / 免费额度」用完 —— 未确认为现金欠费**\n\n"
            "这不是欠费告警，先别急着充值。常见含义：\n"
            "• 免费 tokens / 推理点用完（现金余额可能还有钱）\n"
            "• 该模型没买资源包\n\n"
            "请先到控制台看一眼现金余额：\n"
            "• 现金余额还有钱 → 不用充值，去买推理点包或换已开通模型\n"
            "• 现金余额确实为 0 → 才是真欠费\n\n"
            f"原始错误片段：\n{snippet}\n\n"
            f"{foot}\n\n"
            f"控制台：{console}",
        )

    if alert_type.endswith("_model_not_open"):
        return (
            f"⚠️ {label} 模型未开通（不是欠费，充值无用）",
            f"**⚠️ {label}：模型未开通 / 未购买**\n\n"
            "这不是余额问题——充值解决不了。\n\n"
            f"原始错误片段：\n{snippet}\n\n"
            f"{foot}\n\n"
            f"建议：到 {console} 开通该模型；\n"
            "或调整路由改用已开通的模型（后端 DOUBAO_MODEL_ROUTING）。",
        )

    if alert_type.endswith("_auth_failed"):
        return (
            f"🔑 {label} API Key 异常",
            f"**⚠️ {label} API Key 可能失效或权限不足**\n\n"
            "这是配置问题，不是余额问题。\n\n"
            f"原始错误片段：\n{snippet}\n\n"
            f"{foot}\n\n"
            f"建议：到 {console} 检查 API Key 是否有效、是否绑定了计费账户。",
        )

    # 兜底（P3 等不推送的类型理论上走不到这里）
    return (
        f"⚠️ {label} LLM 调用异常",
        f"**⚠️ {label} 调用异常（未分类）**\n\n"
        f"原始错误片段：\n{snippet}\n\n"
        f"{foot}",
    )


# ============================================================
# 主入口
# ============================================================
def maybe_alert_quota(
    provider: str,
    status_code: int,
    error_msg: str,
    *,
    model: str = "",
    module: str = "",
):
    """检测错误并按需推送告警（自动去重 + 优先级 + 免打扰时段）。

    Args:
        provider: "deepseek" / "doubao"
        status_code: HTTP 状态码（网络异常时为 0）
        error_msg: 原始错误体
        model: 触发告警的具体模型名（用于去重与文案溯源，可为空）
        module: 触发来源模块（可为空）

    安全：异常时静默，不影响主流程。
    """
    try:
        alert_type, err_code, snippet = classify_llm_error_detail(
            provider, status_code, error_msg
        )
        tag = f"provider={provider} status={status_code} code={err_code or '-'} model={model or '-'} module={module or '-'}"

        if not alert_type:
            print(f"[QUOTA_ALERT][IGNORED] 未识别为需告警的错误 | {tag} | {snippet[:120]}")
            return

        priority = alert_priority(alert_type)

        # P3（限流）：永不推送，只落日志
        if priority == "P3":
            print(f"[QUOTA_ALERT][NO_PUSH:P3] {alert_type} | {tag} | {snippet[:120]}")
            return

        # P1/P2：非白天不推（凌晨误报的典型来源）
        if priority in ("P1", "P2") and not _in_push_window():
            print(
                f"[QUOTA_ALERT][QUIET_HOURS_DEFERRED] {alert_type}({priority}) "
                f"不在 {PUSH_WINDOW_START_HOUR}:00-{PUSH_WINDOW_END_HOUR}:00 推送窗口内，仅记录 | {tag}"
            )
            return

        # 当日去重（按 alert_type + 模型，turbo/pro 各自独立）
        dedupe_key = f"{alert_type}|{model or '-'}"
        if _was_sent_today(dedupe_key):
            return

        title, content = build_alert_message(
            alert_type, provider, status_code, err_code, snippet,
            model=model, module=module,
        )

        # 推送给 LeiJiang 和 BuLuoGeLi
        from services.wxwork_push import is_configured, send_daily_report_to
        if not is_configured():
            return

        for uid in ["LeiJiang", "BuLuoGeLi"]:
            try:
                send_daily_report_to(uid, content, title=title)
            except Exception as e:
                print(f"[QUOTA_ALERT] push to {uid} failed: {e}")

        _mark_sent_today(dedupe_key)
        print(f"[QUOTA_ALERT] ✅ 已推送告警: {alert_type}({priority}) | {tag}")

    except Exception as e:
        print(f"[QUOTA_ALERT] err: {e}")

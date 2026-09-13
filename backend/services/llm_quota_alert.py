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

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX 2026-09-13：裸 HTTP 402 不再判 P0（测试 mock 造出的假欠费告警）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
上面这条「402 = 硬信号」**写松了**：402 是谁都能返回的状态码（测试 mock、
反代、网关自造错误），真实厂商的 402 一定**同时**带明确错误码或欠费字样。
事故：backend/tests 里 `_FakeResponse(402, {"error": "doubao quota exceeded"})`
（`error` 是字符串不是 dict ⇒ 取不到 code）被一路判成 P0，用户收到
「💳 豆包余额告警（确证欠费信号）」并被引导去充值 —— **豆包实际没欠费**。

现在：402 / 欠费错误码命中后，**还要**有 code 或欠费字样才判 P0；
两者皆无 → 降 P2 额度告警。详见 classify_llm_error_detail 第 4 步。
"""
from __future__ import annotations

import config
import json
import os
import re
import sys
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
    # FIX 2026-09-13：原来只在 `provider == "deepseek"` 的专属兜底分支里认
    # "payment required"，豆包方向遇到「HTTP 402 + 正文 Payment Required」
    # 会一路漏到 P2/不告警。HTTP 402 的语义名就是 Payment Required，两家厂商
    # 通用，提到公共 hints 里，deepseek 那条专属分支保持不动（多一层兜底）。
    # err_lower 已小写，故这里也写小写。
    "payment required",
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
    #    FIX 2026-09-13：**裸 402 不再无条件直落 P0**。
    #    旧写法 `if status == 402: return balance_exhausted` 把「状态码」当成了
    #    「确证欠费」。但 402 是**谁都能返回**的：测试 mock、反代、网关自造错误、
    #    甚至某些框架把鉴权失败也报成 402。真实厂商的 402 一定**同时**带明确的
    #    错误码（PaymentRequired / InsufficientBalance）或明确的欠费字样 ——
    #    裸 402 恰恰是"来源可疑"的信号，不是"确证欠费"的信号。
    #
    #    事故指纹（2026-09-13 19:35 那条假告警）：HTTP 402 + **错误码为空** ——
    #    来自 backend/tests 的 `_FakeResponse(402, {"error": "doubao quota
    #    exceeded"})`，`error` 是字符串不是 dict，_extract_error_code() 取不到
    #    code。豆包实际没欠费（生产 Key 直连 ARK 实测 HTTP 200），但告警文案写着
    #    「确证欠费信号」并让用户去充值。
    #
    #    修法：402 / 欠费错误码 命中后，**还要**有 code 或欠费字样才判 P0；
    #    两者皆无 → 降级为 P2 额度告警（文案明确"未确认为现金欠费，先别急着
    #    充值"）。宁可少一条 P0，不可再骗用户去充钱。
    if status == 402 or code in _ARREARS_CODES:
        if code or any(h in err_lower for h in _ARREARS_HINTS):
            return f"{provider}_balance_exhausted", code, _snippet(raw)
        # 402 但既无错误码、也无任何欠费字样 —— 来源可疑（mock / 代理 / 网关
        # 自造），不能再当「确证欠费」推 P0。
        return f"{provider}_quota_exhausted", code, _snippet(raw)
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
            f"（硬信号：402 或欠费错误码，且必须同时带厂商错误码 / 明确欠费字样；"
            f"裸 402 已于 2026-09-13 起不再判 P0）\n\n"
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
# 测试环境短路（FIX 2026-09-13：测试 mock 造出的假告警被真推到了企微）
# ============================================================
# 事故（真实发生，2026-09-13 19:35）：
#   backend/tests/test_chat_model_routing.py 用 fake httpx 造了一个 HTTP 402
#   响应（_FakeResponse(402, {"error": "doubao quota exceeded"})），gateway 的
#   回退分支把它当成真实调用失败，一路走到本文件的 maybe_alert_quota；而
#   classify_llm_error 对 `status == 402` 是**硬判定**直落 P0 现金欠费
#   （不看错误码 —— 那条 mock 的 error 是字符串不是 dict，解析出的错误码是空）。
#   于是「💳 豆包余额告警（确证欠费信号）」被推送到用户企微。
#   **豆包实际没欠费**：生产 Key 直连 ARK 实测 doubao-seed-2-1-turbo-260628
#   返回 HTTP 200。这是一条由测试造出来的假告警。
#
# 为什么不能只靠 backend/tests/conftest.py 拦：
#   conftest 只保护 backend/tests/ 这一个入口。maybe_alert_quota 的调用方还
#   包括网关回退分支、6 个 cron 脚本、以及任何手工排查脚本 —— 谁 import 了
#   本模块谁就能触发真实推送。所以必须在**本入口**再上一道，做到"任何调用方
#   在测试进程里都发不出去"。
#
# 为什么不干脆在测试环境一律 return：
#   那样会连「用假 sender 演练推送路径」的合法用例一起废掉（这类用例正是
#   验证"真欠费半夜也要推"的唯一手段）。折中判据是：只有解析出来的发送函数
#   仍是**生产实现**（定义在 services.wxwork_push 里）才拦；测试 monkeypatch
#   进来的 lambda / MagicMock / 假模块物理上发不出网络请求，照常放行。
_WXWORK_MODULE = "services.wxwork_push"


def _env_truthy(name: str) -> bool:
    """环境变量是否为「真」；未设置 / 空串 / 0 / false / no / off 都算假。"""
    raw = str(os.environ.get(name, "")).strip().lower()
    return raw not in ("", "0", "false", "no", "off")


# ⚠️ import 期就固化一次，之后**不再重新探测**：
#   PYTEST_CURRENT_TEST 由 pytest 逐用例设置，任何测试只要 monkeypatch.delenv
#   就能把它抹掉；"pytest" in sys.modules 才是"本进程确实跑在 pytest 下"的硬
#   证据。固化成常量后，单个测试再怎么改环境变量也撤不掉这道闸门 ——
#   与 conftest 里 DATA_DIR 那个 `if not os.environ.get("DATA_DIR")` 逃逸口
#   是同一类教训：**防护不能靠"调用方不会去动它"来保证**。
_TEST_MODE_AT_IMPORT: bool = (
    bool(os.environ.get("PYTEST_CURRENT_TEST"))
    or _env_truthy("MONEYBAG_TEST_MODE")
    or ("pytest" in sys.modules)
)


def _in_test_mode() -> bool:
    """当前是否处于「测试环境」—— 命中则 maybe_alert_quota 不得真实推送。

    显式设置 MONEYBAG_TEST_MODE 可覆盖自动探测（例如确需在 pytest 里演练真实
    推送链路），但关闭时会打印醒目警告 —— 静默放行会让这道闸门自己骗自己。
    """
    explicit = str(os.environ.get("MONEYBAG_TEST_MODE", "")).strip()
    if explicit:
        forced = _env_truthy("MONEYBAG_TEST_MODE")
        if not forced and _TEST_MODE_AT_IMPORT:
            print("[QUOTA_ALERT][CONFIG] ⚠️ MONEYBAG_TEST_MODE 显式关闭了测试模式，"
                  "但本进程仍检测到 pytest —— 告警**会**被真实推送，"
                  "确认这是你想要的")
        return forced
    return _TEST_MODE_AT_IMPORT or bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _resolve_wxwork_module():
    """拿到 services.wxwork_push 模块对象（优先 sys.modules，兼容测试假模块）。"""
    mod = sys.modules.get(_WXWORK_MODULE)
    if mod is not None:
        return mod
    try:
        import importlib

        return importlib.import_module(_WXWORK_MODULE)
    except Exception:  # noqa: BLE001 - 拿不到就交给调用方按"保守拦截"处理
        return None


def _is_production_sender(fn: object) -> bool:
    """判断解析到的发送函数是不是「会真发网络请求」的生产实现。

    判据：函数定义在 services.wxwork_push 里。解析不到、或拿不到 __module__
    时一律**保守当真**（宁可误拦，不可误发 —— 误拦只是一条日志，误发是一条
    打扰真实用户的假告警）。

    测试里 monkeypatch 进来的 lambda / MagicMock / SimpleNamespace 假模块都
    不定义在这个模块里，因此**故意放行**：它们物理上发不出真实请求，这正是
    既有用例（test_p0_pushes_even_at_night 等）需要的合法路径。
    """
    if fn is None:
        return True
    module = getattr(fn, "__module__", "")
    if not module:
        return True
    return module == _WXWORK_MODULE


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

        # ── 测试环境短路（FIX 2026-09-13，详见本文件上方专章）──────────
        # 位置有讲究：放在「当日去重」**之前**。
        #   ALERT_STATE_FILE = DATA_DIR / "llm_alert_state.json"，而测试进程里
        #   DATA_DIR 被 conftest 隔离到临时目录 —— 状态永远写不进生产
        #   /opt/moneybag/data，等于去重彻底失效（每跑一次测试就重推一条假
        #   告警）。短路时干脆不读也不写状态文件，生产路径不受任何影响。
        if _in_test_mode():
            _wxwork = _resolve_wxwork_module()
            _send_fn = getattr(_wxwork, "send_daily_report_to", None) \
                if _wxwork is not None else None
            if _is_production_sender(_send_fn):
                print(
                    f"[QUOTA_ALERT][TEST_MODE_BLOCKED] 测试环境：告警未推送（已拦截真实"
                    f"企微发送） | alert_type={alert_type} priority={priority} "
                    f"provider={provider} model={model or '-'} "
                    f"module={module or '-'} status={status_code} "
                    f"code={err_code or '-'}"
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

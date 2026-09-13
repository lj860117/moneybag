"""
LLM Gateway -- 统一 LLM 调用入口（实现本体，原 services/llm_gateway.py 迁入）
=============================================================================
职责：
  1. 模型路由（DeepSeek V4 Flash/Pro + 豆包 Seed 2.1 Pro/Turbo）
  2. 缓存（相同请求 1 小时内复用）
  3. 计费（按 user_id + module 双标签记账，豆包两档 + DeepSeek 两档价目）
  4. 熔断（日限 + 突发限）
  5. 降级（DeepSeek 主、豆包兜底；多模态视觉降级链）

迁移说明（2026-09-06 strangler-fig 阶段 1）：
  - 本文件曾是 112 行 LLMClient 薄适配器（反向 lazy import services.llm_gateway）
  - 现改为实现本体，services/llm_gateway.py 退化为 deprecated 转发壳
  - LLMClient 保留为兼容类（委托同文件的 LLMGateway，不再反向 import services）

设计文档：docs/design/14-llm-gateway-migration-map.md
不变式 #3：所有 LLM 调用走 infra/llm/gateway（权威出处 00-ANCHOR.md:63）
"""
from __future__ import annotations
import config

import inspect
import os
import time
import json
import hashlib
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, cast
from infra.cache import MemoryCache

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - py<3.9 fallback
    ZoneInfo = None  # type: ignore[misc, assignment]

# ---- 配置 ----
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_API_BASE = os.environ.get("LLM_API_BASE", "https://api.deepseek.com/v1")
DOUBAO_API_BASE = os.environ.get("DOUBAO_API_BASE", os.environ.get("ARK_API_BASE", "https://ark.cn-beijing.volces.com/api/v3"))

# 模型路由
#
# 2026-09-11 全面 Flash 化：**llm_heavy 不再代表 Pro**。
# 实测 DeepSeek 官方 /v1/models 只有 ["deepseek-flash", "deepseek-v4-pro"]，
# `deepseek-v4-flash` 被 API 静默归一化为当前最新的 flash 档（官方不换 ID 升级），
# 继续沿用该 ID，不要改成 v4.1 之类的猜测名。
#
# 两档 tier 现在的语义收敛为：
#   - 决定「输出预算」：llm_heavy 仍会抬高 max_tokens 下限（见 _call / _stream）
#   - 决定「显式 Pro 时的降级档位」：见 _fallback_tier_for()
# 实际主模型一律 Flash。只有用户在对话页显式选 Pro 才用贵模型。
MODEL_ROUTING = {
    "llm_light": "deepseek-v4-flash",     # V4 Flash: 聊天/点评/解读/信号
    "llm_heavy": "deepseek-v4-flash",     # 2026-09-11 全面 Flash 化：不再是 Pro
}
DOUBAO_MODEL_ROUTING = {
    "llm_light": "doubao-seed-2-1-turbo-260628",
    "llm_heavy": "doubao-seed-2-1-pro-260628",
}
INTERACTIVE_AUTO_MODULES = {
    "chat",
    "chat_stream",
    "chat_ui",
    "chat_fc",
    "fc_agent",
    "panel_synthesis",
}

# ---- 依赖倒置钩子 ----
# infra 不得反向依赖 services（不变式 #10）。配额/余额告警逻辑在
# services.llm_quota_alert，由组合根（main.py 启动时）通过 set_alert_hook 注入。
# 未注入时（如独立 cron 进程不经 main.py 启动）在 _maybe_alert 内回退 lazy import，
# 保证配额告警不因迁移而静默丢失。
_alert_hook = None


def set_alert_hook(fn: Callable[..., None]) -> None:
    """注入配额告警函数（services.llm_quota_alert.maybe_alert_quota）。

    由组合根调用一次，避免 infra/llm/gateway 反向 import services。
    """
    global _alert_hook
    _alert_hook = fn


def _maybe_alert(
    provider: str,
    status_code: int,
    error_msg: str,
    *,
    model: str = "",
    module: str = "",
) -> None:
    """触发配额/余额告警，注入钩子优先，未注入时回退 lazy import。

    独立进程（night_worker.py 等 6 个 cron 脚本）不经过 main.py 的启动注入，
    钩子为 None，故在此回退到 services.llm_quota_alert.maybe_alert_quota。
    任何环节异常均静默吞掉，不影响主流程（含 raise RuntimeError 流程）。

    FIX 2026-09-12：额外透传 model / module，让告警文案能带上「哪个模型、
    哪个模块」触发的，便于自查（豆包误报那次的告警文案完全没有溯源信息）。
    老钩子若不接受这两个 kwarg，自动退回三参数调用，不破坏兼容性。
    """
    global _alert_hook
    fn = _alert_hook
    if fn is None:
        try:
            from services.llm_quota_alert import maybe_alert_quota
            fn = maybe_alert_quota
        except Exception:
            return
    try:
        try:
            params = inspect.signature(fn).parameters
            supports_ctx = "model" in params or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
            )
        except (TypeError, ValueError):
            supports_ctx = False

        if supports_ctx:
            fn(provider, status_code, error_msg, model=model, module=module)
        else:
            fn(provider, status_code, error_msg)
    except Exception:
        pass


def _china_now(now: Optional[datetime] = None) -> datetime:
    if now is None:
        if ZoneInfo is not None:
            return datetime.now(ZoneInfo("Asia/Shanghai"))
        return datetime.now()
    if getattr(now, "tzinfo", None) is not None and ZoneInfo is not None:
        return now.astimezone(ZoneInfo("Asia/Shanghai"))
    return now


def _is_interactive_auto_module(module: str = "") -> bool:
    module = (module or "").strip().lower()
    if not module:
        return False
    if module in INTERACTIVE_AUTO_MODULES:
        return True
    return module.startswith("chat") or module.startswith("panel_")


def _is_deepseek_peak_window(now: Optional[datetime] = None) -> bool:
    now = _china_now(now)
    if now.weekday() >= 5:   # 周六=5, 周日=6，DeepSeek 周末全天平价
        return False
    hm = (now.hour, now.minute)
    return ((9, 0) <= hm < (12, 0)) or ((14, 0) <= hm < (18, 0))


def _provider_from_model(model: str) -> str:
    if model.startswith("doubao") or model.startswith("ep-"):
        return "doubao"
    return "deepseek"


def _pricing_key_from_model(model: str) -> str:
    """按具体模型名返回定价档位 key（区分 deepseek flash/pro、doubao pro/turbo）。

    返回 "deepseek-flash" / "deepseek-pro" / "doubao-pro" / "doubao-turbo"。
    deepseek 档位判定：
      - 含 "flash" 或 "reasoner" → flash 价表（reasoner 是 flash 的思考模式）
      - 含 "pro" → pro 价表
      - 无法识别 → 保守按 pro 价表记账
    doubao 档位判定：
      - 含 "turbo" → turbo 价表
      - 含 "pro" 或无法识别 → pro 价表（保守）
    """
    lowered = (model or "").lower()
    if lowered.startswith("doubao") or lowered.startswith("ep-"):
        if "turbo" in lowered:
            return "doubao-turbo"
        return "doubao-pro"
    if "flash" in lowered or "reasoner" in lowered:
        return "deepseek-flash"
    if "pro" in lowered:
        return "deepseek-pro"
    return "deepseek-pro"


def _provider_has_key(provider: str) -> bool:
    if provider == "doubao":
        return bool(os.environ.get("DOUBAO_API_KEY", "") or os.environ.get("ARK_API_KEY", ""))
    return bool(os.environ.get("LLM_API_KEY", "") or os.environ.get("OPENAI_API_KEY", ""))


def _resolve_provider_config(model: str) -> tuple[str, str, str]:
    provider = _provider_from_model(model)
    if provider == "doubao":
        return (
            os.environ.get("DOUBAO_API_KEY", "") or os.environ.get("ARK_API_KEY", ""),
            os.environ.get("DOUBAO_API_BASE", os.environ.get("ARK_API_BASE", DOUBAO_API_BASE)),
            "doubao",
        )
    return (
        os.environ.get("LLM_API_KEY", "") or os.environ.get("OPENAI_API_KEY", ""),
        os.environ.get("LLM_API_BASE", LLM_API_BASE),
        "deepseek",
    )


def _preferred_provider_order(module: str = "", now: Optional[datetime] = None) -> list[str]:
    if _is_interactive_auto_module(module) and _is_deepseek_peak_window(now):
        return ["doubao", "deepseek"]
    return ["deepseek", "doubao"]


def _resolve_provider_model(provider: str, model_tier: str = "llm_light", *, need_tools: bool = False, phase: str = "primary") -> str:
    # need_tools / phase 保留为兼容参数：Seed 2.1 收敛为 pro/turbo 两档后，
    # 豆包 fallback 不再有 mini 兜底档，统一用 turbo，故二者不再参与路由决策。
    #
    # 2026-09-11：原先对 doubao 是硬编码 if/else，与 DOUBAO_MODEL_ROUTING 字典
    # 重复定义且易失同步。改为统一查字典，保证改字典即生效。
    if provider == "doubao":
        return DOUBAO_MODEL_ROUTING.get(model_tier, DOUBAO_MODEL_ROUTING["llm_light"])
    return MODEL_ROUTING.get(model_tier, "deepseek-v4-flash")


def _fallback_tier_for(primary_model: str) -> str:
    """降级档位跟随主模型：只有主模型是 pro 才用 pro 档兜底，其余一律便宜档。

    全面 Flash 化后 model_tier 已不能直接反映实际模型（llm_heavy 也解析成 flash），
    所以降级档位必须从「真正要调用的主模型」反推，否则对话页手动选 Pro 会被
    降级成豆包 Turbo，与用户显式选择昂贵模型的意图冲突。
    """
    return "llm_heavy" if "pro" in (primary_model or "").lower() else "llm_light"


def resolve_model_candidates(model_tier: str = "llm_light", module: str = "", explicit_model: str = "", need_tools: bool = False, now: Optional[datetime] = None) -> list[str]:
    preferred = _preferred_provider_order(module, now=now)
    candidates: list[str] = []
    remaining = preferred[:]

    if explicit_model and explicit_model != "auto":
        explicit_provider = _provider_from_model(explicit_model)
        candidates.append(explicit_model)
        remaining = [provider for provider in preferred if provider != explicit_provider]
    else:
        primary_provider = preferred[0]
        candidates.append(_resolve_provider_model(primary_provider, model_tier, need_tools=need_tools, phase="primary"))
        remaining = preferred[1:]

    # 降级档位跟随「实际主模型」而非调用方传入的 tier：
    # llm_heavy 现在也解析成 flash，若继续拿 tier 当降级档位，会把所有重档调用
    # 的兜底抬成豆包 Pro（贵）；反之用户显式选 Pro 时又会被降成 Turbo（掉质量）。
    fallback_tier = _fallback_tier_for(candidates[0]) if candidates else model_tier
    for provider in remaining:
        candidates.append(_resolve_provider_model(provider, fallback_tier, need_tools=need_tools, phase="fallback"))

    deduped: list[str] = []
    for model in candidates:
        if model not in deduped:
            deduped.append(model)
    return deduped


def resolve_default_model(model_tier: str = "llm_light", module: str = "", now: Optional[datetime] = None) -> str:
    candidates = resolve_model_candidates(model_tier, module=module, now=now)
    for model in candidates:
        if _provider_has_key(_provider_from_model(model)):
            return model
    return candidates[0] if candidates else MODEL_ROUTING.get(model_tier, "deepseek-v4-flash")

# 限制
#
# 两个闸门职责完全不同，拒绝时必须分别点名（"拒绝必须说真话"铁律）：
#   - DAILY_LIMIT 是**成本闸门**：全局共享，防整体 LLM 支出失控（付费额度）。
#   - BURST_LIMIT 是**用户体验闸门**：按 user_id 分桶，防单个用户在一个窗口内
#     把自己刷爆（触发降级话术）。它不该被别的用户/脚本的调用殃及。
DAILY_LIMIT = 100      # 每天最多 100 次（全局，成本闸门；Phase 0 从 50 升级）
BURST_LIMIT = 10       # 5 分钟内最多 10 次（按用户，体验闸门）
BURST_WINDOW = 300     # 5 分钟窗口
CACHE_TTL = 3600       # 缓存 1 小时

# 突发窗口桶数量上限：防止 user_id 无界增长把内存撑爆。
# 超过上限时先清理已过期的空桶；若仍超限，把新用户归入共享哨兵桶
# （宁可让这批人互相共享限流，也不让内存无界增长）。
BURST_BUCKET_MAX = 500
# 找不到 user_id 的路径（未传 uid 的旧调用点）共用的哨兵桶名。
# 这个桶是**共享的**：里面每一次调用都会占用彼此的突发额度，可能互相误伤。
# 之所以允许共享而不是放行，是因为不放行最多是"误限流"，放行则是"闸门失守"。
_SHARED_BURST_BUCKET = "__shared_no_uid__"

# ---- 月度金额预算（TOKEN_BUDGET，v9.9.30 接通）----
#
# 背景：config.TOKEN_BUDGET 里的 monthly_budget_rmb / on_exceed / max_input_per_call
# 三个键此前**全仓零引用**（只在 config.py 和自己出现）—— 声明了"¥30/月硬上限"，
# 但唯一真正生效的闸门是 DAILY_LIMIT（按**调用次数**，不按金额）。
# 实测 data/llm_usage/ 里金额一直在正常记账，却没有任何一处拿它拦过调用。
# 这就是"闸门空转仍显绿"：配置+记账齐活，拦截缺失，健康检查照样显示正常。
#
# 现在接成两级真实分支（数据源就是 data/llm_usage/ 的既有日文件，不新造计数）：
#   ① 月度金额 ≥ monthly_budget_rmb * critical_threshold（默认 30*0.9=¥27）
#      → **降级档 A**：强制关闭 thinking + 输出上限压到 DEGRADED_MAX_TOKENS。
#        （仍然出内容，只是用最省的姿态出。切"更便宜的模型"这条路走不通 ——
#          全面 Flash 化后 deepseek-v4-flash 已是底档，没有更便宜的可切。）
#   ② 月度金额 ≥ monthly_budget_rmb（¥30 硬上限）
#      → **降级档 B**，按 on_exceed 语义：
#          - "degrade"（config 默认）：不调用 LLM，返回 source="budget_exceeded"
#            的兜底态，调用方走各自的规则引擎 —— 与
#            docs/token-budget-design.md §5.2 + config 注释"降级为规则引擎"一致。
#          - "warn_only"：只告警，照常调用。
#          - "hard_stop"：不调用，source="budget_hard_stop"。
#   ③ 单次 input 估算 > max_input_per_call → 不调用，source="input_over_budget"。
#      （设计文档写的是"截断上下文"，这里**故意不截断**：prompt 里混着 JSON 契约
#        与格式指令，盲截会破坏契约、让模型产出半截结构，代价比拒绝大。宁可降级。）
MONTHLY_SPEND_CACHE_TTL = 60   # 月度金额汇总缓存（避免每次调用读上百个日文件）
DEGRADED_MAX_TOKENS = 256      # 降级档 A 的输出硬上限

# 输出/输入边界守卫的 enforce 开关。**默认关**（shadow 模式）：
# 线上所有 LLM 输出都会真实跑一遍 red_team_audit，真实产生计数与日志，
# 但不因一条正则误杀正常回复。要真正拦截必须显式开这个环境变量。
GUARD_ENFORCE_ENV = "LLM_OUTPUT_GUARD_ENFORCE"
_TRUTHY = ("1", "true", "yes", "on")


@dataclass(frozen=True)
class _BudgetDecision:
    """预算闸门的裁决结果（纯数据，便于故障注入测试直接断言）。"""
    action: str    # "allow" | "degrade" | "refuse"
    reason: str    # "ok" | "monthly_critical" | "budget_exceeded" | "budget_hard_stop"
                   # | "monthly_warn_only" | "input_over_budget"
    detail: str    # 人可读的真实数字（必须能溯源到 llm_usage 文件）


# ---- 输出边界守卫（shadow，v9.9.30 接线）----
#
# 背景：infra/llm/red_team_audit.py（10KB，功能完整）与 infra/llm/chat_guard.py
# 此前**零生产 import** —— 只有 CI 脚本 / tests/test_skeleton_m1.py 碰它们。
# 文档里却写着"red_team_audit 拦截率 >99%""chat_guard 锚点强制 + 5 轮上限"，
# 而那个 99% 是在 ~28 条自选语料上算的。一个防护模块全仓零调用、却让 CI 和
# 健康检查显示"有防护"，就是在骗人。
#
# 现在接在 gateway 的**三个真实出口**（call_sync / stream_sync / call_multimodal）：
#   - red_team_audit.audit_response(text)：对真实 LLM 输出做禁用词/口径检测，
#     真实累加计数（get_output_guard_stats() 可见），默认只记录不拦截。
#   - chat_guard.check_action_seeking(prompt)：对 chat 系模块的用户提问做诱导检测。
#
# ⚠️ 诚实边界（不要当成"已全量接入"）：
#   1. chat_guard.validate_chat_request（锚点强制 + 5 轮上限）**仍然没有生产调用点**
#      —— 它需要 anchor_id/round_num，而 gateway 拿不到这些上下文，
#      models.schemas.ChatRequest 里也根本没有 anchor_id 字段。强行在 gateway
#      接它等于编造锚点。它的真正接入点是 chat API 层，属 M4 待办，本次不假装。
#   2. stream_sync 的 enforce 是**事后**的：文本已经流给用户了，拦不回来，
#      只能在收尾 chunk 标 error。故默认 shadow，enforce 仅用于自测。
_guard_stats: dict[str, int] = {
    "outputs_audited": 0,       # 真实出口审计过的输出数
    "output_violations": 0,     # 其中命中违规的输出数
    "chat_prompts_checked": 0,  # 走过 chat_guard 诱导检测的提问数
    "chat_action_seeking": 0,   # 其中判定为"索取操作建议"的
    "enforced_blocks": 0,       # 真正被拦下的次数（enforce 开时）
    "guard_errors": 0,          # 守卫自身异常（已放行，不影响链路）
}


def output_guard_enforce_enabled() -> bool:
    """enforce 开关：默认关（shadow）。"""
    return os.environ.get(GUARD_ENFORCE_ENV, "").strip().lower() in _TRUTHY


def get_output_guard_stats() -> dict[str, int]:
    """输出边界守卫的真实计数（供自测/体检证明"不是空转"）。"""
    return dict(_guard_stats)


def _shadow_audit_output(text: str, *, module: str = "", model: str = "") -> Optional[str]:
    """对 LLM 输出跑 red_team_audit 真实检测。

    Returns:
        None       —— 放行（shadow 模式恒为 None）
        "原因串"    —— 仅在 enforce 开启且命中违规时返回，调用方据此拦截
    """
    if not text or not text.strip():
        return None
    try:
        from infra.llm.red_team_audit import audit_response
        _guard_stats["outputs_audited"] += 1
        passed, violations = audit_response(text)
        if passed:
            return None
        _guard_stats["output_violations"] += 1
        first = violations[0] if violations else "unknown"
        print(f"[LLM_GATEWAY] 🛡️ red_team shadow 命中 {len(violations)} 处违规 "
              f"(module={module or '_unknown'} model={model or '_unknown'}) | {first[:120]}")
        if output_guard_enforce_enabled():
            _guard_stats["enforced_blocks"] += 1
            return f"red_team_blocked: {first[:120]}"
        return None
    except Exception as e:  # 守卫绝不能阻断 LLM 主链路
        _guard_stats["guard_errors"] += 1
        print(f"[LLM_GATEWAY] ⚠️ 输出守卫异常（已放行）: {e}")
        return None


def _shadow_guard_chat_input(prompt: str, *, module: str = "") -> None:
    """对 chat 系模块的用户提问跑 chat_guard.check_action_seeking（只记录）。"""
    if not prompt or not _is_interactive_auto_module(module):
        return
    try:
        from infra.llm.chat_guard import check_action_seeking
        _guard_stats["chat_prompts_checked"] += 1
        seeking, _fallback_text = check_action_seeking(prompt)
        if seeking:
            _guard_stats["chat_action_seeking"] += 1
            print(f"[LLM_GATEWAY] 🛡️ chat_guard shadow: 诱导式提问 "
                  f"(module={module})，当前仅记录不拦截"
                  f"（拦截动作属 chat API 层，M4 接入）")
    except Exception as e:
        _guard_stats["guard_errors"] += 1
        print(f"[LLM_GATEWAY] ⚠️ chat_guard 检测异常（已放行）: {e}")


def _estimate_input_tokens(text: str) -> int:
    """输入 token 估算**代理值**，仅用于 max_input_per_call 的量级判断。

    不做精确分词（无 tokenizer 依赖）：中文约 1 token/字、英文约 1 token/4 字符，
    折中取 len//2。它**不参与计费** —— 计费一律用 API 返回的真实 usage。
    """
    return max(0, len(text or "") // 2)


def _estimate_messages_input_tokens(messages: Any) -> int:
    """多模态 messages 的输入估算（按 JSON 序列化长度折算）。"""
    try:
        return max(0, len(json.dumps(messages, ensure_ascii=False, default=str)) // 2)
    except Exception:
        return 0

MODULE_META = {
    "name": "llm_gateway",
    "scope": "public",
    "input": ["prompt", "model_tier"],
    "output": "llm_response",
    "cost": "llm_light",
    "tags": ["infrastructure", "llm"],
    "description": "统一 LLM 调用入口：模型路由 + 缓存 + 计费 + 熔断",
    "layer": "infrastructure",
    "priority": 0,
}


class LLMGateway:
    """所有 LLM 调用的唯一入口"""

    _instance = None

    @classmethod
    def instance(cls) -> "LLMGateway":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._cache = MemoryCache(default_ttl=CACHE_TTL)  # LLM response cache
        self._usage: dict[str, dict[str, dict[str, Any]]] = {}  # {user_id: {module: {calls, tokens, cost}}}
        self._daily_count = 0
        self._daily_date = date.today()
        # 突发窗口按 user_id 分桶：{bucket_key: [时间戳, ...]}
        # 旧实现是单一 list，任何来源在 5 分钟内发 >10 次都会让所有用户降级。
        self._burst_windows: dict[str, list[float]] = {}
        self._cache_dirty = 0      # 脏缓存计数，每 5 次写磁盘
        # 月度金额汇总缓存：(month_prefix, 计算时刻, (金额, 有数据天数))
        self._monthly_spend_cache: Optional[tuple[str, float, tuple[float, int]]] = None
        self._load_cache_from_disk()  # 启动时从磁盘恢复缓存

    # ---- 缓存持久化（Phase 0 新增）----

    CACHE_FILE = Path(config.DATA_DIR) / "cache" / "llm_cache.json"

    def _load_cache_from_disk(self) -> None:
        """启动时从磁盘恢复 LLM 缓存（忽略已过期的条目）"""
        try:
            if self.CACHE_FILE.exists():
                raw = json.loads(self.CACHE_FILE.read_text(encoding="utf-8"))
                now = time.time()
                restored = 0
                for k, v in raw.items():
                    ts = v.get("ts", 0)
                    remaining_ttl = CACHE_TTL - (now - ts)
                    if remaining_ttl > 0:
                        self._cache.set(k, v["result"], ttl=int(remaining_ttl))
                        restored += 1
                if restored:
                    print(f"[LLM_GATEWAY] 💾 从磁盘恢复 {restored} 条缓存")
        except Exception as e:
            print(f"[LLM_GATEWAY] ⚠️ 缓存恢复失败（不影响运行）: {e}")

    def _persist_cache_to_disk(self) -> None:
        """将内存缓存写入磁盘（原子写，复用 infra/store）"""
        try:
            self.CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            # 只持久化未过期的条目（访问 MemoryCache 内部 _data）
            now = time.time()
            valid = {}
            with self._cache._lock:
                for k, entry in self._cache._data.items():
                    if entry.expires_at > now:
                        valid[k] = {"result": entry.value, "ts": now}
            # 原子写：复用 infra.store.atomic_write_json（消除内联重复 R3）
            from infra.store import atomic_write_json
            atomic_write_json(self.CACHE_FILE, valid)
        except Exception as e:
            print(f"[LLM_GATEWAY] ⚠️ 缓存持久化失败: {e}")

    # ---- 核心调用 ----

    def call_sync(self, prompt: str, *, system: str = "",
                  model_tier: str = "llm_light",
                  user_id: str = "", module: str = "",
                  max_tokens: int = 800,
                  explicit_model: str = "",
                  force_no_thinking: bool = False) -> dict[str, Any]:
        """同步调用 LLM（大多数场景用这个）

        force_no_thinking: 显式关闭推理模型 thinking（短输出场景）。
            置 True 时不提升 max_tokens 预算，按调用方给定值走。
        """
        # v9.5.140: 推理档（llm_heavy）保留 thinking，需要更大输出预算，
        # 否则 reasoning_content 挤占 content 导致截断（P0-1 全局修复）。
        # force_no_thinking=True 时调用方已明确要求关推理，预算不提升。
        if model_tier == "llm_heavy" and max_tokens < 3000 and not force_no_thinking:
            max_tokens = 3000
        # 0. 日期重置
        self._check_daily_reset()

        # 0.5 预算闸门（月度金额 / 单次 input 上限）——真实拦截分支，见 _budget_decision
        budget_degraded = False
        decision = self._budget_decision(
            input_tokens_est=_estimate_input_tokens(prompt), module=module,
        )
        if decision.action == "refuse":
            print(f"[LLM_GATEWAY] 🛑 预算闸门拒绝：{decision.reason} —— {decision.detail} "
                  f"module={module or '_unknown'}")
            return {
                "content": "", "source": decision.reason, "fallback": True,
                "model": "", "tokens": 0, "budget_blocked": True,
                "reason": decision.detail,
            }
        if decision.action == "degrade":
            max_tokens = min(max_tokens, DEGRADED_MAX_TOKENS)
            force_no_thinking = True  # 省掉 reasoning token
            budget_degraded = True
            print(f"[LLM_GATEWAY] 🔻 预算降级（强约束输出）：{decision.detail} "
                  f"module={module or '_unknown'} max_tokens→{max_tokens}")

        # 0.6 chat_guard 诱导检测（shadow，仅 chat 系模块）
        _shadow_guard_chat_input(prompt, module=module)

        # 1. 先解析目标模型（显式选模优先，其次再走峰谷默认）
        model = explicit_model or resolve_default_model(model_tier, module=module)

        # 2. 缓存命中？（模型必须参与 cache key，避免跨模型串缓存）
        cache_key = self._cache_key(user_id, module, prompt, system, model)
        cached = self._get_cache(cache_key)
        if cached is not None:
            return {**cached, "source": "cache"}

        # 3. 熔断检查
        #    拒绝必须报真实原因：日限与突发限是两个独立闸门，旧日志恒报 daily
        #    会把运维引向完全不相干的排查方向（生产实测 daily=10/100 却报"日限"）。
        refusal = self._limit_refusal_reason(user_id)
        if refusal is not None:
            print(f"[LLM_GATEWAY] ⚠️ 熔断！{self._describe_limit_refusal(refusal, user_id)}")
            return {
                "content": "",
                "source": "rate_limited",
                "fallback": True,
                "model": "",
                "tokens": 0,
            }
        self._consume_limit_quota(user_id)

        candidate_models = resolve_model_candidates(
            model_tier,
            module=module,
            explicit_model=explicit_model,
            need_tools=False,
        )

        # 5. 构建 messages
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        # 6. 调用（峰谷默认与降级顺序共用同一套候选链）
        def _do_call(use_model: str, use_key: str, use_base: str) -> tuple[int, Any]:
            """实际执行 POST，返回 (status_code, data_or_err_text)"""
            import httpx
            timeout = 60
            body = {
                "model": use_model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.7,
            }
            # 关闭 thinking 的策略（reasoning_content 与 content 共享 max_tokens）：
            # 1) force_no_thinking=True：调用方显式要求（短输出点），强制关闭所有推理模型
            # 2) DeepSeek 实际跑到 flash 档：关闭（避免截断 P0-1，且实测省 token）
            # 3) 豆包 Seed 非推理档：关闭（v9.5.130 既有逻辑）
            # 其余（DeepSeek 实际是 pro 档，即用户在对话页显式选 Pro）：保留推理，
            #     靠 call_sync 顶部提预算兜底
            #
            # v9.9.19：DeepSeek 判据从 model_tier 标签改成「实际解析出的模型」。
            # 全面 Flash 化后 llm_heavy 也解析成 deepseek-v4-flash，继续拿 tier
            # 当判据，晨报/监控/诊断/self_audit/scenario_engine 这些后台跑批会被
            # 当成「重档」而保留 thinking。
            #
            # 收益证据（2026-09-11 实测，同一 prompt 各 2 次）：
            #   | 状态           | completion token | 耗时      |
            #   | thinking 已关  | 51 ~ 133         | 1.1~1.5s |
            #   | thinking 没关  | 934 ~ 1355       | 6.9~9.1s |
            # ⚠️ 只看**绝对量级**，不要写成「N 倍」。n=2 的样本下倍数的置信区间
            # 宽到 7~27× 都能自圆其说，倍数根本无法证伪——之前这里写的
            # 「6.9~8.0 倍」其实是把 B 组的**耗时秒数**（6.9~9.1s）误当成了
            # 倍数，v9.9.20 更正。绝对 token 数是可证伪的：改动后如果后台跑批的
            # completion token 还停在 900+，就说明 thinking 没真关掉。
            # 判据与降级档位共用同一个 _fallback_tier_for()（模型名含 pro 才算重档），
            # 不新造平行判断函数，避免将来两处漂移。
            _is_deepseek_v4 = use_model.startswith("deepseek-v4")
            if force_no_thinking:
                if _is_deepseek_v4 or "doubao-seed" in use_model:
                    body["thinking"] = {"type": "disabled"}
            elif _is_deepseek_v4:
                # 只有「实际解析出 pro」才保留 thinking；flash 一律关掉
                if _fallback_tier_for(use_model) == "llm_light":
                    body["thinking"] = {"type": "disabled"}
            elif model_tier != "llm_light":
                # 豆包：保持 v9.5.130 既有逻辑，本次不动。
                # 注意这里仍按 model_tier 判，不能换成 _fallback_tier_for(use_model)：
                # doubao-seed-2-1-turbo 不含 "pro" 会被判成轻档而漏关。
                if "doubao-seed" in use_model:
                    body["thinking"] = {"type": "disabled"}
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(
                    f"{use_base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {use_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                if resp.status_code == 200:
                    return resp.status_code, resp.json()
                return resp.status_code, resp.text[:500]

        try:
            actual_model = model
            fallback_used = False
            data = None
            last_error = ""

            for idx, candidate_model in enumerate(candidate_models):
                api_key, api_base, provider = _resolve_provider_config(candidate_model)
                if not api_key:
                    last_error = f"{candidate_model}: no_key"
                    print(f"[LLM_GATEWAY] 跳过 {candidate_model}：未配置 key")
                    continue
                try:
                    if idx > 0:
                        print(f"[LLM_GATEWAY] 降级候选({idx + 1}/{len(candidate_models)}) → {candidate_model}")
                    status, payload = _do_call(candidate_model, api_key, api_base)
                    if status != 200:
                        _maybe_alert(
                            provider, status, payload if isinstance(payload, str) else "",
                            model=candidate_model, module=module,
                        )
                        raise RuntimeError(f"HTTP {status}: {payload}")
                    _msg0 = payload.get("choices", [{}])[0].get("message", {})
                    if not (_msg0.get("content") or "").strip() and (_msg0.get("reasoning_content") or "").strip():
                        _rc_len = len(_msg0.get("reasoning_content", ""))
                        raise RuntimeError(f"content_empty: {candidate_model} returned only reasoning ({_rc_len}chars), fallback needed")
                    data = payload
                    actual_model = candidate_model
                    # v9.9.19 归一化漂移监控：deepseek-v4-flash 等 ID 由上游静默归一化，
                    # 一旦官方换挡/下架旧 ID，这里立刻可见，不用等账单或输出质量出问题才发现。
                    _api_model = str(payload.get("model") or "").strip()
                    if _api_model and _api_model != candidate_model:
                        print(f"[LLM_GATEWAY] ⚠️ 模型ID归一化: 请求={candidate_model} 实际生效={_api_model}")
                    fallback_used = idx > 0
                    if fallback_used:
                        print(f"[LLM_GATEWAY] ✅ 降级成功 ({actual_model})")
                    break
                except Exception as candidate_err:
                    last_error = str(candidate_err)
                    print(f"[LLM_GATEWAY] {candidate_model} 调用失败: {candidate_err}")
                    continue

            if data is None:
                return {
                    "content": "", "source": "api_error",
                    "fallback": True, "model": model,
                    "tokens": 0, "error": last_error or "all_candidates_failed",
                }

            # 解析响应
            msg = data["choices"][0]["message"]
            content = msg.get("content") or ""
            reasoning = msg.get("reasoning_content") or ""
            # content 为空但有 reasoning 说明模型进入了思维链模式但没给出最终答案
            # 此时 content 已经在降级链入口被检测到了，到这里就是降级后的结果，正常使用
            if not content.strip() and reasoning.strip():
                # 降级后仍然是 reasoning only → 取 reasoning 最后一段作为输出（尽力而为）
                content = reasoning.strip().split('\n')[-1][:300]
                print(f"[LLM_GATEWAY] 降级后仍 content 为空，取 reasoning 末尾: {len(content)}chars")
            usage = data.get("usage", {})
            total_tokens = usage.get("total_tokens", 0)
            cache_hit_tk = usage.get("prompt_cache_hit_tokens", 0)
            cache_miss_tk = usage.get("prompt_cache_miss_tokens", 0)

            # v9.9.10: 透出 finish_reason，供调用方识别输出被 max_tokens 截断
            # （截断的 JSON 是半截字符串，解析必然失败，绝不应当作结论文本使用）
            finish_reason = data.get("choices", [{}])[0].get("finish_reason", "")
            result = {
                "content": content,
                "reasoning": reasoning,
                "source": "ai",
                "model": actual_model,
                "tokens": total_tokens,
                "cache_hit_tokens": cache_hit_tk,
                "cache_miss_tokens": cache_miss_tk,
                "fallback": False,
                "fallback_used": fallback_used,
                "finish_reason": finish_reason,
                "budget_degraded": budget_degraded,
            }
            # 先记账（钱已经花了），再跑输出边界守卫；被拦的内容**不得进缓存**
            self._record_usage(user_id, module, actual_model, total_tokens)
            input_tk = usage.get("prompt_tokens", usage.get("input_tokens", 0))
            output_tk = usage.get("completion_tokens", usage.get("output_tokens", 0))
            self._record_token_cost(
                user_id, actual_model, input_tk, output_tk,
                cache_hit_tokens=cache_hit_tk,
                cache_miss_tokens=cache_miss_tk,
            )
            block_reason = _shadow_audit_output(content, module=module, model=actual_model)
            if block_reason is not None:
                # enforce 开且命中禁用词 → 不把脏内容交给调用方，也不写缓存
                return {
                    "content": "", "source": "red_team_blocked", "fallback": True,
                    "model": actual_model, "tokens": total_tokens,
                    "error": block_reason, "red_team_blocked": True,
                }
            self._set_cache(cache_key, result)
            return result

        except Exception as e:
            print(f"[LLM_GATEWAY] 调用失败: {e}")
            return {
                "content": "", "source": "error",
                "fallback": True, "model": model,
                "tokens": 0, "error": str(e),
            }

    def stream_sync(self, prompt: str, *, system: str = "",
                    model_tier: str = "llm_light",
                    user_id: str = "", module: str = "",
                    max_tokens: int = 1200,
                    history: list[Any] | None = None,
                    explicit_model: str = "",
                    need_tools: bool = False,
                    force_no_thinking: bool = False) -> Iterator[dict[str, Any]]:
        """流式调用 LLM，yield 标准化的 chunk dict。

        返回同步 Generator[dict, None, None]。
        每个 chunk: {"delta": str, "phase": "thinking"|"answering", "done": bool}
        最后一个 chunk: {"delta": "", "done": True, "usage": {...}}
        错误时: {"delta": "", "done": True, "error": str, "fallback": True}

        history: 多轮对话历史，格式 [{"role":"user"|"assistant","content":str}]
        explicit_model: 用户指定的模型 ID（如 "doubao-seed-2-1-turbo-260628"），优先于 model_tier
        need_tools: 是否需要工具调用能力（Function Calling 场景，降级到豆包时优先选 Turbo）
        不走缓存（streaming 场景缓存无意义），但走限流和计费。

        force_no_thinking: 显式关闭推理模型 thinking（短输出场景）。
            置 True 时不提升 max_tokens 预算，按调用方给定值走。
        """
        # v9.5.140: 推理档（llm_heavy）保留 thinking，需要更大输出预算，
        # 否则 reasoning_content 挤占 content 导致截断（P0-1 全局修复）。
        # force_no_thinking=True 时调用方已明确要求关推理，预算不提升。
        if model_tier == "llm_heavy" and max_tokens < 3000 and not force_no_thinking:
            max_tokens = 3000
        # 0. 日期重置
        self._check_daily_reset()

        # 0.5 预算闸门（月度金额 / 单次 input 上限）——真实拦截分支，见 _budget_decision
        budget_degraded = False
        decision = self._budget_decision(
            input_tokens_est=_estimate_input_tokens(prompt), module=module,
        )
        if decision.action == "refuse":
            print(f"[LLM_GATEWAY] 🛑 stream 预算闸门拒绝：{decision.reason} —— "
                  f"{decision.detail} module={module or '_unknown'}")
            yield {"delta": "", "done": True, "error": decision.reason,
                   "fallback": True, "budget_blocked": True, "reason": decision.detail}
            return
        if decision.action == "degrade":
            max_tokens = min(max_tokens, DEGRADED_MAX_TOKENS)
            force_no_thinking = True
            budget_degraded = True
            print(f"[LLM_GATEWAY] 🔻 stream 预算降级（强约束输出）：{decision.detail} "
                  f"module={module or '_unknown'} max_tokens→{max_tokens}")

        # 0.6 chat_guard 诱导检测（shadow，仅 chat 系模块）
        _shadow_guard_chat_input(prompt, module=module)

        # 1. 熔断检查（拒绝必须报真实原因，见 call_sync 处注释）
        refusal = self._limit_refusal_reason(user_id)
        if refusal is not None:
            print(f"[LLM_GATEWAY] ⚠️ stream 熔断！{self._describe_limit_refusal(refusal, user_id)}")
            yield {"delta": "", "done": True, "error": "rate_limited", "fallback": True}
            return
        self._consume_limit_quota(user_id)

        # 2. 模型候选链（主模型 + 按时间窗口切换的降级顺序）
        candidate_models = resolve_model_candidates(
            model_tier,
            module=module,
            explicit_model=explicit_model,
            need_tools=need_tools,
        )
        model = candidate_models[0] if candidate_models else (explicit_model or resolve_default_model(model_tier, module=module))

        # 4. 构建 messages（支持多轮历史）
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        # 注入多轮对话历史（最多10条，奇偶交替 user/assistant）
        if history:
            for h in history[-10:]:
                role = h.get("role", "user") if isinstance(h, dict) else h.role
                content = h.get("content", "") if isinstance(h, dict) else h.content
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": prompt})

        # 5. 流式调用（主模型失败后按候选链继续降级）
        def _do_stream(use_model: str, use_key: str, use_base: str) -> Iterator[dict[str, Any]]:
            """实际执行流式调用，yield chunk"""
            import httpx
            timeout = 60
            # 关闭 thinking 的策略（reasoning_content 与 content 共享 max_tokens）：
            # 1) force_no_thinking=True：调用方显式要求（短输出点），强制关闭所有推理模型
            # 2) DeepSeek 实际跑到 flash 档：关闭（避免截断 P0-1，且实测省 token）
            # 3) 豆包 Seed 非推理档：关闭（v9.5.130 既有逻辑）
            # 其余（DeepSeek 实际是 pro 档，即用户在对话页显式选 Pro）：保留推理，
            #     靠上方「llm_heavy 抬高 max_tokens 下限」提预算兜底
            #
            # v9.9.19：判据与 call_sync 完全一致（同步/流式两份逻辑本就互为镜像），
            # 从 model_tier 标签改成「实际解析出的模型」。对话页三个入口
            # （api/chat.py:72/618/835）全走流式，同步侧改了流式没改，等于
            # 「手动选 Pro 保留 thinking」这条决策在真实路径上不生效。
            # 复用 _fallback_tier_for()，与降级档位共用同一个「是不是 pro 档」的
            # 定义，不新造平行判断函数。
            stream_body = {
                "model": use_model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.7,
                "stream": True,
            }
            _is_deepseek_v4_stream = use_model.startswith("deepseek-v4")
            if force_no_thinking:
                if _is_deepseek_v4_stream or "doubao-seed" in use_model:
                    stream_body["thinking"] = {"type": "disabled"}
            elif _is_deepseek_v4_stream:
                # 只有「实际解析出 pro」才保留 thinking；flash 一律关掉
                if _fallback_tier_for(use_model) == "llm_light":
                    stream_body["thinking"] = {"type": "disabled"}
            elif model_tier != "llm_light":
                # 豆包：保持 v9.5.130 既有逻辑，本次不动。
                # 注意这里仍按 model_tier 判，不能换成 _fallback_tier_for(use_model)：
                # doubao-seed-2-1-turbo 不含 "pro" 会被判成轻档而漏关。
                if "doubao-seed" in use_model:
                    stream_body["thinking"] = {"type": "disabled"}
            with httpx.Client(timeout=timeout) as client:
                with client.stream(
                    "POST",
                    f"{use_base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {use_key}",
                        "Content-Type": "application/json",
                    },
                    json=stream_body,
                ) as resp:
                    if resp.status_code != 200:
                        try:
                            err_body = resp.read().decode("utf-8", errors="ignore")[:500]
                        except Exception:
                            err_body = ""
                        _maybe_alert(
                            _provider_from_model(use_model), resp.status_code, err_body,
                            model=use_model, module=module,
                        )
                        yield {"_http_error": resp.status_code, "_err_body": err_body}
                        return
                    for line in resp.iter_lines():
                        if not line.startswith("data: "):
                            continue
                        payload = line[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            delta_obj = chunk.get("choices", [{}])[0].get("delta", {})
                            reasoning = delta_obj.get("reasoning_content", "")
                            content = delta_obj.get("content", "")
                            usage = chunk.get("usage")
                            yield {"reasoning": reasoning, "content": content, "usage": usage}
                        except (json.JSONDecodeError, IndexError, KeyError):
                            continue

        try:
            total_content = ""
            total_reasoning = ""
            usage = {}
            fallback_used = False
            actual_model = model
            last_error = ""

            def _consume(it: Iterator[dict[str, Any]]) -> Iterator[dict[str, Any]]:
                """处理流式 chunks 并 yield 外部格式"""
                nonlocal total_content, total_reasoning, usage
                for c in it:
                    if "_http_error" in c:
                        err_body = (c.get("_err_body") or "")[:300]
                        raise RuntimeError(f"HTTP {c['_http_error']} body={err_body}")
                    if c.get("usage"):
                        usage = c["usage"]
                    if c.get("reasoning"):
                        total_reasoning += c["reasoning"]
                        yield {"delta": c["reasoning"], "phase": "thinking", "done": False}
                    elif c.get("content"):
                        total_content += c["content"]
                        yield {"delta": c["content"], "phase": "answering", "done": False}

            success = False
            for idx, candidate_model in enumerate(candidate_models):
                api_key, api_base, provider = _resolve_provider_config(candidate_model)
                if not api_key:
                    last_error = f"{candidate_model}: no_key"
                    print(f"[LLM_GATEWAY] stream 跳过 {candidate_model}：未配置 key")
                    continue
                total_content = ""
                total_reasoning = ""
                usage = {}
                try:
                    if idx > 0:
                        print(f"[LLM_GATEWAY] stream 降级候选({idx + 1}/{len(candidate_models)}) → {candidate_model}")
                    actual_model = candidate_model
                    fallback_used = idx > 0
                    yield from _consume(_do_stream(candidate_model, api_key, api_base))
                    success = True
                    if fallback_used:
                        print(f"[LLM_GATEWAY] ✅ stream 降级成功 ({actual_model})")
                    break
                except Exception as candidate_err:
                    last_error = str(candidate_err)
                    print(f"[LLM_GATEWAY] stream {provider} 失败: {candidate_err}")
                    continue

            if not success:
                yield {"delta": "", "done": True, "error": last_error or "all_candidates_failed", "fallback": True}
                return

            # 6. 流结束 — 计费
            estimated_tokens = len(total_content + total_reasoning) // 2 + len(prompt) // 3
            total_tokens = usage.get("total_tokens", estimated_tokens) if usage else estimated_tokens
            input_tk = usage.get("prompt_tokens", len(prompt) // 3) if usage else len(prompt) // 3
            output_tk = usage.get("completion_tokens", len(total_content + total_reasoning) // 2) if usage else len(total_content + total_reasoning) // 2
            cache_hit_tk = usage.get("prompt_cache_hit_tokens", 0) if usage else 0
            cache_miss_tk = usage.get("prompt_cache_miss_tokens", 0) if usage else 0

            self._record_usage(user_id, module, actual_model, total_tokens)
            self._record_token_cost(user_id, actual_model, input_tk, output_tk,
                                    cache_hit_tokens=cache_hit_tk,
                                    cache_miss_tokens=cache_miss_tk)

            # 输出边界守卫（shadow）。注意：流式场景内容**已经**逐块发给用户了，
            # enforce 只能是事后的（拦不回来），此处仅在 enforce 命中时于收尾
            # chunk 标 error，让调用方知道这段输出不可信。
            block_reason = _shadow_audit_output(total_content, module=module, model=actual_model)
            if block_reason is not None:
                yield {"delta": "", "done": True, "error": "red_team_blocked",
                       "fallback": True, "model": actual_model,
                       "red_team_blocked": True, "reason": block_reason}
                return

            yield {
                "delta": "", "done": True,
                "model": actual_model,
                "tokens": total_tokens,
                "content_length": len(total_content),
                "fallback_used": fallback_used,
                "budget_degraded": budget_degraded,
            }

        except Exception as e:
            print(f"[LLM_GATEWAY] stream 调用失败: {e}")
            yield {"delta": "", "done": True, "error": str(e), "fallback": True}

    def call_multimodal(self, messages: list[Any], *, model: str = "",
                        user_id: str = "", module: str = "",
                        max_tokens: int = 800) -> dict[str, Any]:
        """多模态调用（视觉/图片识别等），接受预组装的 messages。

        与 call_sync 的区别：
        1. 不走 MODEL_ROUTING（vision 模型直接由 model 参数指定）
        2. messages 由调用方完整构造（包含 image_url 等复杂结构）
        3. 不走缓存（图片内容无法稳定 hash）

        返回格式与 call_sync 一致。

        v9.9.11: 补视觉降级链 —— DeepSeek vision 失败时降级到豆包视觉模型
        （`LLM_VISION_MODEL_DOUBAO`，默认 doubao-seed-2-1-pro-260628，其支持图片输入）。
        """
        # 0. 日期重置
        self._check_daily_reset()

        # 0.5 预算闸门（月度金额 / 单次 input 上限）——真实拦截分支，见 _budget_decision
        budget_degraded = False
        decision = self._budget_decision(
            input_tokens_est=_estimate_messages_input_tokens(messages), module=module,
        )
        if decision.action == "refuse":
            print(f"[LLM_GATEWAY] 🛑 multimodal 预算闸门拒绝：{decision.reason} —— "
                  f"{decision.detail} module={module or '_unknown'}")
            return {
                "content": "", "source": decision.reason, "fallback": True,
                "model": model, "tokens": 0, "budget_blocked": True,
                "reason": decision.detail,
            }
        if decision.action == "degrade":
            max_tokens = min(max_tokens, DEGRADED_MAX_TOKENS)
            budget_degraded = True
            print(f"[LLM_GATEWAY] 🔻 multimodal 预算降级（强约束输出）：{decision.detail} "
                  f"module={module or '_unknown'} max_tokens→{max_tokens}")

        # 1. 熔断检查（拒绝必须报真实原因，见 call_sync 处注释）
        refusal = self._limit_refusal_reason(user_id)
        if refusal is not None:
            print(f"[LLM_GATEWAY] ⚠️ multimodal 熔断！{self._describe_limit_refusal(refusal, user_id)}")
            return {"content": "", "source": "rate_limited", "fallback": True, "model": "", "tokens": 0}
        self._consume_limit_quota(user_id)

        # 2. 视觉模型降级链（主：DeepSeek vision，备：豆包视觉）
        if not model:
            model = os.environ.get("LLM_VISION_MODEL", "deepseek-v4-flash-vision-exp")
        fallback_model = os.environ.get(
            "LLM_VISION_MODEL_DOUBAO",
            os.environ.get("DOUBAO_VISION_MODEL", "doubao-seed-2-1-pro-260628"),
        )

        # 构建候选链：主模型 + 豆包备胎（去重，避免主模型本身已是豆包时重复）
        candidates: list[tuple[str, str, str]] = []  # (model, key, base)
        for cand_model in dict.fromkeys([model, fallback_model]):
            key, base = self._resolve_vision_config(cand_model)
            if key:
                candidates.append((cand_model, key, base))

        if not candidates:
            return {"content": "", "source": "no_key", "fallback": True, "model": model, "tokens": 0}

        # 3. 依次尝试
        last_error = ""
        for cand_model, api_key, api_base in candidates:
            try:
                import httpx
                with httpx.Client(timeout=30) as client:
                    resp = client.post(
                        f"{api_base}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": cand_model,
                            "messages": messages,
                            "max_tokens": max_tokens,
                        },
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        msg = data["choices"][0]["message"]
                        content = msg.get("content") or ""
                        reasoning = msg.get("reasoning_content") or ""
                        # 视觉模型可能只返回 reasoning_content 而没有最终 content
                        if not content.strip() and reasoning.strip():
                            content = reasoning.strip().split('\n')[-1][:800]
                        usage = data.get("usage", {})
                        total_tokens = usage.get("total_tokens", 0)
                        input_tk = usage.get("prompt_tokens", 0)
                        output_tk = usage.get("completion_tokens", 0)

                        # 计费
                        self._record_usage(user_id, module, cand_model, total_tokens)
                        self._record_token_cost(user_id, cand_model, input_tk, output_tk)

                        # 输出边界守卫（shadow）
                        block_reason = _shadow_audit_output(
                            content, module=module, model=cand_model)
                        if block_reason is not None:
                            return {
                                "content": "", "source": "red_team_blocked",
                                "fallback": True, "model": cand_model,
                                "tokens": total_tokens, "error": block_reason,
                                "red_team_blocked": True,
                            }

                        return {
                            "content": content,
                            "source": "ai",
                            "model": cand_model,
                            "tokens": total_tokens,
                            "fallback": False,
                            "fallback_used": cand_model != model,
                            "budget_degraded": budget_degraded,
                        }
                    else:
                        last_error = f"HTTP {resp.status_code}"
                        print(f"[LLM_GATEWAY] multimodal {cand_model} API error: {resp.status_code} {resp.text[:200]}")
                        continue
            except Exception as e:
                last_error = str(e)
                print(f"[LLM_GATEWAY] multimodal {cand_model} 调用失败: {e}")
                continue

        return {
            "content": "",
            "source": "api_error",
            "fallback": True,
            "model": model,
            "tokens": 0,
            "error": last_error or "all_vision_candidates_failed",
        }

    def _resolve_vision_config(self, model: str) -> tuple[str, str]:
        """解析视觉模型对应的 (api_key, api_base)。

        豆包模型走豆包 key/base，其余走 DeepSeek。
        """
        if model.startswith("doubao") or model.startswith("ep-"):
            key = os.environ.get("DOUBAO_API_KEY", "") or os.environ.get("ARK_API_KEY", "")
            base = os.environ.get("DOUBAO_API_BASE", os.environ.get("ARK_API_BASE", DOUBAO_API_BASE))
            return key, base
        key = os.environ.get("LLM_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
        base = os.environ.get("LLM_API_BASE", "https://api.deepseek.com/v1")
        return key, base

    # ---- 缓存 ----

    def _cache_key(self, user_id: str, module: str, prompt: str, system: str = "", model: str = "") -> str:
        raw = f"{user_id}:{module}:{model}:{system[:100]}:{prompt[:500]}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _get_cache(self, key: str) -> Any:
        return self._cache.get(key)

    def _set_cache(self, key: str, result: dict[str, Any]) -> None:
        self._cache.set(key, result)
        # 清理过期缓存（超过 200 条时）
        if self._cache.size() > 200:
            pass  # MemoryCache.size() already prunes expired entries
        # 每 5 次新缓存写一次磁盘（Phase 0 持久化）
        self._cache_dirty += 1
        if self._cache_dirty >= 5:
            self._persist_cache_to_disk()
            self._cache_dirty = 0

    # ---- 熔断 ----

    def _check_daily_reset(self) -> None:
        today = date.today()
        if self._daily_date != today:
            self._daily_count = 0
            self._daily_date = today
            self._burst_windows = {}

    # ---- 突发窗口分桶 ----

    def _prune_burst_buckets(self) -> None:
        """清理已无有效时间戳的桶（含过期桶与空桶），控制 dict 规模。"""
        now = time.time()
        stale = [
            key for key, stamps in list(self._burst_windows.items())
            if all(now - t >= BURST_WINDOW for t in stamps)
        ]
        for key in stale:
            del self._burst_windows[key]

    def _burst_bucket(self, user_id: str) -> list[float]:
        """取（必要时创建）某用户的突发窗口桶。

        桶数量上限保护见 BURST_BUCKET_MAX / _SHARED_BURST_BUCKET 的常量注释。
        返回的是 dict 内的**同一个 list 对象**，调用方可原地裁剪。
        """
        key = user_id if user_id else _SHARED_BURST_BUCKET
        bucket = self._burst_windows.get(key)
        if bucket is not None:
            return bucket
        if len(self._burst_windows) >= BURST_BUCKET_MAX:
            self._prune_burst_buckets()
            if len(self._burst_windows) >= BURST_BUCKET_MAX:
                key = _SHARED_BURST_BUCKET
        return self._burst_windows.setdefault(key, [])

    def _burst_used(self, user_id: str = "") -> int:
        """当前用户在突发窗口内已消耗的次数（只读，顺手裁剪过期时间戳）。"""
        bucket = self._burst_bucket(user_id)
        now = time.time()
        bucket[:] = [t for t in bucket if now - t < BURST_WINDOW]
        return len(bucket)

    def _limit_refusal_reason(self, user_id: str = "") -> Optional[str]:
        """判定"若现在调用会不会被拒"，返回**真实原因**。

        - None    : 未熔断，可以调用
        - "daily" : 日限（全局成本闸门）已到
        - "burst" : 该用户的突发限流（体验闸门）已到

        纯判定，**不消耗配额**；调用点据此打印诚实的熔断日志。
        """
        if self._daily_count >= DAILY_LIMIT:
            return "daily"
        if self._burst_used(user_id) >= BURST_LIMIT:
            return "burst"
        return None

    def _describe_limit_refusal(self, reason: str, user_id: str = "") -> str:
        """构造熔断日志正文：同时给出两个闸门的真实数字，并点名真实原因。

        绝不只打印 daily=：突发限流时 daily 通常是 10/100 这种"远未到"的值，
        只报 daily 会让人以为日限触发，把排查完全带偏。
        """
        burst_used = self._burst_used(user_id)
        bucket_label = user_id or _SHARED_BURST_BUCKET
        if reason == "daily":
            reason_label = "日限"
            scope = "全局成本闸门"
        else:
            reason_label = "突发限流"
            scope = f"体验闸门/用户桶={bucket_label}"
        return (
            f"原因={reason_label}（{scope}） "
            f"daily={self._daily_count}/{DAILY_LIMIT} "
            f"burst={burst_used}/{BURST_LIMIT}（窗口 {BURST_WINDOW}s）"
        )

    def _consume_limit_quota(self, user_id: str = "") -> None:
        """消耗一次配额（调用方须先用 _limit_refusal_reason 确认未熔断）。"""
        self._daily_count += 1
        self._burst_bucket(user_id).append(time.time())

    def _check_limits(self, user_id: str = "") -> bool:
        """限流判定 + 消耗配额。返回 False 表示被拒（对外契约保持不变）。

        需要知道"为什么被拒"的调用点，请用 `_limit_refusal_reason()`（纯判定）。
        user_id 用于突发窗口分桶；不传则落入共享哨兵桶（见常量注释）。
        """
        if self._limit_refusal_reason(user_id) is not None:
            return False
        self._consume_limit_quota(user_id)
        return True

    def pre_check(self, user_id: str = "") -> bool:
        """流式调用前的限流检查，通过返回 True 并消耗一次配额。

        用于 streaming 场景：调用者先 pre_check()，再自行发 httpx stream 请求。
        这样 stream 也纳入日限/突发限控制。

        user_id：突发窗口按用户分桶的键。不传则落入共享哨兵桶——多个不传 uid
        的调用方会互相占用突发额度，可能互相误伤，故新增调用点请务必传入。
        """
        self._check_daily_reset()
        return self._check_limits(user_id)

    # ---- 计费 ----

    def _record_usage(self, user_id: str, module: str, model: str, tokens: int) -> None:
        if not user_id:
            user_id = "_anonymous"
        if not module:
            module = "_unknown"
        if user_id not in self._usage:
            self._usage[user_id] = {}
        if module not in self._usage[user_id]:
            self._usage[user_id][module] = {"calls": 0, "tokens": 0, "models": {}}
        u = self._usage[user_id][module]
        u["calls"] += 1
        u["tokens"] += tokens
        u["models"][model] = u["models"].get(model, 0) + 1

    # ---- Phase 0: 金额制 Token 预算 ----

    def _record_token_cost(self, user_id: str, model: str,
                           input_tokens: int, output_tokens: int,
                           cache_hit_tokens: int = 0,
                           cache_miss_tokens: int = 0) -> None:
        """记录本次调用的金额成本到磁盘（按天+按用户双维度）

        按具体模型选价目：
        - deepseek：分 flash/pro 两档，cache_hit/miss 与输出价都按峰谷窗口选值
        - doubao：价目未知（PROVIDER_PRICING=None），只记用量不计费
        """
        try:
            from config import TOKEN_BUDGET, PROVIDER_PRICING

            pricing_key = _pricing_key_from_model(model)
            pricing = PROVIDER_PRICING.get(pricing_key)
            if not pricing:
                # 价目未知（doubao），跳过金额记账
                return

            # deepseek 输出价 + 输入缓存命中/未命中价都按峰谷窗口选择
            is_peak = _is_deepseek_peak_window()
            output_rate = pricing["output_peak"] if is_peak else pricing["output_valley"]
            hit_rate = pricing["input_cache_hit_peak"] if is_peak else pricing["input_cache_hit_valley"]
            miss_rate = pricing["input_cache_miss_peak"] if is_peak else pricing["input_cache_miss_valley"]

            # 用真实命中/未命中 token 算真实成本
            # 若没返回这俩字段（老 API），回退到 hit/miss 平均估算
            if cache_hit_tokens + cache_miss_tokens > 0:
                cost = (
                    cache_hit_tokens * hit_rate
                    + cache_miss_tokens * miss_rate
                    + output_tokens * output_rate
                ) / 1_000_000
                cache_ratio = cache_hit_tokens / (cache_hit_tokens + cache_miss_tokens)
            else:
                input_rate = (hit_rate + miss_rate) / 2
                cost = (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
                cache_ratio = None

            # 读取今日全局用量
            usage_dir = Path(config.DATA_DIR) / "llm_usage"
            usage_dir.mkdir(parents=True, exist_ok=True)
            usage_file = usage_dir / f"{date.today()}.json"

            if usage_file.exists():
                daily = json.loads(usage_file.read_text(encoding="utf-8"))
            else:
                daily = {
                    "date": date.today().isoformat(),
                    "input_tokens": 0, "output_tokens": 0,
                    "cache_hit_tokens": 0, "cache_miss_tokens": 0,
                    "cost_rmb": 0.0, "calls": 0,
                }

            daily["input_tokens"] += input_tokens
            daily["output_tokens"] += output_tokens
            daily["cache_hit_tokens"] = daily.get("cache_hit_tokens", 0) + cache_hit_tokens
            daily["cache_miss_tokens"] = daily.get("cache_miss_tokens", 0) + cache_miss_tokens
            daily["cost_rmb"] = round(daily["cost_rmb"] + cost, 4)
            daily["calls"] += 1

            # 原子写（复用 infra/store，不变式 #5：文件 IO 走 infra/store）
            from infra.store import atomic_write_json
            atomic_write_json(usage_file, daily)

            # 按用户记录
            user_dir = usage_dir / "by_user"
            user_dir.mkdir(parents=True, exist_ok=True)
            user_file = user_dir / f"{user_id}_{date.today()}.json"
            if user_file.exists():
                user_daily = json.loads(user_file.read_text(encoding="utf-8"))
            else:
                user_daily = {"user_id": user_id, "date": date.today().isoformat(), "cost_rmb": 0.0, "calls": 0}
            user_daily["cost_rmb"] = round(user_daily["cost_rmb"] + cost, 4)
            user_daily["calls"] += 1
            atomic_write_json(user_file, user_daily)

            # 预警检查
            budget = cast(float, TOKEN_BUDGET.get("daily_budget_rmb", 3.0))
            alert_pct = cast(float, TOKEN_BUDGET.get("alert_threshold", 0.7))
            critical_pct = cast(float, TOKEN_BUDGET.get("critical_threshold", 0.9))

            if daily["cost_rmb"] >= budget * critical_pct:
                print(f"[LLM_GATEWAY] 🔴 日预算 90%！¥{daily['cost_rmb']:.2f} / ¥{budget}")
            elif daily["cost_rmb"] >= budget * alert_pct:
                print(f"[LLM_GATEWAY] 🟡 日预算 70%！¥{daily['cost_rmb']:.2f} / ¥{budget}")

            # V7.6: 命中率偏低时打印提示（前 30 次调用后）
            if daily["calls"] >= 30:
                total_input = daily.get("cache_hit_tokens", 0) + daily.get("cache_miss_tokens", 0)
                if total_input > 0:
                    daily_hit_ratio = daily["cache_hit_tokens"] / total_input
                    if daily_hit_ratio < 0.3 and daily["calls"] % 20 == 0:
                        print(f"[LLM_GATEWAY] 📉 今日缓存命中率 {daily_hit_ratio * 100:.1f}% < 30%，"
                              f"建议检查 system prompt 前缀是否稳定")

        except Exception as e:
            print(f"[LLM_GATEWAY] ⚠️ Token 记账失败（不影响调用）: {e}")

    def record_external_call(self, *, user_id: str, module: str, model: str,
                             input_tokens: int, output_tokens: int,
                             cache_hit_tokens: int = 0, cache_miss_tokens: int = 0) -> None:
        """供外部直连调用点（FC / multi_model_scorer 等）记账的窄接口。

        统一走 usage + cost 两步：
        - usage 始终记录（调用次数与 token 用量）
        - cost 由 _record_token_cost 按 provider 价目决定（价目未知则跳过金额）
        """
        self._record_usage(user_id, module, model, input_tokens + output_tokens)
        self._record_token_cost(user_id, model, input_tokens, output_tokens,
                                cache_hit_tokens, cache_miss_tokens)

    def get_api_config(self, model_tier: str = "llm_light", module: str = "") -> dict[str, Any]:
        """返回当前默认模型对应的 API 配置。"""
        model = resolve_default_model(model_tier, module=module)
        api_key, api_base, _provider = _resolve_provider_config(model)
        return {"api_key": api_key, "api_base": api_base, "model": model}

    # ---- 月度金额预算闸门（v9.9.30 接通 TOKEN_BUDGET.monthly_budget_rmb）----

    def _monthly_spend_rmb(self, now: Optional[datetime] = None) -> tuple[float, int]:
        """汇总本月已花的金额（¥）与有数据天数。

        **数据源就是既有记账产物** `data/llm_usage/YYYY-MM-DD.json` 的 cost_rmb，
        不新造任何计数（单价与峰谷由 _record_token_cost 决定）。
        结果缓存 MIDDAY_SPEND_CACHE_TTL 秒，避免每次调用都读上百个日文件。
        """
        now = _china_now(now)
        month_prefix = now.strftime("%Y-%m")
        cached = self._monthly_spend_cache
        if cached is not None and cached[0] == month_prefix \
                and (time.time() - cached[1]) < MONTHLY_SPEND_CACHE_TTL:
            return cached[2]

        total = 0.0
        day_files = 0
        try:
            usage_dir = Path(config.DATA_DIR) / "llm_usage"
            if usage_dir.exists():
                # 只匹配日文件（YYYY-MM-DD.json）；by_user/ 子目录不参与全局汇总
                for f in sorted(usage_dir.glob(f"{month_prefix}-*.json")):
                    try:
                        data = json.loads(f.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    total += float(data.get("cost_rmb", 0) or 0)
                    day_files += 1
        except Exception as e:
            print(f"[LLM_GATEWAY] ⚠️ 月度金额汇总失败（按 0 处理）: {e}")

        result = (round(total, 6), day_files)
        self._monthly_spend_cache = (month_prefix, time.time(), result)
        return result

    def _budget_decision(self, *, input_tokens_est: int, module: str = "") -> _BudgetDecision:
        """调用前的金额/单次上限裁决（纯判定，不消耗配额、不发请求）。

        这是 TOKEN_BUDGET 里 monthly_budget_rmb / on_exceed / max_input_per_call
        三个键**唯一**的生产读取点。判定依据全部来自真实记账数据。
        """
        try:
            from config import TOKEN_BUDGET
        except Exception:
            return _BudgetDecision("allow", "ok", "")

        # ① 单次 input 上限（防单次上下文失控）
        max_input = int(TOKEN_BUDGET.get("max_input_per_call", 0) or 0)
        if max_input > 0 and input_tokens_est > max_input:
            return _BudgetDecision(
                "refuse", "input_over_budget",
                f"单次估算输入 ~{input_tokens_est} token > max_input_per_call={max_input}",
            )

        # ② 月度金额上限
        budget = float(TOKEN_BUDGET.get("monthly_budget_rmb", 0) or 0)
        on_exceed = str(TOKEN_BUDGET.get("on_exceed", "degrade")).strip().lower()
        if budget <= 0:
            return _BudgetDecision("allow", "ok", "")

        spend, days = self._monthly_spend_rmb()
        if spend >= budget:
            detail = (f"本月已花 ¥{spend:.4f}/¥{budget}（{days} 天记账数据，"
                      f"{spend / budget * 100:.1f}%）")
            if on_exceed == "warn_only":
                print(f"[LLM_GATEWAY] 🟡 月度预算超限（warn_only，仅告警不拦）：{detail} "
                      f"module={module or '_unknown'}")
                return _BudgetDecision("allow", "monthly_warn_only", detail)
            if on_exceed == "hard_stop":
                return _BudgetDecision("refuse", "budget_hard_stop", detail)
            # 默认 degrade → 不调 LLM，交回调用方的规则引擎
            return _BudgetDecision("refuse", "budget_exceeded", detail)

        critical = float(TOKEN_BUDGET.get("critical_threshold", 0.9))
        if spend >= budget * critical:
            return _BudgetDecision(
                "degrade", "monthly_critical",
                f"本月已花 ¥{spend:.4f}/¥{budget}（{spend / budget * 100:.1f}%"
                f" ≥ critical {critical * 100:.0f}%）",
            )

        return _BudgetDecision("allow", "ok", "")

    def check_budget(self) -> dict[str, Any]:
        """检查预算状态（供 /api/health 调用）"""
        try:
            from config import TOKEN_BUDGET
            usage_dir = Path(config.DATA_DIR) / "llm_usage"
            usage_file = usage_dir / f"{date.today()}.json"

            if usage_file.exists():
                daily = json.loads(usage_file.read_text(encoding="utf-8"))
            else:
                daily = {"cost_rmb": 0.0, "calls": 0}

            budget = cast(float, TOKEN_BUDGET.get("daily_budget_rmb", 3.0))
            pct = daily["cost_rmb"] / budget if budget > 0 else 0

            if pct >= cast(float, TOKEN_BUDGET.get("critical_threshold", 0.9)):
                status = "critical"
            elif pct >= cast(float, TOKEN_BUDGET.get("alert_threshold", 0.7)):
                status = "warning"
            else:
                status = "ok"

            # v9.9.30: 月度金额也是**真实闸门**了，健康检查必须一并如实报出，
            # 否则"日度 ok"会掩盖"月度已降级"。
            monthly_spend, monthly_days = self._monthly_spend_rmb()
            monthly_budget = float(TOKEN_BUDGET.get("monthly_budget_rmb", 0) or 0)
            monthly_pct = (monthly_spend / monthly_budget) if monthly_budget > 0 else 0.0
            if monthly_budget <= 0:
                monthly_status = "unknown"
            elif monthly_pct >= 1.0:
                monthly_status = "critical"
            elif monthly_pct >= cast(float, TOKEN_BUDGET.get("critical_threshold", 0.9)):
                monthly_status = "warning"
            else:
                monthly_status = "ok"

            return {
                "today_cost_rmb": round(daily["cost_rmb"], 2),
                "daily_budget_rmb": budget,
                "usage_pct": round(pct * 100, 1),
                "status": status,
                "today_calls": daily.get("calls", 0),
                "monthly": {
                    "cost_rmb": round(monthly_spend, 4),
                    "budget_rmb": monthly_budget,
                    "usage_pct": round(monthly_pct * 100, 1),
                    "status": monthly_status,
                    "days_with_usage": monthly_days,
                    "source": "data/llm_usage/*.json",
                },
                "on_exceed": str(TOKEN_BUDGET.get("on_exceed", "degrade")),
                # v9.9.30: 把输出边界守卫的**真实计数**放进健康检查。
                # 以前健康检查/CI 显示"有防护"，但模块零调用；现在这里给出的是
                # 真跑过的次数（outputs_audited=0 就说明守卫没接线/没流量，骗不了人）。
                "output_guard": {
                    **get_output_guard_stats(),
                    "enforce": output_guard_enforce_enabled(),
                },
            }
        except Exception:
            return {"status": "unknown"}

    def get_usage(self, user_id: str = "") -> dict[str, Any]:
        """获取用量统计"""
        if user_id:
            return {
                "user_id": user_id,
                "modules": self._usage.get(user_id, {}),
                "daily_count": self._daily_count,
                "daily_limit": DAILY_LIMIT,
                "date": self._daily_date.isoformat(),
            }
        return {
            "all_users": self._usage,
            "daily_count": self._daily_count,
            "daily_limit": DAILY_LIMIT,
            "date": self._daily_date.isoformat(),
        }

    def get_daily_remaining(self) -> int:
        """剩余日调用数"""
        self._check_daily_reset()
        return max(0, DAILY_LIMIT - self._daily_count)

    def get_cache_stats(self, days: int = 7) -> dict[str, Any]:
        """获取近 N 天的 DeepSeek 官方缓存命中率统计（V7.6）"""
        from datetime import timedelta
        usage_dir = Path(config.DATA_DIR) / "llm_usage"
        if not usage_dir.exists():
            return {"days": 0, "items": []}

        items = []
        total_hit = 0
        total_miss = 0
        total_cost = 0.0
        total_calls = 0
        for i in range(days):
            d = date.today() - timedelta(days=i)
            f = usage_dir / f"{d}.json"
            if not f.exists():
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            hit = data.get("cache_hit_tokens", 0)
            miss = data.get("cache_miss_tokens", 0)
            total_in = hit + miss
            ratio = hit / total_in if total_in > 0 else None
            items.append({
                "date": data.get("date", str(d)),
                "calls": data.get("calls", 0),
                "cost_rmb": data.get("cost_rmb", 0),
                "cache_hit_tokens": hit,
                "cache_miss_tokens": miss,
                "cache_hit_ratio": round(ratio, 3) if ratio is not None else None,
            })
            total_hit += hit
            total_miss += miss
            total_cost += data.get("cost_rmb", 0)
            total_calls += data.get("calls", 0)

        total_in = total_hit + total_miss
        avg_ratio = (total_hit / total_in) if total_in > 0 else None

        # 估算"满命中"能省多少钱（假设全部 miss 变 hit）
        # 日用量文件未按模型/峰谷拆分。v9.9.19：全面 Flash 化后线上绝大多数
        # 调用跑 deepseek-flash，改用 flash 档谷值估算；此前用 DEEPSEEK_PRICING
        # （pro 档）会把潜在收益高估约 3 倍（pro 差 4.35 vs flash 差 1.45）。
        try:
            from config import PROVIDER_PRICING
            _fx = PROVIDER_PRICING["deepseek-flash"]
            potential_save = total_miss * (
                _fx["input_cache_miss_valley"] - _fx["input_cache_hit_valley"]
            ) / 1_000_000
        except Exception:
            potential_save = None

        return {
            "days": days,
            "total_calls": total_calls,
            "total_cost_rmb": round(total_cost, 4),
            "total_cache_hit_tokens": total_hit,
            "total_cache_miss_tokens": total_miss,
            "avg_cache_hit_ratio": round(avg_ratio, 3) if avg_ratio is not None else None,
            "potential_save_rmb_if_100pct_hit": round(potential_save, 4) if potential_save else None,
            "items": items,
        }


# ---- 全局便捷函数 ----

def llm_call(prompt: str, **kwargs: Any) -> dict[str, Any]:
    """全局便捷调用（给 ds_enhance 等迁移用）"""
    return LLMGateway.instance().call_sync(prompt, **kwargs)


def llm_usage(user_id: str = "") -> dict[str, Any]:
    """获取用量"""
    return LLMGateway.instance().get_usage(user_id)


# ---- LLMClient 兼容适配器（委托同文件 LLMGateway）----

class LLMClient:
    """满足 domain.protocols.LLMClientProtocol 的适配器。

    现委托同文件的 LLMGateway（实现本体），不再反向 import services。
    保留此类以兼容 infra.llm.LLMClient 的既有导出契约。
    """

    def call(self, prompt: str, *, system: str = "", model_tier: str = "llm_light",
             user_id: str = "", module: str = "", max_tokens: int = 800) -> dict[str, Any]:
        raw = LLMGateway.instance().call_sync(
            prompt, system=system, model_tier=model_tier,
            user_id=user_id, module=module, max_tokens=max_tokens,
        )
        return raw

    def stream(self, prompt: str, *, system: str = "", model_tier: str = "llm_light",
               user_id: str = "", module: str = "", max_tokens: int = 1200) -> Iterator[dict[str, Any]]:
        yield from LLMGateway.instance().stream_sync(
            prompt, system=system, model_tier=model_tier,
            user_id=user_id, module=module, max_tokens=max_tokens,
        )

    def call_multimodal(self, messages: list[Any], *, model: str = "", user_id: str = "",
                        module: str = "", max_tokens: int = 800) -> dict[str, Any]:
        return LLMGateway.instance().call_multimodal(
            messages, model=model, user_id=user_id,
            module=module, max_tokens=max_tokens,
        )

    def get_usage(self, user_id: str = "") -> dict[str, Any]:
        return LLMGateway.instance().get_usage(user_id)

    def get_daily_remaining(self) -> int:
        return LLMGateway.instance().get_daily_remaining()

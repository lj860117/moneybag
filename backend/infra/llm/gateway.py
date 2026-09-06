"""
LLM Gateway -- 统一 LLM 调用入口（实现本体，原 services/llm_gateway.py 迁入）
=============================================================================
职责：
  1. 模型路由（DeepSeek V4 Flash/Pro + 豆包 Seed 2.0 Pro/Lite/Mini）
  2. 缓存（相同请求 1 小时内复用）
  3. 计费（按 user_id + module 双标签记账，豆包三档 + DeepSeek 两档价目）
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

import os
import time
import json
import hashlib
from datetime import datetime, date
from pathlib import Path
from infra.cache import MemoryCache

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - py<3.9 fallback
    ZoneInfo = None

# ---- 配置 ----
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_API_BASE = os.environ.get("LLM_API_BASE", "https://api.deepseek.com/v1")
DOUBAO_API_BASE = os.environ.get("DOUBAO_API_BASE", os.environ.get("ARK_API_BASE", "https://ark.cn-beijing.volces.com/api/v3"))

# 模型路由
MODEL_ROUTING = {
    "llm_light": "deepseek-v4-flash",     # V4 Flash: 聊天/点评/解读/信号
    "llm_heavy": "deepseek-v4-pro",       # V4 Pro: 仲裁/诊断/因子生成（快且质量高）
}
DOUBAO_MODEL_ROUTING = {
    "llm_light": "doubao-seed-2-0-lite-260215",
    "llm_heavy": "doubao-seed-2-0-pro-260215",
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


def set_alert_hook(fn) -> None:
    """注入配额告警函数（services.llm_quota_alert.maybe_alert_quota）。

    由组合根调用一次，避免 infra/llm/gateway 反向 import services。
    """
    global _alert_hook
    _alert_hook = fn


def _maybe_alert(provider, status_code, error_msg):
    """触发配额/余额告警，注入钩子优先，未注入时回退 lazy import。

    独立进程（night_worker.py 等 6 个 cron 脚本）不经过 main.py 的启动注入，
    钩子为 None，故在此回退到 services.llm_quota_alert.maybe_alert_quota。
    任何环节异常均静默吞掉，不影响主流程（含 raise RuntimeError 流程）。
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
        fn(provider, status_code, error_msg)
    except Exception:
        pass


def _china_now(now=None):
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


def _is_deepseek_peak_window(now=None) -> bool:
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
    """按具体模型名返回定价档位 key（区分 deepseek flash/pro、doubao pro/lite/mini）。

    返回 "deepseek-flash" / "deepseek-pro" / "doubao-pro" / "doubao-lite" / "doubao-mini"。
    deepseek 档位判定：
      - 含 "flash" 或 "reasoner" → flash 价表（reasoner 是 flash 的思考模式）
      - 含 "pro" → pro 价表
      - 无法识别 → 保守按 pro 价表记账
    doubao 档位判定：
      - 含 "mini" → mini 价表
      - 含 "lite" → lite 价表
      - 含 "pro" 或无法识别 → pro 价表（保守）
    """
    lowered = (model or "").lower()
    if lowered.startswith("doubao") or lowered.startswith("ep-"):
        if "mini" in lowered:
            return "doubao-mini"
        if "lite" in lowered:
            return "doubao-lite"
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


def _preferred_provider_order(module: str = "", now=None) -> list[str]:
    if _is_interactive_auto_module(module) and _is_deepseek_peak_window(now):
        return ["doubao", "deepseek"]
    return ["deepseek", "doubao"]


def _resolve_provider_model(provider: str, model_tier: str = "llm_light", *, need_tools: bool = False, phase: str = "primary") -> str:
    if provider == "doubao":
        if model_tier == "llm_heavy":
            return "doubao-seed-2-0-pro-260215"
        if phase == "fallback" and not need_tools:
            return "doubao-seed-2-0-mini-260215"
        return "doubao-seed-2-0-lite-260215"
    return MODEL_ROUTING.get(model_tier, "deepseek-v4-flash")


def resolve_model_candidates(model_tier: str = "llm_light", module: str = "", explicit_model: str = "", need_tools: bool = False, now=None) -> list[str]:
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

    for provider in remaining:
        candidates.append(_resolve_provider_model(provider, model_tier, need_tools=need_tools, phase="fallback"))

    deduped: list[str] = []
    for model in candidates:
        if model not in deduped:
            deduped.append(model)
    return deduped


def resolve_default_model(model_tier: str = "llm_light", module: str = "", now=None) -> str:
    candidates = resolve_model_candidates(model_tier, module=module, now=now)
    for model in candidates:
        if _provider_has_key(_provider_from_model(model)):
            return model
    return candidates[0] if candidates else MODEL_ROUTING.get(model_tier, "deepseek-v4-flash")

# 限制
DAILY_LIMIT = 100      # 每天最多 100 次（Phase 0 从 50 升级）
BURST_LIMIT = 10       # 5 分钟内最多 10 次
BURST_WINDOW = 300     # 5 分钟窗口
CACHE_TTL = 3600       # 缓存 1 小时

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
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._cache = MemoryCache(default_ttl=CACHE_TTL)  # LLM response cache
        self._usage = {}           # {user_id: {module: {calls, tokens, cost}}}
        self._daily_count = 0
        self._daily_date = date.today()
        self._burst_window = []    # 时间戳列表
        self._cache_dirty = 0      # 脏缓存计数，每 5 次写磁盘
        self._load_cache_from_disk()  # 启动时从磁盘恢复缓存

    # ---- 缓存持久化（Phase 0 新增）----

    CACHE_FILE = Path(os.environ.get("DATA_DIR", "./data")) / "cache" / "llm_cache.json"

    def _load_cache_from_disk(self):
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

    def _persist_cache_to_disk(self):
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
                  force_no_thinking: bool = False) -> dict:
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

        # 1. 先解析目标模型（显式选模优先，其次再走峰谷默认）
        model = explicit_model or resolve_default_model(model_tier, module=module)

        # 2. 缓存命中？（模型必须参与 cache key，避免跨模型串缓存）
        cache_key = self._cache_key(user_id, module, prompt, system, model)
        cached = self._get_cache(cache_key)
        if cached is not None:
            return {**cached, "source": "cache"}

        # 3. 熔断检查
        if not self._check_limits():
            print(f"[LLM_GATEWAY] ⚠️ 熔断！daily={self._daily_count}/{DAILY_LIMIT}")
            return {
                "content": "",
                "source": "rate_limited",
                "fallback": True,
                "model": "",
                "tokens": 0,
            }

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
        def _do_call(use_model: str, use_key: str, use_base: str):
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
            # 2) DeepSeek V4 轻量档：关闭（轻量任务不需要推理，避免截断，P0-1）
            # 3) 豆包 Seed 非推理档：关闭（v9.5.130 既有逻辑）
            # 其余（DeepSeek V4 重档/推理档）：保留推理，靠 call_sync 顶部提预算兜底
            if force_no_thinking:
                if use_model.startswith("deepseek-v4") or "doubao-seed" in use_model:
                    body["thinking"] = {"type": "disabled"}
            elif model_tier == "llm_light" and use_model.startswith("deepseek-v4"):
                body["thinking"] = {"type": "disabled"}
            elif model_tier != "llm_light":
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
                        _maybe_alert(provider, status, payload if isinstance(payload, str) else "")
                        raise RuntimeError(f"HTTP {status}: {payload}")
                    _msg0 = payload.get("choices", [{}])[0].get("message", {})
                    if not (_msg0.get("content") or "").strip() and (_msg0.get("reasoning_content") or "").strip():
                        _rc_len = len(_msg0.get("reasoning_content", ""))
                        raise RuntimeError(f"content_empty: {candidate_model} returned only reasoning ({_rc_len}chars), fallback needed")
                    data = payload
                    actual_model = candidate_model
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
            }
            self._set_cache(cache_key, result)
            self._record_usage(user_id, module, actual_model, total_tokens)
            input_tk = usage.get("prompt_tokens", usage.get("input_tokens", 0))
            output_tk = usage.get("completion_tokens", usage.get("output_tokens", 0))
            self._record_token_cost(
                user_id, actual_model, input_tk, output_tk,
                cache_hit_tokens=cache_hit_tk,
                cache_miss_tokens=cache_miss_tk,
            )
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
                    history: list | None = None,
                    explicit_model: str = "",
                    need_tools: bool = False,
                    force_no_thinking: bool = False):
        """流式调用 LLM，yield 标准化的 chunk dict。

        返回同步 Generator[dict, None, None]。
        每个 chunk: {"delta": str, "phase": "thinking"|"answering", "done": bool}
        最后一个 chunk: {"delta": "", "done": True, "usage": {...}}
        错误时: {"delta": "", "done": True, "error": str, "fallback": True}

        history: 多轮对话历史，格式 [{"role":"user"|"assistant","content":str}]
        explicit_model: 用户指定的模型 ID（如 "doubao-seed-2-0-lite-260215"），优先于 model_tier
        need_tools: 是否需要工具调用能力（Function Calling 场景，降级到豆包时优先选 Lite 而非 Mini）
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

        # 1. 熔断检查
        if not self._check_limits():
            print(f"[LLM_GATEWAY] ⚠️ stream 熔断！daily={self._daily_count}/{DAILY_LIMIT}")
            yield {"delta": "", "done": True, "error": "rate_limited", "fallback": True}
            return

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
        def _do_stream(use_model: str, use_key: str, use_base: str):
            """实际执行流式调用，yield chunk"""
            import httpx
            timeout = 60
            # 关闭 thinking 的策略（reasoning_content 与 content 共享 max_tokens）：
            # 1) force_no_thinking=True：调用方显式要求（短输出点），强制关闭所有推理模型
            # 2) DeepSeek V4 轻量档：关闭（轻量任务不需要推理，避免截断，P0-1）
            # 3) 豆包 Seed 非推理档：关闭（v9.5.130 既有逻辑）
            stream_body = {
                "model": use_model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.7,
                "stream": True,
            }
            if force_no_thinking:
                if use_model.startswith("deepseek-v4") or "doubao-seed" in use_model:
                    stream_body["thinking"] = {"type": "disabled"}
            elif model_tier == "llm_light" and use_model.startswith("deepseek-v4"):
                stream_body["thinking"] = {"type": "disabled"}
            elif model_tier != "llm_light":
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
                        _maybe_alert(_provider_from_model(use_model), resp.status_code, err_body)
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

            def _consume(it):
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

            yield {
                "delta": "", "done": True,
                "model": actual_model,
                "tokens": total_tokens,
                "content_length": len(total_content),
                "fallback_used": fallback_used,
            }

        except Exception as e:
            print(f"[LLM_GATEWAY] stream 调用失败: {e}")
            yield {"delta": "", "done": True, "error": str(e), "fallback": True}

    def call_multimodal(self, messages: list, *, model: str = "",
                        user_id: str = "", module: str = "",
                        max_tokens: int = 800) -> dict:
        """多模态调用（视觉/图片识别等），接受预组装的 messages。

        与 call_sync 的区别：
        1. 不走 MODEL_ROUTING（vision 模型直接由 model 参数指定）
        2. messages 由调用方完整构造（包含 image_url 等复杂结构）
        3. 不走缓存（图片内容无法稳定 hash）

        返回格式与 call_sync 一致。

        v9.9.11: 补视觉降级链 —— DeepSeek vision 失败时降级到豆包视觉模型
        （`LLM_VISION_MODEL_DOUBAO`，默认 doubao-seed-2-0-pro-260215，其支持图片输入）。
        """
        # 0. 日期重置
        self._check_daily_reset()

        # 1. 熔断检查
        if not self._check_limits():
            print(f"[LLM_GATEWAY] ⚠️ multimodal 熔断！daily={self._daily_count}/{DAILY_LIMIT}")
            return {"content": "", "source": "rate_limited", "fallback": True, "model": "", "tokens": 0}

        # 2. 视觉模型降级链（主：DeepSeek vision，备：豆包视觉）
        if not model:
            model = os.environ.get("LLM_VISION_MODEL", "deepseek-v4-flash-vision-exp")
        fallback_model = os.environ.get(
            "LLM_VISION_MODEL_DOUBAO",
            os.environ.get("DOUBAO_VISION_MODEL", "doubao-seed-2-0-pro-260215"),
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

                        return {
                            "content": content,
                            "source": "ai",
                            "model": cand_model,
                            "tokens": total_tokens,
                            "fallback": False,
                            "fallback_used": cand_model != model,
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

    def _get_cache(self, key: str):
        return self._cache.get(key)

    def _set_cache(self, key: str, result: dict):
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

    def _check_daily_reset(self):
        today = date.today()
        if self._daily_date != today:
            self._daily_count = 0
            self._daily_date = today
            self._burst_window = []

    def _check_limits(self) -> bool:
        # 日限
        if self._daily_count >= DAILY_LIMIT:
            return False
        # 突发限
        now = time.time()
        self._burst_window = [t for t in self._burst_window if now - t < BURST_WINDOW]
        if len(self._burst_window) >= BURST_LIMIT:
            return False
        # 通过
        self._daily_count += 1
        self._burst_window.append(now)
        return True

    def pre_check(self) -> bool:
        """流式调用前的限流检查，通过返回 True 并消耗一次配额。

        用于 streaming 场景：调用者先 pre_check()，再自行发 httpx stream 请求。
        这样 stream 也纳入日限/突发限控制。
        """
        self._check_daily_reset()
        return self._check_limits()

    # ---- 计费 ----

    def _record_usage(self, user_id: str, module: str, model: str, tokens: int):
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
                           cache_miss_tokens: int = 0):
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
            usage_dir = Path(os.environ.get("DATA_DIR", "./data")) / "llm_usage"
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
            budget = TOKEN_BUDGET.get("daily_budget_rmb", 3.0)
            alert_pct = TOKEN_BUDGET.get("alert_threshold", 0.7)
            critical_pct = TOKEN_BUDGET.get("critical_threshold", 0.9)

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

    def get_api_config(self, model_tier: str = "llm_light", module: str = "") -> dict:
        """返回当前默认模型对应的 API 配置。"""
        model = resolve_default_model(model_tier, module=module)
        api_key, api_base, _provider = _resolve_provider_config(model)
        return {"api_key": api_key, "api_base": api_base, "model": model}

    def check_budget(self) -> dict:
        """检查预算状态（供 /api/health 调用）"""
        try:
            from config import TOKEN_BUDGET
            usage_dir = Path(os.environ.get("DATA_DIR", "./data")) / "llm_usage"
            usage_file = usage_dir / f"{date.today()}.json"

            if usage_file.exists():
                daily = json.loads(usage_file.read_text(encoding="utf-8"))
            else:
                daily = {"cost_rmb": 0.0, "calls": 0}

            budget = TOKEN_BUDGET.get("daily_budget_rmb", 3.0)
            pct = daily["cost_rmb"] / budget if budget > 0 else 0

            if pct >= TOKEN_BUDGET.get("critical_threshold", 0.9):
                status = "critical"
            elif pct >= TOKEN_BUDGET.get("alert_threshold", 0.7):
                status = "warning"
            else:
                status = "ok"

            return {
                "today_cost_rmb": round(daily["cost_rmb"], 2),
                "daily_budget_rmb": budget,
                "usage_pct": round(pct * 100, 1),
                "status": status,
                "today_calls": daily.get("calls", 0),
            }
        except Exception:
            return {"status": "unknown"}

    def get_usage(self, user_id: str = "") -> dict:
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

    def get_cache_stats(self, days: int = 7) -> dict:
        """获取近 N 天的 DeepSeek 官方缓存命中率统计（V7.6）"""
        from datetime import timedelta
        usage_dir = Path(os.environ.get("DATA_DIR", "./data")) / "llm_usage"
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
        # 日用量文件未按模型/峰谷拆分，这里用 pro 档谷值(平价)做保守估算
        try:
            from config import DEEPSEEK_PRICING
            potential_save = total_miss * (
                DEEPSEEK_PRICING["input_cache_miss_valley"] - DEEPSEEK_PRICING["input_cache_hit_valley"]
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

def llm_call(prompt: str, **kwargs) -> dict:
    """全局便捷调用（给 ds_enhance 等迁移用）"""
    return LLMGateway.instance().call_sync(prompt, **kwargs)


def llm_usage(user_id: str = "") -> dict:
    """获取用量"""
    return LLMGateway.instance().get_usage(user_id)


# ---- LLMClient 兼容适配器（委托同文件 LLMGateway）----

class LLMClient:
    """满足 domain.protocols.LLMClientProtocol 的适配器。

    现委托同文件的 LLMGateway（实现本体），不再反向 import services。
    保留此类以兼容 infra.llm.LLMClient 的既有导出契约。
    """

    def call(self, prompt, *, system="", model_tier="llm_light",
             user_id="", module="", max_tokens=800):
        raw = LLMGateway.instance().call_sync(
            prompt, system=system, model_tier=model_tier,
            user_id=user_id, module=module, max_tokens=max_tokens,
        )
        return raw

    def stream(self, prompt, *, system="", model_tier="llm_light",
               user_id="", module="", max_tokens=1200):
        yield from LLMGateway.instance().stream_sync(
            prompt, system=system, model_tier=model_tier,
            user_id=user_id, module=module, max_tokens=max_tokens,
        )

    def call_multimodal(self, messages, *, model="", user_id="",
                        module="", max_tokens=800):
        return LLMGateway.instance().call_multimodal(
            messages, model=model, user_id=user_id,
            module=module, max_tokens=max_tokens,
        )

    def get_usage(self, user_id=""):
        return LLMGateway.instance().get_usage(user_id)

    def get_daily_remaining(self):
        return LLMGateway.instance().get_daily_remaining()

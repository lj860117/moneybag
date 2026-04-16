"""
钱袋子 — LLM Gateway（统一 LLM 调用入口）
职责：
  1. 模型路由（V3 轻量 / R1 深度推理）
  2. 缓存（相同请求 1 小时内复用）
  3. 计费（按 user_id + module 双标签记账）
  4. 熔断（日限 50 次 + 5 分钟限 10 次）
  5. 降级（LLM 不可用时返回 fallback）

设计文档：§六.A
"""
import os
import time
import json
import hashlib
from datetime import datetime, date
from pathlib import Path

# ---- 配置 ----
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_API_BASE = os.environ.get("LLM_API_BASE", "https://api.deepseek.com/v1")

# 模型路由
MODEL_ROUTING = {
    "llm_light": "deepseek-chat",       # V3: 点评/解读/信号/聊天
    "llm_heavy": "deepseek-reasoner",    # R1: 仲裁/诊断/因子生成
}

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
        self._cache = {}           # {cache_key: {result, ts}}
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
                    if now - v.get("ts", 0) < CACHE_TTL:
                        self._cache[k] = v
                        restored += 1
                if restored:
                    print(f"[LLM_GATEWAY] 💾 从磁盘恢复 {restored} 条缓存")
        except Exception as e:
            print(f"[LLM_GATEWAY] ⚠️ 缓存恢复失败（不影响运行）: {e}")

    def _persist_cache_to_disk(self):
        """将内存缓存写入磁盘（原子写）"""
        try:
            import tempfile
            self.CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            # 只持久化未过期的条目
            now = time.time()
            valid = {k: v for k, v in self._cache.items() if now - v.get("ts", 0) < CACHE_TTL}
            # 原子写：tmp + rename
            fd, tmp_path = tempfile.mkstemp(dir=str(self.CACHE_FILE.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(valid, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, str(self.CACHE_FILE))
        except Exception as e:
            print(f"[LLM_GATEWAY] ⚠️ 缓存持久化失败: {e}")

    # ---- 核心调用 ----

    def call_sync(self, prompt: str, *, system: str = "",
                  model_tier: str = "llm_light",
                  user_id: str = "", module: str = "",
                  max_tokens: int = 800) -> dict:
        """同步调用 LLM（大多数场景用这个）"""
        # 0. 日期重置
        self._check_daily_reset()

        # 1. 缓存命中？
        cache_key = self._cache_key(user_id, module, prompt, system)
        cached = self._get_cache(cache_key)
        if cached is not None:
            return {**cached, "source": "cache"}

        # 2. 熔断检查
        if not self._check_limits():
            print(f"[LLM_GATEWAY] ⚠️ 熔断！daily={self._daily_count}/{DAILY_LIMIT}")
            return {
                "content": "",
                "source": "rate_limited",
                "fallback": True,
                "model": "",
                "tokens": 0,
            }

        # 3. API key 检查（每次调用时读，不在模块加载时读——.env 可能还没加载）
        api_key = os.environ.get("LLM_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return {
                "content": "",
                "source": "no_key",
                "fallback": True,
                "model": "",
                "tokens": 0,
            }

        # 4. 选模型
        model = MODEL_ROUTING.get(model_tier, "deepseek-chat")

        # 5. 构建 messages
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        # 6. 调用
        try:
            import httpx
            timeout = 120 if model == "deepseek-reasoner" else 60
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(
                    f"{os.environ.get('LLM_API_BASE', 'https://api.deepseek.com/v1')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": messages,
                        "max_tokens": max_tokens,
                        "temperature": 0.7,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    msg = data["choices"][0]["message"]
                    # R1 模型: content 可能为 None，正式回答在 content，思考过程在 reasoning_content
                    content = msg.get("content") or ""
                    reasoning = msg.get("reasoning_content") or ""
                    # 如果 content 为空但有 reasoning，用 reasoning 作为回答（R1 有时把结论放在 reasoning 里）
                    if not content.strip() and reasoning.strip():
                        content = reasoning
                        print(f"[LLM_GATEWAY] R1 content 为空，用 reasoning_content 替代 ({len(reasoning)} chars)")
                    usage = data.get("usage", {})
                    total_tokens = usage.get("total_tokens", 0)

                    result = {
                        "content": content,
                        "reasoning": reasoning,
                        "source": "ai",
                        "model": model,
                        "tokens": total_tokens,
                        "fallback": False,
                    }

                    # 7. 写缓存
                    self._set_cache(cache_key, result)

                    # 8. 记账
                    self._record_usage(user_id, module, model, total_tokens)

                    return result
                else:
                    print(f"[LLM_GATEWAY] API error: {resp.status_code} {resp.text[:200]}")
                    return {
                        "content": "",
                        "source": "api_error",
                        "fallback": True,
                        "model": model,
                        "tokens": 0,
                        "error": f"HTTP {resp.status_code}",
                    }
        except Exception as e:
            print(f"[LLM_GATEWAY] 调用失败: {e}")
            return {
                "content": "",
                "source": "error",
                "fallback": True,
                "model": model,
                "tokens": 0,
                "error": str(e),
            }

    # ---- 缓存 ----

    def _cache_key(self, user_id: str, module: str, prompt: str, system: str = "") -> str:
        raw = f"{user_id}:{module}:{system[:100]}:{prompt[:500]}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _get_cache(self, key: str):
        if key in self._cache:
            entry = self._cache[key]
            if time.time() - entry["ts"] < CACHE_TTL:
                return entry["result"]
            else:
                del self._cache[key]
        return None

    def _set_cache(self, key: str, result: dict):
        self._cache[key] = {"result": result, "ts": time.time()}
        # 清理过期缓存（超过 200 条时）
        if len(self._cache) > 200:
            now = time.time()
            self._cache = {
                k: v for k, v in self._cache.items()
                if now - v["ts"] < CACHE_TTL
            }
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


# ---- 全局便捷函数 ----

def llm_call(prompt: str, **kwargs) -> dict:
    """全局便捷调用（给 ds_enhance 等迁移用）"""
    return LLMGateway.instance().call_sync(prompt, **kwargs)


def llm_usage(user_id: str = "") -> dict:
    """获取用量"""
    return LLMGateway.instance().get_usage(user_id)

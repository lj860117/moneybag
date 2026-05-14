# LLM Gateway Quick Reference (2026-05-15)

## TL;DR - Critical Facts for Extension Planning

### Architecture Layers (3-tier)

1. **Client Layer:** `infra/llm/gateway.LLMClient` (72 lines)
   - Thin adapter implementing `LLMClientProtocol`
   - Lazy imports to avoid circular dependencies

2. **Gateway Layer:** `services/llm_gateway.LLMGateway` (520 lines)
   - Singleton with model routing, caching, rate limiting, billing
   - Key methods: `call_sync()`, `pre_check()`, `get_api_config()`

3. **Storage/Cache:** MemoryCache + Disk persistence
   - In-memory: TTL-based with threading.Lock
   - Disk: JSON files (atomically written)

### call_sync() Signature & Return

```python
def call_sync(self, prompt: str, *, 
              system: str = "",
              model_tier: str = "llm_light",      # or "llm_heavy"
              user_id: str = "",
              module: str = "",
              max_tokens: int = 800) -> dict:
```

Returns:
```python
{
    "content": str,                 # Generated text
    "reasoning": str,               # R1 chain-of-thought
    "source": str,                  # "ai" | "cache" | "rate_limited" | "no_key" | "api_error" | "error"
    "model": str,                   # "deepseek-v4-flash" | "deepseek-reasoner"
    "tokens": int,                  # Total tokens
    "cache_hit_tokens": int,        # Cached prompt tokens (V7.6)
    "cache_miss_tokens": int,       # Uncached prompt tokens (V7.6)
    "fallback": bool,               # True if error/degraded
    "error": str,                   # Error message (if any)
}
```

### Model Routing

```python
MODEL_ROUTING = {
    "llm_light": "deepseek-v4-flash",    # Fast: chat/comments/signals
    "llm_heavy": "deepseek-reasoner",    # Slow: arbitration/diagnosis
}
```

### Rate Limits (Hybrid Daily + Burst)

- **Daily:** 100 calls/day (resets at midnight)
- **Burst:** 10 calls per 5-minute window
- Both must pass for call to go through

### Cache Mechanism

- **Key Generation:** MD5(user_id:module:system[:100]:prompt[:500])
- **TTL:** 3600 seconds (1 hour)
- **Memory:** MemoryCache with threading.Lock
- **Persistence:** 
  - Path: `~/.data/cache/llm_cache.json`
  - Trigger: Every 5 cache writes
  - Method: Atomic (tempfile + os.replace)

### Cost Tracking (V7.6 Real Cache Awareness)

**Pricing (¥/million tokens):**
- Cache hit: ¥0.20
- Cache miss: ¥2.03
- Output: ¥3.04

**Files:**
- Global: `~/.data/llm_usage/{date}.json`
- Per-user: `~/.data/llm_usage/by_user/{user_id}_{date}.json`

**Budget:**
- Daily limit: ¥3.0
- Alert at: 70% (🟡)
- Critical at: 90% (🔴)

### Environment Variables

| Variable | Default | Priority |
|----------|---------|----------|
| `LLM_API_KEY` | "" | Primary; fallback to OPENAI_API_KEY |
| `LLM_API_BASE` | https://api.deepseek.com/v1 | API endpoint |
| `LLM_MODEL` | deepseek-v4-flash | Unused (MODEL_ROUTING takes precedence) |
| `DATA_DIR` | ./data | Persistence root |

---

## Planning Extensions: stream_sync()

### Current Status
- No streaming support
- `pre_check()` exists for manual streaming
- `get_api_config()` returns config for external use

### Implementation Path

**Option A (Recommended):** Use existing helpers
```python
if not gateway.pre_check():
    return {"source": "rate_limited"}

config = gateway.get_api_config()
async with httpx.AsyncClient() as client:
    async with client.stream("POST", ..., json={...}) as r:
        async for line in r.aiter_lines():
            # Process streaming
```

**Option B:** Add stream_sync() to gateway
```python
async def stream_sync(self, prompt: str, **kwargs) -> AsyncIterator[str]:
    # Pre-check, cache lookup, stream, cache result, track usage
```

**Key Design Decisions:**
- Rate limit: consume 1 quota at start (via pre_check)
- Cache: store full streamed response after completion
- Tokens: extract from stream's final [DONE] message
- Type: Return AsyncIterator[str] for token-by-token

---

## Planning Extensions: Multimodal Support

### Current Limitations
- Text-only prompts
- No image handling
- No vision model routing

### Implementation Path

**1. Extend call_sync() signature:**
```python
def call_sync(self, prompt: str, *,
              ...existing params...,
              # NEW:
              images: list[str] = None,        # Base64 or URLs
              media_type: str = "image/jpeg",  
              vision_detail: str = "low") -> dict:
```

**2. Update MODEL_ROUTING:**
```python
MODEL_ROUTING = {
    "llm_light": "deepseek-v4-flash",
    "llm_heavy": "deepseek-reasoner",
    # NEW:
    "vision": "deepseek-vision-v4",  # If supported
}
```

**3. Extend cache key (include image hash):**
```python
def _cache_key(self, user_id, module, prompt, system, images=None):
    image_hash = hashlib.md5(str(images or []).encode()).hexdigest()
    raw = f"{user_id}:{module}:{system[:100]}:{prompt[:500]}:{image_hash}"
    return hashlib.md5(raw.encode()).hexdigest()
```

**4. Build multimodal messages:**
```python
messages = [{
    "role": "user",
    "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": image_url1, "detail": "low"}},
        {"type": "image_url", "image_url": {"url": image_url2, "detail": "high"}},
    ]
}]
```

**5. Handle vision model pricing:**
- Vision models typically have different token costs
- Image tokens counted separately
- Add to DEEPSEEK_PRICING config

---

## Key Implementation Details

### R1 Model Edge Case (lines 181-187)

DeepSeek R1 sometimes returns empty `content` with reasoning in `reasoning_content`:

```python
content = msg.get("content") or ""
reasoning = msg.get("reasoning_content") or ""
if not content.strip() and reasoning.strip():
    content = reasoning  # Fallback to reasoning
```

### Cache Persistence Atomicity (lines 91-111)

```python
fd, tmp_path = tempfile.mkstemp(...)  # Create temp file
with os.fdopen(fd, "w") as f:
    json.dump(data, f)
    f.flush()
    os.fsync(f.fileno())               # Durability
os.replace(tmp_path, final_path)       # Atomic rename
```

### Dirty Writes Optimization (lines 252-261)

Only persist cache every 5 writes:
```python
self._cache_dirty += 1
if self._cache_dirty >= 5:
    self._persist_cache_to_disk()
    self._cache_dirty = 0
```

### Burst Window Pruning (lines 272-284)

Removes timestamps > 5 minutes old before checking limit:
```python
now = time.time()
self._burst_window = [t for t in self._burst_window if now - t < 300]
if len(self._burst_window) >= 10:
    return False  # Hit burst limit
```

---

## Monitoring & Debugging

### Check Budget Status
```python
gateway = LLMGateway.instance()
status = gateway.check_budget()
# {today_cost_rmb, daily_budget_rmb, usage_pct, status, today_calls}
```

### Get Cache Statistics
```python
stats = gateway.get_cache_stats(days=7)
# {days, total_calls, total_cost_rmb, total_cache_hit_tokens, ...}
# Includes per-day breakdown + potential_save_rmb_if_100pct_hit
```

### View Usage by User
```python
usage = gateway.get_usage(user_id="alice")
# {user_id, modules, daily_count, daily_limit, date}
```

### Console Markers (grep logs for these)

- `💾 从磁盘恢复 N 条缓存` — Cache loaded
- `⚠️ 熔断！daily=X/100` — Rate limited
- `🔴 日预算 90%！¥X / ¥3` — Critical budget
- `🟡 日预算 70%！¥X / ¥3` — Warning budget
- `📉 今日缓存命中率 X% < 30%` — Low cache ratio
- `R1 content 为空，用 reasoning_content 替代` — R1 edge case

---

## Data Files & Directories

```
~/.data/
├── cache/
│   └── llm_cache.json               # Persistent cache
│       {key: {result: {...}, ts: float}, ...}
├── llm_usage/
│   ├── 2026-05-15.json              # Global daily stats
│   │   {date, input_tokens, output_tokens, 
│   │    cache_hit_tokens, cache_miss_tokens, cost_rmb, calls}
│   └── by_user/
│       └── alice_2026-05-15.json    # Per-user breakdown
│           {user_id, date, cost_rmb, calls}
```

---

## Code Import Patterns

### Correct (Use These)

```python
# New code: use infra.llm.LLMClient
from infra.llm.gateway import LLMClient
client = LLMClient()
response = client.call(prompt, user_id="alice", model_tier="llm_light")

# Or convenience function
from services.llm_gateway import llm_call
response = llm_call(prompt, user_id="alice", module="decision_engine")

# For streaming setup
from services.llm_gateway import LLMGateway
gateway = LLMGateway.instance()
if gateway.pre_check():
    config = gateway.get_api_config()
    # Use config for external streaming
```

### Incorrect (Don't Do These)

```python
# ❌ Don't import gateway directly (bypass adapter)
from services.llm_gateway import LLMGateway
gateway = LLMGateway.instance()
result = gateway.call_sync(...)  # Skip protocol layer!

# ❌ Don't import for protocol—use structural typing
from domain.protocols.llm_client import LLMClientProtocol
```

---

## Dependencies

**Imports by services/llm_gateway.py:**
```python
import os, time, json, hashlib
from datetime import datetime, date
from pathlib import Path
from infra.cache import MemoryCache
from config import TOKEN_BUDGET, DEEPSEEK_PRICING
from services.persistence import atomic_write_json
```

**External library:**
- `httpx` — HTTP client for DeepSeek API

---

## Version History Reference

### V7.6 (2026-04-19)
- Real cache hit/miss token tracking
- Updated cost calculation with actual cache stats
- Cache ratio warning (<30% after 30 calls)

### Phase 0 (Earlier)
- Disk cache persistence added
- Token budget tracking
- Daily/burst rate limits
- Cost tracking by user + module

---

## Extension Checklist

**For stream_sync():**
- [ ] Design async signature
- [ ] Implement pre-flight checks (cache, rate limits)
- [ ] Add token counting from stream
- [ ] Cache full streamed response
- [ ] Record usage/cost post-stream
- [ ] Add to LLMClientProtocol (future)

**For multimodal:**
- [ ] Extend call_sync() signature (images, media_type, detail)
- [ ] Update MODEL_ROUTING for vision models
- [ ] Extend _cache_key() for image hashes
- [ ] Update message construction (content array)
- [ ] Add vision model pricing to DEEPSEEK_PRICING
- [ ] Handle image token counting
- [ ] Test with real images


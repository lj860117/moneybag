# LLM Gateway Extension Plan: stream_sync() & Multimodal Support

**Date**: 2026-05-15  
**Stability**: PLANNING PHASE (no code changes yet)  
**Target**: Add streaming responses + vision/image support while preserving existing cache/billing/limits

---

## Executive Summary

The current `LLMGateway.call_sync()` (537 lines, single-worker singleton) is production-hardened for:
- ✅ Sync calls with caching (1h TTL, disk-persisted)
- ✅ Dual-tier rate limiting (100/day, 10/5min)
- ✅ Per-token cost tracking (¥0.20-3.04 per M tokens)
- ✅ RMB budget enforcement (¥3/day with alerts)
- ✅ Model routing (V4-flash for light, R1 for heavy)

**Planned additions**:
- ❌ `stream_sync()` — streaming responses with cost accumulation
- ❌ `call_with_images()` — multimodal vision support
- ❌ Cache key extension for vision (image hashes)
- ❌ Dual response mode: streaming vs. buffered

---

## 1. STREAM_SYNC() DESIGN

### 1.1 Method Signature

```python
def stream_sync(self, prompt: str, *, 
                system: str = "",
                model_tier: str = "llm_light",
                user_id: str = "", 
                module: str = "",
                max_tokens: int = 800) -> Iterator[str]:
    """Yield response tokens as they arrive from DeepSeek API.
    
    Behavior differences vs. call_sync():
    - Returns Iterator[str], not dict
    - Cannot use cache (can't cache partial responses)
    - Consumes rate limit quota on start (pre_check())
    - Cost accumulated as tokens arrive
    - No fallback dict on error (raises exception or yields error token)
    
    Usage:
        for token in gw.stream_sync("What is...?", user_id="u123"):
            print(token, end="", flush=True)
    """
```

### 1.2 Implementation Strategy

**Option A: Simple (current recommendation)**
```python
def stream_sync(self, prompt, *, system="", model_tier="llm_light", 
                user_id="", module="", max_tokens=800) -> Iterator[str]:
    # Step 1: Pre-flight checks
    self._check_daily_reset()
    if not self._check_limits():
        raise RateLimitError("Daily/burst limit exceeded")
    
    # Step 2: Get config
    api_key = os.environ.get("LLM_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("No LLM_API_KEY")
    
    model = MODEL_ROUTING.get(model_tier, "deepseek-v4-flash")
    
    # Step 3: Build messages
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    
    # Step 4: Stream from API
    with httpx.stream(
        "POST",
        f"{os.environ.get('LLM_API_BASE', 'https://api.deepseek.com/v1')}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.7,
            "stream": True,
        },
        timeout=120 if model == "deepseek-reasoner" else 60,
    ) as response:
        total_tokens = 0
        full_response = ""
        
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            
            if line == "data: [DONE]":
                break
            
            try:
                chunk = json.loads(line[6:])
                delta = chunk["choices"][0]["delta"]
                
                if "content" in delta:
                    content = delta["content"]
                    full_response += content
                    total_tokens += 1  # Approximate (real count from finish_reason)
                    yield content
                
                # On stream end, get real token count
                if chunk["choices"][0].get("finish_reason"):
                    usage = chunk.get("usage", {})
                    total_tokens = usage.get("total_tokens", 0)
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
        
        # Step 5: Record usage (buffered, not real-time)
        self._record_usage(user_id, module, model, total_tokens)
        self._record_token_cost(user_id, model, 
                               input_tokens=len(prompt) // 4,  # Estimate
                               output_tokens=total_tokens)
```

**Option B: Advanced (future)**
- Yield real-time cost as it updates
- Support vision streaming (model-dependent)
- Add cancellation token support
- Implement server-sent event parsing

### 1.3 Error Handling

```python
# Errors during streaming should be handled by caller
try:
    for token in gw.stream_sync("prompt"):
        print(token, end="")
except RateLimitError:
    print("[RATE LIMITED]")
except httpx.TimeoutException:
    print("[TIMEOUT]")
except Exception as e:
    print(f"[ERROR: {e}]")
```

### 1.4 Testing

```python
def test_stream_sync_consumes_quota():
    gw = LLMGateway()
    gw._daily_count = 100  # At limit
    
    with pytest.raises(RateLimitError):
        list(gw.stream_sync("test", user_id="u1"))

def test_stream_sync_records_usage():
    gw = LLMGateway()
    tokens = []
    
    for token in gw.stream_sync("What is 2+2?"):
        tokens.append(token)
    
    usage = gw.get_usage("u1")
    assert usage["_unknown"]["calls"] == 1  # module defaults to "_unknown"
```

---

## 2. MULTIMODAL SUPPORT: call_with_images()

### 2.1 Method Signature

```python
def call_with_images(self, prompt: str, images: List[str], *,
                     system: str = "",
                     user_id: str = "", 
                     module: str = "",
                     max_tokens: int = 1000) -> dict:
    """Call vision model with image(s) + text prompt.
    
    Args:
        prompt: Text question about the image(s)
        images: List of image URLs (must be publicly accessible)
        system: Optional system prompt
        user_id: For tracking
        module: For tracking
        max_tokens: Vision models may have different limits
    
    Returns:
        Same dict structure as call_sync():
        {content, reasoning, source, model, tokens, fallback, error}
    
    Usage:
        resp = gw.call_with_images(
            "What company logo is this?",
            images=["https://example.com/logo.png"],
            user_id="u123",
            module="brand_recognition"
        )
    """
```

### 2.2 Implementation Strategy

#### 2.2.1 Model Routing Extension

**In config.py**, add:
```python
MODEL_ROUTING = {
    "llm_light": "deepseek-v4-flash",
    "llm_heavy": "deepseek-reasoner",
    "llm_vision": "deepseek-vision-model",         # NEW
}
```

#### 2.2.2 Cache Key for Vision

**Decision**: Images break caching (content-addressed by URL)
```python
def _cache_key_vision(self, user_id: str, module: str, prompt: str, 
                      image_urls: List[str], system: str = "") -> str:
    """Include image URL hashes to make key deterministic
    
    Why: Same image URL should cache, but different URLs = different cache
    """
    image_part = "|".join(hashlib.md5(url.encode()).hexdigest() for url in image_urls)
    raw = f"{user_id}:{module}:{system[:100]}:{prompt[:500]}:{image_part}"
    return hashlib.md5(raw.encode()).hexdigest()
```

#### 2.2.3 Message Construction for Vision

**OpenAI/DeepSeek vision format**:
```python
messages = [{
    "role": "user",
    "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": images[0]}},
        {"type": "image_url", "image_url": {"url": images[1]}},
        # ... more images ...
    ]
}]
```

#### 2.2.4 Full Implementation

```python
def call_with_images(self, prompt: str, images: List[str], *,
                     system: str = "",
                     user_id: str = "", 
                     module: str = "",
                     max_tokens: int = 1000) -> dict:
    """Vision multimodal call"""
    
    # 1. Daily reset
    self._check_daily_reset()
    
    # 2. Cache lookup (including images)
    cache_key = self._cache_key_vision(user_id, module, prompt, images, system)
    cached = self._get_cache(cache_key)
    if cached is not None:
        return {**cached, "source": "cache"}
    
    # 3. Rate limit
    if not self._check_limits():
        return {
            "content": "",
            "source": "rate_limited",
            "fallback": True,
            "model": "",
            "tokens": 0,
        }
    
    # 4. API key
    api_key = os.environ.get("LLM_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return {"content": "", "source": "no_key", "fallback": True, "model": "", "tokens": 0}
    
    # 5. Model selection (always use vision model)
    model = MODEL_ROUTING.get("llm_vision", "deepseek-vision-model")
    
    # 6. Build vision messages
    message_content = [{"type": "text", "text": prompt}]
    for img_url in images:
        message_content.append({
            "type": "image_url",
            "image_url": {"url": img_url}
        })
    
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": message_content})
    
    # 7. API call (similar to call_sync)
    try:
        import httpx
        with httpx.Client(timeout=120) as client:
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
                content = msg.get("content", "")
                reasoning = msg.get("reasoning_content", "")
                usage = data.get("usage", {})
                total_tokens = usage.get("total_tokens", 0)
                cache_hit = usage.get("prompt_cache_hit_tokens", 0)
                cache_miss = usage.get("prompt_cache_miss_tokens", 0)
                
                result = {
                    "content": content,
                    "reasoning": reasoning,
                    "source": "ai",
                    "model": model,
                    "tokens": total_tokens,
                    "cache_hit_tokens": cache_hit,
                    "cache_miss_tokens": cache_miss,
                    "fallback": False,
                }
                
                # 8. Cache + record
                self._set_cache(cache_key, result)
                self._record_usage(user_id, module, model, total_tokens)
                self._record_token_cost(user_id, model,
                                       input_tokens=usage.get("prompt_tokens", 0),
                                       output_tokens=usage.get("completion_tokens", 0),
                                       cache_hit_tokens=cache_hit,
                                       cache_miss_tokens=cache_miss)
                return result
            else:
                return {
                    "content": "",
                    "source": "api_error",
                    "fallback": True,
                    "model": model,
                    "tokens": 0,
                    "error": f"HTTP {resp.status_code}",
                }
    except Exception as e:
        return {
            "content": "",
            "source": "error",
            "fallback": True,
            "model": model,
            "tokens": 0,
            "error": str(e),
        }
```

### 2.3 Vision Token Pricing

**Decision**: Vision models may have different pricing

**In config.py**:
```python
DEEPSEEK_PRICING = {
    "input_cache_hit": 0.20,
    "input_cache_miss": 2.03,
    "output": 3.04,
}

DEEPSEEK_VISION_PRICING = {  # NEW
    "input_cache_hit": 0.50,          # Higher per-image
    "input_cache_miss": 5.00,
    "output": 5.00,
}
```

**In `_record_token_cost()`**:
```python
def _record_token_cost(self, user_id: str, model: str,
                       input_tokens: int, output_tokens: int,
                       cache_hit_tokens: int = 0,
                       cache_miss_tokens: int = 0,
                       is_vision: bool = False):
    """Extended to support vision pricing"""
    pricing = DEEPSEEK_VISION_PRICING if is_vision else DEEPSEEK_PRICING
    # ... rest of implementation using `pricing` dict ...
```

### 2.4 Testing

```python
def test_call_with_images_cache():
    gw = LLMGateway()
    images = ["https://example.com/image.png"]
    
    resp1 = gw.call_with_images("What's this?", images, user_id="u1")
    resp2 = gw.call_with_images("What's this?", images, user_id="u1")
    
    assert resp2["source"] == "cache"

def test_call_with_images_different_urls_no_cache():
    gw = LLMGateway()
    resp1 = gw.call_with_images("What's this?", 
                                ["https://example.com/image1.png"], 
                                user_id="u1")
    resp2 = gw.call_with_images("What's this?", 
                                ["https://example.com/image2.png"], 
                                user_id="u1")
    
    # Different URLs = different cache keys = no cache hit
    assert resp1["source"] == "ai"
    assert resp2["source"] == "ai"

def test_call_with_images_routing():
    gw = LLMGateway()
    resp = gw.call_with_images("Describe", 
                               ["https://example.com/img.png"],
                               user_id="u1")
    assert resp["model"] == "deepseek-vision-model"
```

---

## 3. PROTOCOL UPDATES

### 3.1 Update `domain/protocols/llm_client.py`

```python
@runtime_checkable
class LLMClientProtocol(Protocol):
    
    def call(self, prompt: str, *, ...) -> LLMResponse: ...
    
    def stream_sync(self, prompt: str, *, 
                    system: str = ...,
                    model_tier: str = ...,
                    user_id: str = ...,
                    module: str = ...,
                    max_tokens: int = ...) -> Iterator[str]:
        """Yield tokens as they arrive from LLM."""
        ...
    
    def call_with_images(self, prompt: str, images: List[str], *,
                        system: str = ...,
                        user_id: str = ...,
                        module: str = ...,
                        max_tokens: int = ...) -> LLMResponse:
        """Call vision model with image(s)."""
        ...
    
    def get_usage(self, user_id: str = ...) -> Dict[str, object]: ...
    def get_daily_remaining(self) -> int: ...
```

### 3.2 Update `infra/llm/gateway.py` LLMClient Adapter

```python
class LLMClient:
    def stream_sync(self, prompt: str, *, ...) -> Iterator[str]:
        gw = self._gateway()
        return gw.stream_sync(prompt, ...)
    
    def call_with_images(self, prompt: str, images: List[str], *, ...) -> LLMResponse:
        gw = self._gateway()
        raw = gw.call_with_images(prompt, images, ...)
        return LLMResponse.from_dict(raw)
```

---

## 4. CONFIGURATION UPDATES

### In `config.py`

```python
# Add vision model routing
MODEL_ROUTING = {
    "llm_light": "deepseek-v4-flash",
    "llm_heavy": "deepseek-reasoner",
    "llm_vision": "deepseek-vision-model",    # NEW
}

# Add vision pricing
DEEPSEEK_VISION_PRICING = {                  # NEW
    "input_cache_hit": 0.50,
    "input_cache_miss": 5.00,
    "output": 5.00,
}

# Extend token budget for vision
TOKEN_BUDGET_VISION = {                      # NEW
    "daily_budget_rmb": 5.0,                 # Vision calls are pricier
    "alert_threshold": 0.7,
    "critical_threshold": 0.9,
}
```

---

## 5. IMPLEMENTATION CHECKLIST

### Phase 1: Streaming (2-3 days)
- [ ] `stream_sync()` in `services/llm_gateway.py`
- [ ] Update protocol `domain/protocols/llm_client.py`
- [ ] Update adapter `infra/llm/gateway.py`
- [ ] Add integration tests
- [ ] Document in docstrings

### Phase 2: Vision/Multimodal (3-4 days)
- [ ] `call_with_images()` in `services/llm_gateway.py`
- [ ] `_cache_key_vision()` helper
- [ ] Extend message construction logic
- [ ] Update config with vision routing + pricing
- [ ] Update `_record_token_cost()` for vision pricing
- [ ] Add vision integration tests
- [ ] Update adapter `infra/llm/gateway.py`

### Phase 3: Polish & Testing (1-2 days)
- [ ] End-to-end streaming test
- [ ] End-to-end vision test
- [ ] Rate limit interaction tests
- [ ] Cache invalidation tests
- [ ] Error handling tests
- [ ] Cost tracking verification

### Phase 4: Deploy & Monitor (1 day)
- [ ] Rollout to staging
- [ ] Monitor cache hit ratio
- [ ] Monitor cost/budget
- [ ] A/B test streaming vs. buffering
- [ ] Gather user feedback

---

## 6. BACKWARD COMPATIBILITY

### No Breaking Changes
- Existing `call_sync()` unchanged
- `LLMClientProtocol` only **adds** methods (Protocol evolution)
- `MODEL_ROUTING` dict only **adds** entries
- Cache structure unchanged (new methods = new cache namespace)

### Migration Path
```python
# Old code still works
from infra.llm import LLMClient
client = LLMClient()
resp = client.call(prompt)  # ✅ unchanged

# New code uses streaming
for token in client.stream_sync(prompt):
    print(token, end="")

# New code uses vision
resp = client.call_with_images(prompt, images)
```

---

## 7. PERFORMANCE IMPACT

### Memory
- **Vision cache key**: +50 bytes per vision call (image URL hashes)
- **Streaming buffer**: +1KB per concurrent stream (iterator state)
- **Total impact**: Negligible (<100KB for 100 concurrent streams)

### Latency
- **`stream_sync()`**: Identical to `call_sync()` (same API call)
  - Advantage: Caller sees tokens immediately vs. waiting for full response
- **`call_with_images()`**: +20% latency (larger context due to images)
  - Vision models are slower: 5-15s expected

### Cost
- **Streaming**: No additional cost (same model, same token count)
- **Vision**: 2-3× cost per call (different model + pricing)

---

## 8. ERROR HANDLING STRATEGY

### For `stream_sync()`
```python
# Errors on startup (pre-flight)
try:
    for token in client.stream_sync(prompt):
        pass
except RateLimitError:          # Can't get quota
    ...
except RuntimeError:            # No API key
    ...
except httpx.TimeoutError:      # Connection timeout
    ...

# Errors mid-stream
# → Partial response already yielded
# → Caller must handle incomplete responses
```

### For `call_with_images()`
```python
# Same as call_sync() → always returns dict with fallback=True on error
resp = client.call_with_images(prompt, images)
if resp["fallback"]:
    print(f"Error: {resp['error']}")
else:
    print(f"Content: {resp['content']}")
```

---

## 9. MIGRATION CHECKLIST FOR EXISTING CODE

### Services using LLM should add:

```python
# Where to add streaming support
from infra.llm import LLMClient
client = LLMClient()

# Signal analysis
def analyze_signal_streaming(signal_data):
    prompt = f"Analyze: {signal_data}"
    for token in client.stream_sync(
        prompt, 
        user_id="signal_bot",
        module="signal_scout"
    ):
        # Stream tokens to WebSocket
        ws.send_text(token)

# Vision/multimodal
def identify_brand(image_url):
    resp = client.call_with_images(
        "What brand is this logo?",
        images=[image_url],
        user_id="brand_bot",
        module="brand_recognition"
    )
    return resp["content"]
```

---

## 10. KNOWN RISKS & MITIGATIONS

| Risk | Probability | Severity | Mitigation |
|------|---|---|---|
| Streaming connection drops | Medium | Medium | Caller handles partial response + retry |
| Vision model rate limits | High | Low | Separate quota per model_tier |
| Cache pollution (image URLs) | Low | Low | URL validation + hash-based cache key |
| Thread safety with streaming | Low | High | Document single-worker requirement |
| Vision token counting errors | Medium | Medium | Validate token counts from API |
| Cost explosion on vision | High | Medium | Separate budget + alerts for vision |

---

**END OF EXTENSION PLAN**

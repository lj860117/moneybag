# LLM Gateway Architecture — Complete Reference

This directory contains comprehensive documentation of the LLM gateway implementation:

## 📄 Documentation Files

### 1. **`quick_summary.txt`** ⚡ START HERE
- **Size**: 11 KB
- **Purpose**: Quick reference guide (organized with visual structure)
- **Read time**: 5-10 minutes
- **Contains**:
  - File structure overview
  - All method signatures with line numbers
  - Core data flow (10 steps)
  - Configuration constants
  - Extension points for stream/multimodal
  - Known limitations

### 2. **`llm_gateway_architecture.md`** 📚 DEEP DIVE
- **Size**: 43 KB
- **Purpose**: Complete technical analysis with line-by-line details
- **Read time**: 45-60 minutes
- **Contains 16 sections**:
  1. Architecture layers (4-tier stack)
  2. Class structure & method signatures
  3. Cache subsystem (disk persistence)
  4. Complete flow diagrams
  5. Global convenience functions
  6. Configuration constants
  7. Error handling & fallback strategy
  8. Environment variables
  9. Persistence & atomicity
  10. Streaming & multimodal extension points
  11. Key implementation details
  12. Concurrency & thread safety
  13. Testing extension points
  14. Performance characteristics
  15. Summary method table
  16. Extension checklist

### 3. **`EXTENSION_PLAN.md`** 🚀 IMPLEMENTATION GUIDE
- **Size**: 20 KB
- **Purpose**: Detailed plan for adding stream_sync() and multimodal support
- **Read time**: 30-40 minutes
- **Contains 10 sections**:
  1. Executive summary
  2. `stream_sync()` design (method signature, implementation strategy, testing)
  3. `call_with_images()` design (vision routing, cache keys, pricing)
  4. Protocol updates needed
  5. Configuration updates needed
  6. Implementation checklist (4 phases)
  7. Backward compatibility guarantees
  8. Performance impact analysis
  9. Error handling strategy
  10. Migration checklist & risk matrix

---

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│ LAYER 1: Domain Protocol (LLMClientProtocol)               │
│ @runtime_checkable interface (no inheritance required)     │
├─────────────────────────────────────────────────────────────┤
│ LAYER 2: Adapter (LLMClient, strangler fig pattern)        │
│ Wraps singleton LLMGateway, returns typed LLMResponse      │
├─────────────────────────────────────────────────────────────┤
│ LAYER 3: Core (LLMGateway singleton, 537 lines)            │
│ ├─ call_sync()       (127 lines) ← Main entry point        │
│ ├─ stream_sync()     (planned)                             │
│ ├─ call_with_images() (planned)                            │
│ ├─ Caching (disk-persisted, 1h TTL)                        │
│ ├─ Rate limits (100/day + 10/5min burst)                   │
│ ├─ Cost tracking (¥0.20-3.04 per M tokens)                 │
│ └─ Model routing (V4-flash, R1)                            │
├─────────────────────────────────────────────────────────────┤
│ LAYER 4: Support                                           │
│ ├─ MemoryCache (thread-safe, 132 lines)                   │
│ ├─ Atomic JSON persistence                                 │
│ └─ Config constants                                        │
└─────────────────────────────────────────────────────────────┘
```

---

## 📊 Key Statistics

| Metric | Value |
|--------|-------|
| Total lines (3 main files) | 608 lines |
| Singleton class | LLMGateway (537 lines) |
| Main method | `call_sync()` (127 lines) |
| Cache mechanism | MemoryCache + disk (1h TTL) |
| Rate limits | 100/day + 10/5min burst |
| Default timeout | 60s (V4), 120s (R1) |
| Cost tracking | Daily + per-user JSON files |
| API provider | DeepSeek |
| Models | V4-flash (light), R1 (heavy), vision (planned) |
| Cache file | `./data/cache/llm_cache.json` |
| Usage files | `./data/llm_usage/{TODAY}.json` |

---

## 🎯 Quick Start: Understanding the Code

### If you have 5 minutes:
→ Read **`quick_summary.txt`** sections: "FILE SIZES", "CORE SINGLETON", "CALL_SYNC() FLOW"

### If you have 30 minutes:
→ Read **`EXTENSION_PLAN.md`** sections: "Executive Summary", "STREAM_SYNC() DESIGN", "MULTIMODAL SUPPORT"

### If you have 1 hour:
→ Read **`llm_gateway_architecture.md`** sections: 1-7

### If you're implementing:
→ Read **`EXTENSION_PLAN.md`** sections: 1-5 (for design), then 6-10 (for implementation details)

---

## 🔧 For Implementers: stream_sync() Roadmap

### Phase 1: Streaming (2-3 days)
```python
# Add to services/llm_gateway.py
def stream_sync(self, prompt: str, ...) -> Iterator[str]:
    """Yield tokens as they arrive from DeepSeek API"""
    # Pre-flight: _check_daily_reset() + _check_limits()
    # Setup: Build messages, get config
    # Stream: httpx.stream() + yield tokens
    # Record: _record_usage() + _record_token_cost() after stream ends
```

### Phase 2: Vision/Multimodal (3-4 days)
```python
# Add to services/llm_gateway.py
def call_with_images(self, prompt: str, images: List[str], ...) -> dict:
    """Call vision model with image(s) + text"""
    # Setup: _cache_key_vision() for image-aware caching
    # Routing: Use llm_vision model tier
    # Messages: Build vision-format message with image URLs
    # Record: Track vision token costs separately
```

### Phase 3: Configuration (1 day)
```python
# Update config.py
MODEL_ROUTING["llm_vision"] = "deepseek-vision-model"
DEEPSEEK_VISION_PRICING = {...}  # Separate pricing
TOKEN_BUDGET_VISION = {...}      # Separate budget
```

### Phase 4: Testing & Deployment (2-3 days)
```python
# Add tests for streaming, vision, error cases
# Deploy to staging, monitor metrics
# Gather feedback before production rollout
```

---

## ⚠️ Important Constraints

### Single-Worker Only
- **Current design**: Assumes `uvicorn --workers 1`
- **Reason**: `_daily_count` and `_burst_window` are not thread-safe
- **Future**: Use Redis for multi-worker support

### No Cache for Streaming
- Streaming responses can't be cached (how to cache partial responses?)
- Vision responses CAN be cached (cache key includes image URL hash)

### Soft Budget Enforcement
- Budget limits trigger **warnings** (logs 🔴 at 90%)
- **Not** enforced (doesn't block calls)
- "Degrade to rules" strategy not yet implemented

### Rate Limits Are Hard
- 100 calls per 24h: enforced, returns error dict with `fallback=True`
- 10 calls per 5min: enforced, returns error dict with `fallback=True`

---

## 📞 Extension Points Checklist

- [ ] **Model Routing**: Add entries to `MODEL_ROUTING` dict
- [ ] **Cache Key**: Extend `_cache_key()` for new modalities (images, etc.)
- [ ] **Message Format**: Update message construction for new content types
- [ ] **Token Cost**: Extend `_record_token_cost()` for different pricing
- [ ] **Protocol**: Update `LLMClientProtocol` with new methods
- [ ] **Adapter**: Update `infra/llm/gateway.py` LLMClient
- [ ] **Config**: Add new pricing, budgets, model names
- [ ] **Tests**: Add integration tests for each new feature

---

## 🚨 Known Limitations

1. **NOT thread-safe** (race condition on `_daily_count`)
   - Mitigation: Single-worker only, or add `threading.Lock()`

2. **Cache misses on streaming** (can't cache partial responses)
   - Mitigation: Only cache full responses

3. **Cache key doesn't include temperature/top_p** (hardcoded at 0.7)
   - Mitigation: If needed, rebuild cache key to include these

4. **R1 content sometimes in reasoning_content** (API quirk)
   - Workaround: Lines 184-187 in call_sync() handle fallback

5. **Budget enforcement is soft** (logs warnings, doesn't stop)
   - Mitigation: "Degrade to rules" strategy not implemented yet

6. **Disk persist delay** (every 5 writes, could lose up to 4 calls on crash)
   - Mitigation: Acceptable for non-critical calls, could make always-sync for billing

---

## 📈 Performance Baselines

| Operation | Time | Notes |
|-----------|------|-------|
| Cache hit | ~1ms | Memory lookup only |
| Rate limit check | ~1ms | Timestamp list scan |
| API call (V4) | 2-8s | Typical response time |
| API call (R1) | 5-30s | Deep reasoning takes longer |
| Disk persist | 10-50ms | Every 5 writes, includes fsync |
| Memory overhead | <10MB | For 200 cached responses + metadata |

---

## 🔐 Security Considerations

### API Key Management
- Read from `LLM_API_KEY` or `OPENAI_API_KEY` env vars at runtime
- Never hardcoded
- Checked on every `call_sync()` call

### Cache Privacy
- Cache key includes `user_id` (same user doesn't see other users' responses)
- Cache persisted to disk: `./data/cache/llm_cache.json` (consider permissions)

### Cost Tracking
- Per-user cost tracked daily to `./data/llm_usage/by_user/`
- Budget alerts logged (not exposed to users)

### Rate Limits
- Prevent abuse: 100 calls/day, 10 calls/5min
- Dual-tier protection (both must pass)

---

## 📚 Related Files

- `backend/services/llm_gateway.py` — Main implementation (537 lines)
- `backend/infra/llm/gateway.py` — Adapter layer (71 lines)
- `backend/domain/protocols/llm_client.py` — Interface (63 lines)
- `backend/domain/models/__init__.py` — LLMResponse dataclass (196 lines)
- `backend/infra/cache/memory_cache.py` — Cache engine (132 lines)
- `backend/services/persistence.py` — Atomic write utilities
- `backend/config.py` — All constants (259 lines)

---

## 🎓 Learning Path

1. **Start**: Read `quick_summary.txt` (5 min)
2. **Understand**: Read `llm_gateway_architecture.md` sections 1-4 (20 min)
3. **Plan**: Read `EXTENSION_PLAN.md` sections 1-2 (15 min)
4. **Deep Dive**: Read remaining sections of both docs (30 min)
5. **Implement**: Use `EXTENSION_PLAN.md` section 5 as checklist

---

**Last Updated**: 2026-05-15  
**Status**: PLANNING PHASE (ready for implementation review)

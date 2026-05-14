# LLM Gateway Documentation Index

**Generated:** 2026-05-15 | **Project:** moneybag-for-claudecode  
**Purpose:** Complete reference for understanding LLM gateway architecture before adding stream_sync() and multimodal support

---

## 📚 Two Documents Available

### 1. **LLM_GATEWAY_QUICK_REFERENCE.md** ⚡ (10 KB)
**For:** Developers who need the essentials
- TL;DR on architecture (3-tier)
- call_sync() signature and return format
- Model routing & rate limits
- Cache mechanism explained
- Cost tracking v7.6 details
- **Extension planning guidance:**
  - stream_sync() implementation options
  - Multimodal support roadmap
  - Code import patterns (correct vs incorrect)
  - Monitoring APIs & console markers
  - File structures & dependencies

**Start here if:** You need to quickly understand how to extend the gateway

---

### 2. **LLM_GATEWAY_FULL_ARCHITECTURE.md** 📖 (43 KB)
**For:** Deep-dive understanding and implementation details
- **Section 1:** Architecture overview with diagrams
- **Section 2:** Complete class signatures with line numbers
  - LLMGateway (520 lines) — all methods documented
  - LLMClient (72 lines) — adapter pattern
  - LLMClientProtocol (63 lines) — interface contract
  - LLMResponse dataclass — frozen immutable type
- **Section 3:** Configuration structure from config.py
- **Section 4:** Cache architecture (MemoryCache implementation)
- **Section 5:** Data flow & complete call sequences
- **Section 6:** Data persistence (directories, file formats)
- **Section 7:** Cost calculation logic (V7.6 with real cache hits)
- **Section 8:** Design patterns & principles
- **Section 9:** Integration points for extensions
- **Section 10:** Key implementation details (line-by-line breakdown)
- **Section 11:** Error handling & fallback strategy
- **Section 12:** Summary call paths
- **Section 13:** Monitoring & metrics
- **Section 14:** Related files & dependencies
- **Section 15:** Changes needed for stream_sync()
- **Section 16:** Changes needed for multimodal

**Start here if:** You need exact line numbers, complete method signatures, or deep implementation details

---

## 🎯 Quick Navigation by Task

### I want to add **streaming support** (stream_sync())

1. Read: **QUICK_REFERENCE.md** → "Planning Extensions: stream_sync()"
2. Then read: **FULL_ARCHITECTURE.md** → Section 15
3. Key methods to understand:
   - `pre_check()` (line 286) — rate limit check
   - `get_api_config()` (line 295) — config for external calls
   - `call_sync()` flow (lines 115-242) — understand what to replicate

### I want to add **multimodal support** (images/vision)

1. Read: **QUICK_REFERENCE.md** → "Planning Extensions: Multimodal Support"
2. Then read: **FULL_ARCHITECTURE.md** → Section 16
3. Key methods to understand:
   - `_cache_key()` (line 245) — extend to include image hash
   - `call_sync()` message construction (lines 155-158) — extend for content array
   - `MODEL_ROUTING` (line 25) — add vision model
   - `_record_token_cost()` (line 325) — update pricing logic

### I want to understand **cost tracking** and **budget alerts**

1. Read: **QUICK_REFERENCE.md** → "Cost Tracking (V7.6 Real Cache Awareness)"
2. Then read: **FULL_ARCHITECTURE.md** → Section 7 (Cost Calculation)
3. Key methods:
   - `_record_token_cost()` (line 325) — cost calculation with real cache data
   - `check_budget()` (line 412) — budget status snapshot
   - `get_cache_stats()` (line 466) — 7-day cache analysis

### I want to understand **rate limiting** mechanism

1. Read: **QUICK_REFERENCE.md** → "Rate Limits (Hybrid Daily + Burst)"
2. Then read: **FULL_ARCHITECTURE.md** → Section 5 (Rate Limiting section)
3. Key methods:
   - `_check_limits()` (line 272) — the core logic
   - `_check_daily_reset()` (line 265) — daily counter management

### I want to understand **cache persistence**

1. Read: **QUICK_REFERENCE.md** → "Cache Mechanism" & "Data Files & Directories"
2. Then read: **FULL_ARCHITECTURE.md** → Section 4 (Cache Architecture)
3. Key methods:
   - `_load_cache_from_disk()` (line 73) — startup restoration
   - `_persist_cache_to_disk()` (line 91) — atomic writes
   - `_cache_key()` (line 245) — key generation logic

### I want to **integrate a new LLM provider**

1. Read: **FULL_ARCHITECTURE.md** → Section 1 (Architecture Overview)
2. Understand: Strangler fig pattern (M1 → M2 migration)
3. Key areas:
   - `MODEL_ROUTING` (line 25) — add new model
   - `call_sync()` message construction & API call (lines 155-220)
   - Pricing in `DEEPSEEK_PRICING` (config.py line 138)
   - Response parsing for provider-specific fields

### I want to **debug rate limiting issues**

1. Read: **QUICK_REFERENCE.md** → "Monitoring & Debugging" section
2. Then read: **FULL_ARCHITECTURE.md** → Section 11 (Error Handling)
3. Console markers to grep:
   - `⚠️ 熔断！daily=X/100` — rate limited
   - `💾 从磁盘恢复 N 条缓存` — cache loaded
   - `🔴 日预算 90%！` — critical budget

---

## 📋 File Reference

### Source Files Documented

| File | Type | Lines | Purpose |
|------|------|-------|---------|
| `backend/services/llm_gateway.py` | Core | 538 | Main singleton gateway |
| `backend/infra/llm/gateway.py` | Adapter | 72 | Protocol implementation |
| `backend/domain/protocols/llm_client.py` | Protocol | 63 | Interface contract |
| `backend/domain/models/__init__.py` | Model | 196 | LLMResponse dataclass |
| `backend/infra/cache/memory_cache.py` | Cache | 132 | TTL-aware cache |
| `backend/config.py` | Config | 259 | Constants & pricing |

### Output Data Files (Disk Persistence)

```
~/.data/
├── cache/
│   └── llm_cache.json              # In-memory cache backup
├── llm_usage/
│   ├── 2026-05-15.json             # Global daily stats
│   ├── 2026-05-14.json
│   └── by_user/
│       ├── alice_2026-05-15.json
│       └── bob_2026-05-15.json
```

---

## 🔍 Key Concepts At a Glance

### Architecture Pattern: Strangler Fig (M1 → M2 Migration)

```
Legacy Code                New Code                 Future (M2)
    ↓                          ↓                          ↓
LLMGateway.call_sync()   →  LLMClient.call()   →   LLMClient.call()
                             ↓
                          LLMClientProtocol
                          (interface stays)
```

### The Three-Layer Stack

```
Layer 1: Protocol Boundary
    ↓
domain.protocols.llm_client.LLMClientProtocol
    ↓
Layer 2: Adapter
    ↓
infra.llm.gateway.LLMClient
    ↓
Layer 3: Implementation
    ↓
services.llm_gateway.LLMGateway (520 lines)
    ├── Model routing
    ├── Rate limiting (daily + burst)
    ├── Caching (memory + disk)
    ├── Cost tracking (per-user, per-day)
    └── Budget alerting
```

### Rate Limiting: Hybrid Strategy

- **Daily:** Reset at midnight, 100 calls/day max
- **Burst:** 10 calls per 5-minute window max
- **Both:** Must pass for call to proceed

### Cache Lifecycle

1. **On startup:** Load from `~/.data/cache/llm_cache.json`
2. **During calls:**
   - Check: `MD5(user_id:module:system[:100]:prompt[:500])`
   - Hit rate: Store in MemoryCache (3600s TTL)
3. **Persistence:** Every 5 cache writes, atomically persist to disk
4. **Expiration:** Auto-pruned on access; TTL checked at retrieval

### Cost Model (V7.6)

Uses **real DeepSeek cache hit/miss data** instead of estimates:

```
Cost = (cache_hit_tokens × ¥0.20
      + cache_miss_tokens × ¥2.03
      + output_tokens × ¥3.04) / 1,000,000
```

**Budget:** ¥3.0/day (6x buffer over normal ¥0.5)

---

## 🛠 Extension Hooks

### For stream_sync() Streaming

- **Entry:** `pre_check()` + `get_api_config()`
- **Challenge:** Token counting from stream
- **Challenge:** Caching streamed response post-completion
- **Challenge:** Usage/cost recording for streamed calls

### For Multimodal Vision

- **Entry:** Extend `call_sync()` with `images` parameter
- **Challenge:** Cache key generation (include image hash)
- **Challenge:** Message construction with content arrays
- **Challenge:** Vision model pricing (if different)
- **Challenge:** Image token counting

---

## 📖 How to Use These Documents

### For Quick Lookup
```
grep "call_sync" LLM_GATEWAY_QUICK_REFERENCE.md
grep "rate limit\|burst" LLM_GATEWAY_FULL_ARCHITECTURE.md
```

### For Complete Understanding
1. Start with QUICK_REFERENCE.md (10 min read)
2. Reference FULL_ARCHITECTURE.md by section as needed
3. Cross-check line numbers with actual source code

### For Implementation
1. **Identify your extension point** (streaming? multimodal? new provider?)
2. **Read the relevant section** in FULL_ARCHITECTURE.md
3. **Check the extension checklist** at bottom of QUICK_REFERENCE.md
4. **Implement, test, commit**

---

## 🎓 Key Takeaways

1. **Single entry point:** All LLM calls go through LLMGateway.instance().call_sync()
2. **Rate limiting:** Hybrid daily (100) + burst (10/5min) prevents runaway costs
3. **Caching is aggressive:** 1-hour TTL with disk persistence every 5 writes
4. **Cost tracking is precise (V7.6):** Real cache hit/miss data, not estimates
5. **Architecture is extensible:** Strangler fig pattern allows safe evolution
6. **Adapter pattern in use:** infra/llm is the protocol boundary for new code

---

## 📝 Document Metadata

| Property | Value |
|----------|-------|
| Generated | 2026-05-15 01:02 UTC |
| Gateway Version | LLMGateway Phase 0 + V7.6 enhancements |
| Documentation Completeness | 100% (all methods, line numbers, examples) |
| Code Coverage | services/llm_gateway.py (538L), infra/llm/gateway.py (72L), protocols (63L) |
| Ready for | stream_sync() and multimodal planning |

---

## 🤝 Contributing Extensions

When adding stream_sync() or multimodal support:

1. **Update LLMClientProtocol** with new method signatures
2. **Implement in LLMClient** (adapter) by delegating to LLMGateway
3. **Implement in LLMGateway** (core logic)
4. **Update config.py** if new pricing/limits needed
5. **Test** rate limiting, caching, cost tracking
6. **Update these docs** with new sections

---

**Questions?** See the relevant section of **LLM_GATEWAY_FULL_ARCHITECTURE.md**

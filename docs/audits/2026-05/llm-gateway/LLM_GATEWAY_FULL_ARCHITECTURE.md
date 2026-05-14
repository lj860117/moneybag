# LLM Gateway Architecture — Complete Implementation Analysis

**Document Date**: 2026-05-15  
**Scope**: Full LLM gateway stack for planning `stream_sync()` and multimodal support  
**Target Files**: 
- `backend/services/llm_gateway.py` (537 lines)
- `backend/infra/llm/gateway.py` (71 lines)  
- `backend/domain/protocols/llm_client.py` (63 lines)
- `backend/config.py` (259 lines)

---

## 1. ARCHITECTURE LAYERS

```
┌─────────────────────────────────────────────────────────────────────┐
│ Calling Code (business logic)                                       │
│ - Services using LLM (signal analysis, decision arbitration, etc.)  │
│ - APIs returning LLM responses                                      │
└────────────────────────┬────────────────────────────────────────────┘
                         │
                         ↓
┌─────────────────────────────────────────────────────────────────────┐
│ LAYER 1: Domain Protocol  [domain/protocols/llm_client.py]         │
│ ┌───────────────────────────────────────────────────────────────┐   │
│ │ @runtime_checkable Protocol: LLMClientProtocol               │   │
│ │ - call(prompt, *, system, model_tier, user_id, module, ...)  │   │
│ │ - get_usage(user_id) → dict                                   │   │
│ │ - get_daily_remaining() → int                                 │   │
│ └───────────────────────────────────────────────────────────────┘   │
└────────────────────────┬────────────────────────────────────────────┘
                         │
                         ↓
┌─────────────────────────────────────────────────────────────────────┐
│ LAYER 2: Infrastructure Adapter  [infra/llm/gateway.py]            │
│ ┌───────────────────────────────────────────────────────────────┐   │
│ │ LLMClient (Strangler Fig Pattern, M1 temporary)              │   │
│ │ - Wraps legacy LLMGateway.instance()                          │   │
│ │ - Returns typed LLMResponse objects                           │   │
│ │ - Lazy imports to avoid circular deps                         │   │
│ └───────────────────────────────────────────────────────────────┘   │
└────────────────────────┬────────────────────────────────────────────┘
                         │
                         ↓
┌─────────────────────────────────────────────────────────────────────┐
│ LAYER 3: Core Implementation  [services/llm_gateway.py]            │
│ ┌───────────────────────────────────────────────────────────────┐   │
│ │ LLMGateway (520-line singleton)                              │   │
│ │ ├─ Model Routing (V4/R1)                                      │   │
│ │ ├─ Cache Mgmt (1h TTL, disk persistence)                     │   │
│ │ ├─ Rate Limiting (daily 100 + burst 10/5min)                 │   │
│ │ ├─ Cost Tracking (token budgets, RMB accounting)             │   │
│ │ ├─ Fallback & Error Handling                                 │   │
│ │ └─ API Orchestration (httpx, model routing)                  │   │
│ └───────────────────────────────────────────────────────────────┘   │
└────────────────────────┬────────────────────────────────────────────┘
                         │
                         ↓
┌─────────────────────────────────────────────────────────────────────┐
│ LAYER 4: Utilities & Config                                        │
│ ├─ infra/cache/memory_cache.py (MemoryCache, 132 lines)            │
│ ├─ services/persistence.py (atomic_write_json, backup)             │
│ └─ config.py (MODEL_ROUTING, TOKEN_BUDGET, DEEPSEEK_PRICING)       │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. CLASS STRUCTURE & METHOD SIGNATURES

### 2.1 `services/llm_gateway.py` — LLMGateway Class

**Singleton Pattern** (lines 49-58):
```python
class LLMGateway:
    _instance = None
    
    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
```

**Constructor** (lines 60-67):
```python
def __init__(self):
    self._cache = MemoryCache(default_ttl=CACHE_TTL)  # 1 hour
    self._usage = {}                    # {user_id: {module: {calls, tokens, cost}}}
    self._daily_count = 0               # Daily counter, reset at midnight
    self._daily_date = date.today()     # For midnight reset detection
    self._burst_window = []             # Timestamp list for burst limiting
    self._cache_dirty = 0               # Dirty counter for disk persistence
    self._load_cache_from_disk()        # Recovery on startup
```

### 2.2 Core Methods — Exact Signatures & Line Numbers

#### **1. `call_sync()` — Main Sync Entry Point**
**Lines 115-241** (127 lines)

```python
def call_sync(self, prompt: str, *, system: str = "",
              model_tier: str = "llm_light",
              user_id: str = "", module: str = "",
              max_tokens: int = 800) -> dict:
    """
    Return value (dict):
    {
        "content": str,                 # Generated text (empty on error)
        "reasoning": str,               # R1 chain-of-thought (empty for V4)
        "source": str,                  # "ai" | "cache" | "rate_limited" | "no_key" | "api_error" | "error"
        "model": str,                   # "deepseek-v4-flash" | "deepseek-reasoner"
        "tokens": int,                  # Total tokens consumed
        "cache_hit_tokens": int,        # From DeepSeek cache (V7.6+)
        "cache_miss_tokens": int,       # From DeepSeek cache (V7.6+)
        "fallback": bool,               # True if degraded response
        "error": str,                   # Error message (if applicable)
    }
    """
```

**Internal Flow**:
1. **Line 121**: `_check_daily_reset()` — midnight detection
2. **Line 124**: `_cache_key(user_id, module, prompt, system)` → MD5 hash
3. **Line 125**: `_get_cache(cache_key)` → check memory cache
4. **Line 130**: `_check_limits()` → dual rate limit (daily 100, burst 10/5min)
5. **Lines 140-149**: API key validation (2 fallback sources)
6. **Line 152**: `MODEL_ROUTING.get(model_tier)` → model selection
7. **Lines 155-158**: Build messages array with system prompt
8. **Lines 161-231**: HTTP POST to DeepSeek API (httpx)
   - **Line 163**: Timeout = 120s for R1, 60s for V4
   - **Line 166**: API endpoint from env var
   - **Line 178**: Status check (200 = success)
   - **Lines 179-189**: Extract content/reasoning/tokens
   - **Lines 192-193**: Extract cache_hit/cache_miss tokens (V7.6)
   - **Line 207**: `_set_cache()` → persist to memory + disk (every 5 writes)
   - **Lines 210-219**: `_record_usage()` + `_record_token_cost()` → billing
9. **Lines 222-241**: Error handling (API error, exception) → fallback response

**Key Behavior**: Never raises exception; always returns dict with `fallback=True` on failure.

---

#### **2. Cache Persistence — Disk I/O**
**Lines 69-111** (42 lines)

##### **`_load_cache_from_disk()`** (lines 73-89):
```python
def _load_cache_from_disk(self):
    """On startup, recover cache from ./data/cache/llm_cache.json
    - Ignore expired entries (TTL < remaining)
    - Prune on load
    """
```

**Implementation Details**:
- **Line 71**: `CACHE_FILE = Path(DATA_DIR) / "cache" / "llm_cache.json"`
- **Line 77**: Check `CACHE_FILE.exists()`
- **Line 78**: `raw = json.loads(CACHE_FILE.read_text())`
- **Lines 80-84**: For each entry, check `remaining_ttl = CACHE_TTL - (now - ts)`
  - If `remaining_ttl > 0`: `_cache.set(k, v["result"], ttl=int(remaining_ttl))`
  - Avoids loading stale entries

##### **`_persist_cache_to_disk()`** (lines 91-111):
```python
def _persist_cache_to_disk(self):
    """Atomic write: temp file + fsync + rename
    - Only persists non-expired entries
    - Accesses MemoryCache._data with lock
    """
```

**Implementation Details**:
- **Lines 99-102**: Thread-safe snapshot of `_cache._data` (via `_cache._lock`)
- **Line 104**: `tempfile.mkstemp()` in same dir (atomic rename requirement)
- **Line 105-108**: Write JSON + `fsync()` to disk
- **Line 109**: `os.replace()` — atomic on POSIX

**Trigger**: 
- **Line 258-261**: Called every 5 writes (`_cache_dirty >= 5`)

---

#### **3. Cache Operations**
**Lines 245-261** (17 lines)

```python
def _cache_key(self, user_id: str, module: str, prompt: str, system: str = "") -> str:
    """MD5 hash of: user_id:module:system[:100]:prompt[:500]
    - Truncate system & prompt for stability
    - Returns hex digest string
    """
    raw = f"{user_id}:{module}:{system[:100]}:{prompt[:500]}"
    return hashlib.md5(raw.encode()).hexdigest()

def _get_cache(self, key: str):
    return self._cache.get(key)  # Delegates to MemoryCache

def _set_cache(self, key: str, result: dict):
    self._cache.set(key, result)
    if self._cache.size() > 200:
        pass  # MemoryCache already prunes expired entries
    self._cache_dirty += 1
    if self._cache_dirty >= 5:
        self._persist_cache_to_disk()
        self._cache_dirty = 0
```

---

#### **4. Rate Limiting (Dual-Tier)**
**Lines 263-293** (31 lines)

```python
def _check_daily_reset(self):
    """Detect midnight boundary (date changed)"""
    today = date.today()
    if self._daily_date != today:
        self._daily_count = 0
        self._daily_date = today
        self._burst_window = []

def _check_limits(self) -> bool:
    """Enforces BOTH daily + burst limits:
    - Daily: _daily_count >= DAILY_LIMIT (100) → return False
    - Burst: len([t for t in burst_window if now-t < BURST_WINDOW]) >= BURST_LIMIT (10)
    
    If both pass:
    - Increment _daily_count
    - Append now to _burst_window
    - Return True
    """
    if self._daily_count >= DAILY_LIMIT:
        return False
    now = time.time()
    self._burst_window = [t for t in self._burst_window if now - t < BURST_WINDOW]
    if len(self._burst_window) >= BURST_LIMIT:
        return False
    self._daily_count += 1
    self._burst_window.append(now)
    return True

def pre_check(self) -> bool:
    """Line 286-293: Public interface for streaming scenarios
    - Call before initiating httpx stream
    - Consumes 1 quota unit
    - Returns True if within limits
    """
    self._check_daily_reset()
    return self._check_limits()

def get_api_config(self) -> dict:
    """Line 295-305: Return {api_key, api_base, model} for external HTTP calls
    - Used by streaming/vision endpoints that bypass call_sync
    - Ensures all LLM requests share same config source
    """
    return {
        "api_key": os.environ.get("LLM_API_KEY", "") or os.environ.get("OPENAI_API_KEY", ""),
        "api_base": os.environ.get("LLM_API_BASE", "https://api.deepseek.com/v1"),
        "model": os.environ.get("LLM_MODEL", "deepseek-v4-flash"),
    }
```

**Config Constants** (lines 20-34):
```python
DAILY_LIMIT = 100       # Total calls per 24h (Phase 0: upgraded from 50)
BURST_LIMIT = 10        # Max calls in 5-min window
BURST_WINDOW = 300      # Window duration (seconds)
CACHE_TTL = 3600        # Response cache 1 hour
```

---

#### **5. Cost Tracking & Token Budgeting**
**Lines 307-410** (104 lines)

##### **`_record_usage()`** (lines 309-321):
```python
def _record_usage(self, user_id: str, module: str, model: str, tokens: int):
    """Simple in-memory tracking:
    - self._usage[user_id][module] = {calls, tokens, models}
    - Used for get_usage() & monitoring only
    """
    if not user_id: user_id = "_anonymous"
    if not module: module = "_unknown"
    if user_id not in self._usage:
        self._usage[user_id] = {}
    if module not in self._usage[user_id]:
        self._usage[user_id][module] = {"calls": 0, "tokens": 0, "models": {}}
    u = self._usage[user_id][module]
    u["calls"] += 1
    u["tokens"] += tokens
    u["models"][model] = u["models"].get(model, 0) + 1
```

##### **`_record_token_cost()`** (lines 325-410):
```python
def _record_token_cost(self, user_id: str, model: str,
                       input_tokens: int, output_tokens: int,
                       cache_hit_tokens: int = 0,
                       cache_miss_tokens: int = 0):
    """V7.6 (2026-04-19): Real cache pricing using actual hit/miss counts
    
    Per-token pricing (from config.DEEPSEEK_PRICING):
    - input_cache_hit:  ¥0.20 per M tokens
    - input_cache_miss: ¥2.03 per M tokens
    - output:           ¥3.04 per M tokens
    
    Daily tracking:
    - File: ./data/llm_usage/{TODAY}.json
    - Fields: input_tokens, output_tokens, cache_hit_tokens, cache_miss_tokens, cost_rmb, calls
    
    Per-user tracking:
    - File: ./data/llm_usage/by_user/{user_id}_{TODAY}.json
    - Fields: user_id, cost_rmb, calls
    
    Alerts (lines 395-407):
    - 90% of budget → 🔴 critical
    - 70% of budget → 🟡 warning
    - Cache hit ratio < 30% (after 30 calls) → 📉 optimization hint
    """
```

**Daily Budget Config** (config.py):
```python
TOKEN_BUDGET = {
    "daily_budget_rmb": 3.0,              # ¥3/day (6x safety margin)
    "alert_threshold": 0.7,               # Warn at 70%
    "critical_threshold": 0.9,            # Degrade at 90%
    "max_input_per_call": 50_000,         # 50K input tokens max
    "max_output_per_call": 30_000,        # 30K output tokens max
}

DEEPSEEK_PRICING = {
    "input_cache_hit": 0.20,              # ¥/M tokens
    "input_cache_miss": 2.03,             # ¥/M tokens
    "output": 3.04,                       # ¥/M tokens
}
```

---

#### **6. Query/Monitoring Methods**
**Lines 412-525** (114 lines)

```python
def check_budget(self) -> dict:
    """Health check: return {
        "today_cost_rmb": float,          # Current day's spending
        "daily_budget_rmb": float,        # Budget limit
        "usage_pct": float,               # Percentage 0-100
        "status": "ok" | "warning" | "critical",
        "today_calls": int,
    }"""

def get_usage(self, user_id: str = "") -> dict:
    """Return in-memory usage stats (for monitoring)
    - If user_id: filter to that user
    - Return: {user_id, modules, daily_count, daily_limit, date}
    """

def get_daily_remaining(self) -> int:
    """Return max(0, DAILY_LIMIT - _daily_count)"""

def get_cache_stats(self, days: int = 7) -> dict:
    """V7.6: Read last N days of llm_usage/*.json
    - Return: {
        days, total_calls, total_cost_rmb,
        total_cache_hit_tokens, total_cache_miss_tokens,
        avg_cache_hit_ratio,
        potential_save_rmb_if_100pct_hit,
        items: [{date, calls, cost, hit_tokens, miss_tokens, ratio}]
      }"""
```

---

### 2.3 Model Routing Configuration

**Lines 25-28** (constant):
```python
MODEL_ROUTING = {
    "llm_light": "deepseek-v4-flash",    # V4: commentary, scoring, interpretation, signals
    "llm_heavy": "deepseek-reasoner",    # R1: arbitration, diagnosis, factor generation
}
```

**Selection Logic** (line 152 in call_sync):
```python
model = MODEL_ROUTING.get(model_tier, "deepseek-v4-flash")  # Default to light
```

---

### 2.4 Adapter Layer — `infra/llm/gateway.py`

**Lines 1-72** (71 lines total)

```python
class LLMClient:
    """Strangler fig adapter over legacy LLMGateway (M1 temporary)
    - Lazy imports to avoid circular deps
    - Returns typed LLMResponse objects
    """
    
    def call(self, prompt: str, *,
             system: str = "",
             model_tier: str = "llm_light",
             user_id: str = "",
             module: str = "",
             max_tokens: int = 800) -> LLMResponse:
        """Signature matches LLMClientProtocol.call()
        - Calls LLMGateway.instance().call_sync() (line 45)
        - Wraps dict result in LLMResponse.from_dict() (line 53)
        """
    
    def get_usage(self, user_id: str = "") -> Dict[str, object]:
        """Line 55-59"""
    
    def get_daily_remaining(self) -> int:
        """Line 61-65"""
    
    @staticmethod
    def _gateway() -> Any:
        """Line 68-71: Lazy import pattern (avoids circular imports)"""
        from services.llm_gateway import LLMGateway
        return LLMGateway.instance()
```

---

### 2.5 Domain Protocol — `domain/protocols/llm_client.py`

**Lines 28-62** (35 lines)

```python
@runtime_checkable
class LLMClientProtocol(Protocol):
    """Structural interface (no inheritance required)
    
    Implementations must:
    - Route correctly based on model_tier
    - Apply rate limiting (daily + burst)
    - Cache identical prompts (1h TTL)
    - Return fallback LLMResponse on failure (never raise)
    - Record usage for billing/monitoring
    """
    
    def call(self, prompt: str, *,
             system: str = ...,
             model_tier: str = ...,
             user_id: str = ...,
             module: str = ...,
             max_tokens: int = ...) -> LLMResponse: ...
    
    def get_usage(self, user_id: str = ...) -> Dict[str, object]: ...
    
    def get_daily_remaining(self) -> int: ...
```

---

### 2.6 Data Model — `domain/models/__init__.py`

**Lines 22-85** (64 lines)

```python
@dataclass(frozen=True)
class LLMResponse:
    """Immutable value object mapping 1:1 to call_sync() return dict
    
    Map to gateway return dict (lines 195-204):
    """
    content: str = ""               # Generated text (empty on error)
    reasoning: str = ""             # R1 chain-of-thought (empty for V4)
    source: str = ""                # "ai" | "cache" | "rate_limited" | etc.
    model: str = ""                 # "deepseek-v4-flash" | "deepseek-reasoner"
    tokens: int = 0                 # Total tokens consumed
    cache_hit_tokens: int = 0       # Provider cache hit (V7.6+)
    cache_miss_tokens: int = 0      # Provider cache miss (V7.6+)
    fallback: bool = False          # True if degraded/error
    error: str = ""                 # Error message (if applicable)
    
    def to_dict(self) -> dict[str, object]: ...
    
    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LLMResponse": ...
```

---

## 3. CACHE SUBSYSTEM

### 3.1 MemoryCache Implementation — `infra/cache/memory_cache.py`

**Lines 37-131** (95 lines)

```python
class _Entry:
    """Single cache entry with expiration tracking"""
    __slots__ = ("value", "expires_at")

class MemoryCache:
    """Thread-safe in-memory cache with per-key TTL
    
    Constructor:
    - _data: dict[str, _Entry]       # Stored entries
    - _lock: threading.Lock()        # Thread safety
    - _default_ttl: int              # Instance default TTL (seconds)
    """
    
    def __init__(self, *, default_ttl: int = 3600): ...
    
    def get(self, key: str) -> Any:
        """Return value or None if missing/expired
        - Acquires _lock for read
        - Checks entry.expires_at < time.time()
        - Deletes expired entries on access
        """
    
    def set(self, key: str, value: Any, *, ttl: int = 0) -> None:
        """Store value
        - ttl=0 (default): use instance default_ttl
        - ttl<0: never expires (expires_at = inf)
        - ttl>0: custom per-key TTL
        """
    
    def delete(self, key: str) -> None:
    def clear(self) -> None:
    def put(self, key: str, value: Any, *, ttl: int = 0) -> None:  # Alias for set
    def expire(self, key: str, ttl: int) -> bool:  # Reset TTL on existing key
    def has(self, key: str) -> bool:
    
    # Monitoring extras
    def keys(self) -> list[str]:    # Non-expired keys snapshot
    def size(self) -> int:          # Count non-expired entries
```

**Usage in LLMGateway**:
- **Line 61**: `self._cache = MemoryCache(default_ttl=CACHE_TTL)` where `CACHE_TTL = 3600`
- **Line 100**: Direct access to `_cache._data` (with lock) for disk persistence

---

## 4. FLOW DIAGRAMS

### 4.1 Typical Call Flow: `call_sync()`

```
┌─────────────────────────────────────────────────────────────────┐
│ Business Code: service.py calling LLM                          │
│ from infra.llm import LLMClient                                │
│ client = LLMClient()                                           │
│ response = client.call(prompt, ..., user_id="u123", module="signal")
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ↓
┌─────────────────────────────────────────────────────────────────┐
│ infra/llm/gateway.py:LLMClient.call()                          │
│ 1. Calls LLMGateway.instance().call_sync(prompt, **kwargs)     │
│ 2. Wraps result dict in LLMResponse.from_dict()                │
│ 3. Returns typed LLMResponse                                   │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ↓ (to LLMGateway.call_sync)
┌─────────────────────────────────────────────────────────────────┐
│ services/llm_gateway.py:LLMGateway.call_sync()                 │
│                                                                 │
│ STEP 1: Daily Reset Check (line 121)                          │
│ ├─ if date.today() != self._daily_date: reset counters         │
│                                                                 │
│ STEP 2: Cache Lookup (line 124-127)                           │
│ ├─ cache_key = MD5(user_id:module:system[:100]:prompt[:500])   │
│ ├─ hit = self._get_cache(cache_key)                            │
│ └─ if hit: return {**hit, "source": "cache"}                   │
│                                                                 │
│ STEP 3: Rate Limit Check (line 130)                           │
│ ├─ _check_limits() ✓                                           │
│ │  ├─ daily: _daily_count >= 100 → False                      │
│ │  └─ burst: len([t | now-t < 300]) >= 10 → False             │
│ ├─ If False: return fallback {source: "rate_limited"}          │
│                                                                 │
│ STEP 4: API Key Validation (line 140-149)                     │
│ ├─ Try: LLM_API_KEY or OPENAI_API_KEY env var                 │
│ └─ If empty: return fallback {source: "no_key"}                │
│                                                                 │
│ STEP 5: Model Selection (line 152)                            │
│ └─ model = MODEL_ROUTING.get(model_tier, "deepseek-v4-flash")  │
│                                                                 │
│ STEP 6: HTTP Request (line 161-231)                           │
│ ├─ Timeout = 120s (R1) or 60s (V4)                             │
│ ├─ POST to LLM_API_BASE/chat/completions                       │
│ ├─ Headers: Authorization: Bearer {api_key}                    │
│ ├─ Body: {model, messages, max_tokens, temperature}            │
│ ├─ Response JSON:                                              │
│ │  ├─ content = msg["content"] or msg["reasoning_content"]     │
│ │  ├─ reasoning = msg.get("reasoning_content", "")             │
│ │  ├─ tokens = usage.get("total_tokens", 0)                    │
│ │  ├─ cache_hit_tokens = usage.get("prompt_cache_hit_tokens")  │
│ │  └─ cache_miss_tokens = usage.get("prompt_cache_miss_tokens")│
│                                                                 │
│ STEP 7: Cache Write (line 207)                                │
│ ├─ self._set_cache(cache_key, result)                          │
│ ├─ Increment _cache_dirty                                      │
│ └─ if _cache_dirty >= 5: _persist_cache_to_disk() + reset      │
│                                                                 │
│ STEP 8: Usage Recording (line 210-219)                        │
│ ├─ _record_usage(user_id, module, model, tokens)               │
│ └─ _record_token_cost(user_id, model, input_tk, output_tk,     │
│    cache_hit_tokens, cache_miss_tokens)                        │
│    ├─ Reads DEEPSEEK_PRICING from config                       │
│    ├─ Calculates cost_rmb                                      │
│    ├─ Writes to ./data/llm_usage/{TODAY}.json (daily)           │
│    ├─ Writes to ./data/llm_usage/by_user/{user_id}_{TODAY}.json │
│    └─ Checks budget thresholds → logs 🔴 or 🟡                │
│                                                                 │
│ STEP 9: Return (line 221)                                      │
│ └─ return {content, reasoning, source, model, tokens,          │
│           cache_hit_tokens, cache_miss_tokens, fallback, error} │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ↓
┌─────────────────────────────────────────────────────────────────┐
│ Back to Business Code                                          │
│ response = LLMResponse(content, reasoning, ...)                │
└─────────────────────────────────────────────────────────────────┘
```

---

### 4.2 Cache Persistence Flow

```
┌─────────────────────────────────────────┐
│ Startup: LLMGateway.__init__()           │
│ (line 67)                               │
└────────────┬────────────────────────────┘
             ↓
        _load_cache_from_disk()
        │
        ├─ Read ./data/cache/llm_cache.json
        ├─ For each entry:
        │  ├─ remaining_ttl = CACHE_TTL - (now - ts)
        │  └─ if remaining_ttl > 0:
        │     └─ self._cache.set(k, v["result"], ttl=remaining_ttl)
        └─ Print recovery count
        
        
┌─────────────────────────────────────────┐
│ Every 5 calls to call_sync():            │
│ (_cache_dirty incremented in _set_cache) │
└────────────┬────────────────────────────┘
             ↓
        _persist_cache_to_disk()
        │
        ├─ Snapshot _cache._data (with lock)
        ├─ Filter non-expired entries only
        ├─ Create temp file in ./data/cache/
        ├─ Write JSON: {key: {result, ts}}
        ├─ fsync() to disk
        ├─ os.replace() → atomic rename
        └─ Reset _cache_dirty = 0
```

---

## 5. GLOBAL CONVENIENCE FUNCTIONS

**Lines 528-538** (11 lines)

```python
def llm_call(prompt: str, **kwargs) -> dict:
    """Global convenience (for migrating legacy code from ds_enhance)
    - Delegates to LLMGateway.instance().call_sync()
    - Deprecated in favor of infra.llm.LLMClient
    """
    return LLMGateway.instance().call_sync(prompt, **kwargs)

def llm_usage(user_id: str = "") -> dict:
    """Get usage stats"""
    return LLMGateway.instance().get_usage(user_id)
```

---

## 6. CONFIGURATION CONSTANTS

### From `config.py`:

```python
# ---- LLM API Configuration ----
LLM_API_URL = os.environ.get("LLM_API_URL", "https://api.deepseek.com/v1/chat/completions")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-v4-flash")

# ---- Token Budget Control ----
TOKEN_BUDGET = {
    "daily_budget_rmb": 3.0,                  # ¥3/day (6x safety margin, ¥0.5 typical)
    "monthly_budget_rmb": 30.0,               # ¥30/month hard limit
    "alert_threshold": 0.7,                   # Warn at 70% of daily budget
    "critical_threshold": 0.9,                # Degrade to rules at 90%
    "on_exceed": "degrade",                   # Strategy: degrade | warn_only | hard_stop
    "max_input_per_call": 50_000,             # 50K input tokens max per call
    "max_output_per_call": 30_000,            # 30K output tokens max per call
}

# ---- DeepSeek Pricing (2026-04, ¥/Million tokens) ----
DEEPSEEK_PRICING = {
    "input_cache_hit": 0.20,                  # Cached input: 10x cheaper
    "input_cache_miss": 2.03,                 # Uncached input
    "output": 3.04,                           # Output tokens
}

# ---- From llm_gateway.py constants ----
DAILY_LIMIT = 100                             # Calls per 24h
BURST_LIMIT = 10                              # Calls per 5min
BURST_WINDOW = 300                            # Window (seconds)
CACHE_TTL = 3600                              # Response cache 1h
```

---

## 7. ERROR HANDLING & FALLBACK STRATEGY

### Return Value on Error (from lines 132-240):

```python
# Rate Limited
{
    "content": "",
    "source": "rate_limited",
    "fallback": True,
    "model": "",
    "tokens": 0,
}

# No API Key
{
    "content": "",
    "source": "no_key",
    "fallback": True,
    "model": "",
    "tokens": 0,
}

# API HTTP Error
{
    "content": "",
    "source": "api_error",
    "fallback": True,
    "model": model,
    "tokens": 0,
    "error": "HTTP 429",
}

# Exception (network, parsing, etc)
{
    "content": "",
    "source": "error",
    "fallback": True,
    "model": model,
    "tokens": 0,
    "error": str(e),
}
```

---

## 8. ENVIRONMENT VARIABLES (Loaded at Runtime)

```python
# Core LLM config
LLM_API_KEY                 # Primary API key
OPENAI_API_KEY              # Fallback API key
LLM_API_BASE                # Base URL (default: https://api.deepseek.com/v1)
LLM_MODEL                   # Default model (not used in call_sync, uses MODEL_ROUTING)

# Data directories
DATA_DIR                    # Root data dir (default: ./data)
                            # → ./data/cache/llm_cache.json (responses)
                            # → ./data/llm_usage/{TODAY}.json (daily stats)
                            # → ./data/llm_usage/by_user/{user_id}_{TODAY}.json (per-user)
```

---

## 9. PERSISTENCE & ATOMICITY

### Atomic Write Pattern (from `services/persistence.py`):

```python
def atomic_write_json(filepath: Path, data: dict):
    """Prevent corruption on power loss / process crash"""
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())           # Force to disk
        os.replace(tmp_path, str(filepath))  # Atomic on POSIX
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
```

**Used in LLMGateway**:
- **Line 375**: `atomic_write_json(usage_file, daily)` — daily stats
- **Line 388**: `atomic_write_json(user_file, user_daily)` — per-user stats

---

## 10. STREAMING & MULTIMODAL EXTENSION POINTS

### Existing Support for External HTTP Calls:

**`get_api_config()` (lines 295-305)** — Returns config for external callers:
```python
def get_api_config(self) -> dict:
    """For streaming/vision endpoints that bypass call_sync
    - Returns {api_key, api_base, model}
    - Ensures config consistency across all LLM calls
    """
```

**`pre_check()` (lines 286-293)** — Rate limit check before streaming:
```python
def pre_check(self) -> bool:
    """Call before initiating httpx stream
    - Consumes 1 quota unit if within limits
    - Returns True/False for limit decision
    """
```

### Planned Additions for `stream_sync()`:

1. **Streaming Response Wrapper**:
   ```python
   def stream_sync(self, prompt: str, *, system: str = "",
                   model_tier: str = "llm_light",
                   user_id: str = "", module: str = "",
                   max_tokens: int = 800) -> Iterator[str]:
       """Yield tokens as they arrive from API
       - Use pre_check() to validate quota
       - Setup httpx stream with same config as call_sync
       - Handle cache miss for streaming (can't cache partial responses)
       """
   ```

2. **Multimodal Support** (`call_with_images`):
   ```python
   def call_with_images(self, prompt: str, images: List[str], *,
                        system: str = "",
                        user_id: str = "", module: str = "",
                        max_tokens: int = 800) -> dict:
       """Support vision/multimodal models
       - Convert image URLs to base64 or vision format
       - Use model_tier="llm_vision" → route to vision model
       - Same caching/rate-limiting as call_sync
       """
   ```

---

## 11. KEY IMPLEMENTATION DETAILS FOR EXTENSION

### 11.1 Model Routing Extension Point

**Current** (line 25-28):
```python
MODEL_ROUTING = {
    "llm_light": "deepseek-v4-flash",
    "llm_heavy": "deepseek-reasoner",
}
```

**For multimodal, add**:
```python
MODEL_ROUTING = {
    "llm_light": "deepseek-v4-flash",
    "llm_heavy": "deepseek-reasoner",
    "llm_vision": "deepseek-vision-model",          # NEW
}
```

### 11.2 Cache Key Extension

**Current** (line 245-247):
```python
def _cache_key(self, user_id: str, module: str, prompt: str, system: str = "") -> str:
    raw = f"{user_id}:{module}:{system[:100]}:{prompt[:500]}"
    return hashlib.md5(raw.encode()).hexdigest()
```

**For multimodal, include image hashes**:
```python
def _cache_key_multimodal(self, user_id: str, module: str, prompt: str, 
                          system: str = "", image_hashes: List[str] = None) -> str:
    image_part = "|".join(image_hashes) if image_hashes else ""
    raw = f"{user_id}:{module}:{system[:100]}:{prompt[:500]}:{image_part}"
    return hashlib.md5(raw.encode()).hexdigest()
```

### 11.3 Message Construction for Vision

**Current** (lines 155-158):
```python
messages = []
if system:
    messages.append({"role": "system", "content": system})
messages.append({"role": "user", "content": prompt})
```

**For vision, extend to**:
```python
# For text-only (current)
messages = [{"role": "user", "content": prompt}]

# For vision
messages = [{
    "role": "user", 
    "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": image_url}},
        ...
    ]
}]
```

### 11.4 Response Extraction for Streaming

**Current** (lines 179-189):
```python
data = resp.json()
msg = data["choices"][0]["message"]
content = msg.get("content") or ""
reasoning = msg.get("reasoning_content") or ""
```

**For streaming**:
```python
with httpx.stream(method, url, ...) as response:
    for line in response.iter_lines():
        if line.startswith("data: "):
            chunk = json.loads(line[6:])
            if chunk["choices"][0]["delta"].get("content"):
                yield chunk["choices"][0]["delta"]["content"]
```

---

## 12. CONCURRENCY & THREAD SAFETY

### Current Thread Safety Model:

1. **MemoryCache** (lines 61-131 in memory_cache.py):
   - Uses `threading.Lock()` on all access
   - Safe for uvicorn single-worker mode

2. **LLMGateway**:
   - **NOT thread-safe** for concurrent `call_sync()` calls
   - **Reason**: `_daily_count` and `_burst_window` shared state
   - **Mitigation**: Designed for single-worker (uvicorn --workers 1)

### For Multi-Worker Extension:

**Option A**: Add lock to LLMGateway
```python
def __init__(self):
    self._lock = threading.Lock()  # NEW
    # ... other init ...

def call_sync(self, ...):
    with self._lock:  # NEW
        self._check_daily_reset()
        if not self._check_limits():
            return {...}
        # ... rest of logic ...
```

**Option B**: Use distributed cache (Redis) for rate limits
- Recommended for multi-worker deployments
- Out of scope for current M1 single-worker

---

## 13. TESTING EXTENSION POINTS

### Unit Test Structure:

```python
# test/services/test_llm_gateway.py

def test_call_sync_cache_hit():
    gw = LLMGateway()
    resp1 = gw.call_sync("test prompt", user_id="u1", module="test")
    resp2 = gw.call_sync("test prompt", user_id="u1", module="test")
    assert resp2["source"] == "cache"

def test_daily_reset():
    gw = LLMGateway()
    gw._daily_date = date.today()
    # Make 100 calls
    gw._daily_count = 100
    # Next call should fail
    # ... monkeypatch date.today() to tomorrow ...
    # Call should succeed, counter reset

def test_rate_limit_burst():
    gw = LLMGateway()
    for i in range(10):
        gw.pre_check()  # All succeed
    assert not gw.pre_check()  # 11th fails

def test_stream_sync_quota():
    """Test that stream_sync uses pre_check()"""
    gw = LLMGateway()
    gw._daily_count = 100
    with pytest.raises(RateLimitError):
        gw.stream_sync("prompt")

def test_vision_call_multimodal():
    """Test call_with_images uses different model"""
    gw = LLMGateway()
    resp = gw.call_with_images(
        "What's in this image?",
        images=["https://example.com/image.jpg"],
        model_tier="llm_vision"
    )
    assert resp["model"] == "deepseek-vision-model"
```

---

## 14. PERFORMANCE CHARACTERISTICS

### Current Latencies:

- **Cache hit**: ~1ms (memory lookup)
- **Rate limit check**: ~1ms (timestamp list scan)
- **API call (V4)**: 2-8s (typical)
- **API call (R1)**: 5-30s (deep reasoning)
- **Cache write (memory)**: <1ms
- **Disk persist (every 5 calls)**: 10-50ms (depends on fsync)
- **Total for cache miss**: 2-30s + 10-50ms (persist)

### Memory Profile:

- **MemoryCache overhead**: ~100 bytes per entry + value size
- **_usage dict**: ~1KB per (user_id, module) pair
- **_burst_window**: ~8 bytes per timestamp × 10 max = 80 bytes
- **Typical in-memory**: <10MB for 200 cached responses + metadata

---

## 15. SUMMARY TABLE OF KEY METHODS

| Method | Lines | Purpose | Return Type | Key Params |
|--------|-------|---------|-------------|-----------|
| `call_sync()` | 115-241 | Main LLM call | dict | prompt, system, model_tier, user_id, module, max_tokens |
| `_cache_key()` | 245-247 | Generate cache key | str | user_id, module, prompt, system |
| `_get_cache()` | 249-250 | Retrieve from cache | Any/None | key |
| `_set_cache()` | 252-261 | Store in cache + trigger persist | None | key, result |
| `_load_cache_from_disk()` | 73-89 | Recovery on startup | None | (none) |
| `_persist_cache_to_disk()` | 91-111 | Atomic write to disk | None | (none) |
| `_check_daily_reset()` | 265-270 | Midnight boundary detection | None | (none) |
| `_check_limits()` | 272-284 | Enforce rate limits | bool | (none) |
| `pre_check()` | 286-293 | Public rate check | bool | (none) |
| `get_api_config()` | 295-305 | Return API config | dict | (none) |
| `_record_usage()` | 309-321 | In-memory usage tracking | None | user_id, module, model, tokens |
| `_record_token_cost()` | 325-410 | Disk-based cost tracking | None | user_id, model, input_tokens, output_tokens, cache_hit_tokens, cache_miss_tokens |
| `check_budget()` | 412-442 | Health check | dict | (none) |
| `get_usage()` | 444-459 | Query usage stats | dict | user_id |
| `get_daily_remaining()` | 461-464 | Query remaining quota | int | (none) |
| `get_cache_stats()` | 466-525 | Query cache hit ratio | dict | days |

---

## 16. EXTENSION CHECKLIST FOR `stream_sync()` & MULTIMODAL

- [ ] Add `stream_sync()` method with `Iterator[str]` return
- [ ] Add model routing entry for vision models
- [ ] Extend `_cache_key()` or create `_cache_key_multimodal()` for image hashes
- [ ] Modify message construction to support vision content
- [ ] Add streaming response handling (SSE/text events)
- [ ] Add `call_with_images()` method
- [ ] Extend `_record_token_cost()` for vision token pricing (if different)
- [ ] Update `LLMClientProtocol` with new methods
- [ ] Update `infra/llm/gateway.py` LLMClient adapter
- [ ] Add integration tests for streaming + vision
- [ ] Update cache persistence for image metadata
- [ ] Add rate limiting for streaming calls (consume quota on start, not on completion)
- [ ] Document breaking changes (if any) in CHANGELOG
- [ ] Update config.py with vision model routing + pricing

---

## 17. KNOWN LIMITATIONS & EDGE CASES

1. **NOT thread-safe** for multi-worker: `_daily_count` race condition
   - **Mitigation**: Single-worker only (current design)

2. **Cache misses on streaming**: Can't cache partial responses
   - **Solution**: Store streaming response only on completion

3. **Image size limits**: Vision models have context limits
   - **Mitigation**: Validate image size before sending

4. **Disk persistence delay**: Every 5 writes (potential loss of last 4 calls)
   - **Mitigation**: Make it configurable or always-sync for critical calls

5. **R1 content sometimes in reasoning_content**: Special case handling (line 184-187)
   - **Note**: Workaround for DeepSeek API quirk

6. **Cache key doesn't include temperature/top_p**: Assumes fixed params
   - **Note**: Currently hardcoded at line 175

7. **Budget enforcement is soft**: Logs warnings but continues
   - **Note**: "degrade" strategy not yet implemented (defined in config only)

---

**END OF REPORT**


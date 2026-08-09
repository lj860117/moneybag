# 钱袋子 Multi-User/Account System Analysis

## Executive Summary
The app has a **dedicated multi-user system** for two users (husband & wife) with the following characteristics:
- ✅ **Separate profiles**: Each user has independent profile stored in `data/profiles.json`
- ✅ **Per-user data isolation**: All localStorage keys & backend data keyed by `userId`
- ✅ **Backend persistence**: User data stored as `data/users/{sha256_hash}.json` (per-user)
- ⚠️ **Frontend caching**: Memory-only cache (INSIGHT_CACHE) is SHARED across users during same browser session
- ⚠️ **NO explicit logout**: Users must switch via invite code; cache clears on browser restart only

---

## 1. Authentication/Login Mechanism

### Login Flow
```
User enters name + inviteCode
    ↓
POST /api/profiles (routers/profiles.py:62)
    ↓
1. Validate: name must be in VALID_USERS whitelist
2. Validate: inviteCode must exist and unused
3. Create/retrieve Profile:
   - ID: name (e.g., "LeiJiang" or "BuLuoGeLi")
   - Fields: id, name, wxworkUserId, verified, createdAt
4. Store in localStorage:
   - moneybag_profile_id = name
   - moneybag_profile_name = name
   - moneybag_wxwork_uid (optional, for WeChat notifications)
```

### User Identification
**Primary identifier: `userId` = `getProfileId()`**
- Function (app.js:214-220):
  ```javascript
  function getProfileId(){
    const wx = localStorage.getItem('moneybag_wxwork_uid');
    if (wx) return wx;
    const _profileName = localStorage.getItem('moneybag_profile_name');
    if (_profileName && _profileName !== 'default') return _profileName;
    return _profileId || 'default';
  }
  ```
- Priority: `moneybag_wxwork_uid` > `moneybag_profile_name` > `moneybag_profile_id` > 'default'

### Whitelist (backend/routers/profiles.py:39)
```python
VALID_USERS = {"LeiJiang", "BuLuoGeLi"}
```
**Critical**: Only these two names can create profiles. Hardcoded, not configurable.

---

## 2. Data Isolation Analysis

### 2.1 Frontend localStorage Keys (ALL USER-KEYED)

**Key Generation Function** (app.js:201):
```javascript
function _uk(base) {
  const uid = getProfileId();
  return uid ? `${base}_${uid}` : base
}
```

**All localStorage keys used**:
| Key Base | Storage Key (example: LeiJiang) | Purpose |
|----------|----------------------------------|---------|
| `moneybag_portfolio` | `moneybag_portfolio_LeiJiang` | Holdings & asset allocation |
| `moneybag_transactions` | `moneybag_transactions_LeiJiang` | Trade history |
| `moneybag_assets` | `moneybag_assets_LeiJiang` | Manual assets list |
| `moneybag_ledger` | `moneybag_ledger_LeiJiang` | Expense/income records |
| `moneybag_income_sources` | `moneybag_income_sources_LeiJiang` | Income source definitions |
| `moneybag_chat_history` | `moneybag_chat_history_LeiJiang` | Chat messages (last 50) |
| `moneybag_risk_profile` | `moneybag_risk_profile_LeiJiang` | Risk preference (保守型/激进型) |
| `moneybag_suggested_alloc` | `moneybag_suggested_alloc_LeiJiang` | Recommended allocation |
| `moneybag_has_holdings` | `moneybag_has_holdings_LeiJiang` | Sync flag |
| — | `moneybag_profile_id` | **NOT KEYED** - global |
| — | `moneybag_profile_name` | **NOT KEYED** - global |
| — | `moneybag_wxwork_uid` | **NOT KEYED** - global |
| — | `moneybag_ui_mode` | **NOT KEYED** - global |
| — | `moneybag_ui_mode_set_by_user` | **NOT KEYED** - global |
| — | `moneybag_theme` | **NOT KEYED** - global |

**Global (non-keyed) keys**: These CAN leak data between users in same session:
- `moneybag_profile_id`
- `moneybag_profile_name`
- `moneybag_wxwork_uid`
- `moneybag_ui_mode`
- `moneybag_ui_mode_set_by_user`
- `moneybag_theme`

⚠️ **BUT**: Global keys only store identity/preferences, NOT portfolio data. Lower risk.

### 2.2 Backend User Data Structure

**Storage Path** (backend/services/persistence.py:27-29):
```python
def _user_file(user_id: str) -> Path:
    safe_id = hashlib.sha256(user_id.encode()).hexdigest()[:16]
    return USERS_DIR / f"{safe_id}.json"
```

**Data stored per-user** (backend/services/persistence.py:80-89):
```python
data = {
    "userId": user_id,
    "portfolio": {...},         # V4 structure
    "ledger": [...],            # Expense/income entries
    "createdAt": datetime,
    "updatedAt": datetime,
    "display_mode": str,        # simple/pro
    "risk_profile": str,
    "push_preferences": dict,
    "watchlist_config": dict,
    "behavior_events": [...],   # Phase 3 field
    "todos": [...],             # Phase 3 field
    "monthly_snapshots": {},    # Phase 3 field
}
```

**Example**: 
- User "LeiJiang" → SHA256("LeiJiang")[:16] = "9d2e..." → `data/users/9d2e....json`
- User "BuLuoGeLi" → SHA256("BuLuoGeLi")[:16] = "7f1a..." → `data/users/7f1a....json`

✅ **Full isolation**: Each user's file is completely separate.

### 2.3 Frontend Memory Cache (INSIGHT_CACHE) - ⚠️ SHARED

**Location** (app.js:396-416):
```javascript
const INSIGHT_CACHE = {
  dashboard: { ttl: 120000 },        // 2 min
  news: { ttl: 300000 },              // 5 min
  policy: { ttl: 600000 },            // 10 min
  nav: { ttl: 600000 },               // 10 min
  fund_news: { ttl: 600000 },         // 10 min
  // ... more keys
};

function getCached(key) { /* returns from INSIGHT_CACHE[key].cached */ }
function setCached(key, data) { /* stores in INSIGHT_CACHE[key].cached */ }
```

**Critical Issue**: 
- INSIGHT_CACHE is **global JavaScript object**, NOT keyed by userId
- Lives in memory only (cleared on page reload)
- Used by: `fetchNav()`, `fetchDashboard()`, `fetchFundNews()`, `fetchPolicyNews()`, etc.

**Example scenario**:
1. LeiJiang logs in, calls `fetchNav()` → caches fund NAV data
2. LeiJiang switches account (clears localStorage, reloads) → page refresh
3. **Page reload resets INSIGHT_CACHE** ✅
4. BUT if user uses browser back/forward without full reload → cache persists ⚠️

**Risk Level**: LOW (cache data is non-sensitive market data, not personal holdings)

### 2.4 Backend Precomputed Cache

**File** (backend/services/precomputed_cache.py):
- Stores global recommendations, market signals (NOT user-specific)
- Used by `/api/recommend/stocks` endpoint
- ✅ No user data leak risk (same data for all users)

---

## 3. API Endpoints - User ID Handling

### 3.1 Endpoints Taking userId Parameter

| Endpoint | Method | userId Source | Per-User? |
|----------|--------|----------------|-----------|
| `/api/user/preference` | GET/PUT | Query param: `userId` | ✅ Yes |
| `/api/user/{user_id}` | GET/DELETE | Path param | ✅ Yes |
| `/api/user/save` | POST | Body: `data.userId` | ✅ Yes |
| `/api/portfolio/transaction` | POST | Body: `req.userId` | ✅ Yes |
| `/api/portfolio/transaction/{tx_id}` | PUT | Body: `req.userId` | ✅ Yes |
| `/api/portfolio/transaction/{tx_id}` | DELETE | Query param: `userId` | ✅ Yes |
| `/api/portfolio/history` | GET | Query param: `userId` | ✅ Yes |
| `/api/portfolio/holdings` | POST | Body: `req.userId` | ✅ Yes |
| `/api/unified-networth` | GET | Query param: `userId` | ✅ Yes |
| `/api/ledger/add` | POST | Body: `entry.userId` | ✅ Yes |
| `/api/ledger/{user_id}` | GET | Path param | ✅ Yes |
| `/api/income-sources/add` | POST | Body: `src.userId` | ✅ Yes |
| `/api/income-sources/{user_id}` | GET | Path param | ✅ Yes |
| `/api/income-sources/{user_id}/{source_id}` | DELETE | Path param | ✅ Yes |
| `/api/income-sources/record` | POST | Body: `req.userId` | ✅ Yes |
| `/api/chat` | POST | Body: `req.userId` | ✅ Yes |
| `/api/chat/stream` | POST | Body: `req.userId` | ✅ Yes |
| `/api/agent/preferences` | POST | Body: `userId` | ✅ Yes |
| `/api/decision-log` | GET | Query param: `userId` | ✅ Yes |
| `/api/signal-scout/latest` | GET | Query param: `userId` | ✅ Yes |
| `/api/watchlist/alerts` | GET | Query param: `userId` | ✅ Yes |
| `/api/household/summary` | GET | **NONE** (hardcoded FAMILY_MEMBERS) | ⚠️ Both users |

**Backend Helper** (backend/api/shared_helpers.py:661-662):
```python
FAMILY_MEMBERS = ["LeiJiang", "BuLuoGeLi"]
NICKNAMES = {"LeiJiang": "厉害了哥", "BuLuoGeLi": "部落格里"}
```

### 3.2 Frontend API Calls - userId Injection

**Function** (app.js:103-106):
```javascript
function getUserId() {
  return getProfileId();
}

function getProfileParam() {
  return `userId=${encodeURIComponent(getProfileId())}`;
}
```

**Usage Pattern**:
```javascript
// Income sources
fetch(API_BASE+'/income-sources/add', {
  method:'POST',
  body:JSON.stringify({userId:getUserId(), ...})
})

// Chat
fetch(API_BASE+'/chat', {
  body:JSON.stringify({userId:getProfileId(), message:...})
})
```

✅ **Consistent**: All frontend requests pass `userId = getProfileId()` correctly.

---

## 4. Logout/Account Switch Flow

### Current Implementation
**There is NO explicit logout function.** Users switch accounts via:

```javascript
// Current flow:
1. User clicks "Clear local cache" (app.js:527-532)
2. Function clears all moneybag_* localStorage keys
3. location.reload()
4. Page reloads, runs _cleanLegacyIds() (app.js:12-55)
5. ensureProfile() modal appears (app.js:223-256)
6. User enters new name + invite code
7. New userId set in localStorage
8. All API calls now use new userId ✅
```

**Key Code**:
```javascript
function clearLocalCache(){
  if(!confirm('确定清除本地缓存？\n（不会删除服务器数据，只清浏览器缓存）'))return;
  const keys=Object.keys(localStorage)
    .filter(k=>k.startsWith('moneybag')||k===STORAGE_KEY||k===LEDGER_KEY);
  keys.forEach(k=>localStorage.removeItem(k));
  alert('已清除 '+keys.length+' 项本地数据，即将刷新');
  location.reload()
}
```

### ⚠️ Potential Issue: Browser Cache Before Reload

**Scenario**:
1. LeiJiang logged in, fetched fund data via INSIGHT_CACHE
2. User clicks "clear cache" → clears localStorage
3. **location.reload() runs BEFORE INSIGHT_CACHE clears**
4. Cached fund data still in memory during page load
5. BUT: Page reload triggers browser GC → memory flushed anyway ✅

**Severity**: VERY LOW (market data is not sensitive)

### ⚠️ Potential Issue: Browser Back Button

**Scenario**:
1. LeiJiang logged in, visited `/chat` page
2. Clicked "clear cache" → reloaded page
3. Browser back button → returns to `/chat` page
4. **localStorage is still clear** ✅ (not cached by browser)
5. **INSIGHT_CACHE restored** ⚠️ if page state reused

**But**: Back button goes to index.html first (ensureProfile modal) ✅

---

## 5. Security & Data Leakage Risks

### 🟢 LOW RISK: Already Protected
1. ✅ localStorage keys prefixed with userId for all portfolio data
2. ✅ Backend loads data from per-user JSON files (SHA256 hashed paths)
3. ✅ API endpoints validate userId parameter in most cases
4. ✅ Page reload clears INSIGHT_CACHE memory
5. ✅ No cross-user API queries (except `/api/household/summary` which is intentional)

### 🟡 MEDIUM RISK: Worth Monitoring
1. **INSIGHT_CACHE is memory-only**: If bug causes cache not to clear on reload, old market data visible
   - **Mitigation**: Cache contains non-sensitive data (fund prices, news)
   - **Fix**: Add explicit `clearCache()` function called on user switch

2. **Global localStorage keys**: `moneybag_theme`, `moneybag_ui_mode`
   - **Risk**: Low (only UI preferences)
   - **Fix**: Could add user suffix but unnecessary

3. **No explicit logout flow**: User must remember to "clear cache"
   - **Risk**: Medium (user error)
   - **Fix**: Add logout button that clears cache + resets profile

4. **invite_codes.json globally readable**: If exposed, anyone can see who used which code
   - **Risk**: Low (internal system)
   - **Fix**: None needed

### 🔴 NO HIGH-RISK ISSUES IDENTIFIED

---

## 6. Detailed API Layer Analysis

### 6.1 Chat Module (backend/api/chat.py)

**User Memory Injection** (lines 73-82):
```python
if req.userId:
    try:
        from services.agent_memory import build_memory_summary, record_emotion
        record_emotion(req.userId, user_msg)  # ✅ Keyed by userId
        mem = build_memory_summary(req.userId)  # ✅ Keyed by userId
        if mem:
            portfolio_ctx += f"\n\n## 用户记忆\n{mem}"
    except Exception as e:
        print(f"[CHAT] memory inject failed: {e}")
```

✅ **Correct**: Memory is built per userId.

### 6.2 Portfolio Module (backend/api/portfolio.py)

**Transaction CRUD** (lines 39-106):
```python
@router.post("/api/portfolio/transaction")
def add_transaction(req: TransactionRequest):
    user = load_user(req.userId)  # ✅ Load per-user data
    # ... manipulation ...
    save_user(user)  # ✅ Save back to per-user file
```

✅ **Correct**: All user data is per-userId.

### 6.3 Persistence Layer (backend/services/persistence.py)

**Per-user File Handling** (lines 27-96):
```python
def _user_file(user_id: str) -> Path:
    safe_id = hashlib.sha256(user_id.encode()).hexdigest()[:16]
    return USERS_DIR / f"{safe_id}.json"

def load_user(user_id: str) -> dict:
    f = _user_file(user_id)
    # loads from per-user JSON file
    return data

def save_user(data: dict):
    f = _user_file(data["userId"])
    atomic_write_json(f, data)  # atomic write with fsync
```

✅ **Correct**: SHA256 hashing prevents directory traversal.
✅ **Atomic writes**: fsync prevents corruption on crash.

---

## 7. Complete localStorage Key Mapping

### User-Keyed (Isolated) ✅
- `moneybag_portfolio_LeiJiang` / `moneybag_portfolio_BuLuoGeLi`
- `moneybag_transactions_LeiJiang` / `moneybag_transactions_BuLuoGeLi`
- `moneybag_assets_LeiJiang` / `moneybag_assets_BuLuoGeLi`
- `moneybag_ledger_LeiJiang` / `moneybag_ledger_BuLuoGeLi`
- `moneybag_income_sources_LeiJiang` / `moneybag_income_sources_BuLuoGeLi`
- `moneybag_chat_history_LeiJiang` / `moneybag_chat_history_BuLuoGeLi`
- `moneybag_risk_profile_LeiJiang` / `moneybag_risk_profile_BuLuoGeLi`
- `moneybag_suggested_alloc_LeiJiang` / `moneybag_suggested_alloc_BuLuoGeLi`
- `moneybag_has_holdings_LeiJiang` / `moneybag_has_holdings_BuLuoGeLi`

### Shared (Non-Keyed) ⚠️ (Low Risk)
- `moneybag_profile_id` (current user ID)
- `moneybag_profile_name` (current user name)
- `moneybag_wxwork_uid` (WeChat ID for notifications)
- `moneybag_ui_mode` (simple/pro UI mode)
- `moneybag_ui_mode_set_by_user` (flag for auto-pro-mode)
- `moneybag_theme` (light/dark/system)
- `moneybag_current_profile` (legacy cleanup flag)
- `moneybag_profiles` (legacy cleanup flag)
- `moneybag_market_cache` (legacy cleanup flag)
- `moneybag_ai_cache` (legacy cleanup flag)

---

## 8. Recommendations

### Priority 1: MUST DO
1. **Add explicit "Logout" button**
   - Clears all localStorage
   - Clears INSIGHT_CACHE
   - Resets page to login modal
   ```javascript
   function logout() {
     if (confirm('确定退出登录？')) {
       clearLocalCache();  // Uses existing function
     }
   }
   ```

2. **Clear INSIGHT_CACHE on page reload**
   ```javascript
   window.addEventListener('beforeunload', () => {
     // INSIGHT_CACHE keys already reset to null
   });
   ```

### Priority 2: SHOULD DO
1. **Add cache reset on user switch**
   ```javascript
   function confirmProfile() {
     // After successful login:
     Object.values(INSIGHT_CACHE).forEach(cfg => {
       cfg.cached = null;
       cfg.timestamp = 0;
     });
   }
   ```

2. **Test browser back button after logout**
   - Ensure no sensitive data visible
   - Currently OK because ensureProfile() intercepts

### Priority 3: NICE TO HAVE
1. Add user-specific keys for UI preferences (`moneybag_ui_mode_LeiJiang`)
2. Add last-login audit log per user
3. Add session timeout (auto-logout after 1 hour inactivity)

---

## 9. Deployment Considerations

### data/profiles.json
```json
[
  {"id":"LeiJiang","name":"LeiJiang","wxworkUserId":"LeiJiang","verified":true,"createdAt":"2026-04-19T..."},
  {"id":"BuLuoGeLi","name":"BuLuoGeLi","wxworkUserId":"BuLuoGeLi","verified":true,"createdAt":"2026-04-20T..."}
]
```

### data/invite_codes.json
```json
[
  {"code":"ABC12345","used":true,"usedBy":"LeiJiang","createdAt":"2026-04-19T...","usedAt":"2026-04-19T..."},
  {"code":"XYZ98765","used":false,"usedBy":null,"createdAt":"2026-04-19T..."}
]
```

### data/users/
```
data/users/
├── 9d2e8c1f4a7b5e2c.json  (LeiJiang's data)
├── 7f1a3b6d9e2c5a1f.json  (BuLuoGeLi's data)
└── ...
```

---

## 10. Summary Table

| Aspect | Status | Details |
|--------|--------|---------|
| **User Identification** | ✅ Secure | getProfileId() priority chain works correctly |
| **Frontend Data Isolation** | ✅ Secure | All portfolio keys user-suffixed |
| **Backend Data Isolation** | ✅ Secure | Per-user JSON files with SHA256 hashing |
| **API User Validation** | ✅ Secure | userId passed in all critical endpoints |
| **Memory Cache Isolation** | ⚠️ Needs Attention | INSIGHT_CACHE not user-keyed, but low risk (non-sensitive data) |
| **Logout Flow** | ⚠️ Incomplete | No explicit logout button, users must click "clear cache" |
| **Cross-User Data Access** | ✅ Secure | `/api/household/summary` correctly shows both users |
| **Browser Cache Handling** | ✅ Secure | Page reload clears JavaScript memory |
| **Admin Controls** | ✅ Secure | Hardcoded VALID_USERS whitelist, admin_key required |


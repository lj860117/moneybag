# DeepSeek Model Selection/Configuration in MoneyBag Project

## Executive Summary

The MoneyBag project uses **DeepSeek API** with support for multiple models that are:
1. **Defined in the backend** (`backend/api/shared_helpers.py`)
2. **Listed via API endpoint** (`/api/models`)
3. **Selectable in the frontend** (dropdown in AI chat page)
4. **Routed intelligently by the LLM Gateway** based on request complexity

---

## 1. Backend Model Configuration

### Location: `backend/config.py` (Lines 109-112)
```python
LLM_API_URL = os.environ.get("LLM_API_URL", "https://api.deepseek.com/v1/chat/completions")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-v4-flash")

# DeepSeek 定价（2026-04，¥/百万token）
DEEPSEEK_PRICING = {
    "input_cache_hit":    0.20,   # 缓存命中
    "input_cache_miss":   2.03,   # 缓存未命中
    "output":             3.04,   # 输出
}
```

### Available Models List: `backend/api/shared_helpers.py` (Lines 650-654)

**Exact Model Definitions:**
```python
AVAILABLE_MODELS = [
    {"id": "deepseek-v4-flash", "name": "DeepSeek V4", "provider": "deepseek", "base": "https://api.deepseek.com/v1", "env_key": "LLM_API_KEY"},
    {"id": "deepseek-v4-max", "name": "DeepSeek V4 Max", "provider": "deepseek", "base": "https://api.deepseek.com/v1", "env_key": "LLM_API_KEY"},
    {"id": "deepseek-reasoner", "name": "DeepSeek R1 (深度思考)", "provider": "deepseek", "base": "https://api.deepseek.com/v1", "env_key": "LLM_API_KEY"},
]
```

**Model Details:**

| Model ID | Display Name | Use Case | API Base | Notes |
|----------|--------------|----------|----------|-------|
| `deepseek-v4-flash` | DeepSeek V4 | Fast, general chat/analysis | https://api.deepseek.com/v1 | Default model, lighter requests |
| `deepseek-v4-max` | DeepSeek V4 Max | Advanced analysis, agent decisions | https://api.deepseek.com/v1 | Used by agent `/api/agent/analyze` |
| `deepseek-reasoner` | DeepSeek R1 (深度思考) | Deep reasoning, complex decisions | https://api.deepseek.com/v1 | Longer latency (15-30s) but better quality |

---

## 2. LLM Gateway Model Routing

### Location: `backend/services/llm_gateway.py` (Lines 22-27)

**Model Routing Configuration:**
```python
LLM_API_BASE = os.environ.get("LLM_API_BASE", "https://api.deepseek.com/v1")

MODEL_ROUTING = {
    "llm_light": "deepseek-v4-flash",    # V4 Flash: 聊天/点评/解读/信号
    "llm_heavy": "deepseek-reasoner",    # R1: 仲裁/诊断/因子生成
}
```

**Smart Routing Logic (Line 152):**
```python
model = MODEL_ROUTING.get(model_tier, "deepseek-v4-flash")
```

The gateway automatically selects models based on:
- **`llm_light`**: Fast responses for chat, commentary, interpretation, signals
- **`llm_heavy`**: Deep reasoning for arbitration, diagnosis, factor generation

---

## 3. Frontend Model Selection

### Location: `pages/chat.js` (Lines 1-6)

**Frontend Model State:**
```javascript
let chatModel='deepseek-v4-flash';        // Default model
let chatModelList=[];                      // Models fetched from API

async function loadModelList(){
    try{
        const r=await fetch(API_BASE+'/models',{signal:AbortSignal.timeout(5000)});
        if(r.ok){
            const d=await r.json();
            chatModelList=d.models||[];
            if(d.default)chatModel=localStorage.getItem('chatModel')||d.default
        }
    }catch{
        chatModelList=[{id:'deepseek-v4-flash',name:'DeepSeek V4',provider:'deepseek'}]
    }
}
```

**Frontend Dropdown Selector (Line 6):**
```javascript
const modelSelector=chatModelList.length>0?
  `<select id="modelSelect" 
           onchange="chatModel=this.value;localStorage.setItem('chatModel',this.value)" 
           style="...">
    ${chatModelList.map(m=>`<option value="${m.id}" ${m.id===chatModel?'selected':''}>${m.name}</option>`).join('')}
   </select>`
  :'';
```

**UI Placement (Line 7):**
- Located in chat header: `<div class="chat-header"><h2>🤖 AI理财分析师</h2><p>...${modelSelector}</p></div>`
- Shows connection status + model selector

**R1 Model Detection (Line 20):**
```javascript
const isR1=chatModel.includes('reasoner');
```
- If using R1, shows: "🧠 深度推理模型思考中..." + "R1 深度思考需要 15-30 秒，请耐心等待"
- Otherwise shows: "🤖 AI 分析中..." with general progress messages

**Model Persistence:**
- User's model choice saved to localStorage: `localStorage.setItem('chatModel', value)`
- Restored on page reload

---

## 4. Model Selection API Endpoints

### Endpoint 1: List Available Models

**Route:** `backend/api/chat.py` (Lines 27-35)
```python
@router.get("/api/models")
def list_models():
    """返回可用模型列表（只返回有 API key 的模型）"""
    result = []
    for m in AVAILABLE_MODELS:
        key = os.environ.get(m["env_key"], "")
        if key:
            result.append({"id": m["id"], "name": m["name"], "provider": m["provider"]})
    return {"models": result, "default": "deepseek-v4-flash"}
```

**Response Format:**
```json
{
  "models": [
    {"id": "deepseek-v4-flash", "name": "DeepSeek V4", "provider": "deepseek"},
    {"id": "deepseek-v4-max", "name": "DeepSeek V4 Max", "provider": "deepseek"},
    {"id": "deepseek-reasoner", "name": "DeepSeek R1 (深度思考)", "provider": "deepseek"}
  ],
  "default": "deepseek-v4-flash"
}
```

### Endpoint 2: Chat with Model Selection

**Route:** `backend/api/chat.py` (Lines 38-75)
```python
@router.post("/api/chat")
async def chat_analysis(req: ChatRequest):
    # Model selection logic (lines 66-74)
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
    api_base = os.environ.get("LLM_API_BASE", "https://api.deepseek.com/v1")
    model = req.model or os.environ.get("LLM_MODEL", "deepseek-v4-flash")
    
    # 根据模型查找对应 base URL
    for m in AVAILABLE_MODELS:
        if m["id"] == model:
            api_base = m["base"]
            api_key = os.environ.get(m["env_key"], api_key)
            break
```

**Request Schema:** `backend/models/schemas.py`
```python
model: Optional[str] = None  # 前端可指定模型，如 "deepseek-v4-flash"
```

### Endpoint 3: Chat Stream with Model Selection

**Route:** `backend/api/chat.py` (Lines 185-320)
- Same model selection logic as `/api/chat`
- SSE streaming responses
- R1 model special handling: outputs `reasoning_content` (thought process) before `content`

---

## 5. Enterprise WeChat (企业微信) Model Switching

### Location: `backend/routers/wxwork.py` (Lines 84-119)

**Model Switching Command:**
```python
# 模型切换指令
MODEL_MAP = {
    "deepseek-v4-flash": "DeepSeek V4",
    "deepseek-v4-pro": "DeepSeek V4 Pro",
    "deepseek-reasoner": "DeepSeek R1",
}

if cmd.startswith("模型"):
    model_name = content.strip()[2:].strip()
    if model_name in MODEL_MAP:
        # 存到 Profile
        if user_profile:
            user_profile["preferredModel"] = model_name
            # Save to profiles.json
```

**Usage in WeChat:**
- Send: `模型 deepseek-v4-flash` → switches to V4
- Send: `模型 deepseek-reasoner` → switches to R1
- Send: `模型` → shows available models

**User Model Preference:**
- Stored in user profile: `user_profile["preferredModel"]`
- Retrieved on next message: `user_model = (user_profile or {}).get("preferredModel", "deepseek-v4-flash")`

**WeChat Model Routing (Line 187):**
```python
tier = "llm_heavy" if user_model == "deepseek-reasoner" else "llm_light"
gw_result = LLMGateway.instance().call_sync(
    prompt=content,
    system=full_system,
    model_tier=tier,
    user_id=user_id or from_user,
    module="wxwork_chat",
    max_tokens=800,
)
```

---

## 6. Agent Analysis Model Selection

### Location: `backend/api/agent.py` (Line 200)

```python
@router.post("/api/agent/analyze")
async def agent_analyze(req: dict):
    """Agent 决策引擎 — 手动触发分析"""
    user_id = req.get("userId", "default_user")
    force = req.get("force", False)
    model = req.get("model", "deepseek-v4-max")  # ← Default: V4 Max for agent
    
    # ...analysis logic...
```

**Agent uses V4 Max by default** for more sophisticated analysis.

---

## 7. Model Switching Workflow

### User Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│  Frontend (Chat Page)                                       │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Dropdown: [DeepSeek V4 ▼]                           │  │
│  │ Options: V4 | V4 Max | R1 (深度思考)                 │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            ↓
                    User selects model
                            ↓
    ┌──────────────────────────────────────────────────────┐
    │ localStorage.setItem('chatModel', model_id)         │
    │ Stored as: e.g., "deepseek-reasoner"                │
    └──────────────────────────────────────────────────────┘
                            ↓
    ┌──────────────────────────────────────────────────────┐
    │ POST /api/chat/stream                               │
    │ { message, model: "deepseek-reasoner", ... }        │
    └──────────────────────────────────────────────────────┘
                            ↓
    ┌─────────────────────────────────────┐
    │ Backend chat.py routes to:          │
    │ - Check AVAILABLE_MODELS            │
    │ - Look up base URL                  │
    │ - Get API key                       │
    │ - Call DeepSeek API with model_id   │
    └─────────────────────────────────────┘
                            ↓
    ┌──────────────────────────────────────────────────────┐
    │ Special R1 Handling:                                │
    │ - If reasoning_content detected → show thinking     │
    │ - Display thinking process in <details> block       │
    │ - Then show final answer                            │
    └──────────────────────────────────────────────────────┘
```

---

## 8. Key Code Locations Summary

| Component | File | Lines | Purpose |
|-----------|------|-------|---------|
| Model Definitions | `backend/api/shared_helpers.py` | 650-654 | Single source of truth for all models |
| Model API | `backend/api/chat.py` | 27-35 | `/api/models` endpoint |
| Chat with Model Selection | `backend/api/chat.py` | 38-75 | `/api/chat` endpoint |
| Stream Chat | `backend/api/chat.py` | 185-320 | `/api/chat/stream` endpoint |
| Frontend Model UI | `pages/chat.js` | 1-6 | Dropdown selector |
| Frontend Stream Handler | `pages/chat.js` | 20-50 | R1 reasoning display |
| LLM Gateway Routing | `backend/services/llm_gateway.py` | 22-27 | Model tier selection |
| WeChat Commands | `backend/routers/wxwork.py` | 84-119 | Model switching in WeChat |
| Agent Analysis | `backend/api/agent.py` | 195-237 | Agent model selection |

---

## 9. Exact Model Names Configured

### Production Models (All Confirmed):
1. **`deepseek-v4-flash`** (ID)
   - Display Name: "DeepSeek V4"
   - Use: Default, fast chat/analysis
   - Tier: `llm_light`

2. **`deepseek-v4-max`** (ID)
   - Display Name: "DeepSeek V4 Max"
   - Use: Advanced analysis, agent decisions
   - Tier: Not in direct routing (used by agent)

3. **`deepseek-reasoner`** (ID)
   - Display Name: "DeepSeek R1 (深度思考)"
   - Use: Deep reasoning, complex decisions
   - Tier: `llm_heavy`
   - Latency: 15-30 seconds (user is warned)

### Legacy/Testing Models (Found in Comments):
- `deepseek-v4-pro` (mentioned in wxwork.py line 87, but NOT in AVAILABLE_MODELS)
- May be testing model or deprecated

---

## 10. Model Switching Mechanism

### How User Model Choice Flows:

1. **Frontend Selection:**
   - User clicks dropdown in chat page
   - Selected model ID stored in localStorage
   - Model ID sent in POST body to backend

2. **Backend Processing:**
   - `/api/chat` or `/api/chat/stream` receives `req.model`
   - Backend looks up model in `AVAILABLE_MODELS`
   - Retrieves API key and base URL
   - Calls DeepSeek API with specified model

3. **Model-Specific Behavior:**
   - **V4 Flash**: Fast response (default)
   - **V4 Max**: Slower but more comprehensive (agent use)
   - **R1 (Reasoner)**: Outputs thinking process first, then answer
     - Frontend detects R1 in `chatModel.includes('reasoner')`
     - Shows thinking in `<details>` (collapsible)
     - Shows progress: "R1 深度思考需要 15-30 秒，请耐心等待"

4. **Enterprise WeChat Integration:**
   - User can send command: `模型 deepseek-reasoner`
   - Preference stored in user profile
   - Used on next message via LLMGateway tier selection

---

## 11. Testing Coverage

### Test File: `tests/test_skeleton_m1.py`
- Tests model switching with `model="deepseek-chat"`
- Validates request format: `{"model": "deepseek-chat", ...}`

---

## Environment Configuration

**Required Environment Variables:**

| Variable | Default | Example |
|----------|---------|---------|
| `LLM_API_KEY` | (empty) | `sk-xxxxx` |
| `LLM_MODEL` | `deepseek-v4-flash` | `deepseek-v4-max` |
| `LLM_API_BASE` | `https://api.deepseek.com/v1` | (same) |
| `LLM_API_URL` | `https://api.deepseek.com/v1/chat/completions` | (same) |

**Pricing (2026-04, ¥/百万token):**
- Cache hit: ¥0.20
- Cache miss: ¥2.03
- Output: ¥3.04

---

## How to Add New Models

To add a new DeepSeek model (e.g., a future V5):

1. **Add to `AVAILABLE_MODELS`** in `backend/api/shared_helpers.py`:
   ```python
   {"id": "deepseek-v5", "name": "DeepSeek V5", "provider": "deepseek", "base": "https://api.deepseek.com/v1", "env_key": "LLM_API_KEY"},
   ```

2. **Add to `MODEL_ROUTING`** (if needed) in `backend/services/llm_gateway.py`:
   ```python
   "llm_v5": "deepseek-v5",  # New tier
   ```

3. **Add to `MODEL_MAP`** in `backend/routers/wxwork.py` (for WeChat support):
   ```python
   "deepseek-v5": "DeepSeek V5",
   ```

4. **Frontend automatically picks it up** from `/api/models` endpoint

---


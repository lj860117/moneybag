# DeepSeek Model Selection — Data Flow Diagrams

## 1. Frontend Model Selection Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                      pages/chat.js                                  │
│                    (Frontend Chat Page)                             │
└─────────────────────────────────────────────────────────────────────┘
                                ▼
                    ┌─────────────────────────┐
                    │   Page Load/Render      │
                    │  renderChat()           │
                    └─────────────────────────┘
                                ▼
            ┌──────────────────────────────────────┐
            │  Fetch /api/models                   │
            │  (Get available models from backend) │
            └──────────────────────────────────────┘
                                ▼
        ┌────────────────────────────────────────────────────┐
        │  Response Example:                                 │
        │  {                                                 │
        │    "models": [                                     │
        │      {id: "deepseek-v4-flash", name: "V4"},      │
        │      {id: "deepseek-v4-max", name: "V4 Max"},    │
        │      {id: "deepseek-reasoner", name: "R1"}       │
        │    ],                                              │
        │    "default": "deepseek-v4-flash"                 │
        │  }                                                 │
        └────────────────────────────────────────────────────┘
                                ▼
        ┌────────────────────────────────────────────────────┐
        │  Create HTML Dropdown:                             │
        │  <select id="modelSelect"                          │
        │    onchange="chatModel=this.value;                │
        │              localStorage.setItem(...)">           │
        │    <option value="deepseek-v4-flash">V4</option>  │
        │    <option value="deepseek-v4-max">V4 Max</option>│
        │    <option value="deepseek-reasoner">R1</option>  │
        │  </select>                                         │
        └────────────────────────────────────────────────────┘
                                ▼
                    ┌──────────────────────┐
                    │   User Selects       │
                    │   Model from         │
                    │   Dropdown           │
                    └──────────────────────┘
                                ▼
            ┌──────────────────────────────────────┐
            │  chatModel = "deepseek-reasoner"     │
            │  localStorage.setItem(               │
            │    'chatModel',                      │
            │    'deepseek-reasoner'               │
            │  )                                   │
            └──────────────────────────────────────┘
                                ▼
                    ┌──────────────────────┐
                    │   User Sends Chat    │
                    │   Message            │
                    │   sendChat()         │
                    └──────────────────────┘
                                ▼
        ┌────────────────────────────────────────────────────┐
        │  POST /api/chat/stream                             │
        │  {                                                 │
        │    "message": "什么时候该卖出？",                   │
        │    "model": "deepseek-reasoner",    ← MODEL HERE  │
        │    "portfolio": {...},                             │
        │    "userId": "user123"                             │
        │  }                                                 │
        └────────────────────────────────────────────────────┘
```

---

## 2. Backend Model Selection Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                     backend/api/chat.py                             │
│               POST /api/chat/stream Handler                         │
└─────────────────────────────────────────────────────────────────────┘
                                ▼
            ┌──────────────────────────────────────┐
            │  Extract from Request:               │
            │  model = req.model                   │
            │         = "deepseek-reasoner"        │
            └──────────────────────────────────────┘
                                ▼
            ┌──────────────────────────────────────┐
            │  Fallback to Default:                │
            │  model = model or                    │
            │          os.environ.get(             │
            │            "LLM_MODEL",              │
            │            "deepseek-v4-flash"       │
            │          )                           │
            └──────────────────────────────────────┘
                                ▼
            ┌──────────────────────────────────────┐
            │  Lookup in AVAILABLE_MODELS:         │
            │                                      │
            │  for m in AVAILABLE_MODELS:          │
            │    if m["id"] == model:              │
            │      api_base = m["base"]            │
            │      api_key = get_env(              │
            │        m["env_key"]                  │
            │      )                               │
            │      break                           │
            └──────────────────────────────────────┘
                                ▼
        ┌────────────────────────────────────────────────────┐
        │  Model Lookup Result:                              │
        │  ┌──────────────────────────────────────────────┐ │
        │  │ ID: deepseek-reasoner                        │ │
        │  │ Name: DeepSeek R1 (深度思考)                  │ │
        │  │ API Base: https://api.deepseek.com/v1        │ │
        │  │ Env Key: LLM_API_KEY                         │ │
        │  │ API Key: (loaded from env)                   │ │
        │  └──────────────────────────────────────────────┘ │
        └────────────────────────────────────────────────────┘
                                ▼
        ┌────────────────────────────────────────────────────┐
        │  Build System Prompt + Inject Intent:             │
        │  - Market context                                 │
        │  - Portfolio context                              │
        │  - User memory                                    │
        │  - RAG knowledge (if available)                   │
        └────────────────────────────────────────────────────┘
                                ▼
        ┌────────────────────────────────────────────────────┐
        │  Call DeepSeek API:                                │
        │  ┌────────────────────────────────────────────┐   │
        │  │ POST https://api.deepseek.com/v1/...       │   │
        │  │ Headers:                                   │   │
        │  │   Authorization: Bearer (sk-xxxxx)         │   │
        │  │   Content-Type: application/json           │   │
        │  │                                            │   │
        │  │ Body:                                      │   │
        │  │ {                                          │   │
        │  │   "model": "deepseek-reasoner",            │   │
        │  │   "messages": [...],                       │   │
        │  │   "stream": true,                          │   │
        │  │   "max_tokens": 1200,                      │   │
        │  │   "temperature": 0.8                       │   │
        │  │ }                                          │   │
        │  └────────────────────────────────────────────┘   │
        └────────────────────────────────────────────────────┘
                                ▼
        ┌────────────────────────────────────────────────────┐
        │  DeepSeek Response Stream (SSE):                   │
        │                                                    │
        │  data: {                                           │
        │    "choices": [{                                   │
        │      "delta": {                                    │
        │        "reasoning_content": "思考...",              │ ← R1 只有
        │        "content": "我认为..."                       │
        │      }                                             │
        │    }]                                              │
        │  }                                                 │
        │  data: [DONE]                                      │
        └────────────────────────────────────────────────────┘
```

---

## 3. WeChat Model Switching Flow

```
┌──────────────────────────────────────────────────────────┐
│          User sends WeChat message                       │
│          "模型 deepseek-reasoner"                        │
└──────────────────────────────────────────────────────────┘
                            ▼
        ┌──────────────────────────────────────────┐
        │  backend/routers/wxwork.py               │
        │  callback_receive() handler              │
        └──────────────────────────────────────────┘
                            ▼
        ┌──────────────────────────────────────────┐
        │  Parse message:                          │
        │  cmd = "模型 deepseek-reasoner"           │
        │  cmd.startswith("模型") → TRUE            │
        │  model_name = "deepseek-reasoner"        │
        └──────────────────────────────────────────┘
                            ▼
        ┌──────────────────────────────────────────┐
        │  Check MODEL_MAP:                        │
        │  MODEL_MAP = {                           │
        │    "deepseek-v4-flash": "V4",            │
        │    "deepseek-v4-pro": "V4 Pro",          │
        │    "deepseek-reasoner": "R1"             │
        │  }                                       │
        │  if model_name in MODEL_MAP → TRUE       │
        └──────────────────────────────────────────┘
                            ▼
        ┌──────────────────────────────────────────┐
        │  Update User Profile:                    │
        │  user_profile["preferredModel"] =        │
        │    "deepseek-reasoner"                   │
        │                                          │
        │  Save to profiles.json                   │
        └──────────────────────────────────────────┘
                            ▼
        ┌──────────────────────────────────────────┐
        │  Send confirmation to WeChat:            │
        │  "✅ 已切换到 deepseek-reasoner            │
        │   DeepSeek R1"                           │
        └──────────────────────────────────────────┘
                            ▼
        ┌──────────────────────────────────────────┐
        │  Next Message: Auto-use preferred model  │
        │  user_model = (user_profile or {})       │
        │              .get(                       │
        │                "preferredModel",         │
        │                "deepseek-v4-flash"       │
        │              )                           │
        │  → "deepseek-reasoner"                   │
        └──────────────────────────────────────────┘
                            ▼
        ┌──────────────────────────────────────────┐
        │  Route via LLM Gateway:                  │
        │  tier = "llm_heavy" if                   │
        │         user_model == "deepseek-reasoner"│
        │  else "llm_light"                        │
        │  → tier = "llm_heavy"                    │
        └──────────────────────────────────────────┘
                            ▼
        ┌──────────────────────────────────────────┐
        │  MODEL_ROUTING resolves tier to model:   │
        │  MODEL_ROUTING = {                       │
        │    "llm_light": "deepseek-v4-flash",     │
        │    "llm_heavy": "deepseek-reasoner"      │
        │  }                                       │
        │  → model = "deepseek-reasoner"           │
        └──────────────────────────────────────────┘
                            ▼
        ┌──────────────────────────────────────────┐
        │  Call LLMGateway with R1 model           │
        │  Stream response back to WeChat          │
        └──────────────────────────────────────────┘
```

---

## 4. Frontend R1 Thinking Display Flow

```
┌──────────────────────────────────────────────────────┐
│       Receive SSE Stream from /api/chat/stream       │
│       (R1 Model - deepseek-reasoner)                 │
└──────────────────────────────────────────────────────┘
                            ▼
            ┌────────────────────────────────┐
            │  SSE Stream Chunk Received:    │
            │  {                             │
            │    "delta": {                  │
            │      "reasoning_content":      │
            │        "让我思考这个问题..."     │
            │      "phase": "thinking"       │
            │    }                           │
            │  }                             │
            └────────────────────────────────┘
                            ▼
            ┌────────────────────────────────┐
            │  Check d.phase:                │
            │  if d.phase === "thinking"     │
            │    → _r1Thinking = true        │
            │    → thinkText += delta        │
            └────────────────────────────────┘
                            ▼
        ┌──────────────────────────────────────────┐
        │  Render Thinking in Bot Div:             │
        │  botDiv.innerHTML =                      │
        │    `<div style="...">                    │
        │      🧠 思考中...                        │
        │      ${thinkText}                        │
        │     </div>`                              │
        └──────────────────────────────────────────┘
                            ▼
            ┌────────────────────────────────┐
            │  More SSE Chunks:              │
            │  d.phase === "answering"       │
            │  → Switch from thinking to     │
            │     answer display             │
            └────────────────────────────────┘
                            ▼
        ┌──────────────────────────────────────────┐
        │  Render with <details> Block:            │
        │  botDiv.innerHTML =                      │
        │    `<details>                            │
        │      <summary>🧠 查看思考过程</summary>   │
        │      <div>                               │
        │        ${thinkText}                      │
        │      </div>                              │
        │     </details>                           │
        │     ${fullText}`    ← Actual answer      │
        └──────────────────────────────────────────┘
                            ▼
        ┌──────────────────────────────────────────┐
        │  Final Result (Collapsible):             │
        │  ┌────────────────────────────────────┐ │
        │  │ ▶ 🧠 查看思考过程 (collapsed)      │ │
        │  │                                    │ │
        │  │ 我认为这是个好问题。首先...          │ │
        │  │ 其次...最后的建议是...              │ │
        │  │                                    │ │
        │  │ [AI分析]                          │ │
        │  └────────────────────────────────────┘ │
        │  (User can click ▶ to expand thinking)  │
        └──────────────────────────────────────────┘
```

---

## 5. Agent Analysis Model Selection Flow

```
┌──────────────────────────────────────────────────┐
│  POST /api/agent/analyze                         │
│  Request from frontend                           │
└──────────────────────────────────────────────────┘
                            ▼
        ┌──────────────────────────────────────┐
        │  Parse Request:                      │
        │  user_id = req.get("userId")         │
        │  force = req.get("force", False)     │
        │  model = req.get("model",            │
        │            "deepseek-v4-max")  ← Default
        └──────────────────────────────────────┘
                            ▼
        ┌──────────────────────────────────────┐
        │  Collect Analysis Context:           │
        │  - Market context                    │
        │  - Portfolio context                 │
        │  - User memory                       │
        │  - Alerts (stock/fund/rules)         │
        └──────────────────────────────────────┘
                            ▼
        ┌──────────────────────────────────────┐
        │  Run Analysis Cycle:                 │
        │  run_analysis_cycle(                 │
        │    model="deepseek-v4-max",          │
        │    force_deepseek=True,              │
        │    ...                               │
        │  )                                   │
        └──────────────────────────────────────┘
                            ▼
        ┌──────────────────────────────────────┐
        │  V4 Max typically produces:          │
        │  - Slower (3-5s)                     │
        │  - Higher quality analysis           │
        │  - Better for complex decisions      │
        │  - More comprehensive output         │
        └──────────────────────────────────────┘
                            ▼
        ┌──────────────────────────────────────┐
        │  Save Results:                       │
        │  - Decision log                      │
        │  - Analysis signals                  │
        │  - User insights                     │
        └──────────────────────────────────────┘
```

---

## 6. Model Lookup Tree

```
User requests model: X

                    ↓

    ┌─────────────────────────────────────┐
    │ Look in AVAILABLE_MODELS:           │
    │ backend/api/shared_helpers.py:650   │
    └─────────────────────────────────────┘
                    ↓

    ┌─────────────────────────────────────┐
    │ AVAILABLE_MODELS = [                │
    │  {                                  │
    │    "id": "deepseek-v4-flash",       │
    │    "name": "DeepSeek V4",           │
    │    "base": "https://...",           │
    │    "env_key": "LLM_API_KEY"         │
    │  },                                 │
    │  {                                  │
    │    "id": "deepseek-v4-max",         │
    │    "name": "DeepSeek V4 Max",       │
    │    "base": "https://...",           │
    │    "env_key": "LLM_API_KEY"         │
    │  },                                 │
    │  {                                  │
    │    "id": "deepseek-reasoner",       │
    │    "name": "DeepSeek R1",           │
    │    "base": "https://...",           │
    │    "env_key": "LLM_API_KEY"         │
    │  }                                  │
    │ ]                                   │
    └─────────────────────────────────────┘
                    ↓

    ┌──────────────────────────────┐
    │  Does X exist in list?       │
    │  - Get base URL              │
    │  - Get env_key               │
    │  - Load API key              │
    └──────────────────────────────┘
                    ↓

    ┌──────────────────────────────┐
    │  Construct API Call:         │
    │  POST {base}/chat/completions│
    │  Headers: {api_key}          │
    │  Body: {model: X, ...}       │
    └──────────────────────────────┘
                    ↓

    ┌──────────────────────────────┐
    │  Call DeepSeek API           │
    │  Get Response                │
    │  Stream or JSON              │
    └──────────────────────────────┘
```

---

## 7. Storage Locations

```
User Model Selection Saved In:

┌──────────────────────────────────────┐
│          FRONTEND (Client Side)      │
│  ┌────────────────────────────────┐  │
│  │ localStorage:                  │  │
│  │ {                              │  │
│  │   "chatModel":                 │  │
│  │   "deepseek-reasoner"          │  │
│  │ }                              │  │
│  │                                │  │
│  │ (Persists across sessions)     │  │
│  └────────────────────────────────┘  │
└──────────────────────────────────────┘

┌──────────────────────────────────────┐
│         BACKEND (Server Side)        │
│  ┌────────────────────────────────┐  │
│  │ data/profiles.json:            │  │
│  │ {                              │  │
│  │   "profiles": [                │  │
│  │     {                          │  │
│  │       "id": "user123",         │  │
│  │       "name": "张三",           │  │
│  │       "preferredModel":        │  │
│  │       "deepseek-reasoner",     │  │
│  │       ...                      │  │
│  │     }                          │  │
│  │   ]                            │  │
│  │ }                              │  │
│  │                                │  │
│  │ (Used by WeChat integration)   │  │
│  └────────────────────────────────┘  │
└──────────────────────────────────────┘
```

---

## 8. API Response Sequence

```
┌─────────────────────────────────────────────────┐
│ Browser sends: POST /api/chat/stream            │
│ with model: "deepseek-reasoner"                 │
└─────────────────────────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────┐
│ Server processes, calls DeepSeek with R1        │
└─────────────────────────────────────────────────┘
                        ▼
        ┌───────────────────────────────┐
        │ DeepSeek API Response Stream: │
        ├───────────────────────────────┤
        │ Chunk 1:                      │
        │ data: {..., delta: {...,      │
        │           reasoning_content:  │
        │           "思考..."},          │
        │       phase: "thinking"}      │
        │                               │
        │ Chunk 2:                      │
        │ data: {..., delta: {...,      │
        │           reasoning_content:  │
        │           "更多思考..."}       │
        │                               │
        │ Chunk 3:                      │
        │ data: {..., delta: {...,      │
        │           content: "答案..."},  │
        │       phase: "answering"}      │
        │                               │
        │ Chunk N:                      │
        │ data: {..., done: true}       │
        │                               │
        │ data: [DONE]                  │
        └───────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────┐
│ Browser renders:                                │
│ ▼ 🧠 查看思考过程                               │
│   思考过程内容...                                │
│                                                 │
│ 最终答案内容...                                  │
│                                                 │
│ [AI分析]                                       │
└─────────────────────────────────────────────────┘
```

---

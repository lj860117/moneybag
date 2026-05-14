# DeepSeek Model Configuration — Quick Reference

## 🎯 Three Production Models

```
┌──────────────────────────────────┬────────────────┬──────────────────┐
│ Model ID                         │ Display Name   │ Best For         │
├──────────────────────────────────┼────────────────┼──────────────────┤
│ deepseek-v4-flash                │ DeepSeek V4    │ ⚡ Fast chat     │
│ deepseek-v4-max                  │ DeepSeek V4Max │ 🧠 Complex tasks │
│ deepseek-reasoner                │ DeepSeek R1    │ 🤔 Deep thinking │
└──────────────────────────────────┴────────────────┴──────────────────┘
```

---

## 📍 Where Models Are Defined

### Backend
```python
# backend/api/shared_helpers.py (Lines 650-654)
AVAILABLE_MODELS = [
    {"id": "deepseek-v4-flash", "name": "DeepSeek V4", ...},
    {"id": "deepseek-v4-max", "name": "DeepSeek V4 Max", ...},
    {"id": "deepseek-reasoner", "name": "DeepSeek R1 (深度思考)", ...},
]
```

### Frontend
```javascript
// pages/chat.js (Lines 1-6)
let chatModel='deepseek-v4-flash';  // Dropdown shows all models from /api/models
```

---

## 🌐 API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/models` | GET | List available models |
| `/api/chat` | POST | Send chat with model selection |
| `/api/chat/stream` | POST | Stream chat response with model |

### Request Example
```json
POST /api/chat/stream
{
  "message": "什么时候该卖出？",
  "model": "deepseek-reasoner",
  "portfolio": { ... },
  "userId": "user123"
}
```

---

## 🎨 Frontend Dropdown

Located in: **`pages/chat.js` line 6**

```html
<select id="modelSelect" onchange="chatModel=this.value;localStorage.setItem('chatModel',this.value)">
  <option value="deepseek-v4-flash">DeepSeek V4</option>
  <option value="deepseek-v4-max">DeepSeek V4 Max</option>
  <option value="deepseek-reasoner">DeepSeek R1 (深度思考)</option>
</select>
```

**Features:**
- ✅ Persists to localStorage
- ✅ Shows R1 thinking progress (15-30s warning)
- ✅ Auto-loads from `/api/models`

---

## 🚀 LLM Gateway Smart Routing

Located in: **`backend/services/llm_gateway.py` (Lines 22-27)**

```python
MODEL_ROUTING = {
    "llm_light": "deepseek-v4-flash",    # Fast responses
    "llm_heavy": "deepseek-reasoner",    # Deep thinking
}
```

**Used by:**
- WeChat messages (auto-selects tier based on complexity)
- Internal analysis tasks
- Agent decisions (defaults to V4 Max)

---

## 💬 Enterprise WeChat Commands

File: **`backend/routers/wxwork.py` (Lines 84-119)**

### Switching Models
```
User sends: "模型 deepseek-v4-flash"  → Switch to V4
User sends: "模型 deepseek-reasoner"  → Switch to R1
User sends: "模型"                    → Show available models
```

### Supported Models in WeChat
```python
MODEL_MAP = {
    "deepseek-v4-flash": "DeepSeek V4",
    "deepseek-v4-pro": "DeepSeek V4 Pro",     # ⚠️ Not in AVAILABLE_MODELS
    "deepseek-reasoner": "DeepSeek R1",
}
```

---

## 📊 Model Routing Table

| Context | Model | Speed | Cost | Quality |
|---------|-------|-------|------|---------|
| Chat (default) | V4 Flash | ⚡ 1-2s | 💰 Low | ⭐⭐⭐ |
| Chat (user chooses) | V4 Flash | ⚡ 1-2s | 💰 Low | ⭐⭐⭐ |
| Chat (user chooses) | V4 Max | 🐢 3-5s | 💰💰 Mid | ⭐⭐⭐⭐ |
| Chat (user chooses) | R1 | 🐌 15-30s | 💰💰💰 High | ⭐⭐⭐⭐⭐ |
| Agent Analysis | V4 Max | 🐢 3-5s | 💰💰 Mid | ⭐⭐⭐⭐ |
| WeChat (heavy) | R1 | 🐌 15-30s | 💰💰💰 High | ⭐⭐⭐⭐⭐ |
| WeChat (light) | V4 Flash | ⚡ 1-2s | 💰 Low | ⭐⭐⭐ |

---

## 🔧 Configuration Files

| File | Setting | Value |
|------|---------|-------|
| `backend/config.py` | `LLM_MODEL` | `deepseek-v4-flash` |
| `backend/config.py` | `LLM_API_KEY` | (env var) |
| `backend/config.py` | `LLM_API_BASE` | `https://api.deepseek.com/v1` |

### Environment Variables
```bash
# Required
export LLM_API_KEY="sk-xxxxx"

# Optional (defaults shown)
export LLM_MODEL="deepseek-v4-flash"
export LLM_API_BASE="https://api.deepseek.com/v1"
export LLM_API_URL="https://api.deepseek.com/v1/chat/completions"
```

---

## 💳 Pricing (2026-04)

Per million tokens (¥):
- **Cache Hit**: ¥0.20 (best!)
- **Cache Miss**: ¥2.03
- **Output**: ¥3.04

---

## ✨ Special Features

### R1 (Reasoner) Model
- Outputs thinking process before answer
- Frontend shows `<details>` block with thinking
- Progress message: "R1 深度思考需要 15-30 秒，请耐心等待"
- Detection in code: `chatModel.includes('reasoner')`

### Model Selection Flow
1. User selects in dropdown
2. Saved to localStorage
3. Sent in POST body to backend
4. Backend looks up in AVAILABLE_MODELS
5. DeepSeek API called with model ID

---

## 🧩 Adding New Models

### Step 1: Add to AVAILABLE_MODELS
File: `backend/api/shared_helpers.py` (Line 650+)
```python
{"id": "deepseek-v5", "name": "DeepSeek V5", "provider": "deepseek", 
 "base": "https://api.deepseek.com/v1", "env_key": "LLM_API_KEY"},
```

### Step 2: Add to MODEL_ROUTING (optional)
File: `backend/services/llm_gateway.py` (Line 26+)
```python
"llm_v5": "deepseek-v5",
```

### Step 3: Add to WeChat MODEL_MAP (optional)
File: `backend/routers/wxwork.py` (Line 85+)
```python
"deepseek-v5": "DeepSeek V5",
```

### Result: Automatic!
- ✅ Appears in dropdown
- ✅ Available in `/api/models`
- ✅ Works in all chat endpoints
- ✅ Can be set in WeChat

---

## 🔍 Code Search Map

| Question | File | Lines |
|----------|------|-------|
| Where are models defined? | `backend/api/shared_helpers.py` | 650-654 |
| How to get model list? | `backend/api/chat.py` | 27-35 |
| Frontend dropdown? | `pages/chat.js` | 1-6 |
| R1 thinking display? | `pages/chat.js` | 38-46 |
| WeChat model switch? | `backend/routers/wxwork.py` | 84-119 |
| Smart routing? | `backend/services/llm_gateway.py` | 22-27 |
| Agent model? | `backend/api/agent.py` | 200 |

---

## ⚠️ Known Issues / Notes

1. **V4 Pro Mismatch**: `deepseek-v4-pro` mentioned in WeChat code (line 87) but NOT in AVAILABLE_MODELS
   - May be legacy/testing
   - Won't appear in frontend dropdown
   
2. **R1 Latency**: Users selecting R1 should be warned about 15-30s wait
   - Already handled in UI: progress message shows this

3. **Model Tier vs Model ID**: 
   - `llm_light` tier → always uses V4 Flash
   - `llm_heavy` tier → always uses R1
   - Chat endpoint uses actual model ID (more flexible)

---

## 📋 Checklist: Adding/Removing Models

- [ ] Update `AVAILABLE_MODELS` in shared_helpers.py
- [ ] Update `MODEL_ROUTING` in llm_gateway.py (if using tier system)
- [ ] Update `MODEL_MAP` in wxwork.py (if supporting WeChat)
- [ ] Verify `/api/models` returns new model
- [ ] Test in frontend dropdown
- [ ] Test in WeChat commands
- [ ] Update documentation

---

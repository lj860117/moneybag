# 🤖 DeepSeek Model Configuration — Complete Guide

This directory contains comprehensive documentation about DeepSeek model selection and configuration in the MoneyBag project.

## 📚 Documentation Files

### 1. **DEEPSEEK_MODEL_ANALYSIS.md** (Comprehensive Reference)
   - **What**: Full technical analysis of model configuration
   - **For**: Developers who need complete understanding
   - **Contains**: 
     - All configuration locations
     - Backend model definitions
     - LLM Gateway routing
     - Frontend implementation
     - API endpoints
     - WeChat integration
     - Environment setup
   - **Length**: ~500 lines

### 2. **DEEPSEEK_MODELS_QUICKREF.md** (Quick Lookup)
   - **What**: Quick reference guide with tables and diagrams
   - **For**: Developers looking for specific information
   - **Contains**:
     - Model quick comparison table
     - Where each component is located
     - API endpoints summary
     - Model routing table
     - Configuration checklist
   - **Length**: ~200 lines

### 3. **DEEPSEEK_DATAFLOW.md** (Visual Flows)
   - **What**: ASCII diagrams showing data flow
   - **For**: Visual learners and architects
   - **Contains**:
     - Frontend selection flow
     - Backend routing flow
     - WeChat model switching flow
     - R1 thinking display flow
     - Agent analysis flow
     - Storage locations
   - **Length**: ~300 lines

### 4. **README_DEEPSEEK_MODELS.md** (This file)
   - **What**: Navigation guide and quick answers
   - **For**: Everyone
   - **Contains**: Quick answers to common questions

---

## ❓ Quick Answers

### Q: What models are available?
**A:** Three production models:
- `deepseek-v4-flash` (V4) — Fast, default
- `deepseek-v4-max` (V4 Max) — Advanced analysis
- `deepseek-reasoner` (R1) — Deep thinking (15-30s)

**File:** `backend/api/shared_helpers.py` lines 650-654

---

### Q: Where is the frontend dropdown?
**A:** Chat page shows a dropdown selector
- **File:** `pages/chat.js` lines 1-6
- **Feature:** Saves selection to localStorage
- **Auto-loads:** Models from `/api/models` endpoint

---

### Q: How do I add a new model?
**A:** Three files to update:

1. **`backend/api/shared_helpers.py`** (line 650+)
   ```python
   {"id": "deepseek-v5", "name": "DeepSeek V5", ...}
   ```

2. **`backend/services/llm_gateway.py`** (line 26+) — optional
   ```python
   "llm_v5": "deepseek-v5"
   ```

3. **`backend/routers/wxwork.py`** (line 85+) — for WeChat
   ```python
   "deepseek-v5": "DeepSeek V5"
   ```

---

### Q: How do users switch models?

**In Chat (Frontend):**
- Click dropdown in chat page header
- Selection saves to localStorage
- Model sent in next chat request

**In WeChat (Enterprise):**
- Send: `模型 deepseek-v4-flash`
- Preference stored in user profile
- Auto-used on next message

---

### Q: What's special about R1?
**A:**
- Outputs "thinking process" before answer
- Frontend shows `<details>` block (collapsible)
- Progress message: "R1 深度思考需要 15-30 秒，请耐心等待"
- Detection: `chatModel.includes('reasoner')`

---

### Q: Which model does agent analysis use?
**A:** **V4 Max** by default (`deepseek-v4-max`)
- **File:** `backend/api/agent.py` line 200
- Used for complex decision analysis
- Can be overridden in request

---

### Q: How is model selection converted to API calls?
**A:** Three-step lookup:

1. **Frontend sends:** `model: "deepseek-reasoner"`
2. **Backend looks up** in `AVAILABLE_MODELS`:
   ```python
   for m in AVAILABLE_MODELS:
     if m["id"] == "deepseek-reasoner":
       api_key = env[m["env_key"]]
       api_base = m["base"]
   ```
3. **Calls DeepSeek API:** with model ID

---

### Q: Where's the LLM Gateway?
**A:** `backend/services/llm_gateway.py`

**Purpose:** Smart model routing for non-chat requests
```python
MODEL_ROUTING = {
    "llm_light": "deepseek-v4-flash",  # Fast
    "llm_heavy": "deepseek-reasoner",  # Deep thinking
}
```

---

### Q: What environment variables are needed?
**A:**

| Variable | Required? | Default |
|----------|-----------|---------|
| `LLM_API_KEY` | ✅ Yes | (none) |
| `LLM_MODEL` | ❌ No | deepseek-v4-flash |
| `LLM_API_BASE` | ❌ No | https://api.deepseek.com/v1 |

---

### Q: Is there a "V4 Pro"?
**A:** ⚠️ **Mentioned but not enabled**
- Found in `backend/routers/wxwork.py` line 87
- Not in `AVAILABLE_MODELS` list
- Won't appear in frontend dropdown
- Likely legacy/testing model

---

### Q: How much does it cost?
**A:** Per million tokens (¥, as of 2026-04):
- Cache hit: ¥0.20 (best!)
- Cache miss: ¥2.03
- Output: ¥3.04

---

### Q: What's the difference between model ID and model name?
**A:**
| Type | Example | Usage |
|------|---------|-------|
| **ID** | `deepseek-v4-flash` | Sent to API, used in code |
| **Name** | `DeepSeek V4` | Shown in UI dropdown |
| **Both** | Defined in `AVAILABLE_MODELS` | Single source of truth |

---

## 🔗 Key Locations Map

| Question | File | Lines |
|----------|------|-------|
| Model definitions? | `shared_helpers.py` | 650-654 |
| Model API endpoint? | `chat.py` | 27-35 |
| Chat with model selection? | `chat.py` | 38-75 |
| Stream chat? | `chat.py` | 185-320 |
| Frontend dropdown? | `pages/chat.js` | 1-6 |
| R1 thinking display? | `pages/chat.js` | 38-46 |
| WeChat model switch? | `wxwork.py` | 84-119 |
| Smart routing? | `llm_gateway.py` | 22-27 |
| Agent model? | `agent.py` | 200 |
| Default config? | `config.py` | 109-142 |

---

## 📊 Model Comparison

```
┌──────────────┬──────────────┬──────────────┬──────────────┐
│ Metric       │ V4 (Flash)   │ V4 Max       │ R1           │
├──────────────┼──────────────┼──────────────┼──────────────┤
│ Speed        │ ⚡ 1-2s      │ 🐢 3-5s      │ 🐌 15-30s    │
│ Cost/token   │ 💰 Low       │ 💰💰 Mid     │ 💰💰💰 High  │
│ Quality      │ ⭐⭐⭐       │ ⭐⭐⭐⭐     │ ⭐⭐⭐⭐⭐    │
│ Use Case     │ Chat, fast   │ Analysis     │ Reasoning    │
│ Default      │ ✅ Yes       │ ❌ No        │ ❌ No        │
│ In Agent     │ ❌ No        │ ✅ Yes       │ ❌ No        │
│ Thinking     │ ❌ No        │ ❌ No        │ ✅ Yes       │
└──────────────┴──────────────┴──────────────┴──────────────┘
```

---

## 🎯 Common Tasks

### Task 1: Switch Frontend Default Model
**File:** `pages/chat.js` line 2
```javascript
let chatModel='deepseek-v4-max';  // Changed from deepseek-v4-flash
```

### Task 2: Add a New Model
**Files to edit:** (See "How do I add a new model?" above)

### Task 3: Change Default Model
**File:** `backend/config.py` line 112
```python
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-v4-max")  # New default
```

### Task 4: Make R1 Default in WeChat
**File:** `backend/routers/wxwork.py` line 116
```python
user_model = (user_profile or {}).get("preferredModel", "deepseek-reasoner")  # Changed
```

### Task 5: Add R1-specific UI Hint
**File:** `pages/chat.js` line 5
```javascript
// Already done! See lines 20-21 for R1 detection
```

---

## 🧪 Testing

### Test File
- **Location:** `tests/test_skeleton_m1.py`
- **Coverage:** Model switching validation
- **Request format:** `{"model": "deepseek-chat", ...}`

### Manual Testing

#### Test 1: Check available models
```bash
curl http://localhost:8000/api/models
```

#### Test 2: Chat with specific model
```bash
curl -X POST http://localhost:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{
    "message": "你好",
    "model": "deepseek-v4-max"
  }'
```

#### Test 3: Check R1 thinking
- Use frontend, select R1
- Send message
- Check if `<details>` block shows thinking

---

## 📖 Reading Order

1. **First time?** → Start with `DEEPSEEK_MODELS_QUICKREF.md`
2. **Need visuals?** → Check `DEEPSEEK_DATAFLOW.md`
3. **Deep dive?** → Read `DEEPSEEK_MODEL_ANALYSIS.md`
4. **Quick lookup?** → Use this file (README)

---

## ✅ Checklist: Before Production

- [ ] API key configured (`LLM_API_KEY`)
- [ ] All three models tested
- [ ] Frontend dropdown shows all models
- [ ] WeChat model switching works
- [ ] R1 thinking displays correctly
- [ ] Agent analysis uses V4 Max
- [ ] Cache hit pricing optimized
- [ ] No "V4 Pro" references in code (unless intentional)

---

## 🚀 Next Steps

1. **Understand the flow:** Read `DEEPSEEK_DATAFLOW.md`
2. **Find what you need:** Use tables in `DEEPSEEK_MODELS_QUICKREF.md`
3. **Deep technical details:** Consult `DEEPSEEK_MODEL_ANALYSIS.md`
4. **Quick answers:** Use Q&A above

---

## 📞 Support

For issues related to:
- **Model not showing:** Check `LLM_API_KEY` environment variable
- **R1 hanging:** Normal 15-30s wait, check progress message
- **WeChat not switching:** Verify profile.json is writable
- **Agent slow:** Check if using V4 Max (it's slower by design)

---

**Last Updated:** 2026-05-14
**Version:** 7.1.0
**Documentation Status:** ✅ Complete

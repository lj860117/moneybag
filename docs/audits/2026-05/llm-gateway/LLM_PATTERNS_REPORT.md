# Streaming & Multimodal LLM Call Patterns Report
**Repository**: `/Users/leijiang/WorkBuddy/moneybag-for-claudecode/`
**Date**: 2026-05-15

---

## Executive Summary

The codebase has **two distinct LLM call patterns**:

1. **Streaming Chat (SSE)**: `/api/chat/stream` endpoint → async HTTP streaming
2. **Multimodal Vision (OCR)**: `_do_ocr()` → base64-encoded image + text prompts

Both patterns share a unified gateway (`LLMGateway`) for:
- API key/model selection
- Rate limiting (daily 100 calls, 5-min burst 10 calls)
- Caching (1 hour TTL)
- Token cost tracking
- Error fallback (rules-based engine)

---

## 1. STREAMING CHAT IMPLEMENTATION

### Location
**File**: `backend/api/chat.py`
**Route**: `@router.post("/api/chat/stream")` (lines 178-380)
**Handler**: `async def chat_analysis_stream(req: ChatRequest)`
**Stream Generator**: `async def stream_gen()` (lines 331-377)

### Request Construction (Lines 335-348)

```python
async with client.stream(
    "POST",
    f"{api_base}/chat/completions",
    headers={
        "Authorization": f"Bearer {api_key}", 
        "Content-Type": "application/json"
    },
    json={
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": 1200,
        "temperature": 0.8,
        "stream": True,
    },
) as resp:
```

**Key Details**:
- Uses `httpx.AsyncClient(timeout=120)` for async streaming
- HTTP/2 streaming with `client.stream()` context manager
- OpenAI-compatible `/chat/completions` endpoint
- Fixed params: `max_tokens=1200`, `temperature=0.8`, `stream=True`
- System prompt injected first, then user message

### Model Selection Logic (Lines 311-322)

```python
from services.llm_gateway import LLMGateway
gw = LLMGateway.instance()
api_cfg = gw.get_api_config()
api_key = api_cfg["api_key"]
api_base = api_cfg["api_base"]
model = req.model or api_cfg["model"]

for m in AVAILABLE_MODELS:
    if m["id"] == model:
        api_base = m["base"]
        api_key = os.environ.get(m["env_key"], api_key)
        break
```

**Model Options** (from `shared_helpers.py`):
```python
AVAILABLE_MODELS = [
    {
        "id": "deepseek-v4-flash",
        "name": "DeepSeek V4",
        "provider": "deepseek",
        "base": "https://api.deepseek.com/v1",
        "env_key": "LLM_API_KEY"
    },
    {
        "id": "deepseek-v4-pro",
        "name": "DeepSeek V4 Pro",
        "provider": "deepseek",
        "base": "https://api.deepseek.com/v1",
        "env_key": "LLM_API_KEY"
    },
    {
        "id": "deepseek-reasoner",
        "name": "DeepSeek R1 (深度思考)",
        "provider": "deepseek",
        "base": "https://api.deepseek.com/v1",
        "env_key": "LLM_API_KEY"
    },
]
```

### SSE Chunk Handling (Lines 355-373)

```python
async for line in resp.aiter_lines():
    if not line.startswith("data: "):
        continue
    payload = line[6:]
    if payload.strip() == "[DONE]":
        yield f"data: {json.dumps({'delta': '', 'source': 'ai', 'done': True}, ensure_ascii=False)}\n\n"
        return
    try:
        chunk = json.loads(payload)
        delta_obj = chunk.get("choices", [{}])[0].get("delta", {})
        
        # R1 Model handling: reasoning_content (thinking) → content (answer)
        reasoning = delta_obj.get("reasoning_content", "")
        content = delta_obj.get("content", "")
        
        if reasoning:
            yield f"data: {json.dumps({'delta': reasoning, 'source': 'ai', 'done': False, 'phase': 'thinking'}, ensure_ascii=False)}\n\n"
        elif content:
            yield f"data: {json.dumps({'delta': content, 'source': 'ai', 'done': False, 'phase': 'answering'}, ensure_ascii=False)}\n\n"
    except (json.JSONDecodeError, IndexError, KeyError):
        continue
```

**SSE Format**:
- **Input**: Standard OpenAI streaming format (`data: {...}\n\n`)
- **Terminal**: `[DONE]` marker
- **Output**: Custom JSON with fields:
  - `delta`: text chunk
  - `source`: "ai" or "rules"
  - `done`: boolean
  - `phase`: "thinking" (R1 reasoning) or "answering" (final response)

**R1 Reasoning Support**:
- Detects `reasoning_content` field (DeepSeek R1 only)
- Emits reasoning separately with `phase: 'thinking'`
- Then emits `content` with `phase: 'answering'`
- Both streamed as separate SSE chunks

### Error Handling & Fallback (Lines 324-354, 374-377)

**Pre-check (Line 324)**:
```python
if not api_key or not gw.pre_check():
    reply = "AI 暂时不可用，请稍后再试~" if not api_key else _rule_based_reply(user_msg, market_ctx, portfolio_ctx)
    async def rules_gen():
        yield f"data: {json.dumps({'delta': reply, 'source': 'rules', 'done': True}, ensure_ascii=False)}\n\n"
    return StreamingResponse(rules_gen(), media_type="text/event-stream", ...)
```

**HTTP Error Handling (Lines 350-354)**:
```python
if resp.status_code != 200:
    # LLM 返回错误 → 降级规则引擎
    reply = _rule_based_reply(user_msg, market_ctx, portfolio_ctx)
    yield f"data: {json.dumps({'delta': reply, 'source': 'rules', 'done': True}, ensure_ascii=False)}\n\n"
    return
```

**Exception Handling (Lines 374-377)**:
```python
except Exception as e:
    print(f"[CHAT-STREAM] LLM stream failed: {e}")
    reply = _rule_based_reply(user_msg, market_ctx, portfolio_ctx)
    yield f"data: {json.dumps({'delta': reply, 'source': 'rules', 'done': True}, ensure_ascii=False)}\n\n"
```

**Fallback Chain**:
1. No API key → hardcoded message
2. Rate limit hit → rules engine
3. HTTP error (non-200) → rules engine
4. JSON parse error → skip chunk, continue
5. Exception during stream → rules engine reply

### Response Format (SSE)

```
data: {"delta": "Hello", "source": "ai", "done": false, "phase": "answering"}

data: {"delta": " world", "source": "ai", "done": false, "phase": "answering"}

data: {"delta": "", "source": "ai", "done": true}

```

---

## 2. MULTIMODAL VISION (OCR) IMPLEMENTATION

### Location
**File**: `backend/api/shared_helpers.py`
**Function**: `async def _do_ocr(file_path: Path, content: bytes) -> dict` (lines 471-589)

### Request Construction (Lines 490-530)

```python
import base64
import httpx

b64 = base64.b64encode(content).decode()
mime = "image/jpeg"
if str(file_path).endswith(".png"):
    mime = "image/png"

async with httpx.AsyncClient(timeout=30) as client:
    resp = await client.post(
        f"{api_base}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        json={
            "model": vision_model,
            "messages": [
                {
                    "role": "system",
                    "content": """你是一个金融记录识别助手。请识别截图类型并提取信息。

支持的截图类型：
1. 支付宝/微信消费记录 → 提取: 金额(amount), 商家(merchant), 分类(category:餐饮/交通/购物/娱乐/医疗/教育/其他), 备注(note)
2. 支付宝/微信账单列表 → 提取: 多条记录records[{amount, merchant, date}]
3. 银行卡交易记录 → 提取: 金额(amount), 交易类型(tx_type:转入/转出), 余额(bank_balance), 银行名(bank_name)
4. 基金买入确认 → 提取: 基金名(fund_name), 基金代码(fund_code), 买入金额(amount), 确认份额(shares), 确认净值(nav), 日期(date)
5. 基金赎回确认 → 提取: 基金名(fund_name), 基金代码(fund_code), 赎回份额(shares), 到账金额(amount), 确认净值(nav), 日期(date)
6. 工资条/收入 → 提取: 税后金额(amount), 日期(date)

返回JSON格式:
{
  "screenshot_type": "consumption|bill_list|bank_tx|fund_buy|fund_sell|income",
  "amount": 数值,
  "merchant": "商家名",
  ...
  "confidence": 0.95
}"""
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"}
                        },
                        {
                            "type": "text",
                            "text": "请识别这张截图的信息，返回 JSON。"
                        }
                    ]
                }
            ],
            "max_tokens": 800,
        },
    )
```

### Multimodal Message Format

**Key Structure**:
- `messages[1]["content"]` is an **array** (not string)
- Item 1: `{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}`
- Item 2: `{"type": "text", "text": "..."}` 

**Image Encoding**:
- Base64 encode entire file content
- MIME: `image/jpeg` (default) or `image/png`
- Data URL format: `data:{mime};base64,{b64}`

### Model Configuration (Lines 476-479)

```python
from services.llm_gateway import LLMGateway
gw = LLMGateway.instance()
api_cfg = gw.get_api_config()
api_key = api_cfg["api_key"]
api_base = os.environ.get("LLM_API_BASE", "https://api.openai.com/v1")
vision_model = os.environ.get("LLM_VISION_MODEL", "gpt-4o-mini")
```

**Vision Model Priority**:
1. Environment variable `LLM_VISION_MODEL` (if set)
2. Default: `"gpt-4o-mini"`
3. API Base: `LLM_API_BASE` (default: `https://api.openai.com/v1`)

### Response Parsing (Lines 531-555)

```python
if resp.status_code == 200:
    data = resp.json()
    text = data["choices"][0]["message"]["content"]
    
    import re
    json_match = re.search(r'\{[^}]+\}', text, re.DOTALL)
    if json_match:
        parsed = json.loads(json_match.group())
        result = {
            "amount": float(parsed.get("amount", 0)),
            "merchant": parsed.get("merchant", ""),
            "category": parsed.get("category", "其他"),
            "note": parsed.get("note", ""),
            "source": "llm_vision",
            "screenshot_type": parsed.get("screenshot_type", "consumption"),
            "fund_code": parsed.get("fund_code", ""),
            "fund_name": parsed.get("fund_name", ""),
            "shares": float(parsed.get("shares", 0)),
            "nav": float(parsed.get("nav", 0)),
            "date": parsed.get("date", ""),
            "bank_balance": float(parsed.get("bank_balance", 0)),
            "records": parsed.get("records", []),
            "confidence": float(parsed.get("confidence", 0)),
            "raw": text,
        }
        return result
```

**Extraction Logic**:
- Parse `choices[0].message.content` as raw text
- Regex search: `r'\{[^}]+\}'` (first JSON object)
- Fall back to Tesseract OCR if regex fails

### Fallback Chain (Lines 556-589)

**1. LLM Vision (Lines 481-555)**:
- Requires API key + gateway pre-check pass
- 30-second timeout
- Source: `"llm_vision"`

**2. Local Tesseract (Lines 559-578)**:
```python
from PIL import Image
import pytesseract

img = Image.open(file_path)
text = pytesseract.image_to_string(img, lang="chi_sim+eng")

amounts = re.findall(r'[\d]+\.[\d]{2}', text)
amount = max([float(a) for a in amounts]) if amounts else 0

return {
    "amount": amount,
    "merchant": "",
    "category": "其他",
    "note": text[:100],
    "source": "tesseract",
    "raw": text[:500],
}
```
- Extracts max float from text
- Chinese + English language
- Source: `"tesseract"`

**3. Failure (Lines 582-589)**:
```python
return {
    "amount": 0,
    "merchant": "",
    "category": "其他",
    "note": "OCR 识别失败，请手动输入",
    "source": "none",
    "raw": "",
}
```

### Response Output Schema

```json
{
  "amount": 123.45,
  "merchant": "星巴克",
  "category": "餐饮",
  "note": "早餐",
  "source": "llm_vision|tesseract|none",
  "screenshot_type": "consumption|bill_list|bank_tx|fund_buy|fund_sell|income",
  "fund_code": "110022",
  "fund_name": "易方达消费行业",
  "shares": 100.5,
  "nav": 1.234,
  "date": "2026-05-15",
  "bank_balance": 50000.0,
  "records": [],
  "confidence": 0.95,
  "raw": "full LLM response text"
}
```

---

## 3. UNIFIED GATEWAY (LLMGateway)

### Location
**File**: `backend/services/llm_gateway.py`

### Configuration Retrieval (Lines 295-305)

```python
def get_api_config(self) -> dict:
    """返回 LLM API 配置（key/base/model）"""
    import os as _os
    api_key = _os.environ.get("LLM_API_KEY", "") or _os.environ.get("OPENAI_API_KEY", "")
    api_base = _os.environ.get("LLM_API_BASE", "https://api.deepseek.com/v1")
    model = _os.environ.get("LLM_MODEL", "deepseek-v4-flash")
    return {"api_key": api_key, "api_base": api_base, "model": model}
```

**Priority**:
1. `LLM_API_KEY` (preferred)
2. `OPENAI_API_KEY` (fallback)
3. API Base: `LLM_API_BASE` (default DeepSeek)
4. Model: `LLM_MODEL` (default `deepseek-v4-flash`)

### Pre-check / Rate Limiting (Lines 286-294)

```python
def pre_check(self) -> bool:
    """流式调用前的限流检查，通过返回 True 并消耗一次配额。
    
    用于 streaming 场景：调用者先 pre_check()，再自行发 httpx stream 请求。
    """
    self._check_daily_reset()
    return self._check_limits()

def _check_limits(self) -> bool:
    # 日限
    if self._daily_count >= DAILY_LIMIT:  # 100
        return False
    # 突发限
    now = time.time()
    self._burst_window = [t for t in self._burst_window if now - t < BURST_WINDOW]  # 300s
    if len(self._burst_window) >= BURST_LIMIT:  # 10
        return False
    # 通过
    self._daily_count += 1
    self._burst_window.append(now)
    return True
```

**Limits**:
- Daily: 100 calls per day
- Burst: 10 calls per 5-minute window
- Pre-check is called BEFORE stream request (consumes quota upfront)

### Caching (Lines 69-262)

```python
CACHE_TTL = 3600  # 1 hour
_cache = MemoryCache(default_ttl=CACHE_TTL)

def _cache_key(self, user_id: str, module: str, prompt: str, system: str = "") -> str:
    raw = f"{user_id}:{module}:{system[:100]}:{prompt[:500]}"
    return hashlib.md5(raw.encode()).hexdigest()
```

**Persistence**:
- Disk cache: `.../data/cache/llm_cache.json`
- Dirty write interval: every 5 new entries
- TTL validation on load: ignore expired entries

---

## 4. COMPLETE REQUEST/RESPONSE FLOW

### Streaming Chat Flow

```
┌─ Client Request ─────────────────────┐
│ POST /api/chat/stream                 │
│ {"message": "...", "model": "..."}    │
└───────────────────────────────────────┘
          │
          ▼
┌─ chat.py::chat_analysis_stream ───┐
│ 1. Get LLMGateway instance         │
│ 2. Check API key                   │
│ 3. call pre_check() for quota      │
│ 4. Select model from AVAILABLE_    │
│    MODELS                          │
│ 5. Build system + user messages    │
└────────────────────────────────────┘
          │
          ▼
┌─ httpx AsyncClient ────────────────────────┐
│ POST {api_base}/chat/completions          │
│ Authorization: Bearer {api_key}           │
│ {                                         │
│   "model": "deepseek-v4-flash",          │
│   "messages": [...],                      │
│   "max_tokens": 1200,                     │
│   "temperature": 0.8,                     │
│   "stream": true                          │
│ }                                         │
└────────────────────────────────────────────┘
          │
          ▼
┌─ Server Streaming Response (SSE) ──────┐
│ data: {"choices":[{"delta":{"content"  │
│ :"Hello"}}]}                           │
│                                        │
│ data: {"choices":[{"delta":{"content"  │
│ :" world"}}]}                          │
│                                        │
│ data: [DONE]                           │
└────────────────────────────────────────┘
          │
          ▼
┌─ Chat.py::stream_gen (aiter_lines) ──┐
│ For each line:                       │
│  1. Skip if not "data: "            │
│  2. Check for [DONE] marker         │
│  3. Parse JSON payload              │
│  4. Extract delta + reasoning       │
│  5. Emit custom SSE format:         │
│     data: {delta, source, phase}    │
│  6. Handle exceptions → skip chunk  │
└──────────────────────────────────────┘
          │
          ▼
┌─ Client (Browser) ──────────────────┐
│ EventSource listener                │
│ Receives:                           │
│ {                                   │
│   "delta": "Hello world",           │
│   "source": "ai",                   │
│   "done": true,                     │
│   "phase": "answering"              │
│ }                                   │
└─────────────────────────────────────┘
```

### Multimodal Vision Flow

```
┌─ File Upload ───────────────────────────────┐
│ shared_helpers.py::_do_ocr(file_path,      │
│                           content: bytes)   │
└─────────────────────────────────────────────┘
          │
          ▼
┌─ Gateway Check ─────────────────────┐
│ 1. Get LLMGateway.instance()        │
│ 2. get_api_config()                 │
│ 3. pre_check() for quota            │
│ 4. api_base + vision_model (env)    │
└─────────────────────────────────────┘
          │
          ▼
┌─ Image Processing ──────────────────┐
│ 1. base64.b64encode(content)        │
│ 2. Detect MIME (jpeg or png)        │
│ 3. Create data URL                  │
└─────────────────────────────────────┘
          │
          ▼
┌─ httpx Client ─────────────────────────────────┐
│ POST {api_base}/chat/completions              │
│ Authorization: Bearer {api_key}               │
│ {                                             │
│   "model": "gpt-4o-mini",                     │
│   "messages": [                               │
│     {"role": "system", "content": "..."},    │
│     {"role": "user", "content": [            │
│       {"type": "image_url", "image_url": {   │
│         "url": "data:image/jpeg;base64,..."  │
│       }},                                     │
│       {"type": "text", "text": "识别..."}    │
│     ]}                                        │
│   ],                                          │
│   "max_tokens": 800                          │
│ }                                             │
└─────────────────────────────────────────────────┘
          │
          ▼
┌─ LLM Response ──────────────────────────┐
│ {                                       │
│   "choices": [{                         │
│     "message": {                        │
│       "content": "{...json...}"         │
│     }                                   │
│   }]                                    │
│ }                                       │
└─────────────────────────────────────────┘
          │
          ▼
┌─ Response Parsing ──────────────────────┐
│ 1. Extract choices[0].message.content   │
│ 2. Regex find JSON: r'\{[^}]+\}'       │
│ 3. Parse extracted JSON                │
│ 4. Map fields → result dict            │
│ 5. Return with source: "llm_vision"    │
└─────────────────────────────────────────┘
          │
          ▼
┌─ Fallback: Tesseract ────────────────┐
│ If LLM fails:                        │
│ 1. PIL.Image.open(file_path)         │
│ 2. pytesseract (chi_sim+eng)         │
│ 3. Regex extract amounts             │
│ 4. Return with source: "tesseract"   │
└──────────────────────────────────────┘
          │
          ▼
┌─ Result ────────────────────────────────┐
│ {                                       │
│   "amount": 123.45,                     │
│   "merchant": "...",                    │
│   "source": "llm_vision|tesseract|none"│
│   ...                                   │
│ }                                       │
└─────────────────────────────────────────┘
```

---

## 5. KEY DESIGN PATTERNS FOR GATEWAY INTEGRATION

### Pattern 1: Config Retrieval
```python
from services.llm_gateway import LLMGateway
gw = LLMGateway.instance()
api_cfg = gw.get_api_config()
api_key = api_cfg["api_key"]
api_base = api_cfg["api_base"]
model = api_cfg["model"]
```

### Pattern 2: Streaming (with pre-check)
```python
if not api_key or not gw.pre_check():
    # fallback
    return

async with httpx.AsyncClient(timeout=120) as client:
    async with client.stream("POST", f"{api_base}/chat/completions", ...) as resp:
        async for line in resp.aiter_lines():
            # process SSE
```

### Pattern 3: Multimodal
```python
b64 = base64.b64encode(content).decode()
resp = await client.post(
    f"{api_base}/chat/completions",
    json={
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {"type": "text", "text": "..."}
                ]
            }
        ]
    }
)
```

### Pattern 4: R1 Reasoning Detection
```python
delta_obj = chunk.get("choices", [{}])[0].get("delta", {})
reasoning = delta_obj.get("reasoning_content", "")
content = delta_obj.get("content", "")

if reasoning:
    # emit thinking phase
elif content:
    # emit answering phase
```

### Pattern 5: Error Fallback
```python
try:
    # LLM call
except Exception:
    # Use rules engine
    reply = _rule_based_reply(msg, market_ctx, portfolio_ctx)
    yield f"data: {json.dumps({'delta': reply, 'source': 'rules', 'done': True})}\n\n"
```

---

## 6. ENVIRONMENT VARIABLES

**For Streaming**:
- `LLM_API_KEY` or `OPENAI_API_KEY` (required)
- `LLM_API_BASE` (default: `https://api.deepseek.com/v1`)
- `LLM_MODEL` (default: `deepseek-v4-flash`)

**For Vision**:
- `LLM_API_BASE` (default: `https://api.openai.com/v1`)
- `LLM_VISION_MODEL` (default: `gpt-4o-mini`)

**Gateway**:
- `DATA_DIR` (default: `./data`) — cache/usage persistence

---

## 7. SUMMARY TABLE

| Aspect | Streaming Chat | Multimodal Vision |
|--------|-----------------|-------------------|
| **Endpoint** | POST /api/chat/stream | (internal) _do_ocr() |
| **HTTP Method** | Stream GET/POST | POST (non-streaming) |
| **Client** | httpx.AsyncClient.stream() | httpx.AsyncClient.post() |
| **Timeout** | 120s | 30s |
| **Model** | deepseek-v4-flash/pro/r1 | gpt-4o-mini (configurable) |
| **Max Tokens** | 1200 | 800 |
| **Temperature** | 0.8 | (default) |
| **Request Format** | text messages | image_url + text |
| **Response Type** | SSE chunks | JSON (single response) |
| **R1 Support** | Yes (reasoning_content) | N/A |
| **Fallback** | Rules engine | Tesseract OCR |
| **Source Tag** | "ai" or "rules" | "llm_vision", "tesseract", or "none" |
| **Rate Limit** | pre_check() (100/day, 10/5m) | pre_check() (100/day, 10/5m) |
| **Cache** | 1 hour TTL (on LLM responses) | None (direct call) |

---

## 8. INTEGRATION CHECKLIST FOR GATEWAY

- [ ] **Config Centralization**: Both streaming and vision retrieve from `LLMGateway.get_api_config()`
- [ ] **Rate Limiting**: Both call `pre_check()` before HTTP request
- [ ] **Model Routing**: Streaming has explicit model list; vision uses env var
- [ ] **Error Handling**: Streaming → rules fallback; vision → Tesseract fallback
- [ ] **Timeout**: Streaming 120s, vision 30s (tunable)
- [ ] **SSE Format**: Streaming uses custom JSON wrapper; vision returns raw dict
- [ ] **Token Tracking**: (Not implemented in stream/vision yet; gateway has infrastructure)
- [ ] **Caching**: Only on gateway's `call_sync()`, not streaming/vision
- [ ] **Reasoning Support**: Streaming detects `reasoning_content` for R1 models


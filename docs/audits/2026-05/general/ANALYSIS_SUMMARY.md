# LLM Streaming & Multimodal Patterns - Analysis Summary

**Date**: 2026-05-15  
**Repository**: `/Users/leijiang/WorkBuddy/moneybag-for-claudecode/`  
**Analysis Scope**: Backend LLM call patterns for gateway integration planning

---

## Executive Summary

✅ **Analysis Complete** — Comprehensive documentation of two distinct LLM patterns:

### Pattern 1: Streaming Chat (SSE)
- **Location**: `backend/api/chat.py` lines 178-380
- **Endpoint**: `POST /api/chat/stream`
- **Transport**: HTTP/2 streaming with custom SSE wrapper
- **Models**: DeepSeek V4 (flash/pro), R1 (reasoning)
- **Key Feature**: R1 reasoning phase detection (`reasoning_content` vs `content`)

### Pattern 2: Multimodal Vision (OCR)
- **Location**: `backend/api/shared_helpers.py` lines 471-589
- **Function**: `async _do_ocr(file_path, content)`
- **Transport**: HTTP POST (non-streaming)
- **Model**: GPT-4o-mini (vision)
- **Key Feature**: Financial screenshot recognition with fallback to Tesseract

### Unified Gateway
- **Location**: `backend/services/llm_gateway.py`
- **Features**: Config, rate limiting, caching, token tracking, fallback
- **Pattern**: Singleton instance used by both streaming and vision
- **Quota**: 100 calls/day, 10/5min burst

---

## 📊 Key Findings

### Streaming Pattern (chat.py:178-380)

**Request Construction**:
```
POST {api_base}/chat/completions
Authorization: Bearer {api_key}

{
  "model": "deepseek-v4-flash|pro|reasoner",
  "messages": [
    {"role": "system", "content": "{system_prompt}"},
    {"role": "user", "content": "{user_message}"}
  ],
  "max_tokens": 1200,
  "temperature": 0.8,
  "stream": true
}
```

**Response Processing** (lines 355-373):
- Reads SSE chunks: `aiter_lines()` on streaming response
- Detects `[DONE]` marker for stream termination
- Extracts `choices[0].delta` object
- Special handling: `reasoning_content` (R1 thinking) + `content` (answer)
- Emits custom JSON with `phase: "thinking"` or `"answering"`

**Error Handling** (fallback chain):
1. No API key → hardcoded message
2. Rate limit hit → rules engine
3. HTTP error → rules engine
4. JSON parse → skip chunk, continue
5. Exception → rules engine

**Custom SSE Output** (to client):
```
data: {"delta": "Hello", "source": "ai", "done": false, "phase": "answering"}
data: {"delta": " world", "source": "ai", "done": false, "phase": "answering"}
data: {"delta": "", "source": "ai", "done": true}
```

---

### Multimodal Pattern (shared_helpers.py:471-589)

**Request Construction**:
```
POST {api_base}/chat/completions
Authorization: Bearer {api_key}

{
  "model": "gpt-4o-mini",
  "messages": [
    {"role": "system", "content": "{financial_ocr_prompt}"},
    {"role": "user", "content": [
      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,{encoded}"}},
      {"type": "text", "text": "识别这张截图的信息，返回 JSON。"}
    ]}
  ],
  "max_tokens": 800
}
```

**Key Differences**:
- User message `content` is an **array** (not string)
- Image encoded as data URL with base64
- MIME type detected: `image/jpeg` or `image/png`
- Non-streaming response

**Response Parsing** (lines 531-555):
- Extracts `choices[0].message.content` (raw text)
- Regex search: `r'\{[^}]+\}'` to find JSON object
- Parses JSON and maps to output schema
- Source tagged: `"llm_vision"`

**Fallback Chain**:
1. LLM vision (30s timeout)
2. Tesseract OCR (local)
3. Return `{amount: 0, source: "none"}`

**Output Schema**:
```python
{
  "amount": 123.45,
  "merchant": "星巴克",
  "category": "餐饮",
  "source": "llm_vision|tesseract|none",
  "screenshot_type": "consumption|bill_list|...",
  "confidence": 0.95,
  "raw": "{full_response_text}"
}
```

---

## 🔐 Gateway Integration Points

### Common Pattern (both use)
```python
from services.llm_gateway import LLMGateway
gw = LLMGateway.instance()

# 1. Get API config (unified source)
api_cfg = gw.get_api_config()
api_key = api_cfg["api_key"]
api_base = api_cfg["api_base"]

# 2. Pre-check quota (before HTTP request)
if not gw.pre_check():
    return fallback  # 100/day or 10/5min limit hit
```

### Model Selection
- **Streaming**: Loop through `AVAILABLE_MODELS` list (lines 318-322)
- **Vision**: Env var `LLM_VISION_MODEL` (line 479, default `gpt-4o-mini`)

### Rate Limits (gateway level)
- Daily: 100 calls
- Burst: 10 calls / 5-minute window
- Pre-check consumes quota upfront

### Caching
- Only on `gateway.call_sync()` (synchronous calls)
- NOT on streaming or vision (manual HTTP)
- TTL: 1 hour, disk-persisted

---

## 📁 Deliverables Created

### 1. LLM_PATTERNS_REPORT.md
- **Content**: Full technical breakdown (800+ lines)
- **Includes**:
  - Complete code snippets with line numbers
  - Request/response format documentation
  - Model configuration table
  - Error handling strategies
  - Environment variables reference
  - Integration checklist

### 2. LLM_PATTERNS_DIAGRAM.txt
- **Content**: ASCII architecture diagrams
- **Includes**:
  - Flow diagrams for both patterns
  - Unified gateway layer visualization
  - Request/response signature comparison
  - Integration quick reference checklist

### 3. LLM_GATEWAY_INTEGRATION_GUIDE.md
- **Content**: Quick-start implementation guide
- **Includes**:
  - Minimal working examples (streaming & vision)
  - Current implementations reference
  - Configuration reference
  - Rate limits & quotas
  - Error handling strategy
  - Testing checklist
  - Common patterns (5 key patterns)
  - Phase roadmap for future work

---

## 🎯 Gateway Integration Readiness

### Current Status ✅
- [x] Streaming uses gateway (config, pre-check)
- [x] Vision uses gateway (config, pre-check)
- [x] Unified rate limiting (both call pre_check)
- [x] Fallback patterns documented

### Gaps Identified
- [ ] Token tracking for streaming (infrastructure exists in gateway, not used in stream)
- [ ] Token tracking for vision (infrastructure exists, not used in vision)
- [ ] Caching for streaming (only cache non-stream calls)
- [ ] Caching for vision (no caching currently)
- [ ] Unified error reporting (both have custom fallbacks)

### Roadmap Recommendations

**Phase 1 (Consolidate)**: ✅ Complete
- Document existing patterns
- Identify unified gateway point
- Create integration reference

**Phase 2 (Enhance)**:
- Add token counting to streaming
- Add token counting to vision
- Record per-user/per-module metrics

**Phase 3 (Optimize)**:
- Implement streaming response caching
- Implement vision result caching
- Enhance error reporting

**Phase 4 (Expand)**:
- Add text-to-speech streaming
- Add embeddings endpoint
- Add function calling support

---

## 📊 Comparison Table

| Aspect | Streaming | Multimodal |
|--------|-----------|-----------|
| **File** | chat.py | shared_helpers.py |
| **Lines** | 178-380 | 471-589 |
| **HTTP Method** | Streaming POST | Regular POST |
| **Client** | httpx.stream() | httpx.post() |
| **Timeout** | 120s | 30s |
| **Model** | deepseek-* | gpt-4o-mini |
| **Max Tokens** | 1200 | 800 |
| **Temperature** | 0.8 | default |
| **Request Format** | text messages | image_url + text |
| **Response Type** | SSE chunks | JSON (single) |
| **R1 Support** | ✅ (reasoning_content) | ❌ |
| **Fallback** | Rules engine | Tesseract OCR |
| **Gateway Use** | get_api_config, pre_check | get_api_config, pre_check |
| **Rate Limit** | ✅ (100/day, 10/5m) | ✅ (100/day, 10/5m) |
| **Caching** | ❌ (manual stream) | ❌ (manual POST) |
| **Token Tracking** | ❌ (not implemented) | ❌ (not implemented) |

---

## 🚀 Usage Example

### To Add New Streaming Endpoint
```python
from services.llm_gateway import LLMGateway
import httpx

@router.post("/api/new/stream")
async def new_streaming(req: Request):
    gw = LLMGateway.instance()
    cfg = gw.get_api_config()
    
    if not cfg["api_key"] or not gw.pre_check():
        return StreamingResponse(fallback_gen(), media_type="text/event-stream")
    
    async def stream_gen():
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream("POST", f"{cfg['api_base']}/chat/completions",
                    headers={"Authorization": f"Bearer {cfg['api_key']}"},
                    json={
                        "model": cfg["model"],
                        "messages": [{"role": "user", "content": req.message}],
                        "stream": True,
                    }) as resp:
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            payload = line[6:]
                            if payload.strip() == "[DONE]":
                                yield f"data: {json.dumps({'done': True})}\n\n"
                                return
                            try:
                                chunk = json.loads(payload)
                                delta = chunk["choices"][0]["delta"].get("content", "")
                                if delta:
                                    yield f"data: {json.dumps({'delta': delta})}\n\n"
                            except:
                                continue
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
    
    return StreamingResponse(stream_gen(), media_type="text/event-stream")
```

### To Add New Vision Endpoint
```python
import base64

async def process_image(file_bytes: bytes, filename: str):
    gw = LLMGateway.instance()
    cfg = gw.get_api_config()
    
    if not cfg["api_key"] or not gw.pre_check():
        return {"error": "Unavailable"}
    
    mime = "image/png" if filename.endswith(".png") else "image/jpeg"
    b64 = base64.b64encode(file_bytes).decode()
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{cfg['api_base']}/chat/completions",
                headers={"Authorization": f"Bearer {cfg['api_key']}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                            {"type": "text", "text": "Analyze this..."}
                        ]
                    }],
                    "max_tokens": 800,
                })
        
        if resp.status_code == 200:
            return {"content": resp.json()["choices"][0]["message"]["content"]}
        else:
            return {"error": "LLM failed", "fallback": "tesseract"}
    except Exception as e:
        return {"error": str(e)}
```

---

## 📚 Documentation Files

All files are saved in project root:

1. **LLM_PATTERNS_REPORT.md** — Technical deep-dive
2. **LLM_PATTERNS_DIAGRAM.txt** — Architecture diagrams
3. **LLM_GATEWAY_INTEGRATION_GUIDE.md** — Implementation guide
4. **ANALYSIS_SUMMARY.md** — This file

---

## ✨ Key Insights

1. **Unified Gateway**: Both patterns already use `LLMGateway.instance()` for config
2. **Rate Limiting**: Implemented at gateway level via `pre_check()`
3. **R1 Support**: Streaming endpoint correctly handles reasoning_content phase
4. **Fallback Strategy**: Different but both use gateway as primary
5. **Error Handling**: Pattern-specific (rules for streaming, Tesseract for vision)
6. **Token Tracking**: Infrastructure exists in gateway but not used by streaming/vision
7. **Caching**: Only on gateway.call_sync(), not on manual HTTP patterns

---

**Status**: ✅ **Complete & Ready for Integration Planning**


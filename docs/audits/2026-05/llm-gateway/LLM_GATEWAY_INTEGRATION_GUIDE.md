# LLM Gateway Integration Guide

**Created**: 2026-05-15  
**Purpose**: Comprehensive reference for streaming and multimodal LLM patterns  
**Status**: Complete Analysis with Code References

---

## 📚 Documentation Index

This analysis includes three documents:

1. **LLM_PATTERNS_REPORT.md** — Full technical report with code excerpts
   - Lines 1-800+: Complete breakdown of both patterns
   - Includes exact line numbers and full code snippets
   - Environment variables and configuration details
   - Summary comparison table

2. **LLM_PATTERNS_DIAGRAM.txt** — Visual ASCII architecture diagrams
   - Flow diagrams for both patterns
   - Message format comparisons
   - Request/response signatures
   - Integration quick reference

3. **LLM_GATEWAY_INTEGRATION_GUIDE.md** (this file) — Quick-start implementation guide

---

## 🚀 Quick Start

### For Adding Streaming Endpoints

**Minimal example**:
```python
from services.llm_gateway import LLMGateway
import httpx

@router.post("/api/my/stream")
async def my_streaming_endpoint(req: Request):
    gw = LLMGateway.instance()
    api_cfg = gw.get_api_config()
    
    if not api_cfg["api_key"] or not gw.pre_check():
        # Fallback to rules engine
        return StreamingResponse(fallback_gen(), media_type="text/event-stream")
    
    async def stream_gen():
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST",
                    f"{api_cfg['api_base']}/chat/completions",
                    headers={"Authorization": f"Bearer {api_cfg['api_key']}"},
                    json={
                        "model": api_cfg["model"],
                        "messages": [
                            {"role": "system", "content": "You are..."},
                            {"role": "user", "content": req.message},
                        ],
                        "stream": True,
                    },
                ) as resp:
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

### For Adding Vision/Multimodal Endpoints

**Minimal example**:
```python
from services.llm_gateway import LLMGateway
import httpx
import base64

async def process_image(file_content: bytes, filename: str):
    gw = LLMGateway.instance()
    api_cfg = gw.get_api_config()
    
    if not api_cfg["api_key"] or not gw.pre_check():
        return {"error": "LLM unavailable"}
    
    # Determine MIME type
    mime = "image/png" if filename.endswith(".png") else "image/jpeg"
    b64 = base64.b64encode(file_content).decode()
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{api_cfg['api_base']}/chat/completions",
                headers={"Authorization": f"Bearer {api_cfg['api_key']}"},
                json={
                    "model": os.environ.get("LLM_VISION_MODEL", "gpt-4o-mini"),
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are an image analyzer..."
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
                                    "text": "Analyze this image..."
                                }
                            ]
                        }
                    ],
                    "max_tokens": 800,
                },
            )
        
        if resp.status_code == 200:
            text = resp.json()["choices"][0]["message"]["content"]
            # Parse JSON from response if needed
            return {"raw": text, "source": "llm_vision"}
        else:
            # Fallback to local OCR, etc.
            return {"error": "LLM failed", "fallback": "tesseract"}
    
    except Exception as e:
        return {"error": str(e), "fallback": "none"}
```

---

## 📋 Current Implementations

### Streaming: `/api/chat/stream`
- **File**: `backend/api/chat.py` (lines 178-380)
- **Models**: `deepseek-v4-flash`, `deepseek-v4-pro`, `deepseek-reasoner`
- **Features**: 
  - R1 reasoning detection (thinking phase)
  - Rules engine fallback
  - Custom SSE format with source/phase tags
- **Testing**: Call with `POST /api/chat/stream {"message": "What's the market outlook?"}`

### Vision: `_do_ocr()`
- **File**: `backend/api/shared_helpers.py` (lines 471-589)
- **Model**: `gpt-4o-mini` (configurable via `LLM_VISION_MODEL`)
- **Features**:
  - Financial screenshot recognition (6 types)
  - Tesseract fallback
  - JSON extraction from response
- **Testing**: Upload financial screenshot, get parsed OCR result

---

## 🔧 Configuration Reference

### Environment Variables

**Required**:
```bash
LLM_API_KEY=sk-xxx...
```

**Optional with Defaults**:
```bash
# For streaming (defaults shown)
LLM_API_BASE=https://api.deepseek.com/v1
LLM_MODEL=deepseek-v4-flash

# For vision (defaults shown)
LLM_VISION_MODEL=gpt-4o-mini

# Gateway
DATA_DIR=./data
```

### Model Endpoints

| Model | Provider | Endpoint | Supports |
|-------|----------|----------|----------|
| deepseek-v4-flash | DeepSeek | /chat/completions | Streaming, reasoning detection |
| deepseek-v4-pro | DeepSeek | /chat/completions | Streaming, reasoning detection |
| deepseek-reasoner | DeepSeek (R1) | /chat/completions | Streaming, **deep reasoning**, vision |
| gpt-4o-mini | OpenAI | /chat/completions | Vision (multimodal), fast |

---

## 📊 Rate Limits & Quotas

All LLM calls consume quota via `pre_check()`:

- **Daily limit**: 100 calls/day
- **Burst limit**: 10 calls per 5-minute window
- **Pre-check timing**: Call BEFORE streaming/HTTP request
- **Quota consumption**: On `pre_check()`, not on response

Example:
```python
# This consumes 1 call from daily quota
if not gw.pre_check():
    return "Rate limited"  # 100 calls already made today

# Make streaming request (quota already consumed)
async with client.stream(...) as resp:
    ...
```

---

## 🛡️ Error Handling Strategy

### Streaming Errors
```
API Key missing  →  Hardcoded message
Rate limit       →  Rules engine reply
HTTP error       →  Rules engine reply
JSON parse fail  →  Skip chunk, continue
Exception        →  Rules engine reply
```

### Multimodal Errors
```
API Key missing  →  Return error
Rate limit       →  Return error
HTTP error       →  Fallback to Tesseract
JSON regex fail  →  Fallback to Tesseract
Tesseract fail   →  Return {amount: 0, source: "none"}
```

---

## 🔄 Testing Checklist

- [ ] Streaming response arrives in chunks (not all at once)
- [ ] SSE format is valid (`data: {...}\n\n`)
- [ ] R1 models emit `reasoning_content` before `content`
- [ ] Fallback works when API key is missing
- [ ] Rate limiting triggers after 100 daily calls
- [ ] Multimodal detects image MIME type correctly
- [ ] Vision OCR extracts JSON from wrapped response
- [ ] Tesseract fallback triggers on LLM failure

---

## 📝 Common Patterns

### Pattern 1: Get Gateway Config
```python
from services.llm_gateway import LLMGateway
gw = LLMGateway.instance()
api_cfg = gw.get_api_config()  # {api_key, api_base, model}
```

### Pattern 2: Check Quota Before Streaming
```python
if not gw.pre_check():
    # 100 daily calls exhausted or 10 calls in last 5 min
    return fallback_response()
```

### Pattern 3: Stream with Timeout
```python
async with httpx.AsyncClient(timeout=120) as client:
    async with client.stream("POST", url, ...) as resp:
        async for line in resp.aiter_lines():
            # Process each SSE line
```

### Pattern 4: Detect R1 Reasoning
```python
delta = chunk["choices"][0]["delta"]
reasoning = delta.get("reasoning_content", "")
content = delta.get("content", "")

if reasoning:
    print("Thinking phase:", reasoning)
elif content:
    print("Answer phase:", content)
```

### Pattern 5: Parse Vision Response
```python
text = resp.json()["choices"][0]["message"]["content"]
json_match = re.search(r'\{[^}]+\}', text, re.DOTALL)
if json_match:
    parsed = json.loads(json_match.group())
    # Extract fields...
```

---

## 🎯 Next Steps for Gateway Integration

### Phase 1: Unify Existing Calls ✅
- [x] Document streaming pattern (`chat.py`)
- [x] Document multimodal pattern (`shared_helpers.py`)
- [x] Identify gateway single point (LLMGateway.instance())

### Phase 2: Token Tracking (Future)
- [ ] Add token counting to streaming responses
- [ ] Integrate token cost calculation
- [ ] Record per-user/per-module metrics

### Phase 3: Caching Extension (Future)
- [ ] Cache streaming responses (currently only call_sync)
- [ ] Cache vision OCR results
- [ ] Implement cache key strategy

### Phase 4: New Patterns (Future)
- [ ] Text-to-speech (streaming audio)
- [ ] Embeddings (non-streaming)
- [ ] Function calling (structured)

---

## 📞 Support & References

- **Full Report**: See `LLM_PATTERNS_REPORT.md` for exact line numbers and code
- **Architecture Diagram**: See `LLM_PATTERNS_DIAGRAM.txt` for visual flows
- **Gateway Source**: `backend/services/llm_gateway.py`
- **Streaming Source**: `backend/api/chat.py` (lines 178-380)
- **Vision Source**: `backend/api/shared_helpers.py` (lines 471-589)

---

## 🗂️ File Structure

```
backend/
├── api/
│   ├── chat.py                    ← Streaming endpoint
│   └── shared_helpers.py          ← Vision/OCR endpoint
├── services/
│   └── llm_gateway.py             ← Unified config & rate limiting
└── models/
    └── schemas.py                 ← ChatRequest, etc.

docs/
├── LLM_PATTERNS_REPORT.md         ← Full technical analysis
├── LLM_PATTERNS_DIAGRAM.txt       ← Architecture diagrams
└── LLM_GATEWAY_INTEGRATION_GUIDE.md ← This file
```

---

**Last Updated**: 2026-05-15  
**Maintainer**: Planning Team  
**Status**: Active Reference

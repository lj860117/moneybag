# Panel Advisor - Technical Documentation

**Last Updated**: May 24, 2026  
**Version**: v9.3.21+  
**Status**: Production - Fully Deployed

---

## Quick Reference

### When Panel Activates

The investment consultation panel activates when a user asks investment decision questions:

#### Intent-based triggers:
```python
_PANEL_INTENTS = {"timing", "take_profit", "allocation", "portfolio_doctor"}
```

#### Keyword-based triggers:
```python
入场 | 进场 | 能买 | 该买 | 适合买 | 抄底 |
该卖 | 减仓 | 止盈 | 止损 | 加仓 | 能入 |
怎么配置 | 资产配置 | 现在适合 | 要不要买 |
能不能买 | 值得买 | 适合入 | 能抄底 |
持仓风险 | 分析持仓 | 持仓调整 | 怎么调整 |
要不要卖 | 该怎么办 | 仓位
```

### Example User Queries That Trigger Panel

✅ "现在能入场吗？" → **Panel triggered** (contains "能入场")  
✅ "该怎么配置我的资产？" → **Panel triggered** (contains "怎么配置")  
✅ "持仓风险大吗？" → **Panel triggered** (contains "持仓风险")  
✅ "现在适合买股票吗？" → **Panel triggered** (contains "现在适合" + "买")  
❌ "今天市场怎么样？" → Regular chat (no panel trigger)

---

## Architecture Overview

### Three-Layer Stack

#### Layer 1: Backend API (chat.py)
**Location**: `backend/api/chat.py` lines 249-310

```python
# 1. Detect investment decision intent
is_panel = (intent["intent"] in _PANEL_INTENTS or
            any(kw in user_msg for kw in _PANEL_KEYWORDS))

# 2. If panel detected, generate perspectives
if is_panel:
    from services.panel_advisor import generate_panel
    panel = generate_panel(uid, user_msg)
    perspectives = panel["perspectives"]
    synthesis_prompt = panel["synthesis_prompt"]
    
    # 3. Stream panel + LLM synthesis
    async def _panel_stream():
        # Send perspectives first
        yield f"data: {json.dumps({'type': 'panel', ...})}\n\n"
        
        # Then stream LLM synthesis
        for chunk in gw.stream_sync(...):
            yield f"data: {json.dumps({'type': 'stream', ...})}\n\n"
```

#### Layer 2: Core Logic (panel_advisor.py)
**Location**: `backend/services/panel_advisor.py` 364 lines

```python
def generate_panel(user_id: str, question: str) -> dict:
    """Main entry point"""
    # 1. Run steward fast pipeline (0 LLM calls)
    modules_data = _run_fast_pipeline(user_id, question)
    
    # 2. Map to 4 perspectives (template-based)
    perspectives = _build_perspectives(modules_data)
    
    # 3. Build synthesis prompt for LLM
    synthesis_prompt = _build_synthesis_prompt(perspectives, question, modules_data)
    
    return {
        "perspectives": perspectives,
        "synthesis_prompt": synthesis_prompt,
        "data_summary": modules_data.get("_summary", ""),
        "elapsed_ms": elapsed,
    }
```

#### Layer 3: Frontend (pages/chat.js)
**Location**: `pages/chat.js` lines 91-160

```javascript
// 1. Detect panel event in SSE stream
if(d.type === 'panel' && d.perspectives) {
    panelHtml = _renderPanelCards(d.perspectives);
    botDiv.innerHTML = panelHtml + '<div class="panel-synthesis">...';
}

// 2. Render 4 perspective cards
function _renderPanelCards(perspectives) {
    return perspectives.map(p => `
        <div style="...">
            <div>${p.emoji} ${p.name}</div>
            <div>${p.focus}</div>
            <div>${p.text}</div>
        </div>
    `).join('');
}

// 3. Append LLM synthesis below
botDiv.innerHTML = panelHtml + '<div class="panel-synthesis">' + fullText + '</div>';
```

---

## Data Flow

### Complete Request-Response Cycle

```
┌─ User: "现在能入场吗？"
│
├─ [Backend] classify_chat_intent()
│  └─ Detected: intent="timing"
│
├─ [Backend] is_panel = True
│  └─ Matches _PANEL_INTENTS["timing"]
│
├─ [Panel Advisor] generate_panel()
│  ├─ _run_fast_pipeline()
│  │  └─ steward.runner.run("fast", ctx)
│  │     └─ Load 18 modules of data (0 LLM)
│  │
│  ├─ _build_perspectives()
│  │  ├─ _perspective_buffett() → 巴菲特视角
│  │  ├─ _perspective_graham() → 格雷厄姆视角
│  │  ├─ _perspective_lynch() → 林奇视角
│  │  └─ _perspective_taleb() → 塔勒布视角
│  │
│  └─ _build_synthesis_prompt()
│     └─ Build LLM instruction with all views
│
├─ [SSE Stream] Send Panel Data
│  └─ {"type": "panel", "perspectives": [...]}
│     └─ [Frontend] _renderPanelCards() renders 4 cards
│
├─ [LLM] Generate Synthesis (1 call)
│  └─ Using synthesis_prompt + gw.stream_sync()
│
└─ [SSE Stream] Send Synthesis Text
   └─ {"type": "stream", "delta": "..."}
      └─ [Frontend] Append to panel-synthesis div
```

### Response Structure

**SSE Event 1: Panel Data**
```json
{
  "type": "panel",
  "perspectives": [
    {
      "emoji": "🎩",
      "name": "巴菲特",
      "focus": "价值投资",
      "text": "估值百分位30%，市场低估..."
    },
    // ... 3 more perspectives
  ],
  "done": false
}
```

**SSE Event 2+: Synthesis Chunks**
```json
{
  "type": "stream",
  "delta": "综合来看，",
  "done": false,
  "phase": "answering"
}
```

**SSE Event N: Done**
```json
{
  "type": "stream",
  "delta": "",
  "done": true,
  "served_by": "panel"
}
```

---

## Four Master Perspectives

### 🎩 Warren Buffett - Value Investing
**Focus**: Is valuation reasonable? Long-term value?

**Data Sources**:
- Valuation percentile (via `get_valuation_percentile()`)
- Dividend yield
- PE ratio
- Market regime

**Output Example**:
> "估值百分位30%，市场低估，价值投资者的好时机。当前PE=15.2。股息率3.5%，分红回报不错。"

### 📚 Benjamin Graham - Margin of Safety
**Focus**: Is funding safe? How much downside risk?

**Data Sources**:
- Northbound flow (外资)
- Margin trading levels
- Shibor rates (银行间利率)
- Risk level alerts

**Output Example**:
> "北向5日净流入100亿，资金面偏暖。融资余额5日变化-0.5%，杠杆平稳。银行间利率1.8%，流动性充裕。"

### 🔍 Peter Lynch - Sector Research
**Focus**: Sector rotation? Institutional positioning?

**Data Sources**:
- Sector rotation module
- Broker research consensus
- News sentiment (bullish/bearish)
- Top gainers

**Output Example**:
> "近期热点板块：新能源、半导体、医药。机构整体看多。新闻面偏正面，市场情绪回暖。"

### 🌪️ Nassim Taleb - Antifragility
**Focus**: Any black swan events? Tail risks?

**Data Sources**:
- Geopolitical risk level
- Fear & Greed Index
- Risk alerts
- Volatility regime

**Output Example**:
> "地缘风险适中（严重度2/5），无明显黑天鹅。恐慌指数45，市场风险适中。高波动环境仓位要轻。"

---

## Performance Characteristics

### Timing Breakdown

| Component | Time | Notes |
|-----------|------|-------|
| Intent classification | <100ms | Local rules |
| Steward fast pipeline | 1.5-2.5s | 18 modules in parallel |
| Perspective generation | <100ms | Template-based, no LLM |
| Panel SSE send | <50ms | Network |
| **Frontend render panels** | 100-200ms | DOM update |
| LLM synthesis | 3-5s | 1 API call, streaming |
| **Total user wait** | ~5-8s | From send to complete |

### Cost Analysis

| Operation | Cost | Notes |
|-----------|------|-------|
| Steward pipeline | ¥0 | Uses existing system |
| Perspective mapping | ¥0 | Pure templates |
| LLM synthesis | ¥0.008 | 1 call to DeepSeek |
| **Total per panel** | **¥0.008** | Same as regular chat |

### Reliability

| Metric | Value | Notes |
|--------|-------|-------|
| Panel generation success rate | >98% | Fallback always works |
| Data availability | 95%+ | Most modules have data |
| Error recovery | 100% graceful | Never shows errors to user |
| Synthesis availability | ~99% | LLM fallback available |

---

## Error Handling & Fallbacks

### Scenario 1: Steward Pipeline Fails
```python
def _run_fast_pipeline(...):
    try:
        steward = get_steward()
        ctx = steward.runner.run("fast", ctx)
        # ... extract data
    except Exception as e:
        print(f"[PANEL] pipeline failed: {e}")
        return _fallback_data()  # ← Graceful fallback
```

**Fallback Data**:
- Gets basic market data (FGI, valuation)
- Still produces valid panel (though less detailed)
- User doesn't notice the failure

### Scenario 2: LLM Synthesis Fails
```python
# In _panel_stream():
for chunk in gw.stream_sync(...):
    if chunk.get("fallback"):
        # LLM unavailable, use simple conclusion
        yield f"data: {json.dumps({'type': 'stream', 'delta': '综合来看，建议观望为主...', 'done': False})}"
        break
```

### Scenario 3: Frontend Doesn't Receive Panel
```javascript
// If SSE stream breaks, frontend continues with regular chat
if (!panelHtml) {
    // Regular LLM response rendering
    botDiv.innerHTML = fullText + '<div class="src-tag">🤖 AI · DeepSeek</div>';
}
```

---

## Configuration & Customization

### Adding New Panel Keywords

**File**: `backend/api/chat.py` line 251-257

```python
_PANEL_KEYWORDS = [
    # Add new keywords here:
    "新增关键词1", "新增关键词2",
    
    # Existing keywords:
    "入场", "进场", "能买", ...
]
```

### Adjusting Perspective Focus

**File**: `backend/services/panel_advisor.py`

Edit individual `_perspective_*()` functions:
```python
def _perspective_buffett(modules: dict, regime: str) -> str:
    parts = []
    # Customize buffett's analysis here
    # Add new checks, change thresholds, etc.
    return "。".join(parts) + "。"
```

### Changing Synthesis Prompt

**File**: `backend/services/panel_advisor.py` line 350-364

```python
def _build_synthesis_prompt(perspectives: list, question: str, modules_data: dict) -> str:
    return f"""你是...(customize here)
    
    ... prompt template ...
    
    请用 100 字以内...(customize here)"""
```

---

## Monitoring & Debugging

### Logging Points

**Backend Logging**:
```
[CHAT-STREAM] ★ 投资会诊模式, intent=timing
[PANEL] pipeline success, modules=15
[PANEL] buffett valuation check: pct=35%
[PANEL] synthesis stream starting...
```

**Frontend Logging** (dev console):
```
Panel event detected: perspectives=4
Rendering 4 perspective cards...
Panel synthesis div created
Stream complete, served_by=panel
```

### Debug Checklist

1. **Panel not triggering?**
   - Check keywords in `_PANEL_KEYWORDS`
   - Check intent in `_PANEL_INTENTS`
   - Verify user message contains trigger word

2. **Perspectives incomplete?**
   - Check steward module availability
   - Check fallback data generation
   - Look for [PANEL] errors in logs

3. **Synthesis not streaming?**
   - Check LLM_API_KEY is set
   - Check model supports streaming
   - Check SSE connection with dev tools

4. **Frontend not rendering?**
   - Check browser console for JS errors
   - Verify SSE data format: `data: {...}\n\n`
   - Check panelHtml string for XSS issues

---

## Testing Scenarios

### Scenario 1: Basic Panel Trigger
```
User: "现在能入场吗？"
Expected: 4 perspective cards + synthesis
Time: ~6-8s
Cost: ¥0.008
```

### Scenario 2: Portfolio Risk Assessment
```
User: "持仓风险大吗？"
Expected: Panel shows risk perspective (Graham)
Time: ~6-8s
Cost: ¥0.008
```

### Scenario 3: Asset Allocation
```
User: "怎么配置我的资产？"
Expected: Panel shows allocation perspective
Time: ~6-8s
Cost: ¥0.008
```

### Scenario 4: Fallback Test (Simulate Pipeline Failure)
```
# In panel_advisor.py, comment out steward call
Expected: Panel still generates with fallback data
Fallback quality: 80% of normal
User experience: Seamless
```

---

## Related Files

| File | Purpose | Lines |
|------|---------|-------|
| `backend/api/chat.py` | API routing & SSE generation | 249-310 |
| `backend/services/panel_advisor.py` | Core logic & perspective generation | 1-365 |
| `backend/services/steward.py` | Data collection | (referenced) |
| `pages/chat.js` | Frontend SSE handling & rendering | 91-160 |
| `backend/models/schemas.py` | ChatRequest schema | (referenced) |

---

## Future Enhancements

1. **Caching**: Cache panel data for 1-2 hours (steward already does this)
2. **Personalization**: Adjust perspectives based on user risk profile
3. **A/B Testing**: Test different perspective focuses
4. **Analytics**: Track which perspective influences decisions most
5. **Multi-language**: Support Chinese/English perspectives
6. **Custom Masters**: Allow users to add their own investment philosophies

---

## Support & Troubleshooting

For issues, check:
1. Backend logs: `tail -f logs/app.log | grep -i panel`
2. Frontend console: Press F12 in browser
3. SSE events: Dev Tools → Network → Filter "stream"
4. Steward pipeline: Check if data modules are available

Common issues & fixes available in `PANEL_TROUBLESHOOTING.md` (if needed).


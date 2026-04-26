"""
钱袋子 — FastAPI 后端 V5.0（模块化重构）
路由入口 + 中间件配置，业务逻辑在 services/ 中
"""
import os
import sys
import json
import time
from pathlib import Path
from datetime import datetime

# 确保能导入同级模块
sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse

# ---- 从 services 导入业务逻辑 ----
from config import DATA_DIR
from models.schemas import ChatRequest
from services.data_layer import (
    get_fund_nav, get_fear_greed_index, get_valuation_percentile,
    get_technical_indicators, get_fund_news, get_market_news,
    get_macro_calendar, get_northbound_flow, get_margin_trading,
    get_treasury_yield, get_shibor, get_dividend_yield,
    get_news_sentiment_score,
)
from services.persistence import load_user, save_user

# ---- FastAPI 应用 ----
from config import APP_VERSION as _APP_VERSION
app = FastAPI(title="钱袋子 API", version=_APP_VERSION)

app.add_middleware(GZipMiddleware, minimum_size=1000)  # >1KB 自动 gzip
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- API 路由 ----

@app.get("/api/glossary")
def get_glossary_api(term: str = None):
    """FIX 2026-04-19 D4: 金融术语词典（小白可用性）
    - 不传 term → 返回全部词典
    - 传 term（如 ?term=PE）→ 返回单个解释
    """
    from services.glossary import get_glossary, explain_term
    if term:
        return {"term": term, **explain_term(term)}
    return {"glossary": get_glossary()}


@app.get("/api/market-status")
def get_market_status():
    """FIX 2026-04-19 F2: 市场状态 API，前端显示『今天是否交易日』
    让用户看到数据来源时间戳，知道为什么盘中数据可能是昨天的
    """
    from services.signal_scout import is_trading_day
    from datetime import datetime, time as dt_time
    now = datetime.now()
    trading_day = is_trading_day(now)

    # 判断交易时段
    t = now.time()
    session = "closed"
    if trading_day:
        if dt_time(9, 30) <= t < dt_time(11, 30):
            session = "morning"
        elif dt_time(13, 0) <= t < dt_time(15, 0):
            session = "afternoon"
        elif t < dt_time(9, 30):
            session = "pre_open"
        elif dt_time(11, 30) <= t < dt_time(13, 0):
            session = "lunch"
        else:
            session = "after_close"

    return {
        "is_trading_day": trading_day,
        "session": session,
        "now": now.isoformat(),
        "weekday": now.strftime("%A"),
        "message": {
            "closed": "📅 非交易日，数据为最近一次收盘快照",
            "pre_open": "🌅 开盘前（9:30 前），数据为昨日收盘",
            "morning": "🟢 上午交易中（9:30-11:30）",
            "lunch": "☕ 午休（11:30-13:00），数据为上午收盘",
            "afternoon": "🟢 下午交易中（13:00-15:00）",
            "after_close": "🌙 已收盘，数据为今日收盘",
        }.get(session, "市场状态未知"),
    }


@app.get("/api/health")
def health():
    from config import APP_VERSION
    from services.llm_gateway import LLMGateway
    budget = LLMGateway.instance().check_budget()
    # Phase 0: API Key 状态检查
    keys_status = {}
    keys_status["deepseek"] = "ok" if os.environ.get("LLM_API_KEY") else "missing"
    keys_status["tushare"] = "ok" if os.environ.get("TUSHARE_TOKEN") else "missing"
    return {
        "status": "ok",
        "time": datetime.now().isoformat(),
        "version": APP_VERSION,
        "llm_usage": budget,
        "keys_status": keys_status,
    }


# ---- 企业微信路由（已拆分到 routers/wxwork.py）----
from routers.wxwork import router as wxwork_router
app.include_router(wxwork_router)
# send_markdown 仍需在 cron 等处使用
from services.wxwork_push import is_configured as wxwork_configured, send_markdown


# ---- 共享辅助函数（从 shared_helpers 导入，供 P3 路由使用）----
from api.shared_helpers import (
    _build_market_context, _build_portfolio_context,
    _build_system_prompt, _load_prompt_template, _rule_based_reply,
    classify_chat_intent, _INTENT_RULES,
    AVAILABLE_MODELS, _cached_file_response, _CACHE_RULES,
)

@app.get("/api/models")
def list_models():
    """返回可用模型列表（只返回有 API key 的模型）"""
    result = []
    for m in AVAILABLE_MODELS:
        key = os.environ.get(m["env_key"], "")
        if key:
            result.append({"id": m["id"], "name": m["name"], "provider": m["provider"]})
    return {"models": result, "default": "deepseek-chat"}


@app.get("/api/nav/all")
def get_all_nav():
    """获取所有推荐基金的净值"""
    codes = ["110020", "050025", "217022", "000216", "008114"]
    result = {}
    for code in codes:
        result[code] = get_fund_nav(code)
    return result


@app.get("/api/nav/{code}")
def get_nav(code: str):
    """获取单只基金净值"""
    return get_fund_nav(code)



# ---- API: 综合市场仪表盘（一次性拉全部数据）----

@app.get("/api/dashboard")
async def get_market_dashboard():
    """V4.5 综合市场仪表盘 — 三级降级: 新鲜缓存 → 过期缓存 → 5s超时实时 → 空壳"""
    import asyncio

    # === 第1级: 新鲜缓存（秒出） ===
    stale_result = None  # 保存过期缓存，备用
    try:
        from services.precomputed_cache import get_precomputed, PRECOMPUTED_DIR
        pc_factors = get_precomputed("factors")
        pc_fgi = get_precomputed("fear_greed")
        pc_val = get_precomputed("valuation")

        if pc_factors and pc_fgi and pc_val:
            return {
                "valuation": pc_val,
                "fear_greed": pc_fgi,
                "northbound": pc_factors.get("northbound", {}),
                "margin": pc_factors.get("margin", {}),
                "shibor": pc_factors.get("shibor", {}),
                "from_cache": True,
                "cache_note": "凌晨预计算数据，盘中每30分钟刷新",
            }

        # === 第2级: 过期缓存也读出来备用 ===
        # 直接读磁盘文件，跳过 TTL 检查
        import json as _json
        from pathlib import Path
        from datetime import date as _date, timedelta as _td
        for days_ago in range(4):  # 最多找 4 天前
            d = _date.today() - _td(days=days_ago)
            factors_f = PRECOMPUTED_DIR / f"factors_{d}.json"
            fgi_f = PRECOMPUTED_DIR / f"fear_greed_{d}.json"
            val_f = PRECOMPUTED_DIR / f"valuation_{d}.json"
            if factors_f.exists() and fgi_f.exists() and val_f.exists():
                try:
                    pf = _json.loads(factors_f.read_text(encoding="utf-8")).get("data", {})
                    pfgi = _json.loads(fgi_f.read_text(encoding="utf-8")).get("data", {})
                    pval = _json.loads(val_f.read_text(encoding="utf-8")).get("data", {})
                    stale_result = {
                        "valuation": pval,
                        "fear_greed": pfgi,
                        "northbound": pf.get("northbound", {}),
                        "margin": pf.get("margin", {}),
                        "shibor": pf.get("shibor", {}),
                        "from_cache": True,
                        "stale": True,
                        "cache_note": f"数据截至 {d}（缓存已过期，优先展示历史数据）",
                    }
                    break
                except Exception:
                    pass
    except Exception:
        pass

    # === 第3级: 实时拉取（5s 超时，不是之前的 30s） ===
    try:
        loop = asyncio.get_event_loop()

        async def _fetch_realtime():
            return await asyncio.gather(
                loop.run_in_executor(None, get_valuation_percentile),
                loop.run_in_executor(None, get_fear_greed_index),
                loop.run_in_executor(None, get_technical_indicators),
                loop.run_in_executor(None, lambda: get_market_news(8)),
                loop.run_in_executor(None, get_macro_calendar),
                loop.run_in_executor(None, get_northbound_flow),
                loop.run_in_executor(None, get_margin_trading),
                loop.run_in_executor(None, get_treasury_yield),
                loop.run_in_executor(None, get_shibor),
                loop.run_in_executor(None, get_dividend_yield),
                loop.run_in_executor(None, get_news_sentiment_score),
            )

        (val, fgi_data, tech, news, macro,
         northbound, margin, treasury, shibor_data, dividend, sentiment
        ) = await asyncio.wait_for(_fetch_realtime(), timeout=8.0)

        return {
            "valuation": val,
            "fearGreed": fgi_data,
            "technical": tech,
            "news": news,
            "macro": macro,
            "northbound": northbound,
            "margin": margin,
            "treasury": treasury,
            "shibor": shibor_data,
            "dividend": dividend,
            "sentiment": sentiment,
            "version": "4.5",
            "updatedAt": datetime.now().isoformat(),
        }
    except (asyncio.TimeoutError, Exception) as e:
        print(f"[DASHBOARD] 实时拉取超时/失败: {e}")

    # === 第4级: 返回过期缓存（总好过空白） ===
    if stale_result:
        return stale_result

    # === 第5级: 空壳（绝不转圈） ===
    return {"valuation": {}, "fear_greed": {}, "from_cache": False, "error": "数据源暂不可用"}



# ---- 多用户 Profile 管理（独立 router）----
from routers.profiles import router as profiles_router, _load_profiles, _save_profiles
app.include_router(profiles_router)

# ---- M1 W2: 第一批拆分 Router（10 文件，~60 路由）----
from api.factors import router as factors_router
from api.macro import router as macro_router
from api.global_market import router as global_market_router
from api.policy import router as policy_router
from api.market_factors import router as market_factors_router
from api.alt_data import router as alt_data_router
from api.quant import router as quant_router
from api.broker import router as broker_router
from api.analysis import router as analysis_router
from api.scenario import router as scenario_router
app.include_router(factors_router)
app.include_router(macro_router)
app.include_router(global_market_router)
app.include_router(policy_router)
app.include_router(market_factors_router)
app.include_router(alt_data_router)
app.include_router(quant_router)
app.include_router(broker_router)
app.include_router(analysis_router)
app.include_router(scenario_router)

# ---- M1 W2: 第二批拆分 Router（5 文件，~80 路由）----
from api.holdings import router as holdings_router
from api.portfolio import router as portfolio_router
from api.signals import router as signals_router
from api.news import router as news_router
from api.user import router as user_router
app.include_router(holdings_router)
app.include_router(portfolio_router)
app.include_router(signals_router)
app.include_router(news_router)
app.include_router(user_router)


# ---- DeepSeek 智能增强 API ----
from services.ds_enhance import (
    analyze_idle_cash, comment_single_stock, comment_single_fund,
    generate_daily_focus, diagnose_user_assets,
)

@app.get("/api/ai-comment/stock")
def ai_comment_stock(code: str, name: str = "", score: float = 0,
                     pe: float = 0, roe: float = 0, gross_margin: float = 0):
    """单只股票 AI 点评（按需，用户点击时调用）"""
    comment = comment_single_stock(code, name, {
        "score": score, "pe": pe, "roe": roe, "gross_margin": gross_margin,
    })
    return {"code": code, "name": name, "comment": comment}


@app.get("/api/ai-comment/fund")
def ai_comment_fund(code: str, name: str = "", score: float = 0,
                    fee: str = "", r3m: float = None, r6m: float = None,
                    r1y: float = None, r3y: float = None):
    """单只基金 AI 点评（按需，用户点击时调用）"""
    returns = {}
    if r3m is not None: returns["3m"] = r3m
    if r6m is not None: returns["6m"] = r6m
    if r1y is not None: returns["1y"] = r1y
    if r3y is not None: returns["3y"] = r3y
    comment = comment_single_fund(code, name, {
        "score": score, "fee": fee, "returns": returns,
    })
    return {"code": code, "name": name, "comment": comment}

@app.post("/api/assets/advice")
def get_asset_advice(req: dict):
    """存款智能建议 — DeepSeek 分析闲置资金配置"""
    return analyze_idle_cash(
        cash_amount=float(req.get("cashAmount", 0)),
        monthly_expense=float(req.get("monthlyExpense", 0)),
        risk_profile=req.get("riskProfile", "稳健型"),
    )


@app.post("/api/ds/asset-diagnosis")
def get_asset_diagnosis(req: dict):
    """AI 资产诊断 — DeepSeek 全量分析用户资产结构"""
    user_id = req.get("userId", "")
    if not user_id:
        raise HTTPException(400, "userId required")
    return diagnose_user_assets(user_id)

@app.get("/api/daily-focus")
def get_daily_focus():
    """首页'今日关注' — DeepSeek 个性化生成"""
    market_ctx = _build_market_context()
    return generate_daily_focus(market_ctx)


# ---- API: AI 对话分析 ----

@app.post("/api/chat")
async def chat_analysis(req: ChatRequest):
    """AI 对话分析 — 回答用户的理财问题"""
    user_msg = req.message.strip()
    if not user_msg:
        raise HTTPException(400, "消息不能为空")

    # Phase 0 (3.6): 意图预分类（规则优先，不调 LLM）
    intent = classify_chat_intent(user_msg)

    # 构建市场上下文
    market_ctx = _build_market_context()
    uid = req.userId or "default"
    portfolio_ctx = _build_portfolio_context(req.portfolio, user_id=uid) if req.portfolio else _build_portfolio_context(user_id=uid)

    # 多用户记忆注入（B1修复：get_memory_summary→build_memory_summary）
    if req.userId:
        try:
            from services.agent_memory import build_memory_summary, record_emotion
            # 2026-04-19 M6: 先记录本次情绪，再 build（这样当前情绪会影响下次的 summary）
            record_emotion(req.userId, user_msg)
            mem = build_memory_summary(req.userId)
            if mem:
                portfolio_ctx += f"\n\n## 用户记忆\n{mem}"
        except Exception as e:
            print(f"[CHAT] memory inject failed: {e}")

    # 尝试调用 LLM（支持 OpenAI 兼容 API）
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
    api_base = os.environ.get("LLM_API_BASE", "https://api.deepseek.com/v1")
    model = req.model or os.environ.get("LLM_MODEL", "deepseek-chat")
    # 根据模型查找对应 base URL
    for m in AVAILABLE_MODELS:
        if m["id"] == model:
            api_base = m["base"]
            api_key = os.environ.get(m["env_key"], api_key)
            break
    print(f"[CHAT] api_key={'SET' if api_key else 'EMPTY'}, base={api_base}, model={model}")

    if api_key:
        try:
            import httpx
            print(f"[CHAT] Calling DeepSeek API... intent={intent}")
            system_prompt = _build_system_prompt(market_ctx, portfolio_ctx)
            # Phase 0: 注入意图提示（帮 LLM 聚焦回答方向）
            if intent.get("intent") != "general":
                system_prompt += f"\n\n## 用户意图预判\n用户可能在问关于「{intent['intent']}」的问题，请优先从这个角度回答。"

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{api_base}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_msg},
                        ],
                        "max_tokens": 800,
                        "temperature": 0.7,
                    },
                )
                print(f"[CHAT] DeepSeek status={resp.status_code}")
                if resp.status_code == 200:
                    data = resp.json()
                    reply = data["choices"][0]["message"]["content"]
                    print(f"[CHAT] LLM reply OK, len={len(reply)}")
                    # Phase 0 (3.7): 记录决策日志
                    try:
                        from services.decision_log import log_decision
                        log_decision(user_id=uid, question=user_msg, advice=reply, source="chat", intent=intent.get("intent", "general"), model=model)
                    except Exception as e:
                        print(f"[CHAT] Decision log failed: {e}")

                    # 2026-04-19 V7.4.2: 后台异步提炼记忆（不阻塞用户响应）
                    if req.userId and len(user_msg) > 10 and len(reply) > 30:
                        try:
                            import threading
                            from services.agent_memory import auto_extract_insight
                            t = threading.Thread(
                                target=auto_extract_insight,
                                args=(req.userId, user_msg, reply),
                                daemon=True,
                            )
                            t.start()
                        except Exception as e:
                            print(f"[CHAT] auto_extract 启动失败: {e}")

                    return {"reply": reply, "source": "ai"}
                else:
                    print(f"[CHAT] DeepSeek error: {resp.text[:200]}")
        except Exception as e:
            import traceback
            print(f"[CHAT] LLM call failed: {e}")
            traceback.print_exc()

    # 降级：规则引擎回答
    reply = _rule_based_reply(user_msg, market_ctx, portfolio_ctx)
    # Phase 0 (3.7): 规则引擎也记录
    try:
        from services.decision_log import log_decision
        log_decision(user_id=uid, question=user_msg, advice=reply, source="rules", intent=intent.get("intent", "general"), model="rules")
    except Exception:
        pass
    return {"reply": reply, "source": "rules"}


# ---- API: 决策日志查询（Phase 0 新增）----

@app.get("/api/decision-log")
def get_decision_log_api(userId: str = "", days: int = 7):
    """查询最近 N 天的决策日志"""
    from services.decision_log import get_decisions
    return {"decisions": get_decisions(userId or None, days)}

@app.get("/api/decision-log/stats")
def get_decision_stats_api(userId: str = "", days: int = 30):
    """决策统计（V8 复盘预览）"""
    from services.decision_log import get_decision_stats
    return get_decision_stats(userId or None, days)


# ---- API: AI 对话分析（SSE 流式）----

@app.post("/api/chat/stream")
async def chat_analysis_stream(req: ChatRequest):
    """AI 对话分析 — SSE 流式响应，逐字输出"""
    user_msg = req.message.strip()
    if not user_msg:
        raise HTTPException(400, "消息不能为空")

    market_ctx = _build_market_context()
    uid = req.userId or "default"
    portfolio_ctx = _build_portfolio_context(req.portfolio, user_id=uid) if req.portfolio else _build_portfolio_context(user_id=uid)

    # 多用户记忆注入
    if req.userId:
        try:
            from services.agent_memory import build_memory_summary, record_emotion
            # 2026-04-19 M6: 同步记录情绪
            record_emotion(req.userId, user_msg)
            mem = build_memory_summary(req.userId)
            if mem:
                portfolio_ctx += f"\n\n## 用户记忆\n{mem}"
        except Exception as e:
            print(f"[CHAT-STREAM] memory inject failed: {e}")

    # 个股/基金新闻注入（检测到用户提到具体公司/基金时，拉最新新闻给 DS）
    try:
        from services.steward import _extract_stock_name, _extract_fund_name
        stock_name, stock_code = _extract_stock_name(user_msg)
        fund_name, fund_code = _extract_fund_name(user_msg)

        if stock_code:
            # 个股新闻
            import akshare as ak
            df = ak.stock_news_em(symbol=stock_code)
            if df is not None and len(df) > 0:
                title_col = [c for c in df.columns if "标题" in c or "title" in c.lower() or "新闻" in c]
                if title_col:
                    titles = df[title_col[0]].head(8).tolist()
                    news_text = "\n".join([f"- {t}" for t in titles])
                    market_ctx += f"\n\n## {stock_name}({stock_code})最新新闻\n{news_text}"
                    print(f"[CHAT] 注入 {stock_name} 个股新闻 {len(titles)} 条")
        elif fund_code and fund_code != "余额宝":
            # 基金新闻
            from services.data_layer import get_fund_news
            fund_news = get_fund_news(fund_code, 8)
            valid_news = [n for n in fund_news if n.get("title") and "加载中" not in n.get("title", "")]
            if valid_news:
                news_text = "\n".join([f"- {n['title']}" for n in valid_news[:8]])
                market_ctx += f"\n\n## {fund_name}({fund_code})最新新闻\n{news_text}"
                print(f"[CHAT] 注入 {fund_name} 基金新闻 {len(valid_news)} 条")
    except Exception as e:
        print(f"[CHAT] news inject: {e}")

    # W8: 注入 steward 最近决策上下文（让 DS 知道管家最近分析了什么）
    try:
        from services.agent_memory import get_context
        last_ctx = get_context(uid)
        if last_ctx.get("last_analysis"):
            portfolio_ctx += f"\n\n## 管家最近分析结论\n{last_ctx['last_analysis'][:300]}"
            if last_ctx.get("market_phase"):
                portfolio_ctx += f"\n市场阶段: {last_ctx['market_phase']}"
    except Exception as e:
        print(f"[CHAT-STREAM] steward ctx inject failed: {e}")

    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
    api_base = os.environ.get("LLM_API_BASE", "https://api.deepseek.com/v1")
    model = req.model or os.environ.get("LLM_MODEL", "deepseek-chat")
    for m in AVAILABLE_MODELS:
        if m["id"] == model:
            api_base = m["base"]
            api_key = os.environ.get(m["env_key"], api_key)
            break
    print(f"[CHAT-STREAM] api_key={'SET' if api_key else 'EMPTY'}, base={api_base}, model={model}")

    if not api_key:
        # 无 API key → 规则引擎降级，一次性返回
        reply = _rule_based_reply(user_msg, market_ctx, portfolio_ctx)
        async def rules_gen():
            yield f"data: {json.dumps({'delta': reply, 'source': 'rules', 'done': True}, ensure_ascii=False)}\n\n"
        return StreamingResponse(rules_gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    system_prompt = _build_system_prompt(market_ctx, portfolio_ctx)

    async def stream_gen():
        import httpx
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST",
                    f"{api_base}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_msg},
                        ],
                        "max_tokens": 800,
                        "temperature": 0.7,
                        "stream": True,
                    },
                ) as resp:
                    if resp.status_code != 200:
                        # LLM 返回错误 → 降级规则引擎
                        reply = _rule_based_reply(user_msg, market_ctx, portfolio_ctx)
                        yield f"data: {json.dumps({'delta': reply, 'source': 'rules', 'done': True}, ensure_ascii=False)}\n\n"
                        return
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
                            # R1 模型：先输出 reasoning_content（思考过程），再输出 content（正式回答）
                            reasoning = delta_obj.get("reasoning_content", "")
                            content = delta_obj.get("content", "")
                            if reasoning:
                                yield f"data: {json.dumps({'delta': reasoning, 'source': 'ai', 'done': False, 'phase': 'thinking'}, ensure_ascii=False)}\n\n"
                            elif content:
                                yield f"data: {json.dumps({'delta': content, 'source': 'ai', 'done': False, 'phase': 'answering'}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, IndexError, KeyError):
                            continue
        except Exception as e:
            print(f"[CHAT-STREAM] LLM stream failed: {e}")
            reply = _rule_based_reply(user_msg, market_ctx, portfolio_ctx)
            yield f"data: {json.dumps({'delta': reply, 'source': 'rules', 'done': True}, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---- 静态文件服务（部署时前后端一体）----
FRONTEND_DIR = Path(__file__).resolve().parent.parent  # moneybag/

@app.get("/")
def serve_index():
    return _cached_file_response(FRONTEND_DIR / "index.html")

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="frontend")

# ---- V4 管家 Steward + Regime API ----
from services.steward import get_steward
from services.regime_engine import classify as classify_regime

@app.post("/api/steward/ask")
def steward_ask(req: dict):
    """管家决策 — 完整 Pipeline 流程"""
    user_id = req.get("userId", "")
    if not user_id:
        raise HTTPException(400, "userId required")
    question = req.get("question", "综合分析")
    pipeline = req.get("pipeline", None)
    steward = get_steward()
    return steward.ask(user_id, question, pipeline_override=pipeline)

@app.get("/api/steward/briefing")
def steward_briefing(userId: str = ""):
    """管家每日简报（快速版，0 次 LLM）"""
    if not userId:
        raise HTTPException(400, "userId required")
    steward = get_steward()
    return steward.briefing(userId)

@app.get("/api/steward/review")
def steward_review(userId: str = ""):
    """管家收盘复盘（完整版，含体检）"""
    if not userId:
        raise HTTPException(400, "userId required")
    steward = get_steward()
    return steward.review(userId)

@app.get("/api/regime")
def get_regime():
    """获取当前市场状态（4 类分类）"""
    return classify_regime()


# ---- LLM Gateway 用量 API ----
from services.llm_gateway import llm_usage

@app.get("/api/llm-usage")
def get_llm_usage(userId: str = ""):
    """LLM 调用用量统计（按用户×模块）"""
    return llm_usage(userId)


# ---- W9: 周报 API ----
from services.weekly_report import generate as generate_weekly, get_history as get_weekly_history

@app.get("/api/weekly-report")
def weekly_report_api(userId: str = "", weeks_ago: int = 0):
    """生成/获取周报"""
    if not userId:
        return {"error": "userId required"}
    return generate_weekly(userId, weeks_ago)

@app.get("/api/weekly-report/history")
def weekly_report_history(userId: str = "", limit: int = 4):
    """获取历史周报列表"""
    if not userId:
        return {"reports": []}
    return {"reports": get_weekly_history(userId, limit)}


# ---- W10: 一键备份 API ----
@app.post("/api/admin/backup")
def create_backup():
    """一键备份全部用户数据"""
    import shutil
    backup_name = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    backup_dir = DATA_DIR.parent / "backups" / backup_name
    try:
        shutil.copytree(DATA_DIR, backup_dir)
        return {"status": "ok", "path": str(backup_dir), "name": backup_name}
    except Exception as e:
        return {"status": "error", "msg": str(e)}


# ---- Agent 决策引擎 API ----
from services.agent_memory import (
    get_preferences, save_preferences, get_decisions, add_decision,
    get_rules, add_rule, remove_rule, get_context, build_memory_summary,
    # 2026-04-19 新增：画像/情绪/铁律
    get_profile, save_profile, get_emotion_summary, record_emotion,
    get_ironies, add_irony, remove_irony,
    # 2026-04-19 新增：生活事件（生日/纪念日）
    get_life_events, save_life_events, add_life_event, remove_life_event,
    get_upcoming_events,
    # 2026-04-19 V7.4.2 新增：自动记忆积累（待审队列）
    get_pending_insights, approve_insight, reject_insight,
)
from services.agent_engine import run_analysis_cycle, save_signal_file

@app.get("/api/agent/memory/{user_id}")
def get_agent_memory(user_id: str):
    """获取用户记忆摘要（含画像/情绪/铁律）"""
    return {
        "preferences": get_preferences(user_id),
        "profile": get_profile(user_id),
        "emotion": get_emotion_summary(user_id),
        "ironies": get_ironies(user_id),
        "decisions": get_decisions(user_id, limit=10),
        "rules": get_rules(user_id),
        "context": get_context(user_id),
        "summary": build_memory_summary(user_id),
    }

@app.post("/api/agent/preferences")
def save_agent_preferences(req: dict):
    """保存用户偏好"""
    user_id = req.pop("userId", "")
    if not user_id:
        raise HTTPException(400, "userId required")
    return save_preferences(user_id, req)

@app.post("/api/agent/rules")
def add_agent_rule(req: dict):
    """添加自定义预警规则"""
    user_id = req.pop("userId", "")
    if not user_id:
        raise HTTPException(400, "userId required")
    return add_rule(user_id, req)

@app.delete("/api/agent/rules/{user_id}/{rule_id}")
def delete_agent_rule(user_id: str, rule_id: str):
    """删除自定义规则"""
    return {"ok": remove_rule(user_id, rule_id)}

# ========== 2026-04-19 新增：画像 / 铁律 / 情绪 ==========

@app.get("/api/agent/profile/{user_id}")
def api_get_profile(user_id: str):
    """读用户画像"""
    return get_profile(user_id)


@app.post("/api/agent/profile")
def api_save_profile(req: dict):
    """保存用户画像（增量合并）"""
    user_id = req.pop("userId", "")
    if not user_id:
        raise HTTPException(400, "userId required")
    return save_profile(user_id, req)


@app.get("/api/agent/ironies/{user_id}")
def api_get_ironies(user_id: str):
    """读用户铁律列表"""
    return {"ironies": get_ironies(user_id)}


@app.post("/api/agent/ironies")
def api_add_irony(req: dict):
    """添加铁律（user 告诉过 AI 的不可违反事实）"""
    user_id = req.get("userId", "")
    text = req.get("text", "").strip()
    source = req.get("source", "manual")
    if not user_id or not text:
        raise HTTPException(400, "userId and text required")
    return add_irony(user_id, text, source=source)


@app.delete("/api/agent/ironies/{user_id}/{iron_id}")
def api_remove_irony(user_id: str, iron_id: str):
    """删除铁律"""
    return {"ok": remove_irony(user_id, iron_id)}


@app.get("/api/agent/emotion/{user_id}")
def api_get_emotion(user_id: str):
    """读用户情绪摘要"""
    return get_emotion_summary(user_id) or {"dominant": None, "sample_size": 0}


@app.get("/api/agent/life-events/{user_id}")
def api_get_life_events(user_id: str):
    """读生活事件列表 + 未来 30 天即将到的"""
    return {
        "events": get_life_events(user_id),
        "upcoming_30d": get_upcoming_events(user_id, days_ahead=30),
    }


@app.post("/api/agent/life-events")
def api_add_life_event(req: dict):
    """添加一个生活事件"""
    user_id = req.get("userId", "")
    title = req.get("title", "").strip()
    date_str = req.get("date", "").strip()
    if not user_id or not title or not date_str:
        raise HTTPException(400, "userId, title, date required")
    return add_life_event(
        user_id,
        title=title,
        date_str=date_str,
        is_lunar=bool(req.get("is_lunar", False)),
        repeat_yearly=bool(req.get("repeat_yearly", True)),
        remind_days_before=int(req.get("remind_days_before", 7)),
    )


@app.delete("/api/agent/life-events/{user_id}/{event_id}")
def api_remove_life_event(user_id: str, event_id: str):
    """删除生活事件"""
    return {"ok": remove_life_event(user_id, event_id)}


# ========== 2026-04-19 V7.4.2 新增：自动记忆积累（待审队列）==========

@app.get("/api/agent/pending-insights/{user_id}")
def api_get_pending(user_id: str):
    """读待审记忆队列（前端红点提示用）"""
    items = get_pending_insights(user_id)
    return {"items": items, "count": len(items)}


@app.post("/api/agent/insight/approve")
def api_approve_insight(req: dict):
    """批准一条待审记忆 → 写入对应模块"""
    user_id = req.get("userId", "")
    insight_id = req.get("id", "")
    if not user_id or not insight_id:
        raise HTTPException(400, "userId and id required")
    return approve_insight(user_id, insight_id)


@app.post("/api/agent/insight/reject")
def api_reject_insight(req: dict):
    """拒绝一条待审记忆"""
    user_id = req.get("userId", "")
    insight_id = req.get("id", "")
    if not user_id or not insight_id:
        raise HTTPException(400, "userId and id required")
    return {"ok": reject_insight(user_id, insight_id)}


@app.post("/api/agent/analyze")
async def agent_analyze(req: dict):
    """Agent 决策引擎 — 手动触发分析"""
    user_id = req.get("userId", "default_user")
    force = req.get("force", False)
    model = req.get("model", "deepseek-chat")

    # 收集数据
    market_ctx = _build_market_context()
    portfolio_ctx = _build_portfolio_context(user_id=user_id)
    memory = build_memory_summary(user_id)

    # 收集预警
    alerts = []
    try:
        from services.stock_monitor import scan_all_holdings
        stock_scan = scan_all_holdings(user_id)
        alerts.extend(stock_scan.get("signals", []))
    except Exception:
        pass
    try:
        from services.fund_monitor import scan_all_fund_holdings
        fund_scan = scan_all_fund_holdings(user_id)
        alerts.extend(fund_scan.get("alerts", []))
    except Exception:
        pass
    try:
        from services.agent_memory import check_rules
        rule_alerts = check_rules(user_id, stock_scan if 'stock_scan' in dir() else {})
        alerts.extend(rule_alerts)
    except Exception:
        pass

    # 运行决策引擎
    result = run_analysis_cycle(
        user_id=user_id,
        market_context=market_ctx,
        portfolio_context=portfolio_ctx,
        alerts=alerts,
        memory_summary=memory,
        force_deepseek=force or len(alerts) > 0,
        model=model,
    )

    # 保存信号文件 + 决策日志
    save_signal_file(user_id, result)
    if result.get("source") == "ai":
        add_decision(user_id, {
            "action": "auto_analyze",
            "summary": result.get("analysis", "")[:200],
            "direction": result.get("direction", "neutral"),
            "confidence": result.get("confidence", 0),
            "alerts_count": len(alerts),
            "skill_used": result.get("skill_used", ""),
        })

    # V6 Phase 5: 自动存档到分析历史
    try:
        from services.analysis_history import save_analysis
        analysis_text = result.get("analysis", "") or result.get("reply", "")
        if analysis_text and result.get("source") == "ai":
            save_analysis(user_id, "deepseek", "DeepSeek V3", "full", analysis_text,
                         direction=result.get("direction", "unknown"),
                         confidence=result.get("confidence", 0))
    except Exception as e:
        print(f"[HISTORY] agent analyze 存档失败: {e}")

    return result

@app.get("/api/agent/signals/{user_id}")
def get_agent_signals(user_id: str):
    """获取最新信号文件"""
    fp = DATA_DIR / user_id / "monitor" / "latest_signal.json"
    if fp.exists():
        return json.loads(fp.read_text(encoding="utf-8"))
    return {"analysis": "暂无信号数据", "source": "none"}


# ---- V4 信号侦察兵 API ----
from services.signal_scout import get_latest as scout_get_latest, get_history as scout_get_history, collect as scout_collect

@app.get("/api/signal-scout/latest")
def api_signal_scout_latest(userId: str = ""):
    """获取用户最新匹配信号"""
    if not userId:
        return {"signals": [], "total": 0}
    return scout_get_latest(userId)

@app.get("/api/signal-scout/history")
def api_signal_scout_history(userId: str = "", days: int = 7):
    """获取历史信号"""
    if not userId:
        return []
    return scout_get_history(userId, days)

@app.post("/api/signal-scout/scan")
def api_signal_scout_scan():
    """手动触发全市场信号扫描（刷新缓存）"""
    from services.signal_scout import _signal_cache
    _signal_cache.clear()
    signals = scout_collect()
    return {"total": len(signals), "scanned_at": datetime.now().isoformat()}


# ---- V4 判断追踪器 API ----
from services.judgment_tracker import (
    scorecard as jt_scorecard, get_weights as jt_get_weights,
    calibrate as jt_calibrate, verify_pending as jt_verify_pending,
)

@app.get("/api/judgment/scorecard")
def api_judgment_scorecard(userId: str = "", months: int = 3):
    """判断成绩单 — 准确率/盈亏/模块贡献"""
    uid = userId or "default"
    return jt_scorecard(uid, months)

@app.get("/api/judgment/weights")
def api_judgment_weights(userId: str = ""):
    """当前模块权重（EMA 校准后）"""
    uid = userId or "default"
    weights = jt_get_weights(uid)
    return {"weights": weights, "user_id": uid}

@app.post("/api/judgment/calibrate")
def api_judgment_calibrate(req: dict = {}):
    """手动触发 EMA 权重校准"""
    uid = req.get("userId", "default")
    return jt_calibrate(uid)






# ---- V6.5: 盈利预测 + 估值 ----
@app.get("/api/earnings/{code}")
def api_earnings(code: str):
    """个股盈利预测（一致预期+评级分布+目标价）"""
    from services.earnings_forecast import get_stock_forecast
    return get_stock_forecast(code)

@app.get("/api/valuation/{code}")
def api_valuation(code: str):
    """个股估值评估（Forward PE+PEG+目标价空间）"""
    from services.valuation_engine import assess_valuation
    return assess_valuation(code)

@app.get("/api/dcf/{code}")
def api_dcf(code: str):
    """个股 DCF 估值（现金流折现法）"""
    from services.valuation_engine import dcf_valuation
    return dcf_valuation(code)

# ---- V7: 推荐引擎 + 买卖决策 ----
@app.get("/api/recommend/stocks")
def api_recommend_stocks(userId: str = "", topN: int = 10, pool: str = "hot", period: str = "medium"):
    """股票推荐（优先凌晨预计算缓存，否则实时算）"""
    from services.precomputed_cache import get_precomputed
    cached = get_precomputed("recommendations")
    if cached:  # 推荐是全局数据，有缓存就用（P0.4修复：去掉 not userId 限制）
        cached["from_cache"] = True
        return cached
    from services.recommend_engine import get_stock_recommendations
    return get_stock_recommendations(userId, topN, pool, period)

@app.get("/api/decisions")
def api_decisions(userId: str = ""):
    """买卖决策（优先凌晨预计算缓存，否则实时算）"""
    uid = userId or "default"
    from services.precomputed_cache import get_precomputed
    cached = get_precomputed("decisions", user_id=uid)
    if cached:
        cached["from_cache"] = True
        return cached
    from services.decision_maker import generate_decisions
    return generate_decisions(uid)

@app.get("/api/exposure/{code}")
def api_exposure(code: str):
    """个股业务敞口（出口占比+地缘脆弱性）"""
    from services.business_exposure import get_business_exposure
    return get_business_exposure(code)

@app.get("/api/fund-share/{ts_code}")
def api_fund_share(ts_code: str):
    """基金/ETF 份额变化"""
    from services.tushare_data import get_fund_share
    return get_fund_share(ts_code)


# 兜底：让 /app.js 等直接路径也能访问
@app.get("/{filename:path}")
def serve_frontend_file(filename: str):
    fp = FRONTEND_DIR / filename
    if fp.is_file():
        return _cached_file_response(fp)
    return _cached_file_response(FRONTEND_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

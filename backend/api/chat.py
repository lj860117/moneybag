"""
Chat & LLM 直调路由
====================
/api/chat          — AI 对话分析（非流式）
/api/chat/stream   — AI 对话分析（SSE 流式）
/api/models        — 可用模型列表

P3 高耦合路由 — 依赖 shared_helpers, agent_memory, steward, httpx
"""
import os
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from models.schemas import ChatRequest
from api.shared_helpers import (
    _build_market_context, _build_portfolio_context,
    _build_system_prompt, _load_prompt_template, _rule_based_reply,
    classify_chat_intent, AVAILABLE_MODELS,
)

router = APIRouter()


@router.get("/api/models")
def list_models():
    """返回可用模型列表（只返回有 API key 的模型）"""
    result = []
    for m in AVAILABLE_MODELS:
        key = os.environ.get(m["env_key"], "")
        if key:
            result.append({"id": m["id"], "name": m["name"], "provider": m["provider"]})
    return {"models": result, "default": "deepseek-v4-flash"}


@router.post("/api/chat")
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
    model = req.model or os.environ.get("LLM_MODEL", "deepseek-v4-flash")
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

            # ---- RAG 知识注入（M4 W3）----
            rag_context: dict = {"has_rag": False, "further_reading": []}
            try:
                from infra.knowledge import get_retriever, load_and_index_articles
                from use_cases.interpret_with_rag import build_rag_context

                retriever = get_retriever()
                if retriever.total_chunks() == 0:
                    load_and_index_articles(retriever)
                rag_context = build_rag_context(
                    retriever,
                    facts_summary=user_msg,
                    category_hint=intent.get("intent", ""),
                    top_k=3,
                )
                if rag_context["has_rag"]:
                    system_prompt += "\n\n" + rag_context["rag_prompt_injection"]
                    print(f"[CHAT] RAG injected {len(rag_context['rag_chunks'])} chunks")
            except Exception as e:
                print(f"[CHAT] RAG injection failed (non-blocking): {e}")
                rag_context = {"has_rag": False, "further_reading": []}

            from services.llm_gateway import LLMGateway
            gw = LLMGateway.instance()
            gw_result = gw.call_sync(
                user_msg,
                system=system_prompt,
                model_tier="llm_light",
                user_id=uid,
                module="chat",
                max_tokens=800,
            )
            print(f"[CHAT] Gateway result source={gw_result.get('source')}")
            if gw_result.get("content") and not gw_result.get("fallback"):
                    reply = gw_result["content"]
                    print(f"[CHAT] LLM reply OK, len={len(reply)}")
                    # Phase 0 (3.7): 记录决策日志
                    try:
                        from services.decision_log import log_decision
                        log_decision(user_id=uid, question=user_msg, advice=reply, source="chat", intent=intent.get("intent", "general"), model=gw_result.get("model", ""))
                    except Exception as e:
                        print(f"[CHAT] Decision log failed: {e}")

                    # ---- RAG 延伸阅读附加（M4 W3）----
                    if rag_context.get("has_rag"):
                        try:
                            from use_cases.interpret_with_rag import enrich_interpretation
                            enriched = enrich_interpretation(
                                retriever,
                                interpretation_text=reply,
                                facts_summary=user_msg,
                                category_hint=intent.get("intent", ""),
                            )
                            reply = enriched["text"]
                        except Exception as e:
                            print(f"[CHAT] RAG enrich failed (non-blocking): {e}")

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

                    return {
                        "reply": reply,
                        "source": "ai",
                        "further_reading": rag_context.get("further_reading", []),
                    }
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


@router.post("/api/chat/stream")
async def chat_analysis_stream(req: ChatRequest):
    """AI 对话分析 — SSE 流式响应，逐字输出"""
    user_msg = req.message.strip()
    if not user_msg:
        raise HTTPException(400, "消息不能为空")

    uid = req.userId or "default"

    # ★ 意图分类：判断是否理财相关
    intent = classify_chat_intent(user_msg)
    is_finance = intent["intent"] != "general"

    # 补充判断：包含股票/基金/市场关键词也算理财
    _FINANCE_KEYWORDS = ["股", "基金", "A股", "大盘", "牛市", "熊市", "涨", "跌",
                         "买入", "卖出", "持仓", "仓位", "定投", "理财", "投资",
                         "收益", "亏", "赚", "ETF", "指数", "板块", "行业",
                         # 地缘/政策事件（对市场有直接影响，需注入市场上下文）
                         "特朗普", "拜登", "普京", "关税", "制裁", "贸易战",
                         "访华", "峰会", "降息", "加息", "降准", "央行",
                         "战争", "冲突", "停火", "地缘", "芯片禁令"]
    if not is_finance:
        is_finance = any(kw in user_msg for kw in _FINANCE_KEYWORDS)

    if is_finance:
        # ---- 理财模式：注入市场数据 + 完整分析 ----
        market_ctx = _build_market_context()
        portfolio_ctx = _build_portfolio_context(req.portfolio, user_id=uid) if req.portfolio else _build_portfolio_context(user_id=uid)

        # ★ 规则优先：明确意图且规则引擎能精准回答的，直接用规则（快+准+用真实数据）
        # 这些意图的规则回答比 LLM 更好：用真实估值/恐贪/北向数据计算出具体建议
        _RULES_FIRST_INTENTS = {"timing", "smart_dca", "take_profit", "allocation",
                                "news", "macro", "valuation", "northbound"}
        if intent["intent"] in _RULES_FIRST_INTENTS:
            rule_reply = _rule_based_reply(user_msg, market_ctx, portfolio_ctx)
            if rule_reply and len(rule_reply) > 50:
                # 规则引擎给出了有效回答，直接流式返回（模拟 SSE 格式）
                print(f"[CHAT-STREAM] ★ 规则优先命中: intent={intent['intent']}, len={len(rule_reply)}")
                async def _rule_stream():
                    # 一次性发出（规则引擎已经格式化好了）
                    yield f"data: {json.dumps({'delta': rule_reply, 'source': 'rules'})}\n\n"
                    yield f"data: {json.dumps({'delta': '', 'source': 'rules', 'done': True})}\n\n"
                return StreamingResponse(_rule_stream(), media_type="text/event-stream")

        # 多用户记忆注入
        if req.userId:
            try:
                from services.agent_memory import build_memory_summary, record_emotion
                record_emotion(req.userId, user_msg)
                mem = build_memory_summary(req.userId)
                if mem:
                    portfolio_ctx += f"\n\n## 用户记忆\n{mem}"
            except Exception as e:
                print(f"[CHAT-STREAM] memory inject failed: {e}")

        # 个股/基金新闻注入
        try:
            from services.steward import _extract_stock_name, _extract_fund_name
            stock_name, stock_code = _extract_stock_name(user_msg)
            fund_name, fund_code = _extract_fund_name(user_msg)

            if stock_code:
                from infra.data_source import get_stock_news
                news = get_stock_news(stock_code, limit=8)
                if news:
                    news_text = "\n".join([f"- {n['title']}" for n in news])
                    market_ctx += f"\n\n## {stock_name}({stock_code})最新新闻\n{news_text}"
            elif fund_code and fund_code != "余额宝":
                from services.data_layer import get_fund_news
                fund_news = get_fund_news(fund_code, 8)
                valid_news = [n for n in fund_news if n.get("title") and "加载中" not in n.get("title", "")]
                if valid_news:
                    news_text = "\n".join([f"- {n['title']}" for n in valid_news[:8]])
                    market_ctx += f"\n\n## {fund_name}({fund_code})最新新闻\n{news_text}"
        except Exception as e:
            print(f"[CHAT] news inject: {e}")

        # 管家上下文注入
        try:
            from services.agent_memory import get_context
            last_ctx = get_context(uid)
            if last_ctx.get("last_analysis"):
                portfolio_ctx += f"\n\n## 管家最近分析结论\n{last_ctx['last_analysis'][:300]}"
        except Exception:
            pass

        system_prompt = _build_system_prompt(market_ctx, portfolio_ctx)
        print(f"[CHAT-STREAM] 理财模式, intent={intent['intent']}")
    else:
        # ---- 闲聊模式：轻量 prompt + 联网搜索（如需要） ----
        base_prompt = _load_prompt_template()

        # 判断是否需要联网（时事、事件、实时信息等）
        # 原则：LLM 训练数据有截止日期，凡是可能涉及"最近发生的事"都应联网
        _NEED_SEARCH_KW = [
            # 时间相关
            "天气", "气温", "下雨", "预报", "今天", "明天", "这周", "本周", "昨天",
            "最新", "最近", "新闻", "刚刚", "热搜", "发生了什么",
            # 时事/事件类（LLM 训练数据过期，必须联网）
            "了吗", "了没", "了么", "是真的吗", "怎么回事", "什么时候",
            "访华", "访问", "峰会", "制裁", "开战", "停火", "选举", "当选",
            "发布会", "声明", "政策", "降息", "加息", "降准",
            # 人物（可能有最新动态）
            "特朗普", "拜登", "普京", "泽连斯基", "马斯克", "任正非",
            "习近平", "李强", "耶伦",
        ]
        _need_search = any(kw in user_msg for kw in _NEED_SEARCH_KW)

        search_ctx = ""
        if _need_search:
            try:
                from services.web_search import search_web, search_weather, format_search_for_prompt
                # 天气问题优先用天气 API
                _WEATHER_KW = ["天气", "气温", "下雨", "温度", "预报"]
                if any(kw in user_msg for kw in _WEATHER_KW):
                    # 提取城市名（简单规则）
                    import re
                    city_match = re.search(r"([一-龥]{2,4}?)(?:的|这周|今天|明天|本周)?(?:天气|气温|温度|下雨)", user_msg)
                    city = city_match.group(1) if city_match else "上海"
                    weather = search_weather(city)
                    if weather:
                        search_ctx = f"\n\n## 实时天气数据\n{weather}"
                else:
                    # 通用搜索（秘塔）
                    results = search_web(user_msg, limit=3)
                    if results:
                        search_ctx = "\n\n" + format_search_for_prompt(results)
            except Exception as e:
                print(f"[CHAT-STREAM] search failed: {e}")

        system_prompt = base_prompt + search_ctx if search_ctx else base_prompt
        print(f"[CHAT-STREAM] 闲聊模式, search={'有' if search_ctx else '无'}, msg={user_msg[:30]}")

    # API key + 模型选择（通过 gateway 统一获取配置）
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

    if not api_key or not gw.pre_check():
        reply = "AI 暂时不可用，请稍后再试~" if not api_key else _rule_based_reply(user_msg, market_ctx, portfolio_ctx)
        async def rules_gen():
            yield f"data: {json.dumps({'delta': reply, 'source': 'rules', 'done': True}, ensure_ascii=False)}\n\n"
        return StreamingResponse(rules_gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def stream_gen():
        try:
            for chunk in gw.stream_sync(
                user_msg,
                system=system_prompt,
                model_tier="llm_light" if "reasoner" not in model else "llm_heavy",
                user_id=uid,
                module="chat_stream",
                max_tokens=1200,
            ):
                if chunk.get("fallback"):
                    # gateway 限流/错误 → 降级规则引擎
                    reply = _rule_based_reply(user_msg, market_ctx, portfolio_ctx)
                    yield f"data: {json.dumps({'delta': reply, 'source': 'rules', 'done': True}, ensure_ascii=False)}\n\n"
                    return
                if chunk.get("done"):
                    yield f"data: {json.dumps({'delta': '', 'source': 'ai', 'done': True}, ensure_ascii=False)}\n\n"
                    return
                # 正常 chunk（thinking / answering）
                delta = chunk.get("delta", "")
                phase = chunk.get("phase", "answering")
                if delta:
                    yield f"data: {json.dumps({'delta': delta, 'source': 'ai', 'done': False, 'phase': phase}, ensure_ascii=False)}\n\n"
        except Exception as e:
            print(f"[CHAT-STREAM] LLM stream failed: {e}")
            reply = _rule_based_reply(user_msg, market_ctx, portfolio_ctx)
            yield f"data: {json.dumps({'delta': reply, 'source': 'rules', 'done': True}, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

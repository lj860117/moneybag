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
    _build_system_prompt, _rule_based_reply,
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


@router.post("/api/chat/stream")
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
            # 个股新闻 — via infra/data_source (Invariant #6)
            from infra.data_source import get_stock_news
            news = get_stock_news(stock_code, limit=8)
            if news:
                news_text = "\n".join([f"- {n['title']}" for n in news])
                market_ctx += f"\n\n## {stock_name}({stock_code})最新新闻\n{news_text}"
                print(f"[CHAT] 注入 {stock_name} 个股新闻 {len(news)} 条")
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
    model = req.model or os.environ.get("LLM_MODEL", "deepseek-v4-flash")
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

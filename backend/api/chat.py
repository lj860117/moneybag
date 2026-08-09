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
    _rule_based_reply_structured, classify_chat_intent, AVAILABLE_MODELS,
)

router = APIRouter()


def _resolve_chat_model(requested_model: str | None, *, model_tier: str = "llm_light", module: str = "chat") -> str:
    if requested_model:
        return requested_model

    try:
        from services.llm_gateway import LLMGateway

        gw = LLMGateway.instance()
        api_cfg = gw.get_api_config(model_tier=model_tier, module=module)
        if api_cfg.get("model"):
            return api_cfg["model"]
    except Exception:
        pass

    try:
        from services.llm_gateway import resolve_default_model

        return resolve_default_model(model_tier, module=module)
    except Exception:
        return "deepseek-v4-flash"


def _extract_and_save_memory(user_id: str, user_msg: str, reply: str) -> None:
    """后台线程：用轻量 LLM 提取本次对话的关键决策/偏好，存入 pending_insights。

    只提取有价值的信息（投资偏好、明确决定、重要约束），忽略闲聊。
    失败时静默，不影响主流程。
    """
    try:
        from services.llm_gateway import LLMGateway
        from domain.services.user_preference_service import add_pending_insight

        extract_prompt = (
            f"用户问：{user_msg[:200]}\n"
            f"AI答：{reply[:400]}\n\n"
            "请提取本次对话中用户透露的**投资偏好/明确决定/重要约束**，一句话（<25字）。\n"
            "示例：「用户风险偏好保守，不接受单仓超20%」「用户决定本月暂停加仓等待回调」\n"
            "如果本次对话没有重要信息，直接输出：无"
        )

        gw = LLMGateway.instance()
        result = gw.call_sync(
            extract_prompt,
            model_tier="llm_light",
            user_id=user_id,
            module="memory_extract",
            max_tokens=40,
        )
        content = result.get("content", "").strip()
        if content and content != "无" and len(content) > 4:
            add_pending_insight(user_id, {
                "type": "chat_extract",
                "text": content,
                "source_q": user_msg[:80],
                "created_at": datetime.now().isoformat(),
            })
            print(f"[MEMORY] 提取记忆: {content[:60]}")
    except Exception as e:
        print(f"[MEMORY] 记忆提取失败（静默）: {e}")


# 快速路径意图 — 这些 intent 优先走规则引擎（毫秒级），不命中再 fall through 到 LLM
FAST_PATH_INTENTS = {"safety_refusal", "holdings_query", "empty_holdings_query",
                     "cross_account_refusal",
                     "timing", "take_profit", "dca", "sentiment", "macro_summary",
                     "smart_dca", "news", "macro", "valuation", "northbound",
                     "briefing_request", "weekly_request", "cash_safety",
                     "operation_goal", "operation_discipline"}


# ========================================================
# v9.5.123: AI 追问建议生成器
# ========================================================
_FOLLOW_UP_MAP = {
    "timing": ["现在适合定投吗", "哪些板块比较安全", "大盘什么时候企稳"],
    "holdings_query": ["帮我分析持仓风险", "哪只基金建议减仓", "持仓行业集中度如何"],
    "dca": ["定投频率多久合适", "哪只基金适合加大定投", "如果暴跌了还要继续定投吗"],
    "smart_dca": ["给我看看每只基金的定投建议", "本月定投金额建议多少", "和固定定投相比优势在哪"],
    "take_profit": ["设多少止盈线合适", "分批止盈还是一次卖出", "止盈后的钱放哪里"],
    "valuation": ["现在哪些指数低估", "估值高位该怎么操作", "估值百分位多少算便宜"],
    "macro": ["利率变化对我持仓有什么影响", "美联储政策对A股影响", "通胀数据怎么看"],
    "sentiment": ["市场情绪指数现在多少", "恐慌时应该怎么做", "当前市场贪婪还是恐惧"],
    "news": ["最近有什么利好政策", "哪些板块最近有利空", "市场热点是什么"],
    "general": ["帮我做个持仓体检", "本周有什么需要注意的", "我的资产配置合理吗"],
}


def _generate_follow_ups(intent: str, user_msg: str) -> list:
    """根据意图生成2-3个追问建议"""
    suggestions = _FOLLOW_UP_MAP.get(intent, _FOLLOW_UP_MAP["general"])
    # 过滤掉和用户问题太相似的
    filtered = [s for s in suggestions if s not in user_msg and user_msg not in s]
    return filtered[:3] if filtered else suggestions[:2]


# ========================================================
# v9.5.122: 预制问题秒回 + 长期记忆
# ========================================================

# 预制问题关键词匹配规则
_PRESET_QUESTIONS = [
    {"keywords": ["诊断", "持仓", "有没有问题", "健康"], "cache_key": "preset_diagnosis"},
    {"keywords": ["估值", "高吗", "入场", "现在贵吗"], "cache_key": "preset_valuation"},
    {"keywords": ["再平衡", "调仓", "配置", "比例"], "cache_key": "preset_rebalance"},
    {"keywords": ["重叠", "精简", "合并", "赛道重复"], "cache_key": "preset_overlap"},
    {"keywords": ["复盘", "本周", "总结", "回顾"], "cache_key": "preset_review"},
    {"keywords": ["风险", "最大风险", "危险", "要注意"], "cache_key": "preset_risk"},
]


def _is_market_anomaly() -> bool:
    """v9.5.123: 检测盘中异动（大盘日跌>2%或日涨>3%），异动时废弃预制答案
    
    通过读取市场行情缓存（shared_helpers 的 market_ctx）快速判断，不额外请求。
    """
    try:
        from pathlib import Path
        import json as _j, time as _t
        cache_dir = Path(os.environ.get("DATA_DIR", "data")) / "_cache"
        # 读市场上下文缓存（由 cache_warmer 维护）
        for fn in ["market_ctx.json", "market_context.json"]:
            fp = cache_dir / fn
            if fp.exists() and (_t.time() - fp.stat().st_mtime) < 7200:  # 2h 内有效
                data = _j.loads(fp.read_text(encoding="utf-8"))
                # 检查大盘日涨跌
                indices = data.get("indices") or data.get("market") or {}
                for idx_name, idx_data in indices.items():
                    if isinstance(idx_data, dict):
                        chg = idx_data.get("change_pct") or idx_data.get("pct_change") or 0
                        if isinstance(chg, (int, float)):
                            if chg < -2.0 or chg > 3.0:
                                print(f"[PRESET] 市场异动检测: {idx_name} 日涨跌 {chg}%, 废弃预制答案")
                                return True
        return False
    except Exception:
        return False  # 检测失败不阻塞


def _check_preset_answer(user_msg: str, user_id: str) -> str | None:
    """检查用户消息是否匹配预制问题，命中则直接返回预计算答案（不调 LLM）
    
    v9.5.123: 盘中异动时自动失效，走实时 LLM 确保准确性。
    """
    import time as _t
    from pathlib import Path
    
    # v9.5.123: 盘中异动检测 — 异动时废弃所有预制答案
    if _is_market_anomaly():
        return None
    
    cache_dir = Path(os.environ.get("DATA_DIR", "data")) / "_cache" / "preset_answers"
    
    for preset in _PRESET_QUESTIONS:
        # 需要匹配至少2个关键词（避免误触）
        matched = sum(1 for kw in preset["keywords"] if kw in user_msg)
        if matched >= 1 and any(kw in user_msg for kw in preset["keywords"][:2]):
            fp = cache_dir / f"{preset['cache_key']}_{user_id}.txt"
            try:
                if fp.exists():
                    age = _t.time() - fp.stat().st_mtime
                    if age < 86400:  # 24h 有效
                        content = fp.read_text(encoding="utf-8").strip()
                        if content and len(content) > 20:
                            return content + f"\n\n_（预计算回答，数据截至 {datetime.fromtimestamp(fp.stat().st_mtime).strftime('%m-%d %H:%M')}。如需最新分析请追问）_"
            except Exception:
                pass
    return None


def _load_user_memory(user_id: str) -> str:
    """加载用户长期记忆（v9.5.122: 只取3个月内的 insights，过期的自动衰减）"""
    try:
        from domain.services.user_preference_service import get_pending_insights
        insights = get_pending_insights(user_id) or []
        if not insights:
            return ""
        # v9.5.122: 只保留3个月内的记忆（过期的不注入避免误导 AI）
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=90)).isoformat()
        valid = []
        for i in insights:
            text = i.get("text", "")
            if not text or text == "无":
                continue
            created = i.get("created_at", "")
            # 没有时间戳的老数据也保留（兼容）
            if created and created < cutoff:
                continue  # 超过3个月，跳过
            valid.append(text)
        if not valid:
            return ""
        # 取最近10条
        recent = valid[-10:]
        return "用户过往表达的偏好/决策（近3个月）：\n" + "\n".join(f"- {r}" for r in recent)
    except Exception:
        return ""


@router.get("/api/models")
def list_models():
    """返回可用模型列表（只返回有 API key 的模型）"""
    result = []
    for m in AVAILABLE_MODELS:
        key = os.environ.get(m["env_key"], "")
        if key:
            result.append({"id": m["id"], "name": m["name"], "provider": m["provider"]})
    return {"models": result, "default": _resolve_chat_model(None, model_tier="llm_light", module="chat_ui")}


@router.post("/api/chat")
async def chat_analysis(req: ChatRequest):
    """AI 对话分析 — 回答用户的理财问题"""
    user_msg = req.message.strip()
    if not user_msg:
        raise HTTPException(400, "消息不能为空")

    # v9.5.123: 数据来源追踪
    import time as _time
    _data_sources = []  # 记录本次回答用了哪些数据源
    _data_cutoff = ""   # 数据截止时间
    
    # v9.5.122: 预制问题秒回 — 匹配高频问题的预计算答案（不调 LLM）
    uid = req.userId or "default"
    preset_hit = _check_preset_answer(user_msg, uid)
    if preset_hit:
        return {
            "reply": preset_hit, "source": "preset_cache", "served_by": "preset",
            "data_meta": {"sources": ["预计算答案(定时更新)"], "note": "此回答来自后台定时预算,非实时计算"},
        }

    # Phase 0 (3.6): 意图预分类（规则优先，不调 LLM）
    intent = classify_chat_intent(user_msg)

    # 构建市场上下文
    market_ctx = _build_market_context()
    portfolio_ctx = _build_portfolio_context(req.portfolio, user_id=uid) if req.portfolio else _build_portfolio_context(user_id=uid)

    # ★ 规则优先：快速路径（<1s，用真实数据计算，比 LLM 编造更可靠）
    # 涉及时事/新闻事件的问题跳过规则引擎（规则引擎没有实时搜索能力）
    _EVENT_KW_NONSTREAM = [
        "最近", "最新", "刚刚", "了吗", "了没", "了么", "是真的吗", "怎么回事",
        "访华", "峰会", "制裁", "开战", "停火", "选举", "当选",
        "降息", "加息", "降准", "暴跌", "暴涨", "崩盘", "退市",
    ]
    _need_event_search = any(kw in user_msg for kw in _EVENT_KW_NONSTREAM)
    if intent["intent"] in FAST_PATH_INTENTS and not _need_event_search:
        rule_result = _rule_based_reply_structured(user_msg, market_ctx, portfolio_ctx)
        if rule_result and rule_result["confidence"] >= 0.7:
            print(f"[CHAT] ★ 规则优先命中: intent={rule_result['intent']}, confidence={rule_result['confidence']}")
            # 记录决策日志
            try:
                from services.decision_log import log_decision
                log_decision(user_id=uid, question=user_msg, advice=rule_result["text"],
                             source="rules", intent=rule_result["intent"], model="rules")
            except Exception:
                pass
            from datetime import datetime
            return {
                "reply": rule_result["text"], "source": "rules", "served_by": "rules",
                "data_meta": {"sources": ["规则引擎(实时计算)"], "cutoff": datetime.now().strftime("%m-%d %H:%M"), "note": "基于确定性数据,无AI推测"},
            }
        # 不命中 → fall through 到 LLM

    # v9.5.122: 长期记忆注入（跨会话偏好积累）
    if req.userId:
        try:
            from services.agent_memory import record_emotion
            record_emotion(req.userId, user_msg)
        except Exception:
            pass
        try:
            mem = _load_user_memory(uid)
            if mem:
                portfolio_ctx += f"\n\n## 用户长期偏好记忆\n{mem}"
        except Exception as e:
            print(f"[CHAT] memory inject failed: {e}")

    # 尝试调用 LLM（显式选模优先，默认模型走 gateway 的峰谷路由）
    from services.llm_gateway import LLMGateway
    gw = LLMGateway.instance()
    model = _resolve_chat_model(req.model, model_tier="llm_light", module="chat")
    api_cfg = gw.get_api_config(model_tier="llm_light", module="chat")
    api_key = req.model or api_cfg.get("api_key")
    api_base = next((m["base"] for m in AVAILABLE_MODELS if m["id"] == model), api_cfg.get("api_base"))
    print(f"[CHAT] api_key={'SET' if api_key else 'EMPTY'}, base={api_base}, model={model}")

    if api_key:
        try:
            import httpx
            print(f"[CHAT] Calling LLM Gateway... intent={intent}")
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

            gw_result = gw.call_sync(
                user_msg,
                system=system_prompt,
                model_tier="llm_light",
                user_id=uid,
                module="chat",
                max_tokens=800,
                explicit_model=model,
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

                    # Response Validator: 校验 LLM 输出质量
                    try:
                        from use_cases.response_validator import validate_response
                        validation = validate_response(reply, user_msg, portfolio_ctx)
                        if not validation["valid"]:
                            reply = validation["reply"]
                            print(f"[CHAT] Response validated, issues={validation['issues']}")
                    except Exception as e:
                        print(f"[CHAT] Response validator failed (non-blocking): {e}")

                    # D4: LLM 输出守卫 — 过滤 prompt 泄漏（宽松模式）
                    try:
                        from services.llm_output_guard import LLMOutputGuard
                        reply_guarded = LLMOutputGuard.filter_chat(reply, fallback=reply)
                        if reply_guarded != reply:
                            print(f"[CHAT] OutputGuard filtered {len(reply)-len(reply_guarded)} chars")
                        reply = reply_guarded
                    except Exception as e:
                        print(f"[CHAT] OutputGuard failed (non-blocking): {e}")

                    # v9.5.123: 数据来源标注
                    _data_sources.append(f"AI模型({gw_result.get('model','DeepSeek')})")
                    if market_ctx:
                        _data_sources.append("市场行情数据")
                    if portfolio_ctx and "持仓" in portfolio_ctx:
                        _data_sources.append("个人持仓数据")
                    if rag_context.get("has_rag"):
                        _data_sources.append("知识库文献")
                    from datetime import datetime
                    _data_cutoff = datetime.now().strftime("%m-%d %H:%M")
                    
                    return {
                        "reply": reply,
                        "source": "ai",
                        "served_by": "llm",
                        "further_reading": rag_context.get("further_reading", []),
                        "follow_ups": _generate_follow_ups(intent.get("intent", "general"), user_msg),
                        "data_meta": {
                            "sources": _data_sources,
                            "cutoff": _data_cutoff,
                            "note": "AI分析基于上述数据源,仅供参考不构成投资建议",
                        },
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
    from datetime import datetime
    return {
        "reply": reply, "source": "rules", "served_by": "rules",
        "data_meta": {
            "sources": ["规则引擎(基于实时市场数据计算)"],
            "cutoff": datetime.now().strftime("%m-%d %H:%M"),
            "note": "此回答由规则引擎生成,基于确定性数据,不含AI推测",
        },
    }


@router.post("/api/chat/stream")
async def chat_analysis_stream(req: ChatRequest):
    """AI 对话分析 — SSE 流式响应，逐字输出"""
    user_msg = req.message.strip()
    if not user_msg:
        raise HTTPException(400, "消息不能为空")

    uid = req.userId or "default"

    # ★ 统一构建市场+持仓上下文（无论理财还是闲聊，都需要）
    market_ctx = ""
    portfolio_ctx = ""
    try:
        market_ctx = _build_market_context()
    except Exception as e:
        print(f"[CHAT-STREAM] market_ctx build failed: {e}")
    try:
        portfolio_ctx = _build_portfolio_context(req.portfolio, user_id=uid) if req.portfolio else _build_portfolio_context(user_id=uid)
    except Exception as e:
        print(f"[CHAT-STREAM] portfolio_ctx build failed: {e}")

    # ★ 意图分类：判断是否理财相关
    intent = classify_chat_intent(user_msg)
    is_finance = intent["intent"] != "general"

    # ★ Function Calling 路径：LLM 主动调工具查数据（Agent 模式）
    # 触发条件：比较/查具体品种/推荐选择等"后端没有预先准备好数据"的问题
    try:
        from api.chat_fc import should_use_fc, run_fc_agent_stream
        if should_use_fc(user_msg, intent["intent"]):
            system_prompt = _build_system_prompt(market_ctx, portfolio_ctx)
            print(f"[FC_AGENT] 触发 Function Calling: {user_msg[:40]}")
            history_dicts = [h.dict() for h in req.history] if req.history else None

            async def fc_stream_gen():
                default_model = _resolve_chat_model(req.model, model_tier="llm_light", module="chat_stream")
                for chunk in run_fc_agent_stream(
                    user_msg,
                    system_prompt=system_prompt,
                    user_id=uid,
                    model=default_model,
                    history=history_dicts,
                ):
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

            return StreamingResponse(fc_stream_gen(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    except Exception as e:
        print(f"[FC_AGENT] 初始化失败，回退到普通模式: {e}")

    # 补充判断：包含股票/基金/市场关键词也算理财
    _FINANCE_KEYWORDS = ["股", "基金", "A股", "大盘", "牛市", "熊市", "涨", "跌",
                         "买入", "卖出", "持仓", "仓位", "定投", "理财", "投资",
                         "收益", "亏", "赚", "ETF", "指数", "板块", "行业",
                         "资产", "净资产", "现金", "配置", "风险", "周报", "晨报",
                         "数据源", "数据", "持有", "账户",
                         # 地缘/政策事件
                         "特朗普", "拜登", "普京", "关税", "制裁", "贸易战",
                         "访华", "峰会", "降息", "加息", "降准", "央行",
                         "战争", "冲突", "停火", "地缘", "芯片禁令"]
    if not is_finance:
        is_finance = any(kw in user_msg for kw in _FINANCE_KEYWORDS)

    # ★ 投资决策意图检测 — 触发会诊面板
    _PANEL_INTENTS = {"timing", "take_profit", "allocation", "portfolio_doctor"}
    _PANEL_KEYWORDS = ["入场", "进场", "能买", "该买", "适合买", "抄底",
                       "该卖", "减仓", "止盈", "止损", "加仓", "能入",
                       "怎么配置", "资产配置", "现在适合", "要不要买",
                       "能不能买", "值得买", "适合入", "能抄底",
                       "持仓风险", "分析持仓", "持仓调整", "怎么调整",
                       "要不要卖", "该怎么办", "仓位"]
    is_panel = (intent["intent"] in _PANEL_INTENTS or
                any(kw in user_msg for kw in _PANEL_KEYWORDS))

    if is_panel:
        # ---- 投资会诊模式：多大师面板 + LLM 综合 ----
        print(f"[CHAT-STREAM] ★ 投资会诊模式, intent={intent['intent']}")
        try:
            from services.panel_advisor import generate_panel
            panel = generate_panel(uid, user_msg)
            perspectives = panel["perspectives"]
            synthesis_prompt = panel["synthesis_prompt"]

            async def _panel_stream():
                # 1. 先发送面板数据（前端渲染为卡片）
                yield f"data: {json.dumps({'type': 'panel', 'perspectives': perspectives, 'done': False}, ensure_ascii=False)}\n\n"

                # 2. 流式发送 AI 综合判断
                try:
                    from services.llm_gateway import LLMGateway
                    gw = LLMGateway.instance()
                    for chunk in gw.stream_sync(
                        user_msg,
                        system=synthesis_prompt,
                        model_tier="llm_light",
                        user_id=uid,
                        module="panel_synthesis",
                        max_tokens=300,
                    ):
                        if chunk.get("fallback"):
                            # LLM 不可用，用简单结论
                            direction = panel.get("data_summary", "")
                            yield f"data: {json.dumps({'type': 'stream', 'delta': '综合来看，建议观望为主，等信号更明确再做决定。', 'done': False}, ensure_ascii=False)}\n\n"
                            break
                        if chunk.get("done"):
                            break
                        delta = chunk.get("delta", "")
                        phase = chunk.get("phase", "answering")
                        if phase == "thinking":
                            continue
                        if delta:
                            yield f"data: {json.dumps({'type': 'stream', 'delta': delta, 'done': False}, ensure_ascii=False)}\n\n"
                except Exception as e:
                    print(f"[PANEL] synthesis stream failed: {e}")
                    yield f"data: {json.dumps({'type': 'stream', 'delta': '综合判断生成失败，请参考上方各视角观点。', 'done': False}, ensure_ascii=False)}\n\n"

                # 3. 结束
                yield f"data: {json.dumps({'type': 'stream', 'delta': '', 'done': True, 'served_by': 'panel'}, ensure_ascii=False)}\n\n"

            return StreamingResponse(_panel_stream(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        except Exception as e:
            print(f"[CHAT-STREAM] panel generation failed, falling through to normal: {e}")
            # 面板生成失败，降级到普通 LLM 回答

    _search_banner = ""  # 搜索来源横幅（有搜索时先于 LLM 推送给前端）

    if is_finance:
        # ---- 理财模式：完整分析 ----

        # 检测是否涉及时事/新闻（需要联网搜索的问题不走规则引擎快速路径）
        _EVENT_SEARCH_KW = [
            "最近", "最新", "刚刚", "要上市", "上市了", "了吗", "了没", "了么",
            "是真的吗", "怎么回事", "什么时候", "新闻", "消息", "发生",
            "访华", "峰会", "制裁", "开战", "停火", "选举", "当选",
            "发布", "声明", "降息", "加息", "降准", "暴跌", "暴涨",
            "崩盘", "跳水", "熔断", "退市", "IPO", "收购", "合并",
            # 影响分析类：用户问某事件对市场的影响，需要先搜事件本身
            "影响", "进展", "动态", "怎么了", "怎么回事", "谈判",
            "关税", "贸易战", "地缘", "芯片禁令", "制裁",
        ]
        _need_finance_search = any(kw in user_msg for kw in _EVENT_SEARCH_KW)

        # ★ 规则优先：明确意图且规则引擎能精准回答的，直接用规则（快+准+用真实数据）
        # 但涉及时事的问题跳过规则引擎（规则引擎没有实时信息）
        if intent["intent"] in FAST_PATH_INTENTS and not _need_finance_search:
            rule_result = _rule_based_reply_structured(user_msg, market_ctx, portfolio_ctx)
            if rule_result and rule_result["confidence"] >= 0.7:
                # 规则引擎给出了有效回答，模拟打字逐段流式返回
                print(f"[CHAT-STREAM] ★ 规则优先命中: intent={rule_result['intent']}, confidence={rule_result['confidence']}")
                async def _rule_stream():
                    # 按段落分块模拟打字效果
                    text = rule_result["text"]
                    chunks = text.split("\n\n")
                    for i, chunk in enumerate(chunks):
                        piece = chunk + ("\n\n" if i < len(chunks) - 1 else "")
                        yield f"data: {json.dumps({'delta': piece, 'source': 'rules', 'done': False, 'phase': 'answering'}, ensure_ascii=False)}\n\n"
                    # 最终 meta event
                    yield f"data: {json.dumps({'delta': '', 'source': 'rules', 'done': True, 'served_by': 'rules'}, ensure_ascii=False)}\n\n"
                return StreamingResponse(_rule_stream(), media_type="text/event-stream",
                                         headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

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

        # ★ 理财问题联网搜索：涉及时事/新闻/最新事件时注入搜索结果
        if _need_finance_search:
            try:
                from services.web_search import search_web, format_search_for_prompt
                import re as _re
                # 搜索 query 构建：提取事件核心词，去掉提问句式
                _search_query = _re.sub(r'[？?呢吗吧啊哦嘛]$', '', user_msg.strip())
                _q_trimmed = _re.sub(r'(有什么|怎么样|如何|是否|能不能|对[^对]{0,10}影响|对[^对]{0,10}有什么).*$', '', _search_query)
                if len(_q_trimmed.strip()) >= 4:
                    _search_query = _q_trimmed.strip()
                _search_query = _search_query[:30] if len(_search_query) > 30 else _search_query
                # 影响分析类多搜几条，普通新闻类搜3条
                _search_limit = 6 if any(kw in user_msg for kw in ["影响", "分析", "进展"]) else 3
                results = search_web(_search_query, limit=_search_limit)
                if results:
                    market_ctx += "\n\n## 联网搜索结果（实时）\n" + format_search_for_prompt(results)
                    # 构建前端可见的搜索来源横幅
                    sources = list(dict.fromkeys([r.get("source","") for r in results if r.get("source")]))[:3]
                    dates = [r.get("date","") for r in results if r.get("date")]
                    latest_date = max(dates) if dates else ""
                    src_txt = "、".join(sources) if sources else "东方财富"
                    date_txt = f"，最新 {latest_date}" if latest_date else ""
                    _search_banner = f"📡 已搜索 {len(results)} 条相关资讯（{src_txt}{date_txt}），基于以下内容分析：\n\n"
                    print(f"[CHAT-STREAM] 理财+联网搜索: q='{_search_query}', {len(results)} 条结果")
            except Exception as e:
                print(f"[CHAT-STREAM] finance search failed: {e}")

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

        # 知识查询兜底：「X是什么/介绍X/解释X」+ 问题超过4字且不是纯常识
        # 用搜索给 LLM 补充背景，避免用过期训练数据答（如"武汉六小龙是什么"）
        _KNOWLEDGE_QUERY_KW = ["是什么", "是谁", "什么是", "介绍一下", "了解一下",
                                "解释一下", "什么意思", "怎么理解", "有哪些", "是哪些",
                                "是指什么", "指的是什么"]
        if not _need_search and len(user_msg) > 4 and any(kw in user_msg for kw in _KNOWLEDGE_QUERY_KW):
            _need_search = True

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
    model = _resolve_chat_model(req.model, model_tier="llm_light", module="chat_stream")
    api_cfg = gw.get_api_config(model_tier="llm_light", module="chat_stream")
    api_key = req.model or api_cfg["api_key"]
    api_base = next((m["base"] for m in AVAILABLE_MODELS if m["id"] == model), api_cfg["api_base"])

    if not api_key or not gw.pre_check():
        reply = "AI 暂时不可用，请稍后再试~" if not api_key else _rule_based_reply(user_msg, market_ctx, portfolio_ctx)
        async def rules_gen():
            yield f"data: {json.dumps({'delta': reply, 'source': 'rules', 'done': True}, ensure_ascii=False)}\n\n"
        return StreamingResponse(rules_gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def stream_gen():
        nonlocal _search_banner
        _full_reply = []  # 累积完整回复，用于回复完成后提取记忆
        try:
            # 如果有搜索来源，先推一条 banner 让用户知道搜到了什么
            if _search_banner:
                yield f"data: {json.dumps({'delta': _search_banner, 'source': 'search_banner', 'done': False, 'phase': 'answering'}, ensure_ascii=False)}\n\n"
            for chunk in gw.stream_sync(
                user_msg,
                system=system_prompt,
                model_tier="llm_light" if "reasoner" not in model else "llm_heavy",
                user_id=uid,
                module="chat_stream",
                max_tokens=1200,
                history=[h.dict() for h in req.history] if req.history else None,
                explicit_model=model,  # 用户主动选择的模型（含千问）
            ):
                if chunk.get("fallback"):
                    # gateway 限流/错误 → 降级规则引擎
                    reply = _rule_based_reply(user_msg, market_ctx, portfolio_ctx)
                    yield f"data: {json.dumps({'delta': reply, 'source': 'rules', 'done': True}, ensure_ascii=False)}\n\n"
                    return
                if chunk.get("done"):
                    yield f"data: {json.dumps({'delta': '', 'source': 'ai', 'done': True, 'served_by': 'llm', 'model': chunk.get('model', ''), 'fallback_used': chunk.get('fallback_used', False)}, ensure_ascii=False)}\n\n"
                    # ★ 回复完成后，后台轻量提取关键决策/偏好存入记忆
                    _full_reply_text = "".join(_full_reply)
                    if uid and uid != "default" and _full_reply_text and len(user_msg) > 6:
                        import threading
                        threading.Thread(
                            target=_extract_and_save_memory,
                            args=(uid, user_msg, _full_reply_text),
                            daemon=True
                        ).start()
                    return
                # 正常 chunk（thinking / answering）
                delta = chunk.get("delta", "")
                phase = chunk.get("phase", "answering")
                # 过滤 thinking phase（不发给前端，避免内部推理泄露）
                if phase == "thinking":
                    continue
                if delta:
                    # 流式级过滤：替换禁止短语
                    delta = delta.replace("我无法访问你的账户", "当前系统记录显示")
                    delta = delta.replace("我无法查看你的", "当前系统记录的")
                    _full_reply.append(delta)  # 累积完整回复
                    yield f"data: {json.dumps({'delta': delta, 'source': 'ai', 'done': False, 'phase': phase}, ensure_ascii=False)}\n\n"
        except Exception as e:
            print(f"[CHAT-STREAM] LLM stream failed: {e}")
            reply = _rule_based_reply(user_msg, market_ctx, portfolio_ctx)
            yield f"data: {json.dumps({'delta': reply, 'source': 'rules', 'done': True}, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

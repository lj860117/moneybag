"""
钱袋子 — Function Calling 处理器
====================================
让 DeepSeek V4 能主动调工具查数据，从"管道模式"升级到"Agent 模式"。

工具列表（10个）：
  1. get_my_holdings        — 查自己的持仓（股票+基金）
  2. get_fund_nav           — 查某只基金当前净值
  3. get_market_status      — 查当前市场状态（恐贪+估值分位+大盘）
  4. get_news               — 查最新财经/政策新闻
  5. search_web             — 联网搜索任意问题
  6. get_northbound_flow    — 查北向资金流向
  7. get_macro_data         — 查宏观数据（CPI/PMI/M2/GDP）
  8. get_fund_history_nav   — 查某只基金历史净值走势
  9. get_my_networth        — 查净资产汇总
  10. compare_funds         — 对比两只基金

触发条件：用户问了"后端没有提前准备好答案"的问题
  - 查具体基金净值、涨跌幅
  - 比较多只基金/股票
  - 问某个具体股票/ETF的情况
  - 问我没有录入的品种能否考虑

不触发条件：
  - 简单市场概况（现有 market_ctx 已有）
  - 已有持仓分析（现有 portfolio_ctx 已有）
  - 聊天/新闻（现有路径已有）
"""
import json
import os
from typing import Iterator

import httpx


# ========================================================
# 工具定义（OpenAI Function Calling 格式，DeepSeek 兼容）
# ========================================================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_my_holdings",
            "description": "查询用户在钱袋子中录入的持仓，包括基金和股票的名称、代码、份额、成本。当用户问'我持有什么'、'我的仓位'、'我买了哪些'时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "用户ID"},
                },
                "required": ["user_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fund_nav",
            "description": "查询某只基金的当前净值、近期涨跌幅、成立以来收益。当用户问某只具体基金的净值/涨幅/表现时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "fund_code": {"type": "string", "description": "基金代码，如 006555"},
                    "fund_name": {"type": "string", "description": "基金名称（可选，用于日志）"},
                },
                "required": ["fund_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_status",
            "description": "查询当前市场整体状态：恐贪指数、A股估值分位、大盘涨跌、北向资金。当用户问'市场怎么样'、'现在适合入场吗'时调用。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_news",
            "description": "查询最新财经新闻或政策新闻。当用户问'最新新闻'、'今天有什么消息'、某个话题的最新动态时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "新闻话题，如'A股'、'基金'、'降息'，为空则返回今日综合财经新闻"},
                    "limit": {"type": "integer", "description": "条数，默认5", "default": 5},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "在互联网上搜索最新信息。当用户问某个具体事件、人物、政策的最新动态，或需要实时信息时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索词，精简为核心关键词，如'普京访华'、'降息'"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_northbound_flow",
            "description": "查询北向资金（沪深港通）今日及近期净流入/流出情况。当用户问北向资金、外资动向时调用。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_macro_data",
            "description": "查询宏观经济数据：CPI、PMI、M2、GDP增速、LPR等。当用户问宏观经济形势、通胀、货币政策时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "indicators": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "需要的指标列表，如['CPI','PMI','M2']，为空则返回全部",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fund_history_nav",
            "description": "查询某只基金近期的历史净值走势。当用户问某基金的趋势、是否在高位、回撤多少时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "fund_code": {"type": "string", "description": "基金代码"},
                    "days": {"type": "integer", "description": "查多少天，默认30", "default": 30},
                },
                "required": ["fund_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_my_networth",
            "description": "查询用户的净资产汇总（投资+现金+其他资产）。当用户问'我总共有多少钱'、'净资产'时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "用户ID"},
                },
                "required": ["user_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_funds",
            "description": "对比两只或多只基金的近期表现、费率、风格。当用户问'A基金和B基金哪个好'、'比较这两只基金'时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "fund_codes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要对比的基金代码列表，如['006555','002163']",
                    },
                },
                "required": ["fund_codes"],
            },
        },
    },
]


# ========================================================
# 工具执行器
# ========================================================

def execute_tool(tool_name: str, tool_args: dict, user_id: str) -> str:
    """执行工具调用，返回结果字符串给 LLM。"""
    import sys
    sys.path.insert(0, os.path.dirname(__file__) + "/..")

    try:
        if tool_name == "get_my_holdings":
            return _tool_get_holdings(user_id)

        elif tool_name == "get_fund_nav":
            return _tool_get_fund_nav(tool_args.get("fund_code", ""), tool_args.get("fund_name", ""))

        elif tool_name == "get_market_status":
            return _tool_get_market_status()

        elif tool_name == "get_news":
            return _tool_get_news(tool_args.get("topic", ""), tool_args.get("limit", 5))

        elif tool_name == "search_web":
            return _tool_search_web(tool_args.get("query", ""))

        elif tool_name == "get_northbound_flow":
            return _tool_get_northbound()

        elif tool_name == "get_macro_data":
            return _tool_get_macro(tool_args.get("indicators", []))

        elif tool_name == "get_fund_history_nav":
            return _tool_get_fund_history(tool_args.get("fund_code", ""), tool_args.get("days", 30))

        elif tool_name == "get_my_networth":
            return _tool_get_networth(user_id)

        elif tool_name == "compare_funds":
            return _tool_compare_funds(tool_args.get("fund_codes", []))

        else:
            return f"未知工具: {tool_name}"

    except Exception as e:
        return f"工具执行失败: {e}"


def _tool_get_holdings(user_id: str) -> str:
    try:
        from services.stock_monitor import load_stock_holdings
        from services.fund_monitor import load_fund_holdings
        stocks = load_stock_holdings(user_id) or []
        funds = load_fund_holdings(user_id) or []
        if not stocks and not funds:
            return "当前钱袋子系统中没有持仓记录。"
        lines = ["持仓明细："]
        for s in stocks:
            lines.append(f"  股票：{s.get('name','?')}({s.get('code','')}) {s.get('shares',0)}股 成本¥{s.get('costPrice',0)}")
        for f in funds:
            lines.append(f"  基金：{f.get('name','?')}({f.get('code','')}) {f.get('shares',0)}份 成本净值{f.get('costNav',0)}")
        return "\n".join(lines)
    except Exception as e:
        return f"查询持仓失败: {e}"


def _tool_get_fund_nav(code: str, name: str = "") -> str:
    try:
        from services.market_data import get_fund_nav
        data = get_fund_nav(code)
        if not data or not data.get("available"):
            return f"未找到基金 {code} 的净值数据。"
        label = name or data.get("name", code)
        nav = data.get("nav", 0)
        change = data.get("change_pct", 0)
        date = data.get("date", "")
        return f"{label}({code})：净值 {nav}，当日涨跌 {change:+.2f}%（{date}）"
    except Exception as e:
        return f"查询净值失败({code}): {e}"


def _tool_get_market_status() -> str:
    try:
        from services.market_data import get_fear_greed_index, get_valuation_percentile
        from services.factor_data import get_northbound_flow
        lines = ["当前市场状态："]
        fgi = get_fear_greed_index()
        if fgi.get("available"):
            lines.append(f"  恐贪指数：{fgi.get('score',0)} ({fgi.get('label','')})")
        vp = get_valuation_percentile()
        if vp.get("available"):
            lines.append(f"  A股估值分位：{vp.get('percentile',0):.0f}%")
        nb = get_northbound_flow()
        if nb.get("available"):
            lines.append(f"  北向资金今日：{nb.get('net_flow_today',0):+.1f}亿")
        return "\n".join(lines)
    except Exception as e:
        return f"查询市场状态失败: {e}"


def _tool_get_news(topic: str = "", limit: int = 5) -> str:
    try:
        from services.news_data import get_market_news, get_policy_news
        if topic and any(k in topic for k in ["政策", "降息", "加息", "央行"]):
            news = get_policy_news(limit)
        else:
            news = get_market_news(limit)
        if not news:
            return "暂无最新新闻。"
        lines = ["最新新闻："]
        for n in news[:limit]:
            title = n.get("title", "")
            source = n.get("source", "")
            lines.append(f"  · {title}（{source}）")
        return "\n".join(lines)
    except Exception as e:
        return f"查询新闻失败: {e}"


def _tool_search_web(query: str) -> str:
    try:
        from services.web_search import search_web, format_search_for_prompt
        results = search_web(query, limit=5)
        if not results:
            return f"搜索'{query}'无结果。"
        return format_search_for_prompt(results)
    except Exception as e:
        return f"搜索失败: {e}"


def _tool_get_northbound() -> str:
    try:
        from services.factor_data import get_northbound_flow
        nb = get_northbound_flow()
        if not nb.get("available"):
            return "北向资金数据暂不可用。"
        return (f"北向资金：今日净流入 {nb.get('net_flow_today',0):+.1f}亿，"
                f"5日合计 {nb.get('net_flow_5d',0):+.1f}亿，趋势 {nb.get('trend','')}")
    except Exception as e:
        return f"查询北向资金失败: {e}"


def _tool_get_macro(indicators: list) -> str:
    try:
        from services.macro_data import get_cpi_ppi, get_pmi
        from services.policy_data import get_lpr
        lines = ["宏观经济数据："]
        try:
            cpi = get_cpi_ppi()
            if cpi.get("available"):
                lines.append(f"  CPI同比：{cpi.get('cpi',0)}%（{cpi.get('date','')}）")
        except Exception:
            pass
        try:
            pmi = get_pmi()
            if pmi.get("available"):
                lines.append(f"  制造业PMI：{pmi.get('pmi',0)}（{pmi.get('date','')}）")
        except Exception:
            pass
        return "\n".join(lines) if len(lines) > 1 else "宏观数据暂不可用。"
    except Exception as e:
        return f"查询宏观数据失败: {e}"


def _tool_get_fund_history(code: str, days: int = 30) -> str:
    try:
        from services.fund_monitor import get_fund_nav_history
        history = get_fund_nav_history(code, days)
        if not history:
            return f"基金 {code} 近{days}天净值数据不可用。"
        navs = [h.get("nav", 0) for h in history if h.get("nav")]
        if not navs:
            return "净值数据为空。"
        latest = navs[-1]
        oldest = navs[0]
        change = (latest - oldest) / oldest * 100 if oldest else 0
        max_nav = max(navs)
        min_nav = min(navs)
        drawdown = (latest - max_nav) / max_nav * 100 if max_nav else 0
        return (f"基金 {code} 近{len(navs)}天：最新净值 {latest:.4f}，"
                f"区间涨跌 {change:+.2f}%，最大净值 {max_nav:.4f}，"
                f"当前回撤 {drawdown:.2f}%")
    except Exception as e:
        return f"查询历史净值失败: {e}"


def _tool_get_networth(user_id: str) -> str:
    try:
        from services.unified_networth import calc_unified_networth
        nw = calc_unified_networth(user_id)
        if not nw or nw.get("netWorth", 0) == 0:
            return "净资产数据暂无，请先录入资产和持仓。"
        bk = nw.get("breakdown", {})
        inv = bk.get("investment", {}).get("total", 0)
        cash = bk.get("cash", {}).get("total", 0)
        return (f"净资产：¥{nw['netWorth']:,.0f}（"
                f"投资 ¥{inv:,.0f} + 现金 ¥{cash:,.0f}）")
    except Exception as e:
        return f"查询净资产失败: {e}"


def _tool_compare_funds(fund_codes: list) -> str:
    try:
        from services.market_data import get_fund_nav
        if not fund_codes:
            return "请提供基金代码。"
        lines = ["基金对比："]
        for code in fund_codes[:4]:
            data = get_fund_nav(code)
            if data and data.get("available"):
                name = data.get("name", code)
                nav = data.get("nav", 0)
                change = data.get("change_pct", 0)
                lines.append(f"  {name}({code})：净值{nav}，今日{change:+.2f}%")
            else:
                lines.append(f"  {code}：数据不可用")
        return "\n".join(lines)
    except Exception as e:
        return f"对比失败: {e}"


# ========================================================
# FC Agent 循环（流式）
# ========================================================

def run_fc_agent_stream(
    user_msg: str,
    system_prompt: str,
    user_id: str,
    model: str = "deepseek-v4-flash",
    history: list | None = None,
    max_rounds: int = 4,
) -> Iterator[dict]:
    """运行 Function Calling Agent 循环，yield SSE 格式 dict。

    流程：
      1. 发消息给 LLM（带 tools）
      2. 如果 LLM 返回 tool_calls → 执行工具 → 自我纠正检测 → 把结果加回 messages → 再次调用
      3. 如果 LLM 返回正常文本 → 流式 yield 给前端
      最多循环 max_rounds 次防止死循环。

    自我纠正：工具返回"数据不可用/失败/无结果"时，自动注入提示让 LLM 用 search_web 补充。
    """
    api_key = os.environ.get("LLM_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    api_base = os.environ.get("LLM_API_BASE", "https://api.deepseek.com/v1")
    if not api_key:
        yield {"delta": "AI 暂时不可用（API Key 未配置）", "done": True, "source": "error"}
        return

    # 构建初始 messages
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    if history:
        for h in history[-8:]:
            role = h.get("role") if isinstance(h, dict) else h.role
            content = h.get("content") if isinstance(h, dict) else h.content
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_msg})

    for round_num in range(max_rounds):
        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(
                    f"{api_base}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": messages,
                        "tools": TOOLS,
                        "tool_choice": "auto",
                        "max_tokens": 1200,
                        "temperature": 0.7,
                        "stream": False,  # FC 先用非流式，拿到 tool_calls 后再流式
                    },
                )

            if resp.status_code != 200:
                yield {"delta": f"API 错误 {resp.status_code}", "done": True, "source": "error"}
                return

            data = resp.json()
            choice = data.get("choices", [{}])[0]
            message = choice.get("message", {})
            finish_reason = choice.get("finish_reason", "stop")

            # ---- 情况 A：LLM 要调工具 ----
            if finish_reason == "tool_calls" and message.get("tool_calls"):
                tool_calls = message["tool_calls"]

                # 先把 assistant message（含 tool_calls）加入 messages
                messages.append(message)

                # 通知前端正在调工具
                for tc in tool_calls:
                    fn_name = tc.get("function", {}).get("name", "")
                    fn_label = {
                        "get_my_holdings": "查询持仓",
                        "get_fund_nav": "查询基金净值",
                        "get_market_status": "查询市场状态",
                        "get_news": "查询最新新闻",
                        "search_web": "联网搜索",
                        "get_northbound_flow": "查询北向资金",
                        "get_macro_data": "查询宏观数据",
                        "get_fund_history_nav": "查询历史净值",
                        "get_my_networth": "查询净资产",
                        "compare_funds": "对比基金",
                    }.get(fn_name, fn_name)
                    yield {
                        "type": "tool_call",
                        "tool": fn_name,
                        "label": f"🔧 {fn_label}...",
                        "done": False,
                        "delta": "",
                        "phase": "tool_call",
                    }

                # 执行工具，把结果加回 messages
                _has_weak_result = False  # 标记是否有"数据不可用"的工具结果
                _weak_topics = []          # 哪些主题拿不到数据，待用 search_web 补充

                for tc in tool_calls:
                    fn_name = tc["function"]["name"]
                    try:
                        fn_args = json.loads(tc["function"]["arguments"])
                    except Exception:
                        fn_args = {}

                    result = execute_tool(fn_name, fn_args, user_id)

                    # ★ 自我纠正检测：工具返回"不可用/失败/无数据"时标记
                    _WEAK_SIGNALS = ["不可用", "失败", "无结果", "暂无", "不存在", "没有记录",
                                     "数据为空", "not available", "error", "暂不可用"]
                    if any(s in result for s in _WEAK_SIGNALS) and fn_name != "search_web":
                        _has_weak_result = True
                        # 提取搜索关键词：从用户消息或工具参数中取
                        _q = fn_args.get("fund_code") or fn_args.get("query") or user_msg[:20]
                        _weak_topics.append(_q)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    })

                # ★ 自我纠正：有工具数据不足时，注入提示让 LLM 用 search_web 补充
                if _has_weak_result and _weak_topics and round_num < max_rounds - 1:
                    _supplement_hint = (
                        f"注意：上述部分工具返回了空数据。"
                        f"请使用 search_web 工具搜索 '{_weak_topics[0]}' 来补充获取实时信息，"
                        f"然后综合所有数据给出回答。"
                    )
                    messages.append({"role": "user", "content": _supplement_hint})
                    yield {
                        "type": "tool_call",
                        "tool": "search_web",
                        "label": "🔍 数据不足，补充联网搜索...",
                        "done": False,
                        "delta": "",
                        "phase": "tool_call",
                    }

                # 继续下一轮
                continue

            # ---- 情况 B：LLM 返回正常回答 ----
            content = message.get("content", "")
            if content:
                # 流式逐块 yield（把完整 content 分段，模拟打字效果）
                chunk_size = 20
                for i in range(0, len(content), chunk_size):
                    yield {
                        "delta": content[i:i+chunk_size],
                        "done": False,
                        "phase": "answering",
                        "source": "ai",
                    }
                yield {"delta": "", "done": True, "source": "ai", "served_by": "fc_agent"}
                return

            # 空回复，退出
            yield {"delta": "（AI 未返回内容）", "done": True, "source": "error"}
            return

        except Exception as e:
            print(f"[FC_AGENT] round {round_num} error: {e}")
            yield {"delta": f"工具调用出错: {e}", "done": True, "source": "error"}
            return

    # 超出最大轮次
    yield {"delta": "（工具调用轮次超限）", "done": True, "source": "error"}


# ========================================================
# FC 触发检测：决定是否走 FC 路径
# ========================================================

# 这些问题用现有管道模式完全够，不走 FC（避免浪费 token）
_FC_BLACKLIST_KW = [
    "你好", "帮我", "谢谢", "现在能进场吗", "入场时机",
    "什么时候买", "定投", "止盈", "止损",
]

# 这些问题需要 FC（LLM 需要主动查数据）
_FC_TRIGGER_KW = [
    # 比较类
    "哪个好", "比一比", "对比", "比较", "哪只更好",
    # 查具体品种（不在持仓里的）
    "基金代码", "净值多少", "今天涨了多少", "跌了多少",
    # 超出现有数据范围
    "推荐", "帮我选", "找一只", "有没有", "值不值得买",
    # 具体分析
    "回撤多少", "历史表现", "最近趋势",
]


def should_use_fc(user_msg: str, intent: str) -> bool:
    """判断是否应该走 Function Calling 路径。

    原则：FC 只用于"后端没有预先准备好答案的问题"，避免过度调用。
    """
    msg_lower = user_msg.lower()

    # 黑名单：明确不走 FC
    if any(k in msg_lower for k in _FC_BLACKLIST_KW):
        return False

    # 白名单：明确走 FC
    if any(k in msg_lower for k in _FC_TRIGGER_KW):
        return True

    # 问到不在持仓里的具体基金/股票（6位数字代码 pattern）
    import re
    if re.search(r'\b\d{6}\b', user_msg):
        return True

    return False

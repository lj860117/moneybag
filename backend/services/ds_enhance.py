"""
钱袋子 — DeepSeek 智能增强层
职责：为各模块提供 DeepSeek LLM 增强（一句话点评/智能建议/个性化分析）
独立 service，不扎堆 main.py

增强点：
  1. 资产页存款建议
  2. 选基/选股一句话点评
  3. 首页"今日关注"
  4. 风控新闻事件联动
  5. 每日信号解读
  6. 新闻影响深度分析
  7. 配置建议增强
"""
import os
import json
import time
import httpx
from pathlib import Path

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "ds_enhance",
    "scope": "public",
    "input": [],
    "output": "ai_comments",
    "cost": "llm_light",
    "tags": ["DS增强", "AI点评", "今日关注", "新闻风控"],
    "description": "DeepSeek智能增强层：点评/建议/关注/风控/信号解读/影响分析",
    "layer": "output",
    "priority": 8,
}

# ---- DeepSeek 调用基础设施 ----

_DS_CACHE = {}  # 短期缓存（避免重复调用）
_DS_CACHE_TTL = 600  # 10 分钟


def _call_deepseek(prompt: str, system: str = "", max_tokens: int = 300, cache_key: str = "") -> str | None:
    """统一的 DeepSeek 同步调用（短文本场景，非聊天）"""
    if cache_key:
        now = time.time()
        if cache_key in _DS_CACHE and now - _DS_CACHE[cache_key]["ts"] < _DS_CACHE_TTL:
            return _DS_CACHE[cache_key]["text"]

    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "deepseek-chat",
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": 0.7,
                },
            )
            if resp.status_code == 200:
                text = resp.json()["choices"][0]["message"]["content"]
                if cache_key:
                    _DS_CACHE[cache_key] = {"text": text, "ts": time.time()}
                return text
            else:
                print(f"[DS_ENHANCE] API error: {resp.status_code} {resp.text[:100]}")
    except Exception as e:
        print(f"[DS_ENHANCE] Call failed: {e}")
    return None


# ============================================================
# 1. 资产页存款智能建议
# ============================================================

def analyze_idle_cash(cash_amount: float, monthly_expense: float = 0, risk_profile: str = "稳健型") -> dict:
    """
    分析闲置资金，给出配置建议
    cash_amount: 银行存款总额（元）
    monthly_expense: 月均支出（元），用于计算应急储备
    risk_profile: 用户风险类型
    """
    if cash_amount <= 0:
        return {"advice": "暂无存款数据", "source": "none"}

    # 计算应急储备（3-6 个月支出）
    if monthly_expense > 0:
        emergency_fund = monthly_expense * 6
        idle_cash = max(0, cash_amount - emergency_fund)
    else:
        # 没有支出数据，按 30% 作为应急
        emergency_fund = cash_amount * 0.3
        idle_cash = cash_amount * 0.7

    # 银行活期损失计算
    bank_rate = 0.002  # 活期 0.2%
    inflation = 0.01  # 通胀约 1%
    annual_loss = cash_amount * (inflation - bank_rate)

    # 构建 prompt
    prompt = f"""用户有 ¥{cash_amount:,.0f} 银行存款，风险类型：{risk_profile}。
应急储备约 ¥{emergency_fund:,.0f}，闲置资金约 ¥{idle_cash:,.0f}。
银行活期利率 0.2%，通胀约 1%，每年实际购买力损失约 ¥{annual_loss:,.0f}。

请用 3-4 句话给出建议：
1. 点明"钱在贬值"的事实
2. 应急储备留多少在余额宝
3. 闲置部分如何配置（债基/定投/其他）
4. 具体基金类型建议（不推具体代码）

要求：通俗易懂，像朋友聊天。"""

    result = _call_deepseek(
        prompt,
        system="你是理财顾问，擅长帮用户优化闲置资金配置。回答简短实用。",
        max_tokens=250,
        cache_key=f"idle_cash_{int(cash_amount)}_{risk_profile}",
    )

    return {
        "advice": result or f"你的 ¥{cash_amount:,.0f} 存款放银行活期年利率仅 0.2%，跑输通胀 1%，每年实际损失约 ¥{annual_loss:,.0f}。建议：保留 ¥{emergency_fund:,.0f} 在余额宝作应急，剩余 ¥{idle_cash:,.0f} 可考虑配置债基(年化 3-4%) + 宽基定投(长期 8-15%)。",
        "source": "ai" if result else "rules",
        "cashAmount": cash_amount,
        "emergencyFund": round(emergency_fund, 0),
        "idleCash": round(idle_cash, 0),
        "annualLoss": round(annual_loss, 0),
        "bankRate": bank_rate,
        "inflation": inflation,
    }


# ============================================================
# 2. 选基/选股一句话点评
# ============================================================

def comment_fund_picks(funds: list) -> list:
    """给选基 TOP 列表加 DeepSeek 一句话点评"""
    if not funds:
        return funds

    # 只给前 5 只点评（控制 token）
    top5 = funds[:5]
    fund_desc = "\n".join([
        f"{i+1}. {f['name']}({f['code']}) 近1年{f['returns'].get('1y','N/A')}% 评分{f['score']}"
        for i, f in enumerate(top5)
    ])

    prompt = f"""以下是 AI 选出的 TOP 5 基金：
{fund_desc}

为每只基金写一句话点评（15字以内），格式：
1. 点评内容
2. 点评内容
...

要求：说人话，突出核心优势或风险。"""

    result = _call_deepseek(
        prompt,
        system="你是基金分析师，点评简短犀利。",
        max_tokens=200,
        cache_key=f"fund_comment_{funds[0]['code'] if funds else ''}",
    )

    if result:
        lines = [l.strip() for l in result.strip().split("\n") if l.strip()]
        for i, f in enumerate(top5):
            if i < len(lines):
                # 去掉序号前缀
                comment = lines[i].lstrip("0123456789.、）) ").strip()
                f["aiComment"] = comment
    return funds


def comment_recommend_funds(allocations: list, risk_profile: str,
                            val_pct: float, fgi: float) -> dict:
    """给推荐配置的每只基金生成 AI 一句话点评（基于当前市场环境）"""
    fund_desc = "\n".join([
        f"{i+1}. {a['fullName']}({a['code']}) 占比{a['pct']}% 类别:{a.get('category','')}"
        for i, a in enumerate(allocations) if a.get('code') != '余额宝'
    ])

    prompt = f"""用户是「{risk_profile}」投资者。当前市场：
- 沪深300估值百分位：{val_pct:.0f}%（{'高估' if val_pct > 70 else '低估' if val_pct < 30 else '适中'}）
- 恐贪指数：{fgi:.0f}（{'贪婪' if fgi > 60 else '恐惧' if fgi < 40 else '中性'}）

推荐配置的基金：
{fund_desc}

为每只基金写一句话点评（20字以内），说明当前市场环境下这只基金的优势或注意点。格式：
1. 点评
2. 点评
...

要求：结合估值和恐贪数据，说人话。"""

    result = _call_deepseek(
        prompt,
        system="你是资产配置顾问，点评简短实用，结合市场环境。",
        max_tokens=250,
        cache_key=f"rec_fund_{risk_profile}_{int(val_pct)}_{int(fgi)}",
    )

    comments = {}
    if result:
        lines = [l.strip() for l in result.strip().split("\n") if l.strip()]
        non_cash = [a for a in allocations if a.get('code') != '余额宝']
        for i, a in enumerate(non_cash):
            if i < len(lines):
                comment = lines[i].lstrip("0123456789.、）) ").strip()
                comments[a["code"]] = comment
    return comments


def comment_stock_picks(stocks: list) -> list:
    """给选股 TOP 列表加 DeepSeek 一句话点评"""
    if not stocks:
        return stocks

    top5 = stocks[:5]
    stock_desc = "\n".join([
        f"{i+1}. {s['name']}({s['code']}) PE={s.get('pe','N/A')} 评分{s['score']}"
        for i, s in enumerate(top5)
    ])

    prompt = f"""以下是 AI 选出的 TOP 5 股票：
{stock_desc}

为每只股票写一句话点评（15字以内），格式同上。
要求：说人话，突出核心逻辑。"""

    result = _call_deepseek(
        prompt,
        system="你是 A 股分析师，点评简短犀利。",
        max_tokens=200,
        cache_key=f"stock_comment_{stocks[0]['code'] if stocks else ''}",
    )

    if result:
        lines = [l.strip() for l in result.strip().split("\n") if l.strip()]
        for i, s in enumerate(top5):
            if i < len(lines):
                comment = lines[i].lstrip("0123456789.、）) ").strip()
                s["aiComment"] = comment
    return stocks


# ============================================================
# 3. 首页"今日关注"
# ============================================================

def generate_daily_focus(market_ctx: str, portfolio_ctx: str = "") -> dict:
    """生成首页个性化'今日关注'卡片"""
    prompt = f"""根据以下市场数据，生成 3 条"今日关注"提示（每条 20 字以内）：

{market_ctx[:800]}

{portfolio_ctx[:400] if portfolio_ctx else ''}

格式：
1. [emoji] 提示内容
2. [emoji] 提示内容
3. [emoji] 提示内容

要求：实用具体，不说废话，像手机推送通知。"""

    result = _call_deepseek(
        prompt,
        system="你是投资提醒助手，生成简短实用的每日关注。",
        max_tokens=150,
        cache_key=f"daily_focus_{time.strftime('%Y%m%d_%H')}",
    )

    if result:
        lines = [l.strip() for l in result.strip().split("\n") if l.strip()]
        tips = [l.lstrip("0123456789.、）) ").strip() for l in lines[:3]]
    else:
        tips = ["📊 查看最新市场数据", "🔍 检查持仓异动", "💡 AI 分析师在线"]

    return {"tips": tips, "source": "ai" if result else "default"}


# ============================================================
# 4. 风控新闻联动
# ============================================================

def assess_news_risk(headlines: list, holdings_summary: str = "") -> dict:
    """评估新闻事件对持仓的风控影响"""
    if not headlines:
        return {"riskLevel": "normal", "alerts": [], "source": "none"}

    news_text = "\n".join([f"- {h}" for h in headlines[:8]])
    prompt = f"""以下是最新新闻：
{news_text}

用户持仓：{holdings_summary or '未知'}

请评估这些新闻对用户持仓的风险影响，返回 JSON：
{{"riskLevel": "normal/elevated/high/critical", "alerts": ["简短警报1","简短警报2"], "summary": "一句话总结"}}
只返回 JSON。"""

    result = _call_deepseek(prompt, max_tokens=200, cache_key=f"news_risk_{hash(news_text[:100])}")

    if result:
        try:
            import re
            m = re.search(r'\{[^}]+\}', result, re.DOTALL)
            if m:
                parsed = json.loads(m.group())
                return {**parsed, "source": "ai"}
        except Exception:
            pass

    return {"riskLevel": "normal", "alerts": [], "summary": "暂无异常", "source": "rules"}


# ============================================================
# 5. 每日信号解读
# ============================================================

def interpret_daily_signal(signal_data: dict) -> str:
    """把 12 维信号数据转为人话解读"""
    if not signal_data:
        return "暂无信号数据"

    score = signal_data.get("score", 0)
    signal = signal_data.get("signal", "观望")
    factors = signal_data.get("factors", {})

    factor_lines = "\n".join([f"- {k}: {v}" for k, v in factors.items()]) if factors else "无详细因子"

    prompt = f"""综合信号评分: {score}/100，信号: {signal}
因子详情:
{factor_lines}

用 2-3 句话解读这个信号，说清楚：当前市场偏多还是偏空？关键驱动因子是什么？普通投资者该怎么做？"""

    result = _call_deepseek(
        prompt,
        system="你是量化分析师，把复杂信号翻译成人话。",
        max_tokens=200,
        cache_key=f"signal_interp_{score}_{signal}",
    )

    return result or f"综合评分 {score}/100，信号: {signal}。建议关注关键因子变化。"


# ============================================================
# 6. 新闻影响深度分析
# ============================================================

def deep_analyze_news_impact(news_items: list, holdings: list = None) -> list:
    """DeepSeek 深度分析新闻→行业→持仓影响链"""
    if not news_items:
        return []

    titles = [n.get("title", "") for n in news_items[:6]]
    news_text = "\n".join([f"{i+1}. {t}" for i, t in enumerate(titles)])

    holdings_text = ""
    if holdings:
        holdings_text = "\n用户持仓：" + ", ".join([h.get("name", h.get("code", "")) for h in holdings[:10]])

    prompt = f"""分析以下新闻对 A 股的影响：
{news_text}
{holdings_text}

对每条重要新闻，给出：
1. 影响的行业/板块
2. 利好还是利空
3. 影响程度（大/中/小）
4. 对用户持仓的具体影响（如有）

用简短 JSON 数组格式返回：
[{{"title":"新闻摘要","sectors":["行业"],"direction":"bullish/bearish/neutral","magnitude":"high/medium/low","impact":"一句话影响"}}]
只返回 JSON 数组。"""

    result = _call_deepseek(prompt, max_tokens=400, cache_key=f"news_deep_{hash(news_text[:100])}")

    if result:
        try:
            import re
            m = re.search(r'\[.*\]', result, re.DOTALL)
            if m:
                return json.loads(m.group())
        except Exception:
            pass

    return []


# ============================================================
# 7. 配置建议增强（接入新闻/行业数据）
# ============================================================

def enhance_allocation_advice(base_advice: dict, market_ctx: str = "", news_summary: str = "") -> dict:
    """在 Level 1 配置建议基础上，DeepSeek 增加新闻/行业维度"""
    target = base_advice.get("target", {})
    summary = base_advice.get("summary", "")
    adjustments = base_advice.get("adjustments", [])

    if not news_summary and not market_ctx:
        return base_advice

    prompt = f"""当前资产配置建议：股票{target.get('stock',50)}%/债券{target.get('bond',30)}%/现金{target.get('cash',20)}%
调整原因：{', '.join(adjustments) if adjustments else '无特殊调整'}

最新市场/新闻信息：
{(news_summary or market_ctx)[:500]}

请用 1-2 句话补充：结合最新新闻/事件，这个配置是否需要微调？有什么特别需要注意的？"""

    result = _call_deepseek(
        prompt,
        system="你是资产配置顾问。",
        max_tokens=150,
        cache_key=f"alloc_enhance_{target.get('stock',0)}_{time.strftime('%Y%m%d_%H')}",
    )


# ============================================================
# 8. 资产诊断 — 全量分析用户资产结构，给出个性化建议
# ============================================================

def diagnose_user_assets(user_id: str) -> dict:
    """
    读取用户全部资产（现金/房产/负债/投资持仓），
    结合市场数据，给出资产结构诊断和优化建议
    """
    # 获取统一净资产数据
    try:
        from services.unified_networth import calc_unified_networth
        nw = calc_unified_networth(user_id)
    except Exception:
        return {"source": "error", "advice": "资产数据加载失败"}

    if nw.get("netWorth", 0) == 0:
        return {"source": "rule", "advice": "您还没有添加资产，添加后 AI 会自动诊断您的资产健康状况。"}

    breakdown = nw.get("breakdown", {})
    allocation = nw.get("allocation", {})

    # 构建资产摘要
    summary_parts = []
    summary_parts.append(f"净资产: ¥{nw['netWorth']:,.0f}")
    if breakdown.get("investment", {}).get("total", 0):
        inv = breakdown["investment"]
        summary_parts.append(f"投资: ¥{inv['total']:,.0f}(股{inv.get('stockCount',0)}只+基{inv.get('fundCount',0)}只)")
    if breakdown.get("cash", {}).get("total", 0):
        summary_parts.append(f"现金: ¥{breakdown['cash']['total']:,.0f}")
    if breakdown.get("property", {}).get("total", 0):
        summary_parts.append(f"房产: ¥{breakdown['property']['total']:,.0f}")
    if breakdown.get("liability", {}).get("total", 0):
        summary_parts.append(f"负债: ¥{breakdown['liability']['total']:,.0f}")
    if allocation:
        alloc_str = " | ".join(f"{k}:{v:.0f}%" for k, v in allocation.items() if v > 0)
        summary_parts.append(f"配置: {alloc_str}")

    health_issues = nw.get("healthIssues", [])
    if health_issues:
        summary_parts.append(f"健康问题: {'; '.join(health_issues)}")

    # 拉持仓新闻
    holdings_news_text = ""
    try:
        from services.stock_monitor import load_stock_holdings
        from services.fund_monitor import load_fund_holdings
        from services.news_data import get_holdings_news, format_holdings_news_for_prompt
        stock_h = load_stock_holdings(user_id) or []
        fund_h = load_fund_holdings(user_id) or []
        if stock_h or fund_h:
            h_news = get_holdings_news(stock_h, fund_h, limit_per=2)
            holdings_news_text = format_holdings_news_for_prompt(h_news)
    except Exception:
        pass

    prompt = f"""用户资产概况：
{chr(10).join(summary_parts)}
{f'{chr(10)}持仓相关新闻：{chr(10)}{holdings_news_text}' if holdings_news_text else ''}

请基于以上数据，给出简洁的资产诊断（3-5条要点，每条一行，带emoji）：
1. 资产结构是否合理？（房产占比过高？投资过于集中？）
2. 现金是否闲置过多？利率损失估算
3. 负债风险（负债率是否过高？月供压力？）
4. 具体优化建议（增加什么/减少什么）
5. 下一步行动建议

保持简洁、直接、有数据支撑。总共不超过200字。"""

    result = _call_deepseek(
        prompt=prompt,
        system="你是资深理财规划师。用户给你看他的资产全貌，你要像医生问诊一样给出精准诊断。",
        max_tokens=300,
        cache_key=f"asset_diag_{user_id}_{time.strftime('%Y%m%d_%H')}",
    )
    if result:
        return {"source": "ai", "advice": result}
    return {"source": "rule", "advice": f"您的净资产 ¥{nw['netWorth']:,.0f}，健康评分 {nw.get('healthScore', 0)} 分。" + (f" 问题：{'; '.join(health_issues)}" if health_issues else "")}

    if result:
        base_advice["aiEnhancement"] = result
        base_advice["enhanced"] = True

    return base_advice

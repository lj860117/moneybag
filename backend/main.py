"""
钱袋子 — FastAPI 后端 V5.0（模块化重构）
路由入口 + 中间件配置，业务逻辑在 services/ 中
"""
import os
import sys
import json
import time
import uuid
import hashlib
import math
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Literal

# 确保能导入同级模块
sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse

# ---- 从 services 导入业务逻辑 ----
from config import DATA_DIR, USERS_DIR, RECEIPTS_DIR
from models.schemas import (
    Holding, Portfolio, Transaction, Asset, PortfolioV4,
    TransactionRequest, AssetRequest, TopupRequest, UserData,
    ChatRequest, LedgerEntry, FundSearchResult,
    IncomeSourceCreate, IncomeSourceRecord,
)
from services.data_layer import (
    get_fund_nav, get_fear_greed_index, get_valuation_percentile,
    get_technical_indicators, get_fund_news, get_market_news,
    get_macro_calendar, get_northbound_flow, get_margin_trading,
    get_treasury_yield, get_shibor, get_dividend_yield,
    get_news_sentiment_score, calc_rsi, calc_macd, calc_bollinger,
    get_fund_dynamic_info, _load_fund_rank_data,
    get_policy_news, analyze_news_impact, _get_nav_on_date,
    get_main_money_flow, get_stock_financials, get_fund_holding_detail,
)
from services.portfolio_calc import (
    calc_holdings_from_transactions, migrate_v3_to_v4, ensure_v4_portfolio,
)
from services.signal import (
    generate_daily_signal, _apply_master_strategies,
    calc_smart_dca, calc_take_profit_strategy,
)
from services.risk import calc_risk_metrics, generate_risk_actions
from services.portfolio import generate_allocation_advice, get_recommend_allocations
from services.fund_screen import screen_funds
from services.stock_screen import screen_stocks
from services.backtest import run_backtest
from services.persistence import load_user, save_user, _user_file

# ---- 缓存兼容（路由中直接引用的缓存变量）----
from services.data_layer import (
    _nav_cache as nav_cache,
    _news_cache as news_cache,
    _macro_cache as macro_cache,
    _fund_rank_cache as fund_rank_cache,
)

# ---- FastAPI 应用 ----
app = FastAPI(title="钱袋子 API", version="6.0.0-phase0")

app.add_middleware(GZipMiddleware, minimum_size=1000)  # >1KB 自动 gzip
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- API 路由 ----

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


# ---- 可用模型列表（前端下拉选择）----
AVAILABLE_MODELS = [
    {"id": "deepseek-chat", "name": "DeepSeek V3", "provider": "deepseek", "base": "https://api.deepseek.com/v1", "env_key": "LLM_API_KEY"},
    {"id": "deepseek-reasoner", "name": "DeepSeek R1 (深度思考)", "provider": "deepseek", "base": "https://api.deepseek.com/v1", "env_key": "LLM_API_KEY"},
]

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


@app.post("/api/signals")
def get_signals(portfolio: Portfolio):
    """根据持仓生成买卖信号（含入场时机/止盈止损/智能定投）"""
    signals = []

    if not portfolio.holdings:
        return signals

    total_amount = sum(h.amount for h in portfolio.holdings)
    if total_amount <= 0:
        return signals

    # 1. 🎯 入场时机 — 基于估值百分位
    val = get_valuation_percentile()
    if val["percentile"] < 30:
        signals.append({
            "icon": "🟢",
            "title": f"当前是好的入场时机！",
            "message": f"{val['index']}估值百分位 {val['percentile']}%（{val['level']}），处于近3年较低水平。历史上低估区间买入，持有3年盈利概率超85%。现在入场性价比高。",
            "type": "timing",
            "severity": "opportunity",
        })
    elif val["percentile"] < 50:
        signals.append({
            "icon": "🟡",
            "title": "入场时机尚可",
            "message": f"{val['index']}估值百分位 {val['percentile']}%（偏低估），不算贵也不算便宜。适合正常定投节奏入场，不用急着一把梭。",
            "type": "timing",
            "severity": "info",
        })
    elif val["percentile"] >= 70:
        signals.append({
            "icon": "🔴",
            "title": "现在入场要谨慎",
            "message": f"{val['index']}估值百分位 {val['percentile']}%（{val['level']}），处于近3年较高水平。建议不要一次性大额买入，可以用定投慢慢建仓，或等回调。",
            "type": "timing",
            "severity": "warning",
        })

    # 2. 💰 止盈止损策略
    profile_name = portfolio.profile or "平衡型"
    total_cost = sum(h.amount for h in portfolio.holdings)
    # 尝试计算当前总市值（简化：用各基金净值估算）
    total_market = 0
    can_calc = False
    for h in portfolio.holdings:
        if h.code == "余额宝":
            total_market += h.amount
            continue
        nav_info = get_fund_nav(h.code)
        if nav_info and nav_info["nav"] != "N/A":
            buy_nav = _get_nav_on_date(h.code, h.buyDate) if h.buyDate else None
            if buy_nav and buy_nav > 0:
                current_nav = float(nav_info["nav"])
                growth = (current_nav - buy_nav) / buy_nav
                total_market += h.amount * (1 + growth)
                can_calc = True
            else:
                total_market += h.amount
        else:
            total_market += h.amount

    if can_calc and total_cost > 0:
        tp = calc_take_profit_strategy(total_cost, total_market, profile_name)
        icon_map = {
            "reached_target": "🎯",
            "partial_profit": "📈",
            "stop_loss": "🚨",
            "in_loss": "📉",
            "holding": "💎",
        }
        signals.append({
            "icon": icon_map.get(tp["status"], "💰"),
            "title": f"止盈止损 | 目标+{tp['targetPct']}% / 止损{tp['stopLossPct']}%",
            "message": tp["action"],
            "type": "take_profit",
            "severity": "opportunity" if tp["status"] == "reached_target" else "warning" if tp["status"] == "stop_loss" else "info",
        })

    # 3. 🧠 智能定投建议
    monthly_invest = total_amount * 0.1  # 月定投基准=总额10%
    smart_dca = calc_smart_dca(monthly_invest, val["percentile"])
    signals.append({
        "icon": "🧠",
        "title": f"智能定投：本月建议 ¥{smart_dca['smartAmount']:,.0f}",
        "message": f"基准定投 ¥{smart_dca['baseAmount']:,.0f}，{smart_dca['advice']}（估值{val['percentile']}%）。智能定投核心：低估多买、高估少买，长期能比固定定投多赚15-20%。",
        "type": "smart_dca",
        "severity": "info",
    })

    # 4. 再平衡信号：检查各资产偏离度
    for h in portfolio.holdings:
        current_pct = h.amount / total_amount * 100
        deviation = abs(current_pct - h.targetPct)
        if deviation > 5:
            direction = "偏多" if current_pct > h.targetPct else "偏少"
            signals.append({
                "icon": "⚖️",
                "title": f"{h.category}需要再平衡",
                "message": f"当前占比 {current_pct:.1f}%，目标 {h.targetPct}%，{direction} {deviation:.1f}%。建议调整。",
                "type": "rebalance",
                "severity": "warning",
            })

    # 5. 恐惧贪婪信号（增强版3维）
    fgi_data = get_fear_greed_index()
    fgi = fgi_data["score"]
    fgi_level = fgi_data["level"]
    dims = fgi_data.get("dimensions", {})
    dim_text = "、".join([f"{d['label']}{d['value']}" for d in dims.values()]) if dims else ""
    if fgi >= 75:
        signals.append({
            "icon": "😱",
            "title": f"市场{fgi_level} — 可能是加仓机会",
            "message": f"恐惧贪婪指数 {fgi:.0f}/100（{fgi_level}）。{dim_text}。历史上极度恐惧时买入，长期收益概率较高。考虑用货币基金的弹药适当加仓。",
            "type": "fear",
            "severity": "opportunity",
        })
    elif fgi <= 25:
        signals.append({
            "icon": "🤑",
            "title": f"市场{fgi_level} — 注意风险",
            "message": f"恐惧贪婪指数 {fgi:.0f}/100（{fgi_level}）。{dim_text}。市场可能过热，建议不要追高，保持定投节奏即可。",
            "type": "greed",
            "severity": "warning",
        })

    # 6. 持仓时间检查
    if portfolio.holdings and portfolio.holdings[0].buyDate:
        try:
            buy_date = datetime.fromisoformat(portfolio.holdings[0].buyDate.replace("Z", "+00:00"))
            days_held = (datetime.now(buy_date.tzinfo) - buy_date).days
            if days_held < 30:
                signals.append({
                    "icon": "⏰",
                    "title": "耐心持有",
                    "message": f"你才持有 {days_held} 天，投资是长跑。至少 3 年才能看到复利效果，别被短期波动影响心态。",
                    "type": "patience",
                    "severity": "info",
                })
        except Exception:
            pass

    return signals


# ---- API: 入场时机 & 智能定投 & 止盈止损 独立接口 ----

@app.get("/api/timing")
def get_timing_advice():
    """获取当前入场时机建议"""
    val = get_valuation_percentile()
    fgi_data = get_fear_greed_index()
    fgi = fgi_data["score"]

    # 综合评分：0-100，越低越适合买入
    timing_score = val["percentile"] * 0.6 + (100 - fgi) * 0.4

    if timing_score < 30:
        verdict = "🟢 非常适合入场"
        detail = "估值低 + 市场恐惧，历史上是最佳买入窗口。"
    elif timing_score < 50:
        verdict = "🟡 适合定投入场"
        detail = "估值合理，适合按计划定投，不建议一次性大额买入。"
    elif timing_score < 70:
        verdict = "🟠 谨慎入场"
        detail = "估值偏高，建议降低定投金额，等更好的机会。"
    else:
        verdict = "🔴 不建议入场"
        detail = "估值高 + 市场贪婪，建议暂停买入，持有现金等待回调。"

    return {
        "timingScore": round(timing_score, 1),
        "verdict": verdict,
        "detail": detail,
        "valuation": val,
        "fearGreed": fgi_data,
    }


@app.post("/api/smart-dca")
def get_smart_dca(portfolio: Portfolio):
    """获取智能定投建议"""
    total = sum(h.amount for h in portfolio.holdings) if portfolio.holdings else 0
    base = total * 0.1 if total > 0 else 1000  # 默认基准1000
    val = get_valuation_percentile()
    result = calc_smart_dca(base, val["percentile"])
    result["valuation"] = val
    return result


@app.post("/api/take-profit")
def get_take_profit(portfolio: Portfolio):
    """获取止盈止损建议"""
    profile = portfolio.profile or "平衡型"
    total_cost = sum(h.amount for h in portfolio.holdings) if portfolio.holdings else 0
    if total_cost <= 0:
        return {"message": "还没有持仓，买入后才能计算止盈止损策略。"}

    # 计算总市值
    total_market = 0
    for h in portfolio.holdings:
        if h.code == "余额宝":
            total_market += h.amount
            continue
        nav_info = get_fund_nav(h.code)
        if nav_info and nav_info["nav"] != "N/A":
            buy_nav = _get_nav_on_date(h.code, h.buyDate) if h.buyDate else None
            if buy_nav and buy_nav > 0:
                growth = (float(nav_info["nav"]) - buy_nav) / buy_nav
                total_market += h.amount * (1 + growth)
            else:
                total_market += h.amount
        else:
            total_market += h.amount

    return calc_take_profit_strategy(total_cost, total_market, profile)


# ---- API: 新闻资讯 ----

@app.get("/api/news/portfolio")
def get_portfolio_news():
    """获取所有持仓基金的相关新闻"""
    codes = ["110020", "050025", "217022", "000216", "008114"]
    result = {}
    for code in codes:
        result[code] = get_fund_news(code, 3)
    return result


@app.get("/api/news/{code}")
def get_news_by_fund(code: str, limit: int = 3):
    """获取单只基金相关新闻"""
    return {"code": code, "news": get_fund_news(code, limit)}


@app.get("/api/news")
def get_all_news(limit: int = 10):
    """获取综合市场新闻"""
    return {"news": get_market_news(limit)}


# ---- 基金动态数据（逻辑在 services/fund_rank.py）----

@app.get("/api/fund/info/{code}")
def get_fund_info(code: str):
    """获取基金动态信息（收益率、净值等，数据来源：天天基金排行榜）"""
    return get_fund_dynamic_info(code)


@app.get("/api/fund/info-batch")
def get_fund_info_batch(codes: str = ""):
    """批量获取基金动态信息，codes 用逗号分隔"""
    if not codes:
        return {"funds": {}}
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    result = {}
    for code in code_list:
        result[code] = get_fund_dynamic_info(code)
    return {"funds": result, "updatedAt": datetime.now().strftime("%Y-%m-%d")}


# ---- 政策 & 国际新闻 + 新闻影响分析（逻辑在 services/news_data.py）----

@app.get("/api/news/policy")
def get_policy_news_api(limit: int = 8):
    """获取政策 & 国际新闻（政府经济政策 + 中美贸易外交 + 地缘冲突）"""
    return {"news": get_policy_news(limit)}


@app.get("/api/news/impact")
def get_news_impact_api():
    """分析最新新闻对持仓基金的影响"""
    policy_news = get_policy_news(15)
    market_news = get_market_news(10)
    all_news = policy_news + market_news
    impacts = analyze_news_impact(all_news)
    return {
        "impacts": impacts[:8],
        "total_news_analyzed": len(all_news),
        "timestamp": datetime.now().isoformat(),
    }


# ---- API: 技术指标 ----

@app.get("/api/technical")
def get_tech_indicators():
    """获取沪深300技术指标（RSI/MACD/布林带）"""
    return get_technical_indicators()


# ---- API: 宏观经济日历 ----

@app.get("/api/macro")
def get_macro_data():
    """获取宏观经济事件日历"""
    return {"events": get_macro_calendar()}


# ---- API: 综合市场仪表盘（一次性拉全部数据）----

@app.get("/api/dashboard")
async def get_market_dashboard():
    """V4.5 综合市场仪表盘 — 11个数据源并行请求"""
    import asyncio

    loop = asyncio.get_event_loop()
    # 把所有阻塞的数据源调用并行化
    (val, fgi_data, tech, news, macro,
     northbound, margin, treasury, shibor_data, dividend, sentiment
    ) = await asyncio.gather(
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


# ---- V4.5 API: 新因子单独接口 ----

@app.get("/api/factors/northbound")
def get_northbound_api():
    """北向资金数据"""
    return get_northbound_flow()

@app.get("/api/factors/margin")
def get_margin_api():
    """融资融券数据"""
    return get_margin_trading()

@app.get("/api/factors/treasury")
def get_treasury_api():
    """国债收益率 / 股债性价比"""
    return get_treasury_yield()

@app.get("/api/factors/shibor")
def get_shibor_api():
    """SHIBOR 银行间利率"""
    return get_shibor()

@app.get("/api/factors/dividend")
def get_dividend_api():
    """股息率数据"""
    return get_dividend_yield()

@app.get("/api/factors/sentiment")
def get_sentiment_api():
    """LLM 新闻情绪评分"""
    return get_news_sentiment_score()


# ---- Phase 2: 扩展宏观数据 API（量化文档核心数据维度补齐）----
from services.macro_extended import (
    get_m1_data, get_social_financing, get_lpr_rate,
    get_market_activity, get_merrill_lynch_clock,
)

@app.get("/api/macro/m1")
def macro_m1():
    """M1 货币供应量 + M1-M2 剪刀差"""
    return get_m1_data()

@app.get("/api/macro/social-financing")
def macro_shrzgm():
    """社会融资规模"""
    return get_social_financing()

@app.get("/api/macro/lpr")
def macro_lpr():
    """LPR 贷款市场报价利率"""
    return get_lpr_rate()

@app.get("/api/market/activity")
def market_activity():
    """市场涨跌家数/赚钱效应"""
    return get_market_activity()

@app.get("/api/macro/clock")
def merrill_clock():
    """美林时钟经济周期判断"""
    return get_merrill_lynch_clock()

@app.get("/api/macro/extended")
def macro_extended_all():
    """一次性获取所有扩展宏观数据"""
    return {
        "m1": get_m1_data(),
        "social_financing": get_social_financing(),
        "lpr": get_lpr_rate(),
        "activity": get_market_activity(),
        "clock": get_merrill_lynch_clock(),
    }


# ---- 国内政策数据 API ----
from services.policy_data import (
    get_real_estate_data, get_house_price_index,
    get_policy_news_by_topic, get_all_policy_topics,
    analyze_policy_impact_ds,
)

# ---- 市场微观因子 API ----
from services.market_factors import (
    get_commodity_prices, get_stock_unlock_schedule,
    get_etf_fund_flow, get_all_market_factors,
    check_holding_unlock,
)

# ---- 持仓关联智能 API ----
from services.holding_intelligence import (
    scan_all_holding_intelligence, build_holding_context,
    get_stock_news as get_stock_related_news, get_stock_fund_flow, get_stock_industry,
)

@app.get("/api/holding-intelligence/{code}")
def get_single_holding_intel(code: str):
    """获取单只持仓股票的关联智能（新闻+资金流+行业+解禁）"""
    result = {}
    try:
        result["news"] = get_stock_related_news(code)
    except Exception:
        result["news"] = []
    try:
        result["fund_flow"] = get_stock_fund_flow(code)
    except Exception:
        result["fund_flow"] = None
    try:
        result["industry"] = get_stock_industry(code)
    except Exception:
        result["industry"] = ""
    # 检查解禁
    try:
        from services.market_factors import check_holding_unlock
        unlocks = check_holding_unlock([code])
        if unlocks:
            result["unlock_risk"] = unlocks[0].get("msg", "")
    except Exception:
        pass
    return result

@app.get("/api/policy/real-estate")
def policy_real_estate():
    """房地产开发投资/销售数据"""
    return get_real_estate_data()

@app.get("/api/policy/house-price")
def policy_house_price():
    """70城新房价格指数"""
    return get_house_price_index()

@app.get("/api/policy/news")
def policy_news_by_topic(topic: str = "房地产", limit: int = 5):
    """按主题获取政策新闻"""
    return get_policy_news_by_topic(topic, limit)

@app.get("/api/policy/all-topics")
def policy_all_topics():
    """一次性获取全部主题政策新闻"""
    return get_all_policy_topics()

@app.get("/api/policy/impact")
def policy_impact():
    """DeepSeek 分析政策→A股影响"""
    return analyze_policy_impact_ds()


# ---- 市场微观因子 API ----

@app.get("/api/market-factors/commodities")
def commodities_api():
    """大宗商品期货（黄金/铜）"""
    return get_commodity_prices()

@app.get("/api/market-factors/unlock")
def unlock_api():
    """限售股解禁计划"""
    return get_stock_unlock_schedule()

@app.get("/api/market-factors/etf-flow")
def etf_flow_api():
    """ETF 资金流向"""
    return get_etf_fund_flow()

@app.get("/api/market-factors/all")
def market_factors_all():
    """全部市场微观因子"""
    return get_all_market_factors()


# ---- 持仓关联智能 API ----

@app.get("/api/holding-intelligence")
def holding_intel_api(userId: str = "default"):
    """全持仓智能扫描（个股新闻+资金流+行业+解禁）"""
    return scan_all_holding_intelligence(userId)


# ---- V8 扩展宏观因子 API ----
from services.macro_v8 import (
    get_gdp, get_industrial_value_added, get_consumer_goods_retail,
    get_fixed_asset_investment, get_lhb_summary, get_management_holdings,
    get_all_v8_macro,
)

@app.get("/api/macro/v8")
def macro_v8_all():
    """V8 全部扩展宏观因子（GDP/工业增加值/社零/固投/龙虎榜/管理层增减持）"""
    return get_all_v8_macro()

@app.get("/api/macro/gdp")
def macro_gdp():
    return get_gdp()

@app.get("/api/macro/lhb")
def macro_lhb():
    """龙虎榜（机构+游资动向）"""
    return get_lhb_summary()


@app.get("/api/factors/all")
def get_all_factors():
    """一次性获取全部新因子数据"""
    return {
        "northbound": get_northbound_flow(),
        "margin": get_margin_trading(),
        "treasury": get_treasury_yield(),
        "shibor": get_shibor(),
        "dividend": get_dividend_yield(),
        "sentiment": get_news_sentiment_score(),
        "mainFlow": get_main_money_flow(),
        "updatedAt": datetime.now().isoformat(),
    }

# ---- V5.5 API: 数据缺口补齐 ----

@app.get("/api/factors/main-flow")
def get_main_flow_api():
    """主力资金流向（今日全市场主力净流入TOP5/流出TOP5）"""
    return get_main_money_flow()

@app.get("/api/stock/financials/{code}")
def get_stock_fin(code: str):
    """个股核心财务数据（ROE/EPS/营收增速）"""
    return get_stock_financials(code)

@app.get("/api/fund/holdings/{code}")
def get_fund_holdings(code: str):
    """基金持仓明细（前10大重仓股+占净值比）"""
    return get_fund_holding_detail(code)

# ---- V4.5 API: 风控指标 ----

@app.post("/api/risk-metrics")
def get_risk_metrics(req: dict):
    """获取组合风控指标（集中度/回撤/相关性）"""
    user_id = req.get("userId", "")
    if not user_id:
        txs = req.get("transactions", [])
    else:
        user = load_user(user_id)
        user = ensure_v4_portfolio(user)
        txs = user["portfolio"].get("transactions", [])
    return calc_risk_metrics(txs)


@app.post("/api/risk-actions")
def get_risk_actions(req: dict):
    """风控硬阈值执行建议（借鉴豆包方案+幻方量化）"""
    user_id = req.get("userId", "")
    if not user_id:
        txs = req.get("transactions", [])
    else:
        user = load_user(user_id)
        user = ensure_v4_portfolio(user)
        txs = user["portfolio"].get("transactions", [])
    # 获取当前估值百分位（get_valuation_percentile 返回 dict）
    try:
        vp = get_valuation_percentile()
        val_pct = vp.get("percentile", 50) if isinstance(vp, dict) else 50
    except Exception:
        val_pct = 50
    return generate_risk_actions(txs, val_pct)


@app.post("/api/allocation-advice")
def get_allocation_advice(req: dict):
    """大类资产配置建议（股/债/现金目标比例+偏离度）"""
    user_id = req.get("userId", "")
    if not user_id:
        txs = req.get("transactions", [])
    else:
        user = load_user(user_id)
        user = ensure_v4_portfolio(user)
        txs = user["portfolio"].get("transactions", [])
    # 获取当前估值和恐惧贪婪（两者都返回 dict）
    try:
        vp = get_valuation_percentile()
        val_pct = vp.get("percentile", 50) if isinstance(vp, dict) else 50
    except Exception:
        val_pct = 50
    try:
        fgi = get_fear_greed_index()
        fg_val = fgi.get("score", 50) if isinstance(fgi, dict) else 50
    except Exception:
        fg_val = 50
    result = generate_allocation_advice(txs, val_pct, fg_val)
    # DeepSeek 增强：接入新闻/市场数据维度
    market_ctx = _build_market_context()
    result = enhance_allocation_advice(result, market_ctx=market_ctx)
    return result


@app.get("/api/recommend-alloc")
def get_recommend_alloc(profile: str = "稳健型", with_ai: bool = False, preference: str = "fund"):
    """推荐配置列表（基金/股票/混合）+ 配置理由 + 可选 AI 点评"""
    return get_recommend_allocations(profile, with_ai=with_ai, preference=preference)


@app.get("/api/fund-screen")
def get_fund_screen(fund_type: str = "all", sort_by: str = "score", top_n: int = 20):
    """基金智能筛选：多维打分排序 + DeepSeek 点评 TOP5"""
    result = screen_funds(fund_type, sort_by, top_n)
    if result.get("funds"):
        result["funds"] = comment_fund_picks(result["funds"])
    return result


@app.get("/api/stock-screen")
def get_stock_screen(top_n: int = 50):
    """AI多因子选股：30因子7维打分 V2 + DeepSeek 点评 TOP5"""
    result = screen_stocks(top_n)
    if result.get("stocks"):
        result["stocks"] = comment_stock_picks(result["stocks"])
    return result


# ---- 回测引擎 API ----
from services.backtest_engine import backtest_single, backtest_portfolio

@app.get("/api/backtest/{code}")
def api_backtest_single(code: str, asset_type: str = "stock", years: int = 3):
    """单只股票/基金回测"""
    return backtest_single(code, asset_type, years)

@app.post("/api/backtest/portfolio")
def api_backtest_portfolio(req: dict):
    """组合回测（按权重加权）"""
    holdings = req.get("holdings", [])
    years = req.get("years", 3)
    return backtest_portfolio(holdings, years)


# ---- Phase 4: LightGBM ML 选股 ----
from services.ml_stock_screen import ml_stock_screen

@app.get("/api/stock-screen/ml")
def get_ml_stock_screen(top_n: int = 30):
    """LightGBM 多因子选股：ML增强版"""
    return ml_stock_screen(top_n)


# ---- Phase 5: 因子 IC 检验 ----
from services.factor_ic import compute_factor_ic, compute_ic_decay

@app.get("/api/factor-ic")
def api_factor_ic(forward_days: int = 20, pool_size: int = 200):
    """因子 IC 检验：验证30因子中哪些真正预测未来收益"""
    return compute_factor_ic(forward_days=forward_days, pool_size=pool_size)

@app.get("/api/factor-ic/decay")
def api_factor_ic_decay(pool_size: int = 150):
    """IC 衰减曲线：因子在不同预测周期下的效果"""
    return compute_ic_decay(pool_size=pool_size)


# ---- Phase 6: 蒙特卡洛模拟 ----
from services.monte_carlo import monte_carlo_single, monte_carlo_portfolio, monte_carlo_compare

@app.get("/api/monte-carlo/{code}")
def api_monte_carlo_single(
    code: str,
    simulations: int = 5000,
    horizon_days: int = 250,
    initial: float = 10000,
    discipline: bool = True,
):
    """单只股票蒙特卡洛模拟：概率分布预测"""
    return monte_carlo_single(
        code=code, simulations=simulations, horizon_days=horizon_days,
        initial_investment=initial, apply_discipline=discipline,
    )

@app.post("/api/monte-carlo/portfolio")
def api_monte_carlo_portfolio(req: dict):
    """组合蒙特卡洛模拟"""
    holdings = req.get("holdings", [])
    simulations = req.get("simulations", 3000)
    horizon_days = req.get("horizon_days", 250)
    initial = req.get("initial", 100000)
    discipline = req.get("discipline", True)
    return monte_carlo_portfolio(
        holdings=holdings, simulations=simulations,
        horizon_days=horizon_days, initial_investment=initial,
        apply_discipline=discipline,
    )

@app.get("/api/monte-carlo/compare/{code}")
def api_monte_carlo_compare(code: str, simulations: int = 3000, horizon_days: int = 250):
    """纪律 vs 无纪律蒙特卡洛对比"""
    return monte_carlo_compare(code=code, simulations=simulations, horizon_days=horizon_days)


# ---- 全球市场 API ----
from services.global_market import (
    get_us_indices, get_forex_data, get_fed_rate,
    get_global_pe, get_global_snapshot,
    analyze_global_impact_on_a_shares, get_decision_data_pack,
)

@app.get("/api/global/indices")
def global_indices():
    """美股三大指数（道琼斯/标普/纳斯达克）"""
    return get_us_indices()

@app.get("/api/global/forex")
def global_forex():
    """外汇数据（美元/人民币）"""
    return get_forex_data()

@app.get("/api/global/fed-rate")
def global_fed_rate():
    """美联储利率"""
    return get_fed_rate()

@app.get("/api/global/pe")
def global_pe():
    """全球 PE 估值对比"""
    return get_global_pe()

@app.get("/api/global/snapshot")
def global_snapshot():
    """全球市场综合快照"""
    return get_global_snapshot()

@app.get("/api/global/impact")
def global_impact():
    """DeepSeek 分析全球→A股影响"""
    return analyze_global_impact_on_a_shares()

@app.get("/api/decision-data")
def decision_data(userId: str = "default"):
    """全量决策数据包（供 Claude 决策用）"""
    return get_decision_data_pack(userId)


# ---- 股票持仓盯盘 API ----
from services.stock_monitor import (
    load_stock_holdings, add_stock_holding, remove_stock_holding,
    update_stock_holding, get_stock_realtime, scan_all_holdings,
)

# ---- 多用户 Profile 管理（独立 router）----
from routers.profiles import router as profiles_router, _load_profiles, _save_profiles
app.include_router(profiles_router)


# ---- 资产总览 API ----
from services.portfolio_overview import get_portfolio_overview
from services.unified_networth import calc_unified_networth

# ---- V4 W5: 持仓体检 API ----
from services.portfolio_doctor import diagnose, stress_test, health_score, concentration_check

@app.get("/api/portfolio-doctor/diagnose")
def portfolio_doctor_api(userId: str = ""):
    """完整持仓体检 — 压力测试+集中度+健康评分"""
    if not userId:
        raise HTTPException(400, "userId required")
    return diagnose(userId)

@app.get("/api/portfolio-doctor/stress-test")
def portfolio_stress_test_api(userId: str = ""):
    """压力测试 — 模拟极端场景对持仓冲击"""
    if not userId:
        raise HTTPException(400, "userId required")
    # 获取合并持仓
    report = diagnose(userId)
    return report.get("stress_test", {"scenarios": [], "summary": "无数据"})

@app.get("/api/portfolio-doctor/health")
def portfolio_health_api(userId: str = ""):
    """健康评分 — 综合 0-100 分"""
    if not userId:
        raise HTTPException(400, "userId required")
    report = diagnose(userId)
    return report.get("health", {"score": 0, "grade": "❓"})

@app.get("/api/portfolio/overview")
def portfolio_overview_api(userId: str = "default"):
    """汇总全资产概览（股票+基金+配置占比+健康评分）"""
    return get_portfolio_overview(userId)

@app.get("/api/unified-networth")
def unified_networth_api(userId: str = ""):
    """统一净资产 — 合并所有数据源（股票+基金+手动资产+负债）"""
    if not userId:
        return {"netWorth": 0, "breakdown": {}}
    return calc_unified_networth(userId)


# ---- DeepSeek 智能增强 API ----
from services.ds_enhance import (
    analyze_idle_cash, comment_fund_picks, comment_stock_picks,
    comment_single_stock, comment_single_fund,
    generate_daily_focus, assess_news_risk, interpret_daily_signal,
    deep_analyze_news_impact, enhance_allocation_advice,
    diagnose_user_assets,
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

@app.get("/api/news/deep-impact")
def get_deep_news_impact():
    """新闻深度影响分析 — DeepSeek 分析事件→行业→持仓"""
    policy = get_policy_news(8)
    market = get_market_news(5)
    return {"impacts": deep_analyze_news_impact(policy + market)}

@app.get("/api/news/risk-assess")
def get_news_risk():
    """新闻风控评估 — DeepSeek 评估新闻对持仓风险影响"""
    policy = get_policy_news(8)
    market = get_market_news(5)
    headlines = [n["title"] for n in (policy + market) if "加载中" not in n.get("title", "")]
    return assess_news_risk(headlines)

@app.get("/api/daily-signal/interpret")
def get_signal_interpretation():
    """每日信号 DeepSeek 解读 — 把 12 维信号翻译成人话"""
    signal = generate_daily_signal()
    interpretation = interpret_daily_signal(signal)
    signal["interpretation"] = interpretation
    return signal


@app.get("/api/stock-holdings")
def get_stock_holdings_api(userId: str = "default"):
    """获取股票持仓列表"""
    return {"holdings": load_stock_holdings(userId)}

@app.post("/api/stock-holdings")
def add_stock_holding_api(req: dict):
    """添加股票持仓"""
    code = req.get("code", "").strip()
    if not code:
        raise HTTPException(400, "股票代码不能为空")
    uid = req.get("userId", "default")
    return add_stock_holding(
        code=code,
        name=req.get("name", ""),
        cost_price=float(req.get("costPrice", 0)),
        shares=int(req.get("shares", 0)),
        note=req.get("note", ""),
        user_id=uid,
    )

@app.delete("/api/stock-holdings/{code}")
def remove_stock_holding_api(code: str, userId: str = "default"):
    """删除股票持仓"""
    return remove_stock_holding(code, userId)

@app.put("/api/stock-holdings/{code}")
def update_stock_holding_api(code: str, req: dict):
    """更新股票持仓信息"""
    uid = req.pop("userId", "default")
    return update_stock_holding(code, user_id=uid, **{
        k: v for k, v in req.items()
        if k in ("costPrice", "shares", "note", "name")
    })

@app.get("/api/stock-holdings/realtime/{code}")
def get_stock_rt_api(code: str):
    """获取单只股票实时行情"""
    return get_stock_realtime(code)

@app.get("/api/stock-holdings/scan")
def scan_holdings_api(userId: str = "default"):
    """扫描全持仓 — 实时行情 + 异动信号"""
    return scan_all_holdings(userId)


@app.post("/api/stock-holdings/analyze")
async def analyze_stock_holdings(req: dict = {}):
    """收盘后 DeepSeek 深度分析全持仓（7 Skill 框架）"""
    uid = req.get("userId", "default")
    scan = scan_all_holdings(uid)
    if not scan.get("holdings"):
        return {"analysis": "暂无持仓股票，请先添加。", "source": "none"}

    # 构建持仓摘要给 DeepSeek
    lines = ["【股票持仓盯盘数据】"]
    for h in scan["holdings"]:
        ind = h.get("indicators", {})
        pnl_str = f"盈亏{h['pnlPct']:+.1f}%" if h.get("pnlPct") is not None else ""
        lines.append(
            f"  {h['name']}({h['code']}) 现价¥{h.get('price','N/A')} "
            f"涨跌{h.get('changePct','N/A')}% {pnl_str} "
            f"RSI={ind.get('rsi14','N/A')} MACD={ind.get('macd_trend','N/A')} "
            f"量比={ind.get('volume_ratio','N/A')}"
        )
    if scan.get("signals"):
        lines.append("\n【异动信号】")
        for s in scan["signals"]:
            lines.append(f"  [{s['level']}] {s['msg']}")

    stock_ctx = "\n".join(lines)
    market_ctx = _build_market_context()

    # 调用 DeepSeek 做深度分析
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        return {"analysis": stock_ctx, "source": "data_only"}

    system_prompt = _load_prompt_template()
    user_prompt = f"""请对我的股票持仓做一次全面深度分析。

{stock_ctx}

{market_ctx}

请按以下结构回答：
1. 📊 总体评估（一句话结论）
2. 逐只分析（每只股票：趋势判断+风险提示+操作建议）
3. 🛡️ 风控经理总结（组合风险+操作优先级）"""

    try:
        import httpx
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": req.get("model", "deepseek-chat"),
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": 1200,
                    "temperature": 0.7,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                reply = data["choices"][0]["message"]["content"]
                return {"analysis": reply, "source": "ai", "scan": scan}
    except Exception as e:
        print(f"[STOCK_ANALYZE] DeepSeek fail: {e}")

    return {"analysis": stock_ctx, "source": "data_only", "scan": scan}


# ---- 基金持仓盯盘 API ----
from services.fund_monitor import (
    load_fund_holdings, add_fund_holding, remove_fund_holding,
    update_fund_holding, get_fund_realtime, scan_all_fund_holdings,
)

@app.get("/api/fund-holdings")
def get_fund_holdings_api(userId: str = "default"):
    """获取基金持仓列表"""
    return {"holdings": load_fund_holdings(userId)}

@app.post("/api/fund-holdings")
def add_fund_holding_api(req: dict):
    """添加基金持仓"""
    code = req.get("code", "").strip()
    if not code:
        raise HTTPException(400, "基金代码不能为空")
    uid = req.get("userId", "default")
    return add_fund_holding(
        code=code,
        name=req.get("name", ""),
        cost_nav=float(req.get("costNav", 0)),
        shares=float(req.get("shares", 0)),
        note=req.get("note", ""),
        user_id=uid,
    )

@app.delete("/api/fund-holdings/{code}")
def remove_fund_holding_api(code: str, userId: str = "default"):
    """删除基金持仓"""
    return remove_fund_holding(code, userId)

@app.put("/api/fund-holdings/{code}")
def update_fund_holding_api(code: str, req: dict):
    """更新基金持仓信息"""
    uid = req.pop("userId", "default")
    return update_fund_holding(code, user_id=uid, **{
        k: v for k, v in req.items()
        if k in ("costNav", "shares", "note", "name")
    })

@app.get("/api/fund-holdings/realtime/{code}")
def get_fund_rt_api(code: str):
    """获取单只基金实时估值"""
    return get_fund_realtime(code)

@app.get("/api/fund-holdings/scan")
def scan_fund_holdings_api(userId: str = "default"):
    """扫描全基金持仓 — 估值 + 风控 + 异动"""
    return scan_all_fund_holdings(userId)

@app.post("/api/fund-holdings/analyze")
async def analyze_fund_holdings(req: dict = {}):
    """DeepSeek 深度分析全基金持仓（7 Skill 框架）"""
    uid = req.get("userId", "default")
    scan = scan_all_fund_holdings(uid)
    if not scan.get("holdings"):
        return {"analysis": "暂无基金持仓，请先添加。", "source": "none"}

    lines = ["【基金持仓盯盘数据】"]
    for h in scan["holdings"]:
        rt = h.get("realtime") or {}
        risk = h.get("risk") or {}
        pnl_str = f"盈亏{h['pnlPct']:+.1f}%" if h.get("pnlPct") is not None else ""
        est_str = f"估算{rt.get('estRate', 'N/A')}%" if rt.get("estRate") is not None else ""
        lines.append(
            f"  {h['name']}({h['code']}) 估值¥{rt.get('estNav','N/A')} "
            f"{est_str} {pnl_str} "
            f"回撤={risk.get('maxDrawdown','N/A')} 波动={risk.get('volatility','N/A')} "
            f"连跌{risk.get('downDays',0)}天"
        )
    if scan.get("alerts"):
        lines.append("\n【基金异动信号】")
        for a in scan["alerts"]:
            lines.append(f"  [{a['level']}] {a.get('fund','')} {a['msg']}")

    fund_ctx = "\n".join(lines)
    market_ctx = _build_market_context()

    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        return {"analysis": fund_ctx, "source": "data_only"}

    system_prompt = _load_prompt_template()
    user_prompt = f"""请对我的基金持仓做一次全面深度分析。

{fund_ctx}

{market_ctx}

请按以下结构回答：
1. 📊 总体评估（一句话结论）
2. 逐只分析（每只基金：估值判断+回撤风险+配置建议）
3. 🛡️ 风控经理总结（组合风险+配置调整建议）"""

    try:
        import httpx
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": req.get("model", "deepseek-chat"),
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": 1200,
                    "temperature": 0.7,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "analysis": data["choices"][0]["message"]["content"],
                    "source": "ai",
                    "scan": scan,
                }
    except Exception as e:
        print(f"[FUND_ANALYZE] DeepSeek fail: {e}")

    return {"analysis": fund_ctx, "source": "data_only", "scan": scan}


# ---- API: 每日智能信号 ----

@app.get("/api/daily-signal")
def get_daily_signal():
    """每日综合交易信号（技术面+基本面+大师策略）"""
    cache_key = "daily_signal"
    now = time.time()
    if cache_key in macro_cache and now - macro_cache[cache_key]["ts"] < 1800:
        return macro_cache[cache_key]["data"]
    result = generate_daily_signal()
    macro_cache[cache_key] = {"data": result, "ts": now}
    return result


# ---- API: 策略回测 ----

@app.get("/api/backtest")
def get_backtest(strategy: str = "smart_dca", years: int = 3, monthly: float = 1000):
    """回测智能定投 vs 固定定投（沪深300历史数据）"""
    cache_key = f"bt_{strategy}_{years}_{monthly}"
    now = time.time()
    if cache_key in macro_cache and now - macro_cache[cache_key]["ts"] < 7200:
        return macro_cache[cache_key]["data"]
    result = run_backtest(strategy, years, monthly)
    macro_cache[cache_key] = {"data": result, "ts": now}
    return result


# ---- V4 API: 基金搜索 ----

@app.get("/api/fund/search")
def search_fund(q: str = ""):
    """基金搜索 — 输入关键词/代码返回基金列表"""
    if not q or len(q) < 2:
        return {"results": []}

    cache_key = f"fund_search_{q}"
    now = time.time()
    if cache_key in nav_cache and now - nav_cache[cache_key]["ts"] < 86400:
        return {"results": nav_cache[cache_key]["data"]}

    results = []
    try:
        import akshare as ak
        df = ak.fund_name_em()
        if df is not None and len(df) > 0:
            code_col = [c for c in df.columns if "代码" in c or "code" in c.lower()]
            name_col = [c for c in df.columns if "名称" in c or "简称" in c or "name" in c.lower()]
            type_col = [c for c in df.columns if "类型" in c or "type" in c.lower()]

            if code_col and name_col:
                cc, nc = code_col[0], name_col[0]
                tc = type_col[0] if type_col else None
                mask = df[cc].astype(str).str.contains(q) | df[nc].astype(str).str.contains(q, case=False)
                matched = df[mask].head(20)
                for _, row in matched.iterrows():
                    results.append({
                        "code": str(row[cc]),
                        "name": str(row[nc]),
                        "type": str(row[tc]) if tc else "",
                    })
    except Exception as e:
        print(f"[FUND_SEARCH] Failed: {e}")

    # 补充硬编码的推荐基金匹配
    hardcoded = {
        "110020": "易方达沪深300ETF联接A",
        "050025": "博时标普500ETF联接A",
        "217022": "招商产业债A",
        "000216": "华安黄金ETF联接A",
        "008114": "天弘中证红利低波动100ETF联接A",
    }
    for code, name in hardcoded.items():
        if q in code or q in name:
            if not any(r["code"] == code for r in results):
                results.insert(0, {"code": code, "name": name, "type": "推荐"})

    nav_cache[cache_key] = {"data": results[:20], "ts": now}
    return {"results": results[:20]}


# ---- V4 API: 交易流水 CRUD ----

@app.post("/api/portfolio/transaction")
def add_transaction(req: TransactionRequest):
    """添加交易记录（BUY/SELL/DIVIDEND）"""
    user = load_user(req.userId)
    user = ensure_v4_portfolio(user)
    p = user["portfolio"]

    tx = req.transaction.dict()
    if not tx.get("id"):
        tx["id"] = f"tx_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    if not tx.get("date"):
        tx["date"] = datetime.now().isoformat()

    # 如果是 BUY 且没有 shares/nav，自动补算
    if tx["type"] == "BUY" and tx.get("amount", 0) > 0:
        if tx.get("shares", 0) <= 0 or tx.get("nav", 0) <= 0:
            nav_val = _get_nav_on_date(tx["code"], tx["date"])
            if not nav_val:
                nav_info = get_fund_nav(tx["code"])
                nav_val = float(nav_info["nav"]) if nav_info and nav_info["nav"] != "N/A" else None
            if nav_val and nav_val > 0:
                tx["nav"] = nav_val
                tx["shares"] = round(tx["amount"] / nav_val, 2)

    p["transactions"].append(tx)
    p["history"].append({
        "date": datetime.now().isoformat(),
        "action": tx["type"].lower(),
        "code": tx["code"],
        "amount": tx.get("amount", 0),
    })
    save_user(user)
    return {"status": "ok", "transaction": tx}


@app.put("/api/portfolio/transaction/{tx_id}")
def update_transaction(tx_id: str, req: TransactionRequest):
    """修改交易记录"""
    user = load_user(req.userId)
    user = ensure_v4_portfolio(user)
    p = user["portfolio"]

    for i, tx in enumerate(p["transactions"]):
        if tx.get("id") == tx_id:
            updated = req.transaction.dict()
            updated["id"] = tx_id
            p["transactions"][i] = updated
            save_user(user)
            return {"status": "ok", "transaction": updated}

    raise HTTPException(404, f"Transaction {tx_id} not found")


@app.delete("/api/portfolio/transaction/{tx_id}")
def delete_transaction(tx_id: str, userId: str = ""):
    """删除交易记录"""
    if not userId:
        raise HTTPException(400, "userId required")
    user = load_user(userId)
    user = ensure_v4_portfolio(user)
    p = user["portfolio"]

    original_len = len(p["transactions"])
    p["transactions"] = [tx for tx in p["transactions"] if tx.get("id") != tx_id]
    if len(p["transactions"]) == original_len:
        raise HTTPException(404, f"Transaction {tx_id} not found")

    save_user(user)
    return {"status": "ok"}


@app.get("/api/portfolio/history")
def get_transaction_history(userId: str = ""):
    """获取交易流水历史"""
    if not userId:
        return {"transactions": []}
    user = load_user(userId)
    user = ensure_v4_portfolio(user)
    txs = user["portfolio"].get("transactions", [])
    # 按日期倒序
    txs_sorted = sorted(txs, key=lambda t: t.get("date", ""), reverse=True)
    return {"transactions": txs_sorted}


# ---- V4 API: 持仓计算 ----

@app.post("/api/portfolio/holdings")
def get_holdings_v4(req: dict):
    """从交易流水计算当前持仓（V4）"""
    user_id = req.get("userId", "")
    if not user_id:
        # 直接传入 transactions
        txs = req.get("transactions", [])
    else:
        user = load_user(user_id)
        user = ensure_v4_portfolio(user)
        txs = user["portfolio"].get("transactions", [])

    result = calc_holdings_from_transactions(txs)

    # 给每个活跃持仓补上实时净值和市值
    for h in result["active"]:
        code = h["code"]
        if code == "余额宝":
            h["currentNav"] = 1.0
            h["marketValue"] = h["shares"]
            h["pnl"] = h["shares"] - h["totalCost"]
            h["pnlPct"] = round(h["pnl"] / h["totalCost"] * 100, 2) if h["totalCost"] > 0 else 0
            continue

        nav_info = get_fund_nav(code)
        if nav_info and nav_info["nav"] != "N/A":
            current_nav = float(nav_info["nav"])
            h["currentNav"] = current_nav
            h["navDate"] = nav_info.get("date", "")
            h["dayChange"] = float(nav_info.get("change", "0"))
            h["marketValue"] = round(h["shares"] * current_nav, 2)
            h["pnl"] = round(h["marketValue"] - h["totalCost"], 2)
            h["pnlPct"] = round(h["pnl"] / h["totalCost"] * 100, 2) if h["totalCost"] > 0 else 0
        else:
            h["currentNav"] = h["avgNav"]
            h["marketValue"] = round(h["shares"] * h["avgNav"], 2)
            h["pnl"] = 0
            h["pnlPct"] = 0

    total_cost = sum(h["totalCost"] for h in result["active"])
    total_market = sum(h.get("marketValue", 0) for h in result["active"])
    total_pnl = total_market - total_cost
    total_realized = sum(result["realized"].values())

    return {
        "holdings": result["active"],
        "closed": result["closed"],
        "totalCost": round(total_cost, 2),
        "totalMarket": round(total_market, 2),
        "totalPnl": round(total_pnl, 2),
        "totalPnlPct": round(total_pnl / total_cost * 100, 2) if total_cost > 0 else 0,
        "totalRealized": round(total_realized, 2),
        "realized": result["realized"],
    }


# ---- V4 API: 资产管理 ----

@app.post("/api/assets")
def add_or_update_asset(req: AssetRequest):
    """添加或更新非投资类资产"""
    user = load_user(req.userId)
    user = ensure_v4_portfolio(user)
    p = user["portfolio"]

    asset = req.asset.dict()
    if not asset.get("id"):
        asset["id"] = f"a_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    if not asset.get("updated"):
        asset["updated"] = datetime.now().strftime("%Y-%m-%d")

    # 如果 id 存在则更新，否则添加
    existing_idx = None
    for i, a in enumerate(p.get("assets", [])):
        if a.get("id") == asset["id"]:
            existing_idx = i
            break

    if existing_idx is not None:
        p["assets"][existing_idx] = asset
    else:
        p.setdefault("assets", []).append(asset)

    save_user(user)
    # 资产变更 → 清除净资产缓存，下次查询返回最新值
    try:
        from services.unified_networth import invalidate_networth_cache
        invalidate_networth_cache(req.userId)
    except Exception:
        pass
    return {"status": "ok", "asset": asset}


@app.delete("/api/assets/{asset_id}")
def delete_asset(asset_id: str, userId: str = ""):
    """删除资产"""
    if not userId:
        raise HTTPException(400, "userId required")
    user = load_user(userId)
    user = ensure_v4_portfolio(user)
    p = user["portfolio"]

    original_len = len(p.get("assets", []))
    p["assets"] = [a for a in p.get("assets", []) if a.get("id") != asset_id]
    if len(p.get("assets", [])) == original_len:
        raise HTTPException(404, f"Asset {asset_id} not found")

    save_user(user)
    # 资产变更 → 清除净资产缓存
    try:
        from services.unified_networth import invalidate_networth_cache
        invalidate_networth_cache(userId)
    except Exception:
        pass
    return {"status": "ok"}


@app.get("/api/assets")
def get_assets(userId: str = ""):
    """获取全部资产"""
    if not userId:
        return {"assets": []}
    user = load_user(userId)
    user = ensure_v4_portfolio(user)
    return {"assets": user["portfolio"].get("assets", [])}


# ---- V4 API: 净资产 ----

@app.post("/api/portfolio/networth")
def calc_networth(req: dict):
    """计算净资产 = 投资市值 + 现金 + 固定资产 + 记账净现金流 - 负债"""
    user_id = req.get("userId", "")
    if not user_id:
        return {"netWorth": 0, "breakdown": {}}

    user = load_user(user_id)
    user = ensure_v4_portfolio(user)
    p = user["portfolio"]

    # 计算投资市值
    txs = p.get("transactions", [])
    holdings_result = calc_holdings_from_transactions(txs)
    investment_value = 0
    for h in holdings_result["active"]:
        code = h["code"]
        if code == "余额宝":
            investment_value += h["shares"]
            continue
        nav_info = get_fund_nav(code)
        if nav_info and nav_info["nav"] != "N/A":
            investment_value += h["shares"] * float(nav_info["nav"])
        else:
            investment_value += h["shares"] * h["avgNav"]

    # 计算各类资产（统一读 value 字段，兼容 balance）
    assets = p.get("assets", [])
    def _av(a): return a.get("value", 0) or a.get("balance", 0) or 0
    cash_total = sum(_av(a) for a in assets if a.get("type") == "cash")
    property_total = sum(_av(a) for a in assets if a.get("type") == "property")
    car_total = sum(_av(a) for a in assets if a.get("type") == "car")
    insurance_total = sum(_av(a) for a in assets if a.get("type") == "insurance")
    other_total = sum(_av(a) for a in assets if a.get("type") == "other")
    liability_total = sum(abs(_av(a)) for a in assets if a.get("type") == "liability")

    # 计算记账收支净额（收入 - 支出）
    ledger = user.get("ledger", [])
    ledger_income = sum(e.get("amount", 0) for e in ledger if e.get("direction") == "income")
    ledger_expense = sum(e.get("amount", 0) for e in ledger if e.get("direction", "expense") == "expense")
    ledger_net = ledger_income - ledger_expense

    net_worth = investment_value + cash_total + property_total + car_total + insurance_total + other_total + ledger_net - liability_total

    return {
        "netWorth": round(net_worth, 2),
        "breakdown": {
            "investment": round(investment_value, 2),
            "cash": round(cash_total, 2),
            "property": round(property_total, 2),
            "car": round(car_total, 2),
            "insurance": round(insurance_total, 2),
            "other": round(other_total, 2),
            "liability": round(liability_total, 2),
            "ledgerIncome": round(ledger_income, 2),
            "ledgerExpense": round(ledger_expense, 2),
            "ledgerNet": round(ledger_net, 2),
        },
        "holdingsCount": len(holdings_result["active"]),
        "assetsCount": len(assets),
    }


# ---- V4 API: 加仓 ----

@app.post("/api/portfolio/topup")
def topup_portfolio(req: TopupRequest):
    """加仓 — 批量生成 BUY 交易"""
    user = load_user(req.userId)
    user = ensure_v4_portfolio(user)
    p = user["portfolio"]

    new_txs = []
    for alloc in req.allocations:
        code = alloc.get("code", "")
        name = alloc.get("name", "")
        amount = alloc.get("amount", 0)
        if not code or amount <= 0:
            continue

        tx_id = f"tx_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        nav_val = None
        shares = 0

        # 获取当前净值计算份额
        if code != "余额宝":
            nav_info = get_fund_nav(code)
            if nav_info and nav_info["nav"] != "N/A":
                nav_val = float(nav_info["nav"])
                shares = round(amount / nav_val, 2)
        else:
            nav_val = 1.0
            shares = amount

        tx = {
            "id": tx_id,
            "type": "BUY",
            "code": code,
            "name": name,
            "amount": amount,
            "shares": shares,
            "nav": nav_val or 0,
            "fee": 0,
            "date": datetime.now().isoformat(),
            "source": "topup",
            "note": f"加仓 ¥{amount:,.0f}",
        }
        p["transactions"].append(tx)
        new_txs.append(tx)

    p["history"].append({
        "date": datetime.now().isoformat(),
        "action": "topup",
        "amount": req.amount,
        "profile": req.profile,
    })
    save_user(user)
    return {"status": "ok", "transactions": new_txs, "count": len(new_txs)}


# ---- V4 API: 数据迁移 ----

@app.post("/api/portfolio/migrate")
def migrate_portfolio(req: dict):
    """手动触发 V3→V4 数据迁移"""
    user_id = req.get("userId", "")
    if not user_id:
        raise HTTPException(400, "userId required")
    user = load_user(user_id)
    user = ensure_v4_portfolio(user)
    save_user(user)
    return {"status": "ok", "version": user["portfolio"].get("version", 4)}


# ---- API: 盈亏计算 ----

@app.post("/api/portfolio/pnl")
def calc_portfolio_pnl(portfolio: Portfolio):
    """计算持仓的实时盈亏"""
    if not portfolio.holdings:
        return {"totalCost": 0, "totalMarket": 0, "totalPnl": 0, "totalPnlPct": 0, "holdings": []}

    results = []
    total_cost = 0
    total_market = 0

    for h in portfolio.holdings:
        cost = h.amount
        total_cost += cost

        # 获取最新净值
        nav_info = get_fund_nav(h.code) if h.code != "余额宝" else None
        if nav_info and nav_info["nav"] != "N/A":
            current_nav = float(nav_info["nav"])
            nav_date = nav_info["date"]
            change_pct = float(nav_info.get("change", "0"))
        else:
            # 余额宝或无数据：假设年化 1.8% 按日计算
            if h.buyDate:
                try:
                    buy_dt = datetime.fromisoformat(h.buyDate.replace("Z", "+00:00"))
                    days = max((datetime.now(buy_dt.tzinfo) - buy_dt).days, 0)
                except Exception:
                    days = 0
            else:
                days = 0
            daily_rate = 0.018 / 365
            current_nav = None
            nav_date = None
            change_pct = 0
            market_val = cost * (1 + daily_rate * days)
            results.append({
                "code": h.code,
                "name": h.name,
                "category": h.category,
                "cost": round(cost, 2),
                "marketValue": round(market_val, 2),
                "pnl": round(market_val - cost, 2),
                "pnlPct": round((market_val - cost) / cost * 100, 2) if cost > 0 else 0,
                "nav": "余额宝",
                "navDate": datetime.now().strftime("%Y-%m-%d"),
                "dayChange": 0,
            })
            total_market += market_val
            continue

        # 用净值变化估算市值（简化：假设买入时净值为基准，当前净值反映涨跌）
        # 更精确做法：记录买入净值。当前 MVP 用买入日期到现在的累计涨幅估算
        # 这里先用 AKShare 拉买入日到最新的净值变化
        buy_nav = _get_nav_on_date(h.code, h.buyDate)
        if buy_nav and buy_nav > 0:
            growth = (current_nav - buy_nav) / buy_nav
            market_val = cost * (1 + growth)
        else:
            market_val = cost  # 无法计算则保持原值

        pnl = market_val - cost
        pnl_pct = (pnl / cost * 100) if cost > 0 else 0
        total_market += market_val

        results.append({
            "code": h.code,
            "name": h.name,
            "category": h.category,
            "cost": round(cost, 2),
            "marketValue": round(market_val, 2),
            "pnl": round(pnl, 2),
            "pnlPct": round(pnl_pct, 2),
            "nav": str(current_nav),
            "navDate": nav_date,
            "dayChange": change_pct,
        })

    total_pnl = total_market - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0

    return {
        "totalCost": round(total_cost, 2),
        "totalMarket": round(total_market, 2),
        "totalPnl": round(total_pnl, 2),
        "totalPnlPct": round(total_pnl_pct, 2),
        "holdings": results,
    }


# ---- API: AI 对话分析 ----

@app.post("/api/chat")
async def chat_analysis(req: ChatRequest):
    """AI 对话分析 — 回答用户的理财问题"""
    user_msg = req.message.strip()
    if not user_msg:
        raise HTTPException(400, "消息不能为空")

    # 构建市场上下文
    market_ctx = _build_market_context()
    uid = req.userId or "default"
    portfolio_ctx = _build_portfolio_context(req.portfolio, user_id=uid) if req.portfolio else _build_portfolio_context(user_id=uid)

    # 多用户记忆注入（B1修复：get_memory_summary→build_memory_summary）
    if req.userId:
        try:
            from services.agent_memory import build_memory_summary
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
            print(f"[CHAT] Calling DeepSeek API...")
            system_prompt = _build_system_prompt(market_ctx, portfolio_ctx)

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
                    return {"reply": reply, "source": "ai"}
                else:
                    print(f"[CHAT] DeepSeek error: {resp.text[:200]}")
        except Exception as e:
            import traceback
            print(f"[CHAT] LLM call failed: {e}")
            traceback.print_exc()

    # 降级：规则引擎回答
    reply = _rule_based_reply(user_msg, market_ctx, portfolio_ctx)
    return {"reply": reply, "source": "rules"}


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
            from services.agent_memory import build_memory_summary
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


# ---- 市场上下文缓存（减少每次对话的预处理延迟）----
_market_ctx_cache = {"text": "", "ts": 0}
_MARKET_CTX_TTL = 300  # 5 分钟缓存

def _build_market_context() -> str:
    """构建市场数据上下文（含恐惧贪婪、技术指标、新闻），5分钟缓存"""
    now = time.time()
    if _market_ctx_cache["text"] and (now - _market_ctx_cache["ts"]) < _MARKET_CTX_TTL:
        return _market_ctx_cache["text"]
    lines = []
    try:
        fgi_data = get_fear_greed_index()
        fgi = fgi_data["score"]
        lines.append(f"恐惧贪婪指数：{fgi:.0f}/100（{fgi_data['level']}）")
        dims = fgi_data.get("dimensions", {})
        if dims:
            dim_parts = [f"{d['label']}:{d['value']}" for d in dims.values()]
            lines.append(f"  ├ 细分：{', '.join(dim_parts)}")
    except Exception:
        lines.append("恐惧贪婪指数：暂无数据")

    # 估值
    try:
        val = get_valuation_percentile()
        lines.append(f"{val['index']}估值百分位：{val['percentile']}%（{val['level']}，{val.get('metric', '')}）")
    except Exception:
        pass

    # 技术指标
    try:
        tech = get_technical_indicators()
        lines.append(f"RSI(14)：{tech['rsi']}（{tech['rsi_signal']}）")
        lines.append(f"MACD：{tech['macd']['trend']}")
        lines.append(f"布林带：{tech['bollinger']['position']}")
    except Exception:
        pass

    codes = {"110020": "沪深300", "050025": "标普500", "000216": "黄金"}
    for code, name in codes.items():
        nav = get_fund_nav(code)
        if nav["nav"] != "N/A":
            lines.append(f"{name}({code})：净值 {nav['nav']}，日涨跌 {nav['change']}%")

    # 宏观经济数据
    try:
        macro = get_macro_data()
        macro_parts = []
        for key, label in [("cpi", "CPI"), ("pmi", "PMI"), ("m2", "M2"), ("ppi", "PPI")]:
            item = macro.get(key, {})
            val = item.get("value")
            if val and val != "N/A":
                macro_parts.append(f"{label}:{val}")
        if macro_parts:
            lines.append(f"宏观数据：{' | '.join(macro_parts)}")
    except Exception:
        pass

    # 最新政策/国际新闻摘要（增强：标题+情绪标签）
    try:
        policy = get_policy_news(10)
        valid = [n for n in policy if n["title"] != "政策资讯加载中..."]
        if valid:
            # 简易情绪标签
            BULL_KW = ["降息", "降准", "宽松", "利好", "上涨", "增持", "反弹", "刺激"]
            BEAR_KW = ["加息", "收紧", "利空", "下跌", "减持", "暴跌", "制裁", "关税"]
            lines.append("\n最新政策/国际动态：")
            for n in valid[:10]:
                title = n["title"]
                if any(k in title for k in BULL_KW):
                    mood = "[利好🟢]"
                elif any(k in title for k in BEAR_KW):
                    mood = "[利空🔴]"
                else:
                    mood = "[中性]"
                lines.append(f"  - {mood} {title}")
    except Exception:
        pass

    # 新闻→持仓关联分析
    try:
        all_news = get_policy_news(10) + get_market_news(5)
        impacts = analyze_news_impact(all_news)
        if impacts:
            lines.append("\n事件对持仓的影响分析：")
            for imp in impacts[:3]:
                bull = "📈利好:" + ",".join(imp["bullish"]) if imp["bullish"] else ""
                bear = "📉利空:" + ",".join(imp["bearish"]) if imp["bearish"] else ""
                lines.append(f"  - [{imp['tag']}] {imp['impact']} {bull} {bear}")
    except Exception:
        pass

    # 全球市场数据（美股/外汇/美联储/PE 对比）
    try:
        from services.global_market import get_global_snapshot
        gs = get_global_snapshot()
        if gs.get("summary"):
            lines.append("")
            lines.append(gs["summary"])
    except Exception:
        pass

    # 国内政策数据（房地产/房价/政策新闻）
    try:
        from services.policy_data import get_policy_summary_for_context
        policy_ctx = get_policy_summary_for_context()
        if policy_ctx:
            lines.append("\n国内政策动态：")
            lines.append(policy_ctx)
    except Exception:
        pass

    # V8 扩展宏观（GDP/工业增加值/社零/固投/龙虎榜）
    try:
        from services.macro_v8 import get_v8_macro_summary
        v8_ctx = get_v8_macro_summary()
        if v8_ctx:
            lines.append("\n经济基本面：")
            lines.append(v8_ctx)
    except Exception:
        pass

    # 大宗商品 + 限售解禁 + ETF 资金流
    try:
        comm = get_commodity_prices()
        if comm.get("available"):
            parts = []
            if comm.get("gold"):
                parts.append(f"黄金{comm['gold']['price']}{comm['gold']['unit']}({comm['gold']['change_pct']:+.1f}%)")
            if comm.get("copper"):
                parts.append(f"铜{comm['copper']['price']}{comm['copper']['unit']}({comm['copper']['change_pct']:+.1f}%)")
            if parts:
                lines.append(f"\n大宗商品：{'，'.join(parts)}")
    except Exception:
        pass

    try:
        etf = get_etf_fund_flow()
        if etf.get("available") and etf.get("top_inflow"):
            top = etf["top_inflow"][0]
            lines.append(f"ETF资金流：TOP流入 {top['name']}({top['flow']:.0f}万)")
    except Exception:
        pass

    result = "\n".join(lines) if lines else "暂无市场数据"
    _market_ctx_cache["text"] = result
    _market_ctx_cache["ts"] = time.time()
    return result


def _build_portfolio_context(p=None, user_id: str = "default") -> str:
    """构建用户持仓+盈亏+风控+配置建议的完整上下文（多用户隔离）"""
    lines = []

    # 1. 基本持仓信息
    if p and p.holdings:
        lines.append(f"【用户画像】风险类型：{p.profile}，总投入：¥{p.amount:,.0f}")
        lines.append("【持仓明细】")
        for h in p.holdings:
            lines.append(f"  - {h.name}({h.code})：¥{h.amount:,.0f}，目标占比 {h.targetPct}%")
    else:
        lines.append("用户尚未通过前端传入持仓数据。")

    # 2. 风控状态
    vp_val = 50
    try:
        vp = get_valuation_percentile()
        vp_val = vp.get("percentile", 50)
        fgi_data = get_fear_greed_index()
        fgi_val = fgi_data.get("score", 50)
        from services.risk import generate_risk_actions
        actions = generate_risk_actions(vp_val, fgi_val)
        if actions:
            danger = [a for a in actions if a.get("level") == "danger"]
            warning = [a for a in actions if a.get("level") == "warning"]
            if danger or warning:
                lines.append("\n【⚠️ 风控预警】")
                for a in danger:
                    lines.append(f"  🔴 {a['message']}")
                for a in warning:
                    lines.append(f"  ⚠️ {a['message']}")
            else:
                lines.append("\n【风控状态】✅ 当前无风险预警")
    except Exception:
        pass

    # 3. 资产配置建议
    try:
        from services.portfolio import get_allocation_advice
        advice = get_allocation_advice(vp_val)
        if advice:
            t = advice.get("target", {})
            dev = advice.get("deviation", {})
            lines.append("\n【资产配置建议】")
            lines.append(f"  估值区间：{advice.get('valuation_zone', '未知')}")
            for k, label in [("stock", "股票"), ("bond", "债券"), ("cash", "现金")]:
                tgt = round(t.get(k, 0))
                d = round(dev.get(k, 0))
                lines.append(f"  {label}：目标{tgt}%，偏离{d:+d}%")
            if advice.get("summary"):
                lines.append(f"  建议：{advice['summary']}")
    except Exception:
        pass

    # 4. 持仓关联智能（个股新闻/资金流/行业/解禁）
    try:
        intel_ctx = build_holding_context(user_id)
        if intel_ctx:
            lines.append(intel_ctx)
    except Exception:
        pass

    # 5. 管理层增减持检查
    try:
        from services.macro_v8 import check_holding_management_change
        from services.stock_monitor import load_stock_holdings
        holdings = load_stock_holdings(user_id)
        codes = [h.get("code", "") for h in holdings if h.get("code")]
        if codes:
            mgmt_alerts = check_holding_management_change(codes)
            if mgmt_alerts:
                lines.append("\n【管理层增减持】")
                for a in mgmt_alerts[:3]:
                    lines.append(f"  {a['msg']}")
    except Exception:
        pass

    return "\n".join(lines) if lines else "用户尚未建仓。"


_system_prompt_template = ""
def _load_prompt_template():
    global _system_prompt_template
    if not _system_prompt_template:
        p = Path(__file__).parent / "prompts" / "system_prompt.md"
        if p.exists():
            _system_prompt_template = p.read_text(encoding="utf-8")
        else:
            _system_prompt_template = "你是钱袋子AI投顾，基于真实数据分析，不编造数字。"
    return _system_prompt_template

def _build_system_prompt(market_ctx: str, portfolio_ctx: str) -> str:
    """统一构建 DeepSeek system prompt — 7 Skill 投资分析框架，prompt 从文件加载"""
    template = _load_prompt_template()
    return f"""{template}

## 实时市场数据
{market_ctx}

## 用户持仓与风控
{portfolio_ctx}"""


def _rule_based_reply(msg: str, market_ctx: str, portfolio_ctx: str) -> str:
    """规则引擎降级回答"""
    msg_lower = msg.lower()

    # 入场时机
    if any(k in msg_lower for k in ["什么时候买", "入手", "入场", "时机", "现在能买", "适合买", "抄底"]):
        val = get_valuation_percentile()
        fgi_data = get_fear_greed_index()
        fgi = fgi_data["score"]
        timing = val["percentile"] * 0.6 + (100 - fgi) * 0.4
        if timing < 30:
            tip = "🟢 **当前非常适合入场！** 估值低+市场恐惧，是历史上最佳买入窗口。"
        elif timing < 50:
            tip = "🟡 **适合定投入场。** 估值合理，按计划定投即可。"
        elif timing < 70:
            tip = "🟠 **谨慎入场。** 估值偏高，建议降低金额或等回调。"
        else:
            tip = "🔴 **不建议大额入场。** 估值高+市场贪婪，建议等待。"
        return f"📊 入场时机分析：\n\n{tip}\n\n{val['index']}估值百分位：{val['percentile']}%（{val['level']}）\n恐惧贪婪指数：{fgi:.0f}\n\n💡 建议：不管时机好坏，定投永远是对的。定投的精髓就是穿越牛熊，低估时多买、高估时少买。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    # 止盈止损 / 卖出 / 什么价位卖
    if any(k in msg_lower for k in ["卖", "止盈", "止损", "价位", "该出", "什么时候出", "锁定利润", "减仓", "到了多少"]):
        return f"🔔 止盈止损策略：\n\n钱袋子采用**分批止盈法**，根据你的风险类型自动设定目标：\n\n🐢 保守型：+15% 止盈 / -8% 止损\n🐰 稳健型：+20% 止盈 / -10% 止损\n🦊 平衡型：+30% 止盈 / -15% 止损\n🦁 进取型：+50% 止盈 / -20% 止损\n🦅 激进型：+80% 止盈 / -25% 止损\n\n📌 操作建议：\n1️⃣ **到了止盈线，不用全卖** — 卖 1/3 锁利润，剩余继续持有\n2️⃣ **到了止损线，先看原因** — 如果基金基本面没变，可能反而是加仓机会\n3️⃣ **不设绝对卖点** — 结合估值百分位综合判断\n\n你可以在首页的 AI 信号里实时看到自己的止盈止损状态 📊\n\n⚠️ 以上仅供参考，不构成投资建议。"

    # 智能定投 / 定投方式
    if any(k in msg_lower for k in ["定投", "智能", "固定还是", "怎么投", "投多少", "每月投"]):
        val = get_valuation_percentile()
        smart = calc_smart_dca(1000, val["percentile"])
        return f"🧠 智能定投 vs 固定定投：\n\n**固定定投**：每月投相同金额，简单省心，长期有效。\n**智能定投**：根据市场估值动态调整 — 低估多买、高估少买。\n\n钱袋子的智能定投策略：\n\n| 估值百分位 | 倍率 | 说明 |\n|-----------|------|------|\n| < 20% | 1.5x | 极度低估，多买 |\n| 20-30% | 1.3x | 低估，适当多买 |\n| 30-50% | 1.1x | 偏低，略多 |\n| 50-70% | 1.0x | 正常，标准额 |\n| 70-85% | 0.7x | 偏高，少买 |\n| > 85% | 0.3x | 高估，大幅减少 |\n\n📊 当前{val['index']}估值：{val['percentile']}%（{val['level']}）\n💡 建议本月倍率：{smart['multiplier']}x — {smart['advice']}\n\n智能定投比固定定投长期多赚约 15-20%，但需要坚持 3 年以上才能看到效果。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    if any(k in msg_lower for k in ["跌", "亏", "赔", "绿", "下跌"]):
        return f"📉 市场波动是正常现象。\n\n{market_ctx}\n\n长期投资（3年+）能大幅平滑短期波动。如果你的资产配比还在目标范围内，建议保持定投节奏，不要恐慌卖出。记住投资铁律：跌了别卖，越跌越该买。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    if any(k in msg_lower for k in ["涨", "赚", "红", "上涨", "牛"]):
        return f"📈 恭喜！不过也别过于乐观。\n\n{market_ctx}\n\n赚钱时更要冷静，检查一下各资产的占比是否偏离目标太多。如果某类资产涨太多导致占比过高，可以考虑再平衡——卖掉一点涨多的，买入涨少的。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    # 特定资产类问题（优先于通用"买/卖"匹配，避免"黄金还能买吗"被通用规则拦截）
    if any(k in msg_lower for k in ["黄金", "gold"]):
        nav = get_fund_nav("000216")
        news = get_fund_news("000216", 3)
        news_text = "\n".join([f"📰 {n['title']}" for n in news if n["title"] != "黄金市场动态获取中..."])
        return f"🪙 黄金近况：净值 {nav['nav']}，日涨跌 {nav['change']}%。\n\n{news_text}\n\n黄金是经典避险资产，近年全球央行持续增持。在你的配置中作为分散风险的角色，建议保持目标占比即可，不用频繁操作。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    if any(k in msg_lower for k in ["标普", "美股", "s&p", "sp500"]):
        nav = get_fund_nav("050025")
        news = get_fund_news("050025", 3)
        news_text = "\n".join([f"📰 {n['title']}" for n in news if "获取中" not in n["title"]])
        return f"🇺🇸 标普500近况：净值 {nav['nav']}，日涨跌 {nav['change']}%。\n\n{news_text}\n\n标普500追踪美国500强企业，过去30年年化约10%。分散地域风险的核心配置。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    if any(k in msg_lower for k in ["沪深", "a股", "300", "大盘"]):
        nav = get_fund_nav("110020")
        news = get_fund_news("110020", 3)
        news_text = "\n".join([f"📰 {n['title']}" for n in news if "获取中" not in n["title"]])
        return f"📊 沪深300近况：净值 {nav['nav']}，日涨跌 {nav['change']}%。\n\n{news_text}\n\n沪深300覆盖A股市值最大的300家公司，是A股的核心指数。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    if any(k in msg_lower for k in ["债券", "债基", "纯债"]):
        nav = get_fund_nav("217022")
        return f"🏦 债券近况：招商产业债A 净值 {nav['nav']}，日涨跌 {nav['change']}%。\n\n纯债基金是组合的\"稳定器\"，历史几乎没有亏过年度。适合保守资金配置。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    if any(k in msg_lower for k in ["买", "加仓", "什么时候"]):
        return f"💰 关于买入时机：\n\n{market_ctx}\n\n定投的精髓就是「不择时」——每月固定日期买入，无论涨跌。这样长期下来会自动实现低买多、高买少。如果恐惧指数很高（市场极度悲观），可以适当多买一点。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    # 政策/大宗商品/行业影响分析
    if any(k in msg_lower for k in ["政策", "降息", "降准", "关税", "贸易战", "制裁", "战争", "地缘",
                                     "大宗", "油价", "原油", "opec", "影响", "利好", "利空",
                                     "行业", "板块", "半导体", "芯片", "基建"]):
        try:
            all_news = get_policy_news(10) + get_market_news(5)
            impacts = analyze_news_impact(all_news)
            if impacts:
                impact_lines = []
                for imp in impacts[:4]:
                    bull = "📈利好：" + "、".join(imp["bullish"]) if imp["bullish"] else ""
                    bear = "📉利空：" + "、".join(imp["bearish"]) if imp["bearish"] else ""
                    impact_lines.append(f"🏷️ **{imp['tag']}**\n{imp['impact']}\n{bull} {bear}\n涉及行业：{', '.join(imp['sectors'])}")
                impact_text = "\n\n".join(impact_lines)
                return f"🏛️ 当前事件对你持仓的影响分析：\n\n{impact_text}\n\n💡 建议：关注事件发展趋势，短期波动不改长期逻辑。如果你是定投模式，保持节奏即可。\n\n⚠️ 以上基于关键词匹配的初步分析，仅供参考，不构成投资建议。"
            else:
                return "🏛️ 当前暂未检测到对你持仓有显著影响的政策/事件。\n\n💡 保持定投节奏，不需要每天看新闻做决定。\n\n⚠️ 以上仅供参考，不构成投资建议。"
        except Exception:
            return "🏛️ 政策影响分析暂时无法获取，请稍后再试。"

    # 新闻/资讯/发生了什么
    if any(k in msg_lower for k in ["新闻", "资讯", "消息", "发生", "怎么了", "什么情况", "为什么"]):
        news = get_market_news(5)
        news_lines = []
        for n in news[:5]:
            if n.get("url"):
                news_lines.append(f'📰 <a href="{n["url"]}" target="_blank" style="color:#F59E0B;text-decoration:underline">{n["title"]}</a>（{n["source"]}）')
            else:
                news_lines.append(f"📰 {n['title']}（{n['source']}）")
        news_text = "\n".join(news_lines)
        return f"📰 最新市场资讯：\n\n{news_text}\n\n💡 建议：关注大趋势，不要因为单条新闻做决定。投资看的是长期逻辑。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    # 技术分析
    if any(k in msg_lower for k in ["技术", "rsi", "macd", "布林", "超买", "超卖", "指标"]):
        tech = get_technical_indicators()
        return f"📊 沪深300技术指标：\n\n📈 RSI(14)：{tech['rsi']}（{tech['rsi_signal']}）\n  └ >70 超买区，<30 超卖区\n\n📉 MACD：{tech['macd']['trend']}\n  └ DIF:{tech['macd']['dif']:.4f} DEA:{tech['macd']['dea']:.4f}\n\n📐 布林带：{tech['bollinger']['position']}\n  └ 上轨:{tech['bollinger']['upper']} 中轨:{tech['bollinger']['middle']} 下轨:{tech['bollinger']['lower']}\n\n💡 技术指标是辅助参考，不能单独作为买卖依据。结合估值+基本面综合判断更靠谱。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    # 宏观/经济/cpi/pmi
    if any(k in msg_lower for k in ["宏观", "经济", "cpi", "pmi", "通胀", "利率", "货币", "m2"]):
        events = get_macro_calendar()
        macro_text = "\n".join([f"{e['icon']} {e['name']}：{e['value']}（{e['date']}）\n  └ {e['impact']}" for e in events])
        return f"🏛️ 宏观经济数据：\n\n{macro_text}\n\n💡 宏观数据影响市场整体方向。CPI低+PMI>50+M2宽松 = 对股市友好的环境。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    return f"🤔 关于你的问题：\n\n当前市场概况：\n{market_ctx}\n\n{portfolio_ctx}\n\n你可以问我：\n📰 「最近有什么新闻？」\n📊 「技术指标怎么样？」\n🏛️ 「宏观经济怎么样？」\n🎯 「现在适合入场吗？」\n💰 「什么时候该卖？」\n🧠 「定投多少合适？」\n\n⚠️ 以上仅供参考，不构成投资建议。"


# ---- API: 数据持久化 ----

@app.post("/api/user/save")
def save_user_data(data: UserData):
    """保存用户数据到服务端（兼容V3和V4）"""
    user = load_user(data.userId)
    if data.portfolio:
        # 接受 dict 格式，兼容 V3 和 V4
        if isinstance(data.portfolio, dict):
            user["portfolio"] = data.portfolio
        else:
            user["portfolio"] = data.portfolio
    if data.ledger:
        user["ledger"] = data.ledger
    if not user.get("createdAt"):
        user["createdAt"] = datetime.now().isoformat()
    save_user(user)
    return {"status": "ok", "userId": data.userId}

@app.get("/api/user/{user_id}")
def get_user_data(user_id: str):
    """读取用户数据"""
    user = load_user(user_id)
    return user

@app.delete("/api/user/{user_id}")
def delete_user_data(user_id: str):
    """删除用户数据"""
    f = _user_file(user_id)
    if f.exists():
        f.unlink()
    return {"status": "ok"}


# ---- API: 用户偏好（Phase 0 新增）----

# 默认偏好
USER_DEFAULTS = {
    "display_mode": "simple",        # 'simple' | 'pro'
    "risk_profile": "balanced",      # conservative / balanced / growth / aggressive
    "push_preferences": {
        "morning_brief": True,
        "closing_review": True,
        "risk_alert": True,
        "trade_signal": True,
        "breaking_news": True,
    },
    "watchlist_config": {
        "stop_loss_pct": -0.08,
        "take_profit_pct": 0.20,
        "price_alert_range": 0.05,
    },
}

# 两个用户的个性化覆盖
USER_OVERRIDES = {
    "LeiJiang": {
        "display_mode": "pro",
        "risk_profile": "growth",
        "push_preferences": {
            "morning_brief": True, "closing_review": True,
            "risk_alert": True, "trade_signal": True, "breaking_news": True,
        },
        "watchlist_config": {
            "stop_loss_pct": -0.10, "take_profit_pct": 0.25, "price_alert_range": 0.05,
        },
    },
    "BuLuoGeLi": {
        "display_mode": "simple",
        "risk_profile": "balanced",
        "push_preferences": {
            "morning_brief": True, "closing_review": False,
            "risk_alert": True, "trade_signal": False, "breaking_news": False,
        },
        "watchlist_config": {
            "stop_loss_pct": -0.05, "take_profit_pct": 0.15, "price_alert_range": 0.03,
        },
    },
}

@app.get("/api/user/preference")
def get_user_preference(userId: str):
    """获取用户偏好（Simple/Pro模式、推送、盯盘阈值）"""
    user = load_user(userId)
    defaults = USER_DEFAULTS.copy()
    overrides = USER_OVERRIDES.get(userId, {})

    return {
        "display_mode": user.get("display_mode", overrides.get("display_mode", defaults["display_mode"])),
        "risk_profile": user.get("risk_profile", overrides.get("risk_profile", defaults["risk_profile"])),
        "push_preferences": user.get("push_preferences", overrides.get("push_preferences", defaults["push_preferences"])),
        "watchlist_config": user.get("watchlist_config", overrides.get("watchlist_config", defaults["watchlist_config"])),
    }

@app.put("/api/user/preference")
def update_user_preference(userId: str, body: dict):
    """更新用户偏好"""
    from services.audit_log import audit_log
    user = load_user(userId)

    changed = {}
    for key in ["display_mode", "risk_profile", "push_preferences", "watchlist_config"]:
        if key in body:
            old_val = user.get(key)
            user[key] = body[key]
            changed[key] = {"old": old_val, "new": body[key]}

    save_user(user)
    audit_log("preference_update", user_id=userId, detail=changed)
    return {"success": True, "changed": list(changed.keys())}


# ---- API: OCR 记账 ----

@app.post("/api/receipt/ocr")
async def ocr_receipt(file: UploadFile = File(...), userId: str = Form("")):
    """拍照识别小票 → 自动提取金额和商品"""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "请上传图片文件")

    # 保存图片
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(400, "图片不能超过 10MB")

    ext = file.filename.split(".")[-1] if file.filename and "." in file.filename else "jpg"
    receipt_id = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
    receipt_path = RECEIPTS_DIR / f"{receipt_id}.{ext}"
    receipt_path.write_bytes(content)

    # 尝试 OCR
    ocr_result = await _do_ocr(receipt_path, content)

    # 如果有用户 ID，根据截图类型自动处理
    if userId and ocr_result.get("amount", 0) > 0:
        user = load_user(userId)
        screenshot_type = ocr_result.get("screenshot_type", "consumption")

        if screenshot_type in ("fund_buy", "fund_sell"):
            # 基金买入/卖出截图 → 生成交易记录
            user = ensure_v4_portfolio(user)
            p = user["portfolio"]
            tx_id = f"tx_ocr_{receipt_id}"
            tx = {
                "id": tx_id,
                "type": "BUY" if screenshot_type == "fund_buy" else "SELL",
                "code": ocr_result.get("fund_code", ""),
                "name": ocr_result.get("fund_name", ""),
                "amount": ocr_result["amount"],
                "shares": ocr_result.get("shares", 0),
                "nav": ocr_result.get("nav", 0),
                "fee": 0,
                "date": ocr_result.get("date", datetime.now().isoformat()),
                "source": "ocr",
                "note": f"OCR识别 - {ocr_result.get('fund_name', '')}",
            }
            p["transactions"].append(tx)
            save_user(user)
            ocr_result["saved"] = True
            ocr_result["savedAs"] = "transaction"
            ocr_result["transaction"] = tx

        elif screenshot_type == "bank_tx" and ocr_result.get("bank_balance", 0) > 0:
            # 银行交易截图 → 更新资产余额 + 记账
            user = ensure_v4_portfolio(user)
            p = user["portfolio"]
            bank_name = ocr_result.get("merchant", "银行卡")
            existing = None
            for a in p.get("assets", []):
                if a.get("type") == "cash" and bank_name in a.get("name", ""):
                    existing = a
                    break
            if existing:
                existing["balance"] = ocr_result["bank_balance"]
                existing["updated"] = datetime.now().strftime("%Y-%m-%d")
            else:
                p.setdefault("assets", []).append({
                    "id": f"a_ocr_{receipt_id}",
                    "type": "cash",
                    "name": bank_name,
                    "balance": ocr_result["bank_balance"],
                    "icon": "🏦",
                    "updated": datetime.now().strftime("%Y-%m-%d"),
                })
            entry = {
                "id": receipt_id,
                "date": datetime.now().isoformat(),
                "amount": ocr_result["amount"],
                "category": "其他",
                "note": f"银行交易 - {bank_name}",
                "source": "ocr",
                "receiptFile": str(receipt_path.name),
            }
            user.setdefault("ledger", []).append(entry)
            save_user(user)
            ocr_result["saved"] = True
            ocr_result["savedAs"] = "asset+ledger"

        else:
            # 普通消费截图 → 记账
            entry = {
                "id": receipt_id,
                "date": datetime.now().isoformat(),
                "amount": ocr_result["amount"],
                "category": ocr_result.get("category", "其他"),
                "note": ocr_result.get("merchant", ocr_result.get("note", "OCR")),
                "source": "ocr",
                "receiptFile": str(receipt_path.name),
            }
            user.setdefault("ledger", []).append(entry)
            save_user(user)
            ocr_result["saved"] = True
            ocr_result["savedAs"] = "ledger"
            ocr_result["entryId"] = receipt_id

    return ocr_result


async def _do_ocr(file_path: Path, content: bytes) -> dict:
    """执行 OCR，优先用 LLM 多模态，降级用本地 OCR"""

    # 方案1：用 LLM 多模态识别（最准）
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
    api_base = os.environ.get("LLM_API_BASE", "https://api.openai.com/v1")
    vision_model = os.environ.get("LLM_VISION_MODEL", "gpt-4o-mini")

    if api_key:
        try:
            import base64
            import httpx
            b64 = base64.b64encode(content).decode()
            mime = "image/jpeg"
            if str(file_path).endswith(".png"):
                mime = "image/png"

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{api_base}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": vision_model,
                        "messages": [
                            {"role": "system", "content": """你是一个金融记录识别助手。请识别截图类型并提取信息。

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
  "category": "分类",
  "note": "备注",
  "fund_code": "基金代码(如有)",
  "fund_name": "基金名(如有)",
  "shares": 份额数(如有),
  "nav": 净值(如有),
  "date": "日期(如有)",
  "bank_balance": 银行余额(如有),
  "records": [多条记录(如有)],
  "confidence": 0.95
}"""},
                            {"role": "user", "content": [
                                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                                {"type": "text", "text": "请识别这张截图的信息，返回 JSON。"},
                            ]},
                        ],
                        "max_tokens": 800,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    text = data["choices"][0]["message"]["content"]
                    # 提取 JSON
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
        except Exception as e:
            print(f"[OCR] LLM vision failed: {e}")

    # 方案2：本地 OCR（pytesseract）
    try:
        from PIL import Image
        import pytesseract
        import re

        img = Image.open(file_path)
        text = pytesseract.image_to_string(img, lang="chi_sim+eng")

        # 简易提取金额
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
    except Exception as e:
        print(f"[OCR] Tesseract failed: {e}")

    return {
        "amount": 0,
        "merchant": "",
        "category": "其他",
        "note": "OCR 识别失败，请手动输入",
        "source": "none",
        "raw": "",
    }


@app.post("/api/ledger/add")
def add_ledger_entry(entry: LedgerEntry):
    """手动添加记账条目（支持收入/支出）"""
    user = load_user(entry.userId)
    item = {
        "id": f"{int(time.time())}_{uuid.uuid4().hex[:8]}",
        "date": entry.date or datetime.now().isoformat(),
        "amount": entry.amount,
        "category": entry.category,
        "note": entry.note,
        "direction": entry.direction,  # "income" or "expense"
        "source": "manual",
    }
    user.setdefault("ledger", []).append(item)
    save_user(user)
    return {"status": "ok", "entry": item}

@app.get("/api/ledger/{user_id}")
def get_ledger(user_id: str):
    """获取用户记账列表"""
    user = load_user(user_id)
    return {"ledger": user.get("ledger", [])}

@app.get("/api/ledger/{user_id}/summary")
def get_ledger_summary(user_id: str, days: int = 30):
    """获取记账统计摘要（区分收入/支出）"""
    user = load_user(user_id)
    ledger = user.get("ledger", [])

    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    recent = [e for e in ledger if e.get("date", "") >= cutoff]

    # 按方向+分类汇总
    expense_by_cat = {}
    income_by_cat = {}
    total_expense = 0
    total_income = 0
    for e in recent:
        cat = e.get("category", "其他")
        amt = e.get("amount", 0)
        direction = e.get("direction", "expense")
        if direction == "income":
            income_by_cat[cat] = income_by_cat.get(cat, 0) + amt
            total_income += amt
        else:
            expense_by_cat[cat] = expense_by_cat.get(cat, 0) + amt
            total_expense += amt

    return {
        "period": f"近{days}天",
        "totalExpense": round(total_expense, 2),
        "totalIncome": round(total_income, 2),
        "netCashFlow": round(total_income - total_expense, 2),
        "count": len(recent),
        "expenseByCategory": expense_by_cat,
        "incomeByCategory": income_by_cat,
        # 兼容旧字段
        "totalSpent": round(total_expense, 2),
        "byCategory": expense_by_cat,
    }


# ============================================================
# 收入源管理 API
# ============================================================

@app.post("/api/income-sources/add")
def add_income_source(src: IncomeSourceCreate):
    """登记新收入源（民宿/出租房/外包/兼职等）"""
    user = load_user(src.userId)
    sources = user.setdefault("income_sources", [])
    new_src = {
        "id": f"src_{int(time.time())}_{uuid.uuid4().hex[:6]}",
        "name": src.name,
        "type": src.type,
        "expectedAmt": src.expectedAmt,
        "note": src.note,
        "createdAt": datetime.now().isoformat(),
        "lastRecordAt": None,
        "totalRecorded": 0,
        "recordCount": 0,
    }
    sources.append(new_src)
    save_user(user)
    return {"ok": True, "source": new_src}

@app.get("/api/income-sources/{user_id}")
def get_income_sources(user_id: str):
    """获取用户所有收入源"""
    user = load_user(user_id)
    return {"sources": user.get("income_sources", [])}

@app.delete("/api/income-sources/{user_id}/{source_id}")
def delete_income_source(user_id: str, source_id: str):
    """删除收入源"""
    user = load_user(user_id)
    sources = user.get("income_sources", [])
    user["income_sources"] = [s for s in sources if s.get("id") != source_id]
    save_user(user)
    return {"ok": True}

@app.post("/api/income-sources/record")
def record_from_source(req: IncomeSourceRecord):
    """从收入源快速入账（一键记录本月收入）"""
    user = load_user(req.userId)
    sources = user.get("income_sources", [])
    src = next((s for s in sources if s.get("id") == req.sourceId), None)
    if not src:
        raise HTTPException(status_code=404, detail="收入源不存在")

    # 写入记账
    ledger = user.setdefault("ledger", [])
    entry = {
        "id": f"{int(time.time())}_{uuid.uuid4().hex[:8]}",
        "date": datetime.now().isoformat(),
        "amount": req.amount,
        "category": src.get("type", "其他"),
        "note": src.get("name", ""),
        "direction": "income",
        "source": "income_source",
        "sourceId": req.sourceId,
    }
    ledger.append(entry)

    # 更新收入源统计
    src["lastRecordAt"] = datetime.now().isoformat()
    src["totalRecorded"] = src.get("totalRecorded", 0) + req.amount
    src["recordCount"] = src.get("recordCount", 0) + 1

    save_user(user)
    return {"ok": True, "entry": entry, "source": src}


# ---- API: 数据健康检查 & 自动审计 ----

@app.get("/api/health/data-audit")
def data_audit():
    """自动审计所有关键数据源的新鲜度和准确性"""
    checks = []
    overall_ok = True

    # 1. 宏观数据新鲜度检查
    try:
        macro = get_macro_data()
        for key, label in [("cpi", "CPI"), ("pmi", "PMI"), ("m2", "M2"), ("ppi", "PPI")]:
            item = macro.get(key, {})
            val = item.get("value")
            period = item.get("period", "")
            if val is None or val == "N/A":
                checks.append({"name": f"宏观·{label}", "status": "error", "msg": "数据缺失", "value": None})
                overall_ok = False
            else:
                # 检查数据是否超过60天未更新
                fresh = True
                if period:
                    try:
                        from datetime import datetime as _dt
                        p = period.replace("年", "-").replace("月", "").strip()
                        data_date = _dt.strptime(p, "%Y-%m")
                        days_old = (datetime.now() - data_date).days
                        if days_old > 90:
                            fresh = False
                    except Exception:
                        pass
                status = "ok" if fresh else "warn"
                if not fresh:
                    overall_ok = False
                checks.append({"name": f"宏观·{label}", "status": status, "msg": f"{period} {val}", "value": val})
    except Exception as e:
        checks.append({"name": "宏观数据", "status": "error", "msg": f"获取失败: {e}", "value": None})
        overall_ok = False

    # 2. 估值数据检查
    try:
        val = get_valuation_percentile()
        pe = val.get("pe_ttm")
        pct = val.get("percentile")
        source = val.get("source", "未知")
        if pe and 5 < pe < 50 and pct is not None:
            checks.append({"name": "估值·PE-TTM", "status": "ok", "msg": f"PE={pe} 百分位={pct}% ({source})", "value": pe})
        else:
            checks.append({"name": "估值·PE-TTM", "status": "warn", "msg": f"数据可能异常: PE={pe} ({source})", "value": pe})
            overall_ok = False
    except Exception as e:
        checks.append({"name": "估值数据", "status": "error", "msg": f"获取失败: {e}", "value": None})
        overall_ok = False

    # 3. 基金净值新鲜度检查
    fund_codes = ["110020", "050025", "217022", "000216", "008114"]
    for code in fund_codes:
        try:
            nav = get_fund_nav(code)
            if nav["nav"] == "N/A":
                checks.append({"name": f"基金净值·{code}", "status": "error", "msg": "获取失败", "value": None})
                overall_ok = False
            else:
                nav_date = nav.get("date", "")
                is_fresh = True
                if nav_date:
                    try:
                        nd = datetime.strptime(nav_date, "%Y-%m-%d")
                        # 允许周末和节假日有3天延迟
                        if (datetime.now() - nd).days > 5:
                            is_fresh = False
                    except Exception:
                        pass
                status = "ok" if is_fresh else "warn"
                if not is_fresh:
                    overall_ok = False
                checks.append({"name": f"基金净值·{code}", "status": status, "msg": f"净值={nav['nav']} ({nav_date})", "value": nav["nav"]})
        except Exception as e:
            checks.append({"name": f"基金净值·{code}", "status": "error", "msg": str(e), "value": None})
            overall_ok = False

    # 4. 新闻内容相关性检查
    try:
        news = get_market_news(10)
        foreign_keywords = ["伦敦", "荷兰", "法兰克福", "多伦多", "澳洲", "欧洲央行", "英镑"]
        foreign_count = sum(1 for n in news if any(k in n.get("title", "") for k in foreign_keywords))
        if foreign_count > 3:
            checks.append({"name": "新闻相关性", "status": "warn", "msg": f"10条新闻中{foreign_count}条疑似海外无关内容", "value": foreign_count})
            overall_ok = False
        else:
            checks.append({"name": "新闻相关性", "status": "ok", "msg": f"10条新闻中{foreign_count}条海外内容（正常范围）", "value": foreign_count})
    except Exception as e:
        checks.append({"name": "新闻数据", "status": "error", "msg": f"获取失败: {e}", "value": None})
        overall_ok = False

    # 5. API 响应时间检查
    import time
    try:
        t0 = time.time()
        get_fear_greed_index()
        elapsed = round((time.time() - t0) * 1000)
        status = "ok" if elapsed < 5000 else ("warn" if elapsed < 15000 else "error")
        if status != "ok":
            overall_ok = False
        checks.append({"name": "API响应·恐贪指数", "status": status, "msg": f"{elapsed}ms", "value": elapsed})
    except Exception as e:
        checks.append({"name": "API响应·恐贪指数", "status": "error", "msg": str(e), "value": None})
        overall_ok = False

    error_count = sum(1 for c in checks if c["status"] == "error")
    warn_count = sum(1 for c in checks if c["status"] == "warn")
    ok_count = sum(1 for c in checks if c["status"] == "ok")

    return {
        "overall": "healthy" if overall_ok else ("degraded" if error_count == 0 else "unhealthy"),
        "summary": f"✅{ok_count} ⚠️{warn_count} ❌{error_count}",
        "checks": checks,
        "timestamp": datetime.now().isoformat(),
    }


# ---- 静态文件服务（部署时前后端一体）----
FRONTEND_DIR = Path(__file__).resolve().parent.parent  # moneybag/

# 静态资源缓存配置（秒）
_CACHE_RULES = {
    ".js": "public, max-age=300, stale-while-revalidate=86400",   # 5 min + 后台刷新
    ".css": "public, max-age=300, stale-while-revalidate=86400",
    ".png": "public, max-age=604800",  # 图标 7 天
    ".ico": "public, max-age=604800",
    ".json": "public, max-age=60",     # manifest 等 1 分钟
    ".html": "no-cache",               # HTML 每次验证
}

def _cached_file_response(fp: Path) -> FileResponse:
    """返回带 Cache-Control 的 FileResponse"""
    suffix = fp.suffix.lower()
    headers = {}
    if suffix in _CACHE_RULES:
        headers["Cache-Control"] = _CACHE_RULES[suffix]
    return FileResponse(fp, headers=headers)

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
)
from services.agent_engine import run_analysis_cycle, save_signal_file

@app.get("/api/agent/memory/{user_id}")
def get_agent_memory(user_id: str):
    """获取用户记忆摘要"""
    return {
        "preferences": get_preferences(user_id),
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


# ============================================================
# 六大量化引擎 API（对标幻方量化）
# ============================================================

# ---- P1: AI 预测引擎 ----
@app.get("/api/ai-predict/{code}")
def api_ai_predict(code: str, days: int = 5):
    """AI 预测单只股票未来 N 天涨跌"""
    from services.ai_predictor import predict_stock
    return predict_stock(code, forward_days=days)

@app.get("/api/ai-predict/portfolio/{user_id}")
def api_ai_predict_portfolio(user_id: str, days: int = 5):
    """AI 预测用户持仓组合"""
    from services.ai_predictor import predict_portfolio
    return predict_portfolio(user_id, forward_days=days)

@app.post("/api/ai-predict/batch")
def api_ai_predict_batch(request: Request):
    """批量预测多只股票"""
    import asyncio
    body = asyncio.get_event_loop().run_until_complete(request.json())
    codes = body.get("codes", [])
    days = body.get("days", 5)
    from services.ai_predictor import batch_predict
    return batch_predict(codes, forward_days=days)

# ---- P2: 遗传编程因子挖掘 ----
@app.get("/api/genetic-factor/{code}")
def api_genetic_factor(code: str, generations: int = 30, top_k: int = 10):
    """对单只股票运行遗传因子挖掘"""
    from services.genetic_factor import evolve_factors
    return evolve_factors(code=code, generations=generations, top_k=top_k)

# ---- P3: 组合优化器 ----
@app.get("/api/portfolio-optimize/{user_id}")
def api_portfolio_optimize(user_id: str, method: str = "all", max_weight: float = 0.20):
    """组合优化：5种方法对比"""
    from services.portfolio_optimizer import optimize_portfolio
    return optimize_portfolio(user_id, method=method, max_weight=max_weight)

# ---- P4: 另类数据仪表盘 ----
@app.get("/api/alt-data/dashboard")
def api_alt_data_dashboard():
    """另类数据综合仪表盘"""
    from services.alt_data import get_alt_data_dashboard
    return get_alt_data_dashboard()

@app.get("/api/alt-data/northbound")
def api_alt_northbound():
    """北向资金详情"""
    from services.alt_data import get_northbound_flow_detail
    return get_northbound_flow_detail()

@app.get("/api/alt-data/margin")
def api_alt_margin():
    """融资融券详情"""
    from services.alt_data import get_margin_detail
    return get_margin_detail()

@app.get("/api/alt-data/dragon-tiger")
def api_alt_dragon_tiger():
    """龙虎榜"""
    from services.alt_data import get_dragon_tiger
    return get_dragon_tiger()

@app.get("/api/alt-data/block-trade")
def api_alt_block_trade():
    """大宗交易"""
    from services.alt_data import get_block_trade
    return get_block_trade()

@app.get("/api/alt-data/sector-flow")
def api_alt_sector_flow():
    """行业资金流"""
    from services.alt_data import get_sector_flow
    return get_sector_flow()

# ---- P5: 强化学习仓位建议 ----
@app.get("/api/rl-position/{code}")
def api_rl_position(code: str):
    """RL 仓位建议（单只股票）"""
    from services.rl_position import get_rl_recommendation
    return get_rl_recommendation(code)

@app.get("/api/rl-position/portfolio/{user_id}")
def api_rl_portfolio(user_id: str):
    """RL 仓位建议（全部持仓）"""
    from services.rl_position import get_rl_portfolio_advice
    return get_rl_portfolio_advice(user_id)

# ---- P6: LLM 因子生成 ----
@app.get("/api/llm-factor/{code}")
def api_llm_factor(code: str, count: int = 5, iterations: int = 2):
    """LLM 驱动的 Alpha 因子生成"""
    from services.llm_factor_gen import generate_alpha_factors
    return generate_alpha_factors(code=code, count=count, iterations=iterations)


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

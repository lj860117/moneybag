"""
钱袋子 — FastAPI 后端 V4.0
交易流水制 + 全资产管理 + OCR增强 + 实时净值 + AI对话 + 买卖信号
"""
import os
import json
import time
import uuid
import hashlib
import math
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Literal

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

app = FastAPI(title="钱袋子 API", version="4.0.0")

# CORS - 允许前端跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- 持久化目录 ----
DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
USERS_DIR = DATA_DIR / "users"
USERS_DIR.mkdir(exist_ok=True)
RECEIPTS_DIR = DATA_DIR / "receipts"
RECEIPTS_DIR.mkdir(exist_ok=True)

# ---- 缓存 ----
nav_cache = {}
news_cache = {}
macro_cache = {}
NAV_CACHE_TTL = 3600  # 1小时缓存
NEWS_CACHE_TTL = 1800  # 30分钟缓存
MACRO_CACHE_TTL = 7200  # 2小时缓存

# ---- 数据模型 ----

# V3 旧模型（兼容）
class Holding(BaseModel):
    code: str
    name: str
    category: str
    targetPct: float
    amount: float
    buyDate: str

class Portfolio(BaseModel):
    """V3 兼容 Portfolio — 旧的 holdings 快照模式"""
    holdings: list[Holding] = []
    history: list = []
    profile: Optional[str] = None
    amount: float = 0

# V4 新模型 — 交易流水制
class Transaction(BaseModel):
    id: str = ""
    type: Literal["BUY", "SELL", "DIVIDEND"] = "BUY"
    code: str
    name: str = ""
    amount: float = 0        # 买入金额（BUY 时）
    shares: float = 0        # 份额
    nav: float = 0           # 成交净值
    fee: float = 0           # 手续费
    date: str = ""           # 交易日期
    source: str = "manual"   # recommend|manual|ocr|topup
    note: str = ""

class Asset(BaseModel):
    id: str = ""
    type: Literal["cash", "property", "liability", "other"] = "cash"
    name: str = ""
    balance: float = 0       # cash/liability 用 balance
    value: float = 0         # property/other 用 value
    icon: str = ""
    updated: str = ""

class PortfolioV4(BaseModel):
    """V4 交易流水制 Portfolio"""
    transactions: list[Transaction] = []
    assets: list[Asset] = []
    profile: Optional[str] = None
    history: list = []
    version: int = 4

class TransactionRequest(BaseModel):
    userId: str
    transaction: Transaction

class AssetRequest(BaseModel):
    userId: str
    asset: Asset

class TopupRequest(BaseModel):
    userId: str
    amount: float
    profile: Optional[str] = None
    allocations: list[dict] = []  # [{code, name, pct, amount}]

class UserData(BaseModel):
    userId: str
    portfolio: Optional[dict] = None  # 兼容 V3 和 V4
    ledger: list = []
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None

class ChatRequest(BaseModel):
    message: str
    portfolio: Optional[Portfolio] = None

class LedgerEntry(BaseModel):
    userId: str
    category: str
    amount: float
    note: str = ""
    date: Optional[str] = None
    direction: Literal["expense", "income"] = "expense"  # 收入/支出

class FundSearchResult(BaseModel):
    code: str
    name: str
    type: str = ""


# ---- V4 核心计算引擎 ----

def calc_holdings_from_transactions(transactions: list[dict]) -> dict:
    """从交易流水计算当前持仓（加权平均成本法）
    参考：Ghostfolio Portfolio Calculator
    """
    holdings = {}  # code -> {shares, totalCost, avgNav, name, txCount}
    realized = {}  # code -> 已实现盈亏

    sorted_txs = sorted(transactions, key=lambda t: t.get("date", ""))

    for tx in sorted_txs:
        code = tx.get("code", "")
        if not code:
            continue

        if code not in holdings:
            holdings[code] = {
                "code": code,
                "name": tx.get("name", ""),
                "shares": 0,
                "totalCost": 0,
                "avgNav": 0,
                "txCount": 0,
                "firstBuyDate": tx.get("date", ""),
            }
        h = holdings[code]

        tx_type = tx.get("type", "BUY")
        if tx_type == "BUY":
            amount = tx.get("amount", 0)
            fee = tx.get("fee", 0)
            shares = tx.get("shares", 0)
            h["totalCost"] += amount + fee
            h["shares"] += shares
            h["avgNav"] = h["totalCost"] / h["shares"] if h["shares"] > 0 else 0
            h["txCount"] += 1
            if not h.get("name"):
                h["name"] = tx.get("name", "")

        elif tx_type == "SELL":
            shares_to_sell = tx.get("shares", 0)
            sell_nav = tx.get("nav", 0)
            fee = tx.get("fee", 0)
            if h["shares"] > 0 and shares_to_sell > 0:
                sell_cost = shares_to_sell * h["avgNav"]
                sell_revenue = shares_to_sell * sell_nav - fee
                realized[code] = realized.get(code, 0) + (sell_revenue - sell_cost)
                h["totalCost"] -= sell_cost
                h["shares"] -= shares_to_sell
                if h["shares"] < 0:
                    h["shares"] = 0

        elif tx_type == "DIVIDEND":
            div_amount = tx.get("amount", 0)
            realized[code] = realized.get(code, 0) + div_amount

    active = [h for h in holdings.values() if h["shares"] > 0]
    closed = [h for h in holdings.values() if h["shares"] <= 0 and h["txCount"] > 0]

    return {
        "active": active,
        "realized": realized,
        "closed": closed,
    }


def migrate_v3_to_v4(old_portfolio: dict) -> dict:
    """将 V3 holdings 快照转为 V4 交易流水"""
    transactions = []
    old_holdings = old_portfolio.get("holdings", [])

    for h in old_holdings:
        code = h.get("code", "")
        if not code:
            continue
        tx_id = f"migrate_{code}_{uuid.uuid4().hex[:6]}"
        transactions.append({
            "id": tx_id,
            "type": "BUY",
            "code": code,
            "name": h.get("name", ""),
            "amount": h.get("amount", 0),
            "shares": 0,  # 待后端补算
            "nav": 0,     # 待后端补算
            "fee": 0,
            "date": h.get("buyDate", datetime.now().isoformat()),
            "source": "recommend",
            "note": "V3迁移",
        })

    return {
        "transactions": transactions,
        "assets": [],
        "profile": old_portfolio.get("profile"),
        "history": old_portfolio.get("history", []),
        "version": 4,
    }


def ensure_v4_portfolio(user_data: dict) -> dict:
    """确保用户数据中的 portfolio 是 V4 格式"""
    p = user_data.get("portfolio")
    if not p:
        user_data["portfolio"] = {
            "transactions": [],
            "assets": [],
            "profile": None,
            "history": [],
            "version": 4,
        }
        return user_data

    if p.get("version") == 4:
        return user_data

    # V3 → V4 迁移
    if "holdings" in p and p["holdings"]:
        user_data["portfolio"] = migrate_v3_to_v4(p)
        # 补算净值和份额
        for tx in user_data["portfolio"]["transactions"]:
            if tx["shares"] == 0 and tx["amount"] > 0:
                buy_nav = _get_nav_on_date(tx["code"], tx["date"])
                if buy_nav and buy_nav > 0:
                    tx["nav"] = buy_nav
                    tx["shares"] = round(tx["amount"] / buy_nav, 2)
                else:
                    # 无法获取历史净值，用当前净值近似
                    nav_info = get_fund_nav(tx["code"])
                    if nav_info and nav_info["nav"] != "N/A":
                        tx["nav"] = float(nav_info["nav"])
                        tx["shares"] = round(tx["amount"] / float(nav_info["nav"]), 2)
                    else:
                        tx["nav"] = 1.0
                        tx["shares"] = tx["amount"]
    else:
        user_data["portfolio"] = {
            "transactions": [],
            "assets": [],
            "profile": p.get("profile"),
            "history": p.get("history", []),
            "version": 4,
        }

    return user_data

# ---- AKShare 数据拉取 ----
def get_fund_nav(code: str) -> dict:
    """获取单只基金的最新净值"""
    cache_key = code
    now = time.time()

    if cache_key in nav_cache and now - nav_cache[cache_key]["ts"] < NAV_CACHE_TTL:
        return nav_cache[cache_key]["data"]

    try:
        import akshare as ak
        # 开放式基金净值
        df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        if df is not None and len(df) > 0:
            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else df.iloc[-1]
            nav_val = float(latest["单位净值"])
            prev_val = float(prev["单位净值"])
            change = round((nav_val - prev_val) / prev_val * 100, 2)
            result = {
                "code": code,
                "nav": str(nav_val),
                "date": str(latest["净值日期"]),
                "change": str(change),
            }
            nav_cache[cache_key] = {"data": result, "ts": now}
            return result
    except Exception as e:
        print(f"[NAV] Failed to fetch {code}: {e}")

    # 降级：返回空
    return {"code": code, "nav": "N/A", "date": "N/A", "change": "0"}


def get_fear_greed_index() -> dict:
    """增强版恐惧贪婪指数（3维：涨跌幅+波动率+成交量偏离）
    返回 dict 包含综合分数和各维度明细，兼容旧代码（可用 result["score"]）
    """
    result = {"score": 50, "level": "中性", "dimensions": {}}
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol="sh000300")
        if df is not None and len(df) >= 60:
            recent_60 = df.tail(60)
            recent_20 = df.tail(20)

            close_20 = recent_20["close"].values
            close_60 = recent_60["close"].values

            # 维度1: 20日涨跌幅 → 恐惧/贪婪 (权重 40%)
            change_20d = (close_20[-1] - close_20[0]) / close_20[0] * 100
            if change_20d < -15:
                dim1_score = 90
            elif change_20d < -8:
                dim1_score = 75
            elif change_20d < -3:
                dim1_score = 60
            elif change_20d < 3:
                dim1_score = 50
            elif change_20d < 8:
                dim1_score = 35
            elif change_20d < 15:
                dim1_score = 20
            else:
                dim1_score = 10
            result["dimensions"]["momentum"] = {"value": round(change_20d, 2), "score": dim1_score, "label": "20日动量"}

            # 维度2: 波动率（20日收益率标准差）→ 高波动=恐惧 (权重 30%)
            returns = [(close_20[i] - close_20[i - 1]) / close_20[i - 1] * 100 for i in range(1, len(close_20))]
            volatility = (sum((r - sum(returns) / len(returns)) ** 2 for r in returns) / len(returns)) ** 0.5
            # 历史波动率阈值（沪深300日波动率通常 0.5-2.5%）
            if volatility > 2.5:
                dim2_score = 90
            elif volatility > 1.8:
                dim2_score = 70
            elif volatility > 1.2:
                dim2_score = 50
            elif volatility > 0.7:
                dim2_score = 35
            else:
                dim2_score = 20
            result["dimensions"]["volatility"] = {"value": round(volatility, 3), "score": dim2_score, "label": "波动率"}

            # 维度3: 成交量偏离（近5日 vs 近60日均量）(权重 30%)
            if "volume" in df.columns:
                vol_5 = df.tail(5)["volume"].mean()
                vol_60 = recent_60["volume"].mean()
                vol_ratio = vol_5 / vol_60 if vol_60 > 0 else 1
                # 缩量下跌=恐惧，放量上涨=贪婪
                if change_20d < 0:  # 下跌中
                    dim3_score = 70 if vol_ratio > 1.3 else 60 if vol_ratio > 1.0 else 80  # 缩量下跌更恐惧
                else:  # 上涨中
                    dim3_score = 20 if vol_ratio > 1.5 else 35 if vol_ratio > 1.0 else 45
                result["dimensions"]["volume"] = {"value": round(vol_ratio, 2), "score": dim3_score, "label": "量能偏离"}
            else:
                dim3_score = 50
                result["dimensions"]["volume"] = {"value": 1.0, "score": 50, "label": "量能(无数据)"}

            # 加权综合
            composite = dim1_score * 0.4 + dim2_score * 0.3 + dim3_score * 0.3
            result["score"] = round(composite, 1)

            if composite >= 75:
                result["level"] = "极度恐惧"
            elif composite >= 60:
                result["level"] = "恐惧"
            elif composite >= 40:
                result["level"] = "中性"
            elif composite >= 25:
                result["level"] = "贪婪"
            else:
                result["level"] = "极度贪婪"

    except Exception as e:
        print(f"[FGI] Failed: {e}")
    return result


def get_valuation_percentile() -> dict:
    """获取沪深300真实PE估值百分位（近3年）"""
    try:
        import akshare as ak
        # 优先尝试获取真实 PE 数据
        try:
            df = ak.stock_a_pe(symbol="000300.SH")
            if df is not None and len(df) >= 250:
                pe_col = [c for c in df.columns if "pe" in c.lower() or "市盈率" in c]
                if pe_col:
                    pe_data = df[pe_col[0]].dropna().tail(750)
                    if len(pe_data) >= 100:
                        current_pe = float(pe_data.iloc[-1])
                        pe_values = pe_data.values
                        percentile = round(sum(1 for p in pe_values if p <= current_pe) / len(pe_values) * 100, 1)
                        return {
                            "index": "沪深300",
                            "percentile": percentile,
                            "level": "低估" if percentile < 30 else "适中" if percentile < 70 else "高估",
                            "current_pe": round(current_pe, 2),
                            "metric": "PE",
                        }
        except Exception as e:
            print(f"[VAL] PE data failed, falling back to index: {e}")

        # 降级：用指数价格百分位
        df = ak.stock_zh_index_daily(symbol="sh000300")
        if df is not None and len(df) >= 750:
            closes = df.tail(750)["close"].values
            current = closes[-1]
            percentile = round(sum(1 for c in closes if c <= current) / len(closes) * 100, 1)
            return {
                "index": "沪深300",
                "percentile": percentile,
                "level": "低估" if percentile < 30 else "适中" if percentile < 70 else "高估",
                "current_pe": round(float(current), 2),
                "metric": "价格(降级)",
            }
    except Exception as e:
        print(f"[VAL] Failed: {e}")
    return {"index": "沪深300", "percentile": 50, "level": "适中", "current_pe": 0, "metric": "默认"}


# ---- 技术指标 ----

def calc_rsi(prices: list, period: int = 14) -> float:
    """计算 RSI 指标"""
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    recent = deltas[-period:]
    gains = [d for d in recent if d > 0]
    losses = [-d for d in recent if d < 0]
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0.001
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def calc_macd(prices: list) -> dict:
    """计算 MACD 指标（12/26/9）"""
    if len(prices) < 35:
        return {"macd": 0, "signal": 0, "histogram": 0, "trend": "数据不足"}

    def ema(data, period):
        k = 2 / (period + 1)
        result = [data[0]]
        for i in range(1, len(data)):
            result.append(data[i] * k + result[-1] * (1 - k))
        return result

    ema12 = ema(prices, 12)
    ema26 = ema(prices, 26)
    dif = [ema12[i] - ema26[i] for i in range(len(prices))]
    dea = ema(dif, 9)
    macd_val = dif[-1] - dea[-1]

    if dif[-1] > dea[-1] and dif[-2] <= dea[-2]:
        trend = "金叉（买入信号）"
    elif dif[-1] < dea[-1] and dif[-2] >= dea[-2]:
        trend = "死叉（卖出信号）"
    elif dif[-1] > dea[-1]:
        trend = "多头排列"
    else:
        trend = "空头排列"

    return {
        "macd": round(macd_val, 4),
        "dif": round(dif[-1], 4),
        "dea": round(dea[-1], 4),
        "trend": trend,
    }


def calc_bollinger(prices: list, period: int = 20) -> dict:
    """计算布林带"""
    if len(prices) < period:
        return {"upper": 0, "middle": 0, "lower": 0, "position": "数据不足"}
    recent = prices[-period:]
    middle = sum(recent) / period
    std = (sum((p - middle) ** 2 for p in recent) / period) ** 0.5
    upper = middle + 2 * std
    lower = middle - 2 * std
    current = prices[-1]

    if current > upper:
        position = "超买（高于上轨）"
    elif current < lower:
        position = "超卖（低于下轨）"
    elif current > middle:
        position = "中轨上方（偏强）"
    else:
        position = "中轨下方（偏弱）"

    return {
        "upper": round(upper, 2),
        "middle": round(middle, 2),
        "lower": round(lower, 2),
        "current": round(current, 2),
        "position": position,
    }


def get_technical_indicators(symbol: str = "sh000300") -> dict:
    """获取沪深300的技术指标（RSI/MACD/布林带）"""
    cache_key = f"tech_{symbol}"
    now = time.time()
    if cache_key in nav_cache and now - nav_cache[cache_key]["ts"] < NAV_CACHE_TTL:
        return nav_cache[cache_key]["data"]

    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol=symbol)
        if df is not None and len(df) >= 60:
            closes = [float(c) for c in df.tail(120)["close"].values]
            result = {
                "rsi": calc_rsi(closes),
                "macd": calc_macd(closes),
                "bollinger": calc_bollinger(closes),
                "rsi_signal": "超买" if calc_rsi(closes) > 70 else "超卖" if calc_rsi(closes) < 30 else "中性",
            }
            nav_cache[cache_key] = {"data": result, "ts": now}
            return result
    except Exception as e:
        print(f"[TECH] Failed: {e}")
    return {"rsi": 50, "macd": {"macd": 0, "dif": 0, "dea": 0, "trend": "数据不足"}, "bollinger": {"upper": 0, "middle": 0, "lower": 0, "position": "数据不足"}, "rsi_signal": "数据不足"}


# ---- 新闻资讯 ----

def get_fund_news(code: str, limit: int = 3) -> list:
    """获取基金/市场相关新闻"""
    cache_key = f"news_{code}"
    now = time.time()
    if cache_key in news_cache and now - news_cache[cache_key]["ts"] < NEWS_CACHE_TTL:
        return news_cache[cache_key]["data"]

    # 基金代码到关键词映射
    keyword_map = {
        "110020": "沪深300",
        "050025": "标普500",
        "217022": "债券",
        "000216": "黄金",
        "070018": "REITs",
    }
    keyword = keyword_map.get(code, "基金")
    news_list = []

    try:
        import akshare as ak
        # 尝试获取财经新闻
        try:
            df = ak.stock_news_em(symbol=keyword)
            if df is not None and len(df) > 0:
                for _, row in df.head(limit).iterrows():
                    title_col = [c for c in df.columns if "标题" in c or "title" in c.lower()]
                    time_col = [c for c in df.columns if "时间" in c or "date" in c.lower() or "发布" in c]
                    source_col = [c for c in df.columns if "来源" in c or "source" in c.lower() or "文章来源" in c]
                    url_col = [c for c in df.columns if "链接" in c or "url" in c.lower() or "新闻链接" in c]
                    news_list.append({
                        "title": str(row[title_col[0]]) if title_col else str(row.iloc[0]),
                        "time": str(row[time_col[0]]) if time_col else "",
                        "source": str(row[source_col[0]]) if source_col else "东方财富",
                        "url": str(row[url_col[0]]) if url_col else "",
                    })
        except Exception as e:
            print(f"[NEWS] stock_news_em failed for {keyword}: {e}")

        # 黄金专用新闻源
        if code == "000216" and not news_list:
            try:
                df = ak.futures_news_shmet(symbol="黄金")
                if df is not None and len(df) > 0:
                    for _, row in df.head(limit).iterrows():
                        title_col = [c for c in df.columns if "标题" in c or "title" in c.lower()]
                        news_list.append({
                            "title": str(row[title_col[0]]) if title_col else str(row.iloc[0]),
                            "time": "",
                            "source": "上海金属网",
                        })
            except Exception:
                pass

    except Exception as e:
        print(f"[NEWS] Failed: {e}")

    # 如果 AKShare 新闻不可用，返回默认提示
    if not news_list:
        news_list = [{"title": f"{keyword}市场动态获取中...", "time": "", "source": "系统"}]

    news_cache[cache_key] = {"data": news_list, "ts": now}
    return news_list


def get_market_news(limit: int = 10) -> list:
    """获取综合市场新闻"""
    cache_key = "market_news_all"
    now = time.time()
    if cache_key in news_cache and now - news_cache[cache_key]["ts"] < NEWS_CACHE_TTL:
        return news_cache[cache_key]["data"]

    all_news = []
    try:
        import akshare as ak
        # 尝试获取财经要闻
        try:
            df = ak.stock_news_em(symbol="财经")
            if df is not None and len(df) > 0:
                for _, row in df.head(limit).iterrows():
                    title_col = [c for c in df.columns if "标题" in c or "title" in c.lower()]
                    time_col = [c for c in df.columns if "时间" in c or "date" in c.lower() or "发布" in c]
                    source_col = [c for c in df.columns if "来源" in c or "source" in c.lower()]
                    url_col = [c for c in df.columns if "链接" in c or "url" in c.lower() or "新闻链接" in c]
                    all_news.append({
                        "title": str(row[title_col[0]]) if title_col else str(row.iloc[0]),
                        "time": str(row[time_col[0]]) if time_col else "",
                        "source": str(row[source_col[0]]) if source_col else "东方财富",
                        "url": str(row[url_col[0]]) if url_col else "",
                    })
        except Exception as e:
            print(f"[NEWS] market news failed: {e}")
    except Exception as e:
        print(f"[NEWS] import failed: {e}")

    if not all_news:
        all_news = [{"title": "市场资讯加载中...", "time": "", "source": "系统"}]

    news_cache[cache_key] = {"data": all_news, "ts": now}
    return all_news


# ---- 宏观经济日历 ----

def get_macro_calendar() -> list:
    """获取近期宏观经济事件"""
    cache_key = "macro_cal"
    now = time.time()
    if cache_key in macro_cache and now - macro_cache[cache_key]["ts"] < MACRO_CACHE_TTL:
        return macro_cache[cache_key]["data"]

    events = []
    try:
        import akshare as ak
        import math

        def _find_col(cols, keywords):
            """模糊匹配列名"""
            for c in cols:
                cl = str(c).lower()
                if any(k in cl for k in keywords):
                    return c
            return None

        def _get_latest_valid(df, val_col, date_col, max_back=5):
            """从最后往前找第一条 val_col 非 NaN 的行"""
            for i in range(1, min(max_back + 1, len(df) + 1)):
                row = df.iloc[-i]
                v = row[val_col]
                try:
                    if v is not None and not (isinstance(v, float) and math.isnan(v)):
                        d = str(row[date_col]) if date_col else ""
                        return str(v), d
                except (TypeError, ValueError):
                    return str(v), str(row[date_col]) if date_col else ""
            return "", ""

        # 尝试获取中国宏观数据（CPI，改用国家统计局月度接口）
        try:
            df = ak.macro_china_cpi()
            if df is not None and len(df) > 0:
                print(f"[MACRO] CPI columns: {list(df.columns)}")
                print(f"[MACRO] CPI first 3 rows: {df.head(3).to_dict('records')}")
                # macro_china_cpi() 列: ['月份', '全国-当月', '全国-同比增长', ...]
                # 数据按时间倒序，第一行是最新
                val_col = _find_col(df.columns, ["全国-同比增长", "全国-同比", "同比增长"]) or (df.columns[2] if len(df.columns) > 2 else None)
                date_col = _find_col(df.columns, ["月份", "日期"]) or (df.columns[0] if len(df.columns) > 0 else None)
                if val_col:
                    cpi_value = ""
                    cpi_date = ""
                    for i in range(min(5, len(df))):
                        row = df.iloc[i]
                        v = row[val_col]
                        try:
                            if v is not None and not (isinstance(v, float) and __import__('math').isnan(v)):
                                cpi_value = str(v)
                                cpi_date = str(row[date_col]) if date_col else ""
                                break
                        except (TypeError, ValueError):
                            cpi_value = str(v)
                            cpi_date = str(row[date_col]) if date_col else ""
                            break
                    if cpi_value:
                        try:
                            float(cpi_value)
                            cpi_value = cpi_value + "%"
                        except (ValueError, TypeError):
                            pass
                        events.append({
                            "name": "CPI 居民消费价格指数",
                            "date": cpi_date,
                            "value": cpi_value,
                            "impact": "通胀指标，影响央行货币政策",
                            "icon": "📊",
                        })
                    else:
                        print("[MACRO] CPI: all recent values are NaN")
                else:
                    print(f"[MACRO] CPI: cannot find value column in {list(df.columns)}")
        except Exception as e:
            print(f"[MACRO] CPI failed: {e}")
            import traceback; traceback.print_exc()

        # PMI（数据倒序排列，最新在头部）
        try:
            df = ak.macro_china_pmi()
            if df is not None and len(df) > 0:
                print(f"[MACRO] PMI columns: {list(df.columns)}")
                print(f"[MACRO] PMI first 3 rows: {df.head(3).to_dict('records')}")
                # macro_china_pmi() 列: ['月份', '制造业-指数', '制造业-同比增长', '非制造业-指数', '非制造业-同比增长']
                val_col = _find_col(df.columns, ["制造业-指数", "制造业", "pmi"]) or (df.columns[1] if len(df.columns) > 1 else None)
                date_col = _find_col(df.columns, ["月份", "日期"]) or (df.columns[0] if len(df.columns) > 0 else None)
                if val_col:
                    pmi_value = ""
                    pmi_date = ""
                    for i in range(min(5, len(df))):
                        row = df.iloc[i]
                        v = row[val_col]
                        try:
                            if v is not None and not (isinstance(v, float) and __import__('math').isnan(v)):
                                pmi_value = str(v)
                                pmi_date = str(row[date_col]) if date_col else ""
                                break
                        except (TypeError, ValueError):
                            pmi_value = str(v)
                            pmi_date = str(row[date_col]) if date_col else ""
                            break
                    if pmi_value:
                        events.append({
                            "name": "PMI 采购经理指数",
                            "date": pmi_date,
                            "value": pmi_value,
                            "impact": "经济景气度指标，>50扩张、<50收缩",
                            "icon": "🏭",
                        })
        except Exception as e:
            print(f"[MACRO] PMI failed: {e}")

        # M2 货币供应（改用国家统计局月度接口）
        try:
            df = ak.macro_china_money_supply()
            if df is not None and len(df) > 0:
                print(f"[MACRO] M2 columns: {list(df.columns)}")
                print(f"[MACRO] M2 first 3 rows: {df.head(3).to_dict('records')}")
                # macro_china_money_supply() 列: ['月份', '货币和准货币(M2)-数量(亿元)', '货币和准货币(M2)-同比增长', ...]
                # 数据按时间倒序，第一行是最新
                val_col = _find_col(df.columns, ["货币和准货币(M2)-同比增长", "M2-同比", "M2同比"]) or (df.columns[2] if len(df.columns) > 2 else None)
                date_col = _find_col(df.columns, ["月份", "日期"]) or (df.columns[0] if len(df.columns) > 0 else None)
                if val_col:
                    m2_val = ""
                    m2_date = ""
                    for i in range(min(5, len(df))):
                        row = df.iloc[i]
                        v = row[val_col]
                        try:
                            if v is not None and not (isinstance(v, float) and __import__('math').isnan(v)):
                                m2_val = str(v)
                                m2_date = str(row[date_col]) if date_col else ""
                                break
                        except (TypeError, ValueError):
                            m2_val = str(v)
                            m2_date = str(row[date_col]) if date_col else ""
                            break
                    if m2_val:
                        try:
                            float(m2_val)
                            m2_val = m2_val + "%"
                        except (ValueError, TypeError):
                            pass
                        events.append({
                            "name": "M2 广义货币供应量",
                            "date": m2_date,
                            "value": m2_val,
                            "impact": "货币宽松/紧缩信号，影响市场流动性",
                            "icon": "💵",
                        })
                    else:
                        print("[MACRO] M2: all recent values are NaN")
                else:
                    print(f"[MACRO] M2: cannot find value column in {list(df.columns)}")
        except Exception as e:
            print(f"[MACRO] M2 failed: {e}")

        # PPI 工业生产者出厂价格指数（改用月度接口，数据更及时）
        try:
            df = ak.macro_china_ppi()
            if df is not None and len(df) > 0:
                print(f"[MACRO] PPI columns: {list(df.columns)}")
                print(f"[MACRO] PPI first 3 rows: {df.head(3).to_dict('records')}")
                # macro_china_ppi() 列: ['月份', '当月', '当月同比增长', '累计']
                # 数据按时间倒序，第一行就是最新的
                val_col = _find_col(df.columns, ["当月同比增长", "当月同比", "同比"]) or (df.columns[2] if len(df.columns) > 2 else None)
                date_col = _find_col(df.columns, ["月份", "日期", "date"]) or (df.columns[0] if len(df.columns) > 0 else None)
                if val_col:
                    # 数据倒序排列，从头部找第一条有效值
                    ppi_val = ""
                    ppi_date = ""
                    for i in range(min(5, len(df))):
                        row = df.iloc[i]
                        v = row[val_col]
                        try:
                            if v is not None and not (isinstance(v, float) and __import__('math').isnan(v)):
                                ppi_val = str(v)
                                ppi_date = str(row[date_col]) if date_col else ""
                                break
                        except (TypeError, ValueError):
                            ppi_val = str(v)
                            ppi_date = str(row[date_col]) if date_col else ""
                            break
                    if ppi_val:
                        try:
                            float(ppi_val)
                            ppi_val = ppi_val + "%"
                        except (ValueError, TypeError):
                            pass
                        events.append({
                            "name": "PPI 工业生产者出厂价格指数",
                            "date": ppi_date,
                            "value": ppi_val,
                            "impact": "上游价格指标，领先CPI反映通胀趋势",
                            "icon": "🏭",
                        })
                    else:
                        print("[MACRO] PPI: all recent values are NaN")
                else:
                    print(f"[MACRO] PPI: cannot find value column in {list(df.columns)}")
        except Exception as e:
            print(f"[MACRO] PPI failed: {e}")

    except Exception as e:
        print(f"[MACRO] Failed: {e}")

    if not events:
        events = [{"name": "宏观数据加载中", "date": "", "value": "", "impact": "", "icon": "📅"}]

    macro_cache[cache_key] = {"data": events, "ts": now}
    return events


def calc_smart_dca(base_amount: float, valuation_pct: float) -> dict:
    """智能定投：根据估值百分位动态调整定投金额
    低估多买，高估少买，极度高估暂停
    """
    if valuation_pct < 20:
        multiplier = 1.5
        advice = "极度低估，建议定投 1.5 倍"
    elif valuation_pct < 30:
        multiplier = 1.3
        advice = "低估区间，建议定投 1.3 倍"
    elif valuation_pct < 50:
        multiplier = 1.1
        advice = "偏低估，建议定投 1.1 倍"
    elif valuation_pct < 70:
        multiplier = 1.0
        advice = "估值适中，正常定投"
    elif valuation_pct < 85:
        multiplier = 0.7
        advice = "偏高估，建议定投 0.7 倍"
    else:
        multiplier = 0.3
        advice = "极度高估，建议大幅减少或暂停定投"

    return {
        "baseAmount": round(base_amount, 2),
        "multiplier": multiplier,
        "smartAmount": round(base_amount * multiplier, 2),
        "advice": advice,
        "valuationPct": valuation_pct,
    }


def calc_take_profit_strategy(cost: float, market_value: float, profile: str) -> dict:
    """止盈止损策略：根据风险类型给目标收益率和止损线"""
    # 不同风险类型的止盈止损参数
    params = {
        "保守型": {"target_pct": 15, "stop_loss_pct": -8, "partial_pct": 10},
        "稳健型": {"target_pct": 20, "stop_loss_pct": -10, "partial_pct": 15},
        "平衡型": {"target_pct": 30, "stop_loss_pct": -15, "partial_pct": 20},
        "进取型": {"target_pct": 50, "stop_loss_pct": -20, "partial_pct": 30},
        "激进型": {"target_pct": 80, "stop_loss_pct": -25, "partial_pct": 40},
    }
    p = params.get(profile, params["平衡型"])

    current_pnl_pct = ((market_value - cost) / cost * 100) if cost > 0 else 0
    target_value = cost * (1 + p["target_pct"] / 100)
    stop_loss_value = cost * (1 + p["stop_loss_pct"] / 100)

    # 判断当前状态
    if current_pnl_pct >= p["target_pct"]:
        status = "reached_target"
        action = f"🎯 已达止盈目标！建议卖出 {p['partial_pct']}% 锁定利润，剩余继续持有。"
    elif current_pnl_pct >= p["partial_pct"]:
        status = "partial_profit"
        action = f"📈 收益不错（+{current_pnl_pct:.1f}%），可考虑止盈一小部分（20-30%），剩余继续持有。"
    elif current_pnl_pct <= p["stop_loss_pct"]:
        status = "stop_loss"
        action = f"⚠️ 亏损已达 {current_pnl_pct:.1f}%，接近止损线。检查基金基本面是否变化，若无问题可继续持有甚至加仓。"
    elif current_pnl_pct < 0:
        status = "in_loss"
        action = f"📉 当前浮亏 {current_pnl_pct:.1f}%，离止损线还有空间。保持耐心，继续定投摊低成本。"
    else:
        status = "holding"
        action = f"✅ 当前盈利 +{current_pnl_pct:.1f}%，距止盈目标还有 {p['target_pct'] - current_pnl_pct:.1f}%，继续持有。"

    return {
        "currentPnlPct": round(current_pnl_pct, 2),
        "targetPct": p["target_pct"],
        "stopLossPct": p["stop_loss_pct"],
        "targetValue": round(target_value, 2),
        "stopLossValue": round(stop_loss_value, 2),
        "status": status,
        "action": action,
        "profile": profile,
    }


# ============================================================
# 每日智能信号引擎（综合 RSI + MACD + 估值 + 恐贪 + 大师策略）
# ============================================================

def generate_daily_signal() -> dict:
    """生成每日综合交易信号 — 融合技术面 + 基本面 + 大师策略"""
    signal = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "overall": "HOLD",  # BUY / SELL / HOLD
        "confidence": 0,
        "summary": "",
        "details": [],
        "masterStrategies": [],
        "smartDca": None,
    }

    scores = []  # (score, weight, name, detail)  score: -100(强烈卖出) ~ +100(强烈买入)

    # --- 1. RSI 信号 ---
    tech = get_technical_indicators()
    rsi = tech.get("rsi", 50)
    if rsi < 25:
        rsi_score, rsi_detail = 80, f"RSI={rsi}，极度超卖，强烈买入信号"
    elif rsi < 30:
        rsi_score, rsi_detail = 60, f"RSI={rsi}，超卖区，偏向买入"
    elif rsi < 45:
        rsi_score, rsi_detail = 20, f"RSI={rsi}，偏低，轻度看多"
    elif rsi <= 55:
        rsi_score, rsi_detail = 0, f"RSI={rsi}，中性区间"
    elif rsi <= 70:
        rsi_score, rsi_detail = -20, f"RSI={rsi}，偏高，注意风险"
    elif rsi <= 80:
        rsi_score, rsi_detail = -60, f"RSI={rsi}，超买区，偏向卖出"
    else:
        rsi_score, rsi_detail = -80, f"RSI={rsi}，极度超买，强烈卖出信号"
    scores.append((rsi_score, 0.15, "RSI", rsi_detail))

    # --- 2. MACD 信号 ---
    macd = tech.get("macd", {})
    trend = macd.get("trend", "")
    if "金叉" in trend:
        macd_score, macd_detail = 70, f"MACD金叉（{trend}），趋势转多"
    elif "多头" in trend:
        macd_score, macd_detail = 30, f"MACD多头排列，上升趋势持续"
    elif "死叉" in trend:
        macd_score, macd_detail = -70, f"MACD死叉（{trend}），趋势转空"
    elif "空头" in trend:
        macd_score, macd_detail = -30, f"MACD空头排列，下降趋势持续"
    else:
        macd_score, macd_detail = 0, "MACD数据不足"
    scores.append((macd_score, 0.15, "MACD", macd_detail))

    # --- 3. 布林带信号 ---
    boll = tech.get("bollinger", {})
    pos = boll.get("position", "")
    if "超卖" in pos:
        boll_score, boll_detail = 60, f"价格低于布林下轨，超卖反弹机会"
    elif "下方" in pos:
        boll_score, boll_detail = 15, "价格在中轨下方，偏弱但未到极端"
    elif "上方" in pos:
        boll_score, boll_detail = -15, "价格在中轨上方，偏强但注意回调"
    elif "超买" in pos:
        boll_score, boll_detail = -60, "价格高于布林上轨，超买回调风险"
    else:
        boll_score, boll_detail = 0, "布林带数据不足"
    scores.append((boll_score, 0.10, "布林带", boll_detail))

    # --- 4. 估值百分位信号 (最重要) ---
    val = get_valuation_percentile()
    vp = val.get("percentile", 50)
    if vp < 15:
        val_score, val_detail = 90, f"估值百分位{vp}%，极度低估（历史最佳买入区）"
    elif vp < 30:
        val_score, val_detail = 60, f"估值百分位{vp}%，低估区间（适合加仓）"
    elif vp < 50:
        val_score, val_detail = 20, f"估值百分位{vp}%，偏低估（正常定投）"
    elif vp < 70:
        val_score, val_detail = -10, f"估值百分位{vp}%，适中偏高（谨慎加仓）"
    elif vp < 85:
        val_score, val_detail = -50, f"估值百分位{vp}%，偏高估（减少定投）"
    else:
        val_score, val_detail = -80, f"估值百分位{vp}%，极度高估（建议暂停或减仓）"
    scores.append((val_score, 0.25, "估值", val_detail))

    # --- 5. 恐惧贪婪指数 ---
    fgi_data = get_fear_greed_index()
    fgi = fgi_data.get("score", 50)
    if fgi >= 80:
        fgi_score, fgi_detail = 80, f"恐惧指数{fgi:.0f}（极度恐惧），别人恐惧时贪婪"
    elif fgi >= 65:
        fgi_score, fgi_detail = 40, f"恐惧指数{fgi:.0f}（恐惧），市场偏悲观"
    elif fgi >= 40:
        fgi_score, fgi_detail = 0, f"恐惧指数{fgi:.0f}（中性）"
    elif fgi >= 25:
        fgi_score, fgi_detail = -40, f"恐惧指数{fgi:.0f}（贪婪），市场偏乐观"
    else:
        fgi_score, fgi_detail = -80, f"恐惧指数{fgi:.0f}（极度贪婪），别人贪婪时恐惧"
    scores.append((fgi_score, 0.20, "恐贪指数", fgi_detail))

    # --- 6. 宏观经济信号 ---
    macro = get_macro_calendar()
    macro_score = 0
    macro_parts = []
    for e in macro:
        v = e.get("value", "")
        name = e.get("name", "")
        try:
            num = float(str(v).replace("%", ""))
            if "PMI" in name:
                if num > 50:
                    macro_score += 15
                    macro_parts.append(f"PMI={num}(扩张)")
                else:
                    macro_score -= 15
                    macro_parts.append(f"PMI={num}(收缩)")
            elif "M2" in name:
                if num > 8:
                    macro_score += 10
                    macro_parts.append(f"M2增速{num}%(宽松)")
                elif num < 6:
                    macro_score -= 10
                    macro_parts.append(f"M2增速{num}%(偏紧)")
        except (ValueError, TypeError):
            pass
    macro_detail = "宏观环境：" + ("、".join(macro_parts) if macro_parts else "暂无可量化数据")
    scores.append((max(-50, min(50, macro_score)), 0.15, "宏观经济", macro_detail))

    # --- 加权综合 ---
    total_score = sum(s * w for s, w, _, _ in scores)
    total_weight = sum(w for _, w, _, _ in scores)
    final_score = total_score / total_weight if total_weight > 0 else 0

    # --- 信号判定 ---
    if final_score >= 40:
        signal["overall"] = "STRONG_BUY"
        signal["summary"] = "🟢 强烈买入信号 — 多个指标共振看多，是较好的加仓时机"
    elif final_score >= 20:
        signal["overall"] = "BUY"
        signal["summary"] = "🟢 买入信号 — 整体偏向看多，适合按计划定投或小额加仓"
    elif final_score >= -20:
        signal["overall"] = "HOLD"
        signal["summary"] = "🟡 持有观望 — 信号中性，维持当前仓位，不急着操作"
    elif final_score >= -40:
        signal["overall"] = "SELL"
        signal["summary"] = "🟠 减仓信号 — 整体偏空，建议减少定投金额或部分止盈"
    else:
        signal["overall"] = "STRONG_SELL"
        signal["summary"] = "🔴 强烈减仓 — 多个指标共振看空，建议止盈或暂停买入"

    signal["confidence"] = min(abs(final_score), 100)
    signal["score"] = round(final_score, 1)
    signal["details"] = [
        {"name": name, "score": round(s, 1), "weight": f"{w*100:.0f}%", "detail": detail}
        for s, w, name, detail in scores
    ]

    # --- 大师策略 ---
    signal["masterStrategies"] = _apply_master_strategies(val, fgi_data, tech)

    # --- 智能定投建议 ---
    signal["smartDca"] = calc_smart_dca(1000, vp)

    return signal


def _apply_master_strategies(val: dict, fgi_data: dict, tech: dict) -> list:
    """应用投资大师策略"""
    strategies = []
    vp = val.get("percentile", 50)
    pe = val.get("current_pe", 0)
    fgi = fgi_data.get("score", 50)
    rsi = tech.get("rsi", 50)

    # 巴菲特价值投资
    buffett_signal = "HOLD"
    if vp < 30 and fgi >= 60:
        buffett_signal = "BUY"
        buffett_msg = f"✅ 符合巴菲特买入条件！估值低({vp}%) + 市场恐惧({fgi:.0f})。\"别人恐惧时我贪婪\"。"
    elif vp > 70 and fgi < 35:
        buffett_signal = "SELL"
        buffett_msg = f"⚠️ 巴菲特会谨慎！估值高({vp}%) + 市场贪婪({fgi:.0f})。\"别人贪婪时我恐惧\"。"
    elif vp < 40:
        buffett_signal = "HOLD_BUY"
        buffett_msg = f"估值尚可({vp}%)，巴菲特会耐心等待更好价格，但已可以开始建仓。"
    else:
        buffett_msg = f"估值{vp}%，巴菲特会说\"价格合理但不便宜\"，不急着买也不急着卖。"
    strategies.append({
        "master": "巴菲特",
        "philosophy": "价值投资：低估时买入优质资产，长期持有",
        "signal": buffett_signal,
        "message": buffett_msg,
        "icon": "🧓",
    })

    # 格雷厄姆安全边际
    graham_signal = "HOLD"
    if vp < 25:
        graham_signal = "BUY"
        graham_msg = f"✅ 安全边际充足！估值百分位{vp}%，远低于内在价值。格雷厄姆建议果断买入。"
    elif vp < 40:
        graham_signal = "HOLD_BUY"
        graham_msg = f"安全边际尚可({vp}%)。格雷厄姆会建议分批买入，不要一次性重仓。"
    elif vp > 75:
        graham_signal = "SELL"
        graham_msg = f"⚠️ 安全边际不足！估值百分位{vp}%，格雷厄姆会建议减仓或换入防御性资产。"
    else:
        graham_msg = f"估值{vp}%在中间区域。格雷厄姆会说\"保持耐心，等待安全边际出现\"。"
    strategies.append({
        "master": "格雷厄姆",
        "philosophy": "安全边际：只在价格远低于内在价值时买入",
        "signal": graham_signal,
        "message": graham_msg,
        "icon": "📚",
    })

    # 彼得·林奇成长投资
    lynch_signal = "HOLD"
    macro = get_macro_calendar()
    pmi_val = None
    for e in macro:
        if "PMI" in e.get("name", ""):
            try:
                pmi_val = float(str(e.get("value", "")).replace("%", ""))
            except (ValueError, TypeError):
                pass
    if pmi_val and pmi_val > 50 and vp < 50:
        lynch_signal = "BUY"
        lynch_msg = f"✅ 经济扩张(PMI={pmi_val}) + 估值合理({vp}%)。林奇会说\"跟着经济增长投资\"。"
    elif pmi_val and pmi_val < 50 and vp > 60:
        lynch_signal = "SELL"
        lynch_msg = f"⚠️ 经济收缩(PMI={pmi_val}) + 估值偏高({vp}%)。林奇会建议转向防御性持仓。"
    else:
        lynch_msg = f"林奇重视\"用日常观察选股\"。宏观面{'扩张' if (pmi_val and pmi_val > 50) else '收缩' if pmi_val else '未知'}，估值{vp}%，建议关注消费领域基金。"
    strategies.append({
        "master": "彼得·林奇",
        "philosophy": "成长投资：寻找被低估的成长型企业",
        "signal": lynch_signal,
        "message": lynch_msg,
        "icon": "🔍",
    })

    # 约翰·博格 (Vanguard 指数基金之父)
    bogle_msg = "📌 博格指数投资策略永远是：坚持定投，不要择时，降低费用，长期持有。"
    if vp < 30:
        bogle_msg += f"\n当前估值{vp}%偏低，定投的筹码在未来会更有价值。"
    elif vp > 70:
        bogle_msg += f"\n当前估值{vp}%偏高，但博格会说\"不要试图择时，继续你的定投计划\"。"
    strategies.append({
        "master": "约翰·博格",
        "philosophy": "指数投资：低成本指数基金 + 长期持有 + 定期定投",
        "signal": "HOLD",
        "message": bogle_msg,
        "icon": "📊",
    })

    return strategies


# ============================================================
# 策略回测系统
# ============================================================

def run_backtest(strategy: str = "smart_dca", years: int = 3, monthly_amount: float = 1000) -> dict:
    """简易策略回测 — 用沪深300历史数据"""
    result = {
        "strategy": strategy,
        "years": years,
        "monthlyAmount": monthly_amount,
        "totalInvested": 0,
        "finalValue": 0,
        "totalReturn": 0,
        "totalReturnPct": 0,
        "annualizedReturn": 0,
        "maxDrawdown": 0,
        "months": [],
        "comparison": {},
    }

    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol="sh000300")
        if df is None or len(df) < years * 250:
            return {**result, "error": "历史数据不足"}

        # 取最近 N 年数据，按月采样
        total_days = years * 250
        daily = df.tail(total_days)
        closes = daily["close"].values
        dates = daily.index if hasattr(daily.index, '__len__') else list(range(len(daily)))

        # 每月取一个数据点（约每 20 个交易日）
        monthly_prices = []
        monthly_dates = []
        for i in range(0, len(closes), 20):
            monthly_prices.append(float(closes[i]))
            if hasattr(dates[i], 'strftime'):
                monthly_dates.append(dates[i].strftime("%Y-%m"))
            else:
                monthly_dates.append(f"M{i//20+1}")

        if len(monthly_prices) < 12:
            return {**result, "error": "月度数据不足"}

        # 计算近3年估值百分位序列（用价格百分位近似）
        all_closes = [float(c) for c in df.tail(total_days + 250)["close"].values]

        # --- 固定定投策略 ---
        fix_shares = 0
        fix_invested = 0
        fix_curve = []
        for i, price in enumerate(monthly_prices):
            shares_bought = monthly_amount / price
            fix_shares += shares_bought
            fix_invested += monthly_amount
            fix_curve.append({
                "month": monthly_dates[i] if i < len(monthly_dates) else f"M{i+1}",
                "invested": round(fix_invested, 2),
                "value": round(fix_shares * price, 2),
            })

        fix_final = fix_shares * monthly_prices[-1]
        fix_return = fix_final - fix_invested
        fix_return_pct = (fix_return / fix_invested * 100) if fix_invested > 0 else 0

        # --- 智能定投策略 ---
        smart_shares = 0
        smart_invested = 0
        smart_curve = []
        for i, price in enumerate(monthly_prices):
            # 计算当月的价格百分位（用前 N 个月的价格范围）
            lookback = all_closes[:len(all_closes) - len(monthly_prices) + i + 1]
            if len(lookback) > 60:
                lookback_recent = lookback[-750:]  # 近3年
                pct = sum(1 for p in lookback_recent if p <= price) / len(lookback_recent) * 100
            else:
                pct = 50

            # 根据估值调整定投金额
            dca = calc_smart_dca(monthly_amount, pct)
            actual_amount = dca["smartAmount"]

            shares_bought = actual_amount / price
            smart_shares += shares_bought
            smart_invested += actual_amount
            smart_curve.append({
                "month": monthly_dates[i] if i < len(monthly_dates) else f"M{i+1}",
                "invested": round(smart_invested, 2),
                "value": round(smart_shares * price, 2),
                "multiplier": dca["multiplier"],
            })

        smart_final = smart_shares * monthly_prices[-1]
        smart_return = smart_final - smart_invested
        smart_return_pct = (smart_return / smart_invested * 100) if smart_invested > 0 else 0

        # --- 计算最大回撤 ---
        def calc_max_drawdown(curve):
            peak = 0
            max_dd = 0
            for pt in curve:
                v = pt["value"]
                if v > peak:
                    peak = v
                dd = (peak - v) / peak * 100 if peak > 0 else 0
                if dd > max_dd:
                    max_dd = dd
            return round(max_dd, 2)

        # --- 年化收益率 ---
        n_years = len(monthly_prices) / 12
        fix_annual = ((1 + fix_return_pct / 100) ** (1 / n_years) - 1) * 100 if n_years > 0 and fix_return_pct > -100 else 0
        smart_annual = ((1 + smart_return_pct / 100) ** (1 / n_years) - 1) * 100 if n_years > 0 and smart_return_pct > -100 else 0

        result.update({
            "totalInvested": round(smart_invested, 2),
            "finalValue": round(smart_final, 2),
            "totalReturn": round(smart_return, 2),
            "totalReturnPct": round(smart_return_pct, 2),
            "annualizedReturn": round(smart_annual, 2),
            "maxDrawdown": calc_max_drawdown(smart_curve),
            "months": smart_curve,
            "comparison": {
                "fixedDca": {
                    "invested": round(fix_invested, 2),
                    "finalValue": round(fix_final, 2),
                    "totalReturn": round(fix_return, 2),
                    "totalReturnPct": round(fix_return_pct, 2),
                    "annualizedReturn": round(fix_annual, 2),
                    "maxDrawdown": calc_max_drawdown(fix_curve),
                    "months": fix_curve,
                },
                "smartDca": {
                    "invested": round(smart_invested, 2),
                    "finalValue": round(smart_final, 2),
                    "totalReturn": round(smart_return, 2),
                    "totalReturnPct": round(smart_return_pct, 2),
                    "annualizedReturn": round(smart_annual, 2),
                    "maxDrawdown": calc_max_drawdown(smart_curve),
                    "months": smart_curve,
                },
                "advantage": round(smart_return_pct - fix_return_pct, 2),
            },
        })

    except Exception as e:
        print(f"[BACKTEST] Failed: {e}")
        import traceback; traceback.print_exc()
        result["error"] = str(e)

    return result


# ---- API 路由 ----

@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now().isoformat()}


@app.get("/api/nav/all")
def get_all_nav():
    """获取所有推荐基金的净值"""
    codes = ["110020", "050025", "217022", "000216", "070018"]
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
    codes = ["110020", "050025", "217022", "000216", "070018"]
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
def get_market_dashboard():
    """综合市场仪表盘 — 前端资讯页一次性拉取"""
    val = get_valuation_percentile()
    fgi_data = get_fear_greed_index()
    tech = get_technical_indicators()
    news = get_market_news(8)
    macro = get_macro_calendar()

    return {
        "valuation": val,
        "fearGreed": fgi_data,
        "technical": tech,
        "news": news,
        "macro": macro,
        "updatedAt": datetime.now().isoformat(),
    }


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


# ---- 持久化工具 ----
def _user_file(user_id: str) -> Path:
    safe_id = hashlib.sha256(user_id.encode()).hexdigest()[:16]
    return USERS_DIR / f"{safe_id}.json"

def load_user(user_id: str) -> dict:
    f = _user_file(user_id)
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {"userId": user_id, "portfolio": None, "ledger": [], "createdAt": datetime.now().isoformat(), "updatedAt": datetime.now().isoformat()}

def save_user(data: dict):
    data["updatedAt"] = datetime.now().isoformat()
    f = _user_file(data["userId"])
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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
        "070018": "嘉实多利优选混合A",
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

    # 计算各类资产
    assets = p.get("assets", [])
    cash_total = sum(a.get("balance", 0) for a in assets if a.get("type") == "cash")
    property_total = sum(a.get("value", 0) for a in assets if a.get("type") == "property")
    other_total = sum(a.get("value", 0) for a in assets if a.get("type") == "other")
    liability_total = sum(abs(a.get("balance", 0)) for a in assets if a.get("type") == "liability")

    # 计算记账收支净额（收入 - 支出）
    ledger = user.get("ledger", [])
    ledger_income = sum(e.get("amount", 0) for e in ledger if e.get("direction") == "income")
    ledger_expense = sum(e.get("amount", 0) for e in ledger if e.get("direction", "expense") == "expense")
    ledger_net = ledger_income - ledger_expense

    net_worth = investment_value + cash_total + property_total + other_total + ledger_net - liability_total

    return {
        "netWorth": round(net_worth, 2),
        "breakdown": {
            "investment": round(investment_value, 2),
            "cash": round(cash_total, 2),
            "property": round(property_total, 2),
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


def _get_nav_on_date(code: str, date_str: str) -> Optional[float]:
    """获取基金在指定日期的净值"""
    cache_key = f"hist_{code}"
    now = time.time()

    # 使用缓存的历史数据
    if cache_key in nav_cache and now - nav_cache[cache_key]["ts"] < NAV_CACHE_TTL:
        df = nav_cache[cache_key]["data"]
    else:
        try:
            import akshare as ak
            df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
            if df is not None and len(df) > 0:
                nav_cache[cache_key] = {"data": df, "ts": now}
            else:
                return None
        except Exception as e:
            print(f"[HIST_NAV] Failed for {code}: {e}")
            return None

    try:
        target = datetime.fromisoformat(date_str.replace("Z", "+00:00")).strftime("%Y-%m-%d")
        # 找最接近买入日期的净值
        df["date_str"] = df["净值日期"].astype(str)
        match = df[df["date_str"] >= target].head(1)
        if len(match) > 0:
            return float(match.iloc[0]["单位净值"])
        # 如果买入日期比所有数据都新，取最新
        return float(df.iloc[-1]["单位净值"])
    except Exception as e:
        print(f"[HIST_NAV] Parse error: {e}")
        return None


# ---- API: AI 对话分析 ----

@app.post("/api/chat")
async def chat_analysis(req: ChatRequest):
    """AI 对话分析 — 回答用户的理财问题"""
    user_msg = req.message.strip()
    if not user_msg:
        raise HTTPException(400, "消息不能为空")

    # 构建市场上下文
    market_ctx = _build_market_context()
    portfolio_ctx = _build_portfolio_context(req.portfolio) if req.portfolio else "用户尚未建仓。"

    # 尝试调用 LLM（支持 OpenAI 兼容 API）
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
    api_base = os.environ.get("LLM_API_BASE", "https://api.openai.com/v1")
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")

    if api_key:
        try:
            import httpx
            system_prompt = f"""你是「钱袋子」的 AI 理财分析师。你的职责：
1. 用通俗易懂的中文回答用户的理财问题
2. 基于真实市场数据给出分析（不编造数据）
3. 永远提醒用户"投资有风险"
4. 不推荐具体买卖时点，只分析趋势和逻辑
5. 回答控制在 200 字以内，简洁有力

当前市场数据：
{market_ctx}

用户持仓：
{portfolio_ctx}"""

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
                        "max_tokens": 500,
                        "temperature": 0.7,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    reply = data["choices"][0]["message"]["content"]
                    return {"reply": reply, "source": "ai"}
        except Exception as e:
            print(f"[CHAT] LLM call failed: {e}")

    # 降级：规则引擎回答
    reply = _rule_based_reply(user_msg, market_ctx, portfolio_ctx)
    return {"reply": reply, "source": "rules"}


def _build_market_context() -> str:
    """构建市场数据上下文（含恐惧贪婪、技术指标、新闻）"""
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
    return "\n".join(lines) if lines else "暂无市场数据"


def _build_portfolio_context(p: Portfolio) -> str:
    if not p or not p.holdings:
        return "用户尚未建仓。"
    lines = [f"风险类型：{p.profile}，总投入：¥{p.amount:,.0f}"]
    for h in p.holdings:
        lines.append(f"  - {h.name}({h.code})：¥{h.amount:,.0f}，目标占比 {h.targetPct}%")
    return "\n".join(lines)


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
                        "max_tokens": 500,
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

class IncomeSourceCreate(BaseModel):
    userId: str
    name: str
    type: str = "其他"
    expectedAmt: float = 0
    note: str = ""

class IncomeSourceRecord(BaseModel):
    userId: str
    sourceId: str
    amount: float

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


# ---- 静态文件服务（部署时前后端一体）----
FRONTEND_DIR = Path(__file__).resolve().parent.parent  # moneybag/

@app.get("/")
def serve_index():
    return FileResponse(FRONTEND_DIR / "index.html")

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="frontend")

# 兜底：让 /app.js 等直接路径也能访问
@app.get("/{filename:path}")
def serve_frontend_file(filename: str):
    fp = FRONTEND_DIR / filename
    if fp.is_file():
        return FileResponse(fp)
    return FileResponse(FRONTEND_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

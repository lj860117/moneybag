"""
钱袋子 — FastAPI 后端
提供实时基金净值、买卖信号、再平衡提醒
"""
import os
import json
import time
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="钱袋子 API", version="1.0.0")

# CORS - 允许前端跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- 缓存 ----
nav_cache = {}
NAV_CACHE_TTL = 3600  # 1小时缓存

# ---- 数据模型 ----
class Holding(BaseModel):
    code: str
    name: str
    category: str
    targetPct: float
    amount: float
    buyDate: str

class Portfolio(BaseModel):
    holdings: list[Holding] = []
    history: list = []
    profile: Optional[str] = None
    amount: float = 0

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


def get_fear_greed_index() -> float:
    """简易恐惧贪婪指数（基于沪深300近期涨跌幅）"""
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol="sh000300")
        if df is not None and len(df) >= 20:
            recent = df.tail(20)
            close_prices = recent["close"].values
            change_20d = (close_prices[-1] - close_prices[0]) / close_prices[0] * 100
            # 简单映射：-20% → 恐惧(90)，+20% → 贪婪(90)
            if change_20d < -15:
                return 85  # 极度恐惧
            elif change_20d < -8:
                return 70  # 恐惧
            elif change_20d < 0:
                return 50  # 中性偏恐惧
            elif change_20d < 8:
                return 40  # 中性偏贪婪
            elif change_20d < 15:
                return 25  # 贪婪
            else:
                return 10  # 极度贪婪
    except Exception as e:
        print(f"[FGI] Failed: {e}")
    return 50  # 默认中性


# ---- API 路由 ----

@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now().isoformat()}


@app.get("/api/nav/{code}")
def get_nav(code: str):
    """获取单只基金净值"""
    return get_fund_nav(code)


@app.get("/api/nav/all")
def get_all_nav():
    """获取所有推荐基金的净值"""
    codes = ["110020", "050025", "217022", "000216", "070018"]
    result = {}
    for code in codes:
        result[code] = get_fund_nav(code)
    return result


@app.post("/api/signals")
def get_signals(portfolio: Portfolio):
    """根据持仓生成买卖信号"""
    signals = []

    if not portfolio.holdings:
        return signals

    total_amount = sum(h.amount for h in portfolio.holdings)
    if total_amount <= 0:
        return signals

    # 1. 再平衡信号：检查各资产偏离度
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

    # 2. 恐惧贪婪信号
    fgi = get_fear_greed_index()
    if fgi >= 75:
        signals.append({
            "icon": "😱",
            "title": "市场极度恐惧 — 可能是加仓机会",
            "message": f"恐惧指数 {fgi:.0f}/100。历史上极度恐惧时买入，长期收益概率较高。考虑用货币基金的弹药适当加仓。",
            "type": "fear",
            "severity": "opportunity",
        })
    elif fgi <= 25:
        signals.append({
            "icon": "🤑",
            "title": "市场过度贪婪 — 注意风险",
            "message": f"贪婪指数 {100 - fgi:.0f}/100。市场可能过热，建议不要追高，保持定投节奏即可。",
            "type": "greed",
            "severity": "warning",
        })

    # 3. 定投提醒
    now = datetime.now()
    if now.day <= 5:
        monthly_invest = total_amount * 0.1  # 假设月定投为总额的10%
        signals.append({
            "icon": "📅",
            "title": "本月定投提醒",
            "message": f"每月初是定投好时机。按你的配比，本月建议定投 ¥{monthly_invest:,.0f}。",
            "type": "dca",
            "severity": "info",
        })

    # 4. 持仓时间检查
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

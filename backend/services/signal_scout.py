"""
钱袋子 — 信号侦察兵 (Signal Scout)
v3.0 新增模块

职责:
  1. collect() — 从多源收集原始信号（新闻/公告/增减持/解禁/资金异动）
  2. match(user_id) — 将信号与用户持仓匹配（公共→私有）
  3. deliver(user_id) — 推送匹配的信号（企微/前端）
  4. enrich(ctx) — Pipeline 适配层，写入 DecisionContext

数据流:
  collect()=公共(全市场) → match(uid)=私有(用户相关) → deliver(uid)=推送

存储:
  - 原始信号缓存: 内存 30min（公共）
  - 匹配结果: data/{uid}/signals/YYYY-MM-DD.json（私有）
"""

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "signal_scout",
    "scope": "private",
    "input": ["user_id", "stock_holdings", "fund_holdings"],
    "output": "signals",
    "cost": "cpu",
    "tags": ["信号", "新闻", "公告", "增减持", "解禁"],
    "description": "信号侦察兵：多源信号收集(新闻/公告/增减持/解禁/资金)→持仓匹配→推送",
    "layer": "data",
    "priority": 1,
}

import os
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from config import DATA_DIR

# ---- 信号类型定义 ----
SIGNAL_TYPES = {
    "news_policy": "📜 政策信号",
    "news_market": "📰 市场新闻",
    "holder_change": "👔 增减持",
    "pledge_risk": "⚠️ 质押风险",
    "unlock": "🔓 解禁预警",
    "dividend": "💰 分红送转",
    "announcement": "📋 公告",
    "fund_flow": "💹 资金异动",
    "technical": "📊 技术信号",
    "st_warning": "🔴 ST预警",
}

# ---- 缓存 ----
_signal_cache = {}
_SIGNAL_CACHE_TTL = 1800  # 30 分钟

# ---- 休市日历 ----
_MARKET_HOLIDAYS_2026 = {
    "2026-01-01", "2026-01-02",  # 元旦
    "2026-01-26", "2026-01-27", "2026-01-28", "2026-01-29", "2026-01-30",  # 春节
    "2026-02-02", "2026-02-03",
    "2026-04-06",  # 清明
    "2026-05-01", "2026-05-04", "2026-05-05",  # 劳动节
    "2026-06-19",  # 端午
    "2026-09-28", "2026-09-29",  # 中秋
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",  # 国庆
}


def is_trading_day(dt: datetime = None) -> bool:
    """判断是否为交易日（排除周末+法定假日）"""
    if dt is None:
        dt = datetime.now()
    if dt.weekday() >= 5:  # 周六日
        return False
    return dt.strftime("%Y-%m-%d") not in _MARKET_HOLIDAYS_2026


# ============================================================
# 1. collect() — 公共信号收集（全市场，不涉及用户）
# ============================================================

def collect() -> list:
    """
    从多源并行收集原始信号，返回统一格式 list[dict]
    每条信号: {type, title, content, codes[], source, time, level, tags[]}
    """
    cache_key = "all_signals"
    now = time.time()
    if cache_key in _signal_cache and now - _signal_cache[cache_key]["ts"] < _SIGNAL_CACHE_TTL:
        return _signal_cache[cache_key]["data"]

    signals = []

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            pool.submit(_collect_news_signals): "news",
            pool.submit(_collect_holder_changes): "holder",
            pool.submit(_collect_unlock_signals): "unlock",
            pool.submit(_collect_fund_flow_signals): "fund_flow",
            pool.submit(_collect_technical_signals): "technical",
        }
        for fut in futures:
            try:
                result = fut.result(timeout=15)
                if result:
                    signals.extend(result)
            except Exception as e:
                print(f"[SIGNAL_SCOUT] {futures[fut]} failed: {e}")

    # 按时间倒序 + 去重
    seen = set()
    unique = []
    for s in sorted(signals, key=lambda x: x.get("time", ""), reverse=True):
        key = f"{s['type']}_{s['title'][:30]}"
        if key not in seen:
            seen.add(key)
            unique.append(s)

    _signal_cache[cache_key] = {"data": unique, "ts": now}
    return unique


def _collect_news_signals() -> list:
    """从新闻/政策数据中提取信号"""
    signals = []
    try:
        from services.news_data import get_market_news, get_policy_news

        # 政策新闻
        for n in get_policy_news(10):
            title = n.get("title", "")
            if "加载中" in title:
                continue
            level = _classify_news_level(title)
            signals.append({
                "type": "news_policy",
                "title": title,
                "content": n.get("summary", title),
                "codes": _extract_codes_from_text(title),
                "source": n.get("source", "政策"),
                "time": n.get("time", datetime.now().strftime("%H:%M")),
                "level": level,
                "tags": _extract_tags(title),
                "url": n.get("url", ""),
            })

        # 市场新闻
        for n in get_market_news(8):
            title = n.get("title", "")
            level = _classify_news_level(title)
            signals.append({
                "type": "news_market",
                "title": title,
                "content": n.get("summary", title),
                "codes": _extract_codes_from_text(title),
                "source": n.get("source", "市场"),
                "time": n.get("time", ""),
                "level": level,
                "tags": _extract_tags(title),
                "url": n.get("url", ""),
            })
    except Exception as e:
        print(f"[SIGNAL_SCOUT] news failed: {e}")
    return signals


def _collect_holder_changes() -> list:
    """从 Tushare 收集大股东增减持信号"""
    signals = []
    try:
        from services.tushare_data import get_holder_trades
        trades = get_holder_trades()
        for t in trades[:20]:
            action = "增持" if t.get("change_type") == "增持" else "减持"
            level = "warning" if action == "减持" else "info"
            signals.append({
                "type": "holder_change",
                "title": f"{t.get('holder_name', '股东')} {action} {t.get('ann_date', '')}",
                "content": f"变动股数: {t.get('change_vol', 0)}万股, 变动金额: {t.get('change_amount', 0)}万元",
                "codes": [t.get("ts_code", "").split(".")[0]],
                "source": "Tushare",
                "time": t.get("ann_date", ""),
                "level": level,
                "tags": [action, "股东"],
            })
    except Exception as e:
        print(f"[SIGNAL_SCOUT] holder_change failed: {e}")
    return signals


def _collect_unlock_signals() -> list:
    """收集限售股解禁信号"""
    signals = []
    try:
        from services.tushare_data import get_upcoming_unlocks
        unlocks = get_upcoming_unlocks()
        for u in unlocks[:10]:
            float_ratio = u.get("float_ratio", 0)
            level = "danger" if float_ratio > 5 else ("warning" if float_ratio > 2 else "info")
            signals.append({
                "type": "unlock",
                "title": f"解禁预警: {u.get('ts_code', '')} 解禁{float_ratio}%",
                "content": f"解禁日: {u.get('float_date', '')}, 解禁数量: {u.get('float_share', 0)}万股",
                "codes": [u.get("ts_code", "").split(".")[0]],
                "source": "Tushare",
                "time": u.get("float_date", ""),
                "level": level,
                "tags": ["解禁"],
            })
    except Exception as e:
        print(f"[SIGNAL_SCOUT] unlock failed: {e}")
    return signals


def _collect_fund_flow_signals() -> list:
    """收集资金异动信号"""
    signals = []
    try:
        from services.alt_data import get_northbound_flow_detail
        nb = get_northbound_flow_detail()
        if nb.get("available") and nb.get("top_stocks"):
            for s in nb["top_stocks"][:5]:
                signals.append({
                    "type": "fund_flow",
                    "title": f"北向资金买入: {s.get('name', '')} {s.get('net_amount', 0):.0f}万",
                    "content": f"持股变化: {s.get('hold_change', 0)}万股",
                    "codes": [s.get("code", "")],
                    "source": "北向",
                    "time": datetime.now().strftime("%H:%M"),
                    "level": "info",
                    "tags": ["北向", "资金"],
                })
    except Exception as e:
        print(f"[SIGNAL_SCOUT] fund_flow failed: {e}")
    return signals


def _collect_technical_signals() -> list:
    """收集技术面信号（从已有的盯盘数据中提取）"""
    signals = []
    try:
        # 涨停跌停池
        import akshare as ak
        try:
            df = ak.stock_zt_pool_em(date=datetime.now().strftime("%Y%m%d"))
            if df is not None and len(df) > 0:
                zt_count = len(df)
                signals.append({
                    "type": "technical",
                    "title": f"今日涨停 {zt_count} 只",
                    "content": f"涨停家数: {zt_count}",
                    "codes": [],
                    "source": "东财",
                    "time": datetime.now().strftime("%H:%M"),
                    "level": "info" if zt_count < 50 else "warning",
                    "tags": ["涨停", "情绪"],
                })
        except Exception:
            pass
    except Exception as e:
        print(f"[SIGNAL_SCOUT] technical failed: {e}")
    return signals


# ============================================================
# 2. match(user_id) — 私有匹配（信号→用户持仓）
# ============================================================

def match(user_id: str) -> list:
    """
    将公共信号与用户持仓匹配
    返回: 按相关性排序的信号列表，每条附加 relevance 和 holding_name
    """
    all_signals = collect()
    if not all_signals:
        return []

    # 获取用户持仓代码
    user_codes = set()
    user_names = {}
    try:
        from services.stock_monitor import load_stock_holdings
        for h in load_stock_holdings(user_id):
            code = h.get("code", "")
            if code:
                user_codes.add(code)
                user_names[code] = h.get("name", code)
    except Exception:
        pass
    try:
        from services.fund_monitor import load_fund_holdings
        for h in load_fund_holdings(user_id):
            code = h.get("code", "")
            if code:
                user_codes.add(code)
                user_names[code] = h.get("name", code)
    except Exception:
        pass

    matched = []
    for sig in all_signals:
        relevance = 0
        related_holding = ""

        # 直接代码匹配（最高相关性）
        for code in sig.get("codes", []):
            if code in user_codes:
                relevance = 100
                related_holding = user_names.get(code, code)
                break

        # 标签匹配（中等相关性）
        if relevance == 0:
            for tag in sig.get("tags", []):
                if tag in ["降息", "降准", "利好", "利空", "关税", "贸易战"]:
                    relevance = 50
                    break

        # 全市场信号（低相关性但仍有价值）
        if relevance == 0 and sig.get("level") in ("danger", "warning"):
            relevance = 30

        if relevance > 0:
            matched.append({
                **sig,
                "relevance": relevance,
                "related_holding": related_holding,
            })

    # 按相关性+级别排序
    level_order = {"danger": 0, "warning": 1, "info": 2}
    matched.sort(key=lambda x: (-x["relevance"], level_order.get(x.get("level", "info"), 2)))

    # 存储匹配结果
    _save_matched(user_id, matched)

    return matched


def _save_matched(user_id: str, signals: list):
    """保存匹配结果到用户目录"""
    try:
        d = DATA_DIR / user_id / "signals"
        d.mkdir(parents=True, exist_ok=True)
        fp = d / f"{datetime.now().strftime('%Y-%m-%d')}.json"
        fp.write_text(json.dumps(signals[:50], ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[SIGNAL_SCOUT] save failed: {e}")


# ============================================================
# 3. deliver(user_id) — 推送
# ============================================================

def deliver(user_id: str, signals: list = None) -> dict:
    """
    推送信号到企微
    只推高相关性(≥50)或危险级别的信号，避免骚扰
    """
    if signals is None:
        signals = match(user_id)

    important = [s for s in signals if s.get("relevance", 0) >= 50 or s.get("level") == "danger"]
    if not important:
        return {"pushed": 0, "reason": "无重要信号"}

    # 构建推送文本（纯文本，不用 Markdown — 铁律 #20）
    lines = [f"📡 信号侦察 ({len(important)}条)"]
    for s in important[:8]:
        icon = SIGNAL_TYPES.get(s["type"], "📌")
        holding = f" → {s['related_holding']}" if s.get("related_holding") else ""
        lines.append(f"{icon} {s['title']}{holding}")

    text = "\n".join(lines)

    try:
        from services.wxwork_push import send_text_message, is_configured
        if is_configured():
            send_text_message(text, user_id=user_id)
            return {"pushed": len(important), "text": text}
    except Exception as e:
        print(f"[SIGNAL_SCOUT] push failed: {e}")

    return {"pushed": 0, "text": text, "reason": "企微未配置或推送失败"}


# ============================================================
# 4. enrich(ctx) — Pipeline 适配层
# ============================================================

_enrich_cache = {}  # {user_id: {"data": matched, "ts": time}}
_ENRICH_CACHE_TTL = 900  # 15分钟

def enrich(ctx):
    """
    Pipeline 适配: 收集信号 → 匹配用户持仓 → 写入 ctx
    """
    import time as _time
    user_id = ctx.user_id
    if not user_id:
        return ctx

    now = _time.time()
    cached = _enrich_cache.get(user_id)
    if cached and now - cached.get("ts", 0) < _ENRICH_CACHE_TTL:
        matched = cached["data"]
        print("[SIGNAL_SCOUT] enrich using cache")
    else:
        matched = match(user_id)
        _enrich_cache[user_id] = {"data": matched, "ts": now}
    ctx.modules_results["signal_scout"] = {
        "available": True,
        "total_collected": len(collect()),
        "matched_count": len(matched),
        "high_relevance": len([s for s in matched if s.get("relevance", 0) >= 50]),
        "danger_count": len([s for s in matched if s.get("level") == "danger"]),
        "top_signals": matched[:5],
        "confidence": min(0.8, len(matched) / 20),
        "direction": _infer_direction(matched),
    }

    # 如果用户问的是具体个股/基金，拉新闻（关键增量信息）
    stock_code = getattr(ctx, "question_stock_code", "")
    stock_name = getattr(ctx, "question_stock_name", "")
    is_fund = getattr(ctx, "question_is_fund", False)

    if stock_code or stock_name:
        try:
            if is_fund:
                # 基金新闻
                fund_news = _fetch_fund_news(stock_code, stock_name)
                if fund_news:
                    ctx.modules_results["signal_scout"]["fund_news"] = fund_news[:5]
                    ctx.modules_results["signal_scout"]["fund_news_count"] = len(fund_news)
                    print(f"[SIGNAL_SCOUT] 基金新闻: {stock_name or stock_code} → {len(fund_news)}条")
            else:
                # 个股新闻
                stock_news = _fetch_stock_news(stock_code, stock_name)
                if stock_news:
                    ctx.modules_results["signal_scout"]["stock_news"] = stock_news[:5]
                    ctx.modules_results["signal_scout"]["stock_news_count"] = len(stock_news)
                    news_direction = _infer_news_direction(stock_news)
                    if news_direction != "neutral":
                        ctx.modules_results["signal_scout"]["stock_news_direction"] = news_direction
                    print(f"[SIGNAL_SCOUT] 个股新闻: {stock_name or stock_code} → {len(stock_news)}条, 方向={news_direction}")
        except Exception as e:
            print(f"[SIGNAL_SCOUT] 新闻获取失败: {e}")

    return ctx


def _infer_direction(signals: list) -> str:
    """从信号推断整体方向"""
    bull = sum(1 for s in signals if any(t in s.get("tags", []) for t in ["利好", "增持", "降息", "买入"]))
    bear = sum(1 for s in signals if any(t in s.get("tags", []) for t in ["利空", "减持", "加息", "卖出", "ST"]))
    if bull > bear + 2:
        return "bullish"
    if bear > bull + 2:
        return "bearish"
    return "neutral"


def _fetch_stock_news(code: str, name: str) -> list:
    """拉取个股新闻（AKShare + 已有新闻接口）"""
    news = []
    try:
        # 方式1：用已有的 news_data 接口按代码搜
        from services.news_data import get_market_news
        all_news = get_market_news(30)
        # 过滤：标题中包含股票名或代码
        search_terms = [t for t in [name, code] if t]
        for n in all_news:
            title = n.get("title", "")
            if any(term in title for term in search_terms):
                news.append(n)
    except Exception as e:
        print(f"[STOCK_NEWS] market_news filter failed: {e}")

    try:
        # 方式2：AKShare 个股新闻（东方财富）
        import akshare as ak
        df = ak.stock_news_em(symbol=code)  # 直接传6位代码
        if df is not None and len(df) > 0:
            for _, row in df.head(10).iterrows():
                title = str(row.get("新闻标题", ""))
                pub_time = str(row.get("发布时间", ""))
                url = str(row.get("新闻链接", ""))
                source = str(row.get("文章来源", "东财"))
                if title and title not in [n.get("title") for n in news]:
                    news.append({"title": title, "time": pub_time, "url": url, "source": source})
    except Exception as e:
        print(f"[STOCK_NEWS] akshare failed: {e}")

    return news[:10]


def _fetch_fund_news(code: str, name: str) -> list:
    """拉取基金相关新闻"""
    news = []
    try:
        from services.data_layer import get_fund_news
        fund_news = get_fund_news(code, 8)
        for n in fund_news:
            title = n.get("title", "")
            if title and "加载中" not in title:
                news.append(n)
    except Exception as e:
        print(f"[FUND_NEWS] fund_news failed: {e}")

    # 补充：从大盘新闻里筛选和基金相关的
    try:
        from services.news_data import get_market_news
        all_news = get_market_news(30)
        search_terms = [t for t in [name, code] if t]
        for n in all_news:
            title = n.get("title", "")
            if any(term in title for term in search_terms):
                if title not in [x.get("title") for x in news]:
                    news.append(n)
    except Exception:
        pass

    return news[:10]


def _infer_news_direction(news: list) -> str:
    """从个股新闻推断方向"""
    BULL_KW = ["利好", "增持", "回购", "业绩超预期", "大单", "涨停", "突破", "新高"]
    BEAR_KW = ["利空", "减持", "质押", "业绩下滑", "亏损", "跌停", "暴跌", "ST", "处罚", "退市"]
    bull = 0
    bear = 0
    for n in news:
        title = n.get("title", "")
        if any(k in title for k in BULL_KW):
            bull += 1
        if any(k in title for k in BEAR_KW):
            bear += 1
    if bull > bear + 1:
        return "bullish"
    if bear > bull + 1:
        return "bearish"
    return "neutral"


# ============================================================
# 5. API 辅助函数
# ============================================================

def get_latest(user_id: str) -> dict:
    """获取最新匹配信号（供 API 调用）"""
    matched = match(user_id)
    return {
        "signals": matched[:20],
        "total": len(matched),
        "high_relevance": len([s for s in matched if s.get("relevance", 0) >= 50]),
        "scanned_at": datetime.now().isoformat(),
        "is_trading_day": is_trading_day(),
    }


def get_history(user_id: str, days: int = 7) -> list:
    """获取历史信号"""
    results = []
    d = DATA_DIR / user_id / "signals"
    if not d.exists():
        return []

    for i in range(days):
        dt = datetime.now() - timedelta(days=i)
        fp = d / f"{dt.strftime('%Y-%m-%d')}.json"
        if fp.exists():
            try:
                signals = json.loads(fp.read_text(encoding="utf-8"))
                results.append({
                    "date": dt.strftime("%Y-%m-%d"),
                    "count": len(signals),
                    "signals": signals[:10],
                })
            except Exception:
                pass
    return results


# ============================================================
# 工具函数
# ============================================================

# 利好/利空关键词
_BULL_KW = ["降息", "降准", "宽松", "利好", "上涨", "增持", "反弹", "刺激", "补贴", "减税"]
_BEAR_KW = ["加息", "收紧", "利空", "下跌", "减持", "暴跌", "制裁", "关税", "处罚", "退市"]


def _classify_news_level(title: str) -> str:
    """根据标题关键词判断信号级别"""
    if any(k in title for k in ["暴跌", "崩盘", "退市", "爆仓", "处罚"]):
        return "danger"
    if any(k in title for k in _BEAR_KW):
        return "warning"
    if any(k in title for k in _BULL_KW):
        return "info"
    return "info"


def _extract_tags(title: str) -> list:
    """从标题提取标签"""
    tags = []
    tag_map = {
        "降息": "降息", "降准": "降准", "关税": "关税", "贸易": "贸易战",
        "半导体": "科技", "芯片": "科技", "AI": "科技", "利好": "利好",
        "利空": "利空", "增持": "增持", "减持": "减持", "解禁": "解禁",
        "房地产": "地产", "央行": "央行", "美联储": "美联储",
    }
    for kw, tag in tag_map.items():
        if kw in title:
            tags.append(tag)
    return tags[:5]


def _extract_codes_from_text(text: str) -> list:
    """从文本提取股票代码（6位数字）"""
    import re
    codes = re.findall(r'\b(\d{6})\b', text)
    return list(set(codes))[:5]

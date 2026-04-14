"""
钱袋子 — 持仓关联智能
每只持仓股票/基金自动关联：新闻、资金流、解禁、行业动态
结果注入 DeepSeek 实现个股级预警（不只是大盘级）
"""
import time
import traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "holding_intelligence",
    "scope": "private",
    "input": ["user_id", "stock_code"],
    "output": "holding_intel",
    "cost": "cpu",
    "tags": ["个股情报", "新闻", "资金流", "解禁"],
    "description": "持仓关联智能：个股新闻+资金流+行业动态+解禁预警",
    "layer": "data",
    "priority": 2,
}

_intel_cache = {}
_CACHE_TTL = 600  # 10 分钟


def get_stock_news(code: str, limit: int = 5) -> list:
    """获取个股相关新闻"""
    cache_key = f"snews_{code}"
    now = time.time()
    if cache_key in _intel_cache and now - _intel_cache[cache_key]["ts"] < _CACHE_TTL:
        return _intel_cache[cache_key]["data"]

    result = []
    try:
        import akshare as ak
        df = ak.stock_news_em(symbol=code)
        if df is not None and len(df) > 0:
            cols = list(df.columns)
            title_col = [c for c in cols if "标题" in c or "title" in c.lower() or "新闻" in c]
            time_col = [c for c in cols if "时间" in c or "日期" in c or "date" in c.lower()]
            url_col = [c for c in cols if "链接" in c or "url" in c.lower()]

            tc = title_col[0] if title_col else cols[0]
            dc = time_col[0] if time_col else None
            uc = url_col[0] if url_col else None

            for _, row in df.head(limit).iterrows():
                item = {"title": str(row[tc])}
                if dc:
                    item["time"] = str(row[dc])
                if uc:
                    item["url"] = str(row[uc])
                result.append(item)
    except Exception as e:
        print(f"[STOCK_NEWS] {code} fail: {e}")

    _intel_cache[cache_key] = {"data": result, "ts": now}
    return result


def get_stock_fund_flow(code: str) -> dict:
    """获取个股主力资金流向"""
    cache_key = f"sflow_{code}"
    now = time.time()
    if cache_key in _intel_cache and now - _intel_cache[cache_key]["ts"] < _CACHE_TTL:
        return _intel_cache[cache_key]["data"]

    result = {"available": False}
    try:
        import akshare as ak
        df = ak.stock_individual_fund_flow(stock=code, market="sh" if code.startswith("6") else "sz")
        if df is not None and len(df) > 0:
            last = df.iloc[-1]
            cols = list(df.columns)
            # 找主力净流入列
            main_col = [c for c in cols if "主力" in c and "净" in c]
            if main_col:
                val = _safe_float(last[main_col[0]])
                if val is not None:
                    result["main_net"] = round(val, 2)
                    result["main_net_str"] = f"{val/10000:.1f}万" if abs(val) < 1e8 else f"{val/1e8:.2f}亿"
                    result["direction"] = "流入" if val > 0 else "流出"
                    result["available"] = True
    except Exception as e:
        print(f"[FUND_FLOW] {code} fail: {e}")

    _intel_cache[cache_key] = {"data": result, "ts": now}
    return result


def get_stock_industry(code: str) -> str:
    """获取个股所属行业"""
    cache_key = f"sindustry_{code}"
    now = time.time()
    if cache_key in _intel_cache and now - _intel_cache[cache_key]["ts"] < 86400:  # 24h
        return _intel_cache[cache_key]["data"]

    industry = "未知"
    try:
        import akshare as ak
        df = ak.stock_individual_info_em(symbol=code)
        if df is not None and len(df) > 0:
            # 找行业行
            for _, row in df.iterrows():
                vals = [str(v) for v in row.values]
                if any("行业" in v for v in vals):
                    # 行业值通常在第二列
                    industry = vals[1] if len(vals) > 1 else vals[0]
                    break
    except Exception as e:
        print(f"[INDUSTRY] {code} fail: {e}")

    _intel_cache[cache_key] = {"data": industry, "ts": now}
    return industry


def get_industry_news(industry: str, limit: int = 3) -> list:
    """获取行业相关新闻"""
    if not industry or industry == "未知":
        return []
    cache_key = f"indnews_{industry}"
    now = time.time()
    if cache_key in _intel_cache and now - _intel_cache[cache_key]["ts"] < _CACHE_TTL:
        return _intel_cache[cache_key]["data"]

    result = []
    try:
        import akshare as ak
        df = ak.stock_news_em(symbol=industry)
        if df is not None and len(df) > 0:
            cols = list(df.columns)
            tc = [c for c in cols if "标题" in c or "title" in c.lower()]
            tc = tc[0] if tc else cols[0]
            for _, row in df.head(limit).iterrows():
                result.append({"title": str(row[tc]), "source": "行业"})
    except Exception:
        pass

    _intel_cache[cache_key] = {"data": result, "ts": now}
    return result


def scan_single_holding(code: str, name: str = "") -> dict:
    """扫描单只持仓的全部关联信息"""
    intel = {
        "code": code,
        "name": name,
        "news": [],
        "fund_flow": {},
        "industry": "未知",
        "industry_news": [],
        "alerts": [],
    }

    # 1. 个股新闻
    intel["news"] = get_stock_news(code, 3)

    # 2. 资金流向
    intel["fund_flow"] = get_stock_fund_flow(code)
    if intel["fund_flow"].get("available"):
        net = intel["fund_flow"].get("main_net", 0)
        if net < -5000_0000:  # 主力净流出超 5000 万
            intel["alerts"].append({
                "level": "warning",
                "msg": f"⚠️ {name or code} 主力资金净流出 {intel['fund_flow']['main_net_str']}",
            })
        elif net > 1_0000_0000:  # 主力净流入超 1 亿
            intel["alerts"].append({
                "level": "info",
                "msg": f"💰 {name or code} 主力资金净流入 {intel['fund_flow']['main_net_str']}",
            })

    # 3. 行业
    intel["industry"] = get_stock_industry(code)

    # 4. 行业新闻
    intel["industry_news"] = get_industry_news(intel["industry"], 2)

    return intel


def scan_all_holding_intelligence(user_id: str = "default") -> dict:
    """
    全持仓智能扫描 — 遍历用户每只股票，关联新闻/资金/行业
    返回结构化数据供 DeepSeek 分析
    """
    cache_key = f"intel_all_{user_id}"
    now = time.time()
    if cache_key in _intel_cache and now - _intel_cache[cache_key]["ts"] < _CACHE_TTL:
        return _intel_cache[cache_key]["data"]

    result = {
        "holdings": [],
        "all_alerts": [],
        "summary": "",
        "updatedAt": datetime.now().isoformat(),
    }

    # 加载股票持仓
    try:
        from services.stock_monitor import load_stock_holdings
        holdings = load_stock_holdings(user_id)
    except Exception:
        holdings = []

    if not holdings:
        result["summary"] = "暂无股票持仓"
        _intel_cache[cache_key] = {"data": result, "ts": now}
        return result

    # 并发扫描（最多 5 个线程）
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(scan_single_holding, h.get("code", ""), h.get("name", "")): h
            for h in holdings if h.get("code")
        }
        for future in as_completed(futures):
            try:
                intel = future.result()
                result["holdings"].append(intel)
                result["all_alerts"].extend(intel.get("alerts", []))
            except Exception as e:
                print(f"[INTEL] Scan fail: {e}")

    # 检查限售解禁
    try:
        from services.market_factors import check_holding_unlock
        codes = [h.get("code", "") for h in holdings if h.get("code")]
        unlock_alerts = check_holding_unlock(codes)
        result["all_alerts"].extend(unlock_alerts)
    except Exception as e:
        print(f"[INTEL] Unlock check fail: {e}")

    # 汇总
    alert_count = len(result["all_alerts"])
    result["summary"] = f"扫描 {len(result['holdings'])} 只持仓，发现 {alert_count} 条关联信号"

    _intel_cache[cache_key] = {"data": result, "ts": now}
    return result


def build_holding_context(user_id: str = "default") -> str:
    """构建持仓关联上下文（注入 DeepSeek system prompt）"""
    intel = scan_all_holding_intelligence(user_id)
    if not intel["holdings"]:
        return ""

    lines = ["\n【持仓关联情报】"]
    for h in intel["holdings"]:
        lines.append(f"\n  {h['name']}({h['code']}) — 行业:{h['industry']}")
        if h.get("fund_flow", {}).get("available"):
            ff = h["fund_flow"]
            lines.append(f"    资金流向: 主力{ff['direction']}{ff['main_net_str']}")
        if h.get("news"):
            lines.append(f"    近期新闻: {'; '.join([n['title'][:30] for n in h['news'][:2]])}")
        if h.get("industry_news"):
            lines.append(f"    行业动态: {'; '.join([n['title'][:30] for n in h['industry_news'][:2]])}")

    if intel.get("all_alerts"):
        lines.append("\n【持仓预警】")
        for a in intel["all_alerts"]:
            lines.append(f"  [{a['level']}] {a['msg']}")

    return "\n".join(lines)


def _safe_float(x):
    if x is None:
        return None
    try:
        v = float(x)
        return None if v != v else v
    except (ValueError, TypeError):
        return None

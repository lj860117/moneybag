"""
共享辅助函数（从 main.py 提取）
================================
包含被多个 api/ 路由文件共享的:
  - 市场上下文构建 (_build_market_context)
  - 持仓上下文构建 (_build_portfolio_context)
  - System prompt 加载与构建
  - 规则引擎降级回答 (_rule_based_reply)
  - 聊天意图分类 (classify_chat_intent)
  - OCR 处理 (_do_ocr)
  - 预警冷却字典 (_alert_cooldown)
  - 用户偏好默认值 (USER_DEFAULTS / USER_OVERRIDES)
  - 家庭成员常量 (FAMILY_MEMBERS / NICKNAMES)
  - 可用模型列表 (AVAILABLE_MODELS)
  - 静态文件缓存 (_cached_file_response)

Design doc: docs/design/12-framework-refactor.md §四
"""
from __future__ import annotations
import os
import json
import time
import re as _re
from pathlib import Path

from services.data_layer import (
    get_fund_nav, get_fear_greed_index, get_valuation_percentile,
    get_technical_indicators, get_fund_news, get_market_news,
    get_macro_calendar, get_northbound_flow, get_margin_trading,
    get_shibor, get_dividend_yield, get_news_sentiment_score,
    get_policy_news, analyze_news_impact,
)
from services.signal import calc_smart_dca

from fastapi.responses import FileResponse
from infra.cache import MemoryCache


# ========================================================
# v9.5.122: 市场上下文 — 文件缓存优先，后台 cache_warmer 预热
# ========================================================
_MARKET_CTX_FILE = os.path.join(os.environ.get("DATA_DIR", "data"), "_cache", "market_context.txt")
_MARKET_CTX_TTL = 300  # 内存加速层（防同一秒多个对话重复读文件）
_market_ctx_cache = MemoryCache(default_ttl=_MARKET_CTX_TTL)


def _build_market_context() -> str:
    """构建市场数据上下文。
    
    v9.5.122 缓存策略：
    1. 内存缓存 5min（防同秒重复调用）
    2. 文件缓存 10h + stale 24h（后台 cache_warmer 刷新）
    3. 都没命中才实时计算
    """
    cache_key = "market_context"
    cached = _market_ctx_cache.get(cache_key)
    if cached is not None:
        return cached
    # 读文件缓存（10h 有效 + stale 24h）
    try:
        if os.path.exists(_MARKET_CTX_FILE):
            stat = os.stat(_MARKET_CTX_FILE)
            age = time.time() - stat.st_mtime
            if age < 36000:  # 10h
                with open(_MARKET_CTX_FILE, "r", encoding="utf-8") as f:
                    content = f.read()
                if content.strip():
                    _market_ctx_cache.set(cache_key, content, ttl=_MARKET_CTX_TTL)
                    return content
            elif age < 86400:  # stale 24h — 先返旧的，后台会刷新
                with open(_MARKET_CTX_FILE, "r", encoding="utf-8") as f:
                    content = f.read()
                if content.strip():
                    _market_ctx_cache.set(cache_key, content, ttl=60)
                    return content
    except Exception:
        pass
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
        # 沪深300实时点位（从布林带 current 字段获取）
        hs300_price = tech.get('bollinger', {}).get('current', 0)
        if hs300_price:
            lines.append(f"沪深300指数：当前 {hs300_price} 点")
        lines.append(f"RSI(14)：{tech['rsi']}（{tech['rsi_signal']}）")
        lines.append(f"MACD：{tech['macd']['trend']}")
        lines.append(f"布林带：{tech['bollinger']['position']}（上轨{tech['bollinger'].get('upper',0)} 中轨{tech['bollinger'].get('middle',0)} 下轨{tech['bollinger'].get('lower',0)}）")
    except Exception:
        pass

    codes = {"110020": "沪深300", "050025": "标普500", "000216": "黄金"}
    for code, name in codes.items():
        nav = get_fund_nav(code)
        if nav["nav"] != "N/A":
            lines.append(f"{name}({code})：净值 {nav['nav']}，日涨跌 {nav['change']}%")

    # 宏观经济数据
    try:
        macro = get_macro_calendar()
        macro_parts = []
        for key, label in [("cpi", "CPI"), ("pmi", "PMI"), ("m2", "M2"), ("ppi", "PPI")]:
            item = macro.get(key, {}) if isinstance(macro, dict) else {}
            val = item.get("value")
            if val and val != "N/A":
                macro_parts.append(f"{label}:{val}")
        if macro_parts:
            lines.append(f"宏观数据：{' | '.join(macro_parts)}")
    except Exception:
        pass

    # 最新政策/国际新闻摘要
    try:
        policy = get_policy_news(10)
        valid = [n for n in policy if n["title"] != "政策资讯加载中..."]
        if valid:
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

    # 全球市场数据
    try:
        from services.global_market import get_global_snapshot
        gs = get_global_snapshot()
        if gs.get("summary"):
            lines.append("")
            lines.append(gs["summary"])
    except Exception:
        pass

    # 国内政策数据
    try:
        from services.policy_data import get_policy_summary_for_context
        policy_ctx = get_policy_summary_for_context()
        if policy_ctx:
            lines.append("\n国内政策动态：")
            lines.append(policy_ctx)
    except Exception:
        pass

    # V8 扩展宏观
    try:
        from services.macro_v8 import get_v8_macro_summary
        v8_ctx = get_v8_macro_summary()
        if v8_ctx:
            lines.append("\n经济基本面：")
            lines.append(v8_ctx)
    except Exception:
        pass

    # 大宗商品 + ETF 资金流
    try:
        from services.market_factors import get_commodity_prices, get_etf_fund_flow
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
        from services.market_factors import get_etf_fund_flow
        etf = get_etf_fund_flow()
        if etf.get("available") and etf.get("top_inflow"):
            top = etf["top_inflow"][0]
            lines.append(f"ETF资金流：TOP流入 {top['name']}({top['flow']:.0f}万)")
    except Exception:
        pass

    # 资金面三件套
    # ── 北向资金：净流入不可得，只报成交额活跃度 ──
    # 2024-08-19 起沪深交易所停止披露北向「日频净买入」（改为按季度公布），
    # Tushare moneyflow_hsgt 的 north_money/hgt/sgt 此后填的是【当日成交额】。
    # 旧代码在这里拼「今日单日X亿 | 5日累计Y亿（趋势）」，数字来自对成交额做
    # 相邻日差分，是纯噪声（20日 -759.8亿 vs 5日 +100.3亿 符号相反），而这段
    # 正是喂给企业微信对话 / 站内问答 / 预制问答的主 prompt。
    #
    # ⚠️ 三个必须注意的点：
    #   1) net_flow_* 现在是 None 而非缺键 → `.get(key, 0)` 的默认值【不会生效】，
    #      必须显式 `is None` 判断，否则 f"{None:+.1f}" 抛 TypeError；
    #   2) 判断净流入可用性看 net_flow_available，不是 available
    #      （available=True 只代表成交额可得）；
    #   3) 数据层已把 flow_5d_range 改名为 turnover_5d_range，旧名读出来恒空。
    try:
        north = get_northbound_flow() or {}
        _nb_reason = (north.get("unavailable_reason")
                      or "2024-08-19 起沪深交易所停止披露北向日频净买入，改为按季度公布")
        _nb_dd = str(north.get("data_date") or "")
        _nb_date_label = ""
        if len(_nb_dd) == 8 and _nb_dd.isdigit():
            _nb_date_label = f"{int(_nb_dd[4:6])}/{int(_nb_dd[6:8])}"
        _nb_turnover = north.get("turnover_today")
        if north.get("available") and _nb_turnover is not None:
            _nb_parts = [f"{_nb_date_label or '最新'}成交额{float(_nb_turnover):.0f}亿元"]
            _nb_avg5 = north.get("turnover_avg_5d")
            if _nb_avg5 is not None:
                _nb_range = north.get("turnover_5d_range") or ""
                _nb_parts.append(f"近5日日均{float(_nb_avg5):.0f}亿元"
                                 + (f"（{_nb_range}）" if _nb_range else ""))
            _nb_avg20 = north.get("turnover_avg_20d")
            if _nb_avg20 is not None:
                _nb_parts.append(f"近20日日均{float(_nb_avg20):.0f}亿元")
            _nb_parts.append(f"活跃度「{north.get('turnover_trend') or '平稳'}」")
            lines.append(
                f"\n资金面：北向资金 —— 日频净流入已不可得（{_nb_reason}），本次分析没有这项数据；"
                f"仅有成交额：{'，'.join(_nb_parts)}"
                f"\n  注：成交额是买入+卖出的双边合计，不含方向。请勿据此推断外资流入/流出方向，"
                f"也不要给出任何北向净买入/净卖出的金额或结论（该数据已停止披露）；"
                f"用户问外资流向时，如实说明交易所已停止披露日频净买入、改为按季度公布。"
            )
        else:
            lines.append(
                f"\n资金面：北向资金数据不可得（{_nb_reason}）。"
                f"请勿推断外资流入/流出方向，也不要编造净买入金额。"
            )
    except Exception as e:
        # 本次教训的直接应用：原来这里是 `except Exception: pass`，净流入变 None 后
        # 格式化抛 TypeError 被静默吞掉 → 整条「资金面：北向资金…」从 prompt 里消失，
        # 而融资融券在下一个独立 try 块里照常输出，prompt 看起来"资金面还在"，
        # 实际只剩两融，且没有任何日志/告警。沉默的降级 = 看不见的错误，必须留痕。
        print(f"[MARKET_CTX] northbound injection failed, section skipped: "
              f"{type(e).__name__}: {e}")
    try:
        margin = get_margin_trading()
        if margin.get("available"):
            lines.append(f"融资融券：余额{margin.get('margin_balance', 0):.0f}亿，5日变动{margin.get('margin_change_5d', 0):+.1f}%")
    except Exception:
        pass
    try:
        shibor_data = get_shibor()
        if shibor_data.get("available"):
            lines.append(f"SHIBOR：隔夜{shibor_data.get('overnight', 0)}%（{shibor_data.get('trend', '')}）")
    except Exception:
        pass

    # v9.5.123: 注入预热数据 + 标注时效性(避免和晨报矛盾时用户困惑)
    from datetime import datetime as _dt_ctx
    lines.append(f"\n--- 以下为AI增强数据(更新: {_dt_ctx.now().strftime('%H:%M')}) ---")
    # 行业热点
    try:
        from services.sector_rotation import get_sector_ranking
        sr = get_sector_ranking()
        if sr and sr.get("available") and sr.get("top_gainers"):
            hot = sr["top_gainers"][:3]
            hot_text = " | ".join(f"{s.get('name','')} {s.get('change_pct',0):+.1f}%" for s in hot)
            lines.append(f"\n行业热点：{hot_text}")
            cold = sr.get("top_losers", [])[:2]
            if cold:
                cold_text = " | ".join(f"{s.get('name','')} {s.get('change_pct',0):+.1f}%" for s in cold)
                lines.append(f"行业冷门：{cold_text}")
    except Exception:
        pass

    # 8维走势信号(偏多/偏空/震荡) + 定投倍率
    try:
        from services.precomputed_cache import get_precomputed
        sig = get_precomputed("daily_signal")
        # get_precomputed直接返回data dict(不是{data:xxx}), 所以sig就是signal本身
        if sig and isinstance(sig, dict) and sig.get("overall"):
            overall = sig.get("overall", "")  # HOLD/BUY/SELL
            score = sig.get("score", 0)
            summary = sig.get("summary", "")
            dca_mult = sig.get("smartDca", {}).get("multiplier", 1.0)
            if summary:
                lines.append(f"\n今日综合信号：{summary}")
                lines.append(f"信号评分：{score:+.1f}，方向：{overall}")
                if dca_mult and dca_mult != 1.0:
                    lines.append(f"定投倍率建议：{dca_mult:.1f}x（{'加码' if dca_mult > 1 else '缩减'}）")
                else:
                    lines.append("定投倍率建议：1.0x（标准定投）")
    except Exception as e:
        print(f"[MARKET_CTX] daily_signal injection failed: {e}")

    # 选基TOP3摘要(用户问"有什么好基金"时AI能直接回答)
    try:
        import json as _json_ctx
        import glob as _glob_ctx
        cache_dir = os.environ.get("DATA_DIR", "data")
        cache_path = os.path.join(cache_dir, "_cache")
        # 找任何一个 fund_screen_all_score_*.json 文件
        fs_files = _glob_ctx.glob(os.path.join(cache_path, "fund_screen_all_score_*.json"))
        if fs_files:
            fs_file = sorted(fs_files, key=os.path.getmtime, reverse=True)[0]  # 最新的
            fs_data = _json_ctx.loads(open(fs_file, encoding="utf-8").read())
            # 缓存结构: {"data": {"funds": [...]}, "expires_at": ...}
            inner = fs_data.get("data", fs_data)  # 兼容直接结构和嵌套结构
            funds = inner.get("funds", [])[:3]
            if funds:
                lines.append(f"\n选基推荐TOP3：")
                for f in funds:
                    r = f.get("returns", {})
                    lines.append(f"  - {f.get('name','')[:12]} 评分{f.get('score',0):.0f} 近1年{r.get('1y','?')}%")
    except Exception as e:
        print(f"[MARKET_CTX] fund_screen injection failed: {e}")

    result = "\n".join(lines) if lines else "暂无市场数据"
    _market_ctx_cache.set("market_context", result, ttl=_MARKET_CTX_TTL)
    # v9.5.122: 写文件缓存（供后续请求和重启后读取）
    try:
        os.makedirs(os.path.dirname(_MARKET_CTX_FILE), exist_ok=True)
        with open(_MARKET_CTX_FILE, "w", encoding="utf-8") as f:
            f.write(result)
    except Exception:
        pass
    return result


# ========================================================
# v9.5.122: 持仓上下文 — per-user 文件缓存，后台 cache_warmer 预热
# ========================================================
_PORTFOLIO_CTX_DIR = os.path.join(os.environ.get("DATA_DIR", "data"), "_cache")


def _build_portfolio_context(p=None, user_id: str = "default") -> str:
    """构建用户持仓+盈亏+风控+配置建议的完整上下文（多用户隔离）
    
    v9.5.122: 文件缓存 10h + stale 24h，cache_warmer 预热。
    """
    # 读 per-user 文件缓存
    ctx_file = os.path.join(_PORTFOLIO_CTX_DIR, f"portfolio_ctx_{user_id}.txt")
    try:
        if os.path.exists(ctx_file):
            age = time.time() - os.stat(ctx_file).st_mtime
            if age < 36000:  # 10h
                with open(ctx_file, "r", encoding="utf-8") as f:
                    content = f.read()
                if content.strip():
                    return content
            elif age < 86400:  # stale 24h
                with open(ctx_file, "r", encoding="utf-8") as f:
                    content = f.read()
                if content.strip():
                    return content
    except Exception:
        pass
    
    from services.holding_intelligence import build_holding_context

    lines = []

    # 1. 基本持仓信息
    if p and p.holdings:
        lines.append(f"【用户画像】风险类型：{p.profile}，总投入：¥{p.amount:,.0f}")
        lines.append("【持仓明细】")
        for h in p.holdings:
            lines.append(f"  - {h.name}({h.code})：¥{h.amount:,.0f}，目标占比 {h.targetPct}%")
    else:
        # 后端主动拉取真实持仓（stock-holdings + fund-holdings + assets）
        _has_data = False
        try:
            from services.stock_monitor import load_stock_holdings
            from services.fund_monitor import load_fund_holdings
            stocks = load_stock_holdings(user_id) or []
            funds = load_fund_holdings(user_id) or []
            if stocks or funds:
                _has_data = True
                lines.append("【持仓明细】（后端真实数据）")
                for s in stocks:
                    lines.append(f"  - 股票：{s.get('name','?')}({s.get('code','')}) {s.get('shares',0)}股 成本¥{s.get('costPrice',0)}")
                for f in funds:
                    lines.append(f"  - 基金：{f.get('name','?')}({f.get('code','')}) {f.get('shares',0)}份 成本{f.get('costNav',0)}")
        except Exception:
            pass
        try:
            from services.unified_networth import calc_unified_networth
            nw = calc_unified_networth(user_id)
            if nw and nw.get("netWorth", 0) > 0:
                _has_data = True
                lines.append(f"  - 净资产：¥{nw['netWorth']:,.0f}（投资¥{nw.get('breakdown',{}).get('investment',{}).get('total',0):,.0f} + 现金¥{nw.get('breakdown',{}).get('cash',{}).get('total',0):,.0f}）")
        except Exception:
            pass
        if not _has_data:
            lines.append("【重要】当前用户在钱袋子中没有任何持仓/资产记录。如果用户问'我持有什么'，必须回答'当前记录中没有持仓数据'。不要用市场新闻或泛数据来替代回答。")

    # 1.5 家庭画像（风险偏好/投资目标/约束）
    if user_id and user_id != "default":
        try:
            from domain.services.user_preference_service import get_profile
            profile = get_profile(user_id)
            if profile and isinstance(profile, dict):
                risk = profile.get("risk_level") or profile.get("riskLevel") or profile.get("risk_tolerance")
                goal = profile.get("investment_goal") or profile.get("goal")
                horizon = profile.get("horizon") or profile.get("investment_horizon")
                if any([risk, goal, horizon]):
                    lines.append(f"\n【家庭画像】")
                    if risk: lines.append(f"  风险偏好：{risk}")
                    if goal: lines.append(f"  投资目标：{goal}")
                    if horizon: lines.append(f"  投资期限：{horizon}")
        except Exception:
            pass

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

    # 4. 持仓关联智能
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

    # 5.5 v9.5.98: 持仓股票的业绩预告 + 龙虎榜活跃度
    try:
        from services.stock_monitor import load_stock_holdings
        from services.tushare_data import get_earning_forecast, get_top_list
        from datetime import datetime, timedelta
        holdings = load_stock_holdings(user_id) or []
        codes = [h.get("code", "") for h in holdings if h.get("code")][:10]  # 最多10只
        if codes:
            forecast_alerts = []
            for code in codes:
                try:
                    fc = get_earning_forecast(code=code) or []
                    if fc:
                        latest = fc[0]
                        ftype = latest.get("type", "")  # 预增/预减/预亏 等
                        pmin = latest.get("p_change_min", 0) or 0
                        pmax = latest.get("p_change_max", 0) or 0
                        if ftype:
                            sign = "📈" if "增" in ftype else ("📉" if "减" in ftype or "亏" in ftype else "•")
                            forecast_alerts.append(f"{sign} {code}: {ftype}（净利变动 {pmin:.0f}%~{pmax:.0f}%）")
                except Exception:
                    pass
            if forecast_alerts:
                lines.append("\n【持仓股业绩预告】")
                for a in forecast_alerts[:5]:
                    lines.append(f"  {a}")

            # 龙虎榜：今天上榜的持仓股
            try:
                today_top = get_top_list() or []
                hit_top = [t for t in today_top if any(c in t.get("ts_code", "") for c in codes)]
                if hit_top:
                    lines.append("\n【今日上龙虎榜】")
                    for t in hit_top[:3]:
                        # 龙虎榜(top_list) 与北向是不同数据源，未受 2024-08-19 沪深港通口径变更影响。
                        # 2026-08-28 实测确认 net_amount 有值（5410945.7），所以下面的缺值分支
                        # 只是纵深防御。措辞刻意用"本次缺失"而非"未披露"——龙虎榜照常每日披露，
                        # 说"未披露"会把一次偶发的取数失败误说成披露制度问题（只有北向净买入是
                        # 永久性不可得，不要把那个结论外推到别的数据源）。
                        # 仍不能写 `t.get('net_amount', 0)/1e8`（None 会 TypeError），
                        # 也不能用 `or 0` 兜（会凭空输出"净流入¥0.00亿"这种假数字）。
                        _seg = f"  📊 {t.get('name','?')}({t.get('ts_code','')}) — {t.get('reason','')[:30]}"
                        _net = t.get("net_amount")
                        if _net is not None:
                            try:
                                _seg += f", 净流入¥{float(_net)/1e8:.2f}亿"
                            except (TypeError, ValueError):
                                _seg += ", 净流入金额数据异常"
                        else:
                            _seg += ", 净流入金额本次缺失"
                        lines.append(_seg)
                    # v9.5.101: 进一步看席位类型（机构 vs 游资）
                    try:
                        from services.tushare_data import get_top_inst
                        for t in hit_top[:3]:
                            ts_code = t.get("ts_code", "")
                            insts = get_top_inst(code=ts_code) or []
                            if insts:
                                # 区分机构席位（含\"机构\"或\"基金\"）vs 游资营业部
                                inst_buy = sum((i.get("net_buy") or 0) for i in insts if "机构" in (i.get("exalter") or "") and (i.get("net_buy") or 0) > 0)
                                inst_sell = sum((i.get("net_buy") or 0) for i in insts if "机构" in (i.get("exalter") or "") and (i.get("net_buy") or 0) < 0)
                                hf_net = sum((i.get("net_buy") or 0) for i in insts if "机构" not in (i.get("exalter") or ""))
                                role_lines = []
                                if inst_buy > 0:
                                    role_lines.append(f"机构净买¥{inst_buy/1e8:.2f}亿")
                                if inst_sell < 0:
                                    role_lines.append(f"机构净卖¥{abs(inst_sell)/1e8:.2f}亿")
                                if abs(hf_net) > 1e7:
                                    sign = "游资净" + ("买" if hf_net > 0 else "卖")
                                    role_lines.append(f"{sign}¥{abs(hf_net)/1e8:.2f}亿")
                                if role_lines:
                                    lines.append(f"     席位明细：{' / '.join(role_lines)}")
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        pass

    # 5.6 v9.5.99: 宏观背景（PMI / CPI / 沪深300估值）— 进程内缓存 1h，避免每次都拉
    try:
        from services.tushare_data import get_macro_pmi, get_macro_cpi, get_index_dailybasic
        import time as _t
        global _MACRO_CTX_CACHE
        try:
            cache = _MACRO_CTX_CACHE
        except NameError:
            cache = None
        now_ts = _t.time()
        if cache and (now_ts - cache.get("ts", 0)) < 3600:
            macro_lines = cache.get("lines", [])
        else:
            macro_lines = []
            try:
                pmi_rows = get_macro_pmi(months=2)
                if pmi_rows:
                    latest = pmi_rows[0]
                    pmi_val = latest.get("pmi010000")
                    if pmi_val is not None:
                        flag = "⬆扩张" if pmi_val > 50 else "⬇收缩"
                        macro_lines.append(f"  📊 制造业PMI {pmi_val}（{flag}，{latest.get('month','?')}）")
            except Exception:
                pass
            try:
                cpi_rows = get_macro_cpi(months=2)
                if cpi_rows:
                    latest = cpi_rows[0]
                    cpi_yoy = latest.get("nt_yoy")
                    if cpi_yoy is not None:
                        macro_lines.append(f"  💹 CPI同比 {cpi_yoy}%（{latest.get('month','?')}）")
            except Exception:
                pass
            try:
                idx_rows = get_index_dailybasic(ts_code="000300.SH", days=5)
                if idx_rows:
                    latest = idx_rows[0]
                    pe = latest.get("pe_ttm")
                    pb = latest.get("pb")
                    dv = latest.get("dv_ttm")
                    parts = []
                    if pe is not None: parts.append(f"PE {pe:.1f}")
                    if pb is not None: parts.append(f"PB {pb:.2f}")
                    if dv is not None: parts.append(f"股息率 {dv:.2f}%")
                    if parts:
                        macro_lines.append(f"  📈 沪深300估值：{' · '.join(parts)}")
            except Exception:
                pass
            try:
                globals()["_MACRO_CTX_CACHE"] = {"ts": now_ts, "lines": macro_lines}
            except Exception:
                pass
        if macro_lines:
            lines.append("\n【宏观背景】")
            lines.extend(macro_lines)
    except Exception:
        pass

    # 5.7 v9.5.99: 持仓股回购信号（看好自家股票）
    try:
        from services.tushare_data import get_share_repurchase, get_holder_number
        from services.stock_monitor import load_stock_holdings
        held = load_stock_holdings(user_id) or []
        codes = [h.get("code", "") for h in held if h.get("code")][:10]
        if codes:
            repurchase_alerts = []
            for code in codes:
                try:
                    rep = get_share_repurchase(code=code, days=180) or []
                    if rep:
                        latest = rep[0]
                        amt = latest.get("amount") or 0
                        if amt > 1000000:  # 100w+ 才提
                            repurchase_alerts.append(f"💰 {code} 回购¥{amt/1e8:.2f}亿（{latest.get('ann_date','?')[:10]}）— 公司看好自家股票")
                except Exception:
                    pass
            if repurchase_alerts:
                lines.append("\n【持仓股回购】")
                for a in repurchase_alerts[:3]:
                    lines.append(f"  {a}")

            # 股东户数趋势：减少=筹码集中（看涨）
            holder_alerts = []
            for code in codes:
                try:
                    hn = get_holder_number(code) or []
                    if len(hn) >= 2:
                        cur = hn[0].get("holder_num") or 0
                        prev = hn[1].get("holder_num") or 0
                        if cur > 0 and prev > 0:
                            chg = (cur - prev) / prev * 100
                            if chg < -3:  # 减少 3% 以上
                                holder_alerts.append(f"🎯 {code} 股东户数 {prev}→{cur}（{chg:+.1f}%）— 筹码集中信号")
                            elif chg > 5:  # 增加 5% 以上
                                holder_alerts.append(f"⚠️ {code} 股东户数 {prev}→{cur}（{chg:+.1f}%）— 筹码分散，警惕")
                except Exception:
                    pass
            if holder_alerts:
                lines.append("\n【股东户数变化】")
                for a in holder_alerts[:3]:
                    lines.append(f"  {a}")
    except Exception:
        pass

    # 5.8 v9.5.99: 沪深股通十大成交活跃股 — 持仓股是否在北向成交活跃榜上
    # 2026-08-28 实测修正（team-lead 用真实 token 逐日回看 8/17~8/28）：
    #   ✅ 每日有值: trade_date, ts_code, name, close, change, rank, market_type, amount(成交额,元)
    #   ❌ 恒为 None: net_amount, buy, sell
    # 也就是说「成交额仍每日披露，净买入不再披露」这条规律在【个股层面同样成立】，
    # 不只是市场层面。榜单本身每日更新（不陈旧、不是季度数据），但方向性字段已废。
    #
    # 旧代码 `net = r.get("net_amount", 0) or 0` 的后果不是崩溃而是更糟：
    # None 被 `or 0` 兜成 0 → 每只股票都输出「❄️ 净流出¥0.00亿」，即凭空编造出
    # 一个"全部净流出"的假信号喂给 LLM。所以这里改用真实可得的 amount/rank/close，
    # 并显式声明"成交活跃 ≠ 持仓 ≠ 加仓"。
    try:
        from services.tushare_data import get_hsgt_top10
        from services.stock_monitor import load_stock_holdings
        held = load_stock_holdings(user_id) or []
        held_codes = set(h.get("code", "")[:6] for h in held if h.get("code"))
        if held_codes:
            hsgt_alerts = []
            try:
                # 沪+深股通各取前10
                for mt in ["1", "3"]:
                    rows = get_hsgt_top10(market_type=mt) or []
                    for r in rows[:10]:
                        ts_code = r.get("ts_code", "")
                        code6 = ts_code.split(".")[0] if ts_code else ""
                        if code6 not in held_codes:
                            continue
                        board = "沪股通" if mt == "1" else "深股通"
                        seg = [f"📊 {r.get('name', '?')}({code6}) 在{board}成交活跃榜"]
                        # rank：排名（实测有值），显式判空避免 None 拼进文案
                        _rank = r.get("rank")
                        if _rank is not None:
                            seg.append(f"第{_rank}名")
                        # amount：北向成交额，单位【元】→ 亿元（实测有值，是这段唯一的真数据）
                        _amt = r.get("amount")
                        if _amt is not None:
                            try:
                                seg.append(f"成交额{float(_amt) / 1e8:.2f}亿元")
                            except (TypeError, ValueError):
                                pass
                        # close：收盘价（实测有值）
                        _close = r.get("close")
                        if _close is not None:
                            try:
                                seg.append(f"收盘{float(_close):.2f}元")
                            except (TypeError, ValueError):
                                pass
                        # change：涨跌【额】，单位元 —— 不是百分比！
                        # 2026-08-28 交叉验证：hsgt_top10.change=0.65 == daily.change=0.65，
                        # 而同日 daily.pct_chg=2.4083、pre_close+change=26.99+0.65=27.64=close。
                        # 若误当百分比渲染会显示"涨跌0.65%"而真实涨幅+2.41%，差近4倍且看起来合理。
                        # 所以这里必须带「元」字，禁止改成 %。
                        _chg = r.get("change")
                        if _chg is not None:
                            try:
                                seg.append(f"较前收盘{float(_chg):+.2f}元")
                            except (TypeError, ValueError):
                                pass
                        hsgt_alerts.append("，".join(seg))
            except Exception as _e_top:
                print(f"[MARKET_CTX] hsgt_top10 rows parse failed: "
                      f"{type(_e_top).__name__}: {_e_top}")
            if hsgt_alerts:
                lines.append("\n【沪深股通十大成交活跃股·你的持仓在榜】")
                for a in hsgt_alerts[:3]:
                    lines.append(f"  {a}")
                lines.append("  注：该榜按【北向成交额】排名（买入+卖出双边合计），只反映这只股票的"
                             "北向交投活跃度，**不含方向**。个股净买入/买入额/卖出额（net_amount/"
                             "buy/sell）交易所已停止披露、实测返回空值，因此无法判断外资是在买还是在卖。"
                             "上榜≠外资持仓、≠外资加仓，请勿据此推断加仓/减仓或外资看多看空。")
    except Exception as e:
        print(f"[MARKET_CTX] hsgt_top10 injection failed, section skipped: "
              f"{type(e).__name__}: {e}")

    # 6. 历史决策记忆（pending_insights + ironies）— 让 AI 知道你说过什么
    if user_id and user_id != "default":
        try:
            from domain.services.user_preference_service import get_pending_insights, get_ironies
            insights = get_pending_insights(user_id)
            # 只取最近3条 chat_extract 类型的记忆
            chat_memories = [i for i in insights if i.get("type") == "chat_extract"][-3:]
            if chat_memories:
                lines.append("\n【你的历史偏好/决策（AI 记忆）】")
                for m in chat_memories:
                    lines.append(f"  · {m.get('text', '')}")
        except Exception:
            pass
        try:
            from domain.services.user_preference_service import get_ironies
            ironies = get_ironies(user_id)
            if ironies:
                lines.append("\n【你的自定义投资铁律】")
                for rule in ironies[:5]:
                    lines.append(f"  · {rule.get('content', rule) if isinstance(rule, dict) else rule}")
        except Exception:
            pass

    # 7. 今日晨报结论 + 上周复盘摘要（让 AI 知道"昨天的判断是什么"）
    if user_id and user_id != "default":
        try:
            import json as _json
            from pathlib import Path as _Path
            from datetime import date as _date
            _data_dir = _Path(os.environ.get("DATA_DIR", "data"))

            # 今日晨报（先找 user_id，找不到找 default）
            _today = _date.today().strftime("%Y%m%d")
            for _bfname in [f"{user_id}_{_today}.json", f"default_{_today}.json"]:
                _bfp = _data_dir / "briefings" / _bfname
                if _bfp.exists():
                    _bf = _json.loads(_bfp.read_text(encoding="utf-8"))
                    _regime = _bf.get("regime_description", "")
                    _one_line = _bf.get("one_line", "")
                    _risk = _bf.get("risk_level", "")
                    if _one_line or _regime:
                        lines.append("\n【今日晨报结论】")
                        if _one_line:
                            lines.append(f"  一句话：{_one_line}")
                        if _regime:
                            lines.append(f"  市场机制：{_regime}")
                        if _risk and _risk != "normal":
                            lines.append(f"  风险等级：{_risk}")
                    break

            # 最近一期周报摘要
            _rpt_dir = _data_dir / user_id / "reports"
            if _rpt_dir.exists():
                _weekly_files = sorted(_rpt_dir.glob("week_*.json"), reverse=True)
                if _weekly_files:
                    _wr = _json.loads(_weekly_files[0].read_text(encoding="utf-8"))
                    _summary = _wr.get("summary", "") or _wr.get("one_line", "")
                    _period = _wr.get("period", "") or _wr.get("week_start", "")
                    if _summary:
                        lines.append(f"\n【上期周报摘要（{_period}）】")
                        lines.append(f"  {str(_summary)[:200]}")
        except Exception:
            pass

    # v9.5.95: 持仓基金"贵不贵"检查 — 把净值历史百分位/规模/经理换届注入 AI 上下文
    if user_id and user_id != "default":
        try:
            from services.fund_monitor import load_fund_holdings
            from api.signals import _get_fund_nav_percentile
            my_funds = load_fund_holdings(user_id) or []
            if my_funds:
                high_pos = []      # 净值在历史高位
                low_pos = []       # 净值在历史低位
                manager_warns = [] # 经理换届预警
                for mf in my_funds[:10]:  # 最多查 10 只，避免超时
                    code = mf.get("code", "")
                    name = mf.get("name", code)[:14]
                    try:
                        info = _get_fund_nav_percentile(code) or {}
                        pct = info.get("nav_pct")
                        if pct is not None:
                            if pct >= 80:
                                high_pos.append(f"{name}({pct}%)")
                            elif pct <= 30:
                                low_pos.append(f"{name}({pct}%)")
                    except Exception:
                        pass
                    try:
                        from api.fund_detail import _get_fund_manager_change
                        mgr = _get_fund_manager_change(code) or {}
                        if mgr.get("has_change"):
                            manager_warns.append(f"{name}（{mgr.get('manager_name','新任')}近{mgr.get('days_since','?')}天上任）")
                    except Exception:
                        pass

                if high_pos or low_pos or manager_warns:
                    lines.append("\n【持仓基金估值与人事】")
                    if high_pos:
                        lines.append(f"  📈 历史高位（≥80%分位）：{', '.join(high_pos)} — 进一步加仓需谨慎")
                    if low_pos:
                        lines.append(f"  📉 历史低位（≤30%分位）：{', '.join(low_pos)} — 可优先加仓")
                    if manager_warns:
                        lines.append(f"  ⚠️ 基金经理近6月换届：{', '.join(manager_warns)} — 历史业绩参考价值降低")
        except Exception:
            pass

    # v9.5.95: 再平衡缺口（如果前端有传 rebalanceGap 字段就用，否则用后端理想配置算）
    if p and getattr(p, 'rebalanceGap', None):
        try:
            rg = p.rebalanceGap if isinstance(p.rebalanceGap, dict) else {}
            gaps = rg.get('gaps', [])
            surplus = rg.get('surplus', [])
            if gaps or surplus:
                lines.append("\n【再平衡缺口】")
                lines.append(f"  当前总投入：¥{rg.get('totalInvest', 0):,}（偏离阈值 ±{rg.get('threshold', 5)}%）")
                if gaps:
                    gap_text = '、'.join([f"{g.get('label','?')}缺¥{g.get('need',0):,}" for g in gaps[:3]])
                    lines.append(f"  ⬇ 欠配方向：{gap_text} — 用户问「该买什么」时建议优先补这些方向")
                if surplus:
                    surplus_text = '、'.join([f"{s.get('label','?')}超¥{s.get('excess',0):,}" for s in surplus[:3]])
                    lines.append(f"  ⬆ 超配方向：{surplus_text} — 暂不加仓")
        except Exception:
            pass

    result = "\n".join(lines) if lines else "用户尚未建仓。"
    # v9.5.122: 写 per-user 文件缓存
    try:
        os.makedirs(_PORTFOLIO_CTX_DIR, exist_ok=True)
        with open(ctx_file, "w", encoding="utf-8") as f:
            f.write(result)
    except Exception:
        pass
    return result


# ========================================================
# System Prompt 加载与构建
# ========================================================

_system_prompt_template = ""

def _load_prompt_template():
    global _system_prompt_template
    if not _system_prompt_template:
        p = Path(__file__).parent.parent / "prompts" / "system_prompt.md"
        if p.exists():
            _system_prompt_template = p.read_text(encoding="utf-8")
        else:
            _system_prompt_template = "你是钱袋子AI投顾，基于真实数据分析，不编造数字。"
    return _system_prompt_template


def _build_system_prompt(market_ctx: str, portfolio_ctx: str) -> str:
    """统一构建 DeepSeek system prompt"""
    template = _load_prompt_template()
    return f"""{template}

## 实时市场数据
{market_ctx}

## 用户持仓与风控
{portfolio_ctx}"""


# ========================================================
# 聊天意图分类
# ========================================================

_INTENT_RULES = [
    # 安全拒绝（最高优先级）
    (["目标价", "满仓", "全仓", "梭哈", "稳赚", "保本", "借钱炒股", "贷款炒股"], "safety_refusal", None),
    # 用户持仓查询
    (["我持有", "我有什么", "我的持仓", "我的资产", "是不是持有", "现在还在", "我当前", "我有没有", "持有什么", "有什么基金", "有什么股票", "我的基金", "我的股票", "我买了什么", "主账号持有", "老婆持有", "老公持有"], "holdings_query", None),
    (["入场", "时机", "现在适合买", "适合买", "该买吗", "能买吗", "进场", "能进场", "抄底", "适合入"], "timing", "/api/timing"),
    (["定投", "DCA", "每月投", "定投多少", "怎么投", "投多少"], "smart_dca", "/api/smart-dca"),
    (["止盈", "止损", "该卖吗", "减仓", "该出", "锁定利润"], "take_profit", None),
    (["持仓分析", "诊断", "体检", "检查持仓"], "portfolio_doctor", "/api/portfolio-doctor/diagnose"),
    (["配置建议", "资产配置", "怎么分配"], "allocation", None),
    (["新闻", "今天发生", "消息面", "利空", "利好", "什么情况"], "news", None),
    (["宏观", "GDP", "CPI", "利率", "经济", "PMI", "通胀", "M2"], "macro", None),
    (["估值", "PE", "PB", "贵不贵"], "valuation", None),
    (["基金", "选基", "推荐基金"], "fund", None),
    (["北向", "外资", "净流入"], "northbound", None),
    (["情绪", "恐惧", "贪婪", "恐慌", "市场情绪", "散户情绪"], "sentiment", None),
    # 晨报/周报请求（引导到对应功能）
    (["晨报", "早报", "briefing"], "briefing_request", None),
    (["周报", "weekly", "本周总结"], "weekly_request", None),
    # 现金/应急储备
    (["安全垫", "应急", "现金够", "留多少现金", "备用金"], "cash_safety", None),
    # v9.5.123 P2-3: 操作指令(设目标/调纪律线/设止盈止损)
    (["设定目标", "设个目标", "财务目标", "设目标", "攒够", "攒到", "存够", "万的目标", "万目标"], "operation_goal", "/api/goals/set"),
    (["止盈线", "止损线", "纪律线", "设止盈", "设止损", "调止盈", "调止损"], "operation_discipline", "/api/fund-holdings/discipline"),
]


def classify_chat_intent(msg: str) -> dict:
    """规则引擎意图分类（不调 LLM，毫秒级）

    增加否定约束检测：如果消息包含"不要/别给/不需要"+ 关键词，不触发对应意图
    """
    msg_lower = msg.lower()

    # 否定模式：如果用户说"不要买卖建议"，不应触发 take_profit
    _NEGATION_PATTERNS = ["不要", "别给", "不需要", "不用", "不想要", "禁止"]
    has_negation = any(neg in msg_lower for neg in _NEGATION_PATTERNS)

    for keywords, intent, api in _INTENT_RULES:
        for kw in keywords:
            if kw in msg_lower:
                # 如果带否定词且关键词紧跟否定词，跳过
                if has_negation:
                    for neg in _NEGATION_PATTERNS:
                        if neg in msg_lower:
                            neg_pos = msg_lower.index(neg)
                            kw_pos = msg_lower.index(kw)
                            # 否定词在关键词前面20字以内，认为是否定
                            if 0 <= kw_pos - neg_pos <= 20:
                                break
                    else:
                        return {"intent": intent, "keyword": kw, "api": api}
                    continue  # 被否定了，跳过这个意图
                return {"intent": intent, "keyword": kw, "api": api}
    return {"intent": "general", "keyword": None, "api": None}


# ========================================================
# 规则引擎降级回答
# ========================================================

def _rule_based_reply_structured(msg: str, market_ctx: str, portfolio_ctx: str) -> dict | None:
    """规则引擎结构化回答 — 命中返回 {text, confidence, intent}，不命中返回 None。

    confidence=0.85 表示规则精准匹配（用真实数据计算），比 LLM 编造更可靠。
    """
    msg_lower = msg.lower()

    # ★ 最高优先级1：安全硬拒绝（目标价/满仓/稳赚/借钱炒股）
    _SAFETY_KEYWORDS = ["目标价", "明天涨", "明天跌", "预测价格", "涨到多少",
                        "满仓", "全仓", "梭哈", "稳赚", "保本", "确定赚",
                        "借钱炒股", "借钱投资", "贷款炒股", "杠杆炒股"]
    if any(k in msg_lower for k in _SAFETY_KEYWORDS):
        text = "🚫 我不能预测具体价格，也不能建议满仓、借钱投资或承诺保本收益。\n\n可以帮你做的：\n• 基于当前持仓做风险检查\n• 分析估值是否偏高\n• 给出仓位建议（但不是满仓）\n\n⚠️ 投资有风险，入市需谨慎。"
        return {"text": text, "confidence": 0.95, "intent": "safety_refusal"}

    # ★ v9.5.123: 操作指令(设目标/纪律线) — 识别后引导用户提供参数
    _GOAL_KW = ["设定目标", "设个目标", "财务目标", "设目标", "攒够", "攒到", "存够", "万的目标", "万目标"]
    _DISC_KW = ["止盈线", "止损线", "纪律线", "设止盈", "设止损", "调止盈", "调止损"]
    if any(k in msg_lower for k in _GOAL_KW):
        # 尝试从消息里提取参数
        import re as _re_op
        amount_m = _re_op.search(r'(\d+)\s*[万w]', msg_lower)
        text = "🎯 设定财务目标\n\n"
        if amount_m:
            amt = int(amount_m.group(1)) * 10000
            text += f"已识别目标金额: ¥{amt:,.0f}\n\n"
            text += '请补充以下信息（直接回复）：\n• 目标名称（如"装修费"）\n• 预计截止日期（如"2028年底"）\n• 每月可存入金额（如"5000元"）'
        else:
            text += '请告诉我：\n• 目标金额（如"30万"）\n• 目标名称（如"装修费"）\n• 截止日期 + 每月存入\n\n示例：帮我设一个30万装修目标，每月存5000，2028年底前'
        return {"text": text, "confidence": 0.85, "intent": "operation_goal"}
    
    if any(k in msg_lower for k in _DISC_KW):
        import re as _re_op2
        pct_m = _re_op2.search(r'(\d+)\s*%', msg_lower)
        text = "🎯 设定纪律线\n\n"
        if pct_m:
            pct = int(pct_m.group(1))
            is_tp = any(k in msg_lower for k in ["止盈", "设止盈", "调止盈"])
            line_type = "止盈" if is_tp else "止损"
            text += f"已识别: {line_type}线 {pct}%\n\n"
            text += '请指定基金代码或名称(如沪深300/005827)，我就帮你设好。到达纪律线时会通过企微推送提醒你执行。'
        else:
            text += '请告诉我：\n* 哪只基金(代码或名称)\n* 止盈线百分比(如+30%)\n* 止损线百分比(如-20%)\n\n示例：帮我给沪深300设止盈30%止损-20%'
        return {"text": text, "confidence": 0.85, "intent": "operation_discipline"}

    # ★ 最高优先级2：用户持仓/资产查询（必须基于真实数据回答）
    # 排除：问"老婆/家人"的持仓 — 直接规则拒绝
    _OTHER_PERSON_KW = ["老婆", "老公", "家人", "她的", "他的", "对方", "主账号", "另一个账号"]
    _asking_about_others = any(k in msg_lower for k in _OTHER_PERSON_KW)
    if _asking_about_others and any(k in msg_lower for k in ["持有", "资产", "持仓", "买了", "有什么"]):
        text = "🔒 当前钱袋子系统只能查看**你自己账号**的数据，无法读取其他家庭成员的持仓。\n\n如果想查看对方的资产，需要切换到对方的账号登录。\n\n⚠️ 账号之间数据完全隔离，互不可见。"
        return {"text": text, "confidence": 0.90, "intent": "cross_account_refusal"}

    _HOLDING_QUERY_KW = ["我有什么", "我的持仓", "我的资产",
                          "现在还在", "我刚才", "录入的", "我当前",
                          "我现在有", "净资产", "我有多少", "持有什么", "有什么基金",
                          "有什么股票", "我的基金", "我的股票", "有哪些持仓",
                          "我买了什么", "我买了哪些"]
    # 含有决策意图的持仓问题 → 必须交给 LLM 做分析（规则引擎只能列持仓，不能给建议）
    _DECISION_KW = ["定投", "卖出", "卖掉", "减仓", "加仓", "止盈", "止损",
                    "继续", "要不要", "该不该", "值得", "怎么办", "如何操作",
                    "换仓", "调仓", "赎回", "申购", "对不对", "合不合适"]
    _has_decision_intent = any(k in msg_lower for k in _DECISION_KW)
    if _has_decision_intent:
        return None  # 含决策意图，必须走 LLM 做个性化分析

    # "我持有X吗/我有没有X/是不是持有X" + 具体标的名 → 交给LLM精准回答
    _SPECIFIC_QUERY_KW = ["我有没有", "是不是持有"]
    _is_specific_query = any(k in msg_lower for k in _SPECIFIC_QUERY_KW)
    # "我持有" + "吗" = 问具体标的，也交给LLM
    if "我持有" in msg_lower and "吗" in msg_lower:
        _is_specific_query = True
    # 纯 "我持有" 无 "吗" = 问全部持仓 → 走规则返回列表
    if "我持有" in msg_lower and "吗" not in msg_lower:
        _HOLDING_QUERY_KW.append("我持有")
    if not _asking_about_others and not _is_specific_query and any(k in msg_lower for k in _HOLDING_QUERY_KW):
        # 从 portfolio_ctx 中提取真实持仓信息
        if "没有任何持仓" in portfolio_ctx or "没有持仓" in portfolio_ctx or "尚未录入" in portfolio_ctx:
            text = "**结论：** 当前钱袋子系统没有记录到持仓/资产数据。\n\n**依据：** 股票持仓、基金持仓、手动资产均为空。\n\n**建议：** 去 持仓页 或 资产页 添加你的真实持仓，我就能给你个性化分析了。\n\n⚠️ 仅基于钱袋子系统记录。"
            return {"text": text, "confidence": 0.90, "intent": "empty_holdings_query"}
        elif "持仓明细" in portfolio_ctx:
            # 有持仓数据，把上下文中的持仓信息提取出来
            import re
            holdings_lines = re.findall(r'- (?:股票|基金)：(.+)', portfolio_ctx)
            nw_line = re.search(r'净资产：¥([\d,.]+)', portfolio_ctx)
            parts = ["**结论：** 当前钱袋子系统记录如下：\n"]
            if nw_line:
                parts.append(f"💰 净资产：¥{nw_line.group(1)}")
            if holdings_lines:
                parts.append("\n**持仓明细：**")
                for h in holdings_lines[:5]:
                    parts.append(f"  • {h}")
            if len(parts) > 1:
                parts.append("\n**数据来源：** 钱袋子持仓记录 + 资产记录")
                parts.append("\n⚠️ 以上仅基于系统记录，不包含未同步的券商/银行账户。")
                text = "\n".join(parts)
                return {"text": text, "confidence": 0.90, "intent": "holdings_query"}
        # 没提取到有用信息，交给 LLM
        pass

    # 入场时机
    if any(k in msg_lower for k in ["什么时候买", "入手", "入场", "时机", "现在能买", "适合买", "抄底", "能进场"]):
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
        text = f"📊 入场时机分析：\n\n{tip}\n\n{val['index']}估值百分位：{val['percentile']}%（{val['level']}）\n恐惧贪婪指数：{fgi:.0f}\n\n💡 建议：不管时机好坏，定投永远是对的。定投的精髓就是穿越牛熊，低估时多买、高估时少买。\n\n⚠️ 以上仅供参考，不构成投资建议。"
        return {"text": text, "confidence": 0.85, "intent": "timing"}

    # 止盈止损
    if any(k in msg_lower for k in ["卖", "止盈", "止损", "价位", "该出", "什么时候出", "锁定利润", "减仓", "到了多少"]):
        text = "🔔 止盈止损策略：\n\n钱袋子采用**分批止盈法**，根据你的风险类型自动设定目标：\n\n🐢 保守型：+15% 止盈 / -8% 止损\n🐰 稳健型：+20% 止盈 / -10% 止损\n🦊 平衡型：+30% 止盈 / -15% 止损\n🦁 进取型：+50% 止盈 / -20% 止损\n🦅 激进型：+80% 止盈 / -25% 止损\n\n📌 操作建议：\n1️⃣ **到了止盈线，不用全卖** — 卖 1/3 锁利润，剩余继续持有\n2️⃣ **到了止损线，先看原因** — 如果基金基本面没变，可能反而是加仓机会\n3️⃣ **不设绝对卖点** — 结合估值百分位综合判断\n\n你可以在首页的 AI 信号里实时看到自己的止盈止损状态 📊\n\n⚠️ 以上仅供参考，不构成投资建议。"
        return {"text": text, "confidence": 0.85, "intent": "take_profit"}

    # 智能定投
    if any(k in msg_lower for k in ["定投", "智能", "固定还是", "怎么投", "投多少", "每月投", "dca"]):
        val = get_valuation_percentile()
        smart = calc_smart_dca(1000, val["percentile"])
        text = f"🧠 智能定投 vs 固定定投：\n\n**固定定投**：每月投相同金额，简单省心，长期有效。\n**智能定投**：根据市场估值动态调整 — 低估多买、高估少买。\n\n钱袋子的智能定投策略：\n\n| 估值百分位 | 倍率 | 说明 |\n|-----------|------|------|\n| < 20% | 1.5x | 极度低估，多买 |\n| 20-30% | 1.3x | 低估，适当多买 |\n| 30-50% | 1.1x | 偏低，略多 |\n| 50-70% | 1.0x | 正常，标准额 |\n| 70-85% | 0.7x | 偏高，少买 |\n| > 85% | 0.3x | 高估，大幅减少 |\n\n📊 当前{val['index']}估值：{val['percentile']}%（{val['level']}）\n💡 建议本月倍率：{smart['multiplier']}x — {smart['advice']}\n\n智能定投比固定定投长期多赚约 15-20%，但需要坚持 3 年以上才能看到效果。\n\n⚠️ 以上仅供参考，不构成投资建议。"
        return {"text": text, "confidence": 0.85, "intent": "dca"}

    # 市场情绪 / 恐惧贪婪
    if any(k in msg_lower for k in ["情绪", "恐惧", "贪婪", "恐慌", "fgi", "市场情绪", "散户情绪"]):
        fgi_data = get_fear_greed_index()
        fgi = fgi_data["score"]
        if fgi < 25:
            level = "极度恐惧 😱"
            advice = "历史上极度恐惧时买入，半年后大概率盈利。"
        elif fgi < 40:
            level = "恐惧 😰"
            advice = "市场悲观情绪浓，适合逆向加仓。"
        elif fgi < 60:
            level = "中性 😐"
            advice = "情绪平稳，按计划操作即可。"
        elif fgi < 75:
            level = "贪婪 😊"
            advice = "市场乐观，注意控制仓位。"
        else:
            level = "极度贪婪 🤑"
            advice = "市场过热，考虑适当减仓锁利。"
        text = f"🎭 市场情绪分析：\n\n恐惧贪婪指数：**{fgi:.0f}** — {level}\n\n{advice}\n\n{market_ctx}\n\n💡 「别人恐惧时我贪婪」说的容易做起来难，但数据不会骗人。\n\n⚠️ 以上仅供参考，不构成投资建议。"
        return {"text": text, "confidence": 0.85, "intent": "sentiment"}

    # 北向资金 / 外资 —— 净流入【永久不可得】，给确定性回答
    # 2024-08-19 起沪深交易所停止披露北向日频净买入（改为按季度公布），Tushare
    # moneyflow_hsgt 的 north_money 现为当日成交额。这是永久性数据边界，不应该依赖
    # 「模型会不会照着 prompt 里的说明回答」这种软约束（换模型/改温度就可能失效），
    # 所以在规则层直接答死；顺带省掉一次 LLM 调用（用户会反复追问这个问题）。
    # 必须放在「宏观/新闻」分支之前，否则「外资为什么流出」会被新闻分支抢走。
    if any(k in msg_lower for k in ["北向", "外资", "陆股通", "沪股通", "深股通"]):
        nb: dict = {}
        try:
            nb = get_northbound_flow() or {}
        except Exception as _e_nb:
            print(f"[RULES] northbound fetch failed: {type(_e_nb).__name__}: {_e_nb}")
        _nb_reason = (nb.get("unavailable_reason")
                      or "2024-08-19 起沪深交易所停止披露北向日频净买入，改为按季度公布")
        _nb_t = nb.get("turnover_today")
        _nb_dd = str(nb.get("data_date") or "")
        _nb_label = ""
        if len(_nb_dd) == 8 and _nb_dd.isdigit():
            _nb_label = f"（{int(_nb_dd[4:6])}/{int(_nb_dd[6:8])}）"

        _nb_rows = []
        if _nb_t is not None:
            try:
                _nb_rows.append(f"• 当日成交额：{float(_nb_t):.0f}亿元{_nb_label}")
            except (TypeError, ValueError):
                pass
        for _key, _cn in (("turnover_avg_5d", "近5日日均"), ("turnover_avg_20d", "近20日日均")):
            _v = nb.get(_key)
            if _v is None:
                continue
            try:
                _nb_rows.append(f"• {_cn}：{float(_v):.0f}亿元")
            except (TypeError, ValueError):
                pass
        if nb.get("turnover_trend"):
            _nb_rows.append(f"• 交投活跃度：{nb['turnover_trend']}（近5日相对近20日）")

        # ⚠️ 措辞约束（2026-08，配合 infra/llm/red_team_audit.py 的北向拦截规则）：
        # 守卫正则 `(北向|外资)[^。；\n]{0,15}(净流入|净流出|净买入|净卖出)` 的放行条件是
        # 【匹配之后 25 字内】出现 不可得/停止披露/按季度 等词。它只向后看，不向前看，
        # 所以"引用式否定"（「外资净流入X亿」这种说法不可信）会被误判为幻觉断言。
        # 因此下面每处出现「外资+净流入」的地方，都必须在同一句、紧跟其后带上
        # 「已停止披露」之类的字样，否则我们自己的诚实文案会被守卫拦掉。
        text = ("🌏 北向资金（外资）\n\n"
                "先说结论：**「外资今天净流入多少亿」这个数字已停止披露，现在看不到了。**\n\n"
                f"{_nb_reason}。所以任何声称「外资今日净流入X亿」的数字都不可信"
                "（该数据已停止披露），要么用的是旧数据，要么是拿成交额硬算出来的。\n\n")
        if _nb_rows:
            text += ("📊 现在能看到的是北向成交额（买入金额+卖出金额的合计）：\n"
                     + "\n".join(_nb_rows)
                     + "\n\n🔍 怎么理解：\n"
                       "• 放量 = 外资交投更活跃，调仓和分歧都在增多\n"
                       "• 缩量 = 外资参与度下降，观望为主\n"
                       "• 成交额是买卖双边合计，**不含方向** —— 它只能说明外资忙不忙，"
                       "不能说明外资在买还是在卖\n\n")
        else:
            text += "📊 北向成交额本次也未取到（数据源暂时无返回），所以这轮没有可用的北向数据。\n\n"
        text += ("💡 想看外资真实的净买入方向，只能等交易所的季度披露。\n\n"
                 "⚠️ 以上仅供参考，不构成投资建议。")
        return {"text": text, "confidence": 0.85, "intent": "northbound"}

    # 宏观经济
    if any(k in msg_lower for k in ["宏观", "经济", "cpi", "pmi", "通胀", "利率", "货币", "m2", "gdp"]):
        events = get_macro_calendar()
        macro_text = "\n".join([f"{e['icon']} {e['name']}：{e['value']}（{e['date']}）\n  └ {e['impact']}" for e in events])
        text = f"🏛️ 宏观经济数据：\n\n{macro_text}\n\n💡 宏观数据影响市场整体方向。CPI低+PMI>50+M2宽松 = 对股市友好的环境。\n\n⚠️ 以上仅供参考，不构成投资建议。"
        return {"text": text, "confidence": 0.85, "intent": "macro_summary"}

    # 新闻/资讯
    if any(k in msg_lower for k in ["新闻", "资讯", "消息", "发生", "怎么了", "什么情况", "为什么", "利空", "利好"]):
        # 检查是否有个股实体（如果问"茅台有什么利空"，只返回茅台相关新闻）
        _STOCK_NAMES = {"茅台": "600519", "宁德": "300750", "比亚迪": "002594",
                        "腾讯": "00700", "阿里": "09988", "中兴": "000063",
                        "平安": "601318", "招商": "600036", "格力": "000651"}
        entity_name = None
        entity_code = None
        for name, code in _STOCK_NAMES.items():
            if name in msg:
                entity_name = name
                entity_code = code
                break
        # 如果没匹配到常见股票名，尝试从用户持仓匹配
        if not entity_name and portfolio_ctx:
            import re
            # 从 portfolio_ctx 里提取股票名
            stock_matches = re.findall(r'股票：(.+?)\((\d{6})\)', portfolio_ctx)
            for sname, scode in stock_matches:
                if sname[:2] in msg or sname in msg:
                    entity_name = sname
                    entity_code = scode
                    break

        if entity_name:
            # 个股新闻查询：只返回该股票相关的
            try:
                from services.news_data import get_stock_news_by_code
                stock_news = get_stock_news_by_code(entity_code, limit=5)

                # 判断用户是否在问特定方向（利空/利好）
                asking_bearish = any(k in msg_lower for k in ["利空", "坏消息", "负面", "风险", "暴雷"])
                asking_bullish = any(k in msg_lower for k in ["利好", "好消息", "正面"])

                if stock_news:
                    # 对每条新闻做简单情感标注
                    _BULL_KW = ["上涨", "涨价", "增长", "利润", "突破", "新高", "利好", "上调", "买入"]
                    _BEAR_KW = ["下跌", "暴跌", "亏损", "减持", "处罚", "退市", "利空", "下调", "卖出", "刑拘"]
                    tagged_news = []
                    bull_count = 0
                    bear_count = 0
                    for n in stock_news[:5]:
                        title = n.get("title", "")
                        if any(k in title for k in _BEAR_KW):
                            tag = "🔴"
                            bear_count += 1
                        elif any(k in title for k in _BULL_KW):
                            tag = "🟢"
                            bull_count += 1
                        else:
                            tag = "⚪"
                        tagged_news.append(f"{tag} {title}（{n.get('source', '')}）")

                    news_text = "\n".join(tagged_news)

                    # 根据用户问题方向给出结论
                    if asking_bearish:
                        if bear_count > 0:
                            conclusion = f"**结论：** 检索到 {bear_count} 条可能偏负面的消息，建议关注但不必恐慌。"
                        else:
                            conclusion = f"**结论：** 当前检索到的 {len(stock_news)} 条{entity_name}相关新闻中，**未发现明确的重大利空**。"
                    elif asking_bullish:
                        if bull_count > 0:
                            conclusion = f"**结论：** 检索到 {bull_count} 条偏正面的消息。"
                        else:
                            conclusion = f"**结论：** 当前新闻中未发现明确利好信号。"
                    else:
                        conclusion = f"**结论：** 检索到 {len(stock_news)} 条{entity_name}相关消息（🟢利好{bull_count} 🔴利空{bear_count} ⚪中性{len(stock_news)-bull_count-bear_count}）。"

                    text = f"{conclusion}\n\n**近期消息：**\n{news_text}\n\n📌 数据来源：东方财富新闻 · 已过滤无关内容 · 情感标注仅供参考\n⚠️ 单条新闻不应直接触发买卖决策，请以公告和权威来源为准。"
                else:
                    if asking_bearish:
                        text = f"**结论：** 当前没有检索到与{entity_name}直接相关的利空/负面新闻。\n\n📌 未检索到≠没有发生，可能是数据源延迟或信息尚未公开。\n💡 如果你听到了具体消息，可以告诉我内容，我来帮你判断真假和可能影响。\n⚠️ 以上仅供参考。"
                    else:
                        text = f"**结论：** 当前没有检索到与{entity_name}直接相关的重大新闻。\n\n📌 未检索到≠没有发生，可能是数据源延迟。\n⚠️ 以上仅供参考。"
                return {"text": text, "confidence": 0.85, "intent": "stock_news"}
            except Exception:
                pass

        # 泛市场新闻
        news = get_market_news(5)
        news_lines = []
        for n in news[:5]:
            if n.get("url"):
                news_lines.append(f'📰 [{n["title"]}]({n["url"]}) （{n["source"]}）')
            else:
                news_lines.append(f"📰 {n['title']}（{n['source']}）")
        news_text = "\n".join(news_lines)
        text = f"📰 最新市场资讯：\n\n{news_text}\n\n💡 建议：关注大趋势，不要因为单条新闻做决定。投资看的是长期逻辑。\n\n⚠️ 以上仅供参考，不构成投资建议。"
        return {"text": text, "confidence": 0.85, "intent": "news"}

    # 技术分析
    if any(k in msg_lower for k in ["技术", "rsi", "macd", "布林", "超买", "超卖", "指标"]):
        tech = get_technical_indicators()
        text = f"📊 沪深300技术指标：\n\n📈 RSI(14)：{tech['rsi']}（{tech['rsi_signal']}）\n  └ >70 超买区，<30 超卖区\n\n📉 MACD：{tech['macd']['trend']}\n  └ DIF:{tech['macd']['dif']:.4f} DEA:{tech['macd']['dea']:.4f}\n\n📐 布林带：{tech['bollinger']['position']}\n  └ 上轨:{tech['bollinger']['upper']} 中轨:{tech['bollinger']['middle']} 下轨:{tech['bollinger']['lower']}\n\n💡 技术指标是辅助参考，不能单独作为买卖依据。结合估值+基本面综合判断更靠谱。\n\n⚠️ 以上仅供参考，不构成投资建议。"
        return {"text": text, "confidence": 0.85, "intent": "technicals"}

    # 政策/地缘/影响
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
                text = f"🏛️ 当前事件对你持仓的影响分析：\n\n{impact_text}\n\n💡 建议：关注事件发展趋势，短期波动不改长期逻辑。如果你是定投模式，保持节奏即可。\n\n⚠️ 以上基于关键词匹配的初步分析，仅供参考，不构成投资建议。"
                return {"text": text, "confidence": 0.85, "intent": "macro_impact"}
        except Exception:
            pass
        return None

    # 晨报/周报请求 — 引导用户到正确功能
    if any(k in msg_lower for k in ["晨报", "早报", "briefing"]):
        text = "📋 你可以在首页查看每日晨报，或者直接访问 **分析页 → 管家晨报** 获取最新版。\n\n晨报内容包括：市场状态、持仓异动、风控提醒、今日建议。\n\n💡 晨报每天凌晨 4:30 自动生成，也可以手动刷新获取最新数据。"
        return {"text": text, "confidence": 0.80, "intent": "briefing_request"}

    if any(k in msg_lower for k in ["周报", "weekly", "本周总结"]):
        text = "📊 你可以在 **分析页 → 周报** 查看本周投资总结。\n\n周报内容包括：本周净资产变动、持仓盈亏、市场回顾、下周关注点。\n\n💡 周报每周日自动生成，也可以在分析页手动触发生成。"
        return {"text": text, "confidence": 0.80, "intent": "weekly_request"}

    # 现金安全垫/应急储备
    if any(k in msg_lower for k in ["安全垫", "应急", "现金够", "留多少现金", "备用金", "紧急备用"]):
        # 从持仓上下文提取现金信息
        import re
        cash_match = re.search(r'现金[：:]?\s*¥?([\d,.]+)', portfolio_ctx)
        if cash_match:
            cash_str = cash_match.group(1)
            text = f"💰 你当前记录的现金约 ¥{cash_str}。\n\n**安全垫建议：**\n• 保留 3-6 个月生活费作为应急储备\n• 放在 T+0 货币基金（如余额宝）\n• 不计入投资，随时可取\n\n**判断标准：**\n• 月支出 5000 → 安全垫 1.5-3 万\n• 月支出 10000 → 安全垫 3-6 万\n• 月支出 20000 → 安全垫 6-12 万\n\n如果现金不够安全垫，暂停新增高风险资产，优先攒够。\n\n⚠️ 以上仅供参考，不构成投资建议。"
        else:
            text = "💰 **安全垫建议：**\n\n保留 3-6 个月生活费作为应急储备，放在 T+0 货币基金。\n\n你目前没有录入现金数据，去 **资产页** 添加你的银行存款/余额宝金额，我就能帮你判断够不够了。\n\n⚠️ 以上仅供参考，不构成投资建议。"
        return {"text": text, "confidence": 0.85, "intent": "cash_safety"}

    # 市场下跌安慰
    if any(k in msg_lower for k in ["跌", "亏", "赔", "绿", "下跌"]):
        text = f"📉 市场波动是正常现象。\n\n{market_ctx}\n\n长期投资（3年+）能大幅平滑短期波动。如果你的资产配比还在目标范围内，建议保持定投节奏，不要恐慌卖出。记住投资铁律：跌了别卖，越跌越该买。\n\n⚠️ 以上仅供参考，不构成投资建议。"
        return {"text": text, "confidence": 0.85, "intent": "market_down"}

    # 市场上涨
    if any(k in msg_lower for k in ["涨", "赚", "红", "上涨", "牛"]):
        text = f"📈 恭喜！不过也别过于乐观。\n\n{market_ctx}\n\n赚钱时更要冷静，检查一下各资产的占比是否偏离目标太多。如果某类资产涨太多导致占比过高，可以考虑再平衡——卖掉一点涨多的，买入涨少的。\n\n⚠️ 以上仅供参考，不构成投资建议。"
        return {"text": text, "confidence": 0.85, "intent": "market_up"}

    # 不命中 → 返回 None，交给 LLM
    return None


def _rule_based_reply(msg: str, market_ctx: str, portfolio_ctx: str) -> str:
    """规则引擎降级回答（兼容旧调用方）"""
    result = _rule_based_reply_structured(msg, market_ctx, portfolio_ctx)
    if result:
        return result["text"]

    # 兜底前检查1：未知代码 — 消息含6位数字且不是已知股票
    import re
    msg_lower = msg.lower()
    code_match = re.search(r'(\d{6})', msg)
    if code_match:
        unknown_code = code_match.group(1)
        # 排除已知存在的代码（避免误判用户持仓里的合法代码）
        try:
            from services.tushare_data import validate_stock_code, validate_fund_code
            stock_check = validate_stock_code(unknown_code)
            fund_check = validate_fund_code(unknown_code)
            if stock_check.get("valid") is False and fund_check.get("valid") is False:
                return f"❓ 代码 {unknown_code} 在 A 股和公募基金数据库中均**未查到**，待核实。\n\n可能原因：\n• 代码输入有误\n• 已退市/未上市\n• 非 A 股/非公募基金标的\n\n请确认后重新提问。"
        except Exception:
            pass

    # 兜底前检查2：如果用户问的是持仓/标的相关 + portfolio为空 → 直接说无持仓
    _ASSET_HINTS = ["持有", "持仓", "资产", "还在", "删除", "茅台", "宁德", "基金", "股票", "买了"]
    if any(k in msg_lower for k in _ASSET_HINTS):
        if "没有任何持仓" in portfolio_ctx or "没有持仓" in portfolio_ctx or "尚未录入" in portfolio_ctx or "尚未建仓" in portfolio_ctx:
            return "📋 当前你的钱袋子系统中**没有持仓/资产记录**。\n\n如果之前有数据但已删除，确认已清空。\n如果是新账号，去 持仓页 或 资产页 添加数据即可。\n\n⚠️ 仅基于钱袋子系统记录。"

    # 兜底回复（不倾倒市场概况，简短引导用户提出更明确的问题）
    return "🤔 这个问题我需要更多上下文才能精准回答。\n\n你可以试试这些问法：\n📰 「最近有什么新闻？」\n📊 「技术指标怎么样？」\n🎯 「现在适合入场吗？」\n💰 「什么时候该卖？」\n🧠 「定投多少合适？」\n🔍 「茅台有什么利空？」（指定个股）\n\n或者直接告诉我你想了解的股票/基金代码，我来帮你查。\n\n⚠️ 以上仅供参考，不构成投资建议。"


# ========================================================
# OCR 处理
# ========================================================

async def _do_ocr(file_path: Path, content: bytes) -> dict:
    """执行 OCR，优先用 LLM 多模态（通过 gateway），降级用本地 OCR"""
    from services.llm_gateway import LLMGateway
    gw = LLMGateway.instance()
    vision_model = os.environ.get("LLM_VISION_MODEL", "gpt-4o-mini")

    try:
        import base64
        b64 = base64.b64encode(content).decode()
        mime = "image/jpeg"
        if str(file_path).endswith(".png"):
            mime = "image/png"

        messages = [
            {"role": "system", "content": """你是一个金融记录识别助手。请识别截图类型并提取信息。

支持的截图类型：
1. 支付宝/微信消费记录 → 提取: 金额(amount), 商家(merchant), 分类(category:餐饮/交通/购物/娱乐/医疗/教育/其他), 备注(note)
2. 支付宝/微信账单列表 → 提取: 多条记录records[{amount, merchant, date}]
3. 银行卡交易记录 → 提取: 金额(amount), 交易类型(tx_type:转入/转出), 余额(bank_balance), 银行名(bank_name)
4. 基金买入确认 → 提取: 基金名(fund_name), 基金代码(fund_code), 买入金额(amount), 确认份额(shares), 确认净值(nav), 日期(date：优先取"买入时间"，没有再取"确认时间"或"交易时间"，必须是 YYYY-MM-DD 格式)
5. 基金赎回确认 → 提取: 基金名(fund_name), 基金代码(fund_code), 赎回份额(shares), 到账金额(amount), 确认净值(nav), 日期(date：同上)
6. 工资条/收入 → 提取: 税后金额(amount), 日期(date)

⚠️ 重要：基金买入截图里如果同时有"买入时间"和"确认时间"，**优先用"买入时间"**（这才是用户实际下单日，确认时间是T+1清算日）。如果只有日期没有时间，取日期部分（YYYY-MM-DD）。

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
        ]

        llm_result = gw.call_multimodal(
            messages,
            model=vision_model,
            user_id="",
            module="ocr_vision",
            max_tokens=800,
        )

        if not llm_result.get("fallback") and llm_result.get("content"):
            text = llm_result["content"]
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


# ========================================================
# 预警冷却
# ========================================================
_alert_cooldown = {}  # {alert_key: last_alert_time}


# ========================================================
# 用户偏好默认值
# ========================================================
USER_DEFAULTS = {
    "display_mode": "pro",
    "risk_profile": "balanced",
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
        "display_mode": "pro",
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


# ========================================================
# 家庭成员常量
# ========================================================
FAMILY_MEMBERS = ["LeiJiang", "BuLuoGeLi"]
NICKNAMES = {"LeiJiang": "厉害了哥", "BuLuoGeLi": "部落格里"}


# ========================================================
# 可用模型列表
# ========================================================
# FIX 2026-08-09: DeepSeek 已于2026-07-24 停用 deepseek-reasoner(R1)/deepseek-chat(V3)
# 旧模型名，官方目前将其静默重定向到deepseek-v4-flash（返回200但content为空，
# 只有reasoning_content，触发网关降级链，白白多耗一次调用+等待）。
# 深度推理场景请直接选用 deepseek-v4-pro，故从可选列表移除 deepseek-reasoner。
AVAILABLE_MODELS = [
    {"id": "deepseek-v4-flash", "name": "DeepSeek V4 (快速·主力)", "provider": "deepseek", "base": "https://api.deepseek.com/v1", "env_key": "LLM_API_KEY"},
    {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro (高质量·仲裁)", "provider": "deepseek", "base": "https://api.deepseek.com/v1", "env_key": "LLM_API_KEY"},
    {"id": "doubao-seed-2-0-pro-260215", "name": "豆包 Seed 2.0 Pro (字节·旗舰)", "provider": "doubao", "base": "https://ark.cn-beijing.volces.com/api/v3", "env_key": "DOUBAO_API_KEY"},
    {"id": "doubao-seed-2-0-lite-260215", "name": "豆包 Seed 2.0 Lite (字节·通用)", "provider": "doubao", "base": "https://ark.cn-beijing.volces.com/api/v3", "env_key": "DOUBAO_API_KEY"},
    {"id": "doubao-seed-2-0-mini-260215", "name": "豆包 Seed 2.0 Mini (字节·快速)", "provider": "doubao", "base": "https://ark.cn-beijing.volces.com/api/v3", "env_key": "DOUBAO_API_KEY"},
    {"id": "qwen3.6-plus", "name": "通义千问3.6 Plus (高性价比)", "provider": "qwen", "base": "https://dashscope.aliyuncs.com/compatible-mode/v1", "env_key": "DASHSCOPE_API_KEY"},
    {"id": "qwen3.6-flash", "name": "通义千问3.6 Flash (轻量快速)", "provider": "qwen", "base": "https://dashscope.aliyuncs.com/compatible-mode/v1", "env_key": "DASHSCOPE_API_KEY"},
]


# ========================================================
# 静态文件缓存
# ========================================================
_CACHE_RULES = {
    ".js": "public, max-age=300, stale-while-revalidate=86400",
    ".css": "public, max-age=300, stale-while-revalidate=86400",
    ".png": "public, max-age=604800",
    ".ico": "public, max-age=604800",
    ".json": "public, max-age=60",
    ".html": "no-cache",
}


def _cached_file_response(fp: Path) -> FileResponse:
    """返回带 Cache-Control 的 FileResponse"""
    suffix = fp.suffix.lower()
    headers = {}
    if suffix in _CACHE_RULES:
        headers["Cache-Control"] = _CACHE_RULES[suffix]
    return FileResponse(fp, headers=headers)

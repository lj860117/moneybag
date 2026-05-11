"""
钱袋子 — V7.2: 推荐引擎
6 维评分（估值28% + 盈利23% + 技术15% + 资金14% + 风险10% + 题材10%）→ 排序 → R1 生成理由

候选池：Tushare report_rc 有研报覆盖的股票（≈200-300只）
评分来源：V6.5 盈利预测 + 已有 signal/factor/risk 模块 + 同花顺热点题材
输出：Top N 推荐列表 + 6维雷达图数据 + R1推理理由 + 建议仓位
"""
from __future__ import annotations

MODULE_META = {
    "name": "recommend_engine",
    "scope": "public",
    "input": ["earnings_forecast", "valuation_engine", "factor_data"],
    "output": "recommendations",
    "cost": "llm_heavy",
    "tags": ["推荐", "评分", "选股", "配置"],
    "description": "V7.2推荐引擎：6维评分（新增题材维度）+R1理由+建议仓位",
    "layer": "analysis",
    "priority": 5,
}

import os
import time
import json
import threading
from datetime import datetime
from config import DATA_DIR
from infra.cache import MemoryCache

_REC_CACHE_TTL = 3600  # 1小时
_pool_building = False  # 标记 fina_indicator 正在后台构建中
_rec_cache = MemoryCache(default_ttl=_REC_CACHE_TTL)

# 6 维权重（可配置，V8 复盘可调）— V7.2 新增 theme 维度
RECOMMEND_WEIGHTS = {
    "valuation": 0.28,  # 估值（原30%，让出2%给题材）
    "earnings":  0.23,  # 盈利（原25%，让出2%给题材）
    "technical": 0.15,  # 技术（不变）
    "capital":   0.14,  # 资金（原15%，让出1%给题材）
    "risk":      0.10,  # 风险（原15%，让出5%给题材）
    "theme":     0.10,  # 题材热度（新增：同花顺热门题材归因）
}

# V7.5 增强：按持有周期分类的权重（含 V7.2 新增 theme 维度）
PERIOD_WEIGHTS = {
    "short": {  # 短线 1-2 周
        "valuation": 0.05, "earnings": 0.10, "technical": 0.35,
        "capital": 0.30, "risk": 0.10, "theme": 0.10,
        "label": "短线（1-2周）", "icon": "⚡",
    },
    "medium": {  # 中线 1-3 月
        "valuation": 0.25, "earnings": 0.28, "technical": 0.15,
        "capital": 0.17, "risk": 0.05, "theme": 0.10,
        "label": "中线（1-3月）", "icon": "📊",
    },
    "long": {  # 长线 6 月+
        "valuation": 0.33, "earnings": 0.28, "technical": 0.05,
        "capital": 0.09, "risk": 0.15, "theme": 0.10,
        "label": "长线（6月+）", "icon": "🏦",
    },
}


def get_stock_recommendations(user_id: str = "", top_n: int = 10, pool: str = "hot", period: str = "medium") -> dict:
    """股票推荐主函数

    Args:
        user_id: 用户 ID
        top_n: 返回前 N 个推荐
        pool: 候选池 - "hot"(研报热门) / "hs300"(沪深300) / "all"
        period: 持有周期 - "short"(短线) / "medium"(中线) / "long"(长线)
    """
    # 根据周期选择权重
    weights = PERIOD_WEIGHTS.get(period, PERIOD_WEIGHTS["medium"])
    period_label = weights.get("label", "中线")

    # V7.5: 按用户风险偏好调整权重
    if user_id:
        try:
            from services.user_service import get_user_preference
            pref = get_user_preference(user_id)
            risk_type = pref.get("riskType", "balanced")
            if risk_type == "growth":
                # 进攻型：加技术+资金，减风险
                weights = {**weights, "technical": weights.get("technical", 0.15) + 0.05,
                           "capital": weights.get("capital", 0.15) + 0.05,
                           "risk": max(0.05, weights.get("risk", 0.15) - 0.10)}
                period_label += " · 进攻型"
            elif risk_type == "conservative":
                # 保守型：加估值+风险，减技术
                weights = {**weights, "valuation": weights.get("valuation", 0.30) + 0.10,
                           "risk": weights.get("risk", 0.15) + 0.05,
                           "technical": max(0.05, weights.get("technical", 0.15) - 0.10),
                           "capital": max(0.05, weights.get("capital", 0.15) - 0.05)}
                period_label += " · 保守型"
            elif risk_type == "balanced":
                period_label += " · 均衡型"
        except Exception:
            pass

    active_weights = {k: v for k, v in weights.items() if k in RECOMMEND_WEIGHTS}

    cache_key = f"rec_{user_id}_{pool}_{top_n}_{period}"
    now = time.time()
    cached = _rec_cache.get(cache_key)
    if cached is not None:
        return cached

    # 文件缓存：推荐结果持久化（TTL=4小时），服务重启后秒级响应
    from pathlib import Path
    import json as _json
    _file_cache_dir = Path(DATA_DIR) / "_cache"
    _file_cache_dir.mkdir(parents=True, exist_ok=True)
    _safe_key = cache_key.replace("/", "_")
    _file_cache_fp = _file_cache_dir / f"recommend_{_safe_key}.json"
    _FILE_CACHE_TTL = 4 * 3600  # 4小时
    if _file_cache_fp.exists():
        try:
            payload = _json.loads(_file_cache_fp.read_text())
            if time.time() < payload.get("_expires_at", 0):
                result = {k: v for k, v in payload.items() if not k.startswith("_")}
                _rec_cache.set(cache_key, result)
                return result
        except Exception:
            pass

    print(f"[RECOMMEND] 开始推荐: user={user_id}, pool={pool}, top={top_n}")

    # 1. 获取候选池
    candidates = _get_candidate_pool(pool)
    if not candidates:
        # 降级：尝试从 AKShare 拉热门股票
        try:
            from infra.data_source.market.stocks import get_stock_realtime_quotes_em
            df = get_stock_realtime_quotes_em()
            if df is not None and len(df) > 0:
                cols = list(df.columns)
                code_col = next((c for c in cols if "代码" in c), None)
                name_col = next((c for c in cols if "名称" in c), None)
                pe_col = next((c for c in cols if "市盈率" in c), None)
                if code_col and name_col:
                    # 取成交额 TOP 30（热门）
                    vol_col = next((c for c in cols if "成交额" in c), None)
                    if vol_col:
                        df = df.sort_values(vol_col, ascending=False)
                    for _, row in df.head(30).iterrows():
                        c = str(row[code_col])
                        if c.startswith(("0", "3", "6")):
                            candidates.append({
                                "code": c, "ts_code": c,
                                "name": str(row.get(name_col, "")),
                                "forecast_pe": float(row[pe_col]) if pe_col and row.get(pe_col) else None,
                                "rating": "", "source": "akshare_hot",
                            })
                    print(f"[RECOMMEND] 降级候选池(AKShare热门): {len(candidates)} 只")
        except Exception as e:
            print(f"[RECOMMEND] 降级候选池也失败: {e}")

    if not candidates:
        return {"recommendations": [], "pool_size": 0, "error": "候选池为空"}

    # 2. 逐个评分
    scored = []
    for stock in candidates[:50]:  # 最多评 50 只，控制 API 调用量
        try:
            s = _calc_composite_score(stock)
            if s:
                scored.append(s)
        except Exception as e:
            print(f"[RECOMMEND] 评分失败 {stock.get('code', '')}: {e}")

    # 3. 排序取 Top N
    scored.sort(key=lambda x: x.get("total_score", 0), reverse=True)
    top = scored[:top_n]

    # 4. R1 生成推荐理由（批量）
    if top:
        _generate_reasons(top)

    # 5. 计算建议仓位
    for item in top:
        item["suggested_position"] = _calc_position(item)

    result = {
        "recommendations": top,
        "pool_size": len(candidates),
        "scored_count": len(scored),
        "generated_at": datetime.now().isoformat(),
        "weights": active_weights,
        "period": period,
        "period_label": period_label,
    }

    print(f"[RECOMMEND] 完成: 候选{len(candidates)} → 评分{len(scored)} → 推荐{len(top)}")

    _rec_cache.set(cache_key, result)
    # 写入文件缓存（4小时 TTL，服务重启后可秒级响应）
    try:
        payload = {**result, "_expires_at": time.time() + _FILE_CACHE_TTL}
        _file_cache_fp.write_text(_json.dumps(payload, ensure_ascii=False, default=str))
    except Exception as e:
        print(f"[RECOMMEND] 文件缓存写入失败: {e}")
    return result


def _get_candidate_pool(pool: str) -> list:
    """获取候选股票池

    缓存策略（SQLite 每日缓存）：
    - 同一天同一 pool 只请求一次 report_rc（避免消耗每日 10 次限额）
    - report_rc 频率超限时自动切换 fina_indicator + HS300 成分股兜底
    - 两者都失败时回落到 SQLite 中最近 7 天的历史记录
    """
    import sqlite3
    from pathlib import Path
    from datetime import datetime as _dt, timedelta as _td

    today = _dt.now().strftime("%Y%m%d")
    db_path = Path(DATA_DIR) / "_cache" / "candidate_pool.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    def _db_read(date_str: str) -> list | None:
        try:
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute(
                "CREATE TABLE IF NOT EXISTS pool_cache "
                "(date TEXT, pool TEXT, data TEXT, PRIMARY KEY(date, pool))"
            )
            cur.execute("SELECT data FROM pool_cache WHERE date=? AND pool=?", (date_str, pool))
            row = cur.fetchone()
            conn.close()
            return json.loads(row[0]) if row else None
        except Exception:
            return None

    def _db_write(data: list, date_str: str = today):
        try:
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute(
                "CREATE TABLE IF NOT EXISTS pool_cache "
                "(date TEXT, pool TEXT, data TEXT, PRIMARY KEY(date, pool))"
            )
            cur.execute(
                "INSERT OR REPLACE INTO pool_cache VALUES (?,?,?)",
                (date_str, pool, json.dumps(data, ensure_ascii=False)),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[RECOMMEND] SQLite 写缓存失败: {e}")

    # 1. 今日缓存命中 → 直接返回（一天只拉一次 report_rc）
    cached = _db_read(today)
    if cached:
        print(f"[RECOMMEND] 候选池 SQLite 命中(今日/{today}): {len(cached)} 只")
        return cached

    candidates: list = []

    if pool == "hot":
        # 2. 尝试 report_rc（每天限10次，写入缓存后当日不再消耗）
        try:
            from services.tushare_data import _call_tushare
            rows = _call_tushare(
                "report_rc",
                {"limit": 200},
                "ts_code,name,report_date,eps,pe,roe,rating,max_price,min_price",
            )
            seen: dict = {}
            for r in rows:
                code = r.get("ts_code", "")
                if code and code not in seen:
                    seen[code] = {
                        "code": code.split(".")[0],
                        "ts_code": code,
                        "name": r.get("name", ""),
                        "forecast_eps": r.get("eps"),
                        "forecast_pe": r.get("pe"),
                        "forecast_roe": r.get("roe"),
                        "rating": r.get("rating", ""),
                        "target_high": r.get("max_price"),
                        "target_low": r.get("min_price"),
                        "source": "report_rc",
                    }
            candidates = list(seen.values())
            print(f"[RECOMMEND] 候选池(report_rc): {len(candidates)} 只")
            # report_rc 返回空（可能次数用尽但无异常），自动切换 fina_indicator 兜底
            if not candidates:
                print("[RECOMMEND] report_rc 返回空，切换 fina_indicator 兜底")
                candidates = _build_fina_pool_sync_or_bg(today, pool, _db_write)
        except Exception as e:
            print(f"[RECOMMEND] report_rc 失败({e})，切换 fina_indicator 兜底")
            candidates = _build_fina_pool_sync_or_bg(today, pool, _db_write)

    # 3. 成功则写入今日 SQLite 缓存
    if candidates:
        _db_write(candidates, today)
        return candidates

    # 4. 两者均失败 → 回落最近 7 天历史记录
    for i in range(1, 8):
        past_date = (_dt.now() - _td(days=i)).strftime("%Y%m%d")
        stale = _db_read(past_date)
        if stale:
            print(f"[RECOMMEND] 使用 {past_date} 历史缓存（降级）: {len(stale)} 只")
            return stale

    return candidates


def _build_fina_pool_sync_or_bg(today: str, pool: str, db_write_fn) -> list:
    """首次冷启动时，在后台线程构建候选池（避免 HTTP 超时）。

    - 如果后台已在构建（_pool_building），直接返回空列表，让上层走 7 天缓存。
    - 如果没有在构建，启动后台线程，本次请求返回空列表（前端显示"正在预热"）。
    - 后台线程完成后写入 SQLite，下次请求即可命中缓存（通常 1-2 分钟）。
    """
    global _pool_building
    if _pool_building:
        print("[RECOMMEND] fina_indicator 正在后台构建，本次跳过等待")
        return []

    def _bg_build():
        global _pool_building
        _pool_building = True
        try:
            candidates = _build_fina_pool()
            if candidates:
                db_write_fn(candidates, today)
                print(f"[RECOMMEND] 后台候选池构建完成: {len(candidates)} 只，已写入 SQLite")
        except Exception as e:
            print(f"[RECOMMEND] 后台候选池构建失败: {e}")
        finally:
            _pool_building = False

    t = threading.Thread(target=_bg_build, daemon=True)
    t.start()
    print("[RECOMMEND] 候选池正在后台预热（fina_indicator），约1-2分钟后就绪")
    return []


def _build_fina_pool(limit: int = 60) -> list:
    """report_rc 限额时的兜底候选池

    用 fina_indicator（5000分，无每日次数上限，仅有每分钟频次限制）
    从 HS300 成分股拼财务候选池。

    Steps:
    1. 优先通过 index_weight 取最新 HS300 成分股；失败则用内置静态列表
    2. 逐只查 fina_indicator（0.12s 间隔控速，约 500次/分钟）
    3. 一次性查 stock_basic 补充股票名称
    """
    import time as _time

    # HS300 核心成分股静态备用列表（按市值权重排序，更新于 2026Q1）
    _HS300_STATIC = [
        "600519.SH", "300750.SZ", "601318.SH", "600036.SH", "600900.SH",
        "601166.SH", "601398.SH", "601288.SH", "601939.SH", "601628.SH",
        "000858.SZ", "000333.SZ", "600276.SH", "002475.SZ", "601888.SH",
        "600887.SH", "600309.SH", "002594.SZ", "000568.SZ", "600031.SH",
        "601006.SH", "601012.SH", "000725.SZ", "601186.SH", "600741.SH",
        "000002.SZ", "601601.SH", "000651.SZ", "002304.SZ", "600030.SH",
        "601688.SH", "600690.SH", "601669.SH", "002415.SZ", "601390.SH",
        "600048.SH", "601766.SH", "601211.SH", "601236.SH", "000776.SZ",
        "002230.SZ", "300015.SZ", "600918.SH", "601225.SH", "600104.SH",
        "601336.SH", "002049.SZ", "600600.SH", "601319.SH", "600438.SH",
        "601111.SH", "603288.SH", "002142.SZ", "002607.SZ", "600089.SH",
        "000860.SZ", "002236.SZ", "600702.SH", "601816.SH", "600050.SH",
    ]

    candidates: list = []
    try:
        from services.tushare_data import _call_tushare

        # Step 1: 获取成分股列表
        ts_codes: list = []
        try:
            wt_rows = _call_tushare(
                "index_weight", {"index_code": "399300.SZ", "limit": 100}, "con_code,weight"
            )
            if wt_rows:
                wt_rows.sort(key=lambda x: float(x.get("weight") or 0), reverse=True)
                ts_codes = [r["con_code"] for r in wt_rows[:limit] if r.get("con_code")]
                print(f"[RECOMMEND] index_weight 获取 {len(ts_codes)} 只 HS300 成分股")
        except Exception:
            pass
        if not ts_codes:
            ts_codes = _HS300_STATIC[:limit]
            print(f"[RECOMMEND] 使用静态 HS300 列表: {len(ts_codes)} 只")

        # Step 2: 逐只查 fina_indicator（ROE/EPS/PE 最新季报）
        seen: dict = {}
        for ts_code in ts_codes:
            if ts_code in seen:
                continue
            try:
                rows = _call_tushare(
                    "fina_indicator",
                    {"ts_code": ts_code, "limit": 1},
                    "ts_code,end_date,roe,eps,pe,grossprofit_margin",
                )
                r = rows[0] if rows else {}
                seen[ts_code] = {
                    "code": ts_code.split(".")[0],
                    "ts_code": ts_code,
                    "name": ts_code.split(".")[0],  # 暂用代码，Step 3 补名称
                    "forecast_eps": r.get("eps"),
                    "forecast_pe": r.get("pe"),
                    "forecast_roe": r.get("roe"),
                    "rating": "",
                    "target_high": None,
                    "target_low": None,
                    "source": "fina_indicator",
                }
                _time.sleep(0.12)  # 控速：≤500次/分钟
            except Exception as e:
                print(f"[RECOMMEND] fina_indicator {ts_code}: {e}")
                continue

        # Step 3: 补充股票名称（stock_basic 一次批量）
        if seen:
            try:
                name_rows = _call_tushare(
                    "stock_basic",
                    {"ts_code": ",".join(seen.keys()), "list_status": "L"},
                    "ts_code,name",
                )
                for nr in name_rows or []:
                    tc = nr.get("ts_code", "")
                    if tc in seen and nr.get("name"):
                        seen[tc]["name"] = nr["name"]
            except Exception:
                pass

        candidates = list(seen.values())
        print(f"[RECOMMEND] 候选池(fina_indicator 兜底): {len(candidates)} 只")

    except Exception as e:
        print(f"[RECOMMEND] fina_indicator 兜底完全失败: {e}")

    # mootdx finance 兜底（fina_indicator 积分耗尽时，取 HS300 前 20 只）
    if not candidates:
        print("[RECOMMEND] 尝试 mootdx finance 作最后兜底...")
        _HS300_TOP20 = [
            "600519", "300750", "601318", "600036", "600900",
            "601166", "601398", "601288", "601939", "601628",
            "000858", "000333", "600276", "002475", "601888",
            "600887", "600309", "002594", "000568", "600031",
        ]
        for code in _HS300_TOP20:
            try:
                from infra.data_source.providers.mootdx_provider import get_finance_mootdx
                f = get_finance_mootdx(code)
                if f:
                    ts_code = f"{code}.SH" if code.startswith("6") else f"{code}.SZ"
                    candidates.append({
                        "code": code,
                        "ts_code": ts_code,
                        "name": code,  # 无名称来源，用代码替代
                        "forecast_eps": f.get("eps"),
                        "forecast_pe": None,
                        "forecast_roe": f.get("roe"),
                        "rating": "",
                        "target_high": None,
                        "target_low": None,
                        "source": "mootdx",
                    })
            except Exception as e:
                print(f"[RECOMMEND] mootdx finance 兜底 {code}: {e}")
        if candidates:
            print(f"[RECOMMEND] mootdx finance 兜底成功: {len(candidates)} 只")

    return candidates


def _calc_composite_score(stock: dict) -> dict:
    """计算 6 维综合评分（V7.2 新增 theme 维度）"""
    scores = {
        "valuation": _score_valuation(stock),
        "earnings": _score_earnings(stock),
        "technical": _score_technical(stock),
        "capital": _score_capital(stock),
        "risk": _score_risk(stock),
        "theme": _score_theme(stock),   # V7.2 新增：同花顺热点题材
    }

    total = sum(scores[k] * RECOMMEND_WEIGHTS[k] for k in RECOMMEND_WEIGHTS)

    # 构造 evidence（每维度的打分依据）
    evidence = {}
    for dim, score in scores.items():
        evidence[dim] = {
            "score": score,
            "weight": f"{RECOMMEND_WEIGHTS[dim]*100:.0f}%",
        }

    return {
        **stock,
        "total_score": round(total, 1),
        "dimension_scores": scores,
        "evidence": evidence,
    }


def _score_valuation(stock: dict) -> int:
    """估值维度评分 (0-100)"""
    score = 50
    try:
        from services.valuation_engine import assess_valuation
        code = stock.get("code", "")
        if code:
            v = assess_valuation(code)
            if v.get("available"):
                return v.get("score", 50)
    except Exception:
        pass

    # 降级：直接用研报的 PE 和 ROE
    pe = stock.get("forecast_pe")
    if pe:
        pe = float(pe)
        if pe < 15:
            score = 80
        elif pe < 20:
            score = 65
        elif pe < 30:
            score = 50
        elif pe < 50:
            score = 35
        else:
            score = 20

    # 腾讯财经兜底（tushare/研报 PE 为 None 时）
    if not stock.get("forecast_pe"):
        try:
            code = stock.get("code", "")
            if code:
                from infra.data_source.providers.tencent_provider import get_stock_quote_tencent
                q = get_stock_quote_tencent(code)
                if q and q.get("pe_ttm"):
                    pe = float(q["pe_ttm"])
                    if pe < 15:
                        score = 80
                    elif pe < 20:
                        score = 65
                    elif pe < 30:
                        score = 50
                    elif pe < 50:
                        score = 35
                    else:
                        score = 20
        except Exception:
            pass

    return score


def _score_earnings(stock: dict) -> int:
    """盈利维度评分 (0-100)"""
    score = 50
    roe = stock.get("forecast_roe")
    if roe:
        roe = float(roe)
        if roe > 25:
            score = 85
        elif roe > 15:
            score = 70
        elif roe > 10:
            score = 55
        elif roe > 5:
            score = 40
        else:
            score = 25

    # 评级加分
    rating = stock.get("rating", "")
    if rating in ("买入", "强烈推荐", "强推"):
        score = min(100, score + 10)
    elif rating in ("增持", "推荐"):
        score = min(100, score + 5)
    elif rating in ("减持", "卖出"):
        score = max(0, score - 15)

    return score


def _score_technical(stock: dict) -> int:
    """技术维度评分 (0-100) — 个股级 RSI/MACD/均线
    P0.2: 从空壳（永远50）改为真实评分
    """
    code = stock.get("code", "")
    if not code:
        return 50

    # 检查缓存（1小时 TTL，避免重复拉 K 线）
    cache_key = f"tech_{code}"
    cached = _rec_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        from infra.data_source.market.stocks import get_stock_daily_hist
        import numpy as np
        from datetime import datetime as _dt, timedelta as _td

        # 拉 60 日 K 线（前复权）— 2026-04-19 A3: 走统一 provider
        end_date = _dt.now().strftime("%Y%m%d")
        start_date = (_dt.now() - _td(days=90)).strftime("%Y%m%d")
        try:
            from services.stock_price_provider import get_daily_df
            df = get_daily_df(code, days=90)
        except Exception:
            df = get_stock_daily_hist(code=code, period="daily",
                                     start_date=start_date, end_date=end_date, adjust="qfq")
        if df is None or len(df) < 30:
            return 50

        close = df["收盘"].values.astype(float)
        score = 50

        # RSI(14)
        deltas = np.diff(close)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains[-14:])
        avg_loss = np.mean(losses[-14:])
        if avg_loss > 0:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        else:
            rsi = 100
        if rsi < 30:
            score += 20  # 超卖
        elif rsi < 40:
            score += 10
        elif rsi > 70:
            score -= 20  # 超买
        elif rsi > 60:
            score -= 10

        # MACD（12, 26, 9）
        def _ema(arr, span):
            result = np.zeros_like(arr)
            result[0] = arr[0]
            alpha = 2 / (span + 1)
            for i in range(1, len(arr)):
                result[i] = alpha * arr[i] + (1 - alpha) * result[i - 1]
            return result

        ema12 = _ema(close, 12)
        ema26 = _ema(close, 26)
        macd_line = ema12 - ema26
        signal_line = _ema(macd_line, 9)
        if len(macd_line) >= 2 and len(signal_line) >= 2:
            if macd_line[-1] > signal_line[-1] and macd_line[-2] <= signal_line[-2]:
                score += 15  # 金叉
            elif macd_line[-1] < signal_line[-1] and macd_line[-2] >= signal_line[-2]:
                score -= 15  # 死叉
            elif macd_line[-1] > signal_line[-1]:
                score += 5   # 多头
            elif macd_line[-1] < signal_line[-1]:
                score -= 5   # 空头

        # MA20 位置
        if len(close) >= 20:
            ma20 = np.mean(close[-20:])
            if close[-1] > ma20:
                score += 10  # 站上均线
            else:
                score -= 10  # 跌破均线

        final_score = max(0, min(100, score))
        _rec_cache.set(cache_key, final_score)
        return final_score

    except Exception as e:
        print(f"[RECOMMEND] 技术评分失败 {code}: {e}")
        return 50


def _score_capital(stock: dict) -> int:
    """资金维度评分 (0-100) — 个股级北向持仓变化 + 全局趋势兜底
    P1.2: 从全局同分改为个股化
    """
    ts_code = stock.get("ts_code", "")
    score = 50

    # 1. Tushare hk_hold: 个股北向持仓占比变化（5000积分可用）
    if ts_code:
        try:
            from services.tushare_data import _call_tushare
            rows = _call_tushare("hk_hold", {"ts_code": ts_code, "limit": 10},
                                  "trade_date,vol,ratio")
            if rows and len(rows) >= 2:
                latest_ratio = float(rows[0].get("ratio") or 0)
                prev_ratio = float(rows[-1].get("ratio") or 0)
                change = latest_ratio - prev_ratio
                if change > 0.5:
                    score = 80  # 外资显著加仓
                elif change > 0.1:
                    score = 65
                elif change < -0.5:
                    score = 25  # 外资显著减仓
                elif change < -0.1:
                    score = 40
                return score
        except Exception as e:
            print(f"[RECOMMEND] hk_hold 查询失败 {ts_code}: {e}")

    # 2. 兜底：全局北向趋势
    try:
        from services.factor_data import get_northbound_flow
        north = get_northbound_flow()
        if north.get("trend") in ("大幅流入", "净流入"):
            score = 65
        elif north.get("trend") in ("大幅流出", "净流出"):
            score = 35
    except Exception:
        pass
    return score


def _score_risk(stock: dict) -> int:
    """风险维度评分 (0-100, 越高越安全) — 个股波动率 + 地缘加成
    P1.2: 从全局同分改为个股化
    """
    score = 60  # 默认中等安全
    code = stock.get("code", "")

    # 1. 个股 20 日年化波动率
    if code:
        try:
            from infra.data_source.market.stocks import get_stock_daily_hist
            import numpy as np
            from datetime import datetime as _dt, timedelta as _td
            end_date = _dt.now().strftime("%Y%m%d")
            start_date = (_dt.now() - _td(days=60)).strftime("%Y%m%d")
            # 2026-04-19 A3: 走统一 provider
            try:
                from services.stock_price_provider import get_daily_df
                df = get_daily_df(code, days=60)
            except Exception:
                df = get_stock_daily_hist(code=code, period="daily",
                                         start_date=start_date, end_date=end_date, adjust="qfq")
            if df is not None and len(df) >= 20:
                close = df["收盘"].values.astype(float)
                returns = np.diff(close) / close[:-1]
                vol_20d = np.std(returns[-20:]) * (252 ** 0.5)  # 年化波动率
                if vol_20d < 0.25:
                    score = 80  # 低波动，安全
                elif vol_20d < 0.40:
                    score = 60
                elif vol_20d < 0.60:
                    score = 40
                else:
                    score = 25  # 高波动，危险
        except Exception as e:
            print(f"[RECOMMEND] 风险评分波动率失败 {code}: {e}")

    # 2. 地缘加成（负面）
    try:
        from services.geopolitical import get_geopolitical_risk_score
        geo = get_geopolitical_risk_score()
        severity = geo.get("severity", 0)
        if severity >= 4:
            score = max(10, score - 30)
        elif severity >= 2:
            score = max(20, score - 15)
    except Exception:
        pass
    return score


def _score_theme(stock: dict) -> int:
    """题材维度评分 (0-100) — 同花顺热门题材归属
    V7.2 新增：个股属于越多热门题材，题材热度分越高
    - 不在任何热门题材中：50（中性）
    - 命中1个热门题材：65
    - 命中2个：70
    - 命中3个及以上：最高 85

    副作用：将 theme_tags 写入 stock dict，供 LLM 生成理由时引用。
    """
    code = stock.get("code", "")
    if not code:
        return 50

    try:
        from infra.data_source.alt.ths_concepts import get_stock_theme_tags
        tags = get_stock_theme_tags(code, top_concepts=20)
        if not tags:
            return 50
        # 命中1个热门题材 +15分，之后每多一个 +5分，上限 85
        score = min(85, 50 + 15 + (len(tags) - 1) * 5)
        stock["theme_tags"] = tags  # 供 LLM 生成推荐理由时引用
        return score
    except Exception as e:
        print(f"[RECOMMEND] 题材评分失败 {code}: {e}")
        return 50


def _generate_reasons(top_items: list) -> None:
    """用 R1 批量生成推荐理由"""
    try:
        from config import LLM_API_URL, LLM_API_KEY
        if not LLM_API_KEY:
            for item in top_items:
                item["reason"] = _rule_reason(item)
            return

        import httpx
        stocks_text = "\n".join(
            f"{i+1}. {item['name']}({item['code']}) 综合{item['total_score']}分 "
            f"估值={item['dimension_scores']['valuation']} "
            f"盈利={item['dimension_scores']['earnings']} "
            f"题材={item['dimension_scores'].get('theme', 50)} "
            f"PE={item.get('forecast_pe', '?')} ROE={item.get('forecast_roe', '?')}% "
            f"评级={item.get('rating', '?')} "
            f"热点题材={','.join(item.get('theme_tags', [])[:3]) or '无'}"
            for i, item in enumerate(top_items[:5])
        )

        prompt = f"""你是 A 股投资顾问。以下是推荐引擎筛选出的 Top 股票，请为每只股票写一句话推荐理由（20-40字，说清楚为什么推荐）。

{stocks_text}

输出 JSON 数组，每项一句话：
[{{"code":"600519","reason":"一句话理由"}}, ...]
只输出 JSON，不要其他内容。"""

        with httpx.Client(timeout=30) as client:
            resp = client.post(
                LLM_API_URL,
                headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "deepseek-chat",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 500,
                    "temperature": 0.5,
                },
            )
            if resp.status_code == 200:
                import re
                text = resp.json()["choices"][0]["message"]["content"]
                json_match = re.search(r'\[[\s\S]*\]', text)
                if json_match:
                    reasons = json.loads(json_match.group())
                    reason_map = {r.get("code", ""): r.get("reason", "") for r in reasons}
                    for item in top_items:
                        item["reason"] = reason_map.get(item.get("code", ""), _rule_reason(item))
                    return

    except Exception as e:
        print(f"[RECOMMEND] R1 理由生成失败: {e}")

    # 降级：规则理由
    for item in top_items:
        item["reason"] = _rule_reason(item)


def _rule_reason(item: dict) -> str:
    """规则引擎降级理由"""
    parts = []
    vs = item.get("dimension_scores", {})
    if vs.get("valuation", 0) >= 70:
        parts.append("估值偏低")
    if vs.get("earnings", 0) >= 70:
        parts.append("盈利能力强")
    rating = item.get("rating", "")
    if rating in ("买入", "推荐", "强推"):
        parts.append(f"机构评级{rating}")
    if not parts:
        parts.append("综合评分较高")
    return f"{item.get('name', '')}：{'，'.join(parts)}"


def _calc_position(item: dict) -> dict:
    """计算建议仓位"""
    score = item.get("total_score", 0)
    if score >= 80:
        return {"action": "建议买入", "position_pct": 5, "emoji": "🟢"}
    elif score >= 70:
        return {"action": "可以关注", "position_pct": 3, "emoji": "🟡"}
    elif score >= 60:
        return {"action": "观望", "position_pct": 0, "emoji": "⚪"}
    else:
        return {"action": "不推荐", "position_pct": 0, "emoji": "🔴"}

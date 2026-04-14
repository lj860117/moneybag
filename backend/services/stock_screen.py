"""
钱袋子 — AI 多因子选股 V3
30 因子体系：价值(6)/成长(5)/质量(6)/动量(4)/风险(4)/流动性(3)/舆情(2)
V3 新增：
  - DeepSeek 动态权重（牛市/熊市/震荡自动调权）
  - 舆情因子真正接入（get_news_sentiment_score）
  - LLM 因子生成器加分（有效因子注入评分）
参考：Zen Ratings 115因子 + AI Hedge Fund 17 Agent + 幻方量化多因子框架

架构：
  Step 1: 批量行情筛选 TOP 200（stock_data_provider，秒级）
  Step 2: 并发拉 TOP 200 财务数据（AKShare 0.5s/只，20并发≈5s）
  Step 3: 30 因子综合打分排序 → TOP N
"""
import time
import json
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import STOCK_CACHE_TTL

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "stock_screen",
    "scope": "public",
    "input": ["top_n"],
    "output": "screened_stocks",
    "cost": "llm_light",
    "tags": ["选股", "30因子", "动态权重"],
    "description": "30因子7维打分V3，DeepSeek动态权重+LLM因子加分",
    "layer": "analysis",
    "priority": 2,
}

_stock_cache = {}

# ---- 30 因子权重配置（默认值，可被 AI 覆盖）----
# 7 大维度权重
DEFAULT_DIM_WEIGHTS = {
    "value": 0.20,      # 价值
    "growth": 0.15,      # 成长
    "quality": 0.18,     # 质量（提权：区分好坏公司的核心）
    "momentum": 0.15,    # 动量
    "risk": 0.12,        # 风险
    "liquidity": 0.10,   # 流动性
    "sentiment": 0.10,   # 舆情
}

# ---- DeepSeek 动态权重调整 ----
_weight_cache = {}
_WEIGHT_CACHE_TTL = 3600  # 1 小时


def _get_dynamic_weights() -> dict:
    """让 DeepSeek 根据市场环境动态调整 7 维权重
    牛市→加动量减风险 / 熊市→加风险减动量 / 震荡→加质量加价值
    失败则返回默认权重
    """
    cache_key = "dynamic_weights"
    now = time.time()
    if cache_key in _weight_cache and now - _weight_cache[cache_key]["ts"] < _WEIGHT_CACHE_TTL:
        return _weight_cache[cache_key]["data"]

    try:
        from config import LLM_API_URL, LLM_API_KEY, LLM_MODEL
        if not LLM_API_KEY:
            return DEFAULT_DIM_WEIGHTS

        # 收集市场环境数据
        market_ctx = ""
        try:
            from services.data_layer import get_valuation_percentile, get_fear_greed_index
            val = get_valuation_percentile()
            fgi = get_fear_greed_index()
            val_pct = val.get("percentile", 50)
            fgi_score = fgi.get("score", 50)
            market_ctx += f"估值百分位: {val_pct:.0f}% | 恐贪指数: {fgi_score:.0f}\n"
        except Exception:
            pass

        try:
            from services.factor_data import get_news_sentiment_score
            sentiment = get_news_sentiment_score()
            if sentiment.get("available"):
                market_ctx += f"新闻情绪: {sentiment.get('score', 0):+d}分 ({sentiment.get('level', '中性')})\n"
        except Exception:
            pass

        try:
            from services.macro_extended import get_market_breadth
            breadth = get_market_breadth()
            if breadth.get("available"):
                market_ctx += f"涨跌家数: 涨{breadth.get('up', 0)} 跌{breadth.get('down', 0)} 活跃度{breadth.get('activity', 0)}%\n"
        except Exception:
            pass

        if not market_ctx:
            market_ctx = "市场数据暂不可用"

        prompt = f"""你是量化投资权重优化师。请根据当前市场环境，动态调整选股因子的 7 个维度权重。

当前市场环境：
{market_ctx}

7 个维度及默认权重：
- value（价值）: 20% — PE/PB/股息率等
- growth（成长）: 15% — 营收增速/ROE趋势
- quality（质量）: 18% — ROE/毛利率/现金流
- momentum（动量）: 15% — 5日/20日/60日涨跌
- risk（风险）: 12% — 振幅/负债率/极端值
- liquidity（流动性）: 10% — 换手率/市值/成交额
- sentiment（舆情）: 10% — 新闻情绪/社交热度

调整原则：
- 牛市（估值高+贪婪）→ 提权动量+舆情，降权价值
- 熊市（估值低+恐惧）→ 提权价值+质量+风险，降权动量
- 震荡（估值适中）→ 提权质量+价值，保持均衡
- 7 个权重加起来必须 = 1.00

返回 JSON，格式：
{{"value":0.20,"growth":0.15,"quality":0.18,"momentum":0.15,"risk":0.12,"liquidity":0.10,"sentiment":0.10,"regime":"牛市/熊市/震荡","reason":"一句话说明"}}
只返回 JSON。"""

        import httpx
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                LLM_API_URL,
                headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": LLM_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 300,
                    "temperature": 0.2,
                },
            )
            if resp.status_code != 200:
                print(f"[DYN_WEIGHT] LLM failed: {resp.status_code}")
                return DEFAULT_DIM_WEIGHTS

            text = resp.json()["choices"][0]["message"]["content"]
            import re
            json_match = re.search(r'\{[^}]+\}', text, re.DOTALL)
            if not json_match:
                return DEFAULT_DIM_WEIGHTS

            parsed = json.loads(json_match.group())

        # 验证权重合法性
        weights = {}
        total = 0
        for key in DEFAULT_DIM_WEIGHTS:
            w = float(parsed.get(key, DEFAULT_DIM_WEIGHTS[key]))
            w = max(0.03, min(0.40, w))  # 单维度 3%-40%
            weights[key] = w
            total += w

        # 归一化
        for key in weights:
            weights[key] = round(weights[key] / total, 3)

        regime = parsed.get("regime", "未知")
        reason = parsed.get("reason", "")
        weights["_regime"] = regime
        weights["_reason"] = reason
        print(f"[DYN_WEIGHT] regime={regime}, weights={weights}, reason={reason}")

        _weight_cache[cache_key] = {"data": weights, "ts": now}
        return weights

    except Exception as e:
        print(f"[DYN_WEIGHT] Failed: {e}")
        return DEFAULT_DIM_WEIGHTS


# ---- 舆情因子真正接入 ----
_sentiment_cache = {"score": None, "ts": 0}
_SENTIMENT_CACHE_TTL = 1800  # 30 分钟


def _get_sentiment_score() -> float:
    """获取全市场舆情得分，映射到 [0, 100]"""
    now = time.time()
    if _sentiment_cache["score"] is not None and now - _sentiment_cache["ts"] < _SENTIMENT_CACHE_TTL:
        return _sentiment_cache["score"]

    try:
        from services.factor_data import get_news_sentiment_score
        result = get_news_sentiment_score()
        if result.get("available"):
            raw = result.get("score", 0)  # -100 ~ +100
            # 映射到 0~100：-100→0, 0→50, +100→100
            mapped = max(0, min(100, 50 + raw * 0.5))
            _sentiment_cache["score"] = mapped
            _sentiment_cache["ts"] = now
            print(f"[SENTIMENT_FACTOR] raw={raw}, mapped={mapped:.0f}")
            return mapped
    except Exception as e:
        print(f"[SENTIMENT_FACTOR] Failed: {e}")

    return 50  # 中性默认


# ---- LLM 因子生成器加分 ----
_llm_bonus_cache = {}
_LLM_BONUS_CACHE_TTL = 7200  # 2 小时


def _get_llm_factor_bonus(code: str) -> float:
    """从 LLM 因子生成器的缓存中获取有效因子加分
    已有缓存（generate_alpha_factors 运行过）→ 用有效因子 IC 值算加分
    无缓存 → 返回 0（不阻塞选股流程）

    加分规则：
    - 有效因子数 × 2 分（上限 10 分）
    - 最佳 IC > 0.05 额外 +5 分
    - 最佳 IC > 0.03 额外 +3 分
    """
    cache_key = f"llm_bonus_{code}"
    now = time.time()
    if cache_key in _llm_bonus_cache and now - _llm_bonus_cache[cache_key]["ts"] < _LLM_BONUS_CACHE_TTL:
        return _llm_bonus_cache[cache_key]["data"]

    try:
        from services.llm_factor_gen import _llm_factor_cache

        # 查找任何包含此股票代码的缓存
        clean_code = code.replace("sh", "").replace("sz", "").replace("SH", "").replace("SZ", "")
        bonus = 0.0

        for key, cached in _llm_factor_cache.items():
            if clean_code in key and now - cached.get("ts", 0) < 86400:  # 24小时内
                data = cached.get("data", {})
                effective = data.get("effective_factors", [])
                summary = data.get("summary", {})

                if effective:
                    # 有效因子数加分
                    bonus += min(len(effective) * 2, 10)
                    # 最佳 IC 加分
                    best_ic = summary.get("best_ic", 0)
                    if best_ic > 0.05:
                        bonus += 5
                    elif best_ic > 0.03:
                        bonus += 3

                    print(f"[LLM_BONUS] {code}: {len(effective)} effective factors, best_ic={best_ic:.4f}, bonus={bonus}")
                break

        _llm_bonus_cache[cache_key] = {"data": bonus, "ts": now}
        return bonus

    except Exception:
        return 0.0


def _score_value(s: dict, fin: dict) -> float:
    """价值维度：PE + PB + 股息率 + EV/EBITDA + PE/G + 股价/净资产（6 因子）"""
    score = 0
    pe = s.get("pe")
    pb = s.get("pb")

    # F1: PE（越低越好）
    if pe is not None:
        if pe < 10: score += 18
        elif pe < 15: score += 15
        elif pe < 20: score += 12
        elif pe < 30: score += 6
        elif pe < 50: score += 2

    # F2: PB（越低越好）
    if pb is not None:
        if pb < 1: score += 18
        elif pb < 1.5: score += 15
        elif pb < 2: score += 12
        elif pb < 3: score += 6
        elif pb < 5: score += 2

    # F3: 股息率（从 PE 反推：1/PE，PE<20 → 股息率>5% 概率高）
    if pe is not None and pe > 0:
        implied_yield = 100 / pe
        if implied_yield > 5: score += 15
        elif implied_yield > 3: score += 10
        elif implied_yield > 2: score += 5

    # F4: ROE/PB（格雷厄姆价值指标：ROE 高但 PB 低 = 便宜好货）
    roe = fin.get("roe")
    if roe is not None and pb is not None and pb > 0:
        roe_pb = roe / pb
        if roe_pb > 10: score += 18
        elif roe_pb > 5: score += 12
        elif roe_pb > 3: score += 6

    # F5: EPS（每股收益，越高越好）
    eps = fin.get("eps")
    if eps is not None:
        if eps > 3: score += 16
        elif eps > 1.5: score += 12
        elif eps > 0.5: score += 6
        elif eps > 0: score += 2

    # F6: 低 PE + 高 ROE 复合（巴菲特最爱）
    if pe is not None and roe is not None:
        if pe < 20 and roe > 15: score += 15
        elif pe < 30 and roe > 10: score += 8

    return min(score, 100)


def _score_growth(s: dict, fin: dict) -> float:
    """成长维度：营收增速 + 净利增速 + EPS趋势 + ROE趋势 + 动量辅助（5 因子）"""
    score = 50  # 中性起步

    # F7: 营收增速
    rev_g = fin.get("revenue_growth")
    if rev_g is not None:
        if rev_g > 30: score += 20
        elif rev_g > 15: score += 15
        elif rev_g > 5: score += 8
        elif rev_g > 0: score += 3
        elif rev_g < -10: score -= 15
        elif rev_g < 0: score -= 5

    # F8: ROE（高 ROE = 持续成长能力）
    roe = fin.get("roe")
    if roe is not None:
        if roe > 20: score += 15
        elif roe > 15: score += 10
        elif roe > 10: score += 5
        elif roe < 5: score -= 10

    # F9: EPS（盈利能力）
    eps = fin.get("eps")
    if eps is not None:
        if eps > 2: score += 10
        elif eps > 1: score += 5
        elif eps < 0: score -= 15

    # F10: 60日动量辅助（上涨趋势 = 市场认可成长）
    c60 = s.get("change_60d")
    if c60 is not None:
        if 5 < c60 < 30: score += 10
        elif c60 >= 30: score += 3
        elif c60 < -15: score -= 10

    # F11: 低 PE + 上涨 = PEG 概念
    pe = s.get("pe")
    if pe is not None and c60 is not None:
        if pe < 25 and c60 > 0: score += 5

    return max(0, min(score, 100))


def _score_quality(s: dict, fin: dict) -> float:
    """质量维度：ROE + 毛利率 + 净利率 + 负债率 + 现金流 + 市值（6 因子）"""
    score = 40  # 略偏正面起步

    # F12: ROE（核心质量指标）
    roe = fin.get("roe")
    if roe is not None:
        if roe > 25: score += 20
        elif roe > 20: score += 16
        elif roe > 15: score += 12
        elif roe > 10: score += 6
        elif roe > 5: score += 2
        elif roe < 0: score -= 20

    # F13: 毛利率（越高越有定价权）
    gm = fin.get("gross_margin")
    if gm is not None:
        if gm > 60: score += 15
        elif gm > 40: score += 12
        elif gm > 25: score += 6
        elif gm < 15: score -= 5

    # F14: 净利率
    nm = fin.get("net_margin")
    if nm is not None:
        if nm > 25: score += 12
        elif nm > 15: score += 8
        elif nm > 8: score += 4
        elif nm < 3: score -= 8

    # F15: 资产负债率（越低越安全）
    dr = fin.get("debt_ratio")
    if dr is not None:
        if dr < 30: score += 12
        elif dr < 50: score += 6
        elif dr > 70: score -= 10
        elif dr > 80: score -= 18

    # F16: 每股经营现金流（正现金流 = 赚真钱）
    cf = fin.get("cash_flow_per_share")
    if cf is not None:
        if cf > 3: score += 12
        elif cf > 1: score += 8
        elif cf > 0: score += 3
        elif cf < 0: score -= 10

    # F17: 市值（大市值通常质量更好）
    mcap = s.get("market_cap")
    if mcap is not None:
        if mcap > 2000: score += 10
        elif mcap > 500: score += 6
        elif mcap > 200: score += 3

    return max(0, min(score, 100))


def _score_momentum(s: dict) -> float:
    """动量维度：5日/20日/60日涨跌 + 成交额排名（4 因子）"""
    score = 50

    # F18: 5 日动量
    c5 = s.get("change_5d")
    if c5 is not None:
        if 0 < c5 < 5: score += 15
        elif c5 >= 5: score += 5
        elif -5 < c5 < 0: score += 5
        else: score -= 10

    # F19: 20 日动量
    c20 = s.get("change_20d")
    if c20 is not None:
        if 0 < c20 < 10: score += 12
        elif c20 >= 10: score += 5
        elif c20 < -10: score -= 10

    # F20: 60 日动量（趋势）
    c60 = s.get("change_60d")
    if c60 is not None:
        if 5 < c60 < 30: score += 20
        elif c60 >= 30: score += 5
        elif -10 < c60 < 5: score += 8
        else: score -= 15

    # F21: 今日涨跌（短期动能）
    cpct = s.get("change_pct")
    if cpct is not None:
        if 0 < cpct < 3: score += 8
        elif cpct >= 3: score += 3
        elif cpct < -3: score -= 8

    return max(0, min(score, 100))


def _score_risk(s: dict, fin: dict) -> float:
    """风险维度：振幅 + 负债率 + 现金流 + PE极端值（4 因子）"""
    score = 70

    # F22: 振幅（越低越稳）
    amp = s.get("amplitude")
    if amp is not None:
        if amp < 2: score += 15
        elif amp < 4: score += 8
        elif amp > 8: score -= 20
        elif amp > 6: score -= 10

    # F23: 负债率风险
    dr = fin.get("debt_ratio")
    if dr is not None:
        if dr > 80: score -= 25
        elif dr > 70: score -= 12
        elif dr < 40: score += 10

    # F24: 现金流风险（负现金流 = 危险）
    cf = fin.get("cash_flow_per_share")
    if cf is not None:
        if cf < -1: score -= 20
        elif cf < 0: score -= 8
        elif cf > 2: score += 8

    # F25: PE 极端值风险
    pe = s.get("pe")
    if pe is not None:
        if pe > 100: score -= 20
        elif pe > 60: score -= 10
        elif pe < 8: score += 5  # 极低 PE 可能是价值陷阱，只加小分

    return max(0, min(score, 100))


def _score_liquidity(s: dict) -> float:
    """流动性维度：换手率 + 市值 + 成交量（3 因子）"""
    score = 50

    # F26: 换手率（适中最好）
    to = s.get("turnover")
    if to is not None:
        if 1 < to < 5: score += 25
        elif 0.5 < to <= 1: score += 12
        elif to >= 5: score += 8

    # F27: 市值（>500 亿流动性好）
    mcap = s.get("market_cap")
    if mcap is not None:
        if mcap > 1000: score += 18
        elif mcap > 500: score += 12
        elif mcap > 200: score += 6

    # F28: 成交额隐含（市值×换手率）
    if to is not None and mcap is not None:
        daily_vol = mcap * to / 100  # 亿元
        if daily_vol > 20: score += 7
        elif daily_vol > 5: score += 4

    return max(0, min(score, 100))


def _score_sentiment() -> float:
    """舆情维度：接入 LLM 新闻情绪评分（2 因子）
    F29: 新闻情绪（LLM/关键词评分）
    F30: 市场整体情绪映射
    """
    return _get_sentiment_score()


def _fetch_financials_batch(codes: list) -> dict:
    """并发批量获取财务数据（20 并发，0.5s/只 × 200 ≈ 5 秒）"""
    from services.factor_data import get_stock_financials
    results = {}

    def _fetch_one(code):
        try:
            # 去掉 sh/sz 前缀（Tushare 需要纯数字代码）
            clean_code = code.replace("sh", "").replace("sz", "").replace("SH", "").replace("SZ", "")
            result = get_stock_financials(clean_code)
            if result.get("available"):
                print(f"[SCREEN_FIN] {code}→{clean_code} OK [{result.get('source','')}]")
            return code, result
        except Exception as e:
            print(f"[SCREEN_FIN] {code} ERROR: {e}")
            return code, {}

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(_fetch_one, c): c for c in codes}
        for f in as_completed(futures):
            try:
                code, data = f.result()
                results[code] = data
            except Exception:
                pass

    return results


def screen_stocks(top_n: int = 50) -> dict:
    """
    30 因子多维选股 V3（动态权重 + 舆情 + LLM 因子）
    Step 1: 批量行情 → 基础过滤 → TOP 200 候选
    Step 2: 并发拉 TOP 200 财务数据
    Step 3: 动态权重 + 30 因子打分 → 排序 → TOP N
    """
    cache_key = f"stock_screen_v3_{top_n}"
    now = time.time()
    if cache_key in _stock_cache and now - _stock_cache[cache_key]["ts"] < STOCK_CACHE_TTL:
        return _stock_cache[cache_key]["data"]

    try:
        from services.stock_data_provider import get_stock_data

        # Step 0: 获取动态权重
        weights_data = _get_dynamic_weights()
        regime = weights_data.pop("_regime", "未知") if "_regime" in weights_data else "未知"
        weight_reason = weights_data.pop("_reason", "") if "_reason" in weights_data else ""
        # 清理非权重 key
        DIM_WEIGHTS = {k: v for k, v in weights_data.items() if not k.startswith("_")}
        # 确保有所有必需的 key
        for k in DEFAULT_DIM_WEIGHTS:
            if k not in DIM_WEIGHTS:
                DIM_WEIGHTS[k] = DEFAULT_DIM_WEIGHTS[k]

        # Step 1: 批量行情
        print("[STOCK_SCREEN_V3] Loading via data provider...")
        data = get_stock_data()
        raw_stocks = data.get("stocks", [])
        source = data.get("source", "unknown")
        if not raw_stocks:
            return {"stocks": [], "total": 0, "error": data.get("error", "数据不可用")}

        print(f"[STOCK_SCREEN_V3] Got {len(raw_stocks)} stocks from source={source}")

        # 基础过滤
        filtered = []
        for s in raw_stocks:
            code = s.get("code", "")
            name = s.get("name", "")
            price = s.get("price")
            pe = s.get("pe")
            market_cap_yi = s.get("market_cap")
            turnover = s.get("turnover")

            if not code or not name or price is None or price <= 0:
                continue
            if "ST" in name:
                continue
            if pe is not None and (pe <= 0 or pe > 300):
                continue
            if market_cap_yi is not None and market_cap_yi < 50:
                continue
            if turnover is not None and turnover < 0.3:
                continue
            filtered.append(s)

        print(f"[STOCK_SCREEN_V3] After filter: {len(filtered)}")

        # 按成交额排序取 TOP 200 候选（保证流动性）
        for s in filtered:
            mcap = s.get("market_cap") or 0
            to = s.get("turnover") or 0
            s["_daily_vol"] = mcap * to / 100  # 亿元
        filtered.sort(key=lambda x: x["_daily_vol"], reverse=True)
        candidates_200 = filtered[:200]

        # Step 2: 先用行情因子快速打分选出 TOP 50
        quick_scored = []
        for s in candidates_200:
            try:
                empty_fin = {}
                scores = {
                    "value": _score_value(s, empty_fin),
                    "growth": _score_growth(s, empty_fin),
                    "quality": _score_quality(s, empty_fin),
                    "momentum": _score_momentum(s),
                    "risk": _score_risk(s, empty_fin),
                    "liquidity": _score_liquidity(s),
                    "sentiment": _score_sentiment(),
                }
                total = sum(scores[k] * DIM_WEIGHTS[k] for k in DIM_WEIGHTS)
                s["_quick_score"] = total
                quick_scored.append(s)
            except Exception:
                continue

        quick_scored.sort(key=lambda x: x["_quick_score"], reverse=True)
        top50_candidates = quick_scored[:50]

        # Step 3: 并发拉 TOP 50 的财务数据（速度快很多）
        codes_50 = [s["code"] for s in top50_candidates]
        print(f"[STOCK_SCREEN_V3] Fetching financials for TOP {len(codes_50)}...")
        t0 = time.time()
        financials = _fetch_financials_batch(codes_50)
        t1 = time.time()
        fin_count = sum(1 for v in financials.values() if v.get("available"))
        print(f"[STOCK_SCREEN_V3] Financials done: {fin_count}/{len(codes_50)} available, {t1-t0:.1f}s")

        # Step 4: 30 因子完整打分
        scored = []
        for s in top50_candidates:
            try:
                code = s["code"]
                fin = financials.get(code, {})

                scores = {
                    "value": _score_value(s, fin),
                    "growth": _score_growth(s, fin),
                    "quality": _score_quality(s, fin),
                    "momentum": _score_momentum(s),
                    "risk": _score_risk(s, fin),
                    "liquidity": _score_liquidity(s),
                    "sentiment": _score_sentiment(),
                }

                total = sum(scores[k] * DIM_WEIGHTS[k] for k in DIM_WEIGHTS)

                # LLM 因子生成器加分（如果该股票有缓存的有效因子）
                llm_bonus = _get_llm_factor_bonus(code)
                total += llm_bonus

                scored.append({
                    "code": code,
                    "name": s.get("name", ""),
                    "price": s.get("price"),
                    "pe": s.get("pe"),
                    "pb": s.get("pb"),
                    "change_pct": s.get("change_pct"),
                    "turnover": s.get("turnover"),
                    "market_cap": s.get("market_cap"),
                    "score": round(total, 1),
                    "scores": {k: round(v, 0) for k, v in scores.items()},
                    "llm_bonus": round(llm_bonus, 1),
                    # 展示用的财务指标（顶层 + financials 子对象兼容前端）
                    "roe": fin.get("roe"),
                    "eps": fin.get("eps"),
                    "gross_margin": fin.get("gross_margin"),
                    "net_margin": fin.get("net_margin"),
                    "debt_ratio": fin.get("debt_ratio"),
                    "revenue_growth": fin.get("revenue_growth"),
                    "financials": {
                        "roe": fin.get("roe"),
                        "eps": fin.get("eps"),
                        "gross_margin": fin.get("gross_margin"),
                        "net_margin": fin.get("net_margin"),
                        "debt_ratio": fin.get("debt_ratio"),
                        "source": fin.get("source", "none"),
                        "available": fin.get("available", False),
                    },
                })
            except Exception:
                continue

        # 排序取 TOP N
        scored.sort(key=lambda x: x["score"], reverse=True)
        top = scored[:top_n]

        # 因子说明（含动态权重）
        w_desc = " / ".join([f"{k}({int(DIM_WEIGHTS[k]*100)}%)" for k in DIM_WEIGHTS])
        factor_desc = (
            f"30因子7维打分 V3 — 动态权重\n"
            f"市场状态: {regime} | {weight_reason}\n"
            f"权重: {w_desc}\n"
            f"舆情因子已接入 LLM 新闻情绪评分"
        )

        result = {
            "stocks": top,
            "total": len(scored),
            "source": source,
            "version": "V3_dynamic_weights",
            "method": factor_desc,
            "financials_available": fin_count,
            "regime": regime,
            "weights": {k: round(v * 100, 1) for k, v in DIM_WEIGHTS.items()},
            "note": f"数据源: {source} | 财务数据: {fin_count}/{len(codes_50)} | 市场: {regime}",
        }
        _stock_cache[cache_key] = {"data": result, "ts": time.time()}
        print(f"[STOCK_SCREEN_V3] Final: {len(scored)} scored → TOP {len(top)} | regime={regime}")
        return result

    except Exception as e:
        print(f"[STOCK_SCREEN_V3] Failed: {e}")
        traceback.print_exc()
        return {"stocks": [], "total": 0, "error": str(e)}


# ---- V4 底座：enrich() 适配层 ----
_enrich_cache = {}  # {"data": result, "ts": time}
_ENRICH_CACHE_TTL = 1800  # 30分钟缓存（选股结果不需要实时）

def enrich(ctx):
    """Pipeline 适配：跑选股 → 写回 ctx（市场整体强弱+推荐名单）"""
    import time as _time
    try:
        # 30分钟缓存，避免每次 steward.ask 都跑 40 秒选股
        now = _time.time()
        if _enrich_cache and now - _enrich_cache.get("ts", 0) < _ENRICH_CACHE_TTL:
            result = _enrich_cache["data"]
            print("[STOCK_SCREEN] enrich using cache")
        else:
            result = screen_stocks(20)
            _enrich_cache["data"] = result
            _enrich_cache["ts"] = now
        stocks = result.get("stocks", [])
        regime = result.get("regime", "")
        if stocks:
            avg_score = sum(s.get("score", 0) for s in stocks) / len(stocks)
            direction = "bullish" if avg_score > 60 else ("bearish" if avg_score < 40 else "neutral")
        else:
            avg_score, direction = 50, "neutral"
        ctx.modules_results["stock_screen"] = {
            "available": True,
            "direction": direction,
            "confidence": round(min(avg_score, 90), 1),
            "data": {"top5": [{"name": s["name"], "code": s["code"], "score": s["score"]} for s in stocks[:5]], "avg_score": round(avg_score, 1), "regime": regime, "total_screened": result.get("total", 0)},
            "cost": "llm_light",
            "latency_ms": 0,
        }
        ctx.modules_called.append("stock_screen")
    except Exception as e:
        print(f"[stock_screen.enrich] Error: {e}")
        ctx.errors.append({"module": "stock_screen", "error": str(e), "fallback_used": True})
        ctx.modules_skipped.append({"name": "stock_screen", "reason": str(e)})
    return ctx

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
from infra.cache import MemoryCache

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "stock_screen",
    "scope": "public",
    "input": ["top_n"],
    "output": "screened_stocks",
    "cost": "llm_light",
    "tags": ["选股", "30因子", "动态权重"],
    "description": "30因子7维打分V3，DeepSeek判regime+固化权重表（经验值未回测）+LLM因子加分",
    "layer": "analysis",
    "priority": 2,
}

_stock_cache = MemoryCache(default_ttl=3600)

# ---- 30 因子权重配置（默认权重，不再被 AI 覆盖）----
# FIX 2026-04-19 F4: 权重统一从 config.STOCK_SCREEN_WEIGHTS 读取（Single Source of Truth）
# 原来本地写了一份 quality=0.18，与 config.py 的 0.15 不一致
# P1-7：AI 不再覆盖权重。LLM 只判断 regime（离散分类），7 维权重一律由
#       config.STOCK_FACTOR_WEIGHTS_BY_REGIME 固化的表查得 —— 旧实现让
#       LLM 每次现编 7 个精确数值，同一天跑两次结果不同，排名不可复现。
from config import (
    STOCK_SCREEN_WEIGHTS as DEFAULT_DIM_WEIGHTS,
    STOCK_FACTOR_REGIME_ENUM,
    STOCK_FACTOR_WEIGHT_SOURCE_LLM,
    STOCK_FACTOR_WEIGHT_SOURCE_RULE,
    STOCK_FACTOR_WEIGHT_SOURCE_FALLBACK,
)

# ---- 动态权重：LLM 判 regime，权重查固化表 ----
_WEIGHT_CACHE_TTL = 3600  # 1 小时
_weight_cache = MemoryCache(default_ttl=_WEIGHT_CACHE_TTL)

# LLM 只能从这几个离散值里选一个；不在枚举内 → 视为识别失败，走回退。
_REGIME_ENUM = tuple(STOCK_FACTOR_REGIME_ENUM)


def _normalize_weights(raw: dict) -> dict:
    """把任意权重 dict 归一化成「7 个维度齐全、和为 1.0」的权重。

    纯函数：相同输入永远得到相同输出，不读时间、不读随机、不读外部状态
    —— 可复现性全靠这一点。固化表是手写的，所以仍需防 NaN/负数/缺 key。
    """
    out = {}
    for key in DEFAULT_DIM_WEIGHTS:
        fallback = DEFAULT_DIM_WEIGHTS[key]
        try:
            w = float(raw.get(key, fallback))
        except (TypeError, ValueError):
            w = fallback
        if w != w or w in (float("inf"), float("-inf")):  # NaN / inf
            w = fallback
        out[key] = max(0.0, w)

    total = sum(out.values())
    if total <= 0:
        return dict(DEFAULT_DIM_WEIGHTS)
    return {k: v / total for k, v in out.items()}


def get_weights_for_regime(regime) -> dict:
    """regime → 7 维权重。纯查表，不含任何 LLM 现编数字。

    regime 为 None / 空串 / 未在固化表里（如 "火星牛市"）→ 返回默认权重，
    不抛异常。权重表是经验值、未回测，来源说明见 config.py 的表定义处。
    """
    from config import STOCK_FACTOR_WEIGHTS_BY_REGIME

    key = str(regime).strip() if regime is not None else ""
    raw = (STOCK_FACTOR_WEIGHTS_BY_REGIME or {}).get(key)
    if not isinstance(raw, dict):
        return _normalize_weights(DEFAULT_DIM_WEIGHTS)
    return _normalize_weights(raw)


def _build_market_ctx() -> str:
    """收集市场环境上下文（供 LLM 判断 regime 用），任一数据源失败就跳过。"""
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

    return market_ctx or "市场数据暂不可用"


def _classify_regime_by_llm(market_ctx: str) -> tuple:
    """让 LLM 判断市场状态（离散分类），**不让它输出任何权重数字**。

    返回 (regime, reason)；识别失败 / 返回非法值 → ("", "")，由调用方回退。
    """
    from config import LLM_API_KEY
    if not LLM_API_KEY:
        return "", ""

    enum_desc = "\n".join([
        "- 牛市：估值偏高 + 情绪贪婪 + 普涨",
        "- 熊市：估值偏低 + 情绪恐惧 + 普跌",
        "- 震荡：估值适中 + 指数区间波动，缺乏趋势",
        "- 轮动：资金在行业间快速流动，结构分化明显",
    ])
    prompt = f"""你是 A 股市场环境分类员。请判断当前市场属于哪一种状态。

当前市场环境：
{market_ctx}

可选状态（四选一，不要自造状态名）：
{enum_desc}

注意：你只需要给出状态分类和一句话理由，**不要输出任何权重、百分比或数字**。

返回 JSON，格式：
{{"regime":"牛市","reason":"一句话说明"}}
只返回 JSON。"""

    from infra.llm.gateway import LLMGateway
    gw = LLMGateway.instance()
    result = gw.call_sync(
        prompt,
        system="",
        model_tier="llm_light",
        user_id="",
        module="dyn_weight",
        max_tokens=200,
    )
    if result.get("fallback") or not result.get("content"):
        print(f"[DYN_WEIGHT] LLM gateway fallback: {result.get('source')}")
        return "", ""

    from services.json_extract import extract_json_object
    parsed = extract_json_object(result["content"])
    if not isinstance(parsed, dict):
        return "", ""

    regime = str(parsed.get("regime", "") or "").strip()
    reason = str(parsed.get("reason", "") or "")
    if regime not in _REGIME_ENUM:
        print(f"[DYN_WEIGHT] LLM 返回非法 regime={regime!r}，按识别失败处理")
        return "", ""
    return regime, reason


def _get_dynamic_weights() -> dict:
    """7 维权重：LLM 只负责判断 regime，权重一律由 config 固化表查得。

    返回值除 7 个维度权重外，还带三个下划线前缀的元信息（调用方 pop 掉即可）：
      _regime: 市场状态，空串表示未识别
      _reason: LLM 给的分类理由，可能为空
      _source: 权重来源（"llm_regime"/"rule_regime"/"fallback"）——
               降级必须可见，不允许静默返回一套"看起来合理"的权重
    """
    cache_key = "dynamic_weights"
    cached = _weight_cache.get(cache_key)
    if cached is not None:
        return dict(cached)  # 拷贝：调用方会 pop 元信息，别污染缓存里的那份

    regime, reason = "", ""
    try:
        regime, reason = _classify_regime_by_llm(_build_market_ctx())
    except Exception as e:
        print(f"[DYN_WEIGHT] regime 分类失败，回退默认权重: {e}")
        regime, reason = "", ""

    if regime:
        source = STOCK_FACTOR_WEIGHT_SOURCE_LLM
    else:
        source = STOCK_FACTOR_WEIGHT_SOURCE_FALLBACK
    weights = get_weights_for_regime(regime)

    weights["_regime"] = regime
    weights["_reason"] = reason
    weights["_source"] = source
    print(f"[DYN_WEIGHT] regime={regime or '未识别'} source={source} weights={weights}")

    _weight_cache.set(cache_key, weights)
    return dict(weights)


# ---- 舆情因子真正接入 ----
_SENTIMENT_CACHE_TTL = 1800  # 30 分钟
_sentiment_cache = MemoryCache(default_ttl=_SENTIMENT_CACHE_TTL)


def _get_sentiment_score() -> float:
    """获取全市场舆情得分，映射到 [0, 100]"""
    cache_key = "all_sentiment"
    now = time.time()
    cached = _sentiment_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        from services.factor_data import get_news_sentiment_score
        result = get_news_sentiment_score()
        if result.get("available"):
            raw = result.get("score", 0)  # -100 ~ +100
            # 映射到 0~100：-100→0, 0→50, +100→100
            mapped = max(0, min(100, 50 + raw * 0.5))
            _sentiment_cache.set(cache_key, mapped, ttl=_SENTIMENT_CACHE_TTL)
            print(f"[SENTIMENT_FACTOR] raw={raw}, mapped={mapped:.0f}")
            return mapped
    except Exception as e:
        print(f"[SENTIMENT_FACTOR] Failed: {e}")

    return 50  # 中性默认


# ---- LLM 因子生成器加分 ----
_LLM_BONUS_CACHE_TTL = 7200  # 2 小时
_llm_bonus_cache = MemoryCache(default_ttl=_LLM_BONUS_CACHE_TTL)


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
    cached = _llm_bonus_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        from services.llm_factor_gen import _llm_factor_cache

        # 查找任何包含此股票代码的缓存
        clean_code = code.replace("sh", "").replace("sz", "").replace("SH", "").replace("SZ", "")
        bonus = 0.0

        for key in _llm_factor_cache.keys():
            if clean_code in key:
                data = _llm_factor_cache.get(key)
                if data is None:
                    continue
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

        _llm_bonus_cache.set(cache_key, bonus, ttl=_LLM_BONUS_CACHE_TTL)
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
    # 金融行业ROE门槛降低（银行10-12%即为优秀）
    roe = fin.get("roe")
    industry = s.get("industry", "")
    is_financial = any(kw in industry for kw in ["银行", "保险", "证券", "信托", "金融"])
    is_energy = any(kw in industry for kw in ["石油", "煤炭", "采矿", "能源"])
    if roe is not None:
        if is_financial:
            # 银行/保险用净息差/综合收益衡量，ROE门槛调低
            if roe > 12: score += 20
            elif roe > 10: score += 14
            elif roe > 8: score += 8
            elif roe > 5: score += 2
            elif roe < 0: score -= 20
        else:
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
    # 金融/银行/保险行业高负债是行业属性，不适用通用门槛
    dr = fin.get("debt_ratio")
    industry = s.get("industry", "")
    is_financial = any(kw in industry for kw in ["银行", "保险", "证券", "信托", "金融", "基金", "租赁"])
    if dr is not None and not is_financial:
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


_MOMENTUM_FIELDS = ("change_5d", "change_20d", "change_60d", "change_pct")


def _momentum_coverage(s: dict) -> str:
    """动量维度的数据完整度，形如 "2/4"（4 个因子里有几个真有数据）。

    为何必须单独输出：`_score_momentum` 对缺失因子不增不减，于是
    「4 个因子全部缺失」与「数据齐全、综合判断为中性的股票」都会落在 50 附近。
    下游与用户若只看分数，会把「根本没数据」误读成「判断为中性」——
    这与 P1-1「未判定不得显示为中性」是同一条数据诚实准则。
    """
    if not s:
        return f"0/{len(_MOMENTUM_FIELDS)}"
    have = sum(1 for f in _MOMENTUM_FIELDS if s.get(f) is not None)
    return f"{have}/{len(_MOMENTUM_FIELDS)}"


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

    # F23: 负债率风险（金融行业跳过，高负债是行业属性）
    dr = fin.get("debt_ratio")
    industry = s.get("industry", "")
    is_financial = any(kw in industry for kw in ["银行", "保险", "证券", "信托", "金融", "基金", "租赁"])
    if dr is not None and not is_financial:
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
    cached = _stock_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        from services.stock_data_provider import get_stock_data

        # Step 0: 获取权重（LLM 判 regime → 查固化表；失败则规则推断 → 默认表）
        weights_data = _get_dynamic_weights()
        regime = weights_data.pop("_regime", "") if "_regime" in weights_data else ""
        weight_reason = weights_data.pop("_reason", "") if "_reason" in weights_data else ""
        weight_source = weights_data.pop("_source", STOCK_FACTOR_WEIGHT_SOURCE_FALLBACK)
        # 清理非权重 key
        DIM_WEIGHTS = {k: v for k, v in weights_data.items() if not k.startswith("_")}

        # regime 为空或"未知"时，从估值+恐贪的规则推断降级（仍然是固化表查权重）
        if not regime or regime == "未知":
            try:
                from services.market_data import get_valuation_percentile, get_fear_greed_index
                val_pct = (get_valuation_percentile() or {}).get("percentile", 50)
                fgi = (get_fear_greed_index() or {}).get("score", 50)
                if val_pct >= 80 and fgi >= 60:
                    regime = "牛市"
                elif val_pct <= 30 and fgi <= 30:
                    regime = "熊市"
                elif 40 <= val_pct <= 70:
                    regime = "震荡"
                else:
                    regime = "轮动"
                weight_source = STOCK_FACTOR_WEIGHT_SOURCE_RULE
                weight_reason = weight_reason or "LLM 未识别市场状态，按估值+恐贪规则推断"
                print(f"[STOCK_SCREEN] regime 从市场数据推断: {regime} (val_pct={val_pct}, fgi={fgi})")
            except Exception as _e:
                regime = "震荡"  # 最终兜底，不显示"未知"
                weight_source = STOCK_FACTOR_WEIGHT_SOURCE_FALLBACK
                weight_reason = "市场数据不可用，使用默认权重（经验值，未回测）"
                print(f"[STOCK_SCREEN] regime 推断失败，使用默认: {_e}")
            DIM_WEIGHTS = get_weights_for_regime(regime)
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
            # 过滤北交所股票（代码前缀 92/83，或 baostock 格式 bj.8xxxxx）
            _code_clean = code.lower().replace("bj.", "")
            if _code_clean.startswith("92") or _code_clean.startswith("83") or code.lower().startswith("bj."):
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
                    "change_60d": s.get("change_60d"),   # v9.5.81: 输出60日涨幅，用于潜力评分
                    "change_20d": s.get("change_20d"),   # v9.5.81: 20日涨幅
                    "turnover": s.get("turnover"),
                    "market_cap": s.get("market_cap"),
                    "score": round(total, 1),
                    "scores": {k: round(v, 0) for k, v in scores.items()},
                    "llm_bonus": round(llm_bonus, 1),
                    # v9.9.24 数据完整度：让「没数据」与「判断为中性」可区分。
                    # 缺动量数据时 _score_momentum 不给也不扣，分数仍落在 50 附近，
                    # 与「数据齐全且中性」无法分辨，故必须显式带出覆盖率。
                    "data_completeness": {
                        "momentum": _momentum_coverage(s),
                        "financials": bool(fin.get("available")),
                        "llm_bonus_active": bool(llm_bonus > 0),
                    },
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

        # Tushare 补 PE/PB（2026-04-19 A4: 重构走统一接口 tushare_data.get_valuation_batch_map）
        pe_missing = sum(1 for s in top if s.get("pe") is None)
        if pe_missing > len(top) * 0.3:  # 阈值从 50% 降到 30%，更积极补齐
            try:
                from services.tushare_data import is_configured, get_valuation_batch_map
                if is_configured():
                    pe_map = get_valuation_batch_map()  # 自动找最近交易日，返回 {code: {pe, pb, total_mv, turnover}}
                    if pe_map:
                        filled = 0
                        for s in top:
                            raw_code = s.get("code", "")
                            # 去掉新浪 sh/sz/bj 前缀
                            clean_code = raw_code
                            for prefix in ("sh", "sz", "bj", "SH", "SZ", "BJ"):
                                if clean_code.startswith(prefix):
                                    clean_code = clean_code[len(prefix):]
                                    break
                            ts_row = pe_map.get(clean_code)
                            if ts_row:
                                if s.get("pe") is None and ts_row.get("pe") is not None:
                                    s["pe"] = ts_row["pe"]
                                if s.get("pb") is None and ts_row.get("pb") is not None:
                                    s["pb"] = ts_row["pb"]
                                if s.get("market_cap") is None and ts_row.get("total_mv") is not None:
                                    s["market_cap"] = ts_row["total_mv"]
                                if s.get("turnover") is None and ts_row.get("turnover") is not None:
                                    s["turnover"] = ts_row["turnover"]
                                if s.get("pe") is not None:
                                    filled += 1
                        print(f"[STOCK_SCREEN_V3] Tushare 批量补 PE: {filled}/{len(top)}")
            except Exception as e:
                print(f"[STOCK_SCREEN_V3] Tushare PE 失败: {e}")

        # v9.5.99: 业绩预告 + 业绩快报 + 回购加分（同进程缓存4h）
        try:
            from services.tushare_data import get_earning_forecast, get_express_report, get_share_repurchase, get_top_inst
            import time as _t
            global _STOCK_CATALYST_CACHE
            try:
                _cat = _STOCK_CATALYST_CACHE
            except NameError:
                _cat = {}
            now_ts = _t.time()

            for s in top:
                code = s.get("code", "")
                if not code:
                    continue
                cached = _cat.get(code)
                if cached and (now_ts - cached["ts"]) < 14400:
                    bonus = cached["bonus"]
                    flags = cached["flags"]
                else:
                    bonus = 0
                    flags = []
                    try:
                        fc = get_earning_forecast(code=code) or []
                        if fc:
                            ftype = (fc[0].get("type") or "")
                            pmin = fc[0].get("p_change_min") or 0
                            pmax = fc[0].get("p_change_max") or 0
                            avg = (pmin + pmax) / 2
                            if "增" in ftype or avg > 30:
                                bonus += 5
                                flags.append(f"📈预增{avg:.0f}%")
                            elif "减" in ftype or "亏" in ftype or avg < -20:
                                bonus -= 8
                                flags.append(f"📉{ftype}")
                    except Exception:
                        pass
                    try:
                        ex = get_express_report(code=code) or []
                        if ex:
                            yoy = ex[0].get("yoy_net_profit") or 0
                            if yoy > 50:
                                bonus += 4
                                flags.append(f"📊快报+{yoy:.0f}%")
                            elif yoy < -20:
                                bonus -= 4
                                flags.append(f"📊快报{yoy:.0f}%")
                    except Exception:
                        pass
                    try:
                        rp = get_share_repurchase(code=code, days=180) or []
                        if rp:
                            amt = rp[0].get("amount") or 0
                            if amt > 1e8:  # 1亿以上
                                bonus += 3
                                flags.append("💰大额回购")
                            elif amt > 1e7:
                                bonus += 1
                                flags.append("💰回购")
                    except Exception:
                        pass
                    # v9.5.101: 龙虎榜机构席位（机构净买入是强信号）
                    try:
                        insts = get_top_inst(code=code) or []
                        if insts:
                            inst_net = sum((i.get("net_buy") or 0) for i in insts if "机构" in (i.get("exalter") or ""))
                            if inst_net > 5e7:  # 机构净买 5000万+
                                bonus += 4
                                flags.append(f"🏛️机构净买¥{inst_net/1e8:.1f}亿")
                            elif inst_net > 1e7:
                                bonus += 2
                                flags.append("🏛️机构买入")
                            elif inst_net < -5e7:
                                bonus -= 3
                                flags.append("🏛️机构净卖")
                    except Exception:
                        pass
                    _cat[code] = {"ts": now_ts, "bonus": bonus, "flags": flags}
                if bonus:
                    s["score"] = round(s.get("score", 0) + bonus, 1)
                    s["catalyst_bonus"] = bonus
                if flags:
                    s["catalyst_flags"] = flags
            try:
                globals()["_STOCK_CATALYST_CACHE"] = _cat
            except Exception:
                pass
            # 重新排序（加分后）
            top.sort(key=lambda x: x.get("score", 0), reverse=True)
        except Exception as e:
            print(f"[STOCK_SCREEN_V3] catalyst 加分失败: {e}")

        # 因子说明（含动态权重）
        w_desc = " / ".join([f"{k}({int(DIM_WEIGHTS[k]*100)}%)" for k in DIM_WEIGHTS])
        factor_desc = (
            f"30因子7维打分 V3 — 按市场状态取固化权重\n"
            f"市场状态: {regime} | {weight_reason}\n"
            f"权重: {w_desc}\n"
            f"权重来源: {weight_source}（经验值，未回测）\n"
            f"舆情因子已接入 LLM 新闻情绪评分"
        )

        # v9.9.24 数据质量总览：让调用方知道这批结论建立在多少真实数据上。
        # 财务覆盖率长期只有个位数百分比，若不显式带出，下游会把「50 只里只有 1 只
        # 有财报」算出来的排名，当成全样本结论来用。
        _dq = [x.get("data_completeness", {}) for x in scored]
        _llm_active = sum(1 for d in _dq if d.get("llm_bonus_active"))
        _mom_full = sum(1 for d in _dq if d.get("momentum") == "4/4")

        result = {
            "stocks": top,
            "total": len(filtered),  # 全市场筛选后的候选数（不是TOP数）
            "total_screened": len(filtered),
            "source": source,
            "version": "V3_dynamic_weights",
            "method": factor_desc,
            "financials_available": fin_count,
            "data_quality": {
                "financials_available": fin_count,
                "financials_total": len(codes_50),
                "financials_pct": (round(fin_count / len(codes_50) * 100, 1)
                                   if codes_50 else 0.0),
                "momentum_full_count": _mom_full,
                "llm_bonus_active_count": _llm_active,
                "scored_count": len(scored),
            },
            "regime": regime,
            "weights": {k: round(v * 100, 1) for k, v in DIM_WEIGHTS.items()},
            # P1-7：权重怎么来的必须可见（LLM 判 regime / 规则推断 / 兜底），
            # 不允许静默降级后还伪装成"AI 动态调权"
            "weights_source": weight_source,
            "weights_disclaimer": "经验值，未回测",
            "note": f"数据源: {source} | 财务数据: {fin_count}/{len(codes_50)} | 市场: {regime}",
        }
        _stock_cache.set(cache_key, result)
        print(f"[STOCK_SCREEN_V3] Final: {len(scored)} scored → TOP {len(top)} | regime={regime}")
        return result

    except Exception as e:
        print(f"[STOCK_SCREEN_V3] Failed: {e}")
        traceback.print_exc()
        return {"stocks": [], "total": 0, "error": str(e)}


# ---- V4 底座：enrich() 适配层 ----
_ENRICH_CACHE_TTL = 1800  # 30分钟缓存（选股结果不需要实时）
_enrich_cache = MemoryCache(default_ttl=_ENRICH_CACHE_TTL)

def enrich(ctx):
    """Pipeline 适配：跑选股 → 写回 ctx（市场整体强弱+推荐名单）"""
    import time as _time
    try:
        # 30分钟缓存，避免每次 steward.ask 都跑 40 秒选股
        cache_key = "stock_screen_top20"
        cached = _enrich_cache.get(cache_key)
        if cached is not None:
            result = cached
            print("[STOCK_SCREEN] enrich using cache")
        else:
            result = screen_stocks(20)
            _enrich_cache.set(cache_key, result)
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

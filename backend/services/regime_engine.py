"""
钱袋子 — 市场状态分类引擎 (Regime Engine)
职责：判断当前市场属于哪种状态，供 PipelineRunner 动态选择管线
来源：老师方案 — 4 类市场状态

4 种 Regime:
  trending_bull  — 趋势牛市（均线多头排列 + 温和波动）
  oscillating    — 震荡市（均线纠缠 + 中等波动）
  high_vol_bear  — 高波熊市（均线空头 + 高波动）
  rotation       — 轮动市（大盘横盘 + 板块轮动）

输入：沪深300 近期行情（日K）
输出：{regime, confidence, params, description}
"""
import time
from datetime import datetime
from infra.cache import MemoryCache

MODULE_META = {
    "name": "regime_engine",
    "scope": "public",
    "input": [],
    "output": "regime",
    "cost": "cpu",
    "tags": ["regime", "market_state"],
    "description": "市场状态4类分类（趋势牛/震荡/高波熊/轮动），供Pipeline选管线",
    "layer": "analysis",
    "priority": 0,  # 最高优先，Pipeline第一步
}


# ---- 缓存（Regime 30 分钟更新一次足够）----
_REGIME_TTL = 1800  # 30 min
_regime_cache = MemoryCache(default_ttl=_REGIME_TTL)  # {"regime_analysis": {"data": result, "ts": float}}


def classify(force: bool = False) -> dict:
    """
    分类当前市场状态
    
    Returns:
        {
            "regime": "trending_bull|oscillating|high_vol_bear|rotation",
            "confidence": 0-100,
            "confidence_note": "人话说明该置信度是否在本regime的设计区间内",
            "params": {ma5, ma20, ma60, volatility_20d, return_20d, ...},
            "description": "人话描述",
            "timestamp": ISO时间
        }
    """
    now = time.time()
    cached = _regime_cache.get("regime")
    if not force and cached is not None:
        return cached
    
    try:
        params = _get_market_params()
        regime, confidence, desc = _classify_regime(params)
        
        # V6 Phase 2: 地缘风险覆盖 — severity≥4 → 强制 cautious
        geo_override = False
        geo_severity = 0
        try:
            from services.geopolitical import get_geopolitical_events
            geo_data = get_geopolitical_events()
            geo_severity = geo_data.get("max_severity", 0)
            if geo_severity >= 4:
                geo_override = True
                original_regime = regime
                regime = "high_vol_bear"  # 强制切到 cautious pipeline
                confidence = max(confidence, 70)
                geo_cat = geo_data.get("top_category", "地缘风险")
                desc = f"⚠️ 地缘风险评估（{geo_cat}），切换谨慎模式"
                print(f"[REGIME] 地缘覆盖: severity={geo_severity}, {original_regime} → high_vol_bear")
        except Exception as e:
            print(f"[REGIME] 地缘检查失败(不影响原判定): {e}")

        # FIX 2026-09: 周度自检 LLM 审计层曾把"Regime判断confidence=55"误判为
        # "功能风险"——因为 use_cases/self_audit.py 只给13维信号的低置信度配了
        # 归因豁免说明，完全没提 Regime。根因是 Regime 的置信度公式本身就按
        # 市场状态设计了不同区间（震荡市 40-80，趋势牛/高波熊 更高，轮动市
        # 40-85），这是设计如此，不是计算bug。跟 signal.py 的 confidence_note
        # 模式保持一致：把归因说明放进数据源头，而不是只在 LLM prompt 里加
        # 一条静态规则——这样任何下游消费方（审计/前端/推送）都能拿到同样的
        # 归因上下文，不需要各自维护一份"哪些区间正常"的知识。
        confidence_note = _confidence_note_for_regime(regime, confidence, geo_override)

        result = {
            "regime": regime,
            "confidence": confidence,
            "confidence_note": confidence_note,
            "params": _clean_params(params),
            "description": desc,
            "timestamp": datetime.now().isoformat(),
            "geo_override": geo_override,
            "geo_severity": geo_severity,
        }
    except Exception as e:
        # 降级：数据获取失败时默认震荡
        result = {
            "regime": "oscillating",
            "confidence": 30,
            "confidence_note": "数据获取失败降级为默认震荡判断，confidence=30为保底值，不代表真实市场状态的置信度评估",
            "params": {},
            "description": f"数据获取失败({e})，默认震荡",
            "timestamp": datetime.now().isoformat(),
        }
    
    _regime_cache.set("regime", result, ttl=_REGIME_TTL)
    return result


# Regime 置信度的设计区间（下限, 上限, 场景说明）。
# 这些区间直接对应 _classify_regime() 里各分支的打分/clamp 逻辑：
#   trending_bull: bull_score>=60 才判定，min(bull_score, 95) → [60, 95]
#   high_vol_bear: bear_score>=55 才判定，min(bear_score, 95) → [55, 95]
#   rotation:      rotation_score>=65 才判定，min(rotation_score, 85) → [65, 85]
#   oscillating:   max(40, 100-bull_score-bear_score)，再 min(.., 80) → [40, 80]
# 只要 confidence 落在对应区间内，就是"设计如此"，不是异常；只有明显超出
# 区间（比如震荡市却给出 <20 或 >95）才需要人工排查计算逻辑。
_REGIME_CONFIDENCE_DESIGN_RANGES: dict = {
    "trending_bull": (60, 95, "趋势牛市：均线多头排列+20日涨幅+低波动多重信号叠加，置信度设计区间较高"),
    "high_vol_bear": (55, 95, "高波熊市：均线空头排列+高波动+显著下跌信号叠加，置信度设计区间较高"),
    # QA 复核发现（2026-09）：rotation_score 的4个加分项 {30,25,25,20} 子集和
    # 永远凑不出65（可能值仅 0,20,25,30,45,50,55,70,75,80,100），触发阈值
    # rotation_score>=65 时实际最小可达值是70，65-69是不可达死区。下限改成
    # 70精确匹配 _classify_regime() 的真实可达范围，不再是宽松的近似值。
    "rotation":      (70, 85, "轮动市：大盘横盘+低波动+缩量+均线纠缠多重弱信号叠加，置信度设计上限受控在85"),
    "oscillating":   (40, 80, "震荡市（默认状态）：多空力量接近、缺乏单边趋势信号，置信度天然落在40-80区间，这是市场胶着期的正常特征，不代表判断力弱"),
}


def _confidence_note_for_regime(regime: str, confidence: float, geo_override: bool = False) -> str:
    """给 Regime 判断的置信度附加人话归因说明。

    目的：解决"LLM/人类看到一个孤零零的置信度数字就误判为功能风险"的问题——
    跟 services/signal.py 里 confidence_note 的设计模式保持一致，在数据源头
    就把"这个置信度是否符合设计预期"说清楚，而不是依赖 prompt 里的静态规则。
    """
    if geo_override:
        return "地缘风险覆盖强制切换为高波熊市（cautious模式），confidence已被提升至≥70作为风控保护性判断，非常规市场状态分类，不适用下方设计区间校验"

    lo, hi, scene_desc = _REGIME_CONFIDENCE_DESIGN_RANGES.get(regime, (0, 100, ""))
    if lo <= confidence <= hi:
        return f"{scene_desc}（设计区间{lo}-{hi}，当前{confidence}属于正常范围）"
    return (
        f"⚠️ confidence={confidence} 超出 {regime} 的设计区间（{lo}-{hi}），"
        f"可能存在打分逻辑异常，建议排查 _classify_regime() 的分支打分"
    )


def _get_market_params() -> dict:
    """获取沪深300关键参数（Tushare优先 → AKShare/baostock降级）"""
    import pandas as pd

    df = None

    # 方案 A：Tushare index_daily
    try:
        from services.tushare_data import is_configured, get_index_daily as ts_index
        if is_configured():
            rows = ts_index(ts_code="000300.SH", days=150)
            if rows and len(rows) >= 60:
                df = pd.DataFrame(rows)
                df["close"] = pd.to_numeric(df["close"], errors="coerce")
    except Exception as e:
        print(f"[REGIME] Tushare index_daily failed: {e}")

    # 方案 B：AKShare/baostock
    if df is None or len(df) < 60:
        try:
            from infra.data_source.market.stocks import get_index_daily
            df = get_index_daily(symbol="sh000300")
        except Exception as e:
            print(f"[REGIME] AKShare/baostock failed: {e}")

    if df is None or len(df) < 60:
        return _fallback_params()

    df = df.tail(120).copy()
    close = df["close"].astype(float)

    try:
        # 均线
        ma5 = close.rolling(5).mean().iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1]
        ma60 = close.rolling(60).mean().iloc[-1]
        current = close.iloc[-1]

        # 波动率（20日年化）
        returns = close.pct_change().dropna()
        vol_20d = returns.tail(20).std() * (252 ** 0.5) * 100  # 年化百分比

        # 近20日收益率
        ret_20d = (close.iloc[-1] / close.iloc[-21] - 1) * 100 if len(close) > 21 else 0

        # 近5日收益率
        ret_5d = (close.iloc[-1] / close.iloc[-6] - 1) * 100 if len(close) > 6 else 0

        # 均线排列方向
        ma_bullish = ma5 > ma20 > ma60  # 多头排列
        ma_bearish = ma5 < ma20 < ma60  # 空头排列

        # 价格相对均线位置
        above_ma60 = current > ma60

        # 成交量变化（近5日 vs 近20日均量）
        if "volume" in df.columns:
            vol = df["volume"].astype(float)
            vol_ratio = vol.tail(5).mean() / vol.tail(20).mean() if vol.tail(20).mean() > 0 else 1
        elif "vol" in df.columns:
            vol = df["vol"].astype(float)
            vol_ratio = vol.tail(5).mean() / vol.tail(20).mean() if vol.tail(20).mean() > 0 else 1
        else:
            vol_ratio = 1.0

        return {
            "current": round(current, 2),
            "ma5": round(ma5, 2),
            "ma20": round(ma20, 2),
            "ma60": round(ma60, 2),
            "volatility_20d": round(vol_20d, 1),
            "return_20d": round(ret_20d, 2),
            "return_5d": round(ret_5d, 2),
            "ma_bullish": ma_bullish,
            "ma_bearish": ma_bearish,
            "above_ma60": above_ma60,
            "vol_ratio": round(vol_ratio, 2),
        }
    except Exception as e:
        print(f"[REGIME] 数据计算失败: {e}")
        return _fallback_params()


def _fallback_params() -> dict:
    """降级参数"""
    return {
        "current": 0, "ma5": 0, "ma20": 0, "ma60": 0,
        "volatility_20d": 20, "return_20d": 0, "return_5d": 0,
        "ma_bullish": False, "ma_bearish": False, "above_ma60": False,
        "vol_ratio": 1.0,
    }


def _clean_params(params: dict) -> dict:
    """清洗 numpy 类型为 Python 原生类型（防止 JSON 序列化失败）"""
    cleaned = {}
    for k, v in params.items():
        if hasattr(v, 'item'):  # numpy scalar
            cleaned[k] = v.item()
        elif isinstance(v, (bool,)):
            cleaned[k] = bool(v)
        elif isinstance(v, (int, float, str)):
            cleaned[k] = v
        else:
            try:
                cleaned[k] = float(v)
            except (TypeError, ValueError):
                cleaned[k] = str(v)
    return cleaned


def _classify_regime(params: dict) -> tuple:
    """
    核心分类逻辑
    
    规则（来源：老师方案）：
    1. 趋势牛: 均线多头排列 + 波动率<25% + 20日收益>3%
    2. 高波熊: 均线空头排列 + 波动率>30% + 20日收益<-5%
    3. 轮动: 波动率<20% + |20日收益|<3% + 成交量萎缩
    4. 震荡: 其余情况（默认状态）
    
    Returns: (regime_name, confidence, description)
    """
    vol = params.get("volatility_20d", 20)
    ret_20 = params.get("return_20d", 0)
    ret_5 = params.get("return_5d", 0)
    ma_bull = params.get("ma_bullish", False)
    ma_bear = params.get("ma_bearish", False)
    above_60 = params.get("above_ma60", False)
    vol_ratio = params.get("vol_ratio", 1.0)
    
    # ---- 趋势牛市 ----
    bull_score = 0
    if ma_bull:
        bull_score += 40
    if above_60:
        bull_score += 15
    if ret_20 > 3:
        bull_score += 20
    if ret_5 > 1:
        bull_score += 10
    if vol < 25:
        bull_score += 15
    
    if bull_score >= 60:
        conf = min(bull_score, 95)
        return "trending_bull", conf, f"趋势牛市（均线多头+20日涨{ret_20:.1f}%+波动{vol:.0f}%）"
    
    # ---- 高波熊市 ----
    bear_score = 0
    if ma_bear:
        bear_score += 40
    if not above_60:
        bear_score += 15
    if ret_20 < -5:
        bear_score += 25
    if vol > 30:
        bear_score += 20
    
    if bear_score >= 55:
        conf = min(bear_score, 95)
        return "high_vol_bear", conf, f"高波熊市（均线空头+20日跌{ret_20:.1f}%+波动{vol:.0f}%）"
    
    # ---- 轮动市 ----
    rotation_score = 0
    if abs(ret_20) < 3:
        rotation_score += 30
    if vol < 20:
        rotation_score += 25
    if vol_ratio < 0.8:
        rotation_score += 25  # 缩量
    if not ma_bull and not ma_bear:
        rotation_score += 20
    
    if rotation_score >= 65:
        conf = min(rotation_score, 85)
        return "rotation", conf, f"轮动市（大盘横盘{ret_20:.1f}%+低波动{vol:.0f}%+缩量{vol_ratio:.1f}x）"
    
    # ---- 震荡市（默认）----
    conf = max(40, 100 - bull_score - bear_score)
    return "oscillating", min(conf, 80), f"震荡市（均线纠缠+波动{vol:.0f}%+20日{ret_20:+.1f}%）"


def get_pipeline_for_regime(regime: str) -> str:
    """Regime → 管线映射"""
    mapping = {
        "trending_bull": "default",
        "oscillating": "default",
        "high_vol_bear": "cautious",
        "rotation": "fast",
    }
    return mapping.get(regime, "default")


def enrich(ctx):
    """Pipeline Layer1: 注入 Regime 到 DecisionContext"""
    result = classify()
    ctx.regime = result["regime"]
    ctx.regime_confidence = result["confidence"]
    ctx.regime_params = result.get("params", {})
    ctx.regime_description = result.get("description", "")
    # V6 Phase 2: 地缘覆盖标记
    ctx.regime_params["geo_override"] = result.get("geo_override", False)
    ctx.regime_params["geo_severity"] = result.get("geo_severity", 0)
    return ctx

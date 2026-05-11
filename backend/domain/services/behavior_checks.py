"""
Behavior Checks -- 7 种行为偏差子检测函数
==========================================
从 behavior_detector.py 拆分，负责具体的偏差模式识别逻辑。

每个检测函数签名统一：
  (transactions: list[SimpleTransaction], market_data: Optional[MarketDataProtocol])
  -> Optional[BehaviorPattern]

设计文档：docs/design/m7-plus/05-batch-m8-dynamic-threshold.md
不变式 #8：domain/services 之间禁止互相 import
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from domain.services.behavior_detector import (
    BehaviorPattern,
    MarketDataProtocol,
    SimpleTransaction,
    CHASING_RSI_THRESHOLD,
    CHASING_GAIN_THRESHOLD,
    FOMO_MARKET_GAIN,
    OVER_TRADING_HOLDING_DAYS,
    HIGH_PE_PERCENTILE,
    ANCHORING_MONTHS,
)


# ============================================================
# 辅助函数
# ============================================================

def _ratio_to_severity(ratio: float) -> str:
    """根据占比判定严重程度"""
    if ratio >= 0.80:
        return "severe"
    elif ratio >= 0.50:
        return "moderate"
    else:
        return "mild"


# ============================================================
# 1. 追高倾向
# ============================================================

def detect_chasing_high(
    transactions: list[SimpleTransaction],
    market_data: Optional[MarketDataProtocol],
) -> Optional[BehaviorPattern]:
    """追高倾向：买入时点 RSI>70 或近 20 日涨幅>15%"""
    buy_trades = [t for t in transactions if t.direction == "buy"]
    if not buy_trades or market_data is None:
        return None

    evidence: list[str] = []
    for t in buy_trades:
        rsi = market_data.get_rsi(t.code, t.trade_date)
        gain = market_data.get_recent_gain(t.code, t.trade_date, days=20)

        is_chasing = False
        if rsi is not None and rsi > CHASING_RSI_THRESHOLD:
            is_chasing = True
        if gain is not None and gain > CHASING_GAIN_THRESHOLD:
            is_chasing = True

        if is_chasing:
            evidence.append(t.trade_id)

    if not evidence:
        return None

    ratio = len(evidence) / len(buy_trades)
    severity = _ratio_to_severity(ratio)

    return BehaviorPattern(
        pattern_type="chasing_high",
        severity=severity,
        evidence_count=len(evidence),
        total_relevant=len(buy_trades),
        ratio=ratio,
        description=(
            f"你 {len(buy_trades)} 次买入中 {len(evidence)} 次发生在"
            f" RSI>{CHASING_RSI_THRESHOLD:.0f} 或近20日涨幅>{CHASING_GAIN_THRESHOLD:.0%} 时"
        ),
        supporting_trades=evidence,
    )


# ============================================================
# 2. 止损不一致
# ============================================================

def detect_stop_loss_inconsistent(
    transactions: list[SimpleTransaction],
    market_data: Optional[MarketDataProtocol],
) -> Optional[BehaviorPattern]:
    """止损不一致：浮亏<5% 卖出次数 > 浮亏>20% 卖出次数"""
    sell_trades = [t for t in transactions if t.direction == "sell"]
    if not sell_trades or market_data is None:
        return None

    small_loss_sells = 0  # 浮亏 <5% 时卖出
    big_loss_holds = 0    # 浮亏 >20% 时仍持有（未卖）
    evidence: list[str] = []

    for t in sell_trades:
        pnl = market_data.get_unrealized_pnl(t.code, t.trade_date, t.avg_cost)
        if pnl is None:
            continue
        if -0.05 < pnl < 0:
            small_loss_sells += 1
            evidence.append(t.trade_id)
        elif pnl < -0.20:
            big_loss_holds += 1

    if small_loss_sells == 0:
        return None
    if small_loss_sells <= big_loss_holds:
        return None

    total = small_loss_sells + big_loss_holds
    ratio = small_loss_sells / total if total > 0 else 0
    severity = _ratio_to_severity(ratio)

    return BehaviorPattern(
        pattern_type="stop_loss_inconsistent",
        severity=severity,
        evidence_count=small_loss_sells,
        total_relevant=total,
        ratio=ratio,
        description=f"你倾向于小亏就卖（{small_loss_sells}次浮亏<5%卖出）、大亏装死",
        supporting_trades=evidence,
    )


# ============================================================
# 3. 确认偏误
# ============================================================

def detect_confirmation_bias(
    transactions: list[SimpleTransaction],
    market_data: Optional[MarketDataProtocol],
) -> Optional[BehaviorPattern]:
    """确认偏误：80% 以上交易集中在同一行业"""
    trades_with_industry = [t for t in transactions if t.industry]
    if len(trades_with_industry) < 5:
        return None

    # 统计各行业交易次数
    industry_counts: dict[str, int] = {}
    for t in trades_with_industry:
        industry_counts[t.industry] = industry_counts.get(t.industry, 0) + 1

    total = len(trades_with_industry)
    top_industry = max(industry_counts, key=lambda k: industry_counts[k])
    top_count = industry_counts[top_industry]
    ratio = top_count / total

    if ratio < 0.80:
        return None

    evidence = [t.trade_id for t in trades_with_industry if t.industry == top_industry]
    severity = _ratio_to_severity(ratio)

    return BehaviorPattern(
        pattern_type="confirmation_bias",
        severity=severity,
        evidence_count=top_count,
        total_relevant=total,
        ratio=ratio,
        description=f"你 {ratio:.0%} 交易集中在{top_industry}板块",
        supporting_trades=evidence,
    )


# ============================================================
# 4. FOMO 交易
# ============================================================

def detect_fomo(
    transactions: list[SimpleTransaction],
    market_data: Optional[MarketDataProtocol],
) -> Optional[BehaviorPattern]:
    """FOMO 交易：大涨日（沪深300 单日>2%）交易占比 >0.5"""
    if not transactions or market_data is None:
        return None

    fomo_trades: list[str] = []
    for t in transactions:
        daily_return = market_data.get_csi300_daily_return(t.trade_date)
        if daily_return is not None and daily_return > FOMO_MARKET_GAIN:
            fomo_trades.append(t.trade_id)

    if not fomo_trades:
        return None

    ratio = len(fomo_trades) / len(transactions)
    if ratio <= 0.5:
        return None

    severity = _ratio_to_severity(ratio)

    return BehaviorPattern(
        pattern_type="fomo",
        severity=severity,
        evidence_count=len(fomo_trades),
        total_relevant=len(transactions),
        ratio=ratio,
        description=(
            f"你 {len(transactions)} 次交易中有 {len(fomo_trades)} 次"
            f"发生在市场大涨当天（沪深300日涨幅>{FOMO_MARKET_GAIN:.0%}）"
        ),
        supporting_trades=fomo_trades,
    )


# ============================================================
# 5. 过度交易
# ============================================================

def detect_over_trading(
    transactions: list[SimpleTransaction],
    market_data: Optional[MarketDataProtocol],
) -> Optional[BehaviorPattern]:
    """过度交易：平均持仓周期 <30 天"""
    # 配对买卖计算持仓周期
    buy_dates: dict[str, list[date]] = {}  # code → [buy_dates]
    sell_dates: dict[str, list[date]] = {}  # code → [sell_dates]

    for t in transactions:
        if t.direction == "buy":
            buy_dates.setdefault(t.code, []).append(t.trade_date)
        else:
            sell_dates.setdefault(t.code, []).append(t.trade_date)

    holding_periods: list[int] = []

    for code, sells in sell_dates.items():
        buys = buy_dates.get(code, [])
        if not buys:
            continue
        # 简单配对：每次卖出匹配最近的一次买入
        available_buys = sorted(buys)
        for sell_date in sorted(sells):
            matched_buy = None
            for b in available_buys:
                if b <= sell_date:
                    matched_buy = b
            if matched_buy:
                days = (sell_date - matched_buy).days
                holding_periods.append(days)
                available_buys.remove(matched_buy)

    if not holding_periods:
        return None

    avg_days = sum(holding_periods) / len(holding_periods)
    if avg_days >= OVER_TRADING_HOLDING_DAYS:
        return None

    # 所有交易都是证据
    evidence = [t.trade_id for t in transactions]
    ratio = 1.0 if avg_days < OVER_TRADING_HOLDING_DAYS else 0.0
    severity = "severe" if avg_days < 15 else ("moderate" if avg_days < 25 else "mild")

    return BehaviorPattern(
        pattern_type="over_trading",
        severity=severity,
        evidence_count=len(holding_periods),
        total_relevant=len(transactions),
        ratio=ratio,
        description=f"你平均持仓 {avg_days:.0f} 天（阈值 {OVER_TRADING_HOLDING_DAYS} 天）",
        supporting_trades=evidence[:10],  # 最多 10 条证据
    )


# ============================================================
# 6. 高位加仓
# ============================================================

def detect_high_pe_adding(
    transactions: list[SimpleTransaction],
    market_data: Optional[MarketDataProtocol],
) -> Optional[BehaviorPattern]:
    """高位加仓：加仓时点 PE 历史分位>70% 占比 >0.5"""
    buy_trades = [t for t in transactions if t.direction == "buy"]
    if not buy_trades or market_data is None:
        return None

    high_pe_buys: list[str] = []
    valid_buys = 0

    for t in buy_trades:
        pe_pct = market_data.get_pe_percentile(t.code, t.trade_date)
        if pe_pct is None:
            continue
        valid_buys += 1
        if pe_pct > HIGH_PE_PERCENTILE:
            high_pe_buys.append(t.trade_id)

    if valid_buys == 0:
        return None

    ratio = len(high_pe_buys) / valid_buys
    if ratio <= 0.5:
        return None

    severity = _ratio_to_severity(ratio)

    return BehaviorPattern(
        pattern_type="high_pe_adding",
        severity=severity,
        evidence_count=len(high_pe_buys),
        total_relevant=valid_buys,
        ratio=ratio,
        description=f"你 {ratio:.0%} 的加仓发生在估值高位（PE分位>{HIGH_PE_PERCENTILE:.0%}）",
        supporting_trades=high_pe_buys,
    )


# ============================================================
# 7. 锚定效应
# ============================================================

def detect_anchoring(
    transactions: list[SimpleTransaction],
    market_data: Optional[MarketDataProtocol],
) -> Optional[BehaviorPattern]:
    """锚定效应：买入均价作为心理锚点，3 个月未重估"""
    # 按证券代码分组，找到首次买入后 3 个月内无操作的
    code_trades: dict[str, list[SimpleTransaction]] = {}
    for t in transactions:
        code_trades.setdefault(t.code, []).append(t)

    anchoring_codes: list[str] = []
    total_codes = 0

    for code, trades in code_trades.items():
        buys = [t for t in trades if t.direction == "buy"]
        if not buys:
            continue
        total_codes += 1

        # 最后一次买入日期
        last_buy_date = max(t.trade_date for t in buys)
        # 最后一次操作日期（买或卖）
        last_action_date = max(t.trade_date for t in trades)

        # 如果最后操作就是最后买入，且距今超过 3 个月
        if last_action_date == last_buy_date:
            today = date.today()
            months_since = (today - last_buy_date).days / 30
            if months_since >= ANCHORING_MONTHS:
                anchoring_codes.append(code)

    if not anchoring_codes or total_codes == 0:
        return None

    ratio = len(anchoring_codes) / total_codes
    severity = _ratio_to_severity(ratio)

    # 构造描述
    sample = anchoring_codes[:3]
    names = "、".join(
        next((t.name for t in transactions if t.code == c), c) for c in sample
    )

    return BehaviorPattern(
        pattern_type="anchoring",
        severity=severity,
        evidence_count=len(anchoring_codes),
        total_relevant=total_codes,
        ratio=ratio,
        description=(
            f"你对 {names} 等 {len(anchoring_codes)} 只标的的买入均价"
            f"作为心理锚点已超 {ANCHORING_MONTHS} 个月"
        ),
        supporting_trades=[
            t.trade_id for t in transactions if t.code in anchoring_codes
        ][:10],
    )


# ============================================================
# 导出
# ============================================================

__all__ = [
    "detect_chasing_high",
    "detect_stop_loss_inconsistent",
    "detect_confirmation_bias",
    "detect_fomo",
    "detect_over_trading",
    "detect_high_pe_adding",
    "detect_anchoring",
]

"""
Seed Decision Reviews — Mock data script for M5 monthly report development
===========================================================================
Generates 60-80 fake decision review records covering 2 months (2026-03 to 2026-04).

Requirements:
  - Time span: 2 months (2026-03-01 to 2026-04-30)
  - Record count: 60-80
  - Reason distribution: 情绪面(跟风/涨得好) 40%, 基本面 30%, 其他 30%
  - Quality score: 情绪面平均分 < 50, 基本面 > 70
  - Loss correlation: 勾选"涨得好"的记录中, 60% 后续亏损 > 10%

Usage:
  cd backend && python -m scripts.seed_decision_reviews
  OR: python scripts/seed_decision_reviews.py

Design doc: docs/design/07-decision-guard.md §四
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

# Ensure backend/ is importable
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Import from domain
from domain.models.decision import BuyReasonCategory, SignalLevel


# ============================================================
# Configuration
# ============================================================

SEED_USER_ID = "test_user_report"
TARGET_COUNT = 72  # Between 60-80
START_DATE = datetime(2026, 3, 1)
END_DATE = datetime(2026, 4, 30)

# Asset universe for variety
ASSETS = [
    ("600519", "贵州茅台"),
    ("000858", "五粮液"),
    ("601318", "中国平安"),
    ("000001", "平安银行"),
    ("510300", "沪深300ETF"),
    ("159915", "创业板ETF"),
    ("600036", "招商银行"),
    ("000651", "格力电器"),
    ("002415", "海康威视"),
    ("601012", "隆基绿能"),
    ("000333", "美的集团"),
    ("600030", "中信证券"),
]

ACTIONS = ["buy", "add", "buy", "add", "buy"]  # weighted towards buy/add


# ============================================================
# Reason Templates (by category)
# ============================================================

# Emotional/Follow reasons (40% of all reasons) — red/yellow signals
# Weighted towards momentum_chase to ensure meaningful correlation data
EMOTIONAL_REASONS = [
    {"reason_id": "momentum_chase", "category": "emotional", "signal": "red"},
    {"reason_id": "momentum_chase", "category": "emotional", "signal": "red"},
    {"reason_id": "momentum_chase", "category": "emotional", "signal": "red"},
    {"reason_id": "hot_news", "category": "follow", "signal": "red"},
    {"reason_id": "fomo", "category": "follow", "signal": "red"},
    {"reason_id": "averaging_down", "category": "emotional", "signal": "yellow"},
]

# Fundamental reasons (30%) — green signals
FUNDAMENTAL_REASONS = [
    {"reason_id": "valuation_low", "category": "fundamental", "signal": "green"},
    {"reason_id": "sector_logic", "category": "fundamental", "signal": "green"},
]

# Other/Technical reasons (30%) — green signals
OTHER_REASONS = [
    {"reason_id": "allocation_gap", "category": "technical", "signal": "green"},
    {"reason_id": "dca_plan", "category": "other", "signal": "green"},
]


# ============================================================
# Score computation (simplified, mirrors decision_guard_service)
# ============================================================

def compute_mock_score(reasons: List[Dict[str, str]], context: Dict[str, Any]) -> Dict[str, Any]:
    """Simplified quality score computation for mock data.

    Emotional reasons → low score (< 50 avg target)
    Fundamental reasons → high score (> 70 avg target)
    """
    red_count = sum(1 for r in reasons if r.get("signal") == "red")
    yellow_count = sum(1 for r in reasons if r.get("signal") == "yellow")
    has_green = any(r.get("signal") == "green" for r in reasons)
    has_fundamental = any(r.get("category") == "fundamental" for r in reasons)

    # Base scores
    if has_fundamental and red_count == 0:
        # Fundamental-driven: score 65-90
        reason_clarity = random.randint(18, 25)
        info_source = random.randint(18, 25)
        risk_awareness = random.randint(15, 25)
        time_horizon = random.randint(15, 25)
    elif red_count > 0:
        # Emotional-driven: score 20-50
        reason_clarity = random.randint(8, 15)
        info_source = random.randint(5, 12)
        risk_awareness = random.randint(10, 18)
        time_horizon = random.randint(5, 12)
    else:
        # Mixed/other: score 50-70
        reason_clarity = random.randint(12, 20)
        info_source = random.randint(12, 20)
        risk_awareness = random.randint(12, 20)
        time_horizon = random.randint(12, 20)

    raw_total = reason_clarity + info_source + risk_awareness + time_horizon
    penalty = red_count * 10 + yellow_count * 5
    total = max(10, raw_total - penalty)

    # Grade
    if total >= 80:
        grade = "excellent"
    elif total >= 60:
        grade = "good"
    elif total >= 40:
        grade = "mediocre"
    else:
        grade = "poor"

    return {
        "reason_clarity": reason_clarity,
        "info_source": info_source,
        "risk_awareness": risk_awareness,
        "time_horizon": time_horizon,
        "total": total,
        "red_flags": red_count,
        "yellow_flags": yellow_count,
        "grade": grade,
    }


# ============================================================
# Result tracking (loss correlation)
# ============================================================

def generate_result(reasons: List[Dict[str, str]]) -> Dict[str, Any]:
    """Generate mock trade result with loss correlation.

    Rule: momentum_chase → 60% chance of loss > 10%
          fundamental → 70% chance of profit
          other → 50/50
    """
    has_momentum = any(r.get("reason_id") == "momentum_chase" for r in reasons)
    has_fundamental = any(r.get("category") == "fundamental" for r in reasons)
    has_fomo = any(r.get("reason_id") == "fomo" for r in reasons)

    if has_momentum:
        # 60% chance of significant loss
        if random.random() < 0.60:
            return_pct = random.uniform(-25.0, -10.5)
            win = False
        else:
            return_pct = random.uniform(-5.0, 15.0)
            win = return_pct > 0
    elif has_fomo:
        # 50% chance of loss
        if random.random() < 0.50:
            return_pct = random.uniform(-20.0, -5.0)
            win = False
        else:
            return_pct = random.uniform(-2.0, 12.0)
            win = return_pct > 0
    elif has_fundamental:
        # 70% chance of profit
        if random.random() < 0.70:
            return_pct = random.uniform(2.0, 25.0)
            win = True
        else:
            return_pct = random.uniform(-12.0, -1.0)
            win = False
    else:
        # 50/50
        return_pct = random.uniform(-15.0, 20.0)
        win = return_pct > 0

    return {
        "tracked": True,
        "return_pct": round(return_pct, 2),
        "win": win,
        "days_held": random.randint(7, 90),
    }


# ============================================================
# Main generator
# ============================================================

def generate_reviews() -> List[Dict[str, Any]]:
    """Generate TARGET_COUNT mock decision review records."""
    reviews: List[Dict[str, Any]] = []
    random.seed(42)  # Reproducible

    # Distribute records across 2 months
    total_days = (END_DATE - START_DATE).days
    dates = sorted([
        START_DATE + timedelta(days=random.randint(0, total_days))
        for _ in range(TARGET_COUNT)
    ])

    # Ensure distribution: 40% emotional, 30% fundamental, 30% other
    n_emotional = int(TARGET_COUNT * 0.40)  # ~29
    n_fundamental = int(TARGET_COUNT * 0.30)  # ~22
    n_other = TARGET_COUNT - n_emotional - n_fundamental  # ~21

    # Create category assignments (shuffled)
    categories = (
        ["emotional"] * n_emotional
        + ["fundamental"] * n_fundamental
        + ["other"] * n_other
    )
    random.shuffle(categories)

    for i, (date, category) in enumerate(zip(dates, categories)):
        # Pick asset
        asset_code, asset_name = random.choice(ASSETS)
        action = random.choice(ACTIONS)
        amount = random.randint(5000, 100000)

        # Generate reasons based on category
        reasons: List[Dict[str, str]] = []
        if category == "emotional":
            # Primary: 1-2 emotional/follow reasons
            primary = random.choice(EMOTIONAL_REASONS)
            reasons.append(primary)
            # Sometimes add a second emotional reason
            if random.random() < 0.3:
                secondary = random.choice(EMOTIONAL_REASONS)
                if secondary["reason_id"] != primary["reason_id"]:
                    reasons.append(secondary)
            # Occasionally has a green reason too (mixed)
            if random.random() < 0.2:
                reasons.append(random.choice(OTHER_REASONS))
        elif category == "fundamental":
            # Primary: 1-2 fundamental reasons
            reasons.append(random.choice(FUNDAMENTAL_REASONS))
            if random.random() < 0.4:
                reasons.append(random.choice(FUNDAMENTAL_REASONS + OTHER_REASONS))
        else:
            # Other/technical reasons
            reasons.append(random.choice(OTHER_REASONS))
            if random.random() < 0.3:
                reasons.append(random.choice(FUNDAMENTAL_REASONS))

        # Compute quality score
        context = {
            "has_emergency_fund": random.random() < 0.8,
            "concentration_ok": random.random() < 0.7,
            "money_not_needed_3y": random.random() < 0.75,
            "days_since_last_trade": random.randint(5, 90),
        }
        quality_score = compute_mock_score(reasons, context)

        # Generate result (85% of records have result tracking, 95% for emotional)
        result: Dict[str, Any] = {}
        result_tracked = False
        track_chance = 0.95 if category == "emotional" else 0.80
        if random.random() < track_chance:
            result = generate_result(reasons)
            result_tracked = True

        # Build review record (matching decision_archive format)
        trade_time = date.strftime("%Y-%m-%dT%H:%M:%S")
        review_time = (date + timedelta(hours=random.randint(1, 24))).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )

        review: Dict[str, Any] = {
            "id": f"rev_mock_{i:03d}_{int(date.timestamp())}",
            "type": "review",
            "action": action,
            "summary": f"{action} {asset_name}({asset_code}) ¥{amount:.0f}",
            "asset_code": asset_code,
            "asset_name": asset_name,
            "amount": float(amount),
            "reasons": reasons,
            "quality_score": quality_score,
            "trade_time": trade_time,
            "time": trade_time,  # decision_archive uses 'time' as primary field
            "review_time": review_time,
            "notes": "",
            "result_tracked": result_tracked,
            "result": result,
        }

        reviews.append(review)

    return reviews


def save_reviews(reviews: List[Dict[str, Any]], user_id: str = SEED_USER_ID) -> str:
    """Save reviews to the user's decision archive file.

    Returns the file path where data was saved.
    """
    from config import DATA_DIR

    user_dir = Path(DATA_DIR) / user_id / "memory"
    user_dir.mkdir(parents=True, exist_ok=True)

    decisions_file = user_dir / "decisions.json"

    # Load existing decisions (if any)
    existing: List[Dict[str, Any]] = []
    if decisions_file.exists():
        try:
            existing = json.loads(decisions_file.read_text(encoding="utf-8"))
        except Exception:
            existing = []

    # Append new reviews
    existing.extend(reviews)

    # Write back
    decisions_file.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return str(decisions_file)


def print_stats(reviews: List[Dict[str, Any]]) -> None:
    """Print summary statistics of generated data."""
    print(f"\n{'='*60}")
    print(f"  Seed Decision Reviews — Mock Data Summary")
    print(f"{'='*60}\n")

    # Basic counts
    print(f"  Total records: {len(reviews)}")
    months: Dict[str, int] = {}
    for r in reviews:
        m = r["time"][:7]
        months[m] = months.get(m, 0) + 1
    for m, c in sorted(months.items()):
        print(f"    {m}: {c} records")

    # Reason distribution
    reason_cats: Dict[str, int] = {}
    total_reasons = 0
    for r in reviews:
        for reason in r["reasons"]:
            cat = reason.get("category", "other")
            reason_cats[cat] = reason_cats.get(cat, 0) + 1
            total_reasons += 1

    print(f"\n  Reason distribution (total {total_reasons} reasons):")
    for cat, count in sorted(reason_cats.items(), key=lambda x: -x[1]):
        pct = count / total_reasons * 100 if total_reasons else 0
        print(f"    {cat}: {count} ({pct:.1f}%)")

    # Emotional + follow combined
    emotional_follow = reason_cats.get("emotional", 0) + reason_cats.get("follow", 0)
    print(f"    [emotional+follow combined]: {emotional_follow} ({emotional_follow/total_reasons*100:.1f}%)")

    # Quality score by category
    print(f"\n  Average quality score by primary category:")
    cat_scores: Dict[str, List[int]] = {}
    for r in reviews:
        primary_cat = r["reasons"][0]["category"] if r["reasons"] else "other"
        score = r["quality_score"]["total"]
        cat_scores.setdefault(primary_cat, []).append(score)

    for cat, scores in sorted(cat_scores.items()):
        avg = sum(scores) / len(scores)
        print(f"    {cat}: avg={avg:.1f} (n={len(scores)})")

    # Loss correlation for momentum_chase
    momentum_reviews = [
        r for r in reviews
        if any(reason.get("reason_id") == "momentum_chase" for reason in r["reasons"])
    ]
    momentum_tracked = [r for r in momentum_reviews if r.get("result_tracked")]
    momentum_losses = [
        r for r in momentum_tracked
        if r.get("result", {}).get("return_pct", 0) < -10
    ]

    print(f"\n  Loss correlation ('涨得好' / momentum_chase):")
    print(f"    Total with momentum_chase: {len(momentum_reviews)}")
    print(f"    With result tracked: {len(momentum_tracked)}")
    print(f"    With loss > 10%: {len(momentum_losses)}")
    if momentum_tracked:
        loss_ratio = len(momentum_losses) / len(momentum_tracked)
        print(f"    Loss ratio: {loss_ratio:.1%}")

    print(f"\n{'='*60}\n")


# ============================================================
# Entry point
# ============================================================

def main() -> None:
    """Generate and save mock decision reviews."""
    reviews = generate_reviews()
    print_stats(reviews)

    filepath = save_reviews(reviews)
    print(f"  ✅ Saved {len(reviews)} reviews to: {filepath}")
    print(f"  User ID: {SEED_USER_ID}")
    print()


if __name__ == "__main__":
    main()

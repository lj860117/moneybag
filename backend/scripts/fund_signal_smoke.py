#!/usr/bin/env python3
"""
信号侦察 — 纯基金账户（C 方案）服务器冒烟脚本。

设计依据：docs/design/signal-scout-fund-account.md §5 T05
运行方式（服务器）：
    env $ENVSTR python backend/scripts/fund_signal_smoke.py [user_id]

职责：只读诊断，打印 P0-1 / P0-2 / P0-3 / P1-1 四类基金信号的产出状态，
    不触发任何推送、不写 push_log（避免冒烟误发企微 / 污染预算计数）。

示例输出结构：
    [P0-1 xray]        穿透覆盖 61.8% | 盲区 24.4% | 残差 13.8%
    [P0-2 manager]     快照冷启动（本次只建基线，不推送）
    [P0-3 drawdown]    最深相对成本 005698 -26.95% | 档位状态机冷启动
    [P1-1 dca]         触发日 2026-09-24 | 组合相对成本 -2.2%
"""
import argparse
import os
import sys
from datetime import date
from pathlib import Path

# ---- 路径：backend/scripts/fund_signal_smoke.py → parents[1]=backend, parents[2]=仓库根 ----
BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---- 加载 .env（与 fund_rank_build.py 同款兜底，crontab 未 set -a 时仍可用）----
_ENV_FILE = BACKEND_DIR / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k, _v.strip().strip('"').strip("'"))


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pct(numerator: float, denominator: float):
    if denominator <= 0:
        return None
    return round((numerator / denominator - 1.0) * 100.0, 2)


def _section(title: str) -> None:
    print(f"\n==== {title} ====")


def _smoke_xray(user_id: str, positions: list) -> None:
    """P0-1：穿透覆盖三分解 + 行业/个股 Top + 触发规则。"""
    from services.fund_signal import portfolio, sw_industry, xray
    from services.fund_signal import state

    codes = [p.code for p in positions]
    portfolios = portfolio.fetch_portfolios(codes)
    sw = sw_industry.load_sw_l2()
    sw_map = sw.get("map", {}) if isinstance(sw, dict) else {}
    sw_source = sw.get("source", "sw_l2") if isinstance(sw, dict) else "sw_l2"
    result = xray.compute_exposure(positions, portfolios, sw_map, sw_source)

    cov = result.coverage
    print(f"  持仓截止 {cov.end_date}（公告 {cov.ann_date}，滞后 {cov.lag_days} 天）")
    print(f"  穿透覆盖 {cov.penetrated_pct:.1f}% | 盲区 {cov.blind_pct:.1f}% "
          f"| 残差 {cov.residual_pct:.1f}% | 行业来源 {cov.industry_source}")
    if cov.blind_funds:
        blind = "、".join(f"{f['name']}({f['code']})" for f in cov.blind_funds)
        print(f"  盲区基金: {blind}")
    if result.industries:
        ind = result.industries[0]
        print(f"  行业 Top1: {ind.industry} {ind.exposure_pct:.2f}%")
    if result.stocks:
        s = result.stocks[0]
        print(f"  个股 Top1: {s.name or s.symbol}({s.symbol[:6]}) "
              f"{s.exposure_pct:.2f}% ← {s.fund_count} 只")
    rules = ",".join(result.triggered_rules) if result.triggered_rules else "无"
    st = state.load(user_id, state.XRAY_STATE)
    baseline = "已发基线" if st.get("baseline_sent") else "冷启动（将发基线体检）"
    print(f"  触发规则: {rules} | 状态: {baseline}")


def _smoke_manager(user_id: str, positions: list) -> None:
    """P0-2：经理快照 diff 状态（只读，不建新快照、不推送）。"""
    from services.fund_signal import state

    snap = state.load(user_id, state.MANAGER_SNAPSHOT)
    funds = snap.get("funds", {}) if isinstance(snap, dict) else {}
    if not funds:
        print("  快照: 冷启动（下次运行先建基线，本次不推送）")
        return
    tracked = sum(1 for recs in funds.values() if recs)
    print(f"  快照: 已跟踪 {tracked}/{len(positions)} 只基金的历史经理记录")


def _smoke_drawdown(user_id: str, positions: list) -> None:
    """P0-3：成本净值回撤口径 + 档位状态机状态（只读，不改档位）。"""
    from services.fund_signal import state
    from services.fund_signal.config import DRAWDOWN_RUNGS

    rows = []
    for p in positions:
        dd = _pct(p.unit_nav, p.cost_nav)
        if dd is None:
            continue
        rows.append((dd, p.code, p.name, p.weight_mv))
    rows.sort(key=lambda r: r[0])  # dd 升序：最深在前
    if not rows:
        print("  无有效 cost_nav/unit_nav，无法计算回撤")
        return

    dd, code, name, w = rows[0]
    deepest_rung = -1
    for i, rung in enumerate(DRAWDOWN_RUNGS):
        if dd <= rung:
            deepest_rung = i
    rung_label = f"档{deepest_rung + 1}" if deepest_rung >= 0 else "档外"
    print(f"  最深相对成本: {name}({code}) {dd:.2f}%（{rung_label}，占净值 {w:.1f}%）")

    st = state.load(user_id, state.DRAWDOWN_STATE)
    if st.get("cold_start"):
        print("  状态机: 冷启动（本次只写档位基线，不推送）")
    else:
        rungs = st.get("rungs", {}) if isinstance(st, dict) else {}
        print(f"  状态机: 已跟踪 {len(rungs)} 只基金的档位")


def _smoke_dca(user_id: str, positions: list) -> None:
    """P1-1：触发日 + 幂等 + 组合相对成本（只读）。"""
    from services.fund_signal import dca, state
    from services.fund_signal.config import DCA_TRIGGER_DAY

    today = date.today()
    trigger = dca.resolve_trigger_day(today)
    is_trigger = today == trigger
    month = today.strftime("%Y-%m")
    st = state.load(user_id, state.DCA_STATE)
    idempotent = st.get("last_push_month") == month

    total_mv = sum(p.market_value for p in positions)
    total_cost = sum(p.shares * p.cost_nav for p in positions)
    combo = _pct(total_mv, total_cost)
    combo_s = f"{combo:.2f}%" if combo is not None else "-"

    print(f"  触发日 {DCA_TRIGGER_DAY} 号 → 实际 {trigger.isoformat()} | "
          f"今日{'是' if is_trigger else '非'}触发日")
    print(f"  组合整体相对成本: {combo_s}（总市值/总成本-1，{len(positions)} 只）")
    print(f"  幂等: {'本月已推过，将跳过' if idempotent else '本月未推'} | 状态: "
          f"{'冷启动' if not st else '已就绪'}")


def run(user_id: str) -> int:
    from services.tushare_data import is_configured
    from services.fund_signal import portfolio

    print("=" * 60)
    print(f"信号侦察 — 纯基金账户（C 方案）冒烟 | user={user_id}")
    print("=" * 60)

    if not is_configured():
        print("❌ Tushare 未配置（.env 没有 TUSHARE_TOKEN / $ENVSTR 未注入）")
        return 1

    positions = portfolio.load_positions(user_id)
    if not positions:
        print(f"❌ 用户 {user_id} 无基金持仓或全部缺净值数据")
        return 2
    print(f"持仓: {len(positions)} 只基金，权重合计 "
          f"{sum(p.weight_mv for p in positions):.2f}%")

    # 四类信号分别只读诊断，任一失败不阻断其它（信号侦察是旁路）。
    _section("P0-1 组合穿透体检")
    try:
        _smoke_xray(user_id, positions)
    except Exception as e:
        print(f"  ❌ xray 失败: {e}")

    _section("P0-2 基金经理变更")
    try:
        _smoke_manager(user_id, positions)
    except Exception as e:
        print(f"  ❌ manager 失败: {e}")

    _section("P0-3 回撤档位状态机")
    try:
        _smoke_drawdown(user_id, positions)
    except Exception as e:
        print(f"  ❌ drawdown 失败: {e}")

    _section("P1-1 定投前瞻")
    try:
        _smoke_dca(user_id, positions)
    except Exception as e:
        print(f"  ❌ dca 失败: {e}")

    print("\n" + "=" * 60)
    print("✅ 冒烟完成（只读诊断，未推送、未写预算计数）")
    print("=" * 60)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="信号侦察纯基金账户冒烟")
    ap.add_argument("user_id", nargs="?", default="LeiJiang",
                    help="用户 ID（默认 LeiJiang，对应 /opt/moneybag/data/fund_holdings_LeiJiang.json）")
    args = ap.parse_args()
    return run(args.user_id)


if __name__ == "__main__":
    sys.exit(main())

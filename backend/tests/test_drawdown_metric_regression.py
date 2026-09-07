#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回撤指标「自相矛盾」回归测试 —— 2026-09-07 企微推送事故锁定。

事故现场（企微「钱袋子收盘复盘」推送原文，2026-09-07 21:02）::

    📊 本日异动明细（14 条）
    🔻 回撤类（8 条）
    • 浦银安盛全球智能科技(QDII)A(006555)：🔻 30日最大回撤 13.1%（07/27→07/29），
      距高点-5.7%，已从谷底反弹 +21.9%
    • 华夏先进制造龙头混合A(013107)：🔻 30日最大回撤 12.0%（08/17→09/04），
      距高点-12.0%，已从谷底反弹 +2.9%

两类根因（已定位，本文件只负责把它钉死在测试里）：

    A. reboundFromTrough 参照点错误（数据正确性 bug）
       fund_monitor.py::calc_risk_metrics() 旧实现用 min(navs) —— 窗口内**全局**
       最低点 —— 当谷底，而 maxDrawdown 用的是 468-482 行算出的 peak_idx/trough_idx。
       当全局最低点出现在回撤峰值**之前**（先涨后跌形态），"距高点 -X%"与
       "已从谷底反弹 +Y%"就参照了两个互不相关的时点，输出直接自相矛盾。
       013107 是硬证据：距高点 -12.0% == -max_dd，说明当前净值就是 09/04 那个回撤
       谷底本身，反弹幅度本该是 0，却被报成 +2.9%（那是拿 08/17 之前的 0.855 低点
       算出来的）。修复后 rebound 必须以 trough_idx 为参照，且要求 trough_idx >= peak_idx。

    B. 「N日最大回撤」标签语义错误
       标签取自 navWindowDays = len(navs)，那是**交易日条数**，不是自然日跨度。
       30 个交易日 ≈ 42 个自然日，于是推送里出现「30日最大回撤 …（07/27→07/29）」
       这种"窗口越界"的观感（相对 09/07，07/27 是 42 天前）。
       修复后改为直接展示真实统计区间起止日（07/27~09/04），不再出现裸条数标签。

    F. cron 引用了不存在的符号 → 功能静默失效
       stock_monitor_cron.py 收盘复盘第 4 段「🔔 持仓预警」里 import 的两个符号都不存在：
       `infra.data_source.fund_realtime`（模块全仓没有）和
       `services.fund_monitor.calc_fund_risk`（函数从未定义）。
       两个 ImportError 都被 `except Exception` 吞掉，该段对所有基金永远为空。
       本文件用 AST 静态扫描锁死这一类问题 —— 比逐个补用例更划算。

覆盖范围与函数全部取自事故前（git HEAD）已存在的实现，不在本文件里
依赖修复过程中新增的任何符号 —— 这样同一份文件既能跑修复前的红色基线，
也能跑修复后的绿色回归。

运行方式（本地，务必用这条）::

    cd backend && env -u PYTHONPATH \
        /Users/leijiang/.workbuddy/binaries/python/envs/default/bin/python \
        -m pytest tests/test_drawdown_metric_regression.py -v -rfEX

为什么必须 `env -u PYTHONPATH`：托管 python 的 PYTHONPATH 指向 WorkBuddy 的
sitecustomize.py shim，它会拦截 config.py 模块级的 USERS_DIR.mkdir()，把大量用例
打成 ERROR，看起来像代码坏了其实是跑法不对。（与 test_closing_review_leak_regression.py
同一坑，2026-09-04 踩过。）

CI / 服务器（venv 依赖齐全，没有 shim）直接跑即可::

    cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python \
        -m pytest tests/test_drawdown_metric_regression.py -q -rfEX
"""
from __future__ import annotations

import ast
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from services import fund_monitor
from services.fund_monitor import calc_risk_metrics, detect_fund_alerts

# 「N日最大回撤」这类裸条数标签 —— 修复后不该再出现
_BARE_WINDOW_LABEL_RE = re.compile(r"\d+\s*日最大回撤")

# 事故现场涉及的基金代码，仅用于让断言读起来贴近真实
_ACCIDENT_CODE = "013107"


# ============================================================
# 测试数据构造
# ============================================================

def _build_nav_series(start_date: str, values: list) -> list:
    """把 values 按「跳过周末」规则摊到从 start_date 起的交易日上。

    Args:
        start_date: 起始日期，格式 "YYYY-MM-DD"。
        values: 按顺序排列的净值序列。

    Returns:
        calc_risk_metrics 期望的入参形态：[{"date", "nav", "rate"}, ...]

    注意 rate 键**必须存在**：calc_risk_metrics 里是
    ``rates = [n["rate"] for n in nav_list if n["rate"] is not None]``，
    用的是下标访问而非 .get()，缺键会直接 KeyError。
    """
    out: list = []
    day = datetime.strptime(start_date, "%Y-%m-%d")
    for v in values:
        while day.weekday() >= 5:  # 5=周六 6=周日
            day += timedelta(days=1)
        out.append({"date": day.strftime("%Y-%m-%d"), "nav": v, "rate": 0.0})
        day += timedelta(days=1)
    return out


def _build_2026_09_07_accident_series() -> list:
    """复刻 013107 事故现场：先涨后跌，全局最低点在回撤峰值之前。

    形态（2026-07-27 ~ 2026-09-04，共 30 个交易日）：
        07/27 = 0.855（窗口全局最低，早于峰值）
        08/17 = 1.000（窗口最高 = 回撤峰值）
        09/04 = 0.880（回撤谷底 = 序列末点 = 当前净值）

    由此推出的事故三件套（与推送原文一致）：
        maxDrawdown      = (1.000 - 0.880) / 1.000 = 12.0%
        distFromPeak     = (0.880 - 1.000) / 1.000 = -12.0%
        旧 reboundFromTrough = (0.880 - 0.855) / 0.855 = +2.9%   ← 错的
        新 reboundFromTrough = (0.880 - 0.880) / 0.880 = 0.0     ← 对的
    """
    # idx 0..15：0.855 → 1.000（08/17 触顶）
    rise = [round(0.855 + (1.000 - 0.855) * i / 15, 6) for i in range(16)]
    # idx 16..29：1.000 → 0.880（09/04 见底，也是末点）
    fall = [round(1.000 + (0.880 - 1.000) * i / 14, 6) for i in range(1, 15)]
    return _build_nav_series("2026-07-27", rise + fall)


# ============================================================
# A. reboundFromTrough 必须以「回撤谷底」为参照
# ============================================================

def test_rebound_uses_drawdown_trough():
    """核心用例：当前净值就是回撤谷底时，不得报出"已从谷底反弹 +X%"。

    修复前：calc_risk_metrics 用 min(navs)=0.855（08/17 之前的低点）当谷底，
            返回 round(0.0292, 4) = 0.0292 → 推送里就是那个刺眼的"+2.9%"。
    修复后：谷底取 trough_idx 对应的 0.880（与 maxDrawdown 同一对时点），
            cur_nav == 谷底 → rebound 为 0.0（或 None）。
    """
    navs = _build_2026_09_07_accident_series()
    assert len(navs) == 30, f"构造的交易日数不对: {len(navs)}"

    risk = calc_risk_metrics(navs)

    # --- 回撤区间本身（这部分修复前后都应成立，防止改坏） ---
    assert risk["maxDrawdown"] == pytest.approx(0.12, abs=1e-4), (
        f"maxDrawdown 应为 12.0%，实际 {risk['maxDrawdown']}")
    assert risk["ddPeakDate"] == "08/17", f"回撤峰值日应为 08/17，实际 {risk['ddPeakDate']!r}"
    assert risk["ddTroughDate"] == "09/04", f"回撤谷底日应为 09/04，实际 {risk['ddTroughDate']!r}"

    # --- 距高点：相对窗口内全局最高点（1.000），与事故一致 ---
    assert risk["distFromPeak"] == pytest.approx(-0.12, abs=1e-4), (
        f"distFromPeak 应为 -12.0%，实际 {risk['distFromPeak']}")

    # --- 本条要锁的 bug：反弹幅度 ---
    rebound = risk["reboundFromTrough"]
    assert rebound is None or rebound == pytest.approx(0.0, abs=1e-4), (
        "当前净值就是 09/04 回撤谷底本身，反弹幅度必须是 0 或 None；\n"
        f"实际 reboundFromTrough={rebound!r}（修复前会是 0.0292，即推送里的 +2.9%）"
    )

    # --- 统计区间（B 依赖这两个字段） ---
    assert risk["navStartDate"] == "07/27", f"统计起始日应为 07/27，实际 {risk['navStartDate']!r}"
    assert risk["navEndDate"] == "09/04", f"统计截止日应为 09/04，实际 {risk['navEndDate']!r}"


def test_rebound_none_when_trough_precedes_peak():
    """谷底早于峰值（不构成一次回撤）时，不得拿别处的低点冒充谷底。

    单调递增序列 [1.0, 1.1, 1.2]：max_dd = 0，peak_idx = trough_idx = 0。
    修复前旧实现的 rebound = (1.2 - 1.0) / 1.0 = +20%，会输出
    「最大回撤 0.0% …已从谷底反弹 +20%」这种荒谬文案。
    """
    navs = _build_nav_series("2026-07-27", [1.0, 1.05, 1.1, 1.15, 1.2])

    risk = calc_risk_metrics(navs)
    assert risk["maxDrawdown"] == pytest.approx(0.0, abs=1e-4)

    rebound = risk["reboundFromTrough"]
    assert rebound is None or rebound == pytest.approx(0.0, abs=1e-4), (
        f"单调上升且无回撤时不应报出正反弹，实际 {rebound!r}")


def test_alert_text_has_no_self_contradiction():
    """推送文案不得出现「距高点 -X%」与「已从谷底反弹 +Y%」同时非零的自相矛盾。

    判据：当 distFromPeak ≈ -maxDrawdown（即当前净值正落在回撤谷底上）时，
    文案里不得出现「反弹 +非零值」。
    """
    navs = _build_2026_09_07_accident_series()
    risk = calc_risk_metrics(navs)
    alerts = detect_fund_alerts(_ACCIDENT_CODE, {}, risk)

    drawdown_alerts = [a for a in alerts if a.get("type") == "drawdown"]
    assert len(drawdown_alerts) == 1, f"应恰好产出 1 条回撤异动，实际 {len(drawdown_alerts)}"

    msg = drawdown_alerts[0]["message"]

    # 当前净值 == 回撤谷底（dist == -max_dd），所以不允许宣称正反弹
    if abs(risk["distFromPeak"]) >= 0.01 and risk["maxDrawdown"] > 0:
        if abs(abs(risk["distFromPeak"]) - risk["maxDrawdown"]) < 1e-3:
            m = re.search(r"反弹\s*\+(\d+\.\d)%", msg)
            assert m is None or float(m.group(1)) == 0.0, (
                "当前净值就是回撤谷底，文案不应宣称正反弹：\n" f"{msg}")


# ============================================================
# B. 「N日最大回撤」→ 真实统计区间
# ============================================================

def test_window_label_uses_date_span():
    """回撤文案必须展示真实统计区间起止日，不得再出现裸的「N日」条数标签。

    事故现场：推送写「30日最大回撤 …（07/27→07/29）」，用户按自然日读，
    07/27 是 42 天前，直接判定"窗口越界"。实际 30 是交易日条数。
    修复后应显示「统计区间 07/27~09/04」。
    """
    navs = _build_2026_09_07_accident_series()
    risk = calc_risk_metrics(navs)
    alerts = detect_fund_alerts(_ACCIDENT_CODE, {}, risk)

    drawdown_alerts = [a for a in alerts if a.get("type") == "drawdown"]
    assert len(drawdown_alerts) == 1, f"应恰好产出 1 条回撤异动，实际 {len(drawdown_alerts)}"

    msg = drawdown_alerts[0]["message"]

    # 1) 必须出现真实统计区间
    assert "07/27~09/04" in msg, (
        f"文案应展示统计区间 07/27~09/04，实际：\n{msg}")

    # 2) 不得再出现「30日最大回撤」这类裸条数标签
    assert _BARE_WINDOW_LABEL_RE.search(msg) is None, (
        f"文案仍在使用裸条数标签（N日最大回撤），实际：\n{msg}")

    # 3) 回撤区间本身仍然保留（信息量不能越改越少）
    assert "08/17→09/04" in msg, f"文案应保留回撤区间 08/17→09/04，实际：\n{msg}"
    assert "12.0%" in msg, f"文案应保留回撤幅度 12.0%，实际：\n{msg}"


# ============================================================
# F. cron 引用的 fund_monitor 符号必须真实存在
# ============================================================

def test_fund_monitor_symbols_referenced_by_cron_exist():
    """静态扫描：stock_monitor_cron.py 里 from services.fund_monitor import X 的
    每个 X 都必须真实存在。

    防的是 2026-09-07 发现的整类静默失效：import 了不存在的符号 → ImportError
    被 `except Exception` 吞掉 → 那一段功能永远为空，日志里只有一句无关痛痒的
    "数据获取失败"。单个用例比逐个补功能测试划算得多。

    已命中的两个真实事故符号：
        infra.data_source.fund_realtime  （模块全仓不存在）
        services.fund_monitor.calc_fund_risk （函数从未定义）
    """
    cron_path = Path(fund_monitor.__file__).parent.parent / "scripts" / "stock_monitor_cron.py"
    assert cron_path.exists(), f"找不到 cron 脚本: {cron_path}"

    tree = ast.parse(cron_path.read_text(encoding="utf-8"), filename=str(cron_path))

    missing: list = []
    checked: list = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != "services.fund_monitor":
            continue
        for alias in node.names:
            if alias.name == "*":
                continue
            checked.append(alias.name)
            if not hasattr(fund_monitor, alias.name):
                missing.append(f"{cron_path.name}:{node.lineno} -> {alias.name}")

    assert checked, (
        f"没有从 {cron_path} 解析到任何 `from services.fund_monitor import ...`，"
        "扫描逻辑可能失效了")
    assert not missing, (
        "stock_monitor_cron.py 引用了 services.fund_monitor 里不存在的符号，"
        "会导致对应代码段被 except Exception 静默吞掉：\n  " + "\n  ".join(missing))


def test_fund_monitor_symbols_referenced_by_night_worker_exist():
    """同上，顺带覆盖 night_worker.py（温度计链路也 import 了 fund_monitor）。

    不是事故现场，但成本几乎为零，且温度计直接决定推送里的「总市值」数字。
    """
    nw_path = Path(fund_monitor.__file__).parent.parent / "scripts" / "night_worker.py"
    if not nw_path.exists():
        pytest.skip(f"找不到 night_worker.py: {nw_path}")

    tree = ast.parse(nw_path.read_text(encoding="utf-8"), filename=str(nw_path))

    missing: list = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != "services.fund_monitor":
            continue
        for alias in node.names:
            if alias.name == "*":
                continue
            if not hasattr(fund_monitor, alias.name):
                missing.append(f"{nw_path.name}:{node.lineno} -> {alias.name}")

    assert not missing, (
        "night_worker.py 引用了 services.fund_monitor 里不存在的符号：\n  "
        + "\n  ".join(missing))

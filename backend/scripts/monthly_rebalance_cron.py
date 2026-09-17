#!/usr/bin/env python3
"""
月底资产再平衡提醒（每月最后一个交易日 15:30 触发）
====================================================
逻辑：
  - 读每个用户的目标配置（没设就用美林时钟默认）
  - 对比当前持仓结构
  - 偏离 > 5% 则提醒再平衡

推送方式：企微纯文本
"""
from __future__ import annotations
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

env = ROOT / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))


# 稳健组合目标：65/20/10/5
#
# FIX 2026-09-18: 黄金目标由孤立的 10 统一为 **5**。原值 10 是本文件里一个没有
# 任何来源依据的字面量（注释只写「默认 60/20/10/10 的稳健组合」），而 domain 层
# 两处权威源一致给 5：
#   - domain/rule_engine/glide_path_rules.py:35  GOLD_PCT_DEFAULT = 0.05
#     （_GLIDE_PATH_TABLE 每个年龄档 gold 均为 5：20岁 75/10/10/5、25岁 70/15/10/5 …）
#   - domain/rule_engine/defaults.py:36-37       目标矩阵 (50,25,20,5) / (35,40,20,5)
# 且 glide_path 的 stock_pct 注释写明「股票目标占比（已扣除黄金）」，
# 因此从 stock 扣 5（60 → 65），四档合计仍为 100。
DEFAULT_TARGET = {"stock": 65, "bond": 20, "cash": 10, "gold": 5}


def _pct_of(d: dict, *keys: str) -> float:
    """按候选键名依次取值，全取不到返回 0.0。

    portfolio_overview 的 allocation/deviation/target 用 "equity" 作键名，
    而本脚本的 DEFAULT_TARGET 与兼容分支用 "stock"，两者指同一个东西。
    早期这里写死 `result['current']['stock']`，而 allocation 里根本没有
    "stock" 键 → KeyError 直接把整次推送打挂（异常被 main 的 try 吞掉，
    表现为"[REBALANCE] FAILED"）。改为按候选键名容错取值。
    """
    for k in keys:
        if k in d:
            try:
                return float(d[k] or 0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _fmt_pct(v: float) -> str:
    """渲染百分比：整数值不带小数点（65.0 → "65"），非整数保留 1 位（14.3 → "14.3"）。

    目标是整数（65/20/10/5），当前值来自 allocation 是带小数的（57.1）。
    统一走这个函数，避免出现「目标：股65.0%」这种和原硬编码文案不一致的写法。
    """
    fv = float(v)
    return str(int(fv)) if fv.is_integer() else f"{fv:.1f}"


def analyze_user(user_id: str) -> dict:
    """拉用户持仓 → 算当前比例 → 对比目标（美林时钟推荐）→ 返回偏离"""
    try:
        from services.portfolio_overview import get_portfolio_overview
        overview = get_portfolio_overview(user_id=user_id)
    except Exception as e:
        return {"user": user_id, "available": False, "reason": f"拉持仓失败: {e}"}

    if not overview or not overview.get("totalMarketValue"):
        return {"user": user_id, "available": False, "reason": "无持仓或总市值 0"}

    total = float(overview.get("totalMarketValue") or 0)
    if total <= 0:
        return {"user": user_id, "available": False, "reason": "总市值 0"}

    # portfolio_overview 已经给了 allocation / target / deviation，直接用
    allocation = overview.get("allocation") or {}
    target = overview.get("target") or DEFAULT_TARGET
    deviation = overview.get("deviation") or {}
    rebalance_flag = overview.get("rebalance") or False

    # 兼容：如果 allocation 没给出，自己算
    if not allocation:
        stock_val = float(overview.get("stockValue") or 0)
        fund_val = float(overview.get("fundValue") or 0)
        allocation = {
            "stock": round(stock_val / total * 100, 1),
            "bond": round(fund_val / total * 100, 1),
            "cash": round((total - stock_val - fund_val) / total * 100, 1),
            "gold": 0,
        }
    if not deviation:
        deviation = {k: round(float(allocation.get(k, 0)) - float(target.get(k, 0)), 1) for k in target}

    max_dev = max((abs(float(v)) for v in deviation.values()), default=0)

    return {
        "user": user_id,
        "available": True,
        "total": round(total, 0),
        "current": allocation,
        "target": target,
        "deviations": deviation,
        "max_dev": max_dev,
        "need_rebalance": bool(rebalance_flag) or max_dev > 5,
        "health_grade": overview.get("healthGrade", ""),
    }


def _render_message(result: dict) -> str:
    """渲染推送正文。

    目标值一律从 `result["target"]` **动态**渲染，不再硬编码。

    FIX 2026-09-18: 正文原先写死「目标：股60% · 基金20% · 现金10% · 黄金10%」，
    与 DEFAULT_TARGET 是两份独立维护的同一个数字。更糟的是正常路径下
    `target = overview.get("target")`，拿到的是 portfolio_overview 的
    45/30/20/5，而正文仍在说 60/20/10/10 —— 也就是说这行文本在绝大多数
    情况下**本来就是错的**，只是没人发现（异常被 main 的 try 吞掉）。
    改成动态渲染后，目标值只可能有一个真源，这类不一致不可能再发生。
    """
    user = result.get("user", "")
    cur = result.get("current") or {}
    tgt = result.get("target") or {}
    month = datetime.now().strftime("%Y-%m")

    cur_line = (
        f"当前：股{_fmt_pct(_pct_of(cur, 'stock', 'equity'))}% · "
        f"基金{_fmt_pct(_pct_of(cur, 'bond'))}% · "
        f"现金{_fmt_pct(_pct_of(cur, 'cash'))}% · "
        f"黄金{_fmt_pct(_pct_of(cur, 'gold'))}%"
    )
    tgt_line = (
        f"目标：股{_fmt_pct(_pct_of(tgt, 'stock', 'equity'))}% · "
        f"基金{_fmt_pct(_pct_of(tgt, 'bond'))}% · "
        f"现金{_fmt_pct(_pct_of(tgt, 'cash'))}% · "
        f"黄金{_fmt_pct(_pct_of(tgt, 'gold'))}%"
    )

    if not result.get("need_rebalance"):
        # 偏离 < 5%，温和提醒
        return (
            f"🎯 钱袋子·月度再平衡（{month}）\n\n"
            f"✅ {user}的资产结构健康\n"
            f"━━━━━━━━━━━━━━━\n"
            f"{cur_line}\n"
            f"最大偏离：{result['max_dev']}%（< 5% 无需调整）\n\n"
            f"💡 下月继续保持～"
        )

    # 需要再平衡
    suggestions = []
    for asset, dev in (result.get("deviations") or {}).items():
        if abs(dev) > 5:
            # "equity" 是 portfolio_overview 用的键名，"stock" 是本脚本
            # DEFAULT_TARGET / 兼容分支用的键名，两者都要认。
            cn = {"stock": "股票", "equity": "股票", "bond": "基金",
                  "cash": "现金", "gold": "黄金"}[asset]
            if dev > 0:
                suggestions.append(f"  • {cn} 超配 {dev:+.1f}%，考虑减仓")
            else:
                suggestions.append(f"  • {cn} 低配 {dev:+.1f}%，考虑加仓")

    return (
        f"⚠️ 钱袋子·月度再平衡（{month}）\n\n"
        f"🔔 {user}的资产结构需要调整\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{cur_line}\n"
        f"{tgt_line}\n\n"
        f"📋 调整建议：\n"
        + "\n".join(suggestions) +
        "\n\n💡 再平衡不是必做，但可以降低风险\n"
        "⚠️ 仅供参考，不构成投资建议"
    )


def main():
    try:
        # v9.9.20 (B3): 裸 send_text 没有任何长度保护，超 2048 字节就被企微硬截断。
        # 改走 send_markdown：按字节无损分段，内容一个字都不会丢。
        from services.wxwork_push import is_configured, send_markdown

        if not is_configured():
            print("[REBALANCE] 企微未配置")
            return 0

        whitelist = ["LeiJiang", "BuLuoGeLi"]
        for user in whitelist:
            result = analyze_user(user)
            if not result["available"]:
                print(f"[REBALANCE] {user}: {result.get('reason')}，跳过")
                continue

            text = _render_message(result)

            ok = False
            dry_run = "--dry-run" in sys.argv
            if dry_run:
                print(f"\n[REBALANCE dry-run] 将推送给 {user}:\n{text}\n")
            else:
                ok = send_markdown(text, user_id=user)  # FIX: 参数名是 user_id 不是 to_user
            print(f"[REBALANCE] {user}: {'✅ dry' if dry_run else ('✅ 推送成功' if ok.get('ok') else '❌ 推送失败')}")

        return 0
    except Exception as e:
        traceback.print_exc()
        print(f"[REBALANCE] FAILED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

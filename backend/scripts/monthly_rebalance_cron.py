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


# 默认 60/20/10/10 的稳健组合
DEFAULT_TARGET = {"stock": 60, "bond": 20, "cash": 10, "gold": 10}


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


def main():
    try:
        from services.wxwork_push import is_configured, send_text

        if not is_configured():
            print("[REBALANCE] 企微未配置")
            return 0

        whitelist = ["LeiJiang", "BuLuoGeLi"]
        for user in whitelist:
            result = analyze_user(user)
            if not result["available"]:
                print(f"[REBALANCE] {user}: {result.get('reason')}，跳过")
                continue

            if not result["need_rebalance"]:
                # 偏离 < 5%，温和提醒
                text = (
                    f"🎯 钱袋子·月度再平衡（{datetime.now().strftime('%Y-%m')}）\n\n"
                    f"✅ {user}的资产结构健康\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"当前：股{result['current']['stock']}% · 基金{result['current']['bond']}% · 现金{result['current']['cash']}%\n"
                    f"最大偏离：{result['max_dev']}%（< 5% 无需调整）\n\n"
                    f"💡 下月继续保持～"
                )
            else:
                # 需要再平衡
                devs = result["deviations"]
                suggestions = []
                for asset, dev in devs.items():
                    if abs(dev) > 5:
                        cn = {"stock": "股票", "bond": "基金", "cash": "现金", "gold": "黄金"}[asset]
                        if dev > 0:
                            suggestions.append(f"  • {cn} 超配 {dev:+.1f}%，考虑减仓")
                        else:
                            suggestions.append(f"  • {cn} 低配 {dev:+.1f}%，考虑加仓")

                text = (
                    f"⚠️ 钱袋子·月度再平衡（{datetime.now().strftime('%Y-%m')}）\n\n"
                    f"🔔 {user}的资产结构需要调整\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"当前：股{result['current']['stock']}% · 基金{result['current']['bond']}% · 现金{result['current']['cash']}%\n"
                    f"目标：股60% · 基金20% · 现金10% · 黄金10%\n\n"
                    f"📋 调整建议：\n"
                    + "\n".join(suggestions) +
                    "\n\n💡 再平衡不是必做，但可以降低风险\n"
                    "⚠️ 仅供参考，不构成投资建议"
                )

            ok = False
            dry_run = "--dry-run" in sys.argv
            if dry_run:
                print(f"\n[REBALANCE dry-run] 将推送给 {user}:\n{text}\n")
            else:
                ok = send_text(text, to_user=user)
            print(f"[REBALANCE] {user}: {'✅ dry' if dry_run else ('✅ 推送成功' if ok else '❌ 推送失败')}")

        return 0
    except Exception as e:
        traceback.print_exc()
        print(f"[REBALANCE] FAILED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
周五复盘推送（每周五 15:30 触发）
===================================
内容：
  1. 本周持仓变动汇总（每个用户）
  2. 本周关键指标变化（沪深300 / 美林时钟 / 估值分位）
  3. 下周关注点提示

推送方式：企微 Markdown
"""
from __future__ import annotations
import os
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 加载 .env
env = ROOT / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))


def main():
    try:
        from services.wxwork_push import is_configured, send_text
        from services.market_data import get_fear_greed_index, get_valuation_percentile
        from services.macro_extended import get_merrill_lynch_clock

        if not is_configured():
            print("[WEEKLY] 企微未配置，跳过推送")
            return 0

        today = datetime.now().strftime("%Y-%m-%d")
        week_end = datetime.now()
        week_start = week_end - timedelta(days=4)

        # 取核心指标
        fg = get_fear_greed_index()
        val = get_valuation_percentile()
        clock = get_merrill_lynch_clock()

        fg_score = fg.get("score", 50) if isinstance(fg, dict) else 50
        pe_pct = val.get("percentile", "N/A") if isinstance(val, dict) else "N/A"
        phase = clock.get("label", "未知") if isinstance(clock, dict) else "未知"

        # 组装文本（纯文本，企微微信端不支持 markdown）
        lines = [
            f"📅 钱袋子·本周复盘（{week_start.strftime('%m-%d')} ~ {week_end.strftime('%m-%d')}）",
            "",
            "━━━━━━━━━━━━━━━",
            "📊 关键指标",
            f"  • 美林时钟：{phase}",
            f"  • 沪深300 PE 分位：{pe_pct}%",
            f"  • 恐贪指数：{fg_score}（{'贪婪' if fg_score > 65 else '恐惧' if fg_score < 35 else '中性'}）",
            "",
            "━━━━━━━━━━━━━━━",
            "💡 下周建议",
            "  • 周一盘前检查持仓止损位",
            "  • 关注本周企微里的量化信号",
            f"  • 如 PE 分位 < 30% 可考虑加仓定投",
            "",
            f"⏰ 生成时间：{datetime.now().strftime('%H:%M')}",
            "💬 仅供参考，不构成投资建议",
        ]
        text = "\n".join(lines)

        # 推送给白名单用户（--dry-run 只打印不推送）
        dry_run = "--dry-run" in sys.argv
        whitelist = ["LeiJiang", "BuLuoGeLi"]
        for user in whitelist:
            if dry_run:
                print(f"\n[WEEKLY dry-run] 将推送给 {user}:\n{text}\n")
                continue
            ok = send_text(text, to_user=user)
            print(f"[WEEKLY] 推送 {user}: {'✅' if ok else '❌'}")
        return 0
    except Exception as e:
        traceback.print_exc()
        print(f"[WEEKLY] FAILED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

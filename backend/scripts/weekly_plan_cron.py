#!/usr/bin/env python3
"""
周日下周规划推送（每周日 21:00 触发）
======================================
内容：
  1. 下周财经日历高亮（美联储/CPI/PMI/GDP）
  2. 本周涨跌幅榜 TOP（从持仓中筛）
  3. 一句话规划建议

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

        if not is_configured():
            print("[WEEK_PLAN] 企微未配置")
            return 0

        next_mon = datetime.now() + timedelta(days=1)
        next_fri = datetime.now() + timedelta(days=5)

        # 简化版：周度提醒，没有接财经日历 API 前，给出通用建议
        lines = [
            f"🗓️ 钱袋子·下周规划（{next_mon.strftime('%m-%d')} ~ {next_fri.strftime('%m-%d')}）",
            "",
            "━━━━━━━━━━━━━━━",
            "📌 固定节奏",
            "  • 周一 09:30 — 开盘关注持仓波动",
            "  • 每日 09:27 — 量化日报自动推送",
            "  • 盘中 10-15 分钟 — 企微信号提醒",
            "  • 周五 15:30 — 本周复盘",
            "",
            "━━━━━━━━━━━━━━━",
            "🎯 下周关注",
            "  • 关注经济数据发布（CPI/PMI/社融）",
            "  • 关注政策面消息（央行/证监会）",
            "  • 如恐贪指数极端（<25 或 >75）可能出现拐点",
            "",
            "━━━━━━━━━━━━━━━",
            "💡 温馨提醒",
            "  • 周末别盯盘，享受生活 🍵",
            "  • 下周遇到疑问，打开钱袋子问大师团",
            "",
            f"⏰ 生成：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        ]
        text = "\n".join(lines)

        dry_run = "--dry-run" in sys.argv
        whitelist = ["LeiJiang", "BuLuoGeLi"]
        for user in whitelist:
            if dry_run:
                print(f"\n[WEEK_PLAN dry-run] 将推送给 {user}:\n{text}\n")
                continue
            ok = send_text(text, to_user=user)
            print(f"[WEEK_PLAN] 推送 {user}: {'✅' if ok else '❌'}")
        return 0
    except Exception as e:
        traceback.print_exc()
        print(f"[WEEK_PLAN] FAILED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

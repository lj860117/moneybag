#!/usr/bin/env python3
"""
周五复盘推送（每周五 15:30 触发）
===================================
新设计（7个模块）：
  1. 💰 本周表现（vs成本、周环比）
  2. 📊 资产配置分析
  3. 🎯 风险提示（集中度、回撤、波动率）
  4. 📌 投资操作回顾
  5. 💡 下周行动建议（可操作）
  6. 📅 下周重要事件（财经日历）
  7. 🎯 判断准确率

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
        from services.wxwork_push import is_configured, send_markdown
        from services.weekly_report import generate
        from services.financial_calendar import get_upcoming_week_events, format_for_weekly_report

        if not is_configured():
            print("[WEEKLY] 企微未配置，跳过推送")
            return 0

        whitelist = ["LeiJiang", "BuLuoGeLi"]
        dry_run = "--dry-run" in sys.argv

        for user in whitelist:
            try:
                print(f"[WEEKLY] 生成用户 {user} 的周报...")

                # 调用完整周报生成（新版本）
                report = generate(user)

                # 取人话版本
                narrative = report.get("narrative", "")
                if not narrative:
                    # 降级方案
                    narrative = f"周报汇总\n{report.get('summary', '暂无数据')}"

                # 追加下周重要事件（如果周报生成器没包含）
                sections = report.get("sections", {})
                events = sections.get("upcoming_events", [])
                if not events:
                    # 尝试直接获取
                    try:
                        events = get_upcoming_week_events()
                    except Exception:
                        events = []

                if events:
                    # 检查 narrative 是否已有"下周重要事件"部分
                    if "下周重要事件" not in narrative:
                        event_text = format_for_weekly_report(events, max_events=5)
                        narrative = narrative.rstrip() + "\n\n📅 下周重要事件\n" + event_text

                if dry_run:
                    print(f"\n[WEEKLY dry-run] 将推送给 {user}:")
                    print(f"  内容长度: {len(narrative)} 字")
                    print(f"  内容预览:")
                    print(f"  {narrative[:300]}...\n")
                    continue

                # 发送
                wrapped = f"**📋 钱袋子周报**\n\n{narrative}\n\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                result = send_markdown(wrapped, user_id=user)
                if result.get("ok"):
                    print(f"[WEEKLY] 推送 {user}: OK ({len(narrative)} 字)")
                else:
                    print(f"[WEEKLY] 推送 {user}: FAIL - {result}")

                # 如果是周五，还需要拍摄周度快照
                if datetime.now().weekday() == 4:  # 周五
                    try:
                        from services.weekly_snapshot import take_snapshot
                        take_snapshot(user)
                        print(f"[WEEKLY] 已拍摄周度快照: {user}")
                    except Exception as e:
                        print(f"[WEEKLY] 快照失败: {e}")

            except Exception as e:
                print(f"[WEEKLY] 用户 {user} 的周报生成失败: {e}")
                traceback.print_exc()

        return 0
    except Exception as e:
        traceback.print_exc()
        print(f"[WEEKLY] FAILED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

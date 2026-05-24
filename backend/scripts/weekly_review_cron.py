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
        from services.wxwork_push import is_configured, send_markdown
        from services.weekly_report import generate

        if not is_configured():
            print("[WEEKLY] 企微未配置，跳过推送")
            return 0

        whitelist = ["LeiJiang", "BuLuoGeLi"]
        dry_run = "--dry-run" in sys.argv

        for user in whitelist:
            try:
                print(f"[WEEKLY] 生成用户 {user} 的周报...")
                
                # 调用完整周报生成
                report = generate(user)
                
                # 取人话版本
                narrative = report.get("narrative", "")
                if not narrative:
                    # 降级方案
                    narrative = f"周报汇总\n{report.get('summary', '暂无数据')}"
                
                if dry_run:
                    print(f"\n[WEEKLY dry-run] 将推送给 {user}:")
                    print(f"  内容长度: {len(narrative)} 字")
                    print(f"  内容预览:")
                    print(f"  {narrative[:200]}...\n")
                    continue
                
                # 发送
                result = send_markdown(narrative, user_id=user)
                if result.get("ok"):
                    print(f"[WEEKLY] 推送 {user}: OK ({len(narrative)} 字)")
                else:
                    print(f"[WEEKLY] 推送 {user}: FAIL - {result}")
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

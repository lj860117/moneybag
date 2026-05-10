#!/usr/bin/env python3
"""
每周自检入口脚本
==============
systemd timer 每周日凌晨 2 点触发：
    ExecStart=/opt/moneybag/venv/bin/python /opt/moneybag/backend/scripts/weekly_self_audit.py

也可手动跑：
    cd /opt/moneybag/backend && python scripts/weekly_self_audit.py
"""
import sys
import os
import json
import logging
from pathlib import Path

# 确保能导入 backend 模块
_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPT_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("weekly_audit")


def main():
    logger.info("=== 钱袋子周自检 开始 ===")
    try:
        from use_cases.self_audit import run_weekly_audit
        report = run_weekly_audit()

        status = report.get("overall_status", "unknown")
        stats  = report.get("stats", {})
        logger.info(
            "审计完成 | 状态=%s | 健康分=%s | fail=%s warn=%s pass=%s",
            status,
            stats.get("health_score", "N/A"),
            stats.get("fail_count", 0),
            stats.get("warn_count", 0),
            stats.get("pass_count", 0),
        )

        # 打印报告摘要到 stdout（systemd journal 可查）
        print(json.dumps(report, ensure_ascii=False, indent=2))
        sys.exit(0)

    except Exception as e:
        logger.error("审计异常: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

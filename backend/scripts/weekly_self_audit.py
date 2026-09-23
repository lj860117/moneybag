#!/usr/bin/env python3
"""
每周自检入口脚本
==============
调度（FIX 2026-09-22 补）：cron 每周日 02:00，见
    docs/ops/weekly-self-audit-cron.md   ← 可直接粘贴的 crontab 条目

⚠️ 本脚本原本只写在注释里的 systemd timer（每周日凌晨 2 点）从未真正存在：
服务器上 crontab 没有任何调用它的条目，backend 全库 grep `weekly_self_audit`
也只有脚本自身、零调用方。结果是 ops_summary 的巡检项「周度自检」
（读 data/audit/latest.json，max_stale_days=7）持续告警 9 天没人知道根因。
现在改由 cron 调度，并在本脚本内加了落盘自检（见 _verify_artifact）——
下次再出现「调度没了」，cron 退出码和日志会直接说，不用靠日报间接猜。

也可手动跑：
    cd /opt/moneybag/backend && python scripts/weekly_self_audit.py
"""
import sys
import os
import json
import logging
import time
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


def _verify_artifact() -> bool:
    """校验审计产物 data/audit/latest.json 确实被写出来且是本次刚写的。

    FIX 2026-09-22: 本脚本此前是「孤儿脚本」——服务器上既没有 cron 条目、
    全库也没有任何调用方（backend 下 grep weekly_self_audit 零命中），
    而 ops_summary 的巡检项「周度自检」读的就是 data/audit/latest.json
    （max_stale_days=7），于是该文件 9 天没更新、运维日报天天告警，
    却没人知道根因是「压根没调度」而不是「审计跑失败」。

    补上调度（见 docs/ops/weekly-self-audit-cron.md）之外，这里再加一道
    落盘自检：如果 latest.json 不存在或 mtime 不是本次刚写的，就打 ERROR
    并让进程以非 0 退出——这样将来即使调度失效或审计中途挂了，cron 的
    退出码/日志也能立刻看出来，而不是靠「日报里那个 stale 天数」间接猜。
    """
    from config import DATA_DIR

    latest = Path(DATA_DIR) / "audit" / "latest.json"
    if not latest.exists():
        logger.error("审计产物缺失: %s —— latest.json 未写出，本次自检等于没跑", latest)
        return False
    age_hours = (time.time() - latest.stat().st_mtime) / 3600.0
    if age_hours > 1.0:
        logger.error(
            "审计产物未更新: %s（mtime 距今 %.1f 小时，不是本次写的）", latest, age_hours
        )
        return False
    logger.info("审计产物已落盘: %s", latest)
    return True


def main():
    logger.info("=== 钱袋子周自检 开始 ===")
    try:
        from use_cases.self_audit import run_weekly_audit
        report = run_weekly_audit()

        status = report.get("overall_status", "unknown")
        stats  = report.get("stats", {})

        # FIX 2026-09-22: 键名对齐 use_cases/self_audit.run_weekly_audit() 实际
        # 写入的 stats 字段。旧代码读的是 fail_count / warn_count / pass_count，
        # 这三个键在 self_audit.py 里**根本不存在**（它写的是 probe_fail /
        # probe_warn / probe_pass / smoke_fail / …），于是 `stats.get(k, 0)` 恒为 0
        # —— 日志写着「fail=0 warn=0」其实是没有读到，属于日志撒谎，和本次
        # 「巡检链不可信」是同一类缺陷。
        # 全部用 .get(k, 0) 兜底：将来再发生键名漂移只会静默变 0（可观测），
        # 不会因为 KeyError 把整个周度自检进程带崩（latest.json 也就写不出来了）。
        fail_count = stats.get("probe_fail", 0) + stats.get("smoke_fail", 0)
        warn_count = stats.get("probe_warn", 0) + stats.get("smoke_warn", 0)
        pass_count = stats.get("probe_pass", 0) + stats.get("smoke_pass", 0)
        logger.info(
            "审计完成 | 状态=%s | 健康分=%s | fail=%s warn=%s pass=%s",
            status,
            stats.get("health_score", "N/A"),
            fail_count,
            warn_count,
            pass_count,
        )

        # 打印报告摘要到 stdout（systemd journal / cron 日志可查）
        print(json.dumps(report, ensure_ascii=False, indent=2))

        # FIX 2026-09-22: 落盘自检（见 _verify_artifact），失败要返回非 0，
        # 免得「审计没跑」和「审计跑完但没写盘」在运维侧长得一模一样。
        sys.exit(0 if _verify_artifact() else 1)

    except Exception as e:
        logger.error("审计异常: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

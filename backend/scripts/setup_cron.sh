#!/usr/bin/env bash
# setup_cron.sh — Install all MoneyBag cron jobs
# Usage: bash backend/scripts/setup_cron.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$BACKEND_DIR")"

echo "=== MoneyBag Cron Job Setup ==="
echo "Backend path: $BACKEND_DIR"
echo "Repo root: $REPO_ROOT"
echo ""

# Create logs directory
mkdir -p "$BACKEND_DIR/logs"
echo "✅ Created logs directory"

# Define all cron jobs
declare -a CRON_JOBS=(
    "0 1 * * * cd $BACKEND_DIR && python scripts/night_worker.py >> logs/night.log 2>&1"
    "0 2 * * * cd $BACKEND_DIR && python scripts/auto_extract_cron.py >> logs/auto_extract.log 2>&1"
    "10 8 * * * cd $BACKEND_DIR && python scripts/daily_reflection_cron.py >> logs/daily_reflection.log 2>&1"
    "30 8 * * 1-5 cd $BACKEND_DIR && python scripts/night_worker.py --push-only >> logs/night.log 2>&1"
    "*/10 9,10,11,13,14 * * 1-5 cd $BACKEND_DIR && python scripts/stock_monitor_cron.py >> logs/stock_monitor.log 2>&1"
    "30 15 * * 1-5 cd $BACKEND_DIR && python scripts/stock_monitor_cron.py --close >> logs/stock_monitor.log 2>&1"
    "30 15 * * 5 cd $BACKEND_DIR && python scripts/weekly_review_cron.py >> logs/weekly_review.log 2>&1"
    "0 21 * * 0 cd $BACKEND_DIR && python scripts/weekly_plan_cron.py >> logs/weekly_plan.log 2>&1"
    "0 4 1 * * cd $BACKEND_DIR && python scripts/memory_archive_cron.py >> logs/memory_archive.log 2>&1"
    # E6 v9.5.44: 晨报幻觉自检（02:35，night_worker 01:00 跑完约 1-2h 后），有问题发企微告警
    "35 2 * * * cd $BACKEND_DIR && python scripts/briefing_hallucination_check.py --rounds 2 --interval 1 --alert >> logs/hallucination_check.log 2>&1"
    # v9.7.0: 补齐遗漏的 cron 任务
    "30 9 * * 6 cd $BACKEND_DIR && python scripts/weekend_push.py >> logs/weekend_push.log 2>&1"
    "0 20 * * 0 cd $BACKEND_DIR && python scripts/broker_rating_cron.py >> logs/broker_rating.log 2>&1"
    "0 1 1 * * cd $BACKEND_DIR && python scripts/monthly_report.py >> logs/monthly_report.log 2>&1"
    "0 10 25 * * cd $BACKEND_DIR && python scripts/dca_scheduler.py --dca >> logs/dca.log 2>&1"
    "0 8 * * * cd $BACKEND_DIR && python scripts/dca_scheduler.py --discipline >> logs/dca_discipline.log 2>&1"
    "0 20 * * 0 cd $BACKEND_DIR && python scripts/dca_scheduler.py --weekly >> logs/dca_weekly.log 2>&1"
    # cache_warmer 五段调度
    "10 18 * * 1-5 cd $BACKEND_DIR && python scripts/cache_warmer.py --after-close >> logs/cache_warmer.log 2>&1"
    "0 16 * * 1-5 cd $BACKEND_DIR && python scripts/cache_warmer.py --harvest >> logs/cache_warmer.log 2>&1"
    "45 8 * * 1-5 cd $BACKEND_DIR && python scripts/cache_warmer.py --morning >> logs/cache_warmer.log 2>&1"
    "5 13 * * 1-5 cd $BACKEND_DIR && python scripts/cache_warmer.py --midday >> logs/cache_warmer.log 2>&1"
    "0 10 * * 6 cd $BACKEND_DIR && python scripts/cache_warmer.py --weekend >> logs/cache_warmer.log 2>&1"
    "30 20 * * 1-5 cd $BACKEND_DIR && python scripts/cache_warmer.py --nav-confirmed >> logs/cache_warmer.log 2>&1"
)

# Check if any cron jobs already exist
EXISTING=$(crontab -l 2>/dev/null | grep -c 'moneybag\|night_worker\|stock_monitor\|weekly_review\|weekly_plan\|memory_archive\|daily_reflection\|auto_extract\|hallucination_check\|weekend_push\|broker_rating\|monthly_report\|dca_scheduler\|cache_warmer' || echo 0)

if [ "$EXISTING" -gt 0 ]; then
    echo "⚠️  Found $EXISTING existing MoneyBag cron jobs"
    read -p "Do you want to replace them? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborting."
        exit 0
    fi
    # Remove old cron jobs
    (crontab -l 2>/dev/null | grep -v 'moneybag\|night_worker\|stock_monitor\|weekly_review\|weekly_plan\|memory_archive\|daily_reflection\|auto_extract\|hallucination_check\|weekend_push\|broker_rating\|monthly_report\|dca_scheduler\|cache_warmer') | crontab - 2>/dev/null || true
    echo "✅ Removed old cron jobs"
fi

# Add all new cron jobs
{
    crontab -l 2>/dev/null || true
    for job in "${CRON_JOBS[@]}"; do
        echo "$job"
    done
} | crontab -

echo "✅ Installed ${#CRON_JOBS[@]} cron jobs"
echo ""
echo "=== Installed Cron Jobs ==="
crontab -l | grep -E 'night_worker|stock_monitor|weekly_review|weekly_plan|memory_archive|daily_reflection|auto_extract|hallucination_check|weekend_push|broker_rating|monthly_report|dca_scheduler|cache_warmer' || echo "(none found)"
echo ""
echo "=== Verification ==="
echo "Run these commands to test:"
echo "  python3 backend/scripts/weekly_review_cron.py --dry-run"
echo "  python3 backend/scripts/night_worker.py --push-only"
echo "  python3 backend/scripts/stock_monitor_cron.py"

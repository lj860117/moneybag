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
)

# Check if any cron jobs already exist
EXISTING=$(crontab -l 2>/dev/null | grep -c 'moneybag\|night_worker\|stock_monitor\|weekly_review\|weekly_plan\|memory_archive\|daily_reflection\|auto_extract' || echo 0)

if [ "$EXISTING" -gt 0 ]; then
    echo "⚠️  Found $EXISTING existing MoneyBag cron jobs"
    read -p "Do you want to replace them? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborting."
        exit 0
    fi
    # Remove old cron jobs
    (crontab -l 2>/dev/null | grep -v 'moneybag\|night_worker\|stock_monitor\|weekly_review\|weekly_plan\|memory_archive\|daily_reflection\|auto_extract') | crontab - 2>/dev/null || true
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
crontab -l | grep -E 'night_worker|stock_monitor|weekly_review|weekly_plan|memory_archive|daily_reflection|auto_extract' || echo "(none found)"
echo ""
echo "=== Verification ==="
echo "Run these commands to test:"
echo "  python3 backend/scripts/weekly_review_cron.py --dry-run"
echo "  python3 backend/scripts/night_worker.py --push-only"
echo "  python3 backend/scripts/stock_monitor_cron.py"

# MoneyBag Cron Jobs Configuration Guide

This document describes all scheduled cron jobs for the MoneyBag notification and monitoring system.

## Cron Jobs Summary

| Time | Frequency | Script | Purpose | Priority |
|------|-----------|--------|---------|----------|
| 01:00 | Daily | night_worker.py | Main daily briefing pipeline (01:00-08:30) | **CRITICAL** |
| 02:00 | Daily | auto_extract_cron.py | Extract habits from conversation snippets | Medium |
| 04:00 | 1st of month | memory_archive_cron.py | Archive old decisions and summarize month | Low |
| 08:10 | Daily | daily_reflection_cron.py | Generate handover notes for AI session | Medium |
| 08:30 | Weekdays (Mon-Fri) | night_worker.py --push-only | Fallback morning briefing push | **CRITICAL** |
| 09:00-15:00 | Every 10 min (trading hours) | stock_monitor_cron.py | Real-time stock monitoring | High |
| 15:30 | Daily | stock_monitor_cron.py --close | End-of-day portfolio review | High |
| 15:30 | Friday | weekly_review_cron.py | Weekly market review and recommendations | High |
| 21:00 | Sunday | weekly_plan_cron.py | Weekly planning and agenda | Medium |
| Last trading day | 15:30 | monthly_rebalance_cron.py | Monthly portfolio rebalancing check | Low |

## Installation Instructions

### Option 1: Automated Setup (Recommended)

Run the setup script:
```bash
bash backend/scripts/setup_cron.sh
```

This will:
1. Verify Python virtual environment
2. Create log directory
3. Add all required cron jobs
4. Display confirmation

### Option 2: Manual Setup

Run these commands to add cron jobs:

```bash
# Main night_worker (01:00 daily)
(crontab -l 2>/dev/null; echo "0 1 * * * cd /opt/moneybag/backend && python scripts/night_worker.py >> logs/night.log 2>&1") | crontab -

# Auto extract (02:00 daily)
(crontab -l 2>/dev/null; echo "0 2 * * * cd /opt/moneybag/backend && python scripts/auto_extract_cron.py >> logs/auto_extract.log 2>&1") | crontab -

# Daily reflection (08:10 daily)
(crontab -l 2>/dev/null; echo "10 8 * * * cd /opt/moneybag/backend && python scripts/daily_reflection_cron.py >> logs/daily_reflection.log 2>&1") | crontab -

# Fallback push (08:30 weekdays)
(crontab -l 2>/dev/null; echo "30 8 * * 1-5 cd /opt/moneybag/backend && python scripts/night_worker.py --push-only >> logs/night.log 2>&1") | crontab -

# Stock monitor regular scan (09:00-15:00 every 10 min)
(crontab -l 2>/dev/null; echo "*/10 9,10,11,13,14 * * 1-5 cd /opt/moneybag/backend && python scripts/stock_monitor_cron.py >> logs/stock_monitor.log 2>&1") | crontab -

# Stock monitor close review (15:30 daily)
(crontab -l 2>/dev/null; echo "30 15 * * * cd /opt/moneybag/backend && python scripts/stock_monitor_cron.py --close >> logs/stock_monitor.log 2>&1") | crontab -

# Weekly review (15:30 Friday)
(crontab -l 2>/dev/null; echo "30 15 * * 5 cd /opt/moneybag/backend && python scripts/weekly_review_cron.py >> logs/weekly_review.log 2>&1") | crontab -

# Weekly plan (21:00 Sunday)
(crontab -l 2>/dev/null; echo "0 21 * * 0 cd /opt/moneybag/backend && python scripts/weekly_plan_cron.py >> logs/weekly_plan.log 2>&1") | crontab -

# Monthly rebalance (15:30 last trading day)
(crontab -l 2>/dev/null; echo "30 15 28,29,30,31 * * cd /opt/moneybag/backend && python scripts/monthly_rebalance_cron.py >> logs/monthly_rebalance.log 2>&1") | crontab -

# Memory archive (04:00 on 1st of month)
(crontab -l 2>/dev/null; echo "0 4 1 * * cd /opt/moneybag/backend && python scripts/memory_archive_cron.py >> logs/memory_archive.log 2>&1") | crontab -
```

## Verification

After installation, verify with:
```bash
crontab -l | grep -E 'night_worker|stock_monitor|weekly_review|weekly_plan|memory_archive|daily_reflection|auto_extract|monthly_rebalance'
```

Expected output: 10 cron job lines

## Testing

Test individual cron jobs:

```bash
# Test weekly review (generates 162+ char narrative)
python3 backend/scripts/weekly_review_cron.py --dry-run

# Test night_worker push-only mode
python3 backend/scripts/night_worker.py --push-only

# Test stock monitor
python3 backend/scripts/stock_monitor_cron.py

# Test other cron jobs
python3 backend/scripts/daily_reflection_cron.py
python3 backend/scripts/auto_extract_cron.py
```

## Log Locations

All logs are written to `logs/` directory:
- `logs/night.log` - night_worker and push-only jobs
- `logs/auto_extract.log` - auto_extract_cron
- `logs/daily_reflection.log` - daily_reflection_cron
- `logs/stock_monitor.log` - stock_monitor_cron (both regular and close)
- `logs/weekly_review.log` - weekly_review_cron
- `logs/weekly_plan.log` - weekly_plan_cron
- `logs/monthly_rebalance.log` - monthly_rebalance_cron
- `logs/memory_archive.log` - memory_archive_cron

## Troubleshooting

### Issue: "notification pipeline stopped"
Check:
1. Verify cron jobs exist: `crontab -l | grep night_worker`
2. Check logs: `tail -50 logs/night.log`
3. Verify Enterprise WeChat config: `grep WXWORK backend/.env`

### Issue: "weekly report only shows 3 metrics"
Fixed in this version. Now calls `weekly_report.generate(user)` to get full 500+ character narrative.

### Issue: "cron jobs not executing"
Check:
1. crontab is running: `ps aux | grep cron`
2. Python path is correct in crontab
3. Working directory is set correctly
4. All log files are writable: `ls -la logs/`

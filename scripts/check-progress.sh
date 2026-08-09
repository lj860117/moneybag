#!/bin/bash
# MoneyBag 重构进度检查脚本
# 检查 PROGRESS.md 是否今日更新 + 今日是否有代码提交

set -e

PROJECT_DIR="/Users/leijiang/WorkBuddy/moneybag-for-claudecode"
PROGRESS_FILE="$PROJECT_DIR/docs/PROGRESS.md"
TODAY=$(date '+%Y-%m-%d')

# 检查 PROGRESS.md 最后修改日期
if [ -f "$PROGRESS_FILE" ]; then
    LAST_MODIFIED=$(stat -f "%Sm" -t "%Y-%m-%d" "$PROGRESS_FILE")
    if [ "$LAST_MODIFIED" = "$TODAY" ]; then
        PROGRESS_UPDATED=true
    else
        PROGRESS_UPDATED=false
        DAYS_SINCE=$(( ($(date -j -f "%Y-%m-%d" "$TODAY" +%s) - $(date -j -f "%Y-%m-%d" "$LAST_MODIFIED" +%s)) / 86400 ))
    fi
else
    PROGRESS_UPDATED=false
    DAYS_SINCE="N/A"
fi

# 检查今日 git 提交
cd "$PROJECT_DIR"
TODAY_COMMITS=$(git log --since="$TODAY 00:00:00" --until="$TODAY 23:59:59" --oneline --all 2>/dev/null | wc -l | tr -d ' ')

if [ "$TODAY_COMMITS" -gt 0 ]; then
    HAS_COMMITS=true
else
    HAS_COMMITS=false
fi

# 输出检查结果
echo "=== MoneyBag Progress Check ($TODAY) ==="
echo ""
if [ "$PROGRESS_UPDATED" = true ]; then
    echo "OK PROGRESS.md updated (last: $LAST_MODIFIED)"
else
    echo "MISSING PROGRESS.md not updated (last: $LAST_MODIFIED, $DAYS_SINCE days ago)"
fi

if [ "$HAS_COMMITS" = true ]; then
    echo "OK Commits today: $TODAY_COMMITS"
else
    echo "MISSING No commits today"
fi

echo ""

# 判断是否需要通知
if [ "$PROGRESS_UPDATED" = false ] || [ "$HAS_COMMITS" = false ]; then
    echo "ALERT: Progress tracking needs attention"
    
    # 构建通知消息
    MSG=""
    if [ "$PROGRESS_UPDATED" = false ] && [ "$HAS_COMMITS" = false ]; then
        MSG="PROGRESS.md ${DAYS_SINCE} days stale, no commits today."
    elif [ "$PROGRESS_UPDATED" = false ]; then
        MSG="PROGRESS.md ${DAYS_SINCE} days stale. Update needed."
    else
        MSG="No commits today."
    fi
    
    # 发送 macOS 通知
    osascript -e "display notification \"$MSG\" with title \"MoneyBag Progress Alert\" sound name \"Glass\"" 2>/dev/null || true
    
    exit 1
else
    echo "OK All metrics good"
    exit 0
fi

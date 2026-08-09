#!/bin/bash
# MoneyBag 三方漂移检测脚本（本地工作区 vs git main vs 服务器 server/main）
#
# 背景：2026-08-09 排查中发现本地/git/服务器三方长期不同步（服务器上
# 从未有过git版本控制，靠SSH直接热改+手动cp备份维护），曾因想当然
# 覆盖导致157行未提交代码丢失。当天已给服务器 /opt/moneybag 建立了
# git仓库基线快照，并在本地添加了 `server` remote，可以直接
# `git fetch server`拉取服务器当前状态做对比，不再需要逐个SSH。
#
# 用法：
#   bash scripts/check-drift.sh              # 全量扫描 backend/ + pages/
#   bash scripts/check-drift.sh <文件路径>    # 只看单个文件的详细diff
#
# 首次使用/怀疑服务器有新热改时，先跑：
#   git fetch server
# 再执行本脚本，才能拿到服务器最新状态。

set -uo pipefail

PROJECT_DIR="/Users/leijiang/WorkBuddy/moneybag-for-claudecode"
cd "$PROJECT_DIR" || exit 1
export GIT_PAGER=cat
export PAGER=cat

if ! git remote get-url server >/dev/null 2>&1; then
    echo "❌ 未配置 server remote，请先执行："
    echo "   git remote add server ssh://ubuntu@150.158.47.189/opt/moneybag"
    exit 1
fi

# 单文件模式：直接展示 diff，方便判断谁更新
if [ $# -eq 1 ]; then
    f="$1"
    echo "=== $f ==="
    echo "--- 本地工作区 vs git main（是否有未提交改动）---"
    git --no-pager diff --stat main -- "$f" 2>/dev/null
    echo ""
    echo "--- 本地工作区 vs server/main（最准确的真实差异，因为本地工作区往往比git main更新）---"
    git --no-pager diff server/main -- "$f" 2>/dev/null
    exit 0
fi

echo "=== 拉取服务器最新git状态 ==="
git fetch server 2>&1 | grep -v "^$" || true

echo ""
echo "=== 本地工作区 vs server/main 差异清单（最准确，直接反映当前真实漂移；一次git diff完成，不逐文件循环）==="
echo "（限定在本地实际tracked/存在的 backend 与 pages 路径，排除服务器上的历史死代码目录）"
git ls-files backend/api backend/services backend/use_cases backend/routers backend/scripts backend/models backend/main.py backend/config.py pages app.js index.html styles.css sw.js 2>/dev/null > /tmp/.check_drift_tracked.txt
find backend/api backend/services backend/use_cases backend/routers backend/scripts backend/models pages -type f \( -name "*.py" -o -name "*.js" \) -not -path "*/__pycache__/*" 2>/dev/null >> /tmp/.check_drift_tracked.txt
sort -u /tmp/.check_drift_tracked.txt -o /tmp/.check_drift_tracked.txt

# 一次性调用 git diff --numstat，输出格式：added\tdeleted\tpath，比逐文件diff快得多
git --no-pager diff --numstat server/main -- \
    backend/api backend/services backend/use_cases backend/routers backend/scripts backend/models backend/main.py backend/config.py \
    pages app.js index.html styles.css sw.js 2>/dev/null > /tmp/.check_drift_numstat.txt

DIFF_COUNT=0
OK_COUNT=0
while IFS= read -r f; do
    [ -f "$f" ] || continue
    # FIX:用行尾锚点精确匹配，避免 wxwork.py 被 wxwork.py.bak_xxx 这类历史遗留
    # 备份文件前缀误匹配（曾导致 wxwork.py 被错误标记为漂移292行）
    line=$(grep -F "	$f" /tmp/.check_drift_numstat.txt | awk -v p="$f" '$0 ~ "\t"p"$"' | head -1)
    if [ -z "$line" ]; then
        OK_COUNT=$((OK_COUNT+1))
    else
        DIFF_COUNT=$((DIFF_COUNT+1))
        added=$(echo "$line" | awk '{print $1}')
        deleted=$(echo "$line" | awk '{print $2}')
        printf "DRIFT  %-50s  +%s -%s\n" "$f" "$added" "$deleted"
    fi
done < /tmp/.check_drift_tracked.txt
rm -f /tmp/.check_drift_tracked.txt /tmp/.check_drift_numstat.txt
echo ""
echo "汇总：一致 $OK_COUNT 个，漂移 $DIFF_COUNT 个"

echo ""
echo "=== 本地工作区 vs git main 差异文件清单（尚未commit的改动）==="
LOCAL_DIFF=$(git --no-pager diff --name-only main -- backend pages app.js index.html styles.css sw.js 2>/dev/null)
UNTRACKED=$(git status --porcelain -- backend pages 2>/dev/null | grep '^??' | awk '{print $2}')
if [ -z "$LOCAL_DIFF" ] && [ -z "$UNTRACKED" ]; then
    echo "✅ 无未提交改动"
else
    [ -n "$LOCAL_DIFF" ] && echo "$LOCAL_DIFF" | sed 's/^/UNCOMMITTED  /'
    [ -n "$UNTRACKED" ] && echo "$UNTRACKED" | sed 's/^/UNTRACKED    /'
fi

echo ""
echo "提示：对某个具体文件想看详细diff，运行："
echo "  bash scripts/check-drift.sh <文件路径>"

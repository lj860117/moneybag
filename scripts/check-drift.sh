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
#
# FIX 2026-08-09: 本地"工作区"里包含大量 untracked（??）文件（如
# backend/api/auth.py、pages/insight-fund.js 等历史上一直没有 git add
# 过的正常开发文件）。`git diff server/main -- <file>` 对 untracked
# 文件会把工作区当成"文件不存在"，导致服务器上的内容被错误报告为
# "整份被删除"（如 +0 -391 这种假阳性）。第一版脚本曾把76个文件全部
# 标记为漂移，实测发现其中约60+个是这个bug造成的假阳性——文件内容
# 本地和服务器实际完全一致。
# 修复：用GIT_INDEX_FILE 构造一个临时 index/tree，把工作区当前状态
# （含untracked 文件）打包成一个 git tree 对象，再用这个 tree 去跟
# server/main 比较，就能正确反映"文件真实内容"而不受 git跟踪状态影响。

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

SCAN_PATHS="backend/api backend/services backend/use_cases backend/routers backend/scripts backend/models backend/main.py backend/config.py pages app.js index.html styles.css sw.js"

# 构造一个临时 tree，把工作区当前实际内容（含未 git add 过的 untracked
# 文件）打包成git 对象，避免 untracked 文件被 git diff 误判为"不存在"
build_worktree_snapshot() {
    local tmp_index
    tmp_index=$(mktemp -u)
    rm -f "$tmp_index"
    GIT_INDEX_FILE="$tmp_index" git add -- $SCAN_PATHS >/dev/null 2>&1
    local tree
    tree=$(GIT_INDEX_FILE="$tmp_index" git write-tree 2>/dev/null)
    rm -f "$tmp_index"
    echo "$tree"
}

# 单文件模式：直接展示 diff，方便判断谁更新
if [ $# -eq 1 ]; then
    f="$1"
    echo "=== $f ==="
    echo "--- 本地工作区 vs git main（是否有未提交改动）---"
    git --no-pager diff --stat main -- "$f" 2>/dev/null
    echo ""
    echo "--- 本地工作区(含未跟踪文件真实内容) vs server/main（最准确的真实差异）---"
    SNAP=$(build_worktree_snapshot)
    git --no-pager diff "server/main" "$SNAP" -- "$f" 2>/dev/null
    exit 0
fi

echo "=== 拉取服务器最新git状态 ==="
git fetch server 2>&1 | grep -v "^$" || true

echo ""
echo "=== 构造工作区快照(含未跟踪文件真实内容,避免误判为'已删除') ==="
SNAP=$(build_worktree_snapshot)
if [ -z "$SNAP" ]; then
    echo "❌ 构造工作区快照失败"
    exit 1
fi

echo ""
echo "=== 本地工作区 vs server/main 差异清单（最准确，直接反映当前真实漂移；一次git diff完成，不逐文件循环）==="
echo "（限定在本地实际tracked/存在的 backend 与 pages 路径，排除服务器上的历史死代码目录）"
git ls-files $SCAN_PATHS 2>/dev/null > /tmp/.check_drift_tracked.txt
find backend/api backend/services backend/use_cases backend/routers backend/scripts backend/models pages -type f \( -name "*.py" -o -name "*.js" \) -not -path "*/__pycache__/*" 2>/dev/null >> /tmp/.check_drift_tracked.txt
sort -u /tmp/.check_drift_tracked.txt -o /tmp/.check_drift_tracked.txt

# 一次性调用 git diff --numstat，输出格式：added\tdeleted\tpath，比逐文件diff快得多
git --no-pager diff --numstat "server/main" "$SNAP" -- $SCAN_PATHS 2>/dev/null > /tmp/.check_drift_numstat.txt

DIFF_COUNT=0
OK_COUNT=0
while IFS= read -r f; do
    [ -f "$f" ] || continue
    # 用行尾锚点精确匹配，避免 wxwork.py 被 wxwork.py.bak_xxx 这类历史遗留
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
echo "汇总：一致 ${OK_COUNT} 个，漂移 ${DIFF_COUNT} 个"

echo ""
echo "=== 本地工作区 vs git main 差异文件清单（尚未commit的改动，仅供参考——不代表和服务器不一致）==="
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

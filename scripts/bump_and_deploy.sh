#!/usr/bin/env bash
# =============================================================================
# bump_and_deploy.sh — 一键 bump 版号 + git commit + 部署到腾讯云
# =============================================================================
# 用法：
#   bash scripts/bump_and_deploy.sh 9.3.32                          # 指定新版本（有交互）
#   bash scripts/bump_and_deploy.sh 9.3.32 --yes -m "描述"          # 无交互，适合脚本调用
#   bash scripts/bump_and_deploy.sh 9.3.32 --no-deploy              # 只 bump+commit，不部署
#   bash scripts/bump_and_deploy.sh 9.3.32 --dry-run                # 只打印要改哪些，不改
#
# 会做什么：
#   1. 把 backend/config.py 里的 APP_VERSION 改成新版本
#   2. 把 index.html 所有 ?v=x.x.x 改成新版本
#   3. 把 sw.js 的 CACHE_NAME 里的版本号改成新版本（去掉点，如 9.3.32 → 9332）
#   4. git add + commit "[home] bump vX.X.X: <commit message>"
#   5. git push origin main
#   6. 调用 backend/scripts/deploy_to_server.sh 推到服务器
# =============================================================================

set -euo pipefail

# ---- 参数解析 ----
NEW_VERSION="${1:-}"
NO_DEPLOY=false
DRY_RUN=false
YES=false      # --yes 跳过交互确认
COMMIT_MSG_ARG=""  # -m "message" 直接传 commit message

i=1
while [ $i -le $# ]; do
    arg="${!i}"
    case "$arg" in
        --no-deploy) NO_DEPLOY=true ;;
        --dry-run)   DRY_RUN=true ;;
        --yes|-y)    YES=true ;;
        -m)
            i=$((i+1))
            COMMIT_MSG_ARG="${!i:-}"
            ;;
    esac
    i=$((i+1))
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# ---- 帮助 ----
if [ -z "$NEW_VERSION" ] || [ "$NEW_VERSION" = "--help" ] || [ "$NEW_VERSION" = "-h" ]; then
    echo ""
    echo "用法: bash scripts/bump_and_deploy.sh <新版本号> [--no-deploy] [--dry-run]"
    echo ""
    echo "示例:"
    echo "  bash scripts/bump_and_deploy.sh 9.3.32              # bump + commit + 部署"
    echo "  bash scripts/bump_and_deploy.sh 9.3.32 --no-deploy  # 只 bump + commit"
    echo "  bash scripts/bump_and_deploy.sh 9.3.32 --dry-run    # 只预览，不改文件"
    echo ""
    # 读取当前版本
    CURRENT=$(grep 'APP_VERSION' backend/config.py | grep -oE '"[0-9]+\.[0-9]+\.[0-9]+"' | tr -d '"' | head -1)
    echo "当前版本: ${CURRENT:-unknown}"
    exit 0
fi

# ---- 校验版本号格式 ----
if ! echo "$NEW_VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    echo "❌ 版本号格式错误，应为 X.Y.Z（例如 9.3.32）"
    exit 1
fi

# ---- 读取当前版本 ----
CURRENT_VERSION=$(grep 'APP_VERSION' backend/config.py | grep -oE '"[0-9]+\.[0-9]+\.[0-9]+"' | tr -d '"' | head -1)
CURRENT_INDEX_VERSION=$(grep '?v=' index.html | grep -oE 'v=[0-9]+\.[0-9]+\.[0-9]+' | head -1 | sed 's/v=//')
CURRENT_SW_VERSION=$(grep 'CACHE_NAME' sw.js | grep -oE "moneybag-v[0-9]+-" | sed "s/moneybag-v//" | tr -d '-')

# sw.js CACHE_NAME 版本号（去点格式）
NEW_SW_VERSION=$(echo "$NEW_VERSION" | tr -d '.')

echo ""
echo "==============================="
echo "  MoneyBag 版本 Bump"
echo "==============================="
echo "  当前版本(config.py):  ${CURRENT_VERSION:-?}"
echo "  当前版本(index.html): ${CURRENT_INDEX_VERSION:-?}"
echo "  当前版本(sw.js):      ${CURRENT_SW_VERSION:-?}"
echo "  目标版本:             $NEW_VERSION"
echo "  sw.js cache key:      moneybag-v${NEW_SW_VERSION}-cache"
if $DRY_RUN; then
    echo "  [DRY RUN 模式，不修改文件]"
fi
echo "==============================="
echo ""

if $DRY_RUN; then
    echo "📋 将要修改的文件："
    echo "  backend/config.py  APP_VERSION: ${CURRENT_VERSION} → ${NEW_VERSION}"
    echo "  index.html         ?v=${CURRENT_INDEX_VERSION:-?} → ?v=${NEW_VERSION}  (22 处)"
    echo "  sw.js              CACHE_NAME: moneybag-v${CURRENT_SW_VERSION}-cache → moneybag-v${NEW_SW_VERSION}-cache"
    echo ""
    echo "✅ Dry run 完成，实际未修改任何文件。"
    exit 0
fi

# ---- 防呆：当前目录有未提交的变更时提醒 ----
DIRTY_COUNT=$(git status --porcelain | grep -v "^??" | wc -l | tr -d ' ')
if [ "$DIRTY_COUNT" -gt 0 ]; then
    echo "⚠️  当前有 ${DIRTY_COUNT} 个已跟踪文件有未提交变更："
    git status --porcelain | grep -v "^??" | head -10
    echo ""
    if $YES; then
        echo "  --yes 模式，自动继续。"
    else
        read -r -p "继续 bump 并把这些变更一起提交？[y/N] " confirm
        if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
            echo "已取消。"
            exit 0
        fi
    fi
fi

# ---- 1. 修改 backend/config.py ----
echo "[1/5] 修改 backend/config.py ..."
if [ -n "$CURRENT_VERSION" ]; then
    sed -i.bak "s/APP_VERSION = \"${CURRENT_VERSION}\"/APP_VERSION = \"${NEW_VERSION}\"/" backend/config.py
    rm -f backend/config.py.bak
    echo "  ✅ APP_VERSION: ${CURRENT_VERSION} → ${NEW_VERSION}"
else
    echo "  ⚠️  未找到 APP_VERSION，手动检查 backend/config.py"
fi

# ---- 2. 修改 index.html (所有 ?v=x.x.x) ----
echo "[2/5] 修改 index.html ..."
OLD_V="${CURRENT_INDEX_VERSION:-}"
if [ -n "$OLD_V" ]; then
    # 用 perl 替换所有出现（macOS sed -i 不支持 \+ 等，perl 更稳健）
    perl -i -pe "s/\?v=${OLD_V//./\\.}/\?v=${NEW_VERSION}/g" index.html
    REPLACED=$(grep -c "?v=${NEW_VERSION}" index.html || echo 0)
    echo "  ✅ ?v=${OLD_V} → ?v=${NEW_VERSION} (共 ${REPLACED} 处)"
else
    echo "  ⚠️  未在 index.html 找到版本号，跳过"
fi

# ---- 3. 修改 sw.js CACHE_NAME ----
echo "[3/5] 修改 sw.js ..."
if grep -q "CACHE_NAME" sw.js; then
    # 把整个 CACHE_NAME 行里的版本替换
    perl -i -pe "s/(CACHE_NAME\s*=\s*')moneybag-v\d+-cache(')/"'${1}'"moneybag-v${NEW_SW_VERSION}-cache"'${2}/g' sw.js
    echo "  ✅ CACHE_NAME → moneybag-v${NEW_SW_VERSION}-cache"
else
    echo "  ⚠️  sw.js 中未找到 CACHE_NAME，跳过"
fi

# ---- 4. git add + commit ----
echo "[4/5] Git commit ..."

# commit message：优先用 -m 参数，否则交互输入，--yes 时用默认
if [ -n "$COMMIT_MSG_ARG" ]; then
    COMMIT_MSG="$COMMIT_MSG_ARG"
elif $YES; then
    COMMIT_MSG="前端+后端版本同步"
else
    read -r -p "  Commit message（直接回车用默认）: " COMMIT_MSG
    if [ -z "$COMMIT_MSG" ]; then
        COMMIT_MSG="前端+后端版本同步"
    fi
fi

FULL_MSG="[home] bump v${NEW_VERSION}: ${COMMIT_MSG}"

git add backend/config.py index.html sw.js
# 如果还有其它已跟踪的变更，也一起加进来
git add -u 2>/dev/null || true

git commit -m "$FULL_MSG"
echo "  ✅ 已提交: $FULL_MSG"

# ---- 5. git push ----
echo "[5/5] Git push ..."
git push origin main
echo "  ✅ 已推送到 origin/main"

# ---- 6. 部署 ----
if $NO_DEPLOY; then
    echo ""
    echo "✅ --no-deploy 模式，跳过部署。"
    echo "   要手动部署请跑："
    echo "   bash backend/scripts/deploy_to_server.sh"
else
    echo ""
    echo "🚀 开始部署到服务器..."
    echo "---"
    bash "$REPO_ROOT/backend/scripts/deploy_to_server.sh"
fi

echo ""
echo "==============================="
echo "  ✅ 全部完成！"
echo "  版本：${CURRENT_VERSION:-?} → ${NEW_VERSION}"
if ! $NO_DEPLOY; then
    echo "  线上：http://150.158.47.189:8000/api/health"
fi
echo "==============================="

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
# 版本 bump 口径（重要，别踩坑）：
#   - 本轮**有前端改动** → 三处一起 bump：config.py + index.html ?v= + sw.js CACHE_NAME。
#     不一起 bump 的后果：用户浏览器和 Service Worker 继续吃旧副本，改了等于没改。
#   - 本轮是**纯后端改动** → 只 bump config.py，加 --backend-only。
#     index.html 的 ?v= 与 sw.js 的 CACHE_NAME 保持不动：改了只会让所有用户白重下
#     一遍资源，且 SW 缓存被整体作废，却没有任何前端内容变化。
#     代价是会出现「后端版本领先前端缓存标记」的状态 —— 这是允许的，
#     下一轮只要动了前端，三处必须一起收拢。
#
# 会做什么：
#   1. 把 backend/config.py 里的 APP_VERSION 改成新版本
#   2. 把 index.html 所有 ?v=x.x.x 改成新版本              [--backend-only 时跳过]
#   3. 把 sw.js 的 CACHE_NAME 里的版本号改成新版本（去掉点）  [--backend-only 时跳过]
#   4. git add + commit "[home] bump vX.X.X: <commit message>"
#   5. git push origin main
#   6. 调用 backend/scripts/deploy_to_server.sh 推到服务器
# =============================================================================

set -euo pipefail

# ---- 纵深防御：让 set -e 的失败不再"静默" ----
# set -e 触发时不会打印任何行号或原因，脚本直接断在半路，看起来像跑完了。
# 2026-09-12 踩过一次 P1：干净工作区时下面 DIRTY_COUNT 那行因 grep 无匹配
# 退出 1，pipefail 把管道状态传出去、set -e 直接终止脚本 —— 现象是只打印
# header、退出码 1、一句错误都没有，而"工作区干净"恰恰是纯 bump 最常见的情况。
# 有这个 trap 后，任何同类问题都会立刻指名行号。
# 注意用【单引号】：让 ${LINENO} / ${?} 在触发时才展开，而不是定义时。
# 必须先把 $? 存进 rc 再 echo：trap 里第一条命令（哪怕是 echo ""）都会把
# $? 重置为 0，直接写 exit=${?} 会永远打印 exit=0，把报错伪装成成功。
# （实测：原始写法打印「第 4 行，exit=0」；rc=$? 前置后是「第 4 行，exit=1」）
# LINENO 不受影响，仍指向触发失败的那一行。
trap 'rc=$?; echo ""; echo "❌ 脚本异常退出：第 ${LINENO} 行，exit=${rc}。请检查上面输出，可能未做任何修改。" >&2' ERR

# ---- 参数解析 ----
NEW_VERSION="${1:-}"
NO_DEPLOY=false
DRY_RUN=false
YES=false      # --yes 跳过交互确认
BACKEND_ONLY=false  # --backend-only 只 bump config.py（纯后端改动用，不动前端缓存标记）
COMMIT_MSG_ARG=""  # -m "message" 直接传 commit message

i=1
while [ $i -le $# ]; do
    arg="${!i}"
    case "$arg" in
        --no-deploy) NO_DEPLOY=true ;;
        --dry-run)   DRY_RUN=true ;;
        --yes|-y)    YES=true ;;
        --backend-only) BACKEND_ONLY=true ;;
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
    echo "  bash scripts/bump_and_deploy.sh 9.3.32 --backend-only  # 纯后端改动：只 bump config.py"
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
if $BACKEND_ONLY; then
    echo "  模式:                 --backend-only（纯后端改动，不动前端缓存标记）"
fi
if $DRY_RUN; then
    echo "  [DRY RUN 模式，不修改文件]"
fi
echo "==============================="
echo ""

if $DRY_RUN; then
    echo "📋 将要修改的文件："
    echo "  backend/config.py  APP_VERSION: ${CURRENT_VERSION} → ${NEW_VERSION}"
    if $BACKEND_ONLY; then
        echo "  index.html         ?v=${CURRENT_INDEX_VERSION:-?} 保持不变（--backend-only）"
        echo "  sw.js              CACHE_NAME: moneybag-v${CURRENT_SW_VERSION}-cache 保持不变（--backend-only）"
        echo ""
        echo "  ⚠️  纯后端改动：bump 后会出现「后端版本领先前端缓存标记」的状态，"
        echo "     这是允许的。下一轮动了前端时，三处必须一起 bump 收拢。"
    else
        # 真实统计"出现次数"而不是写死数字。
        # 2026-09-12：这里原来是硬编码的 "(22 处)"，与实际处数早已不符
        # （实测 27 处），会让 dry-run 预览给出一个假的确定感。
        # 口径必须与 [2/5] 实际替换后的统计一致，否则"预览 27、实际替换 22"
        # 这种对不上的情况无法被发现。
        IDX_OLD_COUNT=0
        if [ -n "${CURRENT_INDEX_VERSION:-}" ]; then
            # grep -oF：-o 逐"出现"输出（而非 -c 的逐"行"计数），
            #          -F 固定字符串（避免版本号里的 . 被当正则元字符）。
            IDX_OLD_COUNT=$(grep -oF "?v=${CURRENT_INDEX_VERSION}" index.html | wc -l | tr -d ' ')
        fi
        echo "  index.html         ?v=${CURRENT_INDEX_VERSION:-?} → ?v=${NEW_VERSION}  (${IDX_OLD_COUNT} 处)"
        echo "  sw.js              CACHE_NAME: moneybag-v${CURRENT_SW_VERSION}-cache → moneybag-v${NEW_SW_VERSION}-cache"
    fi
    echo ""
    echo "✅ Dry run 完成，实际未修改任何文件。"
    exit 0
fi

# ---- 防呆：当前目录有未提交的变更时提醒 ----
# grep -cv 直接数"非 untracked 的行"，一行取代原来的 grep -v | wc -l。
# 尾部 `|| true` 是必需的，不是可有可无的保险：
#   干净工作区 → git status --porcelain 无输出 → grep 无任何匹配行 → 退出码 1
#   → pipefail 让整条管道返回 1 → set -e 静默终止整个脚本。
# 2026-09-12 P1：就是这个原因让"工作区干净时脚本打完 header 就退出 1"，
# 而工作区干净恰恰是纯 bump 最常见的场景。
DIRTY_COUNT=$(git status --porcelain | grep -cv "^??" || true)
if [ "$DIRTY_COUNT" -gt 0 ]; then
    echo "⚠️  当前有 ${DIRTY_COUNT} 个已跟踪文件有未提交变更："
    # 同上一行：这行只在 DIRTY_COUNT>0 时才走到，所以目前没爆过，
    # 但属于同一颗雷（grep 无匹配 → pipefail → set -e 静默终止），一并兜住。
    git status --porcelain | grep -v "^??" | head -10 || true
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
if $BACKEND_ONLY; then
    echo "  ⏭️  --backend-only，跳过（保持 ?v=${CURRENT_INDEX_VERSION:-?} 不变）"
else
    OLD_V="${CURRENT_INDEX_VERSION:-}"
    if [ -n "$OLD_V" ]; then
        # 用 perl 替换所有出现（macOS sed -i 不支持 \+ 等，perl 更稳健）
        perl -i -pe "s/\?v=${OLD_V//./\\.}/\?v=${NEW_VERSION}/g" index.html
        # 与 dry-run 预览保持同一口径：逐"出现"计数，不是逐"行"计数。
        # grep -c 数的是【匹配到的行数】，若某行出现两个 ?v=x.x.x 就会少算，
        # 造成"预览 27 处 / 实际替换 25 处"这种对不上却没人发现的偏差。
        # 与 [dry-run] 的 IDX_OLD_COUNT 用同一套 grep -oF | wc -l，
        # 两处口径一致才能真正互相对账。
        REPLACED=$(grep -oF "?v=${NEW_VERSION}" index.html | wc -l | tr -d ' ')
        echo "  ✅ ?v=${OLD_V} → ?v=${NEW_VERSION} (共 ${REPLACED} 处)"
    else
        echo "  ⚠️  未在 index.html 找到版本号，跳过"
    fi
fi

# ---- 3. 修改 sw.js CACHE_NAME ----
echo "[3/5] 修改 sw.js ..."
if $BACKEND_ONLY; then
    echo "  ⏭️  --backend-only，跳过（保持 moneybag-v${CURRENT_SW_VERSION}-cache 不变）"
elif grep -q "CACHE_NAME" sw.js; then
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
    if $BACKEND_ONLY; then COMMIT_MSG="纯后端改动（不动前端缓存标记）"; else COMMIT_MSG="前端+后端版本同步"; fi
else
    read -r -p "  Commit message（直接回车用默认）: " COMMIT_MSG
    if [ -z "$COMMIT_MSG" ]; then
        if $BACKEND_ONLY; then COMMIT_MSG="纯后端改动（不动前端缓存标记）"; else COMMIT_MSG="前端+后端版本同步"; fi
    fi
fi

FULL_MSG="[home] bump v${NEW_VERSION}: ${COMMIT_MSG}"

if $BACKEND_ONLY; then
    git add backend/config.py
else
    git add backend/config.py index.html sw.js
fi
# 其它变更也一起加进来（含新增文件）。
# 2026-09-12：原来是 `git add -u`，只加【已跟踪】文件 —— 新增文件会被
# 静默漏掉：不报错、不提示，等 push 上去才发现新文件没进版本库。
# 实例：bump v9.9.22 时新写的 backend/tests/test_multi_model_scorer_cache.py
# 就差点没进提交，靠手动 git add 才救回来。
# 改用 -A 覆盖新增。安全边界：.gitignore 已覆盖 *.bak / *.bak-* / *.bak_*
# / *.bak.*，服务器上 pages/ 下那 9 个 .bak* 不会被误提交（已用
# git check-ignore 逐个验证：9/9 IGNORED，漏网 0）。
# 先打印清单，让"到底加进去了什么"肉眼可见（--yes 模式同样打印）。
echo "  📥 git add -A 将纳入的新增文件："
UNTRACKED_LIST=$(git ls-files --others --exclude-standard)
if [ -n "$UNTRACKED_LIST" ]; then
    echo "$UNTRACKED_LIST" | sed 's/^/      /'
else
    echo "      （无新增文件）"
fi
git add -A 2>/dev/null || true

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
    # 强制播报 + 强制等待。注意它【不是】确认，也绝不能挂在 $YES 分支上：
    # 本脚本的部署分支从来没有任何 read -r -p 确认，部署是【默认行为】、
    # --no-deploy 才是例外，所以交互模式下同样不会问。若把防护写成
    # "非 --yes 才提示"，那 --yes（文档里标注"适合脚本调用"的模式）就会被
    # 跳过，等于没防住。因此这里无条件执行，任何模式都跑。
    # 背景：2026-09-12 有人验证脚本时漏写 --no-deploy，直接把 /tmp 克隆
    # 发到了生产并重启服务（PID 2063791→2071104），靠手动回滚才恢复。
    echo ""
    echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    echo "!!  ⚠️  即将部署到【生产服务器】                      !!"
    echo "!!     目标:   ubuntu@150.158.47.189:/opt/moneybag     !!"
    echo "!!     版本:   ${CURRENT_VERSION} → ${NEW_VERSION}     !!"
    echo "!!     动作:   rsync 覆盖 + 重启 moneybag 服务         !!"
    echo "!!                                                    !!"
    echo "!!  3 秒后开始。现在按 Ctrl-C 可取消（尚未做任何改动）。!!"
    echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    sleep 3
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

#!/usr/bin/env bash
# MoneyBag 漂移检测脚本 —— 本地工作区 vs 服务器实际磁盘内容
# =============================================================================
# 用法：
#   bash scripts/check-drift.sh              # 全量对账（清单 + 内容哈希）
#   bash scripts/check-drift.sh <文件路径>    # 只看单个文件的详细 diff
#   bash scripts/check-drift.sh --strict      # 对账；发现真漂移时以非零码退出（可做闸门）
#
# 结果分四类输出（前三类是"需要看的差异"，第四类是"预期不部署"的白名单）：
#   ① 内容不一致（真漂移 / 运行时缓存）
#   ② 本地有、服务器没有（疑似漏部署）
#   ③ 服务器有、本地没有（垃圾 / 服务器本地文件）
#   ④ 白名单：backend/tests/、tests/ 等 dev-only 路径，预期不进自动部署，不计漂移
#
# 设计原则：以「文件清单 + 内容哈希」为准，不依赖服务器上的任何 git 状态。
# -----------------------------------------------------------------------------
# 为什么不能再用 server/main（git）当基线 —— 后人请勿"优化"回去
# -----------------------------------------------------------------------------
# 旧版脚本 `git fetch server` 后拿 server/main 当基准比。但
# backend/scripts/deploy_to_server.sh 里【一个 git 命令都没有】：部署走
# scp/rsync 直传，从不在服务器上 commit。实测 2026-09-13：服务器
# /opt/moneybag 的 HEAD 停在 6943a76（v9.9.23），而工作树有 43 个未提交改动
# —— v9.9.24~v9.9.27 的部署内容全在里面。
# 后果：任何一次【正确】部署之后，server/main 都还是旧的，脚本会把刚部署的
# 文件全部误报成"漂移"。它区分不了"漂移=部署漏了"和"漂移=我部署了但没在
# 服务器 commit"。所以 git 基线在钱袋子的部署模型下是结构性错误的，必须废弃。
#
# 本脚本改为：两边各自 `find` 枚举同一口径的文件清单，逐文件比 sha256 内容哈希。
# 服务器磁盘上的实际内容才是唯一真相。
#
# -----------------------------------------------------------------------------
# ⚠️ 排序必须用 LC_ALL=C（踩过的坑，勿删）
# -----------------------------------------------------------------------------
# macOS(BSD) 与 Linux(GNU) 的 LC_COLLATE 不同，`sort`/`comm` 的排序结果会不一致，
# 导致同一路径在两侧排到不同位置，`comm`/`join` 会输出错位的"幽灵条目"——
# 同一个文件同时出现在"本地有服务器没有"和"服务器有本地没有"两个列表里。
# 因此本脚本所有跨机比较前的排序一律 `LC_ALL=C sort`。
#
# -----------------------------------------------------------------------------
# 历史沿革（为什么以前用 git tree 快照）
# -----------------------------------------------------------------------------
# 2026-08-09：本地工作区含大量 untracked 文件（backend/api/auth.py 等），
# `git diff server/main -- <file>` 会把它们当成"文件不存在"，服务器内容被误报
# 为"整份删除"（+0 -391 之类假阳性），第一版脚本曾把 76 个文件全标为漂移。
# 当时用 GIT_INDEX_FILE 构造工作区 tree 规避。现在改用清单+哈希，这个坑自然消失。
# =============================================================================

set -uo pipefail
# 注意：刻意不加 set -e。本脚本是"对账报告"工具，某一侧的个别文件缺失/读取失败
# 不应导致整轮中止、丢失其余结论。关键失败点（SSH 不可达）已显式判空退出。

# ----------------------------- 配置 -----------------------------------------
PROJECT_DIR="/Users/leijiang/WorkBuddy/moneybag-for-claudecode"
SERVER="${MONEYBAG_SERVER:-150.158.47.189}"
REMOTE_USER="ubuntu"
REMOTE_PATH="/opt/moneybag"
SSH_KEY="${MONEYBAG_SSH_KEY:-$HOME/.ssh/id_ed25519}"

SSH_OPTS=(-o StrictHostKeyChecking=no -o BatchMode=yes -o ConnectTimeout=15)
[ -f "$SSH_KEY" ] && SSH_OPTS+=(-i "$SSH_KEY")

# 扫描范围：与 deploy_to_server.sh 的同步范围对齐，并覆盖全部后端源码树。
# - backend/   整棵后端树（含 prompts/ tests/ domain/ infra/ —— 旧脚本漏掉它们，
#              本轮线上 close_review.md 停旧版、holding_diagnose.md 整个缺失
#              就是"漂移检测器对出问题的那类文件结构性失明"）
# - pages/     前端页面
# - styles/    index.html 引用的样式目录（旧脚本只有 styles.css，漏了 styles/）
# - icons/     PWA 图标
# - tests/     根目录的 dev-only E2E 套件（打活服务、需真 token）。纳入扫描是为了
#              "看得见"而不是"假装它不存在"；它属白名单，不计漂移，见 NONDEPLOY。
SCAN_ROOTS="backend pages styles icons tests"
SCAN_ROOT_FILES="app.js index.html styles.css sw.js manifest.json"

# 必须能被扫到的关键路径（自检用）：少任何一个都说明脚本有盲区，直接报错。
REQUIRED_SCOPE="backend/prompts backend/tests backend/domain backend/infra backend/services pages styles icons manifest.json styles.css sw.js tests"

# 统一排除项（本地/服务器两侧必须完全一致，否则口径不一致会制造假漂移）
# 理由：
#   __pycache__/ *.pyc          —— Python 字节码，机器生成
#   .mypy_cache/ .pytest_cache/ —— 本地工具缓存
#   .git/                       —— 版本库内部
#   node_modules/ venv/ .venv/  —— 依赖目录
#   data-backup* backend/data/  —— 生产数据/备份，绝不能动、也绝不能比
#   backend/logs/ *.log         —— 运行日志，天然不同
#   *.bak* *.orig *.rej         —— 手工备份/补丁残留
#   .DS_Store                   —— macOS 垃圾
#   .env                        —— 服务器本地密钥文件（.env.example 不排除）
FIND_PRED='-not -path */__pycache__/* -not -name *.pyc
 -not -path */.mypy_cache/* -not -path */.pytest_cache/* -not -path */.git/*
 -not -path */node_modules/* -not -path */venv/* -not -path */.venv/*
 -not -path */data-backup* -not -path backend/data/* -not -path backend/logs/*
 -not -name *.bak* -not -name *.orig -not -name *.rej -not -name *.log
 -not -name .DS_Store -not -name .env'

# 运行时缓存路径：两侧内容本就可能不同（服务在跑，缓存会自己刷新）。
# 命中这些路径的差异归入"缓存差异（预期）"，不计为真漂移，--strict 也不据此退出。
# backend/infra/.cache/industry_board_cache.json 即属此类。
RUNTIME_CACHE_RE='/\.cache/'

# 白名单：预期【不纳入自动部署】的 dev-only 路径，两侧数量/内容不一致不算漂移。
# 两个前缀（唯一真相，NONDEPLOY_RE 由它派生，避免两处不一致）：
#   backend/tests/  单元测试
#   tests/          根目录 E2E 套件
# 理由（勿凭"大家都知道这是预期的"删掉本条，这正是本轮 P0 的教训形态：
# 一个"预期不部署"的类别若不写进脚本，将来真漏部署的文件混进同一类别就会被一句话盖过）：
#   1. 纯 dev 产物，生产运行不依赖：干净 venv 只装 requirements.txt + pytest，
#      backend/tests/ 全量 1582 passed（ci-wire-tests 实测），模块级不 import
#      chromadb/sentence-transformers/scipy/tushare/baostock。
#   2. backend/tests/conftest.py:129-151 顶层把 DATA_DIR 强制指向会话临时目录、
#      并【无视外部传入的 DATA_DIR】（注释记录了真实事故：带 DATA_DIR=/opt/moneybag/data
#      跑测试会直写生产 data/users，攒出 13 个脏用户文件）；:168-189 autouse 清空 14 个
#      密钥环境变量。把它同步进生产目录 = 把一个"会写盘、会读 .env"的东西放上线。
#   3. 根 tests/ 是【打活服务的 E2E】（tests/README.md 要求 127.0.0.1:8000 或
#      MB_TEST_HOST，会跑真实 DeepSeek 调用、耗 token，含 llm_heavy marker）——
#      设计上就属于"本地/有密钥环境专用"，不可能简单接进 CI。
#   4. 覆盖已由 CI 承担：.github/workflows/ci.yml 的 backend-test-suite job 每次 push
#      跑 backend/tests/ 全量；根 tests/ 里 CI 只跑自包含的 test_skeleton_m1.py
#      （venv 实测 219 passed / 2.0s，不需要服务器）。守卫在 CI，不靠"服务器磁盘上
#      有没有这些文件"。
# 语义是「不纳入【自动】部署，允许手工临时拷入」——所以这里既豁免"本地有服务器没有"
# 的漏部署误报，也豁免 tests 文件两侧内容不同（可能是历史上手工拷过去的旧副本：
# 服务器上 root tests/ 与 backend/tests/ 的部分副本就来自已废弃的根 deploy.sh 全库 rsync）。
# 一旦出现【非白名单】的"本地有服务器没有"条目，那才是真问题。
DEV_ONLY_PREFIXES="backend/tests tests"
NONDEPLOY_RE="^($(printf '%s' "$DEV_ONLY_PREFIXES" | tr ' ' '|'))/"

STRICT=0
[ "${1:-}" = "--strict" ] && STRICT=1

# ----------------------------- 基础准备 --------------------------------------
cd "$PROJECT_DIR" || { echo "❌ 项目目录不存在：$PROJECT_DIR"; exit 1; }

TMP_DIR="$(mktemp -d 2>/dev/null || mktemp -d -t checkdrift)"
LOCAL_HASH="$TMP_DIR/local.tsv"
SERVER_HASH="$TMP_DIR/server.tsv"
CLASSIFIED="$TMP_DIR/classified.tsv"
trap 'rm -rf "$TMP_DIR"' EXIT

# 本机哈希命令（macOS 默认没有 sha256sum，有 shasum）
pick_local_hasher() {
    if command -v shasum >/dev/null 2>&1; then echo "shasum -a 256"
    elif command -v sha256sum >/dev/null 2>&1; then echo "sha256sum"
    else return 1; fi
}

echo "════════════════════════════════════════════════════════════════"
echo " MoneyBag 漂移对账（文件清单 + sha256 内容哈希，不依赖服务器 git）"
echo " 本地  ：$PROJECT_DIR"
echo " 服务器：$REMOTE_USER@$SERVER:$REMOTE_PATH"
echo " 开始  ：$(date '+%Y-%m-%d %H:%M:%S')"
echo "════════════════════════════════════════════════════════════════"

# ----------------------------- 单文件模式 ------------------------------------
# 保留原有能力：给一个文件路径，直接展示两侧详细 diff。
if [ $# -eq 1 ] && [ "$1" != "--strict" ]; then
    f="$1"
    echo ""
    echo "=== 单文件对账：$f ==="
    echo ""
    echo "--- [1] 本地工作区 vs git main（是否有未提交改动）---"
    git --no-pager diff --stat main -- "$f" 2>/dev/null || echo "（无 git main 或无差异）"
    echo ""
    echo "--- [2] 本地磁盘实际内容 vs 服务器磁盘实际内容（最真实）---"
    if [ ! -f "$f" ]; then
        echo "⚠️  本地不存在该文件：$f"
    fi
    # 先探测服务器：区分「ssh 不可达」与「文件确实不存在」——
    # 不可达时绝不能误报成"未部署"（那是另一种闸门空转）。
    probe=$(ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$SERVER" \
        "if [ -f '$REMOTE_PATH/$f' ]; then echo EXISTS; else echo ABSENT; fi" 2>/dev/null)
    case "$probe" in
        EXISTS)
            if [ -f "$f" ]; then
                lh=$(shasum -a 256 "$f" 2>/dev/null | cut -c1-16)
                rh=$(ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$SERVER" "sha256sum '$REMOTE_PATH/$f' 2>/dev/null | cut -c1-16 || shasum -a 256 '$REMOTE_PATH/$f' | cut -c1-16")
                echo "本地 sha256(前16): $lh"
                echo "服务器 sha256(前16): $rh"
                if [ "$lh" = "$rh" ]; then
                    echo "✅ 内容一致"
                else
                    echo "⚠️  内容不一致，逐行 diff 如下（- 本地 / + 服务器）："
                    ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$SERVER" "cat '$REMOTE_PATH/$f'" 2>/dev/null \
                        | diff -u "$f" - || true
                fi
            fi
            ;;
        ABSENT)
            echo "服务器上不存在该文件（可能是漏部署、或本就不该部署）：$REMOTE_PATH/$f"
            ;;
        *)
            echo "❌ 无法连接服务器判断该文件（ssh 失败），不给出结论。"
            exit 3
            ;;
    esac
    echo ""
    exit 0
fi

# ----------------------------- SSH 可达性（硬门槛）----------------------------
# 服务器侧不可达时【明确报错退出】，绝不静默跳过然后报"一致"——
# 那又是"闸门空转仍显绿"。这是本脚本被坑的第二类教训。
echo ""
echo "[0/4] 检查 SSH 可达性 ..."
if ! ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$SERVER" 'echo ok' >/dev/null 2>&1; then
    echo "❌ 无法连接服务器 ${REMOTE_USER}@${SERVER}（ssh 失败）"
    echo "   SSH 选项：${SSH_OPTS[*]}"
    echo "   请确认网络/密钥/服务器状态后重试。为避免「闸门空转仍显绿」，此处直接退出。"
    exit 3
fi
echo "      ✅ 服务器可达"

# ----------------------------- 扫描范围自检 ----------------------------------
# 证明脚本没有盲区：打印实际扫描范围，并断言关键路径在范围内。
echo ""
echo "[1/4] 扫描范围自检"
echo "      扫描根      ：$SCAN_ROOTS"
echo "      扫描根文件  ：$SCAN_ROOT_FILES"
echo "      排除项      ：__pycache__/ *.pyc .mypy_cache/ .pytest_cache/ .git/"
echo "                    node_modules/ venv/ data-backup* backend/data/ backend/logs/"
echo "                    *.bak* *.orig *.rej *.log .DS_Store .env"

list_local_paths() {
    set -f   # 关闭 glob，让 FIND_PRED 里的通配符保持字面量
    for r in $SCAN_ROOTS; do
        [ -d "$r" ] && find "$r" -type f $FIND_PRED 2>/dev/null
    done
    for f in $SCAN_ROOT_FILES; do
        [ -f "$f" ] && echo "$f"
    done
    set +f
}

list_local_paths | LC_ALL=C sort -u > "$TMP_DIR/local.list"

SELF_CHECK_FAIL=0
for req in $REQUIRED_SCOPE; do
    if grep -q -e "^${req}/" -e "^${req}$" "$TMP_DIR/local.list"; then
        n=$(grep -c -e "^${req}/" -e "^${req}$" "$TMP_DIR/local.list")
        echo "      ✅ 在范围内：$req （本地 $n 项）"
    else
        echo "      ❌ 不在范围内（盲区！）：$req"
        SELF_CHECK_FAIL=1
    fi
done
if [ "$SELF_CHECK_FAIL" -ne 0 ]; then
    echo ""
    echo "❌ 自检失败：扫描范围存在盲区，拒绝继续对账（先修脚本，别信结论）。"
    exit 1
fi

# 白名单"反空转"断言：每个 dev-only 前缀必须在【本地】清单里有命中。
# 否则说明白名单指向了一个已不存在/已改名的路径 —— 它就成了永远不触发、也永远
# 不生效的死配置（"上锁没挂门"）。注意只断言本地侧：服务器侧不要求命中，
# 因为语义是「允许手工临时拷入」，服务器本来就可能没有全量。
for pre in $DEV_ONLY_PREFIXES; do
    n=$(grep -c -e "^${pre}/" "$TMP_DIR/local.list")
    if [ "$n" -gt 0 ]; then
        echo "      ✅ 白名单生效：$pre （本地 $n 项，计入第④类不计漂移）"
    else
        echo "      ❌ 白名单指向不存在的路径（死配置）：$pre"
        SELF_CHECK_FAIL=1
    fi
done
if [ "$SELF_CHECK_FAIL" -ne 0 ]; then
    echo ""
    echo "❌ 自检失败：白名单前缀在本地无任何命中，拒绝继续对账。"
    exit 1
fi
echo "      ✅ 自检通过：关键路径全部在扫描范围内，无结构性盲区"
echo "      ✅ 白名单规则：$NONDEPLOY_RE"

# ----------------------------- 本地侧：清单 + 哈希 ----------------------------
echo ""
echo "[2/4] 枚举本地文件并计算内容哈希 ..."
LOCAL_HASHER="$(pick_local_hasher)" || { echo "❌ 本机没有 shasum/sha256sum，无法计算哈希"; exit 1; }
while IFS= read -r f; do
    [ -n "$f" ] || continue
    h=$($LOCAL_HASHER "$f" 2>/dev/null | cut -c1-16)
    printf '%s\t%s\n' "$f" "$h"
done < "$TMP_DIR/local.list" | LC_ALL=C sort -t "$(printf '\t')" -k1,1 > "$LOCAL_HASH"
LOCAL_N=$(wc -l < "$LOCAL_HASH" | tr -d ' ')
echo "      ✅ 本地 ${LOCAL_N} 个文件（哈希器：${LOCAL_HASHER}）"

# ----------------------------- 服务器侧：清单 + 哈希 --------------------------
# 用【同一套】FIND_PRED 枚举，避免两边口径不一致制造假漂移。
echo ""
echo "[3/4] 枚举服务器文件并计算内容哈希（ssh 一次往返）..."
ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$SERVER" "bash -s" <<REMOTE > "$SERVER_HASH"
cd "$REMOTE_PATH" || exit 9
set -f
FIND_PRED='$FIND_PRED'
SCAN_ROOTS='$SCAN_ROOTS'
SCAN_ROOT_FILES='$SCAN_ROOT_FILES'
if command -v sha256sum >/dev/null 2>&1; then HASH_CMD='sha256sum';
elif command -v shasum >/dev/null 2>&1; then HASH_CMD='shasum -a 256';
else echo 'NO_HASHER' >&2; exit 8; fi
{
  for r in \$SCAN_ROOTS; do
      [ -d "\$r" ] && find "\$r" -type f \$FIND_PRED 2>/dev/null
  done
  for f in \$SCAN_ROOT_FILES; do
      [ -f "\$f" ] && echo "\$f"
  done
} | LC_ALL=C sort -u | while IFS= read -r f; do
      [ -n "\$f" ] || continue
      h=\$(\$HASH_CMD "\$f" 2>/dev/null | cut -c1-16)
      printf '%s\t%s\n' "\$f" "\$h"
  done
REMOTE

if [ ! -s "$SERVER_HASH" ]; then
    echo "❌ 服务器侧未返回任何文件（清单为空）。可能远程路径不对或 ssh 中途失败。"
    echo "   为避免「空转显绿」，此处直接退出，不报「一致」。"
    exit 3
fi
LC_ALL=C sort -t "$(printf '\t')" -k1,1 -o "$SERVER_HASH" "$SERVER_HASH"
SERVER_N=$(wc -l < "$SERVER_HASH" | tr -d ' ')
echo "      ✅ 服务器 $SERVER_N 个文件"

# ----------------------------- 分类对账 --------------------------------------
# 三类结果分开列，绝不混在一起：
#   DIFF  = 内容不一致（真漂移，最需要关注）
#   LONLY = 本地有、服务器没有（可能漏部署，也可能本就不该部署）
#   SONLY = 服务器有、本地没有（垃圾 .bak、服务器本地 .env、运行日志等）
echo ""
echo "[4/4] 逐文件比对内容哈希 ..."
awk -F'\t' '
FNR==NR { lh[$1]=$2; next }
{ sh[$1]=$2 }
END {
    for (p in lh) {
        if (!(p in sh)) printf "LONLY\t%s\n", p;
        else if (lh[p] != sh[p]) printf "DIFF\t%s\t%s\t%s\n", p, lh[p], sh[p];
    }
    for (p in sh) if (!(p in lh)) printf "SONLY\t%s\n", p;
}' "$LOCAL_HASH" "$SERVER_HASH" | LC_ALL=C sort > "$CLASSIFIED"

DIFF_REAL=0; DIFF_CACHE=0; DIFF_WHITE=0
LONLY_REAL=0; LONLY_WHITE=0; SONLY_N=0
WHITE_LIST="$TMP_DIR/whitelist.txt"; : > "$WHITE_LIST"

print_header() { echo ""; echo "──────── $1 ────────"; }

# --- 内容不一致 ---
print_header "① 内容不一致（真漂移）"
while IFS=$'\t' read -r cat path lh rh; do
    [ "$cat" = "DIFF" ] || continue
    if printf '%s' "$path" | grep -qE "$RUNTIME_CACHE_RE"; then
        DIFF_CACHE=$((DIFF_CACHE+1))
        printf "  [缓存·预期] %-58s %s → %s\n" "$path" "$lh" "$rh"
    elif printf '%s' "$path" | grep -qE "$NONDEPLOY_RE"; then
        DIFF_WHITE=$((DIFF_WHITE+1))
        printf '%s\t%s\n' "DIFF" "$path" >> "$WHITE_LIST"
    else
        DIFF_REAL=$((DIFF_REAL+1))
        printf "  ⚠️  %-58s %s → %s\n" "$path" "$lh" "$rh"
    fi
done < "$CLASSIFIED"
[ "$DIFF_REAL" -eq 0 ] && [ "$DIFF_CACHE" -eq 0 ] && [ "$DIFF_WHITE" -eq 0 ] && echo "  ✅ 无"

# --- 本地有、服务器没有 ---
print_header "② 本地有、服务器没有（疑似漏部署）"
while IFS=$'\t' read -r cat path; do
    [ "$cat" = "LONLY" ] || continue
    if printf '%s' "$path" | grep -qE "$NONDEPLOY_RE"; then
        LONLY_WHITE=$((LONLY_WHITE+1))
        printf '%s\t%s\n' "LONLY" "$path" >> "$WHITE_LIST"
    else
        LONLY_REAL=$((LONLY_REAL+1))
        printf "  L  %s\n" "$path"
    fi
done < "$CLASSIFIED"
[ "$LONLY_REAL" -eq 0 ] && echo "  ✅ 无"

# --- 服务器有、本地没有 ---
print_header "③ 服务器有、本地没有（垃圾/服务器本地文件）"
while IFS=$'\t' read -r cat path; do
    [ "$cat" = "SONLY" ] || continue
    SONLY_N=$((SONLY_N+1))
    printf "  S  %s\n" "$path"
done < "$CLASSIFIED"
[ "$SONLY_N" -eq 0 ] && echo "  ✅ 无"

# --- 白名单（预期不部署/允许不一致）---
print_header "④ 白名单：预期不部署，不计漂移（dev-only）"
echo "  匹配规则：$NONDEPLOY_RE"
if [ "$((DIFF_WHITE+LONLY_WHITE))" -eq 0 ]; then
    echo "  ✅ 无"
else
    printf "  内容不同 %s 项 / 本地独有 %s 项（明细见下，仅信息提示）\n" "$DIFF_WHITE" "$LONLY_WHITE"
    while IFS=$'\t' read -r kind path; do
        case "$kind" in
            DIFF)  printf "  [内容不同] %s\n" "$path" ;;
            LONLY) printf "  [未部署]   %s\n" "$path" ;;
        esac
    done < "$WHITE_LIST"
fi

# ----------------------------- 汇总 ------------------------------------------
echo ""
echo "════════════════════════════════════════════════════════════════"
printf "汇总：本地 %s 个 / 服务器 %s 个\n" "$LOCAL_N" "$SERVER_N"
printf "      内容不一致：%s 个（真漂移 %s，运行时缓存 %s，白名单 %s）\n" \
       "$((DIFF_REAL+DIFF_CACHE+DIFF_WHITE))" "$DIFF_REAL" "$DIFF_CACHE" "$DIFF_WHITE"
printf "      本地有服务器没有：%s 个（疑似漏部署 %s，白名单未部署 %s）\n" \
       "$((LONLY_REAL+LONLY_WHITE))" "$LONLY_REAL" "$LONLY_WHITE"
printf "      服务器有本地没有：%s 个\n" "$SONLY_N"
if [ "$DIFF_REAL" -eq 0 ] && [ "$LONLY_REAL" -eq 0 ]; then
    echo "结论：✅ 未发现真漂移（白名单/运行时缓存之外的差异为零）"
else
    echo "结论：⚠️  发现 $DIFF_REAL 个真漂移、$LONLY_REAL 个疑似漏部署，请逐个确认后重新部署"
fi
echo "════════════════════════════════════════════════════════════════"

# ----------------------------- 本地未提交改动（参考）-------------------------
echo ""
echo "=== 本地工作区 vs git main（尚未 commit 的改动，仅供参考——不代表和服务器不一致）==="
LOCAL_DIFF=$(git --no-pager diff --name-only main -- backend pages app.js index.html styles.css sw.js manifest.json styles icons 2>/dev/null)
UNTRACKED=$(git status --porcelain -- backend pages app.js index.html styles.css sw.js manifest.json styles icons 2>/dev/null | grep '^??' | awk '{print $2}')
if [ -z "$LOCAL_DIFF" ] && [ -z "$UNTRACKED" ]; then
    echo "✅ 无未提交改动"
else
    [ -n "$LOCAL_DIFF" ] && echo "$LOCAL_DIFF" | sed 's/^/UNCOMMITTED  /'
    [ -n "$UNTRACKED" ] && echo "$UNTRACKED" | sed 's/^/UNTRACKED    /'
fi

echo ""
echo "提示：对某个具体文件想看详细 diff，运行："
echo "  bash scripts/check-drift.sh <文件路径>"
echo "（对照服务器最新状态不再需要 git fetch server —— 本脚本直连服务器磁盘）"

# ----------------------------- 退出码（可做闸门）-----------------------------
# 白名单（backend/tests 等 dev-only）与运行时缓存不计入闸门，保证"闸门一响即真问题"。
if [ "$STRICT" = "1" ]; then
    if [ "$DIFF_REAL" -gt 0 ] || [ "$LONLY_REAL" -gt 0 ] || [ "$SONLY_N" -gt 0 ]; then
        exit 2
    fi
fi
exit 0

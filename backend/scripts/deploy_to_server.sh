#!/usr/bin/env bash
# deploy_to_server.sh — 将本地改动同步到生产服务器并重启
# 用法：bash backend/scripts/deploy_to_server.sh [SERVER_IP] [SSH_KEY]
# 例：  bash backend/scripts/deploy_to_server.sh 150.158.47.189 ~/.ssh/id_rsa

set -e

SERVER="${1:-150.158.47.189}"
# 优先使用 ed25519，回退到 rsa
SSH_KEY="${2:-}"
if [ -z "$SSH_KEY" ]; then
    if [ -f "$HOME/.ssh/id_ed25519" ]; then
        SSH_KEY="$HOME/.ssh/id_ed25519"
    elif [ -f "$HOME/.ssh/id_rsa" ]; then
        SSH_KEY="$HOME/.ssh/id_rsa"
    fi
fi
REMOTE_PATH="/opt/moneybag"
REMOTE_USER="ubuntu"

# ---- 自动检测 SSH Key ----
if [ -f "$SSH_KEY" ]; then
    SSH_OPTS="-i $SSH_KEY -o StrictHostKeyChecking=no"
    echo "  🔐 使用 SSH Key: $SSH_KEY"
else
    SSH_OPTS="-o StrictHostKeyChecking=no"
    echo "  🔐 SSH Key 不存在，使用密码登录（会提示输入密码）"
    echo "  👉 配置一键部署: bash backend/scripts/setup_deploy.sh"
fi

SSH="ssh $SSH_OPTS $REMOTE_USER@$SERVER"
SCP="scp $SSH_OPTS"
RSYNC="rsync -avz --delete -e \"ssh $SSH_OPTS\""

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

echo "=== 钱袋子 M7+ 部署 ==="
echo "目标服务器: $SERVER ($REMOTE_USER)"
echo "本地仓库:   $REPO_ROOT"
echo ""

# ---- 1. 同步后端核心文件（精确覆盖）----
echo "[1/7] 同步后端根文件..."
BACKEND_FILES=(
    "backend/main.py"
    "backend/config.py"
)

for f in "${BACKEND_FILES[@]}"; do
    if [ -f "$REPO_ROOT/$f" ]; then
        echo "  → $f"
        $SCP "$REPO_ROOT/$f" "$REMOTE_USER@$SERVER:$REMOTE_PATH/$f"
    else
        echo "  ⚠️  跳过不存在: $f"
    fi
done

# ---- 1.6 同步后端「松散」文件（不在任何 BACKEND_DIRS 子目录下的 python 文件）----
# BACKEND_FILES 是 scp 逐文件清单、BACKEND_DIRS 是目录级 rsync，两者都不覆盖这类文件：
#   - backend/domain/__init__.py / backend/infra/__init__.py —— 父包自身的 __init__.py。
#     清单只列了它们的子目录，父包文件从未上线；而 domain. / infra. 作为包被 import 时
#     这两个 __init__.py 会真的执行。
#   - backend/__init__.py —— backend 包根（0 字节），一并纳入以保持一致。
#   - backend/infra/auth.py —— main.py:40 与 api/auth.py:13 都 `from infra.auth import ...`，
#     是运行时硬依赖，却直接躺在 infra/ 下、不在任何被同步的子目录里。
# 单列一个数组而不是塞进 BACKEND_FILES，是为了让「后端根文件精确覆盖」与
# 「松散包文件覆盖」两类语义清晰分开，便于后续增补时不再漏。
BACKEND_LOOSE_FILES=(
    "backend/__init__.py"
    "backend/domain/__init__.py"
    "backend/infra/__init__.py"
    "backend/infra/auth.py"
)

for f in "${BACKEND_LOOSE_FILES[@]}"; do
    if [ -f "$REPO_ROOT/$f" ]; then
        echo "  → $f"
        $SCP "$REPO_ROOT/$f" "$REMOTE_USER@$SERVER:$REMOTE_PATH/$f"
    else
        echo "  ⚠️  跳过不存在: $f"
    fi
done

# ---- 1.5 同步后端新增目录（rsync 增量，M7+ 新模块）----
echo "[2/7] 同步后端目录..."
BACKEND_DIRS=(
    "backend/api/"
    "backend/routers/"
    "backend/services/"
    "backend/domain/rule_engine/"
    "backend/domain/models/"
    "backend/domain/services/"
    "backend/domain/protocols/"
    "backend/infra/data_source/"
    "backend/infra/cache/"
    "backend/infra/store/"
    "backend/infra/llm/"
    "backend/infra/knowledge/"
    "backend/use_cases/"
    # ⚠️ backend/models/ 是运行时硬依赖：api/signals.py / api/user.py / api/chat.py /
    # api/portfolio.py 都有 `from models.schemas import ...`。此前整个目录不在任何清单里，
    # 服务器上的内容只是碰巧与本地一致（靠已废弃的旧根目录 deploy.sh 全量传过一次）；
    # 以后改 schemas.py 永远上不了线。加进来后 --delete 会顺手清掉服务器上的
    # schemas.py.bak-* 垃圾备份，这是期望行为。
    "backend/models/"
    "backend/scripts/"
    # ⚠️ 运行时资产目录：api/shared_helpers.py._load_named_prompt() 会按文件名读取
    # backend/prompts/{x}.md，缺失时 fail-open 走内置兜底。此前不在本清单里 ——
    # 2026-09-13 实测线上 close_review.md 停在 8/30 旧版、holding_diagnose.md 整个缺失，
    # 即本轮 prompt 加固根本没生效；同时线上还滞留三个已删除的死 prompt
    # (portfolio_diagnose.md / signal_extract.md / weekly_report.md)。
    # 加 --delete 后，本地删除会同步到线上。
    "backend/prompts/"
    # ⚠️ 测试目录必须在清单里，否则服务器上的 backend/tests/ 是一份永不更新的化石。
    # 2026-09-13 v9.9.30 实测：服务器只有 52 个 test_*.py（收集 1116 条），本地有 87 个
    # （1834 条）。跑「服务器回归」= 拿 9/7、9/11 的旧测试打 9/13 的新源码，
    # 报出 3 个 FAILED 全是假的：
    #   - test_regression_signal_and_cache.py 的 fake_cfo 缺 cfo_cache_path/write_cfo_cache
    #     （本地 9/13 已补，并顺带修掉「缓存重写没被测到」的假绿）
    #   - test_chat_model_routing.py 的 _FakeGateway.pre_check() 缺 user_id 形参
    #     （本地 9/13 已补）
    # 假红和假绿一样有害：会让人去"修"根本没坏的东西，也会让人不再相信这套回归。
    # 测试文件不被运行时 import，进生产无副作用；conftest.py 无条件隔离 DATA_DIR，
    # 在服务器上跑也不会碰生产数据。
    "backend/tests/"
)

for d in "${BACKEND_DIRS[@]}"; do
    if [ -d "$REPO_ROOT/$d" ]; then
        echo "  → rsync $d"
        eval "$RSYNC --exclude='__pycache__' --exclude='*.pyc' \"$REPO_ROOT/$d\" \"$REMOTE_USER@$SERVER:$REMOTE_PATH/$d\""
    else
        echo "  ⚠️  跳过不存在: $d"
    fi
done

# ---- 2. 同步前端改动文件 ----
echo "[3/7] 同步前端代码..."
# ⚠️ 手工维护的清单 —— 加前端根文件必须同时加到这里，否则「本地改了、线上永远旧」。
# 2026-09-13 v9.9.26 实测踩中：sw.js 的 CACHE_NAME 每轮 bump 都改、都提交、都 push，
# 但它不在本清单里 → 线上 CACHE_NAME 冻在 moneybag-v9923-cache，版本标记与真实产物不一致，
# 且 sw.js 自身的任何改动（缓存策略、预缓存清单）永远无法上线。
# 防复发：backend/tests/test_deploy_asset_coverage.py 会校验本清单覆盖
# index.html / sw.js / manifest.json 引用到的全部静态资产，漏了直接测试转红。
FRONTEND_FILES=(
    "app.js"
    "index.html"
    "styles.css"
    "sw.js"
    "manifest.json"
)

# 前端目录（rsync 增量）—— 同上，index.html 引用到的目录必须在这里
FRONTEND_DIRS=(
    "styles/"
    "icons/"
)
for f in "${FRONTEND_FILES[@]}"; do
    if [ -f "$REPO_ROOT/$f" ]; then
        echo "  → $f"
        $SCP "$REPO_ROOT/$f" "$REMOTE_USER@$SERVER:$REMOTE_PATH/$f"
    else
        echo "  ⚠️  跳过不存在: $f"
    fi
done

# 同步前端 pages 目录
if [ -d "$REPO_ROOT/pages" ]; then
    echo "  → rsync pages/"
    eval "$RSYNC --exclude='__pycache__' \"$REPO_ROOT/pages/\" \"$REMOTE_USER@$SERVER:$REMOTE_PATH/pages/\""
fi

# 同步其余前端目录（styles/ icons/ —— 此前完全不在任何同步清单里，靠"恰好没改过"蒙混）
for d in "${FRONTEND_DIRS[@]}"; do
    if [ -d "$REPO_ROOT/$d" ]; then
        echo "  → rsync $d"
        eval "$RSYNC --exclude='__pycache__' \"$REPO_ROOT/$d\" \"$REMOTE_USER@$SERVER:$REMOTE_PATH/$d\""
    else
        echo "  ⚠️  跳过不存在: $d"
    fi
done

# ---- 检测是否密码登录（sudo 操作需要交互式，非交互式跳过）----
USE_PASSWORD_LOGIN=false
if [ ! -f "$SSH_KEY" ]; then
    USE_PASSWORD_LOGIN=true
fi

# ---- 4. 修复 systemd 环境变量（TUSHARE_TOKEN + DATA_DIR）----
echo "[4/7] 修复 systemd 环境变量..."
if [ "$USE_PASSWORD_LOGIN" = true ]; then
    echo "  ⚠️  密码登录无法自动修改 systemd（需要 sudo + 交互式）"
    echo "  👉 跳过自动 systemd 配置，请手动检查："
    echo "     ssh $REMOTE_USER@$SERVER"
    echo "     sudo cat /etc/systemd/system/moneybag.service | grep -E 'TUSHARE_TOKEN|DATA_DIR'"
else
    $SSH "
# 读取 .env 文件中的 TUSHARE_TOKEN
ENV_FILE=$REMOTE_PATH/backend/.env
TUSHARE=\$(grep '^TUSHARE_TOKEN=' \"\$ENV_FILE\" 2>/dev/null | cut -d= -f2 | tr -d '\"' || echo '')
if [ -z \"\$TUSHARE\" ]; then
    echo '  ⚠️  .env 中未找到 TUSHARE_TOKEN，跳过'
else
    # 检查 systemd service 文件中是否已有 TUSHARE_TOKEN
    SERVICE_FILE=/etc/systemd/system/moneybag.service
    if [ -f \"\$SERVICE_FILE\" ]; then
        if grep -q 'TUSHARE_TOKEN' \"\$SERVICE_FILE\"; then
            echo '  ✅ TUSHARE_TOKEN 已在 systemd 中配置'
        else
            # 在 [Service] 段的 Environment= 行后追加（或新增）
            if grep -q 'Environment=' \"\$SERVICE_FILE\"; then
                # 追加到现有 Environment= 行后
                sudo sed -i \"/^Environment=/a Environment=TUSHARE_TOKEN=\$TUSHARE\" \"\$SERVICE_FILE\"
            else
                sudo sed -i \"/^\[Service\]/a Environment=TUSHARE_TOKEN=\$TUSHARE\" \"\$SERVICE_FILE\"
            fi
            echo '  ✅ TUSHARE_TOKEN 已添加到 systemd'
        fi
        # 同样检查 DATA_DIR
        if grep -q 'DATA_DIR' \"\$SERVICE_FILE\"; then
            echo '  ✅ DATA_DIR 已在 systemd 中配置'
        else
            if grep -q 'Environment=' \"\$SERVICE_FILE\"; then
                sudo sed -i \"/^Environment=/a Environment=DATA_DIR=$REMOTE_PATH/data\" \"\$SERVICE_FILE\"
            else
                sudo sed -i \"/^\[Service\]/a Environment=DATA_DIR=$REMOTE_PATH/data\" \"\$SERVICE_FILE\"
            fi
            echo '  ✅ DATA_DIR 已添加到 systemd'
        fi
        systemctl daemon-reload
        echo '  ✅ systemd daemon-reload 完成'
    else
        echo '  ⚠️  未找到 /etc/systemd/system/moneybag.service，跳过'
    fi
fi
"
fi

# ---- 5. 重启后端服务 ----
echo "[5/7] 重启后端服务..."
if [ "$USE_PASSWORD_LOGIN" = true ]; then
    echo "  ⚠️  密码登录无法自动重启服务（需要 sudo）"
    echo "  👉 请手动执行："
    echo "     ssh $REMOTE_USER@$SERVER"
    echo "     sudo systemctl restart moneybag"
    echo "     sudo systemctl status moneybag"
else
    # FIX: 部署后修复 data/ 目录权限（防止 root 创建的文件导致 ubuntu 写入失败）
    $SSH "sudo chown -R ubuntu:ubuntu $REMOTE_PATH/data/ 2>/dev/null; echo '  ✅ data/ 权限已修复'" || true
    $SSH "sudo systemctl restart moneybag 2>/dev/null || \
        (pkill -f 'uvicorn main:app' 2>/dev/null; sleep 2; \
         cd $REMOTE_PATH/backend && nohup /opt/moneybag/venv/bin/uvicorn main:app \
         --host 0.0.0.0 --port 8000 --workers 2 \
         >> /opt/moneybag/logs/uvicorn.log 2>&1 &)"
    echo "  ✅ 服务已重启"
fi

# ---- 6. 检查定时任务 cron ----
echo "[6/7] 检查 cron 配置..."
if [ "$USE_PASSWORD_LOGIN" = true ]; then
    echo "  ⚠️  密码登录跳过 cron 自动检查"
    echo "  👉 请手动检查：ssh $REMOTE_USER@$SERVER 'crontab -l'"
    echo "  👉 确认包含：night_worker / stock_monitor --close / weekly_review_cron"
else
    $SSH "
CRON_LINE='0 1 * * * cd $REMOTE_PATH/backend && /opt/moneybag/venv/bin/python scripts/night_worker.py >> /opt/moneybag/logs/night.log 2>&1'
PUSH_CRON_LINE='30 8 * * 1-5 cd $REMOTE_PATH/backend && /opt/moneybag/venv/bin/python scripts/night_worker.py --push-only >> /opt/moneybag/logs/night.log 2>&1'
WEEKLY_CRON_LINE='30 15 * * 5 cd $REMOTE_PATH/backend && /opt/moneybag/venv/bin/python scripts/weekly_review_cron.py >> /opt/moneybag/logs/weekly_review.log 2>&1'
CLOSE_CRON_LINE='30 15 * * 1-5 cd $REMOTE_PATH/backend && /opt/moneybag/venv/bin/python scripts/stock_monitor_cron.py --close >> /opt/moneybag/logs/stock_monitor.log 2>&1'

# night_worker
EXISTING=\$(crontab -l 2>/dev/null | grep -c 'night_worker' || echo 0)
if [ \"\$EXISTING\" -eq 0 ]; then
    echo '  添加 night_worker cron...'
    (crontab -l 2>/dev/null; echo \"\$CRON_LINE\"; echo \"\$PUSH_CRON_LINE\") | crontab -
    echo '  ✅ night_worker cron 已添加'
else
    PUSH_EXISTING=\$(crontab -l 2>/dev/null | grep -c 'push-only' || echo 0)
    if [ \"\$PUSH_EXISTING\" -eq 0 ]; then
        (crontab -l 2>/dev/null; echo \"\$PUSH_CRON_LINE\") | crontab -
        echo '  ✅ 兜底推送 cron 已补充'
    else
        echo '  ✅ night_worker cron 已存在'
    fi
fi

# weekly_review_cron（周五15:30推送周报）
WEEKLY_EXISTING=\$(crontab -l 2>/dev/null | grep -c 'weekly_review_cron' || echo 0)
if [ \"\$WEEKLY_EXISTING\" -eq 0 ]; then
    echo '  添加 weekly_review_cron...'
    (crontab -l 2>/dev/null; echo \"\$WEEKLY_CRON_LINE\") | crontab -
    echo '  ✅ 周报 cron 已添加（周五 15:30）'
else
    echo '  ✅ weekly_review_cron 已存在'
fi

# stock_monitor --close（工作日15:30收盘复盘）
CLOSE_EXISTING=\$(crontab -l 2>/dev/null | grep -c 'stock_monitor.*close' || echo 0)
if [ \"\$CLOSE_EXISTING\" -eq 0 ]; then
    echo '  添加 stock_monitor --close cron...'
    (crontab -l 2>/dev/null; echo \"\$CLOSE_CRON_LINE\") | crontab -
    echo '  ✅ 收盘复盘 cron 已添加（工作日 15:30）'
else
    echo '  ✅ stock_monitor --close 已存在'
fi

echo '  --- 当前 cron 任务 ---'
crontab -l | grep -E 'night_worker|stock_monitor|weekly_review|cache_warmer' || echo '  (无相关 cron 条目)'
"
fi

# ---- 7. 冒烟测试 ----
echo "[7/7] 冒烟测试..."
sleep 5  # 等待服务启动
BASE="http://$SERVER:8000"

SMOKE_FAIL=0
SMOKE_TOTAL=0

# check_endpoint <url> <desc> [预算秒=10] [慢响应阈值秒=5]
# 预算必须按端点冷启动真实耗时给：重启后内存缓存被清空，
# 需要现算的端点（如晨报）冷态可到 40s+，硬编码 10s 会把健康端点误报为 ❌。
check_endpoint() {
    local url="$1"
    local desc="$2"
    local budget="${3:-10}"
    local slow_at="${4:-5}"
    local out code elapsed
    SMOKE_TOTAL=$((SMOKE_TOTAL + 1))
    out=$(curl -s -o /dev/null -w "%{http_code} %{time_total}" --max-time "$budget" "$url" 2>/dev/null || true)
    [ -n "$out" ] || out="000 0"
    out="${out%%$'\n'*}"   # 超时(curl rc!=0)时 curl 已打印 "000 <elapsed>"，避免再拼一行导致 elapsed 被读成 0
    code="${out%% *}"
    elapsed="${out##* }"
    if [ "$code" = "200" ]; then
        # 200 不等于体验可接受：冷启动慢的端点单独标注，别让它冒充绿
        if awk "BEGIN{exit !($elapsed > $slow_at)}" 2>/dev/null; then
            echo "  ⚠️  $desc — HTTP 200 但耗时 ${elapsed}s（冷启动慢，预算 ${budget}s）"
        else
            echo "  ✅ $desc ($url)"
        fi
    else
        SMOKE_FAIL=$((SMOKE_FAIL + 1))
        # 注意：${code} 必须带花括号。紧跟全角字符时会话 locale 非 UTF-8 时
        # bash 会把多字节字节当成变量名的一部分（unbound variable），set -e 下直接崩。
        echo "  ❌ $desc — HTTP ${code}（预算 ${budget}s / 实测 ${elapsed}s）($url)"
    fi
}

check_endpoint "$BASE/api/timing?userId=default"        "MB-007 置信度"
check_endpoint "$BASE/api/stock-screen?userId=default"  "MB-010/011 推荐列表"
check_endpoint "$BASE/api/risk-metrics?userId=default"  "MB-017/016 风险指标 GET"
check_endpoint "$BASE/api/news"                         "MB-012 新闻列表"
check_endpoint "$BASE/api/news/deep-impact"             "MB-008 深度新闻分析"
check_endpoint "$BASE/api/global/snapshot"              "MB-015 全球快照"
check_endpoint "$BASE/api/steward/briefing?userId=default"         "MB-018 晨报缓存" 120 15
check_endpoint "$BASE/api/steward/briefing-history?userId=default" "MB-005 往期晨报"

# 验证新闻条数
NEWS_COUNT=$(curl -s "$BASE/api/news?limit=20" | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d.get('news',[])))" 2>/dev/null || echo "?")
echo "  📰 新闻条数: $NEWS_COUNT (期望 ≥15)"

if [ "$SMOKE_FAIL" -eq 0 ]; then
    echo "  ── 冒烟汇总: $SMOKE_TOTAL/$SMOKE_TOTAL 项通过 ──"
else
    echo "  ── 冒烟汇总: $SMOKE_FAIL/$SMOKE_TOTAL 项失败 ──"
fi

echo ""

# ---- 密码登录时的手动收尾提示 ----
if [ "$USE_PASSWORD_LOGIN" = true ]; then
    echo "═══════════════════════════════════════════════════════════"
    echo "⚠️  密码登录模式：以下步骤需手动完成"
    echo "═══════════════════════════════════════════════════════════"
    echo ""
    echo "1️⃣  安装新依赖（如有）："
    echo "   ssh $REMOTE_USER@$SERVER"
    echo "   /opt/moneybag/venv/bin/pip install pandas openpyxl"
    echo ""
    echo "2️⃣  重启服务："
    echo "   sudo systemctl restart moneybag"
    echo "   sudo systemctl status moneybag"
    echo ""
    echo "3️⃣  检查 cron："
    echo "   crontab -l | grep night_worker"
    echo ""
    echo "═══════════════════════════════════════════════════════════"
fi

echo "验证 timing confidence: curl -s '$BASE/api/timing?userId=default' | python3 -m json.tool | grep confidence"
echo "验证 risk-metrics GET:  curl -s '$BASE/api/risk-metrics?userId=default' | python3 -m json.tool | head -5"

# 冒烟未通过时以非零码退出：文件同步成功≠服务健康。
# 否则 200/❌ 都只打印在屏幕上，CI 和调用方（bump_and_deploy.sh）无从判断，
# 就是「闸门空转仍显绿」。
if [ "$SMOKE_FAIL" -ne 0 ]; then
    echo ""
    echo "=== 部署收尾：文件已同步、服务已重启，但冒烟测试 $SMOKE_FAIL/$SMOKE_TOTAL 项失败 ==="
    exit 1
fi

echo "=== 部署完成 ==="

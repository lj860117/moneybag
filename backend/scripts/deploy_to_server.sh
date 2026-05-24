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
    "backend/scripts/"
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
FRONTEND_FILES=(
    "app.js"
    "index.html"
    "styles.css"
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

check_endpoint() {
    local url="$1"
    local desc="$2"
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$url" 2>/dev/null || echo "000")
    if [ "$code" = "200" ]; then
        echo "  ✅ $desc ($url)"
    else
        echo "  ❌ $desc — HTTP $code ($url)"
    fi
}

check_endpoint "$BASE/api/timing?userId=default"        "MB-007 置信度"
check_endpoint "$BASE/api/stock-screen?userId=default"  "MB-010/011 推荐列表"
check_endpoint "$BASE/api/risk-metrics?userId=default"  "MB-017/016 风险指标 GET"
check_endpoint "$BASE/api/news"                         "MB-012 新闻列表"
check_endpoint "$BASE/api/news/deep-impact"             "MB-008 深度新闻分析"
check_endpoint "$BASE/api/global/snapshot"              "MB-015 全球快照"
check_endpoint "$BASE/api/steward/briefing?userId=default"         "MB-018 晨报缓存"
check_endpoint "$BASE/api/steward/briefing-history?userId=default" "MB-005 往期晨报"

# 验证新闻条数
NEWS_COUNT=$(curl -s "$BASE/api/news?limit=20" | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d.get('news',[])))" 2>/dev/null || echo "?")
echo "  📰 新闻条数: $NEWS_COUNT (期望 ≥15)"

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

echo "=== 部署完成 ==="
echo "验证 timing confidence: curl -s '$BASE/api/timing?userId=default' | python3 -m json.tool | grep confidence"
echo "验证 risk-metrics GET:  curl -s '$BASE/api/risk-metrics?userId=default' | python3 -m json.tool | head -5"

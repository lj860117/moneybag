#!/usr/bin/env bash
# deploy_to_server.sh — 将本地改动同步到生产服务器并重启
# 用法：bash backend/scripts/deploy_to_server.sh [SERVER_IP] [SSH_KEY]
# 例：  bash backend/scripts/deploy_to_server.sh 150.158.47.189 ~/.ssh/id_rsa

set -e

SERVER="${1:-150.158.47.189}"
SSH_KEY="${2:-~/.ssh/id_rsa}"
REMOTE_PATH="/opt/moneybag"
REMOTE_USER="root"

SSH="ssh -i $SSH_KEY -o StrictHostKeyChecking=no $REMOTE_USER@$SERVER"
SCP="scp -i $SSH_KEY -o StrictHostKeyChecking=no"

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

echo "=== 钱袋子 P0/P1/P2 Bug修复 部署 ==="
echo "目标服务器: $SERVER"
echo "本地仓库:   $REPO_ROOT"
echo ""

# ---- 1. 同步后端改动文件 ----
echo "[1/6] 同步后端代码..."
BACKEND_FILES=(
    "backend/api/signals.py"
    "backend/api/portfolio.py"
    "backend/api/news.py"
    "backend/api/global_market.py"
    "backend/api/steward.py"
    "backend/services/stock_monitor.py"
    "backend/services/stock_screen.py"
    "backend/services/steward.py"
    "backend/services/news_data.py"
    "backend/scripts/stock_monitor_cron.py"
)

for f in "${BACKEND_FILES[@]}"; do
    echo "  → $f"
    $SCP "$REPO_ROOT/$f" "$REMOTE_USER@$SERVER:$REMOTE_PATH/$f"
done

# ---- 2. 同步前端改动文件 ----
echo "[2/6] 同步前端代码..."
FRONTEND_FILES=(
    "app.js"
    "pages/analysis.js"
    "pages/history.js"
    "pages/insight.js"
)
for f in "${FRONTEND_FILES[@]}"; do
    echo "  → $f"
    $SCP "$REPO_ROOT/$f" "$REMOTE_USER@$SERVER:$REMOTE_PATH/$f"
done

# ---- 3. 修复 systemd 环境变量（TUSHARE_TOKEN + DATA_DIR）----
echo "[3/6] 修复 systemd 环境变量..."
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
                sed -i \"/^Environment=/a Environment=TUSHARE_TOKEN=\$TUSHARE\" \"\$SERVICE_FILE\"
            else
                sed -i \"/^\[Service\]/a Environment=TUSHARE_TOKEN=\$TUSHARE\" \"\$SERVICE_FILE\"
            fi
            echo '  ✅ TUSHARE_TOKEN 已添加到 systemd'
        fi
        # 同样检查 DATA_DIR
        if grep -q 'DATA_DIR' \"\$SERVICE_FILE\"; then
            echo '  ✅ DATA_DIR 已在 systemd 中配置'
        else
            if grep -q 'Environment=' \"\$SERVICE_FILE\"; then
                sed -i \"/^Environment=/a Environment=DATA_DIR=$REMOTE_PATH/data\" \"\$SERVICE_FILE\"
            else
                sed -i \"/^\[Service\]/a Environment=DATA_DIR=$REMOTE_PATH/data\" \"\$SERVICE_FILE\"
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

# ---- 4. 重启后端服务 ----
echo "[4/6] 重启后端服务..."
$SSH "systemctl restart moneybag 2>/dev/null || \
    (pkill -f 'uvicorn main:app' 2>/dev/null; sleep 2; \
     cd $REMOTE_PATH/backend && nohup /opt/moneybag/venv/bin/uvicorn main:app \
     --host 0.0.0.0 --port 8000 --workers 2 \
     >> /opt/moneybag/logs/uvicorn.log 2>&1 &)"

# ---- 5. 检查 night_worker cron（MB-004）----
echo "[5/6] 检查 night_worker cron 配置（MB-004）..."
$SSH "
CRON_LINE='0 1 * * * cd $REMOTE_PATH/backend && /opt/moneybag/venv/bin/python scripts/night_worker.py >> /opt/moneybag/logs/night.log 2>&1'
EXISTING=\$(crontab -l 2>/dev/null | grep -c 'night_worker' || echo 0)
if [ \"\$EXISTING\" -eq 0 ]; then
    echo '  [MB-004] 添加 night_worker cron...'
    (crontab -l 2>/dev/null; echo \"\$CRON_LINE\") | crontab -
    echo '  ✅ cron 已添加'
else
    echo '  ✅ night_worker cron 已存在'
fi
crontab -l | grep -E 'night_worker|stock_monitor|cache_warmer' || echo '  (无相关 cron 条目)'
"

# ---- 6. 冒烟测试 ----
echo "[6/6] 冒烟测试..."
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
echo "=== 部署完成 ==="
echo "验证 timing confidence: curl -s '$BASE/api/timing?userId=default' | python3 -m json.tool | grep confidence"
echo "验证 risk-metrics GET:  curl -s '$BASE/api/risk-metrics?userId=default' | python3 -m json.tool | head -5"

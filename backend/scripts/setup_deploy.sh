#!/usr/bin/env bash
# setup_deploy.sh — 全自动配置一键部署环境（expect 自动输入密码）
# 用法：bash backend/scripts/setup_deploy.sh
# 只需运行一次，之后 deploy_to_server.sh 全自动

set -e

SERVER="${1:-150.158.47.189}"
REMOTE_USER="ubuntu"
PASSWORD="Lj860117"
PUB_KEY="$HOME/.ssh/id_ed25519.pub"
PRIV_KEY="$HOME/.ssh/id_ed25519"

echo "=== 全自动配置一键部署环境 ==="
echo "目标服务器: $SERVER"
echo ""

# ---- 1. 检查 SSH Key ----
if [ ! -f "$PUB_KEY" ]; then
    echo "[1/3] 生成 SSH Key..."
    ssh-keygen -t ed25519 -C "moneybag-deploy" -f "$HOME/.ssh/id_ed25519" -N ""
    echo "  ✅ SSH Key 已生成"
else
    echo "[1/3] SSH Key 已存在"
fi

# ---- 2. 复制公钥到服务器（expect 自动输入密码）----
echo ""
echo "[2/3] 复制 SSH 公钥到服务器（自动输入密码）..."

# 先确保服务器目录存在
expect -c "
set timeout 10
spawn ssh -o StrictHostKeyChecking=no $REMOTE_USER@$SERVER \"mkdir -p ~/.ssh && chmod 700 ~/.ssh\"
expect \"password:\"
send \"$PASSWORD\r\"
interact
" 2>/dev/null || true

# 复制公钥内容
PUB_KEY_CONTENT=$(cat "$PUB_KEY")
expect -c "
set timeout 10
spawn ssh -o StrictHostKeyChecking=no $REMOTE_USER@$SERVER \"echo '$PUB_KEY_CONTENT' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys\"
expect \"password:\"
send \"$PASSWORD\r\"
interact
" 2>/dev/null || true

# 验证 key 登录
if ssh -o BatchMode=yes -o ConnectTimeout=5 "$REMOTE_USER@$SERVER" "echo 'KEY_OK'" 2>/dev/null | grep -q "KEY_OK"; then
    echo "  ✅ SSH Key 登录已生效"
else
    echo "  ⚠️  SSH Key 登录未生效，尝试 ssh-copy-id..."
    expect -c "
set timeout 15
spawn ssh-copy-id -i $PUB_KEY $REMOTE_USER@$SERVER
expect \"password:\"
send \"$PASSWORD\r\"
interact
" 2>/dev/null || true
fi

# ---- 3. 配置 sudo 免密重启 ----
echo ""
echo "[3/3] 配置 sudo 免密重启服务..."

ssh "$REMOTE_USER@$SERVER" "
# 检查是否已有免密规则
if sudo -n systemctl restart moneybag > /dev/null 2>&1; then
    echo '  ✅ sudo 免密已配置'
    exit 0
fi

# 创建 sudoers 免密规则
echo '$REMOTE_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart moneybag, /bin/systemctl status moneybag, /bin/systemctl daemon-reload' | sudo tee /etc/sudoers.d/moneybag-deploy > /dev/null
sudo chmod 440 /etc/sudoers.d/moneybag-deploy
echo '  ✅ sudo 免密规则已创建'
" 2>&1 || {
    echo "  ⚠️  ssh key 登录失败，用密码登录配置 sudo..."
    expect -c "
set timeout 15
spawn ssh -o StrictHostKeyChecking=no $REMOTE_USER@$SERVER \"echo '$REMOTE_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart moneybag, /bin/systemctl status moneybag, /bin/systemctl daemon-reload' | sudo tee /etc/sudoers.d/moneybag-deploy > /dev/null && sudo chmod 440 /etc/sudoers.d/moneybag-deploy\"
expect \"password:\"
send \"$PASSWORD\r\"
expect \"password for $REMOTE_USER:\"
send \"$PASSWORD\r\"
interact
" 2>/dev/null || true
}

# ---- 验证 ----
echo ""
echo "验证中..."
if ssh -o BatchMode=yes -o ConnectTimeout=5 "$REMOTE_USER@$SERVER" "sudo -n systemctl status moneybag > /dev/null 2>&1 && echo 'PASS'" 2>/dev/null | grep -q "PASS"; then
    echo "  ✅ 免密验证通过"
else
    echo "  ⚠️  免密验证失败，可能需重新登录终端生效"
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "✅ 一键部署环境配置完成"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "现在可以全自动部署了："
echo "   bash backend/scripts/deploy_to_server.sh"
echo ""

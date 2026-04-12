# 钱袋子 — 腾讯云部署指南

## 方案说明

**当前架构**：Railway（美国）= 前端静态文件 + 后端 API（全部）
**新架构**：Railway = 前端静态文件 | 腾讯云（国内）= 完整后端 API

> 不需要域名备案：用 IP + 端口直接访问 API

## 预期效果

| API | Railway（当前） | 腾讯云（迁移后） |
|-----|:-----------:|:----------:|
| 选股（5000+股票） | 60-120s ❌超时 | **5-10s** ✅ |
| 选基（17000+基金） | 10-30s | **3-5s** ✅ |
| Dashboard | 10-30s | **2-4s** ✅ |
| AI 聊天 | 5-15s | **<2s** ✅ |
| 新闻/宏观 | 3-15s | **<2s** ✅ |

## 步骤

### 1. 购买腾讯云轻量应用服务器

1. 访问 https://cloud.tencent.com/product/lighthouse
2. 选择：**上海/广州 → 2核2G → Ubuntu 22.04** → ¥35/月
3. 付款（微信支付）
4. 创建完成后记下 **公网 IP**（如 `43.xxx.xxx.xxx`）

### 2. 安全组配置

在轻量服务器控制台 → 防火墙：
- 添加规则：TCP 端口 `8000`，来源 `0.0.0.0/0`

### 3. SSH 登录服务器

```bash
ssh ubuntu@43.xxx.xxx.xxx
# 使用控制台设置的密码或密钥
```

### 4. 安装环境

```bash
# 更新系统
sudo apt update && sudo apt upgrade -y

# 安装 Python 3.11 + pip
sudo apt install -y python3.11 python3.11-venv python3-pip git

# 克隆代码
cd /opt
sudo git clone https://github.com/lj860117/moneybag.git
sudo chown -R ubuntu:ubuntu /opt/moneybag

# 创建虚拟环境
cd /opt/moneybag
python3.11 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 5. 配置环境变量

```bash
cat > /opt/moneybag/.env << 'EOF'
# 可选：DeepSeek API（AI 聊天用）
LLM_API_KEY=your_key_here
LLM_API_URL=https://api.deepseek.com/v1/chat/completions
LLM_MODEL=deepseek-chat

# 数据目录
DATA_DIR=/opt/moneybag/data
EOF
```

### 6. 启动服务

```bash
cd /opt/moneybag/backend
source /opt/moneybag/venv/bin/activate

# 测试启动
uvicorn main:app --host 0.0.0.0 --port 8000

# 验证：另一个终端或浏览器访问
curl http://localhost:8000/api/health
```

### 7. 配置 systemd 守护进程

```bash
sudo cat > /etc/systemd/system/moneybag.service << 'EOF'
[Unit]
Description=Moneybag API Server
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/moneybag/backend
EnvironmentFile=/opt/moneybag/.env
ExecStart=/opt/moneybag/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable moneybag
sudo systemctl start moneybag

# 查看状态
sudo systemctl status moneybag
```

### 8. 验证 API

```bash
# 在本地电脑执行（替换为你的 IP）
curl http://43.xxx.xxx.xxx:8000/api/health
curl http://43.xxx.xxx.xxx:8000/api/macro
curl http://43.xxx.xxx.xxx:8000/api/fund-screen?top_n=5
```

### 9. 前端切换到腾讯云

在手机浏览器打开钱袋子，在控制台（或地址栏）执行：
```javascript
localStorage.setItem('moneybag_engine', 'http://43.xxx.xxx.xxx:8000');
location.reload();
```

或者直接访问 `http://43.xxx.xxx.xxx:8000`（腾讯云也能直接 serve 前端）。

### 10. 更新代码

```bash
# SSH 登录服务器
cd /opt/moneybag && git pull origin main
sudo systemctl restart moneybag
```

## 回退方案

如果腾讯云出问题，清除 engine 配置即可回退到 Railway：
```javascript
localStorage.removeItem('moneybag_engine');
location.reload();
```

## 成本

- 腾讯云轻量 2C2G：¥35/月
- Railway：保持免费（$5 credit 只用于静态文件，几乎不消耗）
- 总计：**¥35/月**

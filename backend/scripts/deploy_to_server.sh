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
    # ⚠️ v9.9.64 公网访问门禁：main.py 顶部 `from infra.http_gate import ...`，
    # 躺在 infra/ 父包下（BACKEND_DIRS 只列了 infra/ 的若干子目录，覆盖不到它）。
    # 不加进来的话以后改门禁逻辑永远上不了线 —— 正是 test_deploy_asset_coverage
    # 类级守卫要防的「本地改了、部署不带、线上永远旧」。
    "backend/infra/http_gate.py"
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

# ---- 1.8 同步仓库根的 tests/（与 backend/tests/ 同一个理由，见 1.5 段末注释）----
# ⚠️ 这里不只是"假红"，是一个**活的写入隐患**：
# 2026-09-13 实测线上 /opt/moneybag/tests/test_ai_chat_regression.py 停在 5/17，
# 第 22 行是 `os.environ.get("MB_TEST_HOST", "http://150.158.47.189:8000")` ——
# 默认值直指生产，而它的 module 级 autouse fixture 会 POST /api/stock-holdings 与
# POST /api/fund-holdings **写数据**。任何人只要在服务器上跑一次 `pytest tests/`
# （不显式带 MB_TEST_HOST）就会往生产写垃圾（历史上攒下 115 条 QA_* 条目、
# llm_usage/by_user/ 下 58 个）。本地 9/13 14:54 已把默认值改回 127.0.0.1，
# 但没同步 → 线上一直是危险版本。
# 另外 backend/tests/test_no_prod_default_in_test_host.py 会扫描本目录，
# 扫不到 ≥3 处兜底点就会因"反空转断言"转红 —— 所以不能简单删掉服务器上的这份。
#
# ⚠️⚠️ 关键区分（2026-09-13 追加，别混淆）：**同步这份 ≠ 可以在服务器上跑这份。**
# 本目录是 HTTP e2e 套件，默认打 http://127.0.0.1:8000 —— 在开发机上那是临时后端，
# 在生产机上那就是**生产服务本体**（共用 /opt/moneybag/data）。9/13 部署验收时在
# 服务器上跑了一次 `pytest tests/`，实测写进 14 个 QA_* 路径（含 llm_usage/by_user/
# 配额文件、decision_logs）并触发 LLM 网关熔断（qa_test_20260419 burst=10/10）。
# 注意：`tests/conftest.py` 顶部的 DATA_DIR 隔离只作用于**测试进程内 import 的代码**，
# 对「HTTP 打到另一个进程」这条路无效 —— 隔离在，照样污染。
# 因此已在 `tests/conftest.py` 顶部加**生产机自锁**：检测到本机是生产服务器就
# 直接拒绝收集（逃生阀 MONEYBAG_ALLOW_E2E_ON_PROD=1）。所以：
#   · 服务器上要跑测试 → 只跑 backend/tests/（已隔离、不依赖 HTTP）
#   · 根 tests/ → 在开发机上跑；同步到服务器只为「消除化石版本 + 让守卫扫得到」
TEST_DIRS=(
    "tests/"
)
for d in "${TEST_DIRS[@]}"; do
    if [ -d "$REPO_ROOT/$d" ]; then
        echo "  → rsync $d"
        eval "$RSYNC --exclude='__pycache__' --exclude='*.pyc' --exclude='.pytest_cache' \"$REPO_ROOT/$d\" \"$REMOTE_USER@$SERVER:$REMOTE_PATH/$d\""
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
sleep 5  # 等待服务启动（systemd restart 后进程还没监听端口，先给一个基础宽限）
BASE="http://$SERVER:8000"

SMOKE_FAIL=0
SMOKE_TOTAL=0

# ============================================================
# 7.0b 门禁密钥 —— 不带密钥的冒烟是「死测试」，永远 401
# ============================================================
# 2026-09-20 实测：公网门禁（backend/infra/http_gate.py，v9.9.64 起）上线后，
#   冒烟 8/8 全红 HTTP 401，而服务其实是健康的 —— 从服务器 loopback 打
#   /api/health 返回 200 且 version=9.9.65。也就是说这个冒烟**已经不可能通过**：
#   每次部署都报红，真故障会淹没在一片假红里。死测试比没有测试更糟，必须修。
#
# 门禁放行方式见 http_gate.py 文件头：loopback / ?k= / X-Moneybag-Gate 头 /
#   Basic 都行。这里用**请求头**：不改 URL，避免和端点已有的 ?userId= 打架。
#
# 密钥只存在服务器 systemd unit 的 Environment 里（本地 backend/.env 没有），
#   所以运行时 ssh 取一次；不写进脚本、不打印值（只打印长度），
#   避免密钥进日志 / 进 git / 进会话记录。
# 万一哪天门禁关了（GATE_ENABLED=false），带上这个头也无害 —— 门禁 fail-open。
SMOKE_GATE_ARGS=()
if [ "${SMOKE_SKIP_GATE:-0}" != "1" ]; then
    _gate_env=$($SSH "systemctl show moneybag -p Environment" 2>/dev/null || true)
    _gate_secret=$(printf '%s' "$_gate_env" | tr ' ' '\n' | sed -n 's/^GATE_SECRET=//p' | head -1)
    if [ -n "$_gate_secret" ]; then
        SMOKE_GATE_ARGS=(-H "X-Moneybag-Gate: ${_gate_secret}")
        echo "  🔐 已取得门禁密钥（长度 ${#_gate_secret}），冒烟请求将带 X-Moneybag-Gate 头"
    else
        echo "  ⚠️  未取到 GATE_SECRET：若门禁开启，下面所有端点都会 401（假红）"
        echo "      排查：ssh $REMOTE_USER@$SERVER 'systemctl show moneybag -p Environment'"
    fi
    unset _gate_env _gate_secret
fi

# ============================================================
# 7.0 就绪探针 —— 把「服务没起来」和「某个端点慢」两件事分开判
# ============================================================
# 为什么单独一步：restart 之后端口不是立刻可连的。以前这件事混在每个端点内部判，
# 端口没开时 curl 拿到 000，和「端点真的挂了」长得一模一样 —— 于是只能靠把预算
# 调大去掩盖，代价是真挂死的端点也要等满预算才报红。分开之后：
#
#   · 探针只回答「端口有没有人在应答」：拿到**任何** HTTP 码（含 4xx/5xx）都算
#     就绪，因为那已经证明 uvicorn 在监听、应用在服务；只有连接级失败（000）
#     才算没起来。所以探针不会因为某个端点返回 500 而误判"服务没起来"。
#   · 探针**不计入** SMOKE_FAIL：它不是质量判定，是前置条件。质量判定交给下面
#     每个端点的 check_endpoint()。
#   · deadline 内始终没起来 → 直接判部署失败退出，不再往下跑 8 个注定 000 的
#     端点、把人晾在屏幕前等几分钟。这是「不放过真失败」的一半。
#
# 探针路径默认 /api/news：它是已验证在产线可用的最便宜端点之一（不是 /api/health，
# 那个要查 LLM 预算 + 数据源健康 + 磁盘，冷启动可能比业务端点还慢，拿它当探针
# 反而会引入新的假红）。可用 SMOKE_READY_PATH 覆盖。
#
# 【--noproxy】冒烟是「本机 → 目标 IP:端口」的直连探测，不该被 http_proxy 劫持。
# 实测（2026-09-17 本机）：`curl -v` 明确打出 "Uses proxy env variable
# http_proxy == 'http://127.0.0.1:62584'"，而 no_proxy 未设置 —— 也就是每个冒烟
# 请求都先绕一层本地代理。代理会加入自己的连接/转发耗时，它自己的超时还会以
# 502/504 的形式冒出来，被误读成"端点坏了"。这正是「客户端耗时 ≫ 服务端处理耗时」
# 的一个可能来源（详见下面 MB-018 处的悬案归档）。
# 默认绕开；确实需要走代理时用 SMOKE_NOPROXY='' 恢复。
wait_for_service_ready() {
    local url="$1"
    local deadline_secs="${SMOKE_READY_TIMEOUT:-120}"
    local interval="${SMOKE_READY_INTERVAL:-2}"
    local probe_budget="${SMOKE_READY_PROBE_BUDGET:-10}"
    local deadline=$(( $(date +%s) + deadline_secs ))
    local code="000"
    local waited=0

    # 注意：${url} 必须带花括号。$url 紧跟全角逗号（多字节）时，bash 在部分
    # locale 下会把后续字节当成变量名的一部分 → set -u 下 unbound variable 直接崩
    # （这个坑本文件已经踩过一次，见 check_endpoint 里 ${code} 的注释）。
    # 本机实测复现：/tmp/_fn_ready.sh:10 url? unbound variable。
    echo "  ⏳ 等待服务就绪（${url}，最多 ${deadline_secs}s）…"
    while [ "$(date +%s)" -lt "$deadline" ]; do
        # 这里必须 `|| true` + 判空，不能写 `|| echo "000"`：
        # curl 失败时 -w 已经打印了 "000" 且返回非 0，`|| echo "000"` 会再拼一个，
        # 而 $( ) 又会吃掉 echo 末尾的换行 —— 结果 code 变成 "000000"，
        # 于是 `code != "000"` 恒成立，探针永远报「已就绪」，整个闸门空转。
        # （这个是实测踩到的，不是读代码推的：死端口上探针返回了 HTTP 000000。）
        code=$(curl -s -o /dev/null --noproxy "${SMOKE_NOPROXY:-*}" ${SMOKE_GATE_ARGS[@]+"${SMOKE_GATE_ARGS[@]}"} -w "%{http_code}" --max-time "$probe_budget" "$url" 2>/dev/null || true)
        [ -n "$code" ] || code="000"
        if [ "$code" != "000" ]; then
            echo "  ✅ 服务已就绪（HTTP ${code}，等待 ${waited}s）"
            return 0
        fi
        sleep "$interval"
        waited=$(( waited + interval ))
    done
    echo "  ❌ 服务在 ${deadline_secs}s 内始终没有应答（最后 HTTP ${code}）"
    return 1
}

if ! wait_for_service_ready "$BASE${SMOKE_READY_PATH:-/api/news}"; then
    echo ""
    echo "  ── 冒烟汇总: 服务未就绪，未执行任何端点检查 ──"
    exit 1
fi

# check_endpoint <url> <desc> [单次预算秒=30] [慢响应阈值秒=5] [最大尝试次数=3] [总墙钟上限秒=单次预算×2]
#
# 三个时间量各管一件事，别混为一谈：
#   1) budget  —— 单次 curl 的 --max-time。它必须覆盖该端点**冷启动**的真实耗时：
#                 慢但健康的端点要能在一次尝试里跑完，否则就是假红。
#   2) total   —— 本端点的总墙钟上限（含重试间隔）。到点就不再试，直接判 ❌。
#                 没有它，3 次重试会把最坏耗时乘 3 倍。
#   3) attempts—— 最多试几次，只对**瞬时**故障（连接被 reset、偶发 502）有意义。
#
# 【为什么默认预算从 10s 提到 30s】依据，不是拍脑袋：
#   2026-09-17 v9.9.47 部署实测：MB-015 全球快照冷启动 5.318s（当时预算 10s，
#   **余量不到 2 倍**，再慢一点就假红）；MB-018 晨报冷启动 46.526s（它自己显式
#   要了 120s，不走默认值）。
#   30s ≈ MB-015 冷启动实测值的 5.7 倍，覆盖了「重启后首批请求要现拉数据」的量级；
#   同时 30s 仍然是个硬上限 —— 真挂死的端点 30s 内就会被判掉，不会变成橡皮图章。
#
# 【「重试救不了冷启动慢」这个说法只对了一半】实测过的结论（见提交记录中的验证台）：
#   curl 撞到 --max-time 只是**客户端**放弃等连接，服务端那一发请求并不会被取消，
#   它算完会把结果写进缓存。所以重试把本端点的有效覆盖从 budget 扩大到约 total：
#     · 冷启动 12s / 预算 10s / 总上限 20s → 第 1 次 10s 超时，第 2 次命中缓存
#       毫秒级返回，✅（实测通过）
#     · 冷启动 15s / 预算 10s / 总上限 20s → 第 1 次 10s 超时，第 2 次只剩 7s
#       还是不够，❌（实测）—— 这种情况重试救不了，只能靠 budget 本身够大
#   所以正确顺序是**先把 budget 抬到能覆盖冷启动，再用重试兜住"差一点点"的那段**，
#   反过来指望重试去补一个太小的预算是不行的。
#
# 【只对 000 重试】拿到真实 HTTP 码（4xx/5xx）就直接判 ❌，不再重试：
#   应用已经应答了，说明它在工作；5xx 是它给的**结论**，重试不会把结论改成 200，
#   只会拖慢出结论（默认预算 30s 下，一个恒定 500 的端点会白耗 2×30=60s）。
#   而冷启动慢的表现是**超时（000）**，不是 5xx —— 要救的正是这一类。
#   若线上确实存在「重启后首个请求瞬时 5xx」，用 SMOKE_RETRY_ON_HTTP_ERROR=1 打开。
#
# 【不放过真失败】四条硬保证，缺一不可：
#   · 200 才算通过；4xx/5xx 一次就判 ❌（不会靠重试把 500 洗成绿）。
#   · 挂死的端点单次撞满 budget，最多撑到 total 上限，必定 ❌ 且退出码非 0。
#   · total 上限的存在让最坏耗时**有界**：没有它，3 次重试能把单点拖到 3×budget。
#   · 预算只是"允许慢"，不是"允许坏"：⚠️ 慢响应照旧单独标注，不冒充 ✅。
check_endpoint() {
    local url="$1"
    local desc="$2"
    local budget="${3:-30}"
    local slow_at="${4:-5}"
    local max_attempts="${5:-3}"
    local retry_interval="${SMOKE_RETRY_INTERVAL:-3}"
    local total_budget="${6:-$(( budget * 2 ))}"
    local deadline=$(( $(date +%s) + total_budget ))
    local attempt=1
    local tried=0
    local out code="000" elapsed="0"
    local remaining attempt_budget
    SMOKE_TOTAL=$((SMOKE_TOTAL + 1))

    while [ "$attempt" -le "$max_attempts" ]; do
        # 总墙钟到点就不再重试：把已经拿到的结果交出去判 ❌，
        # 而不是继续把 budget 一份一份往外花。
        remaining=$(( deadline - $(date +%s) ))
        if [ "$remaining" -le 0 ]; then
            break
        fi

        attempt_budget="$budget"
        if [ "$remaining" -lt "$attempt_budget" ]; then
            attempt_budget="$remaining"
        fi

        out=$(curl -s -o /dev/null --noproxy "${SMOKE_NOPROXY:-*}" ${SMOKE_GATE_ARGS[@]+"${SMOKE_GATE_ARGS[@]}"} -w "%{http_code} %{time_total}" --max-time "$attempt_budget" "$url" 2>/dev/null || true)
        [ -n "$out" ] || out="000 0"
        out="${out%%$'\n'*}"   # 超时(curl rc!=0)时 curl 已打印 "000 <elapsed>"，避免再拼一行导致 elapsed 被读成 0
        code="${out%% *}"
        elapsed="${out##* }"
        tried="$attempt"

        if [ "$code" = "200" ]; then
            # 200 不等于体验可接受：冷启动慢的端点单独标注，别让它冒充绿
            if awk "BEGIN{exit !($elapsed > $slow_at)}" 2>/dev/null; then
                echo "  ⚠️  $desc — HTTP 200 但耗时 ${elapsed}s（冷启动慢，单次预算 ${budget}s，第 ${attempt}/${max_attempts} 次尝试）"
            else
                echo "  ✅ $desc ($url)"
            fi
            return 0
        fi

        # 拿到真实 HTTP 码就不再重试（理由见函数头「只对 000 重试」）
        if [ "$code" != "000" ] && [ "${SMOKE_RETRY_ON_HTTP_ERROR:-0}" != "1" ]; then
            break
        fi

        if [ "$attempt" -lt "$max_attempts" ]; then
            # 重试期间必须有输出：否则运维对着黑屏干等，以为脚本卡死
            echo "  🔄 $desc 第 ${attempt}/${max_attempts} 次尝试 HTTP ${code}（实测 ${elapsed}s），${retry_interval}s 后重试…"
            sleep "$retry_interval"
        fi
        attempt=$((attempt + 1))
    done

    SMOKE_FAIL=$((SMOKE_FAIL + 1))
    # 注意：${code} 必须带花括号。紧跟全角字符时会话 locale 非 UTF-8 时
    # bash 会把多字节字节当成变量名的一部分（unbound variable），set -e 下直接崩。
    echo "  ❌ $desc — HTTP ${code}（单次预算 ${budget}s / 总上限 ${total_budget}s / 实测 ${elapsed}s / 已试 ${tried} 次）($url)"
}

check_endpoint "$BASE/api/timing?userId=default"        "MB-007 置信度"
check_endpoint "$BASE/api/stock-screen?userId=default"  "MB-010/011 推荐列表"
check_endpoint "$BASE/api/risk-metrics?userId=default"  "MB-017/016 风险指标 GET"
check_endpoint "$BASE/api/news"                         "MB-012 新闻列表"
check_endpoint "$BASE/api/news/deep-impact"             "MB-008 深度新闻分析"
check_endpoint "$BASE/api/global/snapshot"              "MB-015 全球快照"
# MB-018 用真实用户 LeiJiang，不要用 default：
#   default 是冒烟专用的空用户，没有任何离线任务给它预生成晨报缓存，打它必然走
#   「现算」路径（fast 管线 + 持仓/估值/地缘等现拉），并把结果写成
#   data/briefings/default_YYYYMMDD.json —— 每次部署往生产 data 目录扔一个垃圾文件。
#   LeiJiang 是真实用户，晨报缓存在真实用户命名空间下是合法数据（chat 上下文也会读它）。
#
# ⚠️ 但换成 LeiJiang 也不保证命中缓存，还剩一道坎：
#   steward.briefing() 的 CACHE_TTL_HOURS=4，凌晨预生成的缓存 11:50 后就失效被删除
#   （这是有意的数据新鲜度取舍，不要为了提速去动它）。
#   —— 另一道坎「文件名大小写分裂」（cron 写小写 leijiang_*.json、API 查大写
#      LeiJiang_*.json，导致预生成的缓存从未被命中）已于 2026-09-17 由
#      services/steward.py 的 brief_cache_key() 归一化修掉。
# 结论：11:50 之后部署，MB-018 仍会走「现算」路径；120s 预算 + 3 次重试只是兜底。
# 实测参考（2026-09-17）：重启后首次现算 59s，服务热起来后 3s —— 不调 LLM（fast 管线 llm_max=0）。
#
# 【悬案归档：那个「MB-018 耗时 413s」是从哪来的】
# 曾经出现过「curl 侧报 413s、journalctl 侧服务端只花 59s」的对不上账，一直没复现。
# 查了各版本脚本后可以排除一个方向：**413s 不可能来自本函数的 %{time_total}**。
#   · 96bbff8 起 MB-018 就是 --max-time 120，curl 单次操作不可能超出自己的
#     --max-time，time_total 恒 ≤ 120；
#   · 96bbff8 之前本函数只打印 %{http_code}，压根不测量耗时。
# 所以 413s 是别处量出来的（多半是"整步/整次部署的墙钟"，或是一次手工 curl 没带
# --max-time），和"服务端处理 59s"并不矛盾 —— 两者量的不是同一个东西。
# 顺带记一笔真正会导致「客户端耗时 ≫ 服务端处理耗时」的机制，改这个文件时别忘：
#   · 服务端日志量的是**处理**耗时，curl 量的是**排队 + 处理**；冒烟撞上 cron
#     （night_worker / cache_warmer / hallucination_check）时，请求会在 uvicorn
#     里排队，客户端看到的时间可以远大于服务端。
#   · 重试会累加：本函数最坏耗时 = budget×attempts + interval×(attempts-1)。
#     这也是上面要给 total_budget 加总墙钟上限的直接原因（没有它，MB-018
#     最坏能拖到 120×3+3×2 = 366s，和当年那个 413s 已经是同一量级了）。
check_endpoint "$BASE/api/steward/briefing?userId=LeiJiang"        "MB-018 晨报缓存" 120 15
check_endpoint "$BASE/api/steward/briefing-history?userId=default" "MB-005 往期晨报"

# 验证新闻条数
# 这行不参与 SMOKE_FAIL 判定，但要给 --max-time：否则端点挂死时整次部署会卡在
# 这里（curl 没有默认超时），而屏幕上的表现和"部署卡住"一模一样。
NEWS_COUNT=$(curl -s --noproxy "${SMOKE_NOPROXY:-*}" ${SMOKE_GATE_ARGS[@]+"${SMOKE_GATE_ARGS[@]}"} --max-time 30 "$BASE/api/news?limit=20" | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d.get('news',[])))" 2>/dev/null || echo "?")
echo "  📰 新闻条数: $NEWS_COUNT (期望 ≥15)"

# 措辞避免 $SMOKE_FAIL/$SMOKE_TOTAL 这种分数式写法：「1/8 项失败」容易被读成
# 「八分之一项失败」，实际含义是「共 8 项，其中 1 项失败」。
if [ "$SMOKE_FAIL" -eq 0 ]; then
    echo "  ── 冒烟汇总: 共 $SMOKE_TOTAL 项，全部通过 ──"
else
    echo "  ── 冒烟汇总: 共 $SMOKE_TOTAL 项，失败 $SMOKE_FAIL 项 ──"
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

# 提示：手动复现时公网要带门禁，否则 401。密钥不在本地 .env，用这条取：
#   ssh $REMOTE_USER@$SERVER 'systemctl show moneybag -p Environment' | tr ' ' '\n' | grep GATE_SECRET
echo "验证 timing confidence: curl -s '$BASE/api/timing?userId=default' | python3 -m json.tool | grep confidence"
echo "验证 risk-metrics GET:  curl -s '$BASE/api/risk-metrics?userId=default' | python3 -m json.tool | head -5"

# 冒烟未通过时以非零码退出：文件同步成功≠服务健康。
# 否则 200/❌ 都只打印在屏幕上，CI 和调用方（bump_and_deploy.sh）无从判断，
# 就是「闸门空转仍显绿」。
if [ "$SMOKE_FAIL" -ne 0 ]; then
    echo ""
    echo "=== 部署收尾：文件已同步、服务已重启，但冒烟测试 $SMOKE_FAIL 项失败（共 $SMOKE_TOTAL 项）==="
    exit 1
fi

echo "=== 部署完成 ==="

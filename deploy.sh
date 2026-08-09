#!/bin/bash
# 钱袋子统一部署脚本
# 用法: ./deploy.sh [文件1 文件2 ...]
# 不带参数: 部署所有改动文件
# 
# 功能:
#   1. 自动从 config.py 读取版本号
#   2. 更新 index.html 和 sw.js 里的所有版本号
#   3. rsync 到腾讯云
#   4. 自动移动文件到正确目录
#   5. 重启服务

set -e

REMOTE="ubuntu@150.158.47.189"
REMOTE_DIR="/opt/moneybag"
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

# 1. 读版本号
VERSION=$(grep 'APP_VERSION' "$LOCAL_DIR/backend/config.py" | grep -oE '9\.[0-9]+\.[0-9]+')
echo "🚀 部署版本: v$VERSION"

# 1.5 ★ 预检查：所有 JS 和 Python 文件语法（v9.5.89 加，防止 await/async 等运行时错误）
echo "🔍 预检查代码语法..."
NODE_BIN="${NODE_BIN:-/Users/leijiang/.workbuddy/binaries/node/versions/22.12.0/bin/node}"
if [ ! -x "$NODE_BIN" ]; then NODE_BIN=$(which node 2>/dev/null); fi
PY_BIN="${PY_BIN:-/usr/bin/python3}"
SYNTAX_FAIL=0

# 检查待部署文件（如果指定了），否则全量检查 pages/ 和 backend/
if [ $# -gt 0 ]; then
    CHECK_FILES=("$@")
else
    CHECK_FILES=($(ls "$LOCAL_DIR/pages"/*.js 2>/dev/null) $(find "$LOCAL_DIR/backend" -name "*.py" -not -path "*/__pycache__/*" 2>/dev/null))
fi

for f in "${CHECK_FILES[@]}"; do
    abs="$LOCAL_DIR/$f"
    [ -f "$f" ] && abs="$f"
    [ ! -f "$abs" ] && continue
    case "$abs" in
        *.js)
            if [ -n "$NODE_BIN" ]; then
                if ! "$NODE_BIN" --check "$abs" 2>/tmp/_syntax_err; then
                    echo "❌ JS 语法错误: $f"
                    cat /tmp/_syntax_err | head -5
                    SYNTAX_FAIL=1
                fi
            fi
            ;;
        *.py)
            if ! "$PY_BIN" -c "import ast; ast.parse(open('$abs').read())" 2>/tmp/_syntax_err; then
                echo "❌ Python 语法错误: $f"
                cat /tmp/_syntax_err | head -5
                SYNTAX_FAIL=1
            fi
            ;;
    esac
done

if [ $SYNTAX_FAIL -ne 0 ]; then
    echo ""
    echo "🛑 预检查失败，中止部署。请修复以上语法错误后重试。"
    exit 1
fi
echo "✅ 语法预检查通过"

# 2. 统一更新 index.html 和 sw.js 里的所有版本号（正则匹配所有旧版本）
sed -i '' "s/v=9\.[0-9]*\.[0-9]*/v=$VERSION/g" "$LOCAL_DIR/index.html"
sed -i '' "s/moneybag-v[0-9]*-cache/moneybag-v$(echo $VERSION | tr -d '.')-cache/g" "$LOCAL_DIR/sw.js"
# v9.5.89: 同步前端 LOCAL_VER 心跳检测变量
sed -i '' "s/const LOCAL_VER = '[0-9.]*'/const LOCAL_VER = '$VERSION'/g" "$LOCAL_DIR/index.html"
echo "✅ index.html + sw.js 版本号已同步到 v$VERSION"

# 3. 确定要上传的文件
if [ $# -gt 0 ]; then
    FILES=("$@" "index.html" "sw.js" "backend/config.py")
else
    # 上传所有近期改动的文件（1小时内）
    FILES=($(find "$LOCAL_DIR" \( -name "*.py" -o -name "*.js" -o -name "*.html" -o -name "*.css" \) \
        -newer "$LOCAL_DIR/backend/config.py" \
        -not -path "*/node_modules/*" -not -path "*/.git/*" \
        2>/dev/null | sed "s|$LOCAL_DIR/||"))
    FILES+=("index.html" "sw.js" "backend/config.py")
fi

# 去重（兼容 macOS bash 3，不用 declare -A）
UNIQUE_FILES=()
for f in "${FILES[@]}"; do
    found=0
    for u in "${UNIQUE_FILES[@]}"; do
        [ "$u" = "$f" ] && found=1 && break
    done
    if [ $found -eq 0 ] && [ -f "$LOCAL_DIR/$f" ]; then
        UNIQUE_FILES+=("$f")
    fi
done

echo "📤 上传 ${#UNIQUE_FILES[@]} 个文件..."

# 4. rsync 上传（使用 -R 保持相对路径目录结构，避免同名文件互相覆盖）
cd "$LOCAL_DIR"
rsync -avzR -e ssh "${UNIQUE_FILES[@]}" "$REMOTE:$REMOTE_DIR/" 2>&1

# 5. 服务器端：用 -R 后文件已在正确位置，只需重启
echo "🔧 服务器端重启服务..."
ssh "$REMOTE" bash << 'ENDSSH'
cd /opt/moneybag

# v9.5.118: 使用 rsync -R 后文件已保持目录结构，无需手动分发
# 只处理可能残留在根目录的历史文件（兼容旧部署）
for f in /opt/moneybag/*.js; do
    [ ! -f "$f" ] && continue
    basename=$(basename "$f")
    if [ "$basename" = "sw.js" ] || [ "$basename" = "app.js" ]; then
        continue  # 顶层文件不动
    fi
    if [ -f "/opt/moneybag/pages/$basename" ]; then
        mv "$f" "/opt/moneybag/pages/$basename" 2>/dev/null && echo "  pages/$basename"
    fi
done
for f in /opt/moneybag/*.py; do
    [ ! -f "$f" ] && continue
    basename=$(basename "$f")
    orig=$(find /opt/moneybag/backend -name "$basename" -not -path "*/services/*" 2>/dev/null | head -1)
    if [ -n "$orig" ]; then
        mv "$f" "$orig" 2>/dev/null && echo "  $orig"
    fi
done

sudo systemctl restart moneybag
sleep 5
echo "Service: $(sudo systemctl is-active moneybag)"
echo "Version: $(grep APP_VERSION /opt/moneybag/backend/config.py)"
ENDSSH

echo "✅ v$VERSION 部署完成"

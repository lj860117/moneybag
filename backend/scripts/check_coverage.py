#!/usr/bin/env python3
"""
钱袋子 — 前后端 API 覆盖率检查
Phase 0 任务 1.2 | 铁律 #18：后端 API 做了必须验证前端调了

用法:
  cd /opt/moneybag/backend && python scripts/check_coverage.py

功能:
  1. 提取 main.py 中所有 @app.xxx("/api/...") 路由
  2. 提取 app.js 中所有 /api/... 调用
  3. 找出"后端有、前端没调"的遗漏 API
  4. 输出覆盖率报告
"""
import re
import sys
from pathlib import Path

# ---- 路径 ----
BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
MAIN_PY = BACKEND_DIR / "main.py"
APP_JS = PROJECT_ROOT / "app.js"


def extract_backend_routes(filepath: Path) -> list:
    """提取 main.py 中所有 API 路由"""
    text = filepath.read_text(encoding="utf-8")
    # 匹配 @app.get("/api/..."), @app.post("/api/...") 等
    pattern = r'@app\.(get|post|put|delete)\(\s*["\']([^"\']+)["\']'
    routes = []
    for method, path in re.findall(pattern, text):
        if path.startswith("/api/"):
            # 将路径参数统一化：/api/nav/{code} → /api/nav/{param}
            normalized = re.sub(r'\{[^}]+\}', '{param}', path)
            routes.append({
                "method": method.upper(),
                "path": path,
                "normalized": normalized,
            })
    return routes


def extract_frontend_calls(filepath: Path) -> set:
    """提取 app.js 中所有 API 调用路径"""
    text = filepath.read_text(encoding="utf-8")
    # 匹配 API_BASE + '/xxx' 或 `${API_BASE}/xxx`
    paths = set()

    # 模式1: API_BASE + '/path'
    for m in re.finditer(r"API_BASE\s*\+\s*['\"`]([^'\"`]+)", text):
        p = m.group(1)
        if p.startswith("/"):
            # 去掉查询参数
            clean = re.split(r'[?&]', p)[0].rstrip("/")
            if clean:
                paths.add(clean)

    # 模式2: `${API_BASE}/path`
    for m in re.finditer(r'\$\{API_BASE\}(/[a-zA-Z0-9/_-]+)', text):
        p = m.group(1).rstrip("/")
        if p:
            paths.add(p)

    # 模式3: fetch('/api/path')
    for m in re.finditer(r"fetch\(['\"]([^'\"]+api/[^'\"]+)", text):
        p = m.group(1)
        if "/api/" in p:
            p = "/api/" + p.split("/api/")[1]
            clean = re.split(r'[?&]', p)[0].rstrip("/")
            paths.add(clean)

    return paths


def normalize_path(path: str) -> str:
    """统一路径格式，去掉路径参数"""
    path = re.sub(r'\{[^}]+\}', '{param}', path)
    return path.rstrip("/")


def main():
    if not MAIN_PY.exists():
        print(f"❌ 找不到 {MAIN_PY}")
        return False
    if not APP_JS.exists():
        print(f"❌ 找不到 {APP_JS}")
        return False

    # 提取
    backend_routes = extract_backend_routes(MAIN_PY)
    frontend_calls = extract_frontend_calls(APP_JS)

    # 归一化前端路径用于匹配
    frontend_normalized = set()
    for p in frontend_calls:
        frontend_normalized.add(normalize_path(p))

    # 分类
    covered = []    # 前端调了的
    uncovered = []  # 前端没调的
    excluded = []   # 排除项（内部 API、静态文件等）

    EXCLUDE_PATTERNS = [
        "/api/health",           # 内部健康检查
        "/api/health/data-audit", # 内部审计
        "/api/models",           # 内部
        "/api/admin",            # 管理
        "/{filename:path}",      # 静态文件
    ]

    for route in backend_routes:
        path = route["path"]
        norm = normalize_path(path)

        # 排除内部 API
        if any(path.startswith(ex) or path == ex for ex in EXCLUDE_PATTERNS):
            excluded.append(route)
            continue

        # 检查前端是否调用（模糊匹配路径前缀）
        found = False
        # 去掉 /api 前缀来匹配前端（前端用 API_BASE + '/xxx'，不含 /api）
        backend_suffix = norm.replace("/api", "", 1)
        for fe_path in frontend_normalized:
            if fe_path == backend_suffix or backend_suffix.startswith(fe_path) or fe_path.startswith(backend_suffix):
                found = True
                break

        if found:
            covered.append(route)
        else:
            uncovered.append(route)

    # 输出报告
    total = len(covered) + len(uncovered)
    rate = len(covered) / total * 100 if total > 0 else 0

    print(f"📊 前后端 API 覆盖率检查")
    print(f"{'='*50}")
    print(f"  后端路由总数: {len(backend_routes)}")
    print(f"  排除（内部）: {len(excluded)}")
    print(f"  需要前端覆盖: {total}")
    print(f"  ✅ 已覆盖: {len(covered)} ({rate:.0f}%)")
    print(f"  ❌ 未覆盖: {len(uncovered)}")

    if uncovered:
        print(f"\n🔴 以下后端 API 前端未调用（铁律 #18）:\n")
        for r in sorted(uncovered, key=lambda x: x["path"]):
            print(f"  {r['method']:6s} {r['path']}")

    print(f"\n{'='*50}")
    return len(uncovered) == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)

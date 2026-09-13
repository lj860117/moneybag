"""防复发守卫：测试文件的 ``MB_TEST_HOST`` 默认值兜底不得指向生产。

## 事故背景

``tests/test_ai_chat_regression.py`` 旧写法把默认值直接写成生产地址：

    BASE = os.environ.get("MB_TEST_HOST", "http://150.158.47.189:8000")

而这个文件的 module 级 autouse fixture 会 ``POST /api/stock-holdings`` 与
``POST /api/fund-holdings`` **写数据**。于是任何人直接跑 ``pytest tests/``
（不显式带 ``MB_TEST_HOST``）都会打到生产并写数据 —— 服务器上因此攒下 115 个
``QA_*`` 垃圾条目（含 ``llm_usage/by_user/`` 下 58 个，污染用量统计）。

根因不是某个文件写错，而是**「默认指向生产」这个形态本身**：跑一次测试 = 往生产
写一次数据。所以这里上锁：扫描仓库里所有测试文件的 ``MB_TEST_HOST`` 默认值兜底，
断言没有一个是非 localhost 的。

## 三条断言（缺一不可）

1. **正向扫描（本守护的核心）**：遍历 ``tests/`` 与 ``backend/tests/`` 下所有
   ``.py``，凡是 ``os.environ.get("MB_TEST_HOST", "<默认>")`` / ``os.getenv(...)``
   这类**默认值兜底**形态，其默认 host 必须是 localhost / 127.0.0.1 / ::1。
   打生产必须由操作者显式设环境变量，不能靠代码里的默认值。

2. **反空转断言（必做）**：先断言确实扫到了预期数量的候选点（≥3 处），否则正则
   写错、扫出 0 个时，断言会「空转仍显绿」。本项目刚吃过「闸门空转仍显绿」的教训。

3. **正则自检**：对扫描用的正则喂合成正样本，断言它能匹配 —— 防第 1 条的正则
   哪天被改坏成永远匹配不到。

全部离线：只读文件 + 正则，不发网络请求、不写盘。
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

# 从 backend/tests/xxx.py 上溯两级到仓库根，不用绝对路径（CI runner 路径不同）。
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCAN_DIRS = (_REPO_ROOT / "tests", _REPO_ROOT / "backend" / "tests")

# 允许的本地 host。
_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1"}

# 只匹配「默认值兜底」形态：第二个参数是默认值。
# 覆盖 os.environ.get(...) 与 os.getenv(...) 两种写法，单/双引号均可。
_FALLBACK_PATTERNS = (
    re.compile(
        r"""os\.environ\.get\(\s*["']MB_TEST_HOST["']\s*,\s*["']([^"']+)["']""",
    ),
    re.compile(
        r"""os\.getenv\(\s*["']MB_TEST_HOST["']\s*,\s*["']([^"']+)["']""",
    ),
)

# 反空转阈值：当前仓库实有 4 处兜底点（root tests/ 下 4 个文件）。
# 定 3 是留一点余量，但足以在「正则写错扫出 0 个」时立刻变红。
_MIN_EXPECTED_CANDIDATES = 3


def _iter_test_py_files():
    for base in _SCAN_DIRS:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            # 跳过本守卫文件自身，避免正则字面量自匹配。
            if path.resolve() == Path(__file__).resolve():
                continue
            yield path


def _collect_fallbacks():
    """返回 [(path, lineno, default_raw), ...]。"""
    found = []
    for path in _iter_test_py_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for pattern in _FALLBACK_PATTERNS:
            for m in pattern.finditer(text):
                lineno = text.count("\n", 0, m.start()) + 1
                found.append((path, lineno, m.group(1)))
    return found


def test_regex_matches_synthetic_prod_sample():
    """正则自检：合成的旧代码必须能被匹配到，否则下面的扫描会空转。"""
    sample = 'BASE = os.environ.get("MB_TEST_HOST", "http://150.158.47.189:8000")'
    matched = [p.search(sample) for p in _FALLBACK_PATTERNS]
    assert any(matched), "扫描正则匹配不到合成正样本 —— 正则会空转，必须先修正则"
    for p, m in zip(_FALLBACK_PATTERNS, matched):
        if m:
            assert m.group(1) == "http://150.158.47.189:8000"


def test_nonempty_scan_guard():
    """反空转：必须真的扫到预期数量的兜底点，否则断言是死的。"""
    found = _collect_fallbacks()
    assert len(found) >= _MIN_EXPECTED_CANDIDATES, (
        f"只扫到 {len(found)} 处 MB_TEST_HOST 默认值兜底点，少于预期"
        f"（≥{_MIN_EXPECTED_CANDIDATES}）。可能是正则失效或扫描目录写错，"
        f"这道闸门正在空转 —— 必须修正扫描逻辑而不是放宽阈值。\n"
        f"实际扫到：{[(str(p), n, d) for p, n, d in found]}"
    )


def test_no_prod_default_in_test_host():
    """核心断言：任何 MB_TEST_HOST 默认值兜底都不得指向非本地地址。"""
    offenders = []
    for path, lineno, default in _collect_fallbacks():
        host = urlparse(default).hostname or default
        if host not in _ALLOWED_HOSTS:
            offenders.append((path, lineno, default))

    assert not offenders, (
        "以下测试文件把 MB_TEST_HOST 的默认值指向了非本地地址 —— "
        "跑测试会误打真实服务（test_ai_chat_regression.py 还会写数据）：\n"
        + "\n".join(
            f"  {p.relative_to(_REPO_ROOT)}:{n}  ->  {d}" for p, n, d in offenders
        )
        + "\n修法：默认值改成本地（如 http://127.0.0.1:8000），"
        "打生产必须由操作者显式设 MB_TEST_HOST。"
    )

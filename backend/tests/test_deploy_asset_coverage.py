"""部署覆盖度守卫 —— 防「本地改了、部署脚本不带它、线上永远旧」的静默漂移。

## 为什么需要这个测试

### 第一例：前端 sw.js

2026-09-13 v9.9.26 上线后对账实测：线上 ``/opt/moneybag/sw.js`` 的 ``CACHE_NAME``
冻在 ``moneybag-v9923-cache``，而同一时刻 ``index.html`` 已是 ``?v=9.9.26``、
``app.js`` 也已同步。根因不是漏跑部署，而是 **``deploy_to_server.sh`` 的
``FRONTEND_FILES`` 里根本没有 ``sw.js``** —— 它每轮 bump 都改、都提交、都 push，
就是不上线。

同批还查出 ``manifest.json`` / ``styles/`` / ``icons/`` 同样不在任何同步清单里，
只是它们自 5 月起没改过，所以还没露馅。

### 第二例：后端 prompts/

同一轮的三方对账（本地 / GitHub / 服务器逐文件内容哈希）又查出
``backend/prompts/`` 也不在 ``BACKEND_DIRS`` 里：线上 ``close_review.md`` 停在 8/30
旧版、``holding_diagnose.md`` 整个缺失、并滞留三个本地已删除的死 prompt。
后果尤其严重 —— ``api/shared_helpers.py._load_named_prompt()`` 在**运行时**按文件名
读这些 md，缺失时 fail-open 走内置兜底。于是本轮「给 close_review 补防编造约束」
变成了**代码上线了、prompt 没上线**，改动等于白做。

### 共同根因

**同步清单是手工维护的，加文件/加目录就漏。** 这不是个案，是需要机械化守卫的
结构性缺陷。

## 判据

1. 「被 ``index.html`` / ``sw.js`` / ``manifest.json`` 引用到的本地静态资产」
   必须全部落在同步范围内（``FRONTEND_FILES`` 直接同步，或父目录被 rsync）。
2. 「代码在运行时从磁盘读取的目录」（当前为 ``backend/prompts/``）必须被同步。

## 反「闸门空转」设计（本仓血教训）

本套件最危险的失效模式不是"漏判"，而是**解析器返回空集导致断言真空通过**：
一旦 ``deploy_to_server.sh`` 的数组写法变了、正则失配，``covered`` 变成 ``set()``，
若断言写成「泄露的资产为空」就会永远绿。因此：

* ``test_parser_really_parses_*`` 显式断言解析器必须解析出已知条目（非空 + 含 ``index.html``）
* ``test_prompt_loader_reads_from_a_synced_directory`` 从 loader **源码**推导目录，
  不硬编码路径，避免代码漂移后守卫失效
* ``test_checker_detects_injected_*`` 用**故障注入**证明判据真的会红

（已实测：摘 ``sw.js`` → 红；破坏解析器 → 5 failed；摘 ``backend/prompts/`` → 3 failed。）
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
DEPLOY_SCRIPT = BACKEND / "scripts" / "deploy_to_server.sh"
INDEX_HTML = REPO_ROOT / "index.html"
SW_JS = REPO_ROOT / "sw.js"
MANIFEST = REPO_ROOT / "manifest.json"

# 这些资产一旦不同步，现象是「前端改了但线上行为不变」，属于必须上线的硬要求。
# 写死一份最小集合，作为「解析器/判据把整个闸门跑空」时的兜底断言。
MUST_BE_DEPLOYED = {"index.html", "app.js", "styles.css", "sw.js", "manifest.json"}


# --------------------------------------------------------------------------
# 解析层
# --------------------------------------------------------------------------
def _parse_shell_array(script_text: str, var_name: str) -> list[str]:
    """从 shell 脚本里解析 ``VAR=( "a" "b" )`` 形式的数组。"""
    m = re.search(
        rf"^{var_name}=\((.*?)^\)",
        script_text,
        re.MULTILINE | re.DOTALL,
    )
    if not m:
        return []
    body = m.group(1)
    # 去掉行内注释后再取引号内的条目
    body = "\n".join(line.split("#")[0] for line in body.splitlines())
    return [q.strip() for q in re.findall(r'"([^"]+)"', body)]


def _deploy_script_text() -> str:
    return DEPLOY_SCRIPT.read_text(encoding="utf-8")


def _frontend_files() -> set[str]:
    return set(_parse_shell_array(_deploy_script_text(), "FRONTEND_FILES"))


def _frontend_dirs() -> set[str]:
    return set(_parse_shell_array(_deploy_script_text(), "FRONTEND_DIRS"))


def _synced_dirs() -> set[str]:
    """所有被 rsync 同步的目录前缀（含写死同步的 pages/）。"""
    text = _deploy_script_text()
    dirs = set(_frontend_dirs())
    dirs |= set(_parse_shell_array(text, "BACKEND_DIRS"))
    dirs.add("pages/")  # deploy_to_server.sh 里单独 rsync 的一段
    return {d if d.endswith("/") else d + "/" for d in dirs}


# --------------------------------------------------------------------------
# 被引用资产枚举（用于建立「必须覆盖」的集合）
# --------------------------------------------------------------------------
def _referenced_assets() -> set[str]:
    """index.html / sw.js / manifest.json 引用到的、仓内真实存在的静态资产。"""
    refs: set[str] = set()

    html = INDEX_HTML.read_text(encoding="utf-8")
    for m in re.finditer(r'(?:src|href)="([^"]+)"', html):
        url = m.group(1)
        if url.startswith(("http://", "https://", "data:", "#", "//")):
            continue
        refs.add(url.split("?")[0].lstrip("/"))

    sw = SW_JS.read_text(encoding="utf-8")
    for m in re.finditer(r"'(/[^']*)'", sw):
        path = m.group(1).lstrip("/")
        refs.add(path or "index.html")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for icon in manifest.get("icons", []):
        refs.add(icon["src"].split("?")[0].lstrip("/"))

    # Service Worker 与 manifest 自身也必须上线
    refs.add("sw.js")
    refs.add("manifest.json")

    return {r for r in refs if (REPO_ROOT / r).is_file()}


# --------------------------------------------------------------------------
# 判据（纯函数，便于故障注入直接调用）
# --------------------------------------------------------------------------
def find_coverage_gaps(
    referenced: set[str],
    frontend_files: set[str],
    synced_dirs: set[str],
) -> list[str]:
    """返回「被引用但不会被部署」的资产清单。空列表 = 覆盖完整。"""
    gaps: list[str] = []
    for asset in sorted(referenced):
        if asset in frontend_files:
            continue
        if any(asset.startswith(d) for d in sorted(synced_dirs)):
            continue
        gaps.append(asset)
    return gaps


# --------------------------------------------------------------------------
# 1) 反空转：解析器必须真的解析出东西
# --------------------------------------------------------------------------
def test_deploy_script_exists():
    assert DEPLOY_SCRIPT.is_file(), f"部署脚本不存在：{DEPLOY_SCRIPT}"


def test_parser_really_parses_frontend_files():
    """解析器必须解析出非空清单 —— 否则后面的覆盖断言会真空通过。"""
    files = _frontend_files()
    assert files, (
        "FRONTEND_FILES 解析结果为空。要么 deploy_to_server.sh 里的数组写法变了、"
        "正则失配，要么清单真的被清空了。无论哪种，本文件的覆盖断言都会变成"
        "「闸门空转仍然显绿」，必须先修解析器。"
    )
    assert "index.html" in files, f"FRONTEND_FILES 应含 index.html，实际 {sorted(files)}"


def test_parser_really_parses_frontend_dirs():
    """FRONTEND_DIRS 同样必须有内容，防止目录同步静默失效。"""
    dirs = _frontend_dirs()
    assert dirs, (
        "FRONTEND_DIRS 解析结果为空 —— styles/ icons/ 会重新变成不同步状态。"
    )


def test_referenced_assets_enumeration_is_not_empty():
    """被引用资产的枚举结果不能为空，否则覆盖断言无对象可比。"""
    refs = _referenced_assets()
    assert refs, "未能从 index.html / sw.js / manifest.json 枚举出任何本地资产，解析逻辑已失效。"
    assert "app.js" in refs and "index.html" in refs, (
        f"枚举结果缺少已知资产，说明解析逻辑退化了：{sorted(refs)}"
    )


# --------------------------------------------------------------------------
# 2) 主判据：部署清单必须覆盖全部被引用资产
# --------------------------------------------------------------------------
def test_frontend_files_covers_every_referenced_static_asset():
    gaps = find_coverage_gaps(_referenced_assets(), _frontend_files(), _synced_dirs())
    assert not gaps, (
        "以下静态资产被 index.html / sw.js / manifest.json 引用，但不在部署脚本的"
        "同步范围内 —— 本地改了也不会到线上：\n"
        + "\n".join(f"  - {g}" for g in gaps)
        + "\n修法：把根文件加进 FRONTEND_FILES，或把其父目录加进 FRONTEND_DIRS。"
    )


def test_must_be_deployed_assets_are_covered():
    """硬要求集合兜底断言（独立于解析结果）。"""
    gaps = find_coverage_gaps(MUST_BE_DEPLOYED, _frontend_files(), _synced_dirs())
    assert not gaps, (
        "以下核心资产必须被部署脚本覆盖，当前未覆盖：\n"
        + "\n".join(f"  - {g}" for g in gaps)
    )


def test_service_worker_is_actually_deployed():
    """点名断言：sw.js 必须上线。

    这是 v9.9.26 实际踩中的那个坑。CACHE_NAME 每轮 bump 都变，若 sw.js 不上线，
    线上版本标记会与真实产物长期不一致，且 sw.js 自身的任何改动永远无法生效。
    """
    covered = _frontend_files() | {
        a for a in ["sw.js"] if any("sw.js".startswith(d) for d in _synced_dirs())
    }
    assert "sw.js" in covered, (
        "sw.js 不在部署同步范围内。它的 CACHE_NAME 随版本 bump，必须上线；"
        "否则会出现「index.html 已是新版本、sw.js 还停在旧版本」的台账失真。"
    )


# --------------------------------------------------------------------------
# 3) 故障注入：证明判据真的会红（防死测试）
# --------------------------------------------------------------------------
def test_checker_detects_injected_gap():
    """把 sw.js 从清单里摘掉，检查器必须报出这个泄漏。

    没有这个测试，上面几条断言可能只是「恰好没触发」的绿，而不是「真的在把关」。
    """
    referenced = _referenced_assets()
    full = _frontend_files()
    dirs = _synced_dirs()

    # 注入前：不应该有泄漏
    assert not find_coverage_gaps(referenced, full, dirs), (
        "注入前就存在覆盖缺口，说明清单本身没修干净，先修清单再看本测试"
    )

    # 注入：摘掉 sw.js
    injected = {f for f in full if f != "sw.js"}
    gaps = find_coverage_gaps(referenced, injected, dirs)
    assert "sw.js" in gaps, (
        "故障注入失败：摘掉 sw.js 后检查器仍未报出泄漏，判据是死的。"
        f"实际报出：{gaps}"
    )


def test_checker_detects_injected_missing_root_file():
    """再注入一个：摘掉 manifest.json，必须同样被抓到。"""
    referenced = _referenced_assets()
    injected = {f for f in _frontend_files() if f != "manifest.json"}
    gaps = find_coverage_gaps(referenced, injected, _synced_dirs())
    assert "manifest.json" in gaps, (
        f"摘掉 manifest.json 后检查器未报出泄漏，判据不完整。实际报出：{gaps}"
    )


def test_checker_does_not_flag_assets_covered_by_directory_sync():
    """反向注入：父目录已 rsync 的资产不得被误报（防「宁可错杀」的假阳性）。"""
    referenced = _referenced_assets()
    dirs = _synced_dirs()
    # pages/ 目录由 rsync 同步，里面的文件不应算泄漏
    page_assets = {a for a in referenced if a.startswith("pages/")}
    assert page_assets, "没有枚举到 pages/ 下的资产，本反向测试失去意义"
    gaps = find_coverage_gaps(page_assets, set(), dirs)
    assert not gaps, f"pages/ 已被 rsync 同步，却仍被报为泄漏（假阳性）：{gaps}"


def test_checker_actually_flags_uncovered_asset():
    """正向对照：一个既不在清单、父目录也没同步的资产，必须被报出来。"""
    gaps = find_coverage_gaps({"totally-not-deployed.js"}, set(), _synced_dirs())
    assert gaps == ["totally-not-deployed.js"], (
        f"检查器对明显未覆盖的资产没有报错，判据是死的。实际：{gaps}"
    )


# --------------------------------------------------------------------------
# 4) 运行时资产目录：代码在运行时从磁盘读取的目录，必须同步
# --------------------------------------------------------------------------
# 2026-09-13 v9.9.26 上线后三方对账查出（同一类缺陷的第二例）：
# backend/prompts/ 不在 BACKEND_DIRS 里 —— 线上 close_review.md 停在 8/30 旧版、
# holding_diagnose.md 整个缺失、并滞留三个本地已删除的死 prompt。
# 后果：本轮的 prompt 防编造加固「代码上线了、prompt 没上线」，等于白做。
RUNTIME_ASSET_DIRS = {
    "backend/prompts/": "api/shared_helpers.py._load_named_prompt() 运行时按文件名读取的提示词",
}

PROMPT_LOADER = BACKEND / "api" / "shared_helpers.py"


def test_runtime_asset_dirs_are_deployed():
    """运行时资产目录必须全部落在部署同步范围内。"""
    synced = _synced_dirs()
    missing = [d for d in RUNTIME_ASSET_DIRS if d not in synced]
    assert not missing, (
        "以下目录会被代码在运行时读取，但不在部署同步范围内 —— 代码上线了、资产没上线：\n"
        + "\n".join(f"  - {d}（{RUNTIME_ASSET_DIRS[d]}）" for d in missing)
        + "\n修法：加进 backend/scripts/deploy_to_server.sh 的 BACKEND_DIRS。"
    )


def test_prompt_loader_reads_from_a_synced_directory():
    """交叉校验：**从 loader 源码推导**它真正读取的目录，断言该目录已同步。

    刻意不写死 ``backend/prompts/`` —— 若将来有人把 prompt 目录挪走，
    硬编码的断言会变成假的绿，而本测试会跟着报红。
    """
    assert PROMPT_LOADER.is_file(), f"prompt 加载器不存在：{PROMPT_LOADER}"
    src = PROMPT_LOADER.read_text(encoding="utf-8")

    m = re.search(r'Path\(__file__\)(.+?)/\s*"([A-Za-z_]+)"\s*/\s*filename', src)
    assert m, (
        "未能在 shared_helpers.py 中定位 prompt loader 的路径拼接形式 "
        "（期待形如 Path(__file__).parent.parent / \"prompts\" / filename）。"
        "若 loader 写法已变更，请同步更新本测试的解析式。"
    )
    ups = m.group(1).count("parent")
    dirname = m.group(2)

    # 从 loader 文件本身起算：Path(__file__).parent.parent 即「文件路径套两层 parent」，
    # 不是「已套一层 parent 的目录再套两层」。
    resolved = PROMPT_LOADER
    for _ in range(ups):
        resolved = resolved.parent
    resolved = resolved / dirname

    rel = resolved.relative_to(REPO_ROOT).as_posix().rstrip("/") + "/"
    assert rel in _synced_dirs(), (
        f"loader 实际从 {rel} 读取 prompt，但该目录不在部署同步范围内。"
        f"当前同步目录：{sorted(_synced_dirs())}"
    )
    # 落盘约定：该目录里必须真的有生产 prompt，否则说明推导结果跑偏了
    assert (resolved / "close_review.md").is_file(), (
        f"{rel} 下找不到 close_review.md，推导出的目录可能不对：{resolved}"
    )


def test_deleted_prompts_will_be_pruned_on_server():
    """死 prompt 的删除必须能传导到线上。

    rsync 带 ``--delete``，所以只要父目录在同步范围内，本地删除就会同步到线上。
    本测试锁定「父目录在范围内」这一前提 —— 否则线上会一直滞留着已删除的 prompt，
    而 fail-open 的 loader 读到的就是一个本该消失的文件。
    """
    synced = _synced_dirs()
    for dead in ("portfolio_diagnose.md", "signal_extract.md", "weekly_report.md"):
        assert not (BACKEND / "prompts" / dead).exists(), (
            f"{dead} 已被判定为死 prompt 并删除，却又出现在本地目录里"
        )
    assert "backend/prompts/" in synced, (
        "backend/prompts/ 不在同步范围内，本地已删除的死 prompt 会一直滞留在线上服务器。"
    )

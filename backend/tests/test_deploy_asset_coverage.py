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

### 第三例（本文件新增的「类级」守卫要解决的）

前两例都是「补一条具体条目」就完事，但根因不止于此：``BACKEND_DIRS`` 是
**目录枚举**而不是文件覆盖。任何**不在这张目录表里的目录，或直接躺在父包下的
松散文件**，都会静默不同步。已实测确认的漏网文件：

* ``backend/models/`` —— 整个目录不在任何清单里，却是运行时硬依赖：
  ``api/signals.py`` / ``api/user.py`` / ``api/chat.py`` / ``api/portfolio.py``
  都有 ``from models.schemas import ...``。服务器上当前内容**只是碰巧**与本地一致
  （靠已废弃的旧根目录 ``deploy.sh`` 全量传过一次），以后改 ``schemas.py`` 永远上不了线。
* ``backend/domain/__init__.py`` / ``backend/infra/__init__.py`` —— 父包自身的
  ``__init__.py``：清单只列了它们的子目录，父包文件从未同步，而这两个包被 import
  时 ``__init__.py`` 会真的执行。
* ``backend/infra/auth.py`` —— 直接躺在 ``infra/`` 下，``main.py:40`` 与
  ``api/auth.py:13`` 都 ``from infra.auth import ...``，但 ``infra/`` 下只有若干
  子目录在清单里。

### 共同根因

**同步清单是手工维护的，加文件/加目录就漏。** 这不是个案，是需要机械化守卫的
结构性缺陷。

## 判据

1. 「被 ``index.html`` / ``sw.js`` / ``manifest.json`` 引用到的本地静态资产」
   必须全部落在同步范围内（``FRONTEND_FILES`` 直接同步，或父目录被 rsync）。
2. 「代码在运行时从磁盘读取的目录」（当前为 ``backend/prompts/``）必须被同步。
3. **类级判据**：枚举 ``backend/`` 下**全部**可能参与运行时的源文件
   （``**/*.py`` / ``**/*.md`` / backend 根下任意文件），每一个都必须被
   ``BACKEND_FILES`` / ``BACKEND_LOOSE_FILES`` / ``BACKEND_DIRS`` 之一覆盖，
   或命中一张**显式、带理由**的豁免表。缺一即失败，且报错必须逐条列出文件路径。
   （前两例只是这条类级判据的三个具体实例。）

## 反「闸门空转」设计（本仓血教训）

本套件最危险的失效模式不是"漏判"，而是**解析器返回空集导致断言真空通过**：
一旦 ``deploy_to_server.sh`` 的数组写法变了、正则失配，``covered`` 变成 ``set()``，
若断言写成「泄露的资产为空」就会永远绿。因此：

* ``test_parser_really_parses_*`` 显式断言解析器必须解析出已知条目（非空 + 含 ``index.html``）
* ``test_prompt_loader_reads_from_a_synced_directory`` 从 loader **源码**推导目录，
  不硬编码路径，避免代码漂移后守卫失效
  （类级守卫同样有从 import 语句反推 ``backend/models/`` 的交叉校验）
* ``test_checker_detects_*`` / ``test_backend_checker_detects_*`` 用**故障注入**
  证明判据真的会红（含纯函数注入与文件级注入）
"""

from __future__ import annotations

import ast
import fnmatch
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


# ==========================================================================
# 5) 类级守卫：枚举全部后端源文件，逐个必须被覆盖或显式豁免
# ==========================================================================
# 第三例的根因是「目录枚举」而非「文件覆盖」——下面是把守卫从「守具体实例」
# 升级为「守整类」的机械判据。
#
# 豁免表刻意写成「显式 + 每条带理由」，并在 test_exemptions_all_carry_a_reason
# 里断言理由非空：宁可让新文件触发失败后人工判断，也不要静默放行。
# --------------------------------------------------------------------------
def _backend_files() -> set[str]:
    return set(_parse_shell_array(_deploy_script_text(), "BACKEND_FILES"))


def _backend_loose_files() -> set[str]:
    return set(_parse_shell_array(_deploy_script_text(), "BACKEND_LOOSE_FILES"))


def _backend_dirs() -> set[str]:
    dirs = set(_parse_shell_array(_deploy_script_text(), "BACKEND_DIRS"))
    return {d if d.endswith("/") else d + "/" for d in dirs}


def _backend_synced_files() -> set[str]:
    return _backend_files() | _backend_loose_files()


# --- 豁免表：目录前缀 -----------------------------------------------------
EXEMPT_BACKEND_DIR_PREFIXES: dict[str, str] = {
    "backend/tests/": "测试代码不部署：生产服务器不跑 pytest，测试已在 CI 全量执行",
    "backend/logs/": "运行时日志目录：由服务器本地生成，反向同步会污染本地",
    "backend/data/": (
        "运行时数据目录：服务器本地持仓/缓存数据，部署脚本另有 chown 修复逻辑，绝不能被覆盖"
    ),
    "backend/_archive/": "M1 之前的历史数据归档（纯数据、无 .py），不参与运行时",
    "backend/infra/.cache/": "运行时缓存目录：服务器本地生成，被 --delete 清掉会丢缓存",
    "backend/.mypy_cache/": "mypy 类型检查缓存，纯构建产物",
    "backend/.pytest_cache/": "pytest 运行缓存，纯构建产物",
    "backend/__pycache__/": "Python 字节码缓存目录",
}

# --- 豁免表：文件名 glob（只匹配 basename）--------------------------------
EXEMPT_BACKEND_FILE_PATTERNS: dict[str, str] = {
    ".env": "服务器本地密钥文件，覆盖会丢 TUSHARE_TOKEN 等生产凭据，绝不能被覆盖",
    ".env.*": "环境变量文件（.env.example 只是模板），一律不上线",
    "*.pyc": "Python 字节码缓存",
    "*.pyo": "Python 字节码缓存",
    "*.bak*": "垃圾备份文件（如服务器上的 schemas.py.bak-*），不该上线",
    "*.log": "运行时日志（服务器本地生成）",
    "requirements.txt": (
        "依赖清单：服务器依赖由 /opt/moneybag/venv 预装，部署脚本只提示手动 pip install，"
        "不走文件同步；依赖同步是另一类问题，不纳入本守卫"
    ),
    "Procfile": "Railway/Heroku 进程声明文件；本机部署走 systemd，与生产无关",
    "railway.toml": "Railway 平台配置；本机部署走 systemd，与生产无关",
    ".python-version": "pyenv 本地开发版本声明；服务器用 /opt/moneybag/venv 指定解释器",
}

# --- 豁免表：精确路径（用于「未接线的空占位包」）---------------------------
EXEMPT_BACKEND_FILES: dict[str, str] = {
    "backend/infra/config/__init__.py": (
        "未接线的空占位包（仅 docstring，全仓无任何 import 引用），不属于运行时依赖"
    ),
    "backend/infra/events/__init__.py": (
        "未接线的空占位包（仅 docstring，全仓无任何 import 引用），不属于运行时依赖"
    ),
}


def _exemption_reason(rel: str) -> str | None:
    """返回该 repo 相对路径的豁免理由；不豁免时返回 None。"""
    if rel in EXEMPT_BACKEND_FILES:
        return EXEMPT_BACKEND_FILES[rel]
    for prefix, reason in EXEMPT_BACKEND_DIR_PREFIXES.items():
        if rel.startswith(prefix):
            return reason
    base = rel.rsplit("/", 1)[-1]
    for pattern, reason in EXEMPT_BACKEND_FILE_PATTERNS.items():
        if fnmatch.fnmatch(base, pattern):
            return reason
    return None


def _all_exemptions() -> dict[str, str]:
    merged: dict[str, str] = dict(EXEMPT_BACKEND_FILES)
    merged.update(EXEMPT_BACKEND_DIR_PREFIXES)
    merged.update(EXEMPT_BACKEND_FILE_PATTERNS)
    return merged


def _enumerate_backend_sources() -> set[str]:
    """枚举 ``backend/`` 下**全部**可能参与运行时的文件（repo 相对路径）。

    枚举范围（刻意取宽，宁多勿漏）：
    * ``backend/**/*.py``
    * ``backend/**/*.md``（prompt / knowledge 等运行时可能读取的文本资产）
    * ``backend/`` 根目录下的**任意**文件（``.txt`` / ``.json`` / ``.sh`` / 无后缀等）

    刻意**不**依赖 ``git ls-files``（CI 上是浅克隆，且未跟踪的新文件正是最需要
    被守卫发现的），改用文件系统遍历 ``Path.rglob``。豁免项不在这里过滤 ——
    过滤统一在 ``find_backend_coverage_gaps`` 里做，便于故障注入直接喂脏数据。
    """
    found: set[str] = set()
    if not BACKEND.is_dir():
        return found
    for p in sorted(BACKEND.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(REPO_ROOT).as_posix()
        if p.suffix in (".py", ".md"):
            found.add(rel)
        elif p.parent == BACKEND:
            found.add(rel)
    return found


def find_backend_coverage_gaps(
    enumerated: set[str],
    synced_files: set[str],
    synced_dirs: set[str],
) -> list[str]:
    """返回「枚举到、但既不被同步也不豁免」的后端文件。空列表 = 覆盖完整。"""
    gaps: list[str] = []
    for rel in sorted(enumerated):
        if _exemption_reason(rel) is not None:
            continue
        if rel in synced_files:
            continue
        if any(rel.startswith(d) for d in sorted(synced_dirs)):
            continue
        gaps.append(rel)
    return gaps


def _module_is_trivial(path: Path) -> bool:
    """判断 .py 是否只有 docstring / 注释 / 空行（无可执行语句）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Pass):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # 裸字符串（docstring）或裸常量
        return False
    return True


# --------------------------------------------------------------------------
# 5.1 反空转：解析器与枚举器必须真的解析/枚举出东西
# --------------------------------------------------------------------------
def test_parser_really_parses_backend_manifests():
    """三张后端清单都必须解析出非空且含已知条目 —— 否则后面断言真空通过。"""
    files = _backend_files()
    loose = _backend_loose_files()
    dirs = _backend_dirs()

    assert files, (
        "BACKEND_FILES 解析结果为空。要么 deploy_to_server.sh 的数组写法变了、"
        "正则失配，要么清单被清空 —— 无论哪种，类级覆盖断言都会变成「闸门空转仍显绿」。"
    )
    assert loose, (
        "BACKEND_LOOSE_FILES 解析结果为空。父包 __init__.py / infra/auth.py 会重新变成"
        "不同步状态，且类级覆盖断言会真空通过。"
    )
    assert dirs, "BACKEND_DIRS 解析结果为空 —— backend/models/ 等目录会重新变成不同步状态。"

    assert "backend/main.py" in files, f"BACKEND_FILES 应含 backend/main.py，实际 {sorted(files)}"
    assert "backend/infra/auth.py" in loose, (
        f"BACKEND_LOOSE_FILES 应含 backend/infra/auth.py（运行时硬依赖），实际 {sorted(loose)}"
    )
    assert "backend/models/" in dirs, (
        f"BACKEND_DIRS 应含 backend/models/（api/*.py 的运行时依赖），实际 {sorted(dirs)}"
    )


def test_backend_source_enumeration_is_not_empty():
    """枚举结果不能为空，否则类级覆盖断言无对象可比。"""
    enumerated = _enumerate_backend_sources()
    assert enumerated, (
        "未能枚举出任何后端源文件 —— 枚举逻辑已失效（BACKEND 路径不对 / rglob 未跑）。"
    )
    for known in (
        "backend/api/chat.py",
        "backend/models/schemas.py",
        "backend/infra/auth.py",
        "backend/prompts/close_review.md",
        "backend/main.py",
    ):
        assert known in enumerated, (
            f"枚举结果缺少已知文件 {known}，枚举范围可能被收窄了：共 {len(enumerated)} 个"
        )


def test_enumerated_backend_sources_all_exist_on_disk():
    """枚举出的每个路径都必须真实存在（防枚举器产出幻觉路径）。"""
    missing = [f for f in sorted(_enumerate_backend_sources()) if not (REPO_ROOT / f).is_file()]
    assert not missing, "枚举结果包含并不存在的文件路径：\n" + "\n".join(f"  - {m}" for m in missing)


def test_exemptions_all_carry_a_reason():
    """豁免表必须显式且每条都带非空理由 —— 禁止静默放行。"""
    for key, reason in _all_exemptions().items():
        assert isinstance(reason, str), f"豁免项 {key} 的理由不是字符串：{reason!r}"
        assert len(reason.strip()) >= 8, (
            f"豁免项 {key} 的理由过于含糊（{reason!r}），必须写清为什么可以不上线"
        )


# --------------------------------------------------------------------------
# 5.2 主判据（类级）：所有后端源文件必须被覆盖或豁免
# --------------------------------------------------------------------------
def test_backend_sources_are_all_deployed_or_exempt():
    """**类级守卫**：枚举到的每个后端源文件都必须被同步或显式豁免。"""
    gaps = find_backend_coverage_gaps(
        _enumerate_backend_sources(), _backend_synced_files(), _backend_dirs()
    )
    assert not gaps, (
        "以下后端源文件既不在部署清单（BACKEND_FILES / BACKEND_LOOSE_FILES / "
        "BACKEND_DIRS）里，也不在豁免表里 —— 本地改了也永远不会上线：\n"
        + "\n".join(f"  - {g}" for g in gaps)
        + "\n修法：加进 deploy_to_server.sh 对应数组；若确认不该上线，"
        "加进本文件 EXEMPT_BACKEND_DIR_PREFIXES / EXEMPT_BACKEND_FILE_PATTERNS / "
        "EXEMPT_BACKEND_FILES 并写明理由。"
    )


def test_known_non_runtime_root_files_are_explicitly_exempt():
    """把「backend 根下非运行时文件」的豁免决定钉在测试里，防止被无声改掉。"""
    for rel, why in (
        ("backend/requirements.txt", "依赖同步靠 venv 手动 pip install，不走文件同步"),
        ("backend/Procfile", "Railway 进程声明，生产走 systemd"),
        ("backend/railway.toml", "Railway 平台配置，生产走 systemd"),
        ("backend/.python-version", "pyenv 本地版本声明，生产用 venv 解释器"),
        ("backend/.env.example", "环境变量模板，服务器用真实 .env"),
        ("backend/tests/test_deploy_asset_coverage.py", "测试代码不部署"),
        ("backend/logs/x.log", "运行时日志"),
        ("backend/data/x.json", "运行时数据"),
        ("backend/infra/.cache/x.json", "运行时缓存"),
        ("backend/models/schemas.py.bak-20260901", "垃圾备份"),
    ):
        reason = _exemption_reason(rel)
        assert reason, f"{rel} 应被显式豁免（理由：{why}），实际未被豁免"


# --------------------------------------------------------------------------
# 5.3 交叉校验：从**源码里的 import 语句**反推运行时依赖，不能硬编码
# --------------------------------------------------------------------------
def _files_importing(module_prefix: str) -> list[str]:
    """返回 backend 下所有 `from <module_prefix>... import` / `import <module_prefix>...`
    的非豁免生产文件（排除 tests/ 与 _archive/）。"""
    hits: list[str] = []
    for rel in sorted(_enumerate_backend_sources()):
        if rel.startswith(("backend/tests/", "backend/_archive/")):
            continue
        if not rel.endswith(".py"):
            continue
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        if re.search(rf"^\s*(from|import)\s+{re.escape(module_prefix)}\b", src, re.MULTILINE):
            hits.append(rel)
    return hits


def test_models_package_is_required_by_runtime_imports():
    """``backend/models/`` 必须被覆盖 —— 判据来自**源码里的 import 语句**。

    刻意不写「backend/models/ 必须出现在某个数组里」这种硬编码：先从源码找出
    到底哪些生产文件 import 了 models，再断言该包整体被同步覆盖。
    """
    importers = _files_importing("models")
    assert importers, "没有找到任何 import models 的生产文件，交叉校验失去意义"
    assert (BACKEND / "models" / "schemas.py").is_file(), (
        "backend/models/schemas.py 不存在，但 api/*.py 仍在 import models.schemas —— "
        "要么文件被误删，要么 import 路径已变，需人工确认"
    )

    synced_files = _backend_synced_files()
    synced_dirs = _backend_dirs()
    for target in ("backend/models/__init__.py", "backend/models/schemas.py"):
        covered = target in synced_files or any(target.startswith(d) for d in synced_dirs)
        assert covered, (
            f"{target} 被以下生产文件在运行时 import 引用：{importers}，"
            "但它不在部署同步范围内 —— 线上跑的是旧版/缺失模块。"
        )


def test_infra_auth_and_parent_package_inits_are_covered():
    """``infra.auth`` 与父包 ``__init__.py`` 必须被覆盖，判据同样来自 import 源码。"""
    importers = _files_importing("infra") + _files_importing("infra.auth")
    assert any("infra/auth" in rel or "main.py" in rel for rel in importers), (
        f"未能从源码定位 infra.auth 的引用方，实际命中：{importers}"
    )

    synced_files = _backend_synced_files()
    synced_dirs = _backend_dirs()
    for target in (
        "backend/infra/auth.py",
        "backend/infra/__init__.py",
        "backend/domain/__init__.py",
        "backend/__init__.py",
    ):
        covered = target in synced_files or any(target.startswith(d) for d in synced_dirs)
        assert covered, (
            f"{target} 不在部署同步范围内。infra/ 与 domain/ 作为包被 import 时其 "
            f"__init__.py 会真的执行；infra/auth.py 被 {importers} 直接 import。"
        )


def test_exempt_placeholder_packages_stay_trivial():
    """豁免的「空占位包」必须一直是空的：一旦有人往里写代码，本测试报红强制重新决策。"""
    for rel, _reason in EXEMPT_BACKEND_FILES.items():
        path = REPO_ROOT / rel
        assert path.is_file(), f"被豁免的空占位包不存在了：{rel}（应同步更新豁免表）"
        assert _module_is_trivial(path), (
            f"{rel} 已被豁免为「未接线的空占位包」，但现在里面有可执行代码了 —— "
            "说明该包已接线，必须从豁免表移出并加进部署清单。"
        )


# --------------------------------------------------------------------------
# 5.4 故障注入：证明类级判据真的会红（纯函数级）
# --------------------------------------------------------------------------
def test_backend_checker_detects_injected_missing_dir_entry():
    """摘掉 ``backend/models/``（真实踩过的那个坑），检查器必须点名报出里面的文件。"""
    enumerated = _enumerate_backend_sources()
    files = _backend_synced_files()
    dirs = _backend_dirs()

    assert not find_backend_coverage_gaps(enumerated, files, dirs), (
        "注入前就存在覆盖缺口，说明清单没修干净，先修清单再看本测试"
    )

    injected = {d for d in dirs if d != "backend/models/"}
    gaps = find_backend_coverage_gaps(enumerated, files, injected)
    assert "backend/models/schemas.py" in gaps, (
        "故障注入失败：摘掉 backend/models/ 后检查器仍未报出 backend/models/schemas.py，"
        f"判据是死的。实际报出：{gaps}"
    )
    assert "backend/models/__init__.py" in gaps, (
        f"摘掉 backend/models/ 后应同时报出 __init__.py。实际报出：{gaps}"
    )


def test_backend_checker_detects_injected_missing_loose_file():
    """摘掉 ``backend/infra/auth.py``（松散文件的真实坑），必须被抓到并点名。"""
    enumerated = _enumerate_backend_sources()
    dirs = _backend_dirs()
    injected = {f for f in _backend_synced_files() if f != "backend/infra/auth.py"}

    gaps = find_backend_coverage_gaps(enumerated, injected, dirs)
    assert "backend/infra/auth.py" in gaps, (
        "故障注入失败：摘掉 backend/infra/auth.py 后检查器仍未报出，判据是死的。"
        f"实际报出：{gaps}"
    )


def test_backend_checker_detects_injected_missing_parent_init():
    """摘掉 ``backend/infra/__init__.py``，同样必须被抓到。"""
    enumerated = _enumerate_backend_sources()
    dirs = _backend_dirs()
    injected = {f for f in _backend_synced_files() if f != "backend/infra/__init__.py"}

    gaps = find_backend_coverage_gaps(enumerated, injected, dirs)
    assert "backend/infra/__init__.py" in gaps, (
        f"摘掉 backend/infra/__init__.py 后检查器未报出，判据不完整。实际报出：{gaps}"
    )


# --------------------------------------------------------------------------
# 5.5 反向注入 / 正向对照：防假阳性、防死判据
# --------------------------------------------------------------------------
def test_backend_checker_does_not_flag_exempt_or_synced_paths():
    """反向注入：豁免项与已同步项都不得被误报（防「宁可错杀」的假阳性）。"""
    exempt_samples = {
        "backend/tests/test_foo.py",
        "backend/tests/fixtures/x.json",
        "backend/logs/app.log",
        "backend/data/users/a.json",
        "backend/_archive/data-pre-m1/README.md",
        "backend/infra/.cache/quote.json",
        "backend/.mypy_cache/3.13/x.py",
        "backend/.env",
        "backend/.env.example",
        "backend/requirements.txt",
        "backend/Procfile",
        "backend/railway.toml",
        "backend/.python-version",
        "backend/infra/config/__init__.py",
        "backend/infra/events/__init__.py",
        "backend/models/schemas.py.bak-20260901",
    }
    gaps = find_backend_coverage_gaps(exempt_samples, set(), _backend_dirs())
    assert not gaps, f"豁免项被误报为覆盖缺口（假阳性）：{gaps}"

    synced_samples = {
        "backend/main.py",
        "backend/config.py",
        "backend/infra/auth.py",
        "backend/__init__.py",
        "backend/api/chat.py",
        "backend/models/schemas.py",
        "backend/prompts/close_review.md",
        "backend/infra/knowledge/content/72-rule.md",
    }
    gaps = find_backend_coverage_gaps(
        synced_samples, _backend_synced_files(), _backend_dirs()
    )
    assert not gaps, f"已同步的文件被误报为覆盖缺口（假阳性）：{gaps}"


def test_backend_checker_actually_flags_uncovered_source():
    """正向对照：一个既不在清单、也不豁免的源文件必须被点名报出。"""
    fabricated = "backend/brand_new_package/nested/module.py"
    gaps = find_backend_coverage_gaps({fabricated}, _backend_synced_files(), _backend_dirs())
    assert gaps == [fabricated], (
        f"检查器对明显未覆盖的源文件没有报错，判据是死的。实际：{gaps}"
    )
    assert fabricated in "\n".join(gaps), "报错信息必须点出具体文件路径"


def test_backend_checker_respects_directory_prefix_boundary():
    """前缀匹配必须按目录边界，不得把 ``backend/models2/`` 误认成 ``backend/models/``。"""
    fabricated = "backend/models2/schemas.py"
    gaps = find_backend_coverage_gaps({fabricated}, _backend_synced_files(), _backend_dirs())
    assert gaps == [fabricated], (
        f"目录前缀匹配越界了：{fabricated} 不应被 backend/models/ 覆盖。实际：{gaps}"
    )

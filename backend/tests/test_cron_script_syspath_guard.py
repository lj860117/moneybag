"""
cron 脚本 sys.path 引导守卫（P0 防回归）
=======================================

事故背景（2026-09-14 生产）
--------------------------
生产 crontab 里有::

    0 22 * * 0 cd /opt/moneybag/backend && set -a && . .env && set +a \\
        && /opt/moneybag/venv/bin/python scripts/fund_rank_build.py >> .../fund_rank.log 2>&1

这条任务从 2026-09-10 起**每次都崩**，崩在 import 期::

    ModuleNotFoundError: No module named 'infra'
    File "/opt/moneybag/backend/scripts/fund_rank_build.py", line 51, in <module>
      from backend.services.tushare_data import (...)
    File "/opt/moneybag/backend/services/tushare_data.py", line 28, in <module>
      from infra.cache import MemoryCache

根因链：
  1. ``fund_rank_build.py`` 只把**仓库根**（/opt/moneybag）插进 sys.path，
     没插 ``backend/``。而 ``infra/`` 包位于 ``/opt/moneybag/backend/infra/``。
  2. ``backend/services/*.py`` 内部用的是**以 backend/ 为根**的绝对导入
     （``from infra.cache import ...``），所以只插仓库根必然在二级 import 处炸。
  3. 为什么只有 cron 崩、API 不崩：uvicorn 从 ``/opt/moneybag/backend`` 启动，
     cwd 在 sys.path 里 → ``infra`` 找得到 → **API 侧一直正常，洞从没暴露**。
     而 ``python scripts/x.py`` 调用时 ``sys.path[0]`` 是**脚本所在目录**
     ``.../backend/scripts``，**不是 cwd** → 找不到 ``infra``。
  4. 业务影响：``data/fund_rank_ts.json`` 停更，``night_worker.py`` 的 72 小时
     过期判断生效 → **基金推荐功能整体失效**。

为什么这个洞之前测不出来
------------------------
``python -c "import xxx"`` 的 ``sys.path[0]`` 是 ``''``（cwd）。只要 cwd 恰好是
``backend/``，用 ``-c`` 复现就会得到 IMPORT_OK 的**假阴性**。所以本文件的运行时
用例一律用**真子进程 + 脚本模式探针**（见 _run_probe），不用 ``-c``。

守卫覆盖三层
------------
1. **静态 AST 层**：每个 import 了 backend-local 顶层包的脚本，都必须有指向
   ``backend/`` 的 sys.path 引导，且模块级引导必须早于首个模块级 backend-local import。
   （静态层能抓到「import 写在函数体内」的延迟导入——运行时探针跑不到函数体。）
2. **运行时子进程层**：真起一个独立 Python 进程，以 ``python <probe>.py`` 脚本模式
   ``runpy.run_path(script, run_name='__not_main__')``，断言 stderr 里没有
   ``ModuleNotFoundError: No module named '<backend-local 包>'``。
   ``run_name != '__main__'`` 保证不会触发 ``main()``，不会真跑业务逻辑。
3. **故障注入自证层**：在 tmp_path 里搭一棵镜像目录，把**真实脚本副本**的
   ``backend/`` 引导整段删掉，断言上面两层对它会报 FAIL —— 证明守卫是活的，
   而不是一组永远为真的断言。

Pitfall 21 合规：守卫必须做故障注入证明它会红，且只红预期的那几条。
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# 路径常量（全部从 __file__ 推导，不依赖 cwd / PYTHONPATH）
# ─────────────────────────────────────────────────────────────────────────────
TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent
SCRIPTS_DIR = BACKEND_DIR / "scripts"
REPO_ROOT = BACKEND_DIR.parent

# sys.path 引导目标的「深度」语义（相对 backend/scripts/xxx.py 的 __file__）
DEPTH_SCRIPTS = 1   # .../backend/scripts
DEPTH_BACKEND = 2   # .../backend      ← config / services / infra 在这里
DEPTH_ROOT = 3      # 仓库根            ← backend 这个包前缀在这里
_TARGET_BY_DEPTH = {DEPTH_SCRIPTS: "scripts", DEPTH_BACKEND: "backend", DEPTH_ROOT: "root"}
_TARGET_LABEL = {"scripts": "scripts/", "backend": "backend/", "root": "仓库根", "unknown": "unknown"}


def _backend_local_top_levels() -> frozenset[str]:
    """backend/ 目录下所有顶层可导入名（包或模块），外加 'backend' 这个包前缀本身。"""
    names: set[str] = {"backend"}
    for child in BACKEND_DIR.iterdir():
        if child.name.startswith("_"):
            continue
        if child.is_dir() and (child / "__init__.py").exists():
            names.add(child.name)
        elif child.is_file() and child.suffix == ".py":
            names.add(child.stem)
    return frozenset(names)


LOCAL_TOP_LEVELS: frozenset[str] = _backend_local_top_levels()

# 只需要「能被发现」的脚本，不需要跑业务逻辑的脚本（见文档字符串里的理由）
_MODULE_NOT_FOUND_RE = re.compile(r"ModuleNotFoundError: No module named '([^']+)'")
_PROBE_SENTINEL = "__CRON_SYSPATH_PROBE_RAN__"
_PROBE_TIMEOUT_SEC = 180

# 反空转计数器：由 _run_probe 累加，末尾的守卫用例断言它们 > 0
_RUNTIME_PROBE_CALLS: list[str] = []


# ─────────────────────────────────────────────────────────────────────────────
# 一、静态 AST 分析
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class _ImportFact:
    """一条「import 了 backend-local 包」的记录。"""

    lineno: int
    module: str
    scope: str              # "module" 或 "<fn>.<fn>" 形式的函数作用域
    seq: int                # 前序遍历序号，用于比较先后
    needed: frozenset[str]  # 这条 import 要求哪些目录在 sys.path 里

    @property
    def needed_label(self) -> str:
        """人类可读的「需要什么」描述。"""
        return " + ".join(_TARGET_LABEL[t] for t in sorted(self.needed))


@dataclass(frozen=True)
class _BootFact:
    """一条 sys.path 引导记录。"""

    lineno: int
    start_line: int
    end_line: int
    target: str     # "scripts" / "backend" / "root" / "unknown"
    scope: str
    seq: int


@dataclass
class _ScriptFacts:
    """一个脚本文件的静态事实快照。"""

    path: Path
    imports: list[_ImportFact] = field(default_factory=list)
    boots: list[_BootFact] = field(default_factory=list)


def _is_file_ref(node: ast.AST) -> bool:
    """判断 AST 节点是否是 `__file__` 引用。"""
    if isinstance(node, ast.Name):
        return node.id == "__file__"
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str) and "__file__" in node.value
    return False


def _path_depth(node: ast.AST, varmap: dict[str, int], extra: int = 0) -> int | None:
    """计算一个路径表达式相对 `__file__` 往上跳了几级。

    返回 ``None`` 表示静态无法判定（例如字符串常量、环境变量）。

    :param node: 待判定的 AST 表达式
    :param varmap: 变量名 -> 深度 的映射，用于解析 `_BACKEND_DIR` 这类中间变量
    :param extra: 已累积的跳数（递归用）
    :return: 1=scripts/, 2=backend/, 3=仓库根，无法判定返回 None
    """
    if _is_file_ref(node):
        return extra
    if isinstance(node, ast.Name):
        return None if node.id not in varmap else varmap[node.id] + extra
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        return _path_depth(node.value, varmap, extra + 1)
    if isinstance(node, ast.Subscript):
        base = node.value
        if isinstance(base, ast.Attribute) and base.attr == "parents":
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int):
                # parents[0] 是 scripts/，所以要比 .parent 链 +1
                return _path_depth(base.value, varmap, extra + node.slice.value + 1)
        return None
    if isinstance(node, ast.Call):
        fn = node.func
        is_dirname = (isinstance(fn, ast.Attribute) and fn.attr == "dirname") or (
            isinstance(fn, ast.Name) and fn.id == "dirname"
        )
        if is_dirname and node.args:
            return _path_depth(node.args[0], varmap, extra + 1)
        if isinstance(fn, ast.Attribute) and fn.attr == "abspath" and node.args:
            return _path_depth(node.args[0], varmap, extra)
        if isinstance(fn, ast.Attribute) and fn.attr == "join" and len(node.args) >= 2:
            ups = sum(1 for a in node.args[1:]
                      if isinstance(a, ast.Constant) and a.value == "..")
            return _path_depth(node.args[0], varmap, extra + ups)
        if isinstance(fn, ast.Attribute) and fn.attr in ("resolve", "absolute"):
            return _path_depth(fn.value, varmap, extra)
        if isinstance(fn, ast.Name) and fn.id in ("Path", "str") and node.args:
            return _path_depth(node.args[0], varmap, extra)
    return None


def _is_syspath_boot(node: ast.AST) -> bool:
    """判断 AST 节点是否是 `sys.path.insert(...)` / `sys.path.append(...)` 调用。"""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr not in ("insert", "append"):
        return False
    owner = node.func.value
    return isinstance(owner, ast.Attribute) and owner.attr == "path" \
        and isinstance(owner.value, ast.Name) and owner.value.id == "sys" and bool(node.args)


def _local_import_names(node: ast.AST) -> list[str]:
    """返回该 import 语句里属于 backend-local 的完整模块名列表。"""
    found: list[str] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name.split(".")[0] in LOCAL_TOP_LEVELS:
                found.append(alias.name)
    elif isinstance(node, ast.ImportFrom):
        if node.level and node.level > 0:      # 相对导入，不依赖 sys.path
            return found
        if node.module and node.module.split(".")[0] in LOCAL_TOP_LEVELS:
            found.append(node.module)
    return found


def _collect_facts(path: Path) -> _ScriptFacts:
    """解析单个脚本，收集 backend-local import 与 sys.path 引导事实。"""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    facts = _ScriptFacts(path=path)

    # 先做 3 轮变量深度推导，容忍 `_BACKEND_DIR = _SCRIPT_DIR.parent` 这类前向/后向引用
    varmap: dict[str, int] = {}
    for _ in range(3):
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name):
                depth = _path_depth(node.value, varmap)
                if depth is not None:
                    varmap[node.targets[0].id] = depth

    loopvars = _collect_loopvar_depths(tree, varmap)
    counter = iter(range(10**9))

    def walk(nodes: list[ast.stmt], scope: str) -> None:
        for node in nodes:
            seq = next(counter)
            # 函数/类体：换作用域继续遍历（延迟 import 也要纳入静态检查）
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk(node.body, f"{scope}.{node.name}")
                continue
            for name in _local_import_names(node):
                # backend/ 是**无条件**必需的：即使写的是 `import backend.services.x`，
                # 那个模块内部用的也是 `from infra.cache import ...` 这种以 backend/
                # 为根的绝对导入，只插仓库根会在二级 import 处抛 No module named 'infra'
                # —— 2026-09-14 生产事故就是这么发生的。
                needed: set[str] = {"backend"}
                if name.split(".")[0] == "backend":
                    needed.add("root")   # `backend` 这个包前缀本身要仓库根
                facts.imports.append(
                    _ImportFact(node.lineno, name, scope, seq, frozenset(needed))
                )
            # 形如 `if X not in sys.path: sys.path.insert(...)` 的整块 If 也算一条引导
            if isinstance(node, ast.If) and len(node.body) == 1 \
                    and isinstance(node.body[0], ast.Expr) \
                    and _is_syspath_boot(node.body[0].value):
                _record_boot(node.body[0].value, scope, seq, node, facts, varmap, loopvars)
                walk(node.orelse, scope)
                continue
            if isinstance(node, ast.Expr) and _is_syspath_boot(node.value):
                _record_boot(node.value, scope, seq, node, facts, varmap, loopvars)
                continue
            for attr in ("body", "orelse", "finalbody"):
                child = getattr(node, attr, None)
                if isinstance(child, list):
                    walk(child, scope)
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    walk(handler.body, scope)

    walk(tree.body, "module")
    return facts


def _collect_loopvar_depths(tree: ast.AST, varmap: dict[str, int]) -> dict[str, set[int]]:
    """解析 `for _p in (str(ROOT), str(BACKEND_DIR)): sys.path.insert(0, _p)` 这种写法。

    循环变量每次迭代指向不同深度的路径，所以它代表**一组**引导目标。

    :return: 循环变量名 -> 该变量可能指向的深度集合
    """
    result: dict[str, set[int]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.For) or not isinstance(node.target, ast.Name):
            continue
        if not isinstance(node.iter, (ast.Tuple, ast.List)):
            continue
        depths = {
            depth
            for elt in node.iter.elts
            if (depth := _path_depth(elt, varmap)) is not None
        }
        if depths:
            result.setdefault(node.target.id, set()).update(depths)
    return result


def _record_boot(call: ast.Call, scope: str, seq: int, stmt: ast.stmt,
                 facts: _ScriptFacts, varmap: dict[str, int],
                 loopvars: dict[str, set[int]] | None = None) -> None:
    """把一条 sys.path 引导记录进 facts（循环变量会展开成多条）。"""
    arg = call.args[-1]
    depths: set[int] = set()
    if isinstance(arg, ast.Name) and loopvars and arg.id in loopvars:
        depths |= loopvars[arg.id]
    single = _path_depth(arg, varmap)
    if single is not None:
        depths.add(single)
    if not depths:
        depths = set()          # 静态无法判定，记为 unknown
    start = stmt.lineno
    end = getattr(stmt, "end_lineno", None) or stmt.lineno
    for depth in sorted(depths):
        facts.boots.append(
            _BootFact(
                lineno=start,
                start_line=start,
                end_line=end,
                target=_TARGET_BY_DEPTH.get(depth, "unknown"),
                scope=scope,
                seq=seq,
            )
        )
    if not depths:
        facts.boots.append(
            _BootFact(start, start, end, "unknown", scope, seq)
        )


def _boot_satisfies(boot: _BootFact, imp: _ImportFact, target: str) -> bool:
    """判断一条引导能否满足一条 import 对 `target` 的需求。

    - 模块级引导先于任何函数体执行，所以对函数内 import 无条件满足；
      对模块级 import 则必须出现在它之前。
    - 函数内的引导只对本函数内、且出现在它之前的 import 有效。
    """
    if boot.target != target:
        return False
    if boot.scope == "module":
        return True if imp.scope != "module" else boot.seq < imp.seq
    return boot.scope == imp.scope and boot.seq < imp.seq


def find_bootstrap_problems(path: Path) -> list[str]:
    """静态检查一个脚本的 sys.path 引导是否完备。

    :param path: 脚本绝对路径
    :return: 问题描述列表；空列表表示通过
    """
    facts = _collect_facts(path)
    problems: list[str] = []
    for imp in facts.imports:
        for target in sorted(imp.needed):
            if not any(_boot_satisfies(b, imp, target) for b in facts.boots):
                problems.append(
                    f"L{imp.lineno} `import {imp.module}` 需要 {_TARGET_LABEL[target]} 在 sys.path 里，"
                    f"但脚本没有对应的引导（作用域={imp.scope}，需求={imp.needed_label}）"
                )
    return problems


# ─────────────────────────────────────────────────────────────────────────────
# 二、运行时子进程探针
# ─────────────────────────────────────────────────────────────────────────────
def _probe_env(cwd: Path) -> dict[str, str]:
    """构造一个干净、不继承 PYTHONPATH 的子进程环境（贴近 cron）。"""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(Path.home()),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        # 防止被脚本的模块级代码写进真实数据目录
        "DATA_DIR": str(cwd / "_probe_data"),
    }
    if os.environ.get("PYTHONHASHSEED"):
        env["PYTHONHASHSEED"] = os.environ["PYTHONHASHSEED"]
    return env


# 两种调用形态都要守住：
#   "abs"           —— 绝对路径 `python /…/backend/scripts/x.py`
#   "cron_relative" —— 生产 crontab 的原样形态：cwd=backend/ + `python scripts/x.py`
# 后者更真实，也更能暴露「__file__ 没 resolve 导致 parents[N] 算错」这类缺陷。
INVOCATION_MODES = ("abs", "cron_relative")


def _run_probe(script: Path, cwd: Path, mode: str = "abs",
               extra_syspath: tuple[Path, ...] = ()) -> tuple[int, str]:
    """以**真子进程 + 脚本模式**执行脚本的模块级代码。

    刻意不用 `python -c`：`-c` 的 sys.path[0] 是 cwd，会得到假阴性。
    探针文件放在 cwd，于是 sys.path[0] 也是 cwd，与 cron 的 `python scripts/x.py`
    一样**不会**自动带入 backend/。

    :param script: 被检查脚本的绝对路径
    :param cwd: 子进程工作目录
    :param mode: "abs" 用绝对路径喂给 runpy；"cron_relative" 则切到 backend/
        作为 cwd、用 `scripts/x.py` 这种相对路径喂给它，与生产 crontab 完全一致
    :param extra_syspath: 额外插入 sys.path 的目录（用于还原 cron 的 scripts/ 在 path[0]）
    :return: (returncode, stdout+stderr)
    """
    cwd.mkdir(parents=True, exist_ok=True)
    probe = cwd / f"_probe_{script.stem}_{mode}.py"
    if mode == "cron_relative":
        run_cwd = BACKEND_DIR
        script_arg = f"{SCRIPTS_DIR.name}/{script.name}"
        assert script.parent == SCRIPTS_DIR, "cron_relative 只适用于 scripts/ 下的脚本"
    else:
        run_cwd = cwd
        script_arg = str(script)

    lines = ["import sys"]
    for entry in extra_syspath:
        lines.append(f"sys.path.insert(0, {str(entry)!r})")
    lines += [
        "import runpy",
        f"print({_PROBE_SENTINEL!r})",
        # run_name != '__main__' → 不触发 main()，只跑模块级代码
        f"runpy.run_path({script_arg!r}, run_name='__not_main__')",
    ]
    probe.write_text("\n".join(lines) + "\n", encoding="utf-8")

    _RUNTIME_PROBE_CALLS.append(f"{script.name}:{mode}")
    proc = subprocess.run(
        [sys.executable, str(probe)],
        cwd=str(run_cwd),
        env=_probe_env(cwd),
        capture_output=True,
        text=True,
        timeout=_PROBE_TIMEOUT_SEC,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _missing_backend_modules(output: str) -> list[str]:
    """从子进程输出里抽取「backend-local 包」的 ModuleNotFoundError。"""
    return sorted(
        {
            name
            for name in _MODULE_NOT_FOUND_RE.findall(output)
            if name.split(".")[0] in LOCAL_TOP_LEVELS
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# 三、故障注入用的镜像目录树
# ─────────────────────────────────────────────────────────────────────────────
def _build_mirror_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    """在 tmp_path 下搭一棵镜像目录树，让真实脚本能在里面跑出真实的 import 链::

        tmp_path/repo/                  ← 仓库根 analog（import backend.* 用）
            backend/                    ← backend analog（import config/infra/services 用）
                __init__.py
                infra      -> 真 backend/infra       （软链，保持真实 import 链）
                services   -> 真 backend/services
                config.py  -> 真 backend/config.py
                ...
                scripts/                ← 放待测脚本副本

    于是副本里 `Path(__file__).resolve().parents[2]` == tmp_path/repo（仓库根），
    `parents[1]` == tmp_path/repo/backend。这与生产 /opt/moneybag 的布局逐层对应，
    所以「只插仓库根」的缺陷版本会**真的**抛出 No module named 'infra'。

    :return: (仓库根 analog, backend analog, scripts 目录)
    """
    root = tmp_path / "repo"
    backend = root / "backend"
    scripts = backend / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (backend / "__init__.py").write_text("", encoding="utf-8")

    linked = 0
    for child in sorted(BACKEND_DIR.iterdir()):
        if child.name in {"scripts", "__pycache__", "tests", "data", "logs"}:
            continue
        if child.name.startswith(".") or child.name.startswith("_"):
            continue
        if child.is_dir() and (child / "__init__.py").exists():
            (backend / child.name).symlink_to(child, target_is_directory=True)
            linked += 1
        elif child.is_file() and child.suffix == ".py":
            (backend / child.name).symlink_to(child)
            linked += 1
    assert linked > 0, "镜像目录树一个软链都没建出来，守卫会空转"
    return root, backend, scripts


def _strip_backend_bootstrap(source: str) -> tuple[str, int]:
    """把源码里所有指向 backend/ 的 sys.path 引导整段删掉，制造「缺陷版本」。

    连 `if _X not in sys.path:` 的 If 块一起删，避免留下空 body 导致语法错误。

    :return: (删改后的源码, 被删除的源码行数)
    """
    tree = ast.parse(source)

    # 先推导一遍变量深度（容忍 `_BACKEND_DIR = _SCRIPT_DIR.parent` 这类间接写法）
    varmap: dict[str, int] = {}
    for _ in range(3):
        for sub in ast.walk(tree):
            if isinstance(sub, ast.Assign) and len(sub.targets) == 1 \
                    and isinstance(sub.targets[0], ast.Name):
                depth = _path_depth(sub.value, varmap)
                if depth is not None:
                    varmap[sub.targets[0].id] = depth

    loopvars = _collect_loopvar_depths(tree, varmap)
    drop: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and len(node.body) == 1 \
                and isinstance(node.body[0], ast.Expr) \
                and _is_syspath_boot(node.body[0].value):
            stmt, call = node, node.body[0].value
        elif isinstance(node, ast.Expr) and _is_syspath_boot(node.value):
            stmt, call = node, node.value
        else:
            continue
        arg = call.args[-1]
        depths: set[int] = set()
        if isinstance(arg, ast.Name) and arg.id in loopvars:
            depths |= loopvars[arg.id]
        single = _path_depth(arg, varmap)
        if single is not None:
            depths.add(single)
        if DEPTH_BACKEND in depths:
            last = getattr(stmt, "end_lineno", None) or stmt.lineno
            drop.update(range(stmt.lineno, last + 1))

    lines = source.splitlines()
    kept = [line for idx, line in enumerate(lines, start=1) if idx not in drop]
    return "\n".join(kept) + "\n", len(drop)


# ─────────────────────────────────────────────────────────────────────────────
# 四、用例
# ─────────────────────────────────────────────────────────────────────────────
def _discover_scripts() -> list[Path]:
    """发现所有需要被守卫的脚本（有 backend-local import 的）。"""
    found: list[Path] = []
    for path in sorted(SCRIPTS_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        if _collect_facts(path).imports:
            found.append(path)
    return found


DISCOVERED_SCRIPTS: list[Path] = _discover_scripts()
SCRIPT_IDS: list[str] = [p.name for p in DISCOVERED_SCRIPTS]


def test_guard_discovers_scripts() -> None:
    """反空转：扫描必须真的扫到脚本，否则下面所有参数化用例都是 0 条空转通过。"""
    assert SCRIPTS_DIR.is_dir(), f"脚本目录不存在: {SCRIPTS_DIR}"
    assert len(DISCOVERED_SCRIPTS) >= 20, (
        f"只发现 {len(DISCOVERED_SCRIPTS)} 个脚本，glob 路径可能写错了: {SCRIPTS_DIR}"
    )
    assert "fund_rank_build.py" in SCRIPT_IDS, "生产事故脚本没被扫到，守卫失去意义"
    assert "night_worker.py" in SCRIPT_IDS, "夜班主链路脚本没被扫到"


@pytest.mark.parametrize("script", DISCOVERED_SCRIPTS, ids=SCRIPT_IDS)
def test_static_bootstrap_is_complete(script: Path) -> None:
    """静态层：每个 backend-local import 都必须有对应的 sys.path 引导，且顺序正确。"""
    problems = find_bootstrap_problems(script)
    assert not problems, (
        f"{script.name} 的 sys.path 引导不完备，cron 以 `python scripts/{script.name}` "
        f"调用时会 ModuleNotFoundError:\n  - " + "\n  - ".join(problems)
    )


@pytest.mark.parametrize("mode", INVOCATION_MODES)
@pytest.mark.parametrize("script", DISCOVERED_SCRIPTS, ids=SCRIPT_IDS)
def test_script_imports_cleanly_in_cron_mode(script: Path, mode: str, tmp_path: Path) -> None:
    """运行层：真子进程、脚本模式，不得出现 backend-local 的 ModuleNotFoundError。

    extra_syspath 只补 scripts/ —— 这正是 cron 里 `python scripts/x.py` 的 sys.path[0]，
    既不额外开后门（不给仓库根），也忠实还原了「脚本之间可以互相 import」这一事实。

    mode="cron_relative" 是与生产 crontab 逐字一致的形态（cwd=backend/ + 相对路径），
    它能额外抓到「`Path(__file__)` 没 resolve 导致 parents[N] 算成 `.`」这类缺陷。
    """
    workdir = tmp_path / f"run_{script.stem}_{mode}"
    _rc, output = _run_probe(script, workdir, mode=mode, extra_syspath=(SCRIPTS_DIR,))

    assert _PROBE_SENTINEL in output, (
        f"探针子进程没有真正执行（看不到哨兵输出），用例会假通过。raw output:\n{output[:2000]}"
    )
    missing = _missing_backend_modules(output)
    assert not missing, (
        f"{script.name} 以 cron 方式（mode={mode}）执行时找不到 backend-local 包: {missing}\n"
        f"—— 这就是 2026-09-14 生产事故的原样复现。\n"
        f"子进程输出尾部:\n{output[-2000:]}"
    )


def test_fund_rank_build_regression_is_pinned() -> None:
    """把 2026-09-14 的生产事故脚本单独钉死，避免它淹没在参数化用例里。"""
    script = SCRIPTS_DIR / "fund_rank_build.py"
    assert script.is_file(), f"事故脚本不见了: {script}"
    assert not find_bootstrap_problems(script), (
        "fund_rank_build.py 的 sys.path 引导被改坏了 —— 它必须在 import "
        "backend.services.tushare_data 之前把 backend/ 加进 sys.path，"
        "否则 tushare_data 内部的 `from infra.cache import MemoryCache` 会炸。"
    )


# ── 故障注入：证明守卫会红 ──────────────────────────────────────────────────
def test_fault_injection_static_detects_missing_backend_bootstrap(tmp_path: Path) -> None:
    """静态层故障注入：删掉 backend/ 引导的副本必须被判 FAIL，原版必须被判 PASS。"""
    _root, _backend, scripts = _build_mirror_tree(tmp_path)
    original = (SCRIPTS_DIR / "fund_rank_build.py").read_text(encoding="utf-8")

    fixed_copy = scripts / "fund_rank_build.py"
    fixed_copy.write_text(original, encoding="utf-8")
    assert find_bootstrap_problems(fixed_copy) == [], "完好版本被误判为有问题（误报）"

    defective_src, dropped = _strip_backend_bootstrap(original)
    assert dropped > 0, "故障注入没删掉任何引导行，用例会假通过"
    defective_copy = scripts / "fund_rank_build_defective.py"
    defective_copy.write_text(defective_src, encoding="utf-8")

    problems = find_bootstrap_problems(defective_copy)
    assert problems, "缺陷版本的 backend/ 引导被删了，静态守卫却没报 FAIL（守卫是死的）"
    assert any("backend/" in p for p in problems), f"报的不是预期的 backend/ 缺失: {problems}"


def test_fault_injection_runtime_detects_missing_backend_bootstrap(tmp_path: Path) -> None:
    """运行层故障注入：缺陷副本必须真的抛 No module named 'infra'，原版必须干净。"""
    _root, _backend, scripts = _build_mirror_tree(tmp_path)
    original = (SCRIPTS_DIR / "fund_rank_build.py").read_text(encoding="utf-8")

    fixed_copy = scripts / "fund_rank_build.py"
    fixed_copy.write_text(original, encoding="utf-8")
    _rc_fixed, out_fixed = _run_probe(fixed_copy, tmp_path / "run_fixed")
    assert _missing_backend_modules(out_fixed) == [], (
        f"完好版本在镜像树里却报了 import 失败，说明镜像树搭错了:\n{out_fixed[-2000:]}"
    )

    defective_src, dropped = _strip_backend_bootstrap(original)
    assert dropped > 0, "故障注入没删掉任何引导行，用例会假通过"
    defective_copy = scripts / "fund_rank_build_defective.py"
    defective_copy.write_text(defective_src, encoding="utf-8")

    _rc_bad, out_bad = _run_probe(defective_copy, tmp_path / "run_defective")
    missing = _missing_backend_modules(out_bad)
    assert "infra" in missing, (
        "缺陷版本没有复现 `No module named 'infra'` —— 运行层守卫抓不到这个缺陷，"
        f"用例是死的。实际 missing={missing}\n{out_bad[-2000:]}"
    )


def test_fault_injection_only_flags_expected_defect(tmp_path: Path) -> None:
    """Pitfall 21：注入只删 backend/ 引导，不应连带污染其他检查（只红预期那几条）。"""
    original = (SCRIPTS_DIR / "fund_rank_build.py").read_text(encoding="utf-8")
    defective_src, dropped = _strip_backend_bootstrap(original)
    assert dropped > 0

    _root, _backend, scripts = _build_mirror_tree(tmp_path)
    defective_copy = scripts / "defective_only.py"
    defective_copy.write_text(defective_src, encoding="utf-8")

    # 1) 仍然是一份语法合法的 Python（删引导不能删出语法错误）
    ast.parse(defective_src, filename=str(defective_copy))

    # 2) 只删了 backend/ 引导，就应该「只红 backend/ 那一条」，不能连带误报仓库根缺失
    problems = find_bootstrap_problems(defective_copy)
    assert problems, "缺陷版本没被判 FAIL，守卫是死的"
    assert all("需要 backend/ 在 sys.path 里" in p for p in problems), (
        f"故障注入只删了 backend/ 引导，却报出了非预期问题: {problems}"
    )
    root_only = [p for p in problems if "需要 仓库根 在 sys.path 里" in p]
    assert not root_only, f"仓库根引导没动，不该被连带判 FAIL: {root_only}"


def test_guards_did_not_idle() -> None:
    """反空转收口：确认运行时探针真的起了子进程，而不是 0 次调用空转通过。"""
    assert len(_RUNTIME_PROBE_CALLS) >= 2, (
        f"运行时探针只被调用了 {len(_RUNTIME_PROBE_CALLS)} 次，疑似空转: {_RUNTIME_PROBE_CALLS}"
    )
    assert len(set(_RUNTIME_PROBE_CALLS)) >= 2, "探针只对同一个脚本跑过，覆盖不足"

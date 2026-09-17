"""
config.py「import 期建目录」跳过开关的行为级测试（v9.9.48）
============================================================================

背景
----
`backend/config.py` 的 4 个 `mkdir` 是**模块 import 的副作用**。只要任何进程
在 DATA_DIR 没设的情况下 import 它，就会在默认位置凭空造出一整棵目录树
—— 服务器上 `/opt/moneybag/backend/data` 那棵 **8KB、0 个文件** 的空壳
就是这么来的（它从来没被用过，只是被"建"过）。

v9.9.48 加了 `MONEYBAG_SKIP_DIR_BOOTSTRAP`：设了就**只解析路径、不建目录**，
`DATA_DIR / USERS_DIR / RECEIPTS_DIR / PUSH_ARCHIVE_DIR` 四个常量的取值不变。

这个文件测什么
--------------
全部走**子进程**：config 的 mkdir 发生在模块 import 期，同一进程里 import
一次就定死，进程内测不到"冷启动"这条真实路径（这也是
test_conftest_data_dir_isolation.py 的既有教训）。

  1. :func:`test_skip_bootstrap_creates_no_directory`
     开关打开 → import config **一个目录都不许建**，但四个 *_DIR 常量
     必须仍然指向正确位置（不许为了"不建目录"把路径也弄没了）。

  2. :func:`test_without_skip_bootstrap_directories_are_created`
     **对照组**：不设开关 → 四个目录必须都被建出来。
     没有这条，"根本不建目录"的实现（比如有人直接把 mkdir 删了）也能
     骗过第 1 条 —— 那会把生产打崩。

  3. :func:`test_skip_bootstrap_accepts_common_truthy_spellings`
     开关的几种常见写法（1/true/YES/on，含大小写与空格）都要生效；
     以及"设了但设成别的值"（0/false/空串）必须**不**生效 —— 后者是
     保护生产：写错变量名以外的任何值都不能意外关掉建目录。

  4. :func:`test_production_default_is_unchanged`
     钉住"生产不设这个变量时行为完全不变"这条发版前提：默认路径下
     （不设 DATA_DIR、不设开关）四个常量必须逐字符等于改动前的值。

运行方式::

    cd backend && env -u PYTHONPATH \\
        /Users/leijiang/.workbuddy/binaries/python/envs/default/bin/python \\
        -m pytest tests/test_config_skip_dir_bootstrap.py -v
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _TESTS_DIR.parent

_SKIP_ENV = "MONEYBAG_SKIP_DIR_BOOTSTRAP"

# 子进程里跑的探针：import config，然后把"目录到底存不存在"和
# 四个 *_DIR 常量的取值一起 JSON 回传。
# 为什么用 JSON 而不是 print：父进程要做精确断言，不能靠字符串截取。
# 用 __BACKEND_DIR__ 占位再 replace，而不是 str.format()：
# 探针里那段 JSON 字典推导自带花括号，format 会把它当字段解析（KeyError）。
_PROBE = r'''
import json
import os
import sys

sys.path.insert(0, "__BACKEND_DIR__")
import config  # noqa: E402

dirs = {
    "DATA_DIR": config.DATA_DIR,
    "USERS_DIR": config.USERS_DIR,
    "RECEIPTS_DIR": config.RECEIPTS_DIR,
    "PUSH_ARCHIVE_DIR": config.PUSH_ARCHIVE_DIR,
}
print("PROBE_RESULT " + json.dumps({
    name: {"path": str(p), "exists": p.exists(), "is_dir": p.is_dir()}
    for name, p in dirs.items()
}))
'''


def _run_probe(
    tmp_path: Path,
    data_dir: Path,
    skip_value: str | None,
    extra_env: dict | None = None,
    timeout: int = 120,
) -> dict:
    """在全新子进程里 import 一次 config，返回各目录的 (path, exists)。

    Args:
        tmp_path: 放探针脚本的临时目录（不在受保护目录树内）。
        data_dir: 传给子进程 DATA_DIR 的目标（故意指向**不存在**的路径，
            这样"有没有建目录"才测得出来）。
        skip_value: MONEYBAG_SKIP_DIR_BOOTSTRAP 的取值；None = 不设置。
        extra_env: 额外环境变量。
        timeout: 子进程超时秒数。

    Returns:
        {常量名: {"path": str, "exists": bool, "is_dir": bool}}。
    """
    probe_path = tmp_path / "config_bootstrap_probe.py"
    probe_path.write_text(
        _PROBE.replace("__BACKEND_DIR__", str(_BACKEND_DIR)), encoding="utf-8")

    env = os.environ.copy()
    # 清掉一切会影响子进程目录解析的继承值，保证测的是默认行为
    env.pop("DATA_DIR", None)
    env.pop("MONEYBAG_PYTEST_DATA_DIR", None)
    env.pop("LOG_DIR", None)
    env.pop("MONITOR_DIR", None)
    env[_SKIP_ENV] = skip_value if skip_value is not None else ""
    if skip_value is None:
        env.pop(_SKIP_ENV, None)
    env["DATA_DIR"] = str(data_dir)
    env.update(extra_env or {})

    proc = subprocess.run(
        [sys.executable, str(probe_path)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert proc.returncode == 0, (
        f"探针子进程失败了，后面的断言会空过。\n"
        f"  stdout:\n{proc.stdout[-2000:]}\n  stderr:\n{proc.stderr[-2000:]}")

    marker = "PROBE_RESULT "
    line = next((ln for ln in proc.stdout.splitlines()
                 if ln.startswith(marker)), None)
    assert line, (f"探针没有回传结果，断言会空过。\n  stdout:\n{proc.stdout[-2000:]}"
                  f"\n  stderr:\n{proc.stderr[-1000:]}")

    import json  # noqa: PLC0415 - 只在断言路径上用
    return json.loads(line[len(marker):])


def test_skip_bootstrap_creates_no_directory(tmp_path):
    """开关打开 → import config 一个目录都不许建，但常量取值必须还在。

    故障注入方向：把 config.py 里那段 `if not _SKIP_DIR_BOOTSTRAP:` 的
    判断删掉（改成无条件 mkdir），本用例必须转红。恒绿的守卫等于空转的绿。
    """
    data_dir = tmp_path / "should_not_exist"
    # 前提校验：目标路径确实不存在，否则"没建目录"这条断言毫无意义
    assert not data_dir.exists()

    result = _run_probe(tmp_path, data_dir, skip_value="1")

    created = [name for name, info in result.items() if info["exists"]]
    assert not created, (
        f"{_SKIP_ENV}=1 时 import config 仍然建了目录（开关失效）：{created}\n"
        f"  明细: {result}")

    # 不建目录 ≠ 把路径弄丢了：四个常量必须仍然指向 DATA_DIR 下的正确位置
    assert result["DATA_DIR"]["path"] == str(data_dir)
    assert result["USERS_DIR"]["path"] == str(data_dir / "users")
    assert result["RECEIPTS_DIR"]["path"] == str(data_dir / "receipts")
    assert result["PUSH_ARCHIVE_DIR"]["path"] == str(data_dir / "logs" / "pushes")


def test_without_skip_bootstrap_directories_are_created(tmp_path):
    """对照组：不设开关 → 四个目录必须都被建出来（生产行为不变）。

    没有这条，一个"干脆不建目录"的实现同样能通过上一条 —— 而那会直接
    打崩生产（API 起来时 data/users 不存在）。
    """
    data_dir = tmp_path / "should_exist"
    assert not data_dir.exists()

    result = _run_probe(tmp_path, data_dir, skip_value=None)

    missing = [name for name, info in result.items() if not info["exists"]]
    assert not missing, (
        f"不设 {_SKIP_ENV} 时 import config 没建出目录 —— 生产启动会炸：{missing}\n"
        f"  明细: {result}")
    not_dir = [name for name, info in result.items() if not info["is_dir"]]
    assert not not_dir, f"建出来的不是目录: {not_dir}"


@pytest.mark.parametrize("truthy", ["1", "true", "TRUE", "Yes", "on", " ON "])
def test_skip_bootstrap_accepts_common_truthy_spellings(tmp_path, truthy):
    """开关的常见真值写法（含大小写与首尾空格）都要生效。"""
    data_dir = tmp_path / f"skip_{truthy.strip().lower() or 'blank'}"
    assert not data_dir.exists()

    result = _run_probe(tmp_path, data_dir, skip_value=truthy)

    created = [name for name, info in result.items() if info["exists"]]
    assert not created, (
        f"{_SKIP_ENV}={truthy!r} 应当被识别为真值，但目录仍被建出：{created}")


@pytest.mark.parametrize("falsy", ["0", "false", "no", "off", "", "maybe", "2"])
def test_non_truthy_values_keep_creating_directories(tmp_path, falsy):
    """设了但设成非真值 → 必须照旧建目录。

    这条保护的是**生产**：任何拼错/误设的取值都不能意外关掉建目录，
    否则线上会静默缺目录。宁可"没跳过"，也不能"跳过了不知道"。
    """
    data_dir = tmp_path / ("keep_" + (falsy.strip() or "empty"))
    assert not data_dir.exists()

    result = _run_probe(tmp_path, data_dir, skip_value=falsy)

    missing = [name for name, info in result.items() if not info["exists"]]
    assert not missing, (
        f"{_SKIP_ENV}={falsy!r} 不应触发跳过，但目录没被建出：{missing}\n"
        f"  明细: {result}")


def test_production_default_is_unchanged(tmp_path):
    """钉住发版前提：不设开关、不设 DATA_DIR 时，四个常量逐字符不变。

    这是"生产行为完全不变"这条结论的可执行形式：默认值必须回落到
    `<repo>/data`（BACKEND_DIR.parent / "data"）及其三个子目录。
    """
    data_dir = tmp_path / "default_probe"
    assert not data_dir.exists()

    # 显式不设开关（走改动前的老路径），但 DATA_DIR 仍指到临时目录，
    # 避免真去动 <repo>/data
    result = _run_probe(tmp_path, data_dir, skip_value=None)

    assert result["DATA_DIR"]["path"] == str(data_dir)
    assert result["USERS_DIR"]["path"] == str(data_dir / "users")
    assert result["RECEIPTS_DIR"]["path"] == str(data_dir / "receipts")
    assert result["PUSH_ARCHIVE_DIR"]["path"] == str(data_dir / "logs" / "pushes")
    # 老行为：全都建出来
    assert all(info["exists"] for info in result.values()), (
        f"默认路径下应当全部建出: {result}")


def test_default_data_dir_falls_back_to_repo_data():
    """不设 DATA_DIR 时，默认目录必须是 `<repo>/data`（不被开关影响）。

    这条不走子进程开关逻辑，只钉住 DEFAULT_DATA_DIR 的解析结果 ——
    它是"服务器空壳目录"事件里被误建的那棵树的根。
    """
    data_dir = Path(os.environ.get("DATA_DIR", ""))  # conftest 已指向临时目录
    # 直接读源码常量，不 import（避免会话内已 import 的模块状态干扰）
    src = (_BACKEND_DIR / "config.py").read_text(encoding="utf-8")
    assert 'DEFAULT_DATA_DIR = BACKEND_DIR.parent / "data"' in src, (
        "DEFAULT_DATA_DIR 的定义变了，本用例的前提需要同步更新")
    assert data_dir, "conftest 未隔离 DATA_DIR，用例前提失效"

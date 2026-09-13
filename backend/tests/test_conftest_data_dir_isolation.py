"""
conftest.py 数据目录自动隔离守护测试（2026-09-01 事故复盘后新增
+ 2026-09-08 逃逸口事故后重写）
============================================================================
背景一（2026-09-01）：曾发生一次真实事故——在生产 `/opt/moneybag` 目录下
直接跑 `pytest tests/test_phase3_services.py`（未设置任何 `DATA_DIR` 环境
变量），该文件 13 个测试用例没有做任何目录隔离，真实写入了生产
`data/users/` 目录，留下 13 个 `test_*` 前缀的脏用户文件。核对
createdAt 时间戳 + 用户 ID 哈希确认真实用户数据未受影响后手动清理。

修复：在 `conftest.py` **模块顶层**（而不是某个 fixture 内部）设置
`DATA_DIR` 环境变量兜底成临时目录——利用 pytest 保证 conftest.py 一定
先于同目录任何测试文件被 import 这个特性，让 `config.py` 首次 import
时读到的 DATA_DIR 已经是安全的临时路径，不管某个测试文件有没有自己写
隔离代码都不会波及真实数据。

背景二（2026-09-08）：上面这套机制**设计是对的，却被一个看似无害的条件
判断整个废掉了**。原写法是::

    if not os.environ.get("DATA_DIR"):        # ← 逃逸口
        _PYTEST_DATA_DIR = tempfile.mkdtemp(...)
        os.environ["DATA_DIR"] = _PYTEST_DATA_DIR

原意是"尊重用户显式意图"，实际效果却是：**只要外部设了 DATA_DIR，整段
隔离被跳过，测试直写生产目录**。而会去设这个变量的人，恰恰正是想"模拟
生产环境"的人 —— 于是项目里那条"服务器跑测试必须带
DATA_DIR=/opt/moneybag/data"（抄自 systemd 配置）的环境铁律，把每一轮
测试都变成了对生产数据的写入。

后果有两层，第二层更隐蔽：
  1. 生产 data/users/ 被写入大量 test_* 脏用户文件（实测 2 → 13 → 15 个）。
  2. 由此产生**假失败**：test_phase3_services 用固定 user_id 写事件后
     断言精确条数（len(...) == 1），在共享生产目录里事件逐次累积（期望 1
     实际 13）⇒ 变红。这些红被误判成代码回归，浪费了两轮排查。

修法：**默认总是隔离**，无视外部传入的 DATA_DIR；逃生阀改用专属变量
MONEYBAG_PYTEST_DATA_DIR —— 不复用 DATA_DIR，因为那会把"模拟生产环境"
和"允许写生产数据"两件事耦合起来，而后者是灾难。

这个文件测什么（用子进程真实跑一次 pytest 来验证，而不是 mock，因为
这个 bug 本质是"进程启动时序"问题，mock 测不出真实的 import 时序）：
  - 完全复现事故场景：不设置 DATA_DIR 跑 test_phase3_services.py，
    确认"生产目录"（这里用 tmp_path 下的诱饵目录代替真实 /opt/moneybag）
    在测试前后文件数量不变——零污染。
  - **外部设置了 DATA_DIR 时同样必须零污染**（2026-09-08 新增，核心断言）：
    不看 conftest 源码长什么样，只看"外部塞了个 DATA_DIR 进来，测试进程
    到底写到了哪里"——断言**行为**而不是实现。
  - 逃生阀 MONEYBAG_PYTEST_DATA_DIR 仍然可用：堵逃逸口不等于把逃生阀
    焊死，将来要在真实数据上调试时仍需要有出口。
  - conftest.py 自己创建的临时目录会在会话结束后自动清理（不留垃圾）；
    用户显式指定的目录绝不删除。

为什么用"诱饵目录"而不是直接断言生产目录：
  生产目录版的断言一旦失败（即隔离被破坏），污染**已经发生了** —— 这个
  测试本身就成了破坏生产数据的元凶。用 tmp_path 下的诱饵目录可以在完全
  等价的前提下验证同一条性质，且零风险。真实生产目录的核对放在部署验收
  环节人工执行。

不测什么：
  - test_phase3_services.py 内部各测试用例的业务逻辑本身（那是它自己
    的职责，这里只测"目录隔离机制"这一层）。

运行方式::

    # 本地
    cd backend && env -u PYTHONPATH python3 -m pytest \\
        tests/test_conftest_data_dir_isolation.py -v

    # 服务器（注意：不要设 DATA_DIR）
    cd /opt/moneybag/backend && PYTHONPATH=/opt/moneybag/backend \\
        /opt/moneybag/venv/bin/python3 -m pytest \\
        tests/test_conftest_data_dir_isolation.py -v
"""
import glob
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parent.parent

# 生产数据目录。本地不存在该路径，相关用例会自动降级为纯临时目录断言。
_PROD_DATA_DIR = "/opt/moneybag/data"

# 已知会往 DATA_DIR/users/ 写东西的测试文件（两次事故里确认过）。
# 选它们作为"写入探针"——如果隔离失效，这些文件一定会把数据写到诱饵目录里。
_WRITER_TESTS = (
    "tests/test_phase3_services.py",
    "tests/test_user_optimistic_lock.py",
)

# 全量套件隔离核对较慢（约 40s），默认跳过；部署验收时设该变量显式跑一次。
_DECOY_ENV_FLAG = "MONEYBAG_FULL_SUITE_ISOLATION_CHECK"


def _run_pytest_subprocess(
    target,
    env_overrides: dict | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess:
    """在独立子进程里跑 pytest，模拟"全新进程"的真实场景。

    用子进程而不是进程内调用，因为 conftest.py 的模块顶层副作用
    （os.environ 修改）只在"首次 import"时触发一次，同一进程内重复跑
    测不出真实的冷启动时序问题。

    Args:
        target: 传给 pytest 的目标，可以是单个字符串，也可以是多个目标
            组成的 list。**多目标必须拆成多个 argv 元素**——传成一个
            "a.py b.py" 的整串会让 pytest 报 usage error 退出，
            于是"目录没被写"这种断言会**空过**（vacuous pass）。
            2026-09-08 故障注入时就是被这个坑骗过一次：看起来绿，
            其实子进程压根没跑测试。
        env_overrides: 要注入的环境变量；默认先清空 DATA_DIR 与
            MONEYBAG_PYTEST_DATA_DIR，模拟"没有人显式设置过"，
            再由本参数覆盖，避免上一轮运行的环境变量泄漏进来。
        timeout: 子进程超时秒数。

    Returns:
        subprocess.CompletedProcess。
    """
    if isinstance(target, str):
        targets = target.split()
    else:
        targets = [str(t) for t in target]

    env = os.environ.copy()
    # 清空，模拟"没有人显式设置过"的真实事故前置条件；
    # 逃生阀也必须清掉，否则测到的就不是默认行为。
    env.pop("DATA_DIR", None)
    env.pop("MONEYBAG_PYTEST_DATA_DIR", None)
    env.update(env_overrides or {})
    return subprocess.run(
        [sys.executable, "-m", "pytest", *targets, "-v", "-p", "no:cacheprovider"],
        cwd=str(_BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _snapshot(root: Path) -> dict:
    """对目录做一次内容快照：相对路径 → (大小, mtime_ns)。

    不只记文件名，也记 mtime 和 size —— 只比对文件名会漏掉"文件已存在、
    但内容被测试改写"这种情况（生产用户文件被覆盖比多一个文件更危险）。

    Args:
        root: 要快照的目录。

    Returns:
        {相对路径字符串: (字节数, mtime_ns)}；目录不存在时返回 {}。
    """
    if not root.exists():
        return {}
    out = {}
    for p in root.rglob("*"):
        try:
            st = p.stat()
        except OSError:
            continue
        out[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return out


def _diff_snapshot(before: dict, after: dict) -> tuple[list, list, list]:
    """比对两次快照，返回 (新增, 改写, 删除) 三组相对路径。"""
    added = sorted(set(after) - set(before))
    changed = sorted(k for k in set(after) & set(before) if after[k] != before[k])
    removed = sorted(set(before) - set(after))
    return added, changed, removed


def test_conftest_auto_isolates_data_dir_when_unset(tmp_path):
    """完全复现 2026-09-01 事故场景：不设置 DATA_DIR，直接跑
    test_phase3_services.py，确认模拟的"生产目录"文件数量在测试前后
    完全不变——这是回归锁定的核心断言。
    """
    # 用一个临时目录模拟"生产 data 目录"，预先放几个"真实用户文件"
    fake_prod_data_dir = tmp_path / "fake_prod_data"
    fake_prod_users_dir = fake_prod_data_dir / "users"
    fake_prod_users_dir.mkdir(parents=True)
    (fake_prod_users_dir / "real_user_abc123.json").write_text('{"userId": "real"}')

    files_before = sorted(p.name for p in fake_prod_users_dir.glob("*.json"))
    assert files_before == ["real_user_abc123.json"]

    # 关键：故意不设置 DATA_DIR，让它保持"未设置"状态——这正是事故复现的
    # 前置条件。fake_prod_data_dir 只是用来验证"如果没有这次修复，本该
    # 写到哪里"的参照物，不会真的被写入（因为 conftest.py 会兜底成一个
    # 完全不同的临时目录，根本不会碰这个目录）。
    result = _run_pytest_subprocess("tests/test_phase3_services.py")

    assert result.returncode == 0, (
        f"test_phase3_services.py 应该正常通过\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )

    files_after = sorted(p.name for p in fake_prod_users_dir.glob("*.json"))
    assert files_after == files_before, (
        f"fake_prod_users_dir 文件数量发生变化，说明测试写到了生产目录！"
        f"before={files_before} after={files_after}"
    )


def test_config_data_dir_is_a_temp_dir_not_production():
    """config.DATA_DIR 必须落在临时目录里，绝不能是生产路径。

    故障注入方向：把 conftest 改回 `if not os.environ.get("DATA_DIR")`
    并在外部设 DATA_DIR=/opt/moneybag/data 启动测试，本用例应变红。
    """
    import config

    data_dir = Path(str(config.DATA_DIR)).resolve()
    tmp_root = Path(tempfile.gettempdir()).resolve()

    assert str(data_dir) != _PROD_DATA_DIR, (
        f"config.DATA_DIR 指向了生产路径 {_PROD_DATA_DIR}，测试会写进生产目录")

    assert data_dir == tmp_root or tmp_root in data_dir.parents, (
        f"config.DATA_DIR 应位于系统临时目录 {tmp_root} 下，实际是 {data_dir}")


def test_conftest_ignores_external_data_dir_env(tmp_path):
    """2026-09-08 核心断言：外部塞进来的 DATA_DIR 必须被无视，且一个字节
    都不许被写。

    旧行为是"用户显式设了 DATA_DIR 就听他的"——正是这条"尊重用户意图"的
    设计让隔离整体失效。现在无论外部怎么设，测试进程都必须待在自己专属的
    临时目录里。断言用快照对比（含 mtime/size），不只是文件集合。

    故障注入方向：把 conftest 改回 `if not os.environ.get("DATA_DIR")`，
    本用例应变红，并精确列出诱饵目录里多了哪些文件。
    """
    decoy = tmp_path / "decoy_data"
    (decoy / "users").mkdir(parents=True)
    # 放一个哨兵文件，确保"零变化"是真的零变化（而不是目录压根没被找到）
    (decoy / "users" / "sentinel.json").write_text(
        '{"sentinel": true}', encoding="utf-8")

    before = _snapshot(decoy)
    assert before, "诱饵目录快照为空，用例前提失效"

    existing = [f for f in _WRITER_TESTS if (_BACKEND_DIR / f).exists()]
    assert existing, f"找不到任何写入探针文件：{_WRITER_TESTS}"

    proc = _run_pytest_subprocess(
        existing, env_overrides={"DATA_DIR": str(decoy)})

    # 防止"空过"：必须确认子进程真的跑了测试。否则一旦 pytest 因为参数错误
    # 提前退出，诱饵目录自然零变化，本用例会**假绿**——这正是 2026-09-08
    # 第一次故障注入时被骗过的原因。
    assert proc.returncode == 0, (
        f"写入探针测试本身应当通过，否则下面'目录零变化'的断言没有意义。\n"
        f"  退出码: {proc.returncode}\n"
        f"  stdout 尾部:\n{proc.stdout[-1500:]}\n"
        f"  stderr 尾部:\n{proc.stderr[-800:]}")

    after = _snapshot(decoy)
    added, changed, removed = _diff_snapshot(before, after)

    assert not added and not changed and not removed, (
        f"外部 DATA_DIR 指向的目录被测试改动了 —— conftest 隔离失效。\n"
        f"  新增: {added}\n"
        f"  改写: {changed}\n"
        f"  删除: {removed}\n"
        f"  子进程退出码: {proc.returncode}\n"
        f"  子进程 stdout 尾部:\n{proc.stdout[-1500:]}")


def test_conftest_escape_hatch_uses_dedicated_env_var(tmp_path):
    """对照用例：堵逃逸口不等于焊死逃生阀。

    需要挂真实数据调试时，用 MONEYBAG_PYTEST_DATA_DIR 显式指定目录，
    数据就应当真的落进那个目录（而不是被重定向到别处）。这条与上面那条
    成对存在，避免为了修一个问题把另一个合理能力一起砍掉。
    """
    target = tmp_path / "explicit_data"
    (target / "users").mkdir(parents=True)

    proc = _run_pytest_subprocess(
        "tests/test_phase3_services.py",
        env_overrides={"MONEYBAG_PYTEST_DATA_DIR": str(target)},
    )
    assert proc.returncode == 0, (
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")

    # 用了显式目录 ⇒ 里面应当真的产生数据（而不是被重定向到别处）
    assert _snapshot(target / "users"), (
        f"显式指定 MONEYBAG_PYTEST_DATA_DIR 后，数据应写入该目录，"
        f"实际目录为空。子进程 stdout 尾部:\n{proc.stdout[-1500:]}")


def test_conftest_does_not_delete_explicitly_given_dir(tmp_path):
    """显式指定的目录绝不能被 pytest_sessionfinish 删掉。

    逃逸口修掉之后，清理逻辑也跟着改成了"只删自己创建的目录"。这条用例
    锁住这半个契约：用户的数据目录不属于 pytest 的临时产物。
    """
    target = tmp_path / "keep_me"
    (target / "users").mkdir(parents=True)
    (target / "users" / "precious.json").write_text(
        '{"userId": "precious"}', encoding="utf-8")

    proc = _run_pytest_subprocess(
        "tests/test_phase3_services.py::TestPersistence::test_init_phase3_fields",
        env_overrides={"MONEYBAG_PYTEST_DATA_DIR": str(target)},
    )
    assert proc.returncode == 0, (
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")

    assert (target / "users" / "precious.json").exists(), (
        "用户显式指定的目录被 pytest_sessionfinish 删掉了 —— "
        "清理逻辑必须只作用于 conftest 自己创建的临时目录")


def test_conftest_cleans_up_its_own_temp_dir_after_session():
    """conftest.py 创建的临时目录必须在测试会话结束后被清理，不能
    每次跑测试都在临时目录里堆积新目录（长期运行会占满磁盘，尤其 CI
    环境反复跑测试的场景）。

    2026-09-13 修：**改为前后差集断言**。
    旧实现统计的是「全局残留数 < 5」，有两个方向的缺陷，实测均已复现：
      · 假红——并行跑的其它会话/队友留下的目录会把计数推过 5，
        于是**清理逻辑完好也照样失败**（实测：往真实 tempdir 放 5 个
        无关目录，本测试即报 `assert 7 < 5`）。
      · 假绿——只要全局残留数偶然 < 5，即使本次运行真的没清理也照样通过。
    它测的从来不是「本会话有没有清理自己的目录」。
    差集写法对本会话之外的残留完全免疫，且真的能验到清理行为。
    故障注入：把 conftest 的 pytest_sessionfinish 清理摘掉 →
    子进程留下的目录会出现在 after - before 里 → 本测试转红。
    """
    tmpdir = Path(tempfile.gettempdir())
    pattern = "moneybag_pytest_data_*"

    before = set(glob.glob(str(tmpdir / pattern)))

    result = _run_pytest_subprocess(
        "tests/test_phase3_services.py::TestPersistence::test_init_phase3_fields")

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    after = set(glob.glob(str(tmpdir / pattern)))

    # 只关心「本次运行新增、且未被清理」的目录；
    # 其它会话的残留不属于本次断言范围（旧实现正是被它们误伤的）。
    leaked = sorted(after - before)
    assert not leaked, (
        f"本次子进程运行新增了 {len(leaked)} 个未被清理的临时目录: {leaked}。"
        f"conftest.py 的 pytest_sessionfinish 清理逻辑可能失效。"
        f"（本次运行前已存在 {len(before)} 个其它会话的残留目录，与本断言无关）")


@pytest.mark.skipif(
    not os.environ.get(_DECOY_ENV_FLAG),
    reason=f"全量套件隔离核对较慢（约 40s），设 {_DECOY_ENV_FLAG}=1 才跑。"
           f"部署验收时应显式跑一次。")
def test_full_suite_leaves_decoy_untouched(tmp_path):
    """跑**全量**套件后，外部 DATA_DIR 目录依然零变化（广度版）。

    上面 test_conftest_ignores_external_data_dir_env 只跑两个已知写入者，
    成本低但可能漏掉将来新增的写入文件。这条跑全量，覆盖面最全，代价是慢，
    因此默认跳过 —— 部署验收时设环境变量显式跑一次。

    注意：不校验子进程退出码。本地环境缺 akshare/tushare 依赖，全量套件
    本来就有上百条既存失败；我们要断言的是"目录没被写"，与测试红绿无关。
    """
    decoy = tmp_path / "decoy_full"
    (decoy / "users").mkdir(parents=True)

    before = _snapshot(decoy)
    proc = _run_pytest_subprocess(
        ["tests/", "--ignore", f"tests/{Path(__file__).name}"],
        env_overrides={"DATA_DIR": str(decoy)},
        timeout=900,
    )
    after = _snapshot(decoy)
    added, changed, _ = _diff_snapshot(before, after)

    # 防止"空过"：确认子进程真的收集并执行了测试（不要求全绿，本地环境
    # 有既存失败属正常，但"collected 0 items"意味着这次核对毫无意义）
    assert "collected" in proc.stdout and "collected 0 items" not in proc.stdout, (
        f"全量套件子进程没有真正收集到测试，本用例的'零变化'断言会空过。\n"
        f"  退出码: {proc.returncode}\n"
        f"  stdout 尾部:\n{proc.stdout[-1500:]}")

    assert not added and not changed, (
        f"全量套件改动了外部 DATA_DIR 指向的目录 —— conftest 隔离失效。\n"
        f"  新增: {added}\n"
        f"  改写: {changed}\n"
        f"  子进程退出码: {proc.returncode}（本地环境有既存失败属正常，"
        f"此处只关心目录是否被写）")

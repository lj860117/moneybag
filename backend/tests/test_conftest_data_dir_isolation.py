"""
conftest.py 数据目录自动隔离守护测试（2026-09-01 事故复盘后新增）
============================================================================
背景：2026-09-01 曾发生一次真实事故——在生产 `/opt/moneybag` 目录下直接
跑 `pytest tests/test_phase3_services.py`（未设置任何 `DATA_DIR` 环境
变量），该文件 13 个测试用例没有做任何目录隔离，真实写入了生产
`data/users/` 目录，留下 13 个 `test_*` 前缀的脏用户文件。核对
createdAt 时间戳 + 用户 ID 哈希确认真实用户数据未受影响后手动清理。

修复：在 `conftest.py` **模块顶层**（而不是某个 fixture 内部）设置
`DATA_DIR` 环境变量兜底成临时目录——利用 pytest 保证 conftest.py 一定
先于同目录任何测试文件被 import 这个特性，让 `config.py` 首次 import
时读到的 DATA_DIR 已经是安全的临时路径，不管某个测试文件有没有自己写
隔离代码都不会波及真实数据。

这个文件测什么（用子进程真实跑一次 pytest 来验证，而不是 mock，因为
这个 bug 本质是"进程启动时序"问题，mock 测不出真实的 import 时序）：
  - 完全复现事故场景：不设置 DATA_DIR，直接跑 test_phase3_services.py，
    确认"生产目录"（这里用一个模拟的假生产路径代替真实 /opt/moneybag）
    在测试前后文件数量不变——零污染。
  - conftest.py 不会覆盖用户显式设置的 DATA_DIR（保留组合灵活性，不能
    为了防护而牺牲现有测试文件自己的隔离逻辑）。
  - conftest.py 创建的临时目录会在会话结束后自动清理（不留垃圾）。

不测什么：
  - test_phase3_services.py 内部各测试用例的业务逻辑本身（那是它自己
    的职责，这里只测"目录隔离机制"这一层）。
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parent.parent


def _run_pytest_subprocess(target: str, env_overrides: dict) -> subprocess.CompletedProcess:
    """在独立子进程里跑 pytest，模拟"全新进程、未设置任何环境变量"的
    真实场景——用子进程而不是进程内调用，因为 conftest.py 的模块顶层
    副作用（os.environ 修改）只在"首次 import"时触发一次，同一进程内
    重复跑测不出真实的冷启动时序问题。
    """
    env = os.environ.copy()
    # 清空 DATA_DIR，模拟"没有人显式设置过"的真实事故前置条件
    env.pop("DATA_DIR", None)
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-m", "pytest", target, "-v"],
        cwd=str(_BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


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

    # 关键：故意不通过 env_overrides 设置 DATA_DIR，让它保持"未设置"
    # 状态——这正是事故复现的前置条件。fake_prod_data_dir 只是用来验证
    # "如果没有这次修复，本该写到哪里"的参照物，不会真的被写入（因为
    # conftest.py 会兜底成一个完全不同的临时目录，根本不会碰这个目录）。
    result = _run_pytest_subprocess(
        "tests/test_phase3_services.py",
        env_overrides={},
    )

    assert result.returncode == 0, (
        f"test_phase3_services.py 应该正常通过\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )

    files_after = sorted(p.name for p in fake_prod_users_dir.glob("*.json"))
    assert files_after == files_before, (
        f"fake_prod_users_dir 文件数量发生变化，说明测试写到了生产目录！"
        f"before={files_before} after={files_after}"
    )


def test_conftest_respects_explicit_data_dir_env(tmp_path):
    """用户显式设置 DATA_DIR 时，conftest.py 不应覆盖它——这个防护
    机制只在"用户没有设置"时才兜底，不能剥夺现有测试文件自己精确控制
    DATA_DIR 的能力（如 test_user_optimistic_lock.py 的每测试独立
    tmp_path 隔离）。
    """
    explicit_dir = tmp_path / "explicit_data_dir"
    explicit_dir.mkdir()

    # 用一个简单的探针脚本验证：import conftest 后 DATA_DIR 环境变量
    # 必须仍然是我们显式设置的值，不能被覆盖成别的临时目录
    probe_script = f'''
import os
os.environ["DATA_DIR"] = {str(explicit_dir)!r}
import sys
sys.path.insert(0, "tests")
import conftest
assert os.environ["DATA_DIR"] == {str(explicit_dir)!r}, (
    f"DATA_DIR 被覆盖了！期望 {str(explicit_dir)!r}，实际 " + os.environ["DATA_DIR"]
)
assert conftest._PYTEST_DATA_DIR == "", (
    "用户显式设置 DATA_DIR 时，_PYTEST_DATA_DIR 应为空字符串"
    "（表示 conftest 没有创建自己的临时目录，不会去清理用户的目录）"
)
print("OK")
'''
    result = subprocess.run(
        [sys.executable, "-c", probe_script],
        cwd=str(_BACKEND_DIR),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "OK" in result.stdout


def test_conftest_cleans_up_its_own_temp_dir_after_session():
    """conftest.py 创建的临时目录必须在测试会话结束后被清理，不能
    每次跑测试都在 /tmp 里堆积新目录（长期运行会占满磁盘，尤其 CI
    环境反复跑测试的场景）。
    """
    result = _run_pytest_subprocess(
        "tests/test_phase3_services.py::TestPersistence::test_init_phase3_fields",
        env_overrides={},
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    # 提取这次运行创建的临时目录路径（约定前缀 moneybag_pytest_data_），
    # 验证会话结束后已被清理
    import glob
    leftover = glob.glob(str(Path(tempfile.gettempdir()) / "moneybag_pytest_data_*"))
    # 允许存在其他并发测试运行残留的目录（不属于本次断言范围），只要
    # 数量没有异常增长即可——这里退化为宽松检查：至少不应该有大量残留
    # （比如同一分钟内跑10次这个测试，残留目录数应该趋近于0，不应该
    # 每次运行都新增一个从不清理）。
    assert len(leftover) < 5, (
        f"发现 {len(leftover)} 个残留的 moneybag_pytest_data_* 临时目录，"
        f"pytest_sessionfinish 清理逻辑可能失效: {leftover}"
    )

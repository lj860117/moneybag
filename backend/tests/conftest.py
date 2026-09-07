"""
backend/tests/ 共享 pytest 配置

背景（FIX 2026-09-01）：`scripts/cache_warmer.py` 模块级代码会在 import
时手动解析 `.env` 并用 `os.environ.setdefault(k, v)` 写入真实密钥——这是
生产环境的正常兜底行为（有些 crontab 条目不走 `set -a && . .env`，脚本
自己兜底加载一次），不应该改。

但这段代码是**模块级副作用**：只要任何测试 `import scripts.cache_warmer`
（哪怕只是为了测别的函数），Python 的模块缓存机制就会让这次 import 只执行
一次，然后**永久污染当前 pytest 进程的 os.environ**——不是通过
`monkeypatch.setenv` 设置的，`monkeypatch` 的自动回滚机制根本管不到它。

真实故障链：本地开发没有 `.env` 文件（在 .gitignore 里）测不出这个问题，
只有服务器上真实 `.env` 存在时才会触发；`test_save_cache_can_replace_
readonly_existing_file` import 了 cache_warmer 后，`test_health_does_not_
flag_missing_cfo_cache_as_degraded` 断言"deepseek key 应为 missing"就会
失败——因为它读到的是这次污染写进去的真实 LLM_API_KEY，不是测试自己设置
的值。单独跑这个测试永远通过（污染源没被 import 过），混在全量套件里跑
才会暴露——这正是为什么必须在服务器隔离环境跑全量套件核对，本地单测
"看起来通过"是不可信的。

修法：在每个测试函数运行前，把 .env 里出现的全部密钥类环境变量强制清空
（autouse fixture，无需每个测试文件单独引用）。选择"清空"而不是"记录
原值再还原"，是因为测试进程本来就不应该依赖真实密钥——如果某个测试确实
需要模拟"已配置"的状态，应该用 monkeypatch.setenv 显式设置，而不是依赖
残留的真实值。

背景（FIX 2026-09-01 第二次，任务：防止测试写脏生产数据）：
`config.py` 的 `DATA_DIR`/`USERS_DIR` 是**进程启动/首次 import 时一次性
解析的模块级常量**（读一次 `os.environ.get("DATA_DIR", ...)` 就定死，
之后同进程内谁都改不了它，只能整体 reload 模块）。此前的隔离手段全部
依赖"每个测试文件自己记得在 import 被测模块之前，用 monkeypatch.setenv
+ importlib.reload 把 DATA_DIR 指到 tmp_path"——`test_user_optimistic_
lock.py`/`test_process_watchdog.py`/`test_fund_detail_ak_timeout.py`
等文件确实这么做了，但这是"人肉纪律"，不是机制强制。

真实事故：`test_phase3_services.py` 顶层直接 `from services.persistence
import load_user, save_user`，没有做任何隔离——如果这次 import 发生在
`DATA_DIR` 环境变量还指向真实生产路径的时刻（例如直接在 /opt/moneybag
生产目录下跑 `pytest tests/`，没有提前设置 `DATA_DIR` 环境变量），
`config.py` 首次 import 时就会把 `USERS_DIR` 锁定成生产路径
`/opt/moneybag/data/users`，该文件 13 个测试用例随后全部真实写入这个
目录，留下 13 个 `test_*` 前缀的脏用户文件。2026-09-01 已发生一次，
核对 createdAt 时间戳 + 用户 ID 哈希确认真实用户数据未受影响后手动清理。

修法：**利用 pytest 保证 conftest.py 模块顶层代码在同目录任何测试文件
被 import 之前执行**这个特性——在本文件模块顶层（不是某个 fixture 内部，
必须在 collection 阶段、第一个测试文件 import 之前就生效）把 `DATA_DIR`
环境变量强制指向一个 pytest 进程专属的临时目录。这样即使未来新增测试
文件、或者现有测试文件忘记做 inline 隔离，`config.py` 首次 import 时
拿到的也必然是临时目录，物理上不可能写到生产路径——**从"记得写隔离代码"
升级为"写不写都安全"**。

不影响现有测试的两种既有写法：
  1. 已经手动 monkeypatch.setenv("DATA_DIR", tmp_path) + reload 的文件
     （如 test_user_optimistic_lock.py）：它们的 tmp_path 会覆盖这里
     设置的临时目录，行为不变，只是更保险（双重兜底）。
  2. 从未做任何隔离、直接用模块级单例的文件（如 test_phase3_services.py
     修复前的状态）：现在会自动落在这里设置的临时目录里，不再是死链。

背景（FIX 2026-09-08 第三次，任务：堵住隔离机制的逃逸口）：
上面这套隔离机制**设计是对的，但被一个看似无害的条件判断整个废掉了**。
原写法是::

    if not os.environ.get("DATA_DIR"):        # ← 逃逸口
        _PYTEST_DATA_DIR = tempfile.mkdtemp(...)
        os.environ["DATA_DIR"] = _PYTEST_DATA_DIR

原意是"尊重用户显式意图"：用户自己设了 DATA_DIR 就听他的。问题是**会去
设这个变量的人，恰恰正是想模拟生产环境的人** —— 而"模拟生产环境"和
"允许写生产数据"是两件完全不同的事，前者是合理需求，后者是灾难。

真实事故链（2026-09-07/08）：
  1. 项目环境铁律要求"服务器跑测试必须带 DATA_DIR=/opt/moneybag/data"
     （抄自 systemd 的 Environment=，本意是让 config.py 找到真实数据目录）。
  2. 这个变量一进来，上面的 if 判断为假，**整段隔离被跳过**，测试进程
     直接把 DATA_DIR 锁死成生产路径 /opt/moneybag/data。
  3. 于是测试真实写入生产 data/users/：跑几轮就攒出十几个 test_* 前缀的
     脏用户文件（实测从 2 个涨到 13 个，两轮后又涨到 15 个）。
  4. 更糟的是由此产生了一批**假失败**：test_phase3_services 用固定
     user_id 写事件后断言精确条数（len(...) == 1），在共享生产目录里事件
     逐次累积（期望 1 实际 13），于是变红；test_fund_risk_adjusted_cache
     同理。这些红被误判成代码回归，浪费了两轮排查，还差点引出对
     classify_fund 的错误怀疑。

实测对照（2026-09-08）：
    带 DATA_DIR=/opt/moneybag/data 跑 test_phase3_services  → 2 failed, 13 passed
    不带 DATA_DIR 跑同一个文件                                → 15 passed
    不带 DATA_DIR 跑全量                                      → 685 passed, 0 failed

修法：改成**默认总是隔离** —— pytest 会话内永远把 DATA_DIR 指向会话专属
临时目录，**无视外部传入的 DATA_DIR**。需要挂真实数据调试时，改用专属变量
MONEYBAG_PYTEST_DATA_DIR（不复用 DATA_DIR，理由见下方代码注释）。

教训（与 2026-09-07 的 D2 是同一类形态）：
  - D2：`MemoryCache.get()` 一个"看起来只是读"的操作，有删除条目的副作用，
    把后续降级路径悄悄废掉了。
  - 本次：隔离机制写对了，但被一个"尊重用户显式意图"的合理设计放过了
    真实危险。
  共同点：**防护机制本身正确，却被另一处看似无害的逻辑悄悄绕过**。
  以后凡是"默认值安全、但允许被环境变量覆盖"的防护，都要先想清楚：
  覆盖它的那个人，是不是正是会踩坑的那个人。
"""
import os
import sys
import tempfile
import shutil
from pathlib import Path

import pytest

# ============================================================
# 关键：必须在任何 test_*.py 被 import 之前执行（模块顶层，非 fixture）
# ============================================================
# pytest 的 collection 阶段会先加载 conftest.py，再 import 各测试文件，
# 这个特性保证了下面的代码一定跑在任何 `from config import ...` /
# `from services.persistence import ...` 之前，从而让 config.py 首次
# import 时读到的 DATA_DIR 已经是隔离目录，而不是真实生产路径。
#
# v9.9.13 FIX（2026-09-08）：**默认总是隔离，无视外部传入的 DATA_DIR**。
# 旧写法 `if not os.environ.get("DATA_DIR")` 是个逃逸口——会去设这个变量
# 的人正是想"模拟生产环境"的人，于是整段隔离被跳过、测试直写生产目录。
#
# 逃生阀改用专属变量 MONEYBAG_PYTEST_DATA_DIR，**不复用 DATA_DIR**：
#   复用 DATA_DIR 会把「模拟生产环境」和「允许写生产数据」两件事耦合在一起
#   —— 前者是合理需求，后者是灾难。拆成两个变量后，想挂真实数据调试的人
#   必须显式写出 MONEYBAG_PYTEST_DATA_DIR，这个动作本身就是一次确认。
_PYTEST_DATA_DIR: str = ""
_PYTEST_DATA_DIR_OWNED: bool = False  # True = 本文件创建的，会话结束要清理

_OVERRIDDEN_EXTERNAL_DATA_DIR: str = os.environ.get("DATA_DIR", "")

_explicit_dir = os.environ.get("MONEYBAG_PYTEST_DATA_DIR", "").strip()
if _explicit_dir:
    _PYTEST_DATA_DIR = _explicit_dir
    _PYTEST_DATA_DIR_OWNED = False
else:
    _PYTEST_DATA_DIR = tempfile.mkdtemp(prefix="moneybag_pytest_data_")
    _PYTEST_DATA_DIR_OWNED = True

os.environ["DATA_DIR"] = _PYTEST_DATA_DIR
(Path(_PYTEST_DATA_DIR) / "users").mkdir(parents=True, exist_ok=True)

if _OVERRIDDEN_EXTERNAL_DATA_DIR and \
        _OVERRIDDEN_EXTERNAL_DATA_DIR != _PYTEST_DATA_DIR:
    # 明确告知，避免"我明明设了 DATA_DIR 怎么没生效"的困惑，
    # 也提醒：这次测试不会碰你指定的那个目录。
    print(f"[conftest] 已忽略外部 DATA_DIR={_OVERRIDDEN_EXTERNAL_DATA_DIR}，"
          f"测试仍在隔离目录 {_PYTEST_DATA_DIR} 中运行"
          f"（如需挂真实数据调试请设 MONEYBAG_PYTEST_DATA_DIR）")


def pytest_sessionfinish(session, exitstatus):
    """整个测试会话结束后清理临时目录。

    **只清理本文件自己创建的目录**（_PYTEST_DATA_DIR_OWNED=True 时）。
    用户通过 MONEYBAG_PYTEST_DATA_DIR 显式指定的目录绝不删除 —— 那是用户
    的数据，不是我们的临时产物。
    """
    if _PYTEST_DATA_DIR_OWNED and _PYTEST_DATA_DIR \
            and os.path.isdir(_PYTEST_DATA_DIR):
        shutil.rmtree(_PYTEST_DATA_DIR, ignore_errors=True)


# 与 backend/.env 里出现的 key 名保持一致（脱敏后的清单，不含真实值）。
# 新增密钥类环境变量时记得同步补充这里。
_SECRET_ENV_KEYS = (
    "LLM_API_KEY", "LLM_API_BASE", "LLM_MODEL",
    "WXWORK_CORP_ID", "WXWORK_AGENT_ID", "WXWORK_SECRET",
    "WXWORK_TOUSER", "WXWORK_CALLBACK_TOKEN", "WXWORK_CALLBACK_AES_KEY",
    "TUSHARE_TOKEN",
    "DOUBAO_API_KEY", "DASHSCOPE_API_KEY",
    "DOUBAO_API_BASE", "DASHSCOPE_API_BASE",
)


@pytest.fixture(autouse=True)
def _clear_secret_env_pollution(monkeypatch):
    """每个测试运行前清空密钥类环境变量，防止 cache_warmer 等模块的
    import 时副作用泄漏真实密钥到其他测试。

    用 monkeypatch.delenv 而不是直接 del os.environ[...]：这样测试结束
    monkeypatch 会自动恢复（虽然我们期望恢复后也是"未设置"，但这样写法
    统一、且不会影响测试进程之外的环境）。
    """
    for key in _SECRET_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield

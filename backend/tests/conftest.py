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

背景（FIX 2026-09-17，任务：把「测试不写仓库」从惯例变成断言）：
上面三层隔离（`DATA_DIR` / `LOG_DIR` / `MONITOR_DIR`）全都只管得住
**愿意读环境变量的模块**。实测仍有多条通道绕过它们直写仓库（硬编码
`Path(__file__)...`、sys.path 里未归一化的 `".."` 等）。所以这里再加一层
**量结局**的守卫：会话开始前给受保护目录树拍快照，会话结束再拍一次，
出现新增/改写/删除就让整个会话失败。

守卫相关的两个环境变量（都是**测试专用**，与业务变量不复用）：

  ``MONEYBAG_PYTEST_GUARD_TREES``
      用给定清单**替换**（不是合并）默认受保护目录树，多个路径用
      `os.pathsep`（同 `PATH`，macOS/Linux 是 `:`）分隔，相对路径会按
      当前工作目录 `resolve()`。主要用于守卫自测：把守卫指向 tmp_path
      下的诱饵目录，就能在不碰真实仓库的前提下验证它"该红时红"。
      例::

          MONEYBAG_PYTEST_GUARD_TREES=/tmp/decoy \
              python -m pytest tests/test_conftest_data_dir_isolation.py -q

      留空 / 不设 → 回到 `_DEFAULT_GUARD_TREES`（真实 data 目录等）。

  ``MONEYBAG_PYTEST_WRITE_GUARD=0``
      整个关掉守卫。**只在一种场合合理**：实时生产机上跑测试，线上进程
      本身就在同时写这些目录，快照对比必然误报。日常开发**不要**设它——
      那不是"关掉一个烦人的报警"，而是把垃圾藏起来。

守卫报错里每一项都带 **mtime**（改写项还带基线 mtime）。因为"哪个文件
被动了"只告诉你去哪儿找，"它是什么时候落的"才指向**是谁**干的 ——
mtime 落在本次会话时段内 vs 落在三个月前，排查方向完全不同。
"""
import datetime
import os
import sys
import tempfile
import time
import shutil
from pathlib import Path
from typing import NamedTuple

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

# ============================================================
# FIX 2026-09-17：只隔离 DATA_DIR 是不够的 —— 还有两个"自己读环境变量定目录"
# 的模块，同样在 **import 期 mkdir**，而且完全不走 config.DATA_DIR：
#
#   scripts/llm_balance_monitor.py:76
#       _LOG_DIR = Path(os.environ.get("LOG_DIR", str(_BACKEND_DIR / "logs")))
#       _LOG_DIR.mkdir(parents=True, exist_ok=True)   # 默认 backend/logs
#   scripts/stock_monitor_cron.py:46
#       MONITOR_DIR = Path(os.environ.get("MONITOR_DIR",
#                          Path(__file__).parent.parent.parent / "data" / "monitor"))
#       MONITOR_DIR.mkdir(parents=True, exist_ok=True)
#
# 实测（2026-09-17，全量 2417 条 + 文件系统调用插桩）：这两个变量没被隔离时，
# 每跑一轮全量套件就会往**真实仓库**里写 4 次：
#   backend/logs/                          mkdir x3
#   backend/logs/llm_balance_monitor.log   open(a) x1  ← 实测已累积到 645KB
# 也就是说"DATA_DIR 隔离"只能证明测试没写 data/，证明不了测试没写仓库。
# 这里把两个变量一起指进会话临时目录，从源头掐掉。
#
# ⚠️ 为什么不用 `if not os.environ.get(...)`：与上面 DATA_DIR 的教训同源
# （2026-09-08 逃逸口事故）——"尊重外部显式设置"正是事故入口。一律强制覆盖。
os.environ["LOG_DIR"] = str(Path(_PYTEST_DATA_DIR) / "_logs")
os.environ["MONITOR_DIR"] = str(Path(_PYTEST_DATA_DIR) / "monitor")

if _OVERRIDDEN_EXTERNAL_DATA_DIR and \
        _OVERRIDDEN_EXTERNAL_DATA_DIR != _PYTEST_DATA_DIR:
    # 明确告知，避免"我明明设了 DATA_DIR 怎么没生效"的困惑，
    # 也提醒：这次测试不会碰你指定的那个目录。
    print(f"[conftest] 已忽略外部 DATA_DIR={_OVERRIDDEN_EXTERNAL_DATA_DIR}，"
          f"测试仍在隔离目录 {_PYTEST_DATA_DIR} 中运行"
          f"（如需挂真实数据调试请设 MONEYBAG_PYTEST_DATA_DIR）")


# ============================================================
# 仓库写入守卫（FIX 2026-09-17：把"测试不写真实 data 目录"从惯例变成断言）
# ============================================================
# 为什么还需要这一层——上面的环境变量隔离有两个结构性盲区：
#
#  盲区 1：**环境变量只能管到"愿意读环境变量的模块"**。实测仍有模块用
#    `Path(__file__).parent... / "data"` 硬编码拼路径（例如
#    infra/data_source/alt/flows.py:374 写 backend/infra/.cache、
#    services/longterm_screen.py:232 读 backend/data/fund_rank_ts.json），
#    它们不看 DATA_DIR，环境变量隔离对它们完全无效。
#
#  盲区 2：**sys.path 里带未归一化的 ".."** 会让 `Path(__file__).parent.parent.parent`
#    算错目录。实测真事故：
#      test_alert_push_test_mode_guard.py:39
#        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
#      → sys.path 里出现 `backend/tests/..`（**未归一化**）
#      → `from scripts import stock_monitor_cron` 的 __file__ 变成
#        `backend/tests/../scripts/stock_monitor_cron.py`
#      → pathlib 不会折叠 ".."，于是 .parent.parent.parent 算出 `backend/tests`
#      → MONITOR_DIR = backend/tests/data/monitor 被真实 mkdir 出来
#    这类"路径算错"不可能靠"多隔离几个环境变量"提前想到，只能靠事后核对发现。
#
# 所以守卫改成**量结局而不是猜入口**：在 conftest 顶层（早于任何 test_*.py
# 被 import）给受保护目录树拍快照，会话结束时再拍一次，只要出现
# 新增/改写/删除就**让会话失败**并列出清单。
#
# 为什么是"快照对比"而不是"拦截 os.mkdir/open"：
#   本仓的开发机 python 是托管版，pathlib 被套了一层 broker，
#   `os.mkdir` / `sys.addaudithook` 都拦不到 `Path.mkdir()`（实测插桩漏掉
#   全部 pathlib 建目录调用）。**依赖拦截 = 在部分环境上静默失效**，
#   这正是本项目最忌讳的"闸门空转仍显绿"。快照对比只用 os.stat/rglob，
#   在任何 python 上行为一致。
_GUARD_BACKEND_DIR = Path(__file__).resolve().parent.parent
_GUARD_REPO_ROOT = _GUARD_BACKEND_DIR.parent

# 受保护的目录树（默认）：真实数据目录 + 实测被写脏过的两处 + 测试目录自身。
# _GUARD_REPO_ROOT / "data"  = config.DEFAULT_DATA_DIR（生产权威数据目录）
# _GUARD_BACKEND_DIR / "data" = 历史遗留目录（ops_summary.py:64 命名为
#                               _LEGACY_DATA_DIR，服务器上的 /opt/moneybag/backend/data）
_DEFAULT_GUARD_TREES = (
    _GUARD_REPO_ROOT / "data",
    _GUARD_BACKEND_DIR / "data",
    _GUARD_BACKEND_DIR / "logs",
    _GUARD_BACKEND_DIR / "tests",
)
#
# ⚠️ 已知**尚未**纳入的第四条通道（2026-09-17 实测，全量一轮必中一次）：
#   backend/infra/.cache/industry_board_cache.json   ← 被"改写"1 次
#   元凶 infra/data_source/alt/flows.py:374
#       cache_dir = Path(__file__).parent.parent.parent / ".cache"
#       cache_dir.mkdir(exist_ok=True)
#     —— 硬编码路径，既不走 config.DATA_DIR，也没有可覆盖的环境变量，
#        测试侧**无法**隔离；要治必须动生产代码（把它改成读
#        config.DATA_DIR 或新增一个 MONEYBAG_CACHE_DIR 环境变量）。
#   为什么现在不把它加进受保护清单：加了全量立刻转红（实测改写 1 项/
#   一轮），而修法在生产侧，测试侧加了只能得到一条永远红的守卫 —— 那正是
#   "闸门空转"的反面（噪音红），同样会让人第一反应是把它关掉。
#   ✅ 一旦 flows.py 改成可配置，把 `_GUARD_BACKEND_DIR / "infra" / ".cache"`
#      追加进上面的元组即可，无需改任何其他代码。
#   现状由 test_pytest_repo_write_guard.py 里的
#   test_known_unfixed_repo_write_channel_is_documented 钉住：
#   谁改了 flows.py，那条用例会红，提醒他回来补这一行。

# 逃生/测试阀：设了就用它**替换**默认清单（不合并），供守卫自测用诱饵目录。
# 与 MONEYBAG_PYTEST_DATA_DIR 同理，不复用业务变量。
# 用法（`os.pathsep` 分隔、替换默认清单、示例与注意点）见
# :func:`_guard_tree_list` 的 docstring 和本文件顶部模块 docstring。
_GUARD_TREES_ENV = "MONEYBAG_PYTEST_GUARD_TREES"
# 开关：实时生产机上跑测试时，data/ 会被线上进程同时写入，快照对比会误报。
# 这种场景显式关掉守卫（MONEYBAG_PYTEST_WRITE_GUARD=0），不要改成"只警告"。
_GUARD_ENABLED_ENV = "MONEYBAG_PYTEST_WRITE_GUARD"

# 不纳入快照的目录/后缀：它们由 python 解释器与 pytest 自己维护，
# 把 __pycache__ 算进来会每次都"变了"，守卫立刻退化成噪音。
_GUARD_SKIP_DIR_NAMES = frozenset({
    "__pycache__", ".pytest_cache", ".git", ".mypy_cache", ".ruff_cache",
    ".idea", ".vscode", ".DS_Store",
})
_GUARD_SKIP_SUFFIXES = frozenset({".pyc", ".pyo"})

GUARD_TREES: tuple = ()
GUARD_ENABLED: bool = True


def _guard_tree_list() -> tuple:
    """解析受保护目录树清单（可被 MONEYBAG_PYTEST_GUARD_TREES 覆盖）。

    环境变量用法（**替换**默认清单，不合并）::

        MONEYBAG_PYTEST_GUARD_TREES=/tmp/decoy_a:/tmp/decoy_b \
            python -m pytest tests/test_conftest_data_dir_isolation.py -q

    多个路径用 `os.pathsep`（Linux/macOS 为 `:`）分隔；空片段被忽略；
    未设或全空则回落到 :data:`_DEFAULT_GUARD_TREES`。
    设计上只用于守卫自测（诱饵目录）与"线上进程同时在写"的临时豁免，
    **日常跑测试不要设它**。

    Returns:
        已 `resolve()` 的目录路径元组。
    """
    raw = os.environ.get(_GUARD_TREES_ENV, "").strip()
    if raw:
        return tuple(Path(part).resolve() for part in raw.split(os.pathsep) if part.strip())
    return tuple(p.resolve() for p in _DEFAULT_GUARD_TREES)


def guard_snapshot(roots=_DEFAULT_GUARD_TREES) -> dict:
    """对受保护目录树做一次内容快照。

    Args:
        roots: 要快照的根目录（不存在则跳过）。

    Returns:
        {根目录字符串: {相对路径: (大小, mtime_ns)}}。目录与文件都记，
        这样"只建了个空目录"这种污染也能被发现——服务器上的
        /opt/moneybag/backend/data 就是这种形态（8KB、0 个文件的空目录树）。

    ⚠️ 第二个元素 `mtime_ns` 不是可有可无的装饰：它既是 diff 判定"改写"
    的一半依据（另一个是 size），也是 :func:`guard_diff` 产出
    :class:`GuardEntry` 的 mtime 来源 —— 报错文本里那句"多久之前落的"
    全靠它。改成只存 size 会让 `_guard_message` 静默退化成 `mtime=未知`
    （不会报错，只会让守卫少一半线索），所以别动这个元组形状。
    """
    out: dict = {}
    for root in roots:
        root_path = Path(root)
        if not root_path.exists():
            continue
        entries: dict = {}
        for p in root_path.rglob("*"):
            try:
                rel = p.relative_to(root_path)
            except ValueError:
                continue
            if any(part in _GUARD_SKIP_DIR_NAMES for part in rel.parts):
                continue
            if p.suffix in _GUARD_SKIP_SUFFIXES:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            entries[str(rel)] = (st.st_size, st.st_mtime_ns)
        out[str(root_path)] = entries
    return out


class GuardEntry(NamedTuple):
    """守卫 diff 结果里的一项。

    Attributes:
        path: `根::相对路径`，与加入 mtime 之前的显示格式完全一致。
        mtime_ns: 该项的 mtime（纳秒）。新增/改写取**当前**快照；删除取
            **基线**快照 —— 文件已经没了，基线里那个是它留下的唯一痕迹。
        baseline_mtime_ns: 基线快照里的 mtime。改写项才有值，新增项没有
            基线（填 None），删除项的它等于 `mtime_ns`。
    """

    path: str
    mtime_ns: int = 0
    baseline_mtime_ns: int | None = None


# 报错里最多列这么多条，超出的只报数量 —— 一次污染几十个文件时，
# 全列出来反而看不清（实测一轮能写十几个）。
_GUARD_MAX_LISTED = 20


def _guard_mtime_of(snapshot_value) -> int:
    """从快照条目 `(size, mtime_ns)` 里取 mtime；拿不到就返回 0。

    拿不到只可能说明快照结构被改过，此时宁可报 `未知` 也不能让守卫
    自己抛异常 —— 守卫一崩，污染就没人拦了。
    """
    try:
        return int(snapshot_value[1])
    except (TypeError, IndexError, ValueError):
        return 0


def _guard_format_delta(seconds: float) -> str:
    """把秒数渲染成人类可读的量级（s / min / h / d）。"""
    sign = "-" if seconds < 0 else ""
    sec = abs(float(seconds))
    if sec < 90:
        return f"{sign}{sec:.1f}s"
    if sec < 5400:
        return f"{sign}{sec / 60:.1f}min"
    if sec < 172800:
        return f"{sign}{sec / 3600:.1f}h"
    return f"{sign}{sec / 86400:.1f}d"


def _guard_format_mtime(mtime_ns: int, now_ns: int) -> str:
    """把 mtime 渲染成「本地时刻（距今 X）」。

    Args:
        mtime_ns: 纳秒时间戳；<=0 视为未知（拿不到 mtime 时的兜底值）。
        now_ns: 「现在」的纳秒时间戳，用来算距今多久。

    Returns:
        形如 `2026-09-17 23:04:05（距今 3.2s）` 的文本。
    """
    if not mtime_ns or mtime_ns <= 0:
        return "未知"
    try:
        stamp = datetime.datetime.fromtimestamp(mtime_ns / 1e9)
        text = stamp.strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return "未知"
    return f"{text}（距今 {_guard_format_delta((now_ns - mtime_ns) / 1e9)}）"


def guard_diff(before: dict, after: dict) -> tuple:
    """比对两次快照，返回 (新增, 改写, 删除) 三组 :class:`GuardEntry`。

    判定依据与加入 mtime 之前完全一致（键集合做差 + `(size, mtime_ns)`
    是否相等），只是每项额外带上 mtime，好让报错能回答"多久之前落的"。

    Args:
        before: 会话开始前的快照。
        after: 会话结束时的快照。

    Returns:
        (added, changed, removed)，每个元素都是 :class:`GuardEntry` 列表。
    """
    added: list = []
    changed: list = []
    removed: list = []
    # 用 sorted 而不是直接迭代 set：str 的 hash 带随机种子，直接迭代会让
    # 报错清单的行序在两次运行之间变化，日志没法对照。排序只影响展示顺序，
    # 不影响"哪些项算变化"这个判定。
    for root in sorted(set(before) | set(after)):
        b = before.get(root, {})
        a = after.get(root, {})
        for rel in sorted(set(a) - set(b)):
            added.append(GuardEntry(f"{root}::{rel}", _guard_mtime_of(a[rel])))
        for rel in sorted(set(b) - set(a)):
            removed.append(GuardEntry(f"{root}::{rel}", _guard_mtime_of(b[rel])))
        for rel in sorted(set(a) & set(b)):
            if a[rel] != b[rel]:
                changed.append(GuardEntry(f"{root}::{rel}",
                                          _guard_mtime_of(a[rel]),
                                          _guard_mtime_of(b[rel])))
    return added, changed, removed


def _guard_check() -> tuple:
    """拍一次快照并与会话基线比对，返回 (新增, 改写, 删除)。"""
    return guard_diff(_GUARD_BASELINE, guard_snapshot(GUARD_TREES))


def _guard_render_group(label: str, entries: list, now_ns: int,
                        with_baseline: bool) -> list:
    """把一组 diff 结果渲染成若干行文本。

    Args:
        label: 组名（新增 / 改写 / 删除）。
        entries: :class:`GuardEntry` 列表。
        now_ns: 「现在」的纳秒时间戳。
        with_baseline: 是否额外给出基线 mtime（改写组为 True）。

    Returns:
        文本行列表（不含末尾换行）。
    """
    lines = [f"  {label} {len(entries)} 项:"]
    for entry in entries[:_GUARD_MAX_LISTED]:
        line = f"    {entry.path}\n        mtime={_guard_format_mtime(entry.mtime_ns, now_ns)}"
        if with_baseline:
            line += (f"  基线 mtime="
                     f"{_guard_format_mtime(entry.baseline_mtime_ns or 0, now_ns)}")
        lines.append(line)
    rest = len(entries) - _GUARD_MAX_LISTED
    if rest > 0:
        lines.append(f"    ...另有 {rest} 项未列出")
    return lines


def _guard_message(added: list, changed: list, removed: list,
                   now_ns: int | None = None) -> str:
    """把一次守卫比对结果渲染成可直接读的错误文本。

    每一项都带 mtime，改写项额外带基线 mtime —— 光知道"哪个文件被动了"
    只能定位到目录，"它是什么时候落的"才能定位到**谁**：落在本次会话时段
    内说明是刚被测试写的，落在很久以前说明是历史残留被本次会话改写。

    Args:
        added: 新增项（:class:`GuardEntry`）。
        changed: 改写项（:class:`GuardEntry`，带基线 mtime）。
        removed: 删除项（:class:`GuardEntry`，mtime 取自基线快照）。
        now_ns: 「现在」的纳秒时间戳，用来算"距今多久"；None 则现取。

    Returns:
        多行错误文本。
    """
    now = int(time.time_ns()) if now_ns is None else int(now_ns)
    lines = [
        "[MONEYBAG_WRITE_GUARD] 测试进程改动了受保护目录树 —— 数据目录隔离失效。",
    ]
    lines.extend(_guard_render_group("新增", added, now, with_baseline=False))
    lines.extend(_guard_render_group("改写", changed, now, with_baseline=True))
    lines.extend(_guard_render_group("删除", removed, now, with_baseline=False))
    lines.append(
        "  修法优先序：① 让被测模块走 config.DATA_DIR（conftest 已把它指到临时目录）；"
        "② 若该模块自己读环境变量定目录，在 conftest 里一并隔离；"
        "③ 若路径是 Path(__file__) 硬拼出来的，检查 sys.path 里有没有未归一化的 '..'。")
    lines.append(
        f"  （受保护目录树: {[str(t) for t in GUARD_TREES]}；"
        f"设 {_GUARD_ENABLED_ENV}=0 可临时关闭，但那只是把垃圾藏起来）")
    return "\n".join(lines)


GUARD_TREES = _guard_tree_list()
GUARD_ENABLED = os.environ.get(_GUARD_ENABLED_ENV, "1").strip().lower() not in (
    "0", "false", "no", "off")

# **就在这里**拍基线：此刻 conftest 刚被 import，还没有任何 test_*.py 被加载，
# 也还没有任何模块的 import 期 mkdir 发生 —— 这是整个会话最早的干净时刻。
_GUARD_BASELINE: dict = guard_snapshot(GUARD_TREES) if GUARD_ENABLED else {}


# ============================================================================
# 这里**故意**没有 session 级 autouse fixture。
# ============================================================================
# 2026-09-18 之前有个 `_repo_write_guard`（session/autouse），它在 teardown
# 里先 raise、后改成写 stderr，**两次都被实测证明不可靠**，现已删除。
# 守卫的唯一判定点收敛到下面的 :func:`pytest_sessionfinish`，单一真相源。
#
# ⚠️⚠️ 同一个 pytest 机制的三副面孔（务必一起读，只看一副会修错地方）
# ----------------------------------------------------------------------------
# 根因是 pytest 的一条语义：**session 级 fixture 的 teardown 会被算到会话
# 最后一个 item 名下**，而不是挂在"守卫"名下。它有三副面孔：
#
#   面孔 A —— **假绿**（2026-09-17 实测，pytest 9.1.1）：
#     最后一个用例是 xfail / skip 时，teardown 抛的 AssertionError 被一起吞成
#     xfailed。最小复现 `test_a`(pass) + `test_b`(non-strict xfail) → 输出
#     `1 passed, 2 xfailed`、**退出码 0**，守卫彻底静音。
#
#   面孔 B —— **假凶手**（2026-09-18 定位）：
#     最后一个用例是普通 pass 时，同一个 teardown 错误不被吞，而是被渲染成
#     `ERROR <最后一个用例>`，守卫嫁祸给一个完全无辜的用例。
#     实测：只跑 `tests/test_wxwork_push_bytes.py` 并让守卫开火 →
#     `49 passed, 1 error`，且
#     `ERROR tests/test_wxwork_push_bytes.py::test_quality_check_uses_effective_channel_limit`。
#     该用例字母序最后、又排在文件末尾，因此命中"最后一个 item"，**与仓库被
#     写脏毫无关系** —— 曾被人当成"顺序依赖型污染"排查了很久。
#
#   面孔 C —— **静默空转**（2026-09-18 实测，压垮它的最后一根稻草）：
#     把 raise 换成 `sys.stderr.write` 后，那行摘要**一条都没输出**：用 Grep
#     工具在 stderr 里只搜到完整清单 1 处、摘要 0 处 —— session 级 fixture 的
#     teardown 期间 pytest 捕获仍生效，写进去的内容进了缓冲区且无人消费。
#     ⇒ 在 fixture teardown 里做**任何输出**都不可靠。
#
# 三副面孔的共同点：**fixture teardown 这个位置本身就不可靠** —— 错误归属
# 不可靠（A/B）、输出不可靠（C）。所以判定放在 pytest_sessionfinish：它在
# 所有 item 之后运行、直接改 session.exitstatus，既不受用例标记影响，也不受
# 捕获影响 —— 吞不掉、嫁祸不了、也不会静默。
#
# ⚠️ 故障注入锚点：把 conftest 顶层的
#    ``os.environ["DATA_DIR"] = _PYTEST_DATA_DIR``
# 注释掉再跑 `pytest tests/test_phase3_services.py`，守卫必须转红并列出真实
# data/users/ 下新增的文件。恒绿的守卫等于空转的绿。
# 验收三条（2026-09-18 定）：① 退出码非 0；② stderr 出现
# MONEYBAG_WRITE_GUARD 完整清单；③ **stdout 不得出现 `ERROR <某个用例>`**
# （第 ③ 条正是面孔 B，是这次改动的核心目的）。


def pytest_sessionfinish(session, exitstatus):
    """整个测试会话结束后清理临时目录。

    **只清理本文件自己创建的目录**（_PYTEST_DATA_DIR_OWNED=True 时）。
    用户通过 MONEYBAG_PYTEST_DATA_DIR 显式指定的目录绝不删除 —— 那是用户
    的数据，不是我们的临时产物。

    ⚠️ 这里还有第二件事，而且是**权威**的那件：仓库写入守卫的判定**唯一**
    放在这里（2026-09-18 起）。原先那个 session 级 autouse fixture
    `_repo_write_guard` 已被删除，原因见上方「同一个 pytest 机制的三副面孔」
    注释块 —— pytest 会把 session 级 fixture 的 teardown 算到会话最后一个
    item 名下：最后一个用例是 xfail 时错误被吞（守卫静音、退出码 0），是普通
    pass 时被渲染成 `ERROR <该用例>`（嫁祸无辜用例），而改成写 stderr 则整行
    进了捕获缓冲区、一条都出不来（静默空转）。
    pytest_sessionfinish 在所有 item 之后运行，直接改 session.exitstatus，
    不受任何用例标记影响；且实测这里的 stderr 写入能落地，而 session 级
    fixture teardown 里的写入会被捕获吞掉（面孔 C）。
    """
    if GUARD_ENABLED:
        added, changed, removed = _guard_check()
        if added or changed or removed:
            # 打到 stderr：stdout 末尾那行 "N passed" 会把人骗过去，
            # stderr 至少在终端/CI 日志里和它是分开的。
            sys.stderr.write("\n" + "=" * 72 + "\n"
                             + _guard_message(added, changed, removed)
                             + "\n" + "=" * 72 + "\n")
            session.exitstatus = 1
    else:
        # 守卫被显式关闭时必须留痕：否则「压根没生效」和「确实没污染」
        # 在输出上完全无法区分 —— 那是另一种静默空转。
        sys.stderr.write(
            f"\n[conftest] ⚠️ {_GUARD_ENABLED_ENV}=0，已跳过仓库写入守卫\n")

    if _PYTEST_DATA_DIR_OWNED and _PYTEST_DATA_DIR \
            and os.path.isdir(_PYTEST_DATA_DIR):
        shutil.rmtree(_PYTEST_DATA_DIR, ignore_errors=True)


# 与 backend/.env 里出现的 key 名保持一致（脱敏后的清单，不含真实值）。
# 新增密钥类环境变量时记得同步补充这里。
#
# FIX 2026-09-13：这份清单此前对企微**是失效的** —— wxwork_push 的
# _CORP_ID/_SECRET/_AGENT_ID 是 import 期冻结的模块级常量，下面那个
# monkeypatch.delenv 改的是 os.environ，冻住的常量读不到。
# 现在 wxwork_push 已改成调用时实时 os.getenv（见 services/wxwork_push.py 的
# _read_config），这个 fixture 才真正成为「配置层」防护：清掉这三个变量，
# is_configured() 就会返回 False，推送分支连入口都进不去。
# 同时补上 WXWORK_USER_ID：它决定默认 touser（缺省 "@all"），生产机上若配了
# 真实 userId，不清掉的话测试进程会拿真实账号当默认收件人。
_SECRET_ENV_KEYS = (
    "LLM_API_KEY", "LLM_API_BASE", "LLM_MODEL",
    "WXWORK_CORP_ID", "WXWORK_AGENT_ID", "WXWORK_SECRET",
    "WXWORK_USER_ID",
    "WXWORK_TOUSER", "WXWORK_CALLBACK_TOKEN", "WXWORK_CALLBACK_AES_KEY",
    "TUSHARE_TOKEN",
    "DOUBAO_API_KEY", "DASHSCOPE_API_KEY",
    "DOUBAO_API_BASE", "DASHSCOPE_API_BASE",
)


# ============================================================
# 企微推送拦截（FIX 2026-09-13：测试 mock 造的假告警被真推到了用户企微）
# ============================================================
# 事故：backend/tests/conftest.py 只隔离了 DATA_DIR，**完全没有拦截企微推送**。
#   test_chat_model_routing.py 用 fake httpx 造出 HTTP 402 后，
#   gateway（infra/llm/gateway.py:110 回退分支）→ services.llm_quota_alert.
#   maybe_alert_quota → services.wxwork_push.send_daily_report_to 走的是
#   **真实**发送函数；而 wxwork_push 的 _CORP_ID/_SECRET/_AGENT_ID 是模块级
#   常量（import 时从 os.environ 抓一次就定死），conftest 那个"每个用例清空
#   密钥环境变量"的 autouse fixture 根本管不到它 —— 只要模块在任何一次密钥
#   还在的环境里被 import 过，is_configured() 就恒为 True，假告警真发出去。
#
# 拦截点选在 wxwork_push **唯一的网络出口** —— 模块级 httpx.Client 单例
# `_http_client`（get = 取 access_token，post = 发消息），而不是替换 send_*：
#   • 替换 send_* 会砸掉 test_wxwork_push_bytes.py 这类合法用例 —— 它们
#     monkeypatch 的是 _send_raw，需要真实走到分片/拼装逻辑去验证字节行为；
#   • 卡在 HTTP 出口对所有调用方一视同仁（不止 maybe_alert_quota），且
#     **不改变任何函数的返回值契约**：_get_token 取不到 token 后 _send_raw
#     照常返回 not-ok，行为与"企微未配置"完全一致，不会有意外异常。
#
# 被拦下的请求必须**打印日志 + 记进 wecom_push_sink**，绝不静默丢弃 ——
# 静默丢弃就是本项目最忌讳的「闸门空转仍显绿」。
_WXWORK_BLOCKED_CALLS: list = []


@pytest.fixture(autouse=True)
def _block_real_wecom_push(monkeypatch):
    """禁止测试进程发起任何真实企微网络请求（autouse，逐个用例生效）。"""
    try:
        from services import wxwork_push as wp
    except Exception as e:  # noqa: BLE001 - 依赖缺失时本就没有网络出口可拦
        print(f"[conftest] ⚠️ 无法导入 services.wxwork_push，跳过企微拦截：{e}")
        yield
        return

    def _record_blocked(method: str, url: str, payload) -> None:
        # ⚠️ url 里带 corpid / corpsecret / access_token —— 只记 path，绝不回显
        path = str(url).split("?", 1)[0]
        preview = ""
        if isinstance(payload, dict):
            body = payload.get("text") or payload.get("markdown") or {}
            if isinstance(body, dict):
                preview = str(body.get("content", ""))[:200]
        _WXWORK_BLOCKED_CALLS.append(
            {"method": method, "path": path, "preview": preview}
        )
        print(f"[WXWORK][TEST_BLOCKED] 已拦截真实企微请求 {method} {path}"
              f"（测试进程禁止外发）｜内容片段：{preview}")

    def _no_get(url, *args, **kwargs):
        _record_blocked("GET", url, None)
        raise RuntimeError("[conftest] 测试环境禁止真实企微 HTTP 请求（GET）")

    def _no_post(url, *args, **kwargs):
        payload = kwargs.get("json")
        if payload is None and args:
            payload = args[0]
        _record_blocked("POST", url, payload)
        raise RuntimeError("[conftest] 测试环境禁止真实企微 HTTP 请求（POST）")

    # raising=True：万一 httpx 改了 API 导致装不上，宁可整片测试红，
    # 也不能"静默没装上" —— 静默失败 = 闸门空转仍显绿（M14）。
    monkeypatch.setattr(wp._http_client, "get", _no_get, raising=True)
    monkeypatch.setattr(wp._http_client, "post", _no_post, raising=True)
    yield


@pytest.fixture
def wecom_push_sink() -> list:
    """本用例期间被 conftest 拦下的真实企微请求（供守卫用例断言）。"""
    _WXWORK_BLOCKED_CALLS.clear()
    return _WXWORK_BLOCKED_CALLS


@pytest.fixture(autouse=True)
def _isolate_alert_state_file(monkeypatch):
    """每个用例用一份干净的去重状态文件，杜绝用例间互相屏蔽。

    背景（与本次假告警同一处设计缺陷）：`ALERT_STATE_FILE = DATA_DIR /
    "llm_alert_state.json"` 是**进程级共享**的，而 DATA_DIR 只是按 pytest
    **会话**隔离 —— 于是任何"真的推了一把"的用例会把 `alert_type|model` 写进
    共享文件，后面的用例再报同类告警就被去重静默吞掉，表现成"推送数 0"。

    这不是理论风险：本次加守卫时就踩到了 —— 守卫用例（按文件名排在前面）
    用 model="m" 推了一次 P0，后面 test_p0_pushes_even_at_night 同键命中去重，
    断言 0 == 2 变红，而代码完全正确。**顺序依赖的假红比真 bug 更费时间**。
    """
    try:
        from services import llm_quota_alert as qa
    except Exception as e:  # noqa: BLE001 - 模块不可用时无事可做
        print(f"[conftest] ⚠️ 无法导入 services.llm_quota_alert，跳过状态隔离：{e}")
        yield
        return

    state_file = Path(_PYTEST_DATA_DIR) / "llm_alert_state.json"
    try:
        if state_file.exists():
            state_file.unlink()
    except OSError as e:  # noqa: BLE001 - 清理失败不该让整片测试红
        print(f"[conftest] ⚠️ 清理去重状态文件失败：{e}")
    monkeypatch.setattr(qa, "ALERT_STATE_FILE", state_file, raising=True)
    yield


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


# ============================================================
# config 模块基线恢复（FIX 2026-09-13：importlib.reload(config) 的顺序依赖污染）
# ============================================================
# 症状：tests/test_llm_quota_alert_dedupe.py::test_state_file_lives_under_data_dir
#   断言 `qa.ALERT_STATE_FILE.parent == config.DATA_DIR`，表现是「单独跑必绿、
#   混在全量里红」。注意这不是随机：当前没装会打乱顺序的插件（pytest-randomly
#   等），执行顺序按文件名字母序是**确定性**的，所以红绿固定，不是抽风。
#
# 根因：一批用例为了测 import 期行为，用 monkeypatch.setenv("DATA_DIR", tmp_path)
#   + importlib.reload(config) 把 config 整个重载（全仓**只有 2 处**：
#   test_fund_detail_ak_timeout.py:44 / test_user_optimistic_lock.py:48）。
#   monkeypatch 只负责还原 **os.environ**，而 reload 是**原地重跑模块代码**：
#   config 模块对象被永久改写，DATA_DIR 连同它派生出来的 USERS_DIR /
#   RECEIPTS_DIR / PUSH_ARCHIVE_DIR 全部停在上一个用例的 tmp_path 上。
#   monkeypatch 压根不知道有这么回事，所以无从还原。
#
# ⚠️ 别把 test_broker_research_quota_degradation.py 算成污染源：它的
#   _reload_broker_research() reload 的是 services.broker_research，而
#   services/broker_research.py 全文既没有 DATA_DIR 也没 import config，
#   **不污染 config**。（这段注释早期写成"3 处"，是错的，已更正。）
#
# 为什么不去改那 2 个污染源：它们**必须** reload 才能测到 import 期行为，
# 改成 monkeypatch.setattr 会让它们测不到真实路径、变成空转的绿。
#
# ⚠️ 谁污染谁，完全由文件名字母序决定（`pytest tests/ --collect-only -q` 实测：
#   broker(b) → config_module_state_restored(c) → fund_detail(f) →
#   dedupe(l) → user_optimistic_lock(u)）。例如 user_optimistic_lock 确实是
#   污染源，但 u > l，它排在 dedupe **后面**，喂不到那条断言 —— 别按"所有污染源
#   都在它前面"推理。也就是说：**没有本守卫时，这套"安全"是文件名给的，不是
#   测试自己挣的**；守卫在，顺序才真的不重要。这是本守卫不能被删的理由。
#
# 实测污染方向（安全确认）：污染后 config.DATA_DIR 指向 pytest 的 tmp 目录
#   （/private/var/.../pytest-of-root/pytest-N/test_xxx0），**不是**生产路径
#   /opt/moneybag/data —— 所以这是「顺序依赖假红」问题，不是「写脏生产数据」
#   问题。下面这条守卫会顺手把这个结论钉成可执行断言。
#
# 恢复手法：**直接把属性改回基线值，绝不 reload** —— reload 会重跑模块代码
#   （读 env、建目录），全量约 2074 条用例各来一次会显著拖慢套件。属性赋值是
#   常数级。（条数会随用例增删漂移，截至 2026-09-14；以
#   `cd backend && env -u PYTHONPATH python3 -m pytest tests/ -q --collect-only`
#   的 collected 总数为准，别拿 `-q` 尾行的 passed 数当总数。）
_RESTORABLE_TYPES = (str, int, float, bool, bytes, tuple, Path, type(None))

# 会话级基线。**在 conftest 模块顶层就地采集**（见下方 _capture 调用点），
# 不是"第一次用到时再采"。理由见 _CONFIG_BASELINE_PHASE 的注释。
_CONFIG_BASELINE: dict = {}
_CONFIG_BASELINE_READY: bool = False

# 采集阶段标记 —— 供守卫用例断言"基线确实是早期采的"。
# 这条标记是**防退化**用的：光看测试绿不绿分不出基线是几点采的，而采晚了
# （比如被某个 module 级 fixture 抢先 reload 过 config）基线本身就是脏的，
# 于是守卫会勤勤恳恳地把每个用例都"恢复"成那个脏值 —— 典型的闸门空转仍显绿。
# 取值：
#   "conftest-import"  — 期望值：conftest 顶层采集，早于任何 test_*.py 被 import
#   "first-test-setup" — 退化值：conftest 顶层 import 失败，退到首个用例 setup 才采
#   "unavailable"      — config 完全不可用，守卫空转（守卫用例会把它打成红）
_CONFIG_BASELINE_PHASE: str = "unavailable"


def _ensure_backend_on_syspath() -> None:
    """把 backend/ 显式放进 sys.path，让 `import config` 与 CWD 无关。

    原本这里什么都没做，靠"某个 test_*.py 已经执行过
    `sys.path.insert(0, str(Path(__file__).parent.parent))`"这种**别人的副作用**
    才让下面的 `import config` 成功。collection 阶段会 import 全部测试模块、
    其中很多确实做了 insert，所以实际上一直能用 —— 但这是运气不是机制：
    一旦 `import config` 失败，守卫会走 except 分支只打印一行警告然后
    **静默空转**，全量照样全绿。安全闸门最忌讳这个，所以在这里补死。
    """
    backend_dir = str(Path(__file__).resolve().parent.parent)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


def _snapshot_config_module(cfg) -> dict:
    """浅拷贝 config 模块里所有**不可变**类型的模块级属性。

    只收不可变类型（str/int/float/bool/tuple/Path/None）：
      • reload 造成的污染全部落在这类值上（DATA_DIR 及其派生路径、以及所有
        os.environ 读出来的配置），收它们就够；
      • 刻意**不收** dict/list/set —— reload 会给它们换成内容相同的新对象，
        还原反而可能踩到「某个用例就地改了共享字典」这类正当用法。
    """
    return {
        name: value
        for name, value in vars(cfg).items()
        if not name.startswith("__") and isinstance(value, _RESTORABLE_TYPES)
    }


# ============================================================
# 就在这里采集基线（conftest 顶层执行，不是 fixture 内）
# ============================================================
# 时机论证（上一版是"第一次用到时才采"，这里改成立即采）：
#   pytest 保证「先 import conftest.py，再 import 同目录任何 test_*.py」。
#   所以本行执行时：① os.environ["DATA_DIR"] 已在上面被指到会话临时目录；
#   ② 还没有任何测试模块被 import，更没有任何一次 setenv+reload 发生过。
#   ⇒ 此刻 config 的状态**必然**是未被污染的，是最早、最干净的采集点。
#
#   上一版推迟到"第一个用例 setup 时才采"，多扛了两轮风险：
#     • 若将来出现 module/session 级 fixture 里做 setenv+reload（这类 fixture
#       的 setup **早于** 本函数级 fixture），基线就会把污染值当成基线，
#       之后每个用例都被"忠实地恢复"成那个脏值 —— 闸门空转仍显绿；
#     • 若首个执行的用例本身就污染 config，同理。
#   立即采集把这两条路径一起堵死，代价只是 config 被提前 import（本来也一定会
#   被 import，且它只读 env + mkdir，无副作用差异）。
_ensure_backend_on_syspath()
try:
    import config as _baseline_config
except Exception as _e:  # noqa: BLE001 - 顶层 import 失败不能让整个会话崩
    print(f"[conftest] ⚠️ 顶层无法导入 config，config 基线退化为懒采集：{_e}")
else:
    _CONFIG_BASELINE.update(_snapshot_config_module(_baseline_config))
    _CONFIG_BASELINE_READY = True
    _CONFIG_BASELINE_PHASE = "conftest-import"


@pytest.fixture(autouse=True)
def _restore_config_module_state():
    """每个用例结束后把 config 模块恢复成会话基线（autouse，逐个用例生效）。

    只做 teardown 恢复、**不做 setup 恢复**，是刻意的：
      • 已确认的全部污染源（setenv + reload）都发生在用例体内或函数级
        fixture 里，函数级 fixture 的 teardown 一定晚于本 fixture 的 setup，
        teardown 恢复足以覆盖；
      • 反过来若在 setup 也恢复一次，会毁掉"module 级 fixture 故意设一个
        非常规 config 值供整文件复用"这种正当写法。宁可少做，不可误伤。
    """
    global _CONFIG_BASELINE_READY, _CONFIG_BASELINE_PHASE

    try:
        import config as cfg
    except Exception as e:  # noqa: BLE001 - config 不可用时无事可做
        # 注意：这里**不能**静默。守卫用例会读 _CONFIG_BASELINE_PHASE，
        # 顶层采集若也失败，守卫用例会红，不会伪装成绿。
        print(f"[conftest] ⚠️ 无法导入 config，跳过模块状态恢复：{e}")
        yield
        return

    if not _CONFIG_BASELINE_READY:
        # 顶层采集失败时的兜底：退到首个用例 setup 再采一次，并如实标记阶段
        _CONFIG_BASELINE.update(_snapshot_config_module(cfg))
        _CONFIG_BASELINE_READY = True
        _CONFIG_BASELINE_PHASE = "first-test-setup"
        print("[conftest] ⚠️ config 基线为懒采集（first-test-setup），"
              "请检查顶层 import 为何失败")

    yield

    # teardown：只改回「和基线不一样」的属性，避免无谓的 setattr
    #
    # ⚠️ 故障注入锚点：把下一行的 setattr 换成 pass，应当**正好 5 条红**。
    # 复现命令（0.3s，必须带守卫文件一起跑，只跑被保护的文件会 0 红）：
    #   pytest tests/test_config_module_state_restored.py \
    #          tests/test_llm_quota_alert_dedupe.py -q
    # 预期红的清单与「少于 5 条才是警报」的理由见
    # tests/test_config_module_state_restored.py 模块 docstring 的「故障注入指纹（FI-1）」。
    for name, baseline_value in _CONFIG_BASELINE.items():
        try:
            if getattr(cfg, name, None) != baseline_value:
                setattr(cfg, name, baseline_value)
        except Exception as e:  # noqa: BLE001 - 单个属性失败不该让整片测试红
            print(f"[conftest] ⚠️ 恢复 config.{name} 失败：{e}")

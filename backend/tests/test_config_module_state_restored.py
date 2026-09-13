"""config 模块基线恢复的守卫用例（FIX 2026-09-13 顺序依赖假红）。

背景
----
一批用例靠 `monkeypatch.setenv("DATA_DIR", tmp_path)` + `importlib.reload(config)`
来测 import 期行为。全仓**只有 2 处**真正的 config 污染源：

  · test_fund_detail_ak_timeout.py:44
  · test_user_optimistic_lock.py:48

⚠️ 别把 `test_broker_research_quota_degradation.py` 算进来 —— 它的
`_reload_broker_research()` reload 的是 `services.broker_research`，
而 `services/broker_research.py` 全文既没有 `DATA_DIR` 也没 import config，
**不污染 config**。（早期版本这里写成"3 处"，是错的，已更正。）

monkeypatch 只还原 **os.environ**；而 reload 是**原地重跑模块代码**，config 模块
对象被永久改写，DATA_DIR 及其派生的 USERS_DIR / RECEIPTS_DIR / PUSH_ARCHIVE_DIR
会一直停在上一个用例的 tmp_path 上。

后果：任何断言 `== config.DATA_DIR` 的用例都变成**按执行顺序红绿不定**
（已知受害者 test_llm_quota_alert_dedupe.py::test_state_file_lives_under_data_dir）。

注：当前**没有**装 pytest-randomly 之类会打乱顺序的插件，也**没有** pytest.ini
/ setup.cfg / pyproject，执行顺序按文件名字母序是**确定性**的 —— 所以表现是
"单跑必绿、全量必红"的固定模式，不是每次随机。但一旦将来引入随机排序插件，
本文件 test_1→test_2、test_5→test_6 的先后依赖会失效；失效时它们是**红**
（`_POLLUTED_* is None` 会断言失败）而不是假绿，这点是可接受的失败模式。
这种用例绿的时候不知道是真绿还是顺序碰巧，红的时候又会被当成噪音忽略 ——
比稳定的红更消耗信任。

防护：conftest 的 autouse fixture `_restore_config_module_state` 在每个用例
teardown 把 config 恢复成会话基线（**直接改属性，不 reload**，见 conftest 注释）。

本文件的用例刻意**主动制造污染且不还原**，用来证明防护真的存在 ——
没有这条，防护在不在从测试结果上完全看不出来（空转的绿）。

故障注入指纹（FI-1）
--------------------
注入方式：把 conftest `_restore_config_module_state` teardown 里的
`                setattr(cfg, name, baseline_value)` 换成 pass。

**主推复现命令（0.3s，必须带本文件一起跑）**：
```
cd backend && env -u PYTHONPATH python3 -m pytest \
    tests/test_config_module_state_restored.py \
    tests/test_llm_quota_alert_dedupe.py -q
```
**预期：5 failed, 28 passed**，固定为下面 5 条（不多不少）：

  1. test_config_module_state_restored.py::test_2_next_test_sees_baseline_restored
  2. test_config_module_state_restored.py::test_3_baseline_is_under_pytest_isolated_dir
  3. test_config_module_state_restored.py::test_6_scalar_config_value_restored
  4. test_config_module_state_restored.py::test_7_downstream_module_path_derives_from_baseline
  5. test_llm_quota_alert_dedupe.py::test_state_file_lives_under_data_dir

⚠️ **必须带污染源一起跑**（这是个真坑，别踩）：
```
env -u PYTHONPATH python3 -m pytest tests/test_llm_quota_alert_dedupe.py -q
→ 25 passed, **0 red**   ← 注入了也不红！
```
第 5 条的红色**完全来自排在它前面的污染源**：本文件 test_1（它污染 DATA_DIR；
test_5 只污染 NAV_CACHE_TTL，喂不到这条），或全量里排在前面的
test_fund_detail_ak_timeout.py:44。单独跑它一个文件，注入了也
全绿 —— 谁图省事这么验一次，会得出「这条断言是空的、删了吧」的**反向结论**。
所以指纹必须用上面那条两文件命令，不能只跑被保护的那一半。

**次级命令（可选，用于查污染面）**：
```
env -u PYTHONPATH python3 -m pytest \
    tests/test_fund_detail_ak_timeout.py \
    tests/test_llm_quota_alert_dedupe.py -q
→ 1 failed, 41 passed   ← 只有第 5 条红
```
这条证明第 5 条**不是只靠本文件才红**：ak_timeout（全量里 f < l，天然排在前面）
单独就能把它喂红。

全量里真正排在 dedupe 前面的污染源是 **2 个**（不是 3 个）：
  · 本文件 test_config_module_state_restored.py（c < l，test_1 制造）
  · test_fund_detail_ak_timeout.py（f < l，第 44 行）
两个易错点：
  · test_user_optimistic_lock.py 确实是污染源（第 48 行），但 **u > l，排在
    dedupe 后面**，喂不到它 —— 它污染的是排在自己之后的用例。别按
    "所有污染源都在它前面"推理。
  · test_broker_research_quota_degradation.py 不是污染源（见文首）。
收集顺序实测（`pytest tests/ --collect-only -q`）：
  broker(b) → config_module_state_restored(c) → fund_detail(f) → dedupe(l)
  → user_optimistic_lock(u)，即按文件名字母序。

**全量跑（`pytest tests/ -q`，约 3 分钟）不是主判据**，但它**实测过两次、结果稳定**：
```
5 failed, 2067 passed, 1 skipped, 1 xfailed, 19 subtests passed in ~179s
```
红的正是上面那 5 条。两次实测分别用**不同注入锚点**（整段恢复循环换成 `return` /
`setattr` 换成 `pass`）、**不同 HEAD**（68033f7 / 53d82ac），结论一致 ⇒ 这个 "5"
不是从两文件结果外推的，是实测值；也说明它不依赖守卫的具体写法（哪天有人把显式
循环"优化"成 `vars(cfg).update(...)`，指纹不会跟着变）。

分工：全量用于**查污染面**（判读里的"多于 5"只有全量看得出来）；
日常回归用上面那条 0.3s 的两文件命令。
⚠️ 这个 5 会随用例增减变化 —— 哪天全量红了 6 条，先按判读规则查污染面，别急着改本段。

判读（两个方向都会响，别只盯一个）：
  • 红**多于** 5 → 还有别的用例在吃 config 基线，污染面比已知的大；
  • 红**少于** 5 → 更危险，分两层看：
      - 掉到 **4**：先看清是谁没红。少的是 **test_7** → 守卫丢了跨模块真实消费方；
        少的是 **第 5 条** → 说明**两个污染源全没了**（不只是本文件被改），
        概率低但性质更严重，先查那 2 个文件（fund_detail_ak_timeout.py:44 /
        user_optimistic_lock.py:48）是不是被"顺手清理"了。
      - 掉到 **3**：test_7 和第 5 条**都没红**，那才真是"守卫只剩自证自话"。

历史：ec41999 的 commit message 里写的是「3 failed」，那是 test_6/test_7 补进来
之前的旧数 + 当时 dedupe 那条断言还处于被逼删状态。真实值是 5（software-engineer
于 68033f7 把 dedupe 的真断言加回后指出并已复核）。以本段为准，commit message 不可改。
A/B/C 三组跑法数据由 software-engineer 提供、本人独立复现（A 25 passed 0 red /
B 5 failed 0.27s / C 1 failed 41 passed 0.76s）。
"""

import importlib
import sys
import tempfile
from pathlib import Path

import config as cfg

# 在**收集期**（conftest 已把 DATA_DIR 指到会话临时目录、且尚无任何污染）
# 采下基线。整个 pytest 会话里 config 的这些值都应当恒等于它。
BASELINE_DATA_DIR = cfg.DATA_DIR
BASELINE_USERS_DIR = cfg.USERS_DIR
BASELINE_RECEIPTS_DIR = cfg.RECEIPTS_DIR
BASELINE_PUSH_ARCHIVE_DIR = cfg.PUSH_ARCHIVE_DIR

# 一个**非路径**的标量：用来证明守卫恢复的是"整份不可变快照"，而不是被
# 写死成"只恢复那 4 个路径" —— 只恢复路径的实现照样能绿 test_2，那是假的。
BASELINE_NAV_CACHE_TTL = cfg.NAV_CACHE_TTL

# 上一条用例污染后留下的值，供下一条用例反查（模块级，跨用例传递）
_POLLUTED_DATA_DIR = None
_POLLUTED_NAV_TTL = None


def test_1_pollute_config_module_without_restoring(monkeypatch, tmp_path):
    """主动制造污染：setenv + reload(config)，然后**什么都不还原**。

    monkeypatch 会在本用例结束时把 os.environ["DATA_DIR"] 还原 —— 但那正是
    事故现场：env 还原了，**reload 过的 config 模块对象不会自己还原**。
    这条用例精确复刻那 2 个污染源（fund_detail_ak_timeout.py:44 /
    user_optimistic_lock.py:48）留下的脏状态。
    """
    global _POLLUTED_DATA_DIR

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    importlib.reload(cfg)

    _POLLUTED_DATA_DIR = cfg.DATA_DIR

    # 反空转：污染必须真的发生了，否则下一条用例的"已恢复"是空转的绿
    assert cfg.DATA_DIR == tmp_path, (
        f"污染没生效：config.DATA_DIR={cfg.DATA_DIR}，期望 {tmp_path}")
    assert cfg.USERS_DIR == tmp_path / "users", cfg.USERS_DIR
    assert cfg.PUSH_ARCHIVE_DIR == tmp_path / "logs" / "pushes", cfg.PUSH_ARCHIVE_DIR

    # 安全方向确认（钉死 2026-09-13 的实测结论）：污染只会把 DATA_DIR 推向
    # 临时目录，**绝不能**推向真实生产路径 /opt/moneybag/data。
    # 哪天这条红了，说明 conftest 的 DATA_DIR 隔离被绕过，那是 P0 不是假红。
    system_tmp = str(Path(tempfile.gettempdir()).resolve())
    assert str(cfg.DATA_DIR).startswith(system_tmp), (
        f"污染方向异常：config.DATA_DIR 被推到了系统临时目录之外 "
        f"({cfg.DATA_DIR}) —— 这不是顺序依赖问题，是写脏生产数据的 P0，立刻停手")
    assert "/opt/moneybag" not in str(cfg.DATA_DIR), (
        f"污染指向了生产路径：{cfg.DATA_DIR}")


def test_2_next_test_sees_baseline_restored():
    """上一条把 config 污染了且没还原；到这条开始时必须已恢复基线。

    这条就是防护存在的证据：把 conftest 的 `_restore_config_module_state`
    注释掉，它必须转红。
    """
    assert _POLLUTED_DATA_DIR is not None, (
        "前一条用例没执行（或被跳过）—— 本用例是空转的绿")

    assert cfg.DATA_DIR == BASELINE_DATA_DIR, (
        f"config 模块未被恢复：DATA_DIR={cfg.DATA_DIR}，"
        f"基线应为 {BASELINE_DATA_DIR}，上一条用例污染成了 {_POLLUTED_DATA_DIR}")
    assert cfg.DATA_DIR != _POLLUTED_DATA_DIR, (
        f"config.DATA_DIR 仍停在上一条用例的 tmp_path（{_POLLUTED_DATA_DIR}）")

    # 派生目录必须一起恢复 —— 只恢复 DATA_DIR 是不够的
    assert cfg.USERS_DIR == BASELINE_USERS_DIR, cfg.USERS_DIR
    assert cfg.RECEIPTS_DIR == BASELINE_RECEIPTS_DIR, cfg.RECEIPTS_DIR
    assert cfg.PUSH_ARCHIVE_DIR == BASELINE_PUSH_ARCHIVE_DIR, cfg.PUSH_ARCHIVE_DIR


def test_3_baseline_is_under_pytest_isolated_dir():
    """基线本身必须落在 pytest 隔离目录里，而不是生产路径。

    这条锁住「恢复的目标值是安全的」：如果基线自己就是 /opt/moneybag/data，
    那"恢复成功"反而意味着后续用例会写生产数据。
    """
    assert "/opt/moneybag" not in str(BASELINE_DATA_DIR), (
        f"基线指向生产路径：{BASELINE_DATA_DIR}")
    assert BASELINE_DATA_DIR != Path("/opt/moneybag/data"), BASELINE_DATA_DIR
    assert cfg.DATA_DIR == BASELINE_DATA_DIR, (
        f"本用例开始时 config 应处于基线状态，实际 {cfg.DATA_DIR}")


def _load_conftest_module():
    """拿到**已加载的** conftest 模块对象（不是重新 import 一份）。

    必须取 sys.modules 里那一份：pytest 用的是同一个对象，守卫 fixture 读写的
    就是这个对象的模块级全局。重新 import 会拿到一个全新副本，那里的
    _CONFIG_BASELINE 恒为空，断言就成了自欺欺人的绿。
    """
    mod = sys.modules.get("conftest")
    if mod is None:
        mod = importlib.import_module("conftest")
    assert mod is not None, "拿不到 conftest 模块，守卫状态无从检查"
    return mod


def test_4_guard_baseline_really_captured_at_conftest_import():
    """守卫不能"静默空转"：基线必须真的采到了，且是在 conftest 顶层采的。

    这条专门防两类假绿：
      • conftest 顶层 `import config` 失败 → 守卫只打印一行警告就空转，
        全量照样全绿，等于没有守卫；
      • 采集时机被人改回"第一个用例 setup 时才采" → 基线可能已经带污染，
        守卫会把每个用例都勤勤恳恳地恢复成那个脏值。
    """
    conftest = _load_conftest_module()

    assert conftest._CONFIG_BASELINE_PHASE == "conftest-import", (
        f"config 基线采集时机退化成 {conftest._CONFIG_BASELINE_PHASE!r} —— "
        f"必须回到 conftest 顶层（早于任何 test_*.py 被 import）采集，"
        f"晚了基线本身可能已被 reload 污染")
    assert conftest._CONFIG_BASELINE_READY is True, "守卫基线未就绪（空转）"

    # 必须真的装了东西，且覆盖到关键字段（写死字段名，不从常量推导）
    required = ("DATA_DIR", "USERS_DIR", "RECEIPTS_DIR", "PUSH_ARCHIVE_DIR",
                "NAV_CACHE_TTL")
    for key in required:
        assert key in conftest._CONFIG_BASELINE, (
            f"守卫基线缺少 {key} —— config 该字段不存在或类型不在可恢复白名单里")

    # 与 collection 期采到的值交叉核对：两者必须是同一次解析的产物
    assert conftest._CONFIG_BASELINE["DATA_DIR"] == BASELINE_DATA_DIR, (
        f"守卫基线 DATA_DIR={conftest._CONFIG_BASELINE['DATA_DIR']} 与"
        f" collection 期看到的 {BASELINE_DATA_DIR} 不一致")
    assert conftest._CONFIG_BASELINE["NAV_CACHE_TTL"] == BASELINE_NAV_CACHE_TTL


def test_5_pollute_a_scalar_config_value_without_restoring():
    """再制造一次污染，这次污染**非路径标量**，且同样什么都不还原。

    目的：证明守卫恢复的是整份不可变快照。一个"只恢复 4 个路径常量"的
    偷懒实现能通过 test_2，但过不了下面 test_6。
    """
    global _POLLUTED_NAV_TTL

    _POLLUTED_NAV_TTL = cfg.NAV_CACHE_TTL
    cfg.NAV_CACHE_TTL = 1  # 直接改属性，不用 monkeypatch —— 精确模拟 reload 残留

    assert cfg.NAV_CACHE_TTL == 1, "标量污染没生效，下一条用例是空转的绿"
    assert cfg.NAV_CACHE_TTL != BASELINE_NAV_CACHE_TTL, (
        "污染值与基线相同，下一条用例分辨不出来")


def test_6_scalar_config_value_restored():
    """上一个用例污染的标量，到这条开始时也必须已恢复基线。"""
    assert _POLLUTED_NAV_TTL is not None, (
        "前一条用例没执行（或被跳过）—— 本用例是空转的绿")
    assert cfg.NAV_CACHE_TTL == BASELINE_NAV_CACHE_TTL, (
        f"config.NAV_CACHE_TTL 未恢复：{cfg.NAV_CACHE_TTL}，"
        f"基线应为 {BASELINE_NAV_CACHE_TTL}")


def test_7_downstream_module_path_derives_from_baseline():
    """真实消费方视角（跨模块）：去重状态文件必须落在**基线** DATA_DIR 下。

    这条就是当初被顺序依赖逼到删掉断言的那一条 ——
    `test_llm_quota_alert_dedupe.py::test_state_file_lives_under_data_dir`
    原本断言 `qa.ALERT_STATE_FILE.parent == config.DATA_DIR`，因为会被
    reload 污染搞成随机红绿，前人被逼改成只断言文件名（覆盖丢失）。

    放在本文件里跑，且排在本文件两次污染之后：守卫在，它就绿；守卫没了，
    config.DATA_DIR 仍停在 test_1 的 tmp_path，它立刻红。
    这样守卫就有一个**真实的跨模块消费方**，而不是只守护自己的守卫用例。

    注意 `qa.ALERT_STATE_FILE` 由 conftest 的 `_isolate_alert_state_file`
    固定在会话级 `_PYTEST_DATA_DIR` 上（刻意不挂 config.DATA_DIR —— 那正是
    会被 reload 污染的变量）；本条断言的正是「两者应当相等」，即 DATA_DIR
    被正确恢复时二者必然同源。
    """
    from services import llm_quota_alert as qa

    assert qa.ALERT_STATE_FILE.parent == cfg.DATA_DIR, (
        f"去重状态文件目录 {qa.ALERT_STATE_FILE.parent} 与 config.DATA_DIR "
        f"{cfg.DATA_DIR} 不同源 —— config 没被恢复，或 ALERT_STATE_FILE 被改挂")
    assert cfg.DATA_DIR == BASELINE_DATA_DIR, (
        f"config.DATA_DIR 未回到基线：{cfg.DATA_DIR} != {BASELINE_DATA_DIR}")


def test_this_file_has_no_skip_markers():
    """这些用例是顺序依赖假红的回归网，被 skip 掉等于网破了个洞还显示绿色。

    标记名拆成字符串拼接，避免本函数自己的源码把待查字符串带进来造成自匹配。
    """
    src = Path(__file__).read_text(encoding="utf-8")
    markers = ("pytest.mark." + "skip", "pytest." + "skip(", "skip" + "if(")
    for marker in markers:
        assert marker not in src, f"本文件出现了 {marker} —— 回归网不允许被跳过"

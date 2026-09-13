"""wxwork_push 配置「延迟读取」故障注入用例（FIX 2026-09-13 事故第二层防护）。

事故背景
--------
2026-09-13 生产服务器跑 pytest，测试 mock 造的假 HTTP 402 触发了**真实的**
企微告警推送（commit 519045b）。修的时候加了两层防护：
  1. 测试环境短路（services/llm_quota_alert.py 的 _in_test_mode）
  2. conftest 卡死 wxwork_push._http_client.get/post（唯一 HTTP 出口）

但第 2 层有个洞：`is_configured()` 读的是 **import 期就冻结的模块级常量**
（旧代码 `_CORP_ID = os.getenv("WXWORK_CORP_ID", "")`），conftest 那个
`monkeypatch.delenv("WXWORK_CORP_ID")` 的 autouse fixture 对它**完全无效** ——
只要模块曾在密钥还在的环境里被 import 过一次，is_configured() 就恒为 True。

本次修复：四个配置改为调用时实时 os.getenv（同时保留模块级名字兼容层，见
services/wxwork_push.py 的 _read_config 注释）。

为什么要用**子进程**做故障注入
------------------------------
缺陷的成立条件是「模块在密钥还在的时候被 import」。pytest 进程里 wxwork_push
在 collection 阶段就被 import 了，之后再怎么 setenv/delenv 都复现不出这个
条件 —— 除非 importlib.reload，但 reload 会永久改写 sys.modules 里的共享模块
（_IMPORT_TIME_ENV 快照、_http_client 单例都会变），造成顺序依赖的脏状态。
子进程能精确构造「import 时环境变量是什么」，且不污染主进程：这是唯一既能
忠实复现、又不留副作用的方式。

铁律：故障注入必须成对且互斥可分辨
----------------------------------
  PHASE1（env 正常，模拟生产）  is_configured() == True
  PHASE2（env 清空，模拟 conftest）is_configured() == False
同一份模块对象、只改环境变量，两个结果必须相反 —— 否则断言无意义。
另外额外抓一个**缺陷指纹** FROZEN_STYLE（旧实现的读法）：
PHASE2 时它必须**仍然为 True**，证明「env 确实被清了、但旧实现读不到」，
即这条用例真的能抓住这个 bug（有人回滚修复 → PHASE2_CONFIGURED 变 True → 转红）。
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from services import wxwork_push as wp

BACKEND_DIR = Path(__file__).resolve().parents[1]

# 模拟生产的假凭据。刻意用非数字 AGENT_ID 以外的形式：AGENT_ID 必须是数字，
# 因为 _send_raw 里有 int(_agent_id())。
FAKE_ENV = {
    "WXWORK_CORP_ID": "SIM-CORP-0001",
    "WXWORK_SECRET": "SIM-SECRET-0001",
    "WXWORK_AGENT_ID": "1000002",
}

WXWORK_CONFIG_KEYS = (
    "WXWORK_CORP_ID", "WXWORK_SECRET", "WXWORK_AGENT_ID", "WXWORK_USER_ID",
)

# 探针脚本：在**独立进程**里完成「import 时 env 在 → 运行中清空 env」的全过程。
# 每个 print 都带 PROBE 前缀，便于解析且不会被模块自身的日志干扰。
_PROBE_SRC = '''
import os
import sys

sys.path.insert(0, __BACKEND_DIR__)

from services import wxwork_push as wp


def _frozen_style():
    """旧实现的读法：直接读 import 期冻结的模块级常量。

    这是**缺陷指纹**本身 —— 修复是否到位，看的就是它和新实现是否分道扬镳。
    """
    return bool(wp._CORP_ID and wp._SECRET and wp._AGENT_ID)


# ---- PHASE 1：env 正常（等价于生产机 / uvicorn 进程）----
print("PROBE PHASE1_CONFIGURED=%r" % wp.is_configured())
print("PROBE PHASE1_FROZEN=%r" % _frozen_style())
print("PROBE PHASE1_CORP=%r" % wp._corp_id())

# ---- PHASE 2：清空 env（等价于 conftest 的 _clear_secret_env_pollution）----
for _k in ("WXWORK_CORP_ID", "WXWORK_SECRET", "WXWORK_AGENT_ID", "WXWORK_USER_ID"):
    os.environ.pop(_k, None)

print("PROBE PHASE2_CONFIGURED=%r" % wp.is_configured())
print("PROBE PHASE2_FROZEN=%r" % _frozen_style())
print("PROBE PHASE2_CORP=%r" % wp._corp_id())
print("PROBE PHASE2_USER=%r" % wp._default_user_id())
'''


def _run_probe(tmp_path) -> dict:
    """在独立进程里跑探针，返回解析后的键値字典。

    子进程与本项目测试跑法保持一致（清掉 PYTHONPATH，避免误挂到别的项目）。
    """
    probe_file = tmp_path / "wxwork_config_probe.py"
    probe_file.write_text(
        _PROBE_SRC.replace("__BACKEND_DIR__", repr(str(BACKEND_DIR))),
        encoding="utf-8",
    )

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.update(FAKE_ENV)  # 「import 时密钥还在」—— 缺陷的成立条件
    env.pop("MONEYBAG_TEST_MODE", None)

    proc = subprocess.run(
        [sys.executable, str(probe_file)],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"探针进程失败（rc={proc.returncode}）：\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )

    parsed: dict = {}
    for line in proc.stdout.splitlines():
        if line.startswith("PROBE ") and "=" in line:
            key, _, value = line[len("PROBE "):].partition("=")
            parsed[key] = value

    missing = {"PHASE1_CONFIGURED", "PHASE1_FROZEN", "PHASE1_CORP",
               "PHASE2_CONFIGURED", "PHASE2_FROZEN", "PHASE2_CORP",
               "PHASE2_USER"} - set(parsed)
    assert not missing, f"探针输出不全，缺 {sorted(missing)}：\n{proc.stdout}"

    parsed["_stdout"] = proc.stdout
    return parsed


@pytest.fixture(scope="module")
def probe(tmp_path_factory) -> dict:
    """整个模块跑一次探针（两次注入共用同一份实测数据，保证成对可比）。"""
    return _run_probe(tmp_path_factory.mktemp("wxwork_probe"))


# ── 注入 1：conftest 清掉 env 后，is_configured() 必须为 False ──────────
def test_injection1_env_cleared_must_disable_wecom(probe):
    """缺陷指纹：旧实现在 PHASE2 仍返回 True，修复后必须返回 False。"""
    assert probe["PHASE2_CONFIGURED"] == "False", (
        "配置层防护失效：清空 WXWORK_* 环境变量后 is_configured() 仍为真 —— "
        "配置又被 import 期冻结了，conftest 的 delenv 管不到它。"
        f"探针原文：\n{probe['_stdout']}")
    assert probe["PHASE2_CORP"] == "''", (
        f"清空 env 后 _corp_id() 应为空串，实际 {probe['PHASE2_CORP']}"
        f"｜探针原文：\n{probe['_stdout']}")

    # 反空转：旧实现的读法在同一时刻必须**仍然为真**。
    # 若它也是 False，说明 env 根本没被清（或模块压根没读到凭据），
    # 上面那条 False 就是空转的绿 —— 什么都没证明。
    assert probe["PHASE2_FROZEN"] == "True", (
        "缺陷指纹消失：旧实现（import 期冻结）读到的也是未配置 —— 说明本用例"
        "没有真正复现「import 时密钥还在」这个前提，上面那条断言是空转的绿。"
        f"探针原文：\n{probe['_stdout']}")


# ── 注入 2：env 正常时，is_configured() 必须为 True ────────────────────
def test_injection2_env_present_must_enable_wecom(probe):
    """与注入 1 成对：只改环境变量、不改别的，结果必须相反。"""
    assert probe["PHASE1_CONFIGURED"] == "True", (
        "延迟读取改坏了生产语义：env 正常时 is_configured() 应为真。"
        f"探针原文：\n{probe['_stdout']}")
    assert probe["PHASE1_CORP"] == repr(FAKE_ENV["WXWORK_CORP_ID"]), (
        f"_corp_id() 应实时读到 {FAKE_ENV['WXWORK_CORP_ID']}，"
        f"实际 {probe['PHASE1_CORP']}｜探针原文：\n{probe['_stdout']}")


def test_injection_pair_is_mutually_exclusive(probe):
    """两条注入必须互斥可分辨 —— 否则「成对」只是自我安慰。

    同一份模块对象、同一进程，唯一变量是环境变量，两个结果必须一真一假。
    """
    assert probe["PHASE1_CONFIGURED"] != probe["PHASE2_CONFIGURED"], (
        "注入 1/2 分辨不出差异：PHASE1 与 PHASE2 结果相同 "
        f"({probe['PHASE1_CONFIGURED']}) —— 这组用例证明不了任何事。"
        f"探针原文：\n{probe['_stdout']}")
    assert probe["PHASE2_FROZEN"] != probe["PHASE2_CONFIGURED"], (
        "新实现与旧实现行为完全一致 —— 修复没生效（或已被回滚）。"
        f"探针原文：\n{probe['_stdout']}")


# ── 生产进程行为不变（uvicorn / cron，env 正常读取）────────────────────
def test_production_process_behaviour_unchanged(probe):
    """生产语义回归网：env 未被改动时，行为必须与旧实现一字不差。

    覆盖了三件事：
      1. is_configured() 仍为真（PHASE1）；
      2. _corp_id() 取到的是 env 里的真值，不是默认值；
      3. 清空 env 后默认接收人回落到 '@all'（旧实现 os.getenv 的默认值）。
    """
    assert probe["PHASE1_CONFIGURED"] == "True", probe["_stdout"]
    assert probe["PHASE1_FROZEN"] == "True", (
        "生产态下新旧实现必须一致 —— 旧实现读常量为真，新实现读 env 却为假，"
        f"说明生产语义被改坏了。探针原文：\n{probe['_stdout']}")
    assert probe["PHASE2_USER"] == repr("@all"), (
        f"WXWORK_USER_ID 未设置时应回落 '@all'，实际 {probe['PHASE2_USER']}"
        f"｜探针原文：\n{probe['_stdout']}")


def test_agent_id_is_int_compatible_and_lazy(monkeypatch):
    """_send_raw 里有 int(_agent_id())：延迟读取不能把它变成非数字或默认值。"""
    monkeypatch.setenv("WXWORK_CORP_ID", FAKE_ENV["WXWORK_CORP_ID"])
    monkeypatch.setenv("WXWORK_SECRET", FAKE_ENV["WXWORK_SECRET"])
    monkeypatch.setenv("WXWORK_AGENT_ID", FAKE_ENV["WXWORK_AGENT_ID"])

    assert wp.is_configured() is True
    assert int(wp._agent_id()) == 1000002, wp._agent_id()

    # 反空转：同一个模块对象，delenv 之后必须立刻变未配置（证明是实时读）
    for key in WXWORK_CONFIG_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert wp.is_configured() is False, "delenv 后仍未配置 → 延迟读取没生效"
    assert wp._corp_id() == ""
    assert wp._default_user_id() == "@all"


def test_default_user_id_reads_env_lazily(monkeypatch):
    """WXWORK_USER_ID 同样延迟读取：设了就是设了，没设回落 '@all'。"""
    monkeypatch.setenv("WXWORK_USER_ID", "LeiJiang")
    assert wp._default_user_id() == "LeiJiang"

    monkeypatch.delenv("WXWORK_USER_ID", raising=False)
    assert wp._default_user_id() == "@all"


# ── 兼容层守卫：模块级名字不能被"清理"掉 ───────────────────────────────
def test_module_level_names_override_still_honored(monkeypatch):
    """老写法 monkeypatch.setattr(wp, "_CORP_ID", ...) 必须继续有效。

    这不是洁癖：backend/tests/test_qa_egress_independent.py::prod_like_wecom
    和 backend/tests/qa_egress_probe.py 就是靠覆盖这四个常量来伪造「生产已配置」。
    一旦有人为了"代码干净"删掉模块级名字，那些用例会**静默退化** ——
    raising=False 的 setattr 不报错，但 is_configured() 恒 False，
    于是"零推送"变成一道空转的绿。这条用例是那个退化的警报器。
    """
    for key in WXWORK_CONFIG_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert wp.is_configured() is False, "前置条件：env 已清空时应为未配置"

    monkeypatch.setattr(wp, "_CORP_ID", "QA-FAKE-CORP", raising=False)
    monkeypatch.setattr(wp, "_SECRET", "QA-FAKE-SECRET", raising=False)
    monkeypatch.setattr(wp, "_AGENT_ID", "QA-FAKE-AGENT", raising=False)

    assert wp.is_configured() is True, (
        "兼容层失效：覆盖模块级 _CORP_ID/_SECRET/_AGENT_ID 后 is_configured() "
        "仍为假 —— test_qa_egress_independent.py 的 prod_like_wecom 会退化成空转的绿")
    assert wp._corp_id() == "QA-FAKE-CORP"
    assert wp._secret() == "QA-FAKE-SECRET"
    assert wp._agent_id() == "QA-FAKE-AGENT"

    # 四个模块级名字必须真实存在（不是只留了个注释），且默认值与 env 一致
    for name in ("_CORP_ID", "_SECRET", "_AGENT_ID", "_USER_ID"):
        assert hasattr(wp, name), f"模块级名字 {name} 被删掉了，外部覆盖会静默失效"


# ── 反空转：本文件不得出现 skip ────────────────────────────────────────
def test_this_file_has_no_skip_markers():
    """这些用例是缺陷的回归网，被 skip 掉等于网破了个洞还显示绿色。

    标记名拆成字符串拼接，避免本函数自己的源码把待查字符串带进来造成
    自匹配（第一版就是这样误报了自己的）。
    """
    src = Path(__file__).read_text(encoding="utf-8")
    markers = ("pytest.mark." + "skip", "pytest." + "skip(", "skip" + "if(")
    for marker in markers:
        assert marker not in src, f"本文件出现了 {marker} —— 回归网不允许被跳过"

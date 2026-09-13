#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
告警去重状态机守卫（2026-09-13 遗留缺口：生产上 llm_alert_state.json 从未出现）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
现象与判定
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
生产服务器 `/opt/moneybag/data/llm_alert_state.json` **从未存在过**。该文件是
`maybe_alert_quota()` 的当日去重状态（key = `alert_type|model`，value = 日期）。

排查结论：**不是 bug，是「去重从未被真实触发过」**。证据链：

1. 状态只在**推送循环跑完之后**才写（`_mark_sent_today` 在最后一行）。生产上
   唯一一次真实推送是 2026-09-13 那条假告警，而它来自 **pytest 进程** ——
   conftest 把 DATA_DIR 隔离到临时目录，状态文件写进了 tmp，永远进不了
   /opt/moneybag/data；2026-09-13 之后测试环境短路又直接 return，连读都不读。
   ⇒ 「文件不存在」与「代码写不进去」是两回事，前者是预期结果。

2. 写入路径本身是好的：`config.py` 在 import 期就 `DATA_DIR.mkdir(parents=True,
   exist_ok=True)`，目录不可写的话**服务根本起不来**；且生产线只要真推一次，
   写文件的代码路径与本文件
   `test_production_like_process_writes_state_file_and_suppresses_second_push`
   走的是同一条（该用例在**非 pytest 子进程**里复现生产进程，实测确认状态文件
   被写出、第二次调用被拦）。

3. 去重逻辑本身也没坏：`test_llm_quota_alert_classify.py::test_dedupe_is_per_model`
   一直在跑且为绿。

所以本文件的任务不是修 bug，而是把去重语义**钉死**，并且每条断言都带
「反空转」凭据 —— 只断言「第二次没推送」是不合格的绿：那也可能是被别的东西
（测试环境短路、免打扰窗口、推送未配置）提前 return 掉的。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
反空转凭据（每条用例都必须至少命中一条）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
A. **守卫确实放行了**：fixture 里断言 `_is_production_sender(假发送器) is False`
   —— 否则「测试环境短路」会把调用吞掉，所有"没推送"的断言全是空的。
B. **状态文件确实被写了**：不能只看推送次数，必须落盘校验 JSON 内容。
C. **删掉状态文件就得重新推送**：证明第二次是被**去重**拦下的，不是被别的
   分支拦下的（这是本文件最关键的一条）。
D. **陈旧日期不生效**：文件存在但日期是昨天 ⇒ 必须重推（证明比的是日期，
   不是文件存在与否）。

⚠️ 全程离线：发送器一律换成进程内假件，任何用例都不得触达真实企微。
"""
import json
import os
import subprocess
import sys
import time
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import llm_quota_alert as qa  # noqa: E402
from services import wxwork_push as wp  # noqa: E402

# P0（确证欠费）：不受免打扰窗口限制 —— 用例在任何挂钟时刻跑都必须同结果。
# 选 P0 而不是 P2，是为了让去重守卫不依赖"恰好在白天跑测试"（本项目踩过的坑）。
P0_ARREARS_BODY = (
    '{"error": {"code": "PaymentRequired", "message": "Payment Required"}}'
)
# P2（模型未开通）：受免打扰窗口约束，用来覆盖「窗口判断之后」的去重分支。
P2_MODEL_NOT_OPEN_BODY = (
    '{"error":{"code":"ModelNotOpen","message":"Your account 2127949875 has '
    'not activated the model doubao-seed-2-1-turbo-260628."}}'
)

MODEL_TURBO = "doubao-seed-2-1-turbo-260628"
MODEL_PRO = "doubao-seed-2-1-pro-260628"
EXPECTED_UIDS = ["LeiJiang", "BuLuoGeLi"]


def _install_fake_sender(monkeypatch, pushed: list):
    """把企微发送函数换成**进程内假件**，绝不触达网络。

    为什么必须是「非生产实现」：
      入口短路（L1）的判据是「发送函数是否定义在 services.wxwork_push 里」。
      这里装进去的函数定义在**本测试模块**，守卫会放行 —— 于是去重逻辑在测试
      进程里真的会被执行到。若反过来伪装成生产实现，所有「没推送」的断言都会
      变成"被短路拦下"的空转绿。
    """
    def _fake(uid, content, title=""):
        pushed.append({"uid": uid, "title": title, "content": content})
        return {"ok": True, "data": {"errcode": 0}}

    monkeypatch.setattr(wp, "is_configured", lambda: True, raising=True)
    monkeypatch.setattr(wp, "send_daily_report_to", _fake, raising=True)


def _install_scripted_sender(monkeypatch, pushed, outcomes):
    """按调用顺序编排每次 send 的返回值（或要抛出的异常）。

    用于模拟「企微抖动 / 挂掉」：outcomes 里放 `{"ok": False}` 表示未送达，
    放异常实例表示 send 直接抛错。用完后默认返回 `{"ok": True}`，这样"多推了"
    会以"不该出现的成功"形式暴露出来，而不是悄悄变绿。
    """
    def _fake(uid, content, title=""):
        pushed.append({"uid": uid, "title": title, "content": content})
        outcome = outcomes.pop(0) if outcomes else {"ok": True}
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(wp, "is_configured", lambda: True, raising=True)
    monkeypatch.setattr(wp, "send_daily_report_to", _fake, raising=True)


def _dedupe_key(model: str = MODEL_TURBO) -> str:
    return f"doubao_balance_exhausted|{model}"


def _shift_failed_marker(state_file, dedupe_key: str, seconds: float) -> float:
    """把失败节流时间戳**往前挪**（数值平移，不改存储格式）。

    用"挪时间戳"而不是"改小节流窗口"，是为了让用例跑的是真实窗口常量
    （30 分钟）的真实算术，而不是把守卫配置改成 0 后自己骗自己。
    """
    state = json.loads(state_file.read_text(encoding="utf-8"))
    fk = qa._failed_key(dedupe_key)
    assert isinstance(state.get(fk), (int, float)), \
        f"失败节流应存数值时间戳（改了存储格式要同步改本用例）：{state}"
    state[fk] = state[fk] - seconds
    state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return state[fk]


@pytest.fixture
def dedupe_env(monkeypatch, tmp_path):
    """统一的去重测试环境：独立状态文件 + 进程内假发送器 + 反空转前置断言。

    反空转 A：**每个用到本 fixture 的用例都先证明守卫放行了**。
    少了这一步，`pushed == []` 到底是「去重拦下」还是「入口短路拦下」说不清。
    """
    state_file = tmp_path / "llm_alert_state.json"
    monkeypatch.setattr(qa, "ALERT_STATE_FILE", state_file, raising=True)

    pushed = []
    _install_fake_sender(monkeypatch, pushed)

    # —— 反空转 A：守卫必须放行，否则下面的断言全是空的 ——
    assert qa._in_test_mode() is True, "本组用例跑在测试进程里，前提成立"
    assert qa._is_production_sender(wp.send_daily_report_to) is False, (
        "假发送器被判成了生产实现 ⇒ 入口短路会拦下调用，"
        "本文件所有用例将变成空转的绿"
    )

    class _Env:
        def __init__(self):
            self.state_file = state_file
            self.pushed = pushed

        def read_state(self) -> dict:
            return json.loads(state_file.read_text(encoding="utf-8"))

        def alert(self, model: str = MODEL_TURBO, module: str = "t",
                  body: str = P0_ARREARS_BODY, status: int = 402):
            qa.maybe_alert_quota("doubao", status, body, model=model, module=module)

    return _Env()


def _assert_is_p0() -> None:
    """反空转：本组主用例的前提是注入的告警确为 P0（不受免打扰窗口影响）。"""
    alert_type, _, _ = qa.classify_llm_error_detail("doubao", 402, P0_ARREARS_BODY)
    assert qa.alert_priority(alert_type) == "P0", (
        f"注入前提失效：{alert_type} 不是 P0，用例会随挂钟时间变脸"
    )


# ============================================================
# 核心语义：第一次推送 → 落盘；同 key 第二次 → 被拦
# ============================================================
def test_first_alert_pushes_and_writes_state_file(dedupe_env, capsys):
    """第一次告警：推送 2 个人，并把 dedupe_key=今天 写进状态文件。

    反空转 B：不只看 pushed，必须落盘校验 JSON —— 否则「去重看起来生效」可能
    只是"别的东西没推"。
    """
    _assert_is_p0()
    dedupe_env.alert(model=MODEL_TURBO)

    assert [p["uid"] for p in dedupe_env.pushed] == EXPECTED_UIDS, \
        f"应推给 {EXPECTED_UIDS}：{dedupe_env.pushed}"
    assert dedupe_env.state_file.exists(), "推送成功后必须落盘去重状态"

    state = dedupe_env.read_state()
    assert state == {f"doubao_balance_exhausted|{MODEL_TURBO}": date.today().isoformat()}, \
        f"状态文件内容不符预期（key 应为 alert_type|model，value 应为今天）：{state}"

    out = capsys.readouterr().out
    assert "✅ 已推送告警" in out, f"未打印推送成功日志：{out}"


def test_second_same_key_call_does_not_push(dedupe_env, capsys):
    """同 key 第二次：不再推送（当日去重生效）。"""
    _assert_is_p0()
    dedupe_env.alert(model=MODEL_TURBO)
    first_count = len(dedupe_env.pushed)
    assert first_count == 2, f"反空转：第一次必须真的推了 2 条：{dedupe_env.pushed}"

    dedupe_env.alert(model=MODEL_TURBO)

    assert len(dedupe_env.pushed) == first_count, \
        f"同 key 当日重复告警未被去重：{dedupe_env.pushed}"
    out = capsys.readouterr().out
    assert out.count("✅ 已推送告警") == 1, \
        f"推送成功日志应只出现 1 次（第二次被去重拦下）：{out}"


# ============================================================
# 反空转 C：删掉状态文件 ⇒ 必须重新推送
# ============================================================
def test_deleting_state_file_re_enables_push(dedupe_env, capsys):
    """★ 最关键的一条：删掉状态文件后同 key 会再次推送。

    它证明上一条的「第二次没推」是**去重**拦下的：
      • 若是被测试环境短路拦的 —— 删文件前后都不会推，这条会红；
      • 若是被免打扰窗口拦的 —— 同理，这条会红（本用例用 P0，不受窗口约束）；
      • 若是被"企微未配置"拦的 —— 第一次就不会有 2 条推送，前置断言已挡掉。
    """
    _assert_is_p0()
    dedupe_env.alert(model=MODEL_TURBO)
    assert len(dedupe_env.pushed) == 2
    assert dedupe_env.state_file.exists(), "反空转 B：第一次推送必须已落盘"

    dedupe_env.state_file.unlink()

    dedupe_env.alert(model=MODEL_TURBO)

    assert len(dedupe_env.pushed) == 4, (
        f"状态文件被删后同 key 应重新推送 —— 若仍为 2，说明上一条用例的"
        f"'没推送'根本不是去重拦的（空转的绿）：{dedupe_env.pushed}"
    )
    assert dedupe_env.state_file.exists(), "重新推送后应再次落盘"
    assert dedupe_env.read_state() == {
        f"doubao_balance_exhausted|{MODEL_TURBO}": date.today().isoformat()
    }
    out = capsys.readouterr().out
    assert out.count("✅ 已推送告警") == 2, f"两次推送都应有成功日志：{out}"


# ============================================================
# 反空转 D：比的是日期，不是文件存在与否
# ============================================================
def test_stale_date_does_not_suppress_push(dedupe_env):
    """状态文件在，但日期是昨天 ⇒ 照常推送，并把日期刷新成今天。"""
    _assert_is_p0()
    key = f"doubao_balance_exhausted|{MODEL_TURBO}"
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    dedupe_env.state_file.write_text(
        json.dumps({key: yesterday}, ensure_ascii=False), encoding="utf-8"
    )

    dedupe_env.alert(model=MODEL_TURBO)

    assert len(dedupe_env.pushed) == 2, \
        f"昨天的去重记录不应拦下今天的告警：{dedupe_env.pushed}"
    assert dedupe_env.read_state()[key] == date.today().isoformat(), \
        "推送后应把日期刷新为今天"


def test_today_date_suppresses_push(dedupe_env):
    """成对用例：日期是**今天**才拦 —— 与上一条构成完整判据。

    只写"陈旧日期要重推"会让守卫越来越松（改成永不过期也绿），必须同时锁住
    "今天已推过就必须拦"。
    """
    _assert_is_p0()
    key = f"doubao_balance_exhausted|{MODEL_TURBO}"
    dedupe_env.state_file.write_text(
        json.dumps({key: date.today().isoformat()}, ensure_ascii=False),
        encoding="utf-8",
    )

    dedupe_env.alert(model=MODEL_TURBO)

    assert dedupe_env.pushed == [], f"今天已推过的 key 必须被拦下：{dedupe_env.pushed}"


# ============================================================
# key 设计：alert_type|model
# ============================================================
def test_dedupe_key_is_per_model(dedupe_env):
    """同 alert_type 下 turbo 与 pro 各自独立，互不屏蔽（key 设计合理）。"""
    _assert_is_p0()
    dedupe_env.alert(model=MODEL_TURBO)
    dedupe_env.alert(model=MODEL_TURBO)
    assert len(dedupe_env.pushed) == 2, "同模型第二次应被去重"

    dedupe_env.alert(model=MODEL_PRO)
    assert len(dedupe_env.pushed) == 4, \
        f"换模型应视为另一件事继续推送：{dedupe_env.pushed}"

    state = dedupe_env.read_state()
    assert set(state) == {
        f"doubao_balance_exhausted|{MODEL_TURBO}",
        f"doubao_balance_exhausted|{MODEL_PRO}",
    }, f"两个模型应各占一个 key：{state}"


def test_dedupe_key_uses_placeholder_for_empty_model(dedupe_env):
    """model 为空时 key 用 '-' 占位，且空/非空视为两个不同的 key。

    锁这个是为了防止哪天改成 `f"{alert_type}|{model}"`（尾随竖线）后，
    老状态文件里的 key 全部失配 —— 去重静默失效正是本次要防的那种衰减。
    """
    _assert_is_p0()
    dedupe_env.alert(model="")
    assert len(dedupe_env.pushed) == 2

    state = dedupe_env.read_state()
    assert "doubao_balance_exhausted|-" in state, \
        f"model 为空时 key 应为 'alert_type|-'：{state}"

    dedupe_env.alert(model=MODEL_TURBO)
    assert len(dedupe_env.pushed) == 4, \
        "空 model 与具体 model 必须视为不同 key，不能互相屏蔽"


# ============================================================
# 状态文件损坏 / 不可写
# ============================================================
def test_corrupt_state_file_is_ignored_and_repaired(dedupe_env):
    """状态文件是坏 JSON 时必须降级为空并重推，绝不能让告警永久静默。

    「宁可重复推一条，不可因为一个坏文件再也不推」—— 去重是优化，不是闸门。
    """
    _assert_is_p0()
    dedupe_env.state_file.write_text("{这不是合法 JSON", encoding="utf-8")

    dedupe_env.alert(model=MODEL_TURBO)

    assert len(dedupe_env.pushed) == 2, \
        f"坏状态文件不应拦住告警：{dedupe_env.pushed}"
    # 且要被修好：否则每次调用都会推（去重永久失效）
    assert dedupe_env.read_state() == {
        f"doubao_balance_exhausted|{MODEL_TURBO}": date.today().isoformat()
    }


def test_save_failure_is_logged_and_costs_dedupe(dedupe_env, monkeypatch, capsys):
    """写状态文件失败时：不抛异常（不拖垮主流程），但**必须留下日志**。

    这是 `_save_state()` 那条 `except Exception: print(...)` 的现状刻画：
      • 不抛 —— 告警主流程不能被状态落盘拖死（合理）；
      • 打印 —— 静默吞掉会让"去重其实一直没生效"这种故障永远查不出来。
    同时诚实记录代价：写盘失败 ⇒ 本次去重额度丢失 ⇒ 同 key 会重复推送。

    构造方式：让状态文件的**父目录是一个普通文件**，`mkdir(parents=True)`
    必然抛 FileExistsError —— 不依赖 chmod / 是否 root，任何环境都成立。
    """
    _assert_is_p0()
    blocker = dedupe_env.state_file.parent / "blocker"
    blocker.write_text("i am a file, not a dir", encoding="utf-8")
    monkeypatch.setattr(qa, "ALERT_STATE_FILE", blocker / "llm_alert_state.json",
                        raising=True)

    dedupe_env.alert(model=MODEL_TURBO)  # 不得抛异常

    assert len(dedupe_env.pushed) == 2, "写盘失败不应影响推送本身"
    out = capsys.readouterr().out
    assert "[QUOTA_ALERT] save state failed" in out, \
        f"写盘失败必须留痕，静默吞掉等于去重永久失效还查不到：{out}"

    # 代价：去重额度丢失 ⇒ 同 key 重复推送（现状语义，改动前先钉住）
    dedupe_env.alert(model=MODEL_TURBO)
    assert len(dedupe_env.pushed) == 4, \
        "写盘失败时同 key 会重复推送（当前实现），此断言用于钉住该语义"


# ============================================================
# 推送未发生 ⇒ 绝不落盘
# ============================================================
def test_no_state_file_written_when_wxwork_not_configured(dedupe_env, monkeypatch):
    """企微未配置时：既不推送，也**不能**写状态文件。

    落盘 = "今天这份告警已经发过了"。没发出去却记账，等于把这条告警从当天
    的告警流里永久抹掉 —— 比重复推送危险得多。
    """
    _assert_is_p0()
    monkeypatch.setattr(wp, "is_configured", lambda: False, raising=True)

    dedupe_env.alert(model=MODEL_TURBO)

    assert dedupe_env.pushed == [], "企微未配置时不应推送"
    assert not dedupe_env.state_file.exists(), \
        "没发出去的告警绝不能记进去重状态"


def test_no_state_file_written_outside_push_window(dedupe_env, monkeypatch):
    """P2 在免打扰时段被推迟时同样不得落盘（与"未配置"同理）。"""
    alert_type, _, _ = qa.classify_llm_error_detail(
        "doubao", 404, P2_MODEL_NOT_OPEN_BODY
    )
    assert qa.alert_priority(alert_type) == "P2", \
        f"注入前提失效：{alert_type} 不是 P2，窗口判断对它不适用"
    monkeypatch.setattr(qa, "_in_push_window", lambda: False, raising=True)

    dedupe_env.alert(model=MODEL_TURBO, body=P2_MODEL_NOT_OPEN_BODY, status=404)

    assert dedupe_env.pushed == [], "免打扰时段不应推送 P2"
    assert not dedupe_env.state_file.exists(), \
        "被窗口推迟的告警没发出去，不能记进去重状态"


def test_p2_dedupe_works_in_daytime(dedupe_env, monkeypatch):
    """P1/P2 走的是「窗口判断之后」的去重分支，同样要被钉住。

    与 P0 那组互为补充：证明去重不是只在 P0 支路上碰巧生效。
    """
    alert_type, _, _ = qa.classify_llm_error_detail(
        "doubao", 404, P2_MODEL_NOT_OPEN_BODY
    )
    assert qa.alert_priority(alert_type) == "P2", \
        f"注入前提失效：{alert_type} 不是 P2"
    monkeypatch.setattr(qa, "_in_push_window", lambda: True, raising=True)

    dedupe_env.alert(model=MODEL_TURBO, body=P2_MODEL_NOT_OPEN_BODY, status=404)
    assert len(dedupe_env.pushed) == 2, f"白天 P2 应正常推送：{dedupe_env.pushed}"

    dedupe_env.alert(model=MODEL_TURBO, body=P2_MODEL_NOT_OPEN_BODY, status=404)
    assert len(dedupe_env.pushed) == 2, "P2 同 key 当日第二次应被去重"


# ============================================================
# 状态文件的位置（解释"生产上为什么没有这个文件"）
# ============================================================
def test_state_file_name_is_stable():
    """状态文件名必须是 llm_alert_state.json（生产上核对的就是这个名字）。

    ⚠️ 这里**故意不**断言 `ALERT_STATE_FILE.parent == config.DATA_DIR`：
    全量套件里有用例会 `importlib.reload(config)`（改 DATA_DIR 后重载），
    一旦它排在前面，本进程里 `config.DATA_DIR` 与 `qa.ALERT_STATE_FILE` 就不是
    同一次解析的产物 —— 断言会变成**顺序依赖的假红**（单跑绿、全量红）。
    「路径确实由 DATA_DIR 派生」这条改由下面的子进程用例证明：那里是全新
    解释器，没有重载污染。
    """
    assert qa.ALERT_STATE_FILE.name == "llm_alert_state.json"


# ============================================================
# 生产同构进程：真的会写状态文件（判定的决定性证据）
# ============================================================
# 上面所有用例都在 pytest 进程里 —— 而 pytest 进程恰恰是"状态文件写不到生产"
# 的那个场景。所以必须再补一个**非 pytest 子进程**：没有 PYTEST_CURRENT_TEST、
# 没有 MONEYBAG_TEST_MODE、sys.modules 里没有 pytest，与生产进程同构。它要
# 证明：只要告警真的推出去了，状态文件**必然**被写出来。
#
# ⚠️ 子进程里发送器仍然是进程内假件，绝不出网；DATA_DIR 指向 tmp，绝不碰生产。
_PROD_LIKE_SCRIPT = r"""
import json
import sys
from datetime import date

sys.path.insert(0, __BACKEND_DIR__)

from services import llm_quota_alert as qa
from services import wxwork_push as wp

print("PROBE_TEST_MODE=" + str(qa._in_test_mode()))
print("PROBE_PYTEST_IN_SYS_MODULES=" + str("pytest" in sys.modules))
print("PROBE_STATE_FILE=" + str(qa.ALERT_STATE_FILE))

_pushed = []


def _fake(uid, content, title=""):
    _pushed.append(uid)
    return {"ok": True}


wp.is_configured = lambda: True
wp.send_daily_report_to = _fake

alert_type, _code, _snip = qa.classify_llm_error_detail("doubao", 402, __BODY__)
print("PROBE_PRIORITY=" + str(qa.alert_priority(alert_type)))
print("PROBE_ALERT_TYPE=" + str(alert_type))

qa.maybe_alert_quota("doubao", 402, __BODY__, model=__MODEL__, module="t")
qa.maybe_alert_quota("doubao", 402, __BODY__, model=__MODEL__, module="t")

print("PROBE_PUSHED=" + str(len(_pushed)))
print("PROBE_TODAY=" + date.today().isoformat())
_state_file = qa.ALERT_STATE_FILE
print("PROBE_STATE_EXISTS=" + str(_state_file.exists()))
if _state_file.exists():
    # 必须压成**单行**回传：文件本身是 indent=2 的多行 JSON，直接打印会被
    # 父进程按行切碎（这里踩过一次：父进程 json.loads("{") 直接炸）。
    print("PROBE_STATE=" + json.dumps(
        json.loads(_state_file.read_text(encoding="utf-8")), separators=(",", ":")
    ))
"""


def _run_prod_like_process(tmp_path: str, body: str, model: str) -> str:
    """在干净子进程里跑生产同构脚本，返回合并后的 stdout+stderr。"""
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    script = (
        _PROD_LIKE_SCRIPT
        .replace("__BACKEND_DIR__", repr(backend_dir))
        .replace("__BODY__", repr(body))
        .replace("__MODEL__", repr(model))
    )

    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("PYTHONPATH", "PYTEST_CURRENT_TEST", "MONEYBAG_TEST_MODE")
    }
    # 状态文件隔离：绝不让子进程碰到生产 /opt/moneybag/data
    env["DATA_DIR"] = str(tmp_path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=backend_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"子进程异常退出：{out}"
    return out


def test_production_like_process_writes_state_file_and_suppresses_second_push(tmp_path):
    """决定性证据：非 pytest 进程里，推送后状态文件**确实**被写出并拦住第二次。

    这条用例是"生产上为什么没有这个文件"这一问的正面回答：写入路径是好的，
    文件不存在只能是"生产上压根没推出去过告警"，而不是"代码写不进去"。
    """
    data_dir = tmp_path / "prod_like_data"
    out = _run_prod_like_process(str(data_dir), P0_ARREARS_BODY, MODEL_TURBO)

    # —— 反空转：子进程必须真的是"生产同构"，否则这条用例就是换皮的空转 ——
    assert "PROBE_TEST_MODE=False" in out, f"子进程仍在测试模式，注入失效：{out}"
    assert "PROBE_PYTEST_IN_SYS_MODULES=False" in out, f"子进程里有 pytest：{out}"
    assert "PROBE_PRIORITY=P0" in out, f"注入前提失效，告警不是 P0：{out}"

    # 状态文件路径必须**由 DATA_DIR 派生**：子进程是全新解释器（无 reload 污染），
    # 在这里断言最可靠。这条同时解释了"生产上为什么没有这个文件" —— 生产
    # DATA_DIR=/opt/moneybag/data，测试进程却被 conftest 隔离到了临时目录。
    assert f"PROBE_STATE_FILE={data_dir / 'llm_alert_state.json'}" in out, (
        f"状态文件应落在 DATA_DIR 下：{out}"
    )

    assert "PROBE_PUSHED=2" in out, (
        f"生产同构进程里：第一次推 2 条、第二次应被去重拦下（共 2 条）：{out}"
    )
    assert "PROBE_STATE_EXISTS=True" in out, (
        f"★ 生产同构进程没有写出去重状态文件 —— 若为真，就是真的 bug：{out}"
    )

    state_line = [ln for ln in out.splitlines() if ln.startswith("PROBE_STATE=")]
    assert state_line, f"子进程未回传状态文件内容：{out}"
    state = json.loads(state_line[0][len("PROBE_STATE="):])
    today_line = [ln for ln in out.splitlines() if ln.startswith("PROBE_TODAY=")]
    assert today_line, f"子进程未回传当天日期：{out}"
    today = today_line[0][len("PROBE_TODAY="):]

    key = f"doubao_balance_exhausted|{MODEL_TURBO}"
    assert state == {key: today}, f"状态文件内容不符预期：{state}"
    assert (data_dir / "llm_alert_state.json").exists(), \
        f"状态文件应落在 DATA_DIR 下（{data_dir}）"


def test_production_like_process_state_file_is_reused_next_day(tmp_path, monkeypatch):
    """同一状态文件在新的一天不再拦（日期比对，不是"有没有这个文件"）。

    用子进程写一次真实状态文件，再把文件里的日期改成昨天，回到进程内断言
    `was_alert_sent_today()` 为 False —— 直接锁住对外 API 的日期语义。
    """
    data_dir = tmp_path / "prod_like_data"
    out = _run_prod_like_process(str(data_dir), P0_ARREARS_BODY, MODEL_TURBO)
    assert "PROBE_STATE_EXISTS=True" in out, f"前置失败：子进程没写状态文件：{out}"

    state_file = data_dir / "llm_alert_state.json"
    key = f"doubao_balance_exhausted|{MODEL_TURBO}"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state[key] = (date.today() - timedelta(days=1)).isoformat()
    state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(qa, "ALERT_STATE_FILE", state_file, raising=True)
    assert qa.was_alert_sent_today(key) is False, \
        "昨天的记录不应拦下今天的告警（对外 API 同样如此）"

    qa.mark_alert_sent_today(key)
    assert qa.was_alert_sent_today(key) is True, \
        "标记之后必须立刻生效（对外 API 与内部去重共享同一份状态文件）"


# ============================================================
# W2：推送失败不得消费当日去重额度（配 30 分钟节流，防「静默丢失」翻成「风暴」）
# ============================================================
# 修之前的语义：推送循环跑完就无条件 _mark_sent_today ⇒ 两次 send 全失败也
# 算"今天已推过"，这条告警当天凭空消失。
# 修之后的语义：
#   成功 ≥ 1 次 → 记 sent_today（当天不再推，原行为不变）
#   全部失败    → 只记失败时间戳，30 分钟内不重试；30 分钟后允许重试
# 本组用例按「反空转」标准写：每条都先断言 send 真的被调用过。
def test_all_pushes_failed_does_not_consume_daily_quota(dedupe_env, monkeypatch, capsys):
    """全部失败：不写 sent_today，改写失败节流时间戳。"""
    _assert_is_p0()
    _install_scripted_sender(
        monkeypatch, dedupe_env.pushed, [{"ok": False}, {"ok": False}] * 2
    )

    dedupe_env.alert()

    # 反空转：send 真的被调用过（否则"没记账"可能只是压根没走到推送）
    assert len(dedupe_env.pushed) == 2, f"反空转：应尝试推给 2 个人：{dedupe_env.pushed}"
    state = dedupe_env.read_state()
    assert _dedupe_key() not in state, \
        f"全部失败时绝不能记 sent_today（记了就等于把这条告警当天抹掉）：{state}"
    assert isinstance(state.get(qa._failed_key(_dedupe_key())), (int, float)), \
        f"应写失败节流时间戳（供 30 分钟后重试）：{state}"

    out = capsys.readouterr().out
    assert "[QUOTA_ALERT][PUSH_FAILED_DEFERRED]" in out, f"应打印失败暂缓日志：{out}"
    assert "✅ 已推送告警" not in out, f"没送出去却报成功（日志失真）：{out}"


def test_second_trigger_within_throttle_window_does_not_retry(
    dedupe_env, monkeypatch, capsys,
):
    """30 分钟内二次触发：不再尝试推送（这是 W2 必须配节流的原因 —— 防风暴）。

    背景：maybe_alert_quota 挂在 gateway 回退分支，**每次 LLM 调用失败都会走
    一遍**。真欠费时一天几百次调用，失败不节流就是几百条推送尝试。
    """
    _assert_is_p0()
    _install_scripted_sender(
        monkeypatch, dedupe_env.pushed, [{"ok": False}, {"ok": False}] * 2
    )
    dedupe_env.alert()
    assert len(dedupe_env.pushed) == 2, "反空转：第一次应真的尝试过 2 次"

    dedupe_env.alert()  # 同一窗口内再次触发

    assert len(dedupe_env.pushed) == 2, \
        f"节流窗口内不得重试（否则就是告警风暴）：{dedupe_env.pushed}"
    out = capsys.readouterr().out
    assert "[QUOTA_ALERT][THROTTLED]" in out, \
        f"反空转：应打印节流日志（证明是节流拦的，不是别的分支拦的）：{out}"


def test_retry_after_throttle_window_expires(dedupe_env, monkeypatch, capsys):
    """窗口过后必须重试 —— 否则企微长期挂掉时告警永久静默丢失。"""
    _assert_is_p0()
    _install_scripted_sender(
        monkeypatch, dedupe_env.pushed, [{"ok": False}, {"ok": False}] * 2
    )
    dedupe_env.alert()
    old_marker = _shift_failed_marker(
        dedupe_env.state_file, _dedupe_key(), qa.FAILED_THROTTLE_SECONDS + 60
    )

    dedupe_env.alert()

    assert len(dedupe_env.pushed) == 4, \
        f"窗口过期后必须重试一次（不重试 = 永久静默丢失）：{dedupe_env.pushed}"
    new_marker = dedupe_env.read_state()[qa._failed_key(_dedupe_key())]
    assert new_marker > old_marker, "重试仍失败时应刷新节流时间戳（滚动窗口）"
    out = capsys.readouterr().out
    assert "[QUOTA_ALERT][THROTTLED]" not in out, f"窗口已过却仍被节流：{out}"


def test_throttle_window_boundary_is_30_minutes(dedupe_env, monkeypatch):
    """节流窗口边界：10 分钟前失败 ⇒ 仍在窗口内；31 分钟前 ⇒ 已过期可重试。

    ⚠️ 偏移量**故意写死绝对秒数**（10min / 31min），不拿 FAILED_THROTTLE_SECONDS
    去推导 —— 用常量推导的话，把窗口改成 24 小时这条用例照样全绿：
    故障注入 C（窗口 30min → 24h）实测**整套 23 条全绿**，守卫自己骗自己，
    正是本项目最忌讳的"闸门空转仍显绿"。窗口真要调，请连本用例一起改 ——
    这份"改动摩擦"是刻意留的。

    上一条 `test_retry_after_throttle_window_expires` 用常量推导偏移（保证算术
    对任意窗口成立），这一条用绝对秒数（保证窗口本身不被偷偷改掉）—— 两条
    互补，缺一个就漏。
    """
    assert qa.FAILED_THROTTLE_SECONDS == 30 * 60, \
        "节流窗口被改了，请同步改本用例的边界偏移量（并确认这是有意为之）"

    _assert_is_p0()
    _install_scripted_sender(
        monkeypatch, dedupe_env.pushed, [{"ok": False}, {"ok": False}] * 3
    )

    dedupe_env.alert()  # 第 1 次：全失败，写节流标记
    _shift_failed_marker(dedupe_env.state_file, _dedupe_key(), 10 * 60)

    dedupe_env.alert()  # 10 分钟前失败 ⇒ 仍在 30 分钟窗口内
    assert len(dedupe_env.pushed) == 2, \
        f"10 分钟 < 30 分钟窗口，不应重试：{dedupe_env.pushed}"

    _shift_failed_marker(dedupe_env.state_file, _dedupe_key(), 21 * 60)  # 累计 31 分钟

    dedupe_env.alert()
    assert len(dedupe_env.pushed) == 4, \
        f"31 分钟 > 30 分钟窗口，应重试：{dedupe_env.pushed}"


def test_partial_success_still_marks_sent_today(dedupe_env, monkeypatch, capsys):
    """第一个失败、第二个成功 ⇒ 记 sent_today（只要有一个人收到就算送达）。"""
    _assert_is_p0()
    _install_scripted_sender(
        monkeypatch, dedupe_env.pushed, [{"ok": False}, {"ok": True}]
    )

    dedupe_env.alert()

    assert len(dedupe_env.pushed) == 2, f"反空转：应尝试 2 次：{dedupe_env.pushed}"
    state = dedupe_env.read_state()
    assert state.get(_dedupe_key()) == date.today().isoformat(), \
        f"部分成功也应记 sent_today：{state}"
    assert qa._failed_key(_dedupe_key()) not in state, \
        f"送达后应清掉失败标记：{state}"
    assert "✅ 已推送告警" in capsys.readouterr().out

    dedupe_env.alert()  # 当天第二次
    assert len(dedupe_env.pushed) == 2, "送达后当天不再推（原行为不变）"


def test_exception_from_sender_counts_as_failure_and_throttles(
    dedupe_env, monkeypatch,
):
    """send 抛异常也算失败：不记 sent_today，且同样进节流窗口。"""
    _assert_is_p0()
    _install_scripted_sender(
        monkeypatch, dedupe_env.pushed,
        [RuntimeError("boom"), RuntimeError("boom")] * 2,
    )

    dedupe_env.alert()

    assert len(dedupe_env.pushed) == 2, f"反空转：应尝试 2 次：{dedupe_env.pushed}"
    state = dedupe_env.read_state()
    assert _dedupe_key() not in state, f"抛异常也不应记 sent_today：{state}"
    assert qa._failed_key(_dedupe_key()) in state, f"应写失败节流时间戳：{state}"

    dedupe_env.alert()
    assert len(dedupe_env.pushed) == 2, "异常导致的失败同样要节流（防风暴）"


def test_unknown_sender_return_value_counts_as_delivered(dedupe_env, monkeypatch):
    """返回值不是 {"ok": ...}（含 None）时**保守当成功** —— 兼容既有假 sender。

    这条防的是「守卫靠改坏别人的合法路径来生效」：既有用例
    （test_alert_push_test_mode_guard.py / test_llm_quota_alert_classify.py 的
    lambda 假件）返回 None。若把 None 判成失败，它们会全部退化成"被节流拦下"
    的空转绿 —— 表面上更严，实际是守卫失效。未知返回值按旧行为记账最安全。
    """
    _assert_is_p0()
    monkeypatch.setattr(wp, "is_configured", lambda: True, raising=True)
    monkeypatch.setattr(
        wp, "send_daily_report_to",
        lambda uid, content, title="": dedupe_env.pushed.append({"uid": uid}),
        raising=True,
    )

    dedupe_env.alert()

    assert len(dedupe_env.pushed) == 2, f"反空转：应尝试 2 次：{dedupe_env.pushed}"
    assert dedupe_env.read_state().get(_dedupe_key()) == date.today().isoformat(), \
        "未知返回值应按送达处理（保持既有合法用例的语义）"


def test_failure_then_success_after_window_marks_sent_today(
    dedupe_env, monkeypatch,
):
    """完整自愈链路：全失败 → 窗口过后重试成功 → 当天不再推。"""
    _assert_is_p0()
    _install_scripted_sender(
        monkeypatch, dedupe_env.pushed,
        [{"ok": False}, {"ok": False}, {"ok": True}, {"ok": True}],
    )

    dedupe_env.alert()
    assert dedupe_env.read_state().get(_dedupe_key()) is None, "第一次全失败，不应记账"

    _shift_failed_marker(
        dedupe_env.state_file, _dedupe_key(), qa.FAILED_THROTTLE_SECONDS + 60
    )
    dedupe_env.alert()  # 重试并成功

    assert len(dedupe_env.pushed) == 4, f"窗口过后应重试：{dedupe_env.pushed}"
    state = dedupe_env.read_state()
    assert state.get(_dedupe_key()) == date.today().isoformat(), f"重试成功应记账：{state}"
    assert qa._failed_key(_dedupe_key()) not in state, f"送达后应清掉失败标记：{state}"

    dedupe_env.alert()  # 当天第三次
    assert len(dedupe_env.pushed) == 4, "已送达 ⇒ 当天不再推"


def test_throttle_marker_does_not_collide_with_dedupe_keys(dedupe_env):
    """节流 key 与去重 key 必须严格隔离（老状态文件读进来不受影响）。

    「失败节流」是本文件引入的新字段，用 `failed|` 前缀与 sent_today 的
    `alert_type|model` 并列存放。这两条断言锁住：
      • 只有节流标记时，对外 was_alert_sent_today 仍为 False（不该拦新告警）
      • 老格式状态文件（只有 {key: date}，没有节流字段）照常生效
    """
    key = _dedupe_key()

    dedupe_env.state_file.write_text(
        json.dumps({qa._failed_key(key): time.time() - 1}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert qa.was_alert_sent_today(key) is False, \
        "节流标记不得被当成「今天已推过」（前缀必须隔离）"

    dedupe_env.state_file.write_text(
        json.dumps({key: date.today().isoformat()}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert qa.was_alert_sent_today(key) is True, "老格式状态文件必须照常生效"

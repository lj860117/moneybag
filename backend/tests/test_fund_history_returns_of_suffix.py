"""
fund_history_returns 的基金代码后缀归一化回归测试
=================================================

背景（2026-09-22，任务：OF 后缀 bug 的**生产残留点**）

`services/fund_history_returns.py::_get_from_tushare` 原来写的是::

    if not code.endswith('.OF'):
        ts_code = code + '.OF'
    else:
        ts_code = code

判据是「后缀**是不是** .OF」，而不是「**有没有**后缀」。于是场内 ETF/LOF
带交易所后缀传进来时会被拼出双后缀::

    510300.SH  → 510300.SH.OF   ❌ 沪深300ETF，用户真实持仓
    161725.SZ  → 161725.SZ.OF   ❌
    006547     → 006547.OF      ✅（裸码路径本来就对，别改坏）

`pro.fund_nav(ts_code="510300.SH.OF")` 必然查不到 → 降级 AKShare → 历史
收益率缺失/变慢。调用链上 `api/fund_detail.py` 的
`/api/fund/history-returns/{code}` 直传 code、没有上游归一化，所以带后缀的
场内码能一路打到这个函数（这是它区别于"已修好的死路径"的地方：它在生产上
真的会被走到）。

修法：对齐项目既有约定 —— `services/tushare_data.py` 的 get_fund_nav /
get_fund_manager / get_fund_portfolio，以及 `api/fund_detail.py` 的两处，
全部是 `code if "." in code else f"{code}.OF"`（幂等、对场内码安全）。
修完只有这一套约定，没有第二套。

测试设计（防止又写成恒绿）
--------------------------
1. **不复制实现里的分支表**。用假 tushare pro 顶掉模块级 `pro`，调**真实的**
   `_get_from_tushare()`，断言它**真正送到 `fund_nav` 的 ts_code**。实现一改
   立刻能感知，不会退化成"改了实现还绿"。
2. **每条断言都是精确等值**，没有"不抛异常""返回值不为空"这类恒真命题。
3. **断言调用次数**：`fund_nav` 必须被调用恰好 1 次。少了这一条，"压根没调
   用"会伪装成绿（本项目最忌讳的闸门空转仍显绿）。
4. **幂等 f(f(x)) == f(x)** 与**单后缀不变式 count('.') == 1** 一起钉住。
   注意：幂等单看一条在注入场景下**不一定红**（见下方 FI-1 的实测数字），
   真正兜底的是"510300.SH 必须原样透传"这条等值断言 + 单后缀不变式。

故障注入指纹（FI-1）
--------------------
把 `services/fund_history_returns.py` 的修复回退成::

    if not code.endswith('.OF'):
        ts_code = code + '.OF'
    else:
        ts_code = code

再跑本文件，预期**至少 6 条红**：
  * `test_ts_code_sent_to_fund_nav[...]` 中 510300.SH / 161725.SZ / 512880.SH
    三例 → 实际送出 `510300.SH.OF` 等
  * `test_exchange_suffixed_code_has_no_double_suffix[...]` 三例
  * `test_public_entry_reaches_tushare_with_normalized_code[510300.SH]` 一例
    （且会因降级而触发 `degraded` 非空断言）

实测数字见「交付报告」，注入前/后的 passed/failed 计数以
`pytest backend/tests/test_fund_history_returns_of_suffix.py -q` 为准。

⚠️ 已知注意点：`test_normalization_is_idempotent` 在 FI-1 下**仍是绿的** ——
因为 `endswith('.OF')` 版本对 `510300.SH.OF` 也满足 endswith，第二次不再追加。
所以幂等这一条**不能单独作为本 bug 的判据**，它防的是另一类退化
（比如把判据写成 `if True` 导致无限追加）。这一点必须写清楚，否则后人会误判
"注入后没全红 = 测试没用"。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Tuple

import pytest

from services import fund_history_returns as fhr


# ============================================================
# 用例表：(传入 code, 期望送到 pro.fund_nav 的 ts_code, 说明)
# ============================================================
SUFFIX_CASES: List[Tuple[str, str, str]] = [
    # ---- 本次修的目标：场内 ETF/LOF 已带交易所后缀，必须原样透传 ----
    ("510300.SH", "510300.SH", "沪深300ETF（用户真实持仓）—— 修复前被拼成 510300.SH.OF"),
    ("161725.SZ", "161725.SZ", "招商中证白酒 LOF（场内）—— 修复前被拼成 161725.SZ.OF"),
    ("512880.SH", "512880.SH", "证券 ETF（场内）"),
    # ---- 裸码：按项目统一约定补 .OF（与修复前一致，防改坏）----
    ("006547", "006547.OF", "场外基金裸码"),
    ("519736", "519736.OF", "场外基金裸码"),
    ("000001", "000001.OF", "场外基金裸码"),
    ("510300", "510300.OF",
     "场内 ETF 的**裸码**：本函数无从判断交易所，沿用全项目统一约定补 .OF"
     "（与 services.tushare_data.get_fund_nav 的行为一致；修复前也是这个结果）"),
    # ---- 已带 .OF：不得重复补 ----
    ("000001.OF", "000001.OF", "已带 .OF 后缀"),
    ("006547.OF", "006547.OF", "已带 .OF 后缀"),
]

# 只挑「已带交易所后缀」的输入，单独钉死（本次 bug 的核心形态）
EXCHANGE_SUFFIXED_INPUTS: List[str] = [
    code for code, expected, _ in SUFFIX_CASES
    if "." in code and not code.endswith(".OF")
]


# ============================================================
# 假 tushare pro
# ============================================================
class _RecordingPro:
    """假的 tushare pro：只记录 `fund_nav` 收到的 ts_code，绝不发网络请求。

    `_get_from_tushare` 里对 pro 的用法只有 `pro.fund_nav(ts_code=..., start_date=...,
    end_date=...)` 一处，所以只需要这一个方法。返回 None 表示"查无此基"，
    正好走 `_get_from_tushare` 的 `df is None` 分支 —— 本测试关心的是
    **送进去的 ts_code**，不是返回的数据。
    """

    def __init__(self, frame=None) -> None:
        self.calls: List[str] = []
        self._frame = frame

    def fund_nav(self, ts_code: str, start_date: str = "", end_date: str = ""):
        self.calls.append(ts_code)
        return self._frame


@pytest.fixture
def recording_pro(monkeypatch) -> _RecordingPro:
    """把模块级 `pro` 换成记录器。

    为什么这样能顶住：`_init_tushare()` 第一行是 `if pro is not None: return pro`，
    所以换成假对象后它不会再去 import 真实 tushare / 读 TUSHARE_TOKEN / 发请求。
    monkeypatch 保证用例结束后还原（模块状态不外泄给其他测试）。
    """
    fake = _RecordingPro()
    monkeypatch.setattr(fhr, "pro", fake, raising=True)
    return fake


def _seen_ts_code(fake: _RecordingPro, code: str) -> str:
    """跑一次**真实的** `_get_from_tushare`，返回它送到 `fund_nav` 的 ts_code。

    这是本文件的核心观测点：不复制实现的分支逻辑，直接读生产代码的实际行为。
    """
    fake.calls.clear()
    fhr._get_from_tushare(code)
    assert len(fake.calls) == 1, (
        f"fund_nav 应被调用恰好 1 次，实际 {len(fake.calls)} 次（calls={fake.calls}）。"
        " 若一次都没调用，下面的等值断言会失去意义 —— 那正是闸门空转仍显绿。"
    )
    return fake.calls[0]


# ============================================================
# 1. 核心：送到 fund_nav 的 ts_code 必须精确等于期望值
# ============================================================
@pytest.mark.parametrize(
    "code,expected,desc",
    SUFFIX_CASES,
    ids=[c[0] for c in SUFFIX_CASES],
)
def test_ts_code_sent_to_fund_nav(
    code: str, expected: str, desc: str, recording_pro: _RecordingPro
) -> None:
    """`_get_from_tushare("{code}")`：期望 fund_nav 收到 {expected}（{desc}）。"""
    got = _seen_ts_code(recording_pro, code)
    assert got == expected, (
        f"输入 {code} 应送出 {expected}，实际送出 {got} —— {desc}。"
        " 双后缀（如 510300.SH.OF）会让 fund_nav 必然查不到、白白降级 AKShare。"
    )


# ============================================================
# 2. 本次 bug 的核心形态：带交易所后缀的码不得出现双后缀
# ============================================================
@pytest.mark.parametrize("code", EXCHANGE_SUFFIXED_INPUTS)
def test_exchange_suffixed_code_has_no_double_suffix(
    code: str, recording_pro: _RecordingPro
) -> None:
    """场内 ETF/LOF（.SH/.SZ）必须原样透传，且结果里恰好只有一个点号。

    `count('.') == 1` 是**单后缀不变式**：修复前 `510300.SH` 会变成
    `510300.SH.OF`（2 个点），这条会红；而它不依赖"期望值表"，能抓住
    "换了别的形式的双后缀"这类退化。
    """
    got = _seen_ts_code(recording_pro, code)
    assert got == code, f"{code} 被二次加工成 {got}（应原样透传）"
    assert got.count(".") == 1, (
        f"{code} 归一化后是 {got}，含 {got.count('.')} 个点号 —— "
        "正常 ts_code 只应有 1 个（交易所后缀），多个说明被重复拼后缀"
    )


# ============================================================
# 3. 幂等：f(f(x)) == f(x)
# ============================================================
@pytest.mark.parametrize(
    "code,expected,desc",
    SUFFIX_CASES,
    ids=[c[0] for c in SUFFIX_CASES],
)
def test_normalization_is_idempotent(
    code: str, expected: str, desc: str, recording_pro: _RecordingPro
) -> None:
    """归一化结果再喂一次，必须得到同样的结果（不得无限追加后缀）。

    ⚠️ 这一条**单独**不足以抓住本次 bug（FI-1 下它仍绿，原因见模块 docstring），
    它防的是另一类退化：判据被改成恒真导致的 `X.OF.OF.OF...`。
    """
    once = _seen_ts_code(recording_pro, code)
    twice = _seen_ts_code(recording_pro, once)
    assert twice == once, (
        f"不幂等：{code} → {once} → {twice}。归一化必须满足 f(f(x)) == f(x)。"
    )
    assert once == expected, f"{code} 首次归一化即为 {once}，期望 {expected}"


# ============================================================
# 4. 端到端：公开入口 get_fund_history_returns 也要走对
# ============================================================
# 为什么还需要这一条：API 入口 `api/fund_detail.py` 的
# `/api/fund/history-returns/{code}` 是**直传 code**的（无上游归一化），
# 带后缀的场内码会一路打到 `get_fund_history_returns` → `_get_from_tushare`。
# 上面几条测的是叶子函数，这一条钉住"入口 → 叶子"整条链都归一化正确，
# 并且**没有因为 ts_code 不对而降级 AKShare**。
def _make_nav_frame(days: int = 1200):
    """造一段足够覆盖 3 年的假净值（升序），让 Tushare 分支能算出结果。"""
    pd = pytest.importorskip("pandas")
    end = datetime.now()
    dates = [(end - timedelta(days=days - 1 - i)).strftime("%Y%m%d") for i in range(days)]
    navs = [round(1.0 + i * 0.001, 6) for i in range(days)]
    return pd.DataFrame(
        {"nav_date": dates, "unit_nav": navs, "accum_nav": navs}
    )


@pytest.mark.parametrize(
    "code,expected",
    [("510300.SH", "510300.SH"), ("006547", "006547.OF")],
)
def test_public_entry_reaches_tushare_with_normalized_code(
    code: str, expected: str, monkeypatch
) -> None:
    """`get_fund_history_returns` 必须用归一化后的 ts_code 命中 Tushare，不降级。"""
    fake = _RecordingPro(frame=_make_nav_frame())
    monkeypatch.setattr(fhr, "pro", fake, raising=True)

    degraded: List[str] = []

    def _fake_akshare(c: str) -> dict:
        degraded.append(c)
        return {"source": "akshare"}

    # raising=True：装不上就整片红，不要"静默没装上"
    monkeypatch.setattr(fhr, "_get_from_akshare", _fake_akshare, raising=True)

    result = fhr.get_fund_history_returns(code)

    assert fake.calls == [expected], (
        f"入口 {code} 应让 fund_nav 收到 {expected}，实际 {fake.calls}"
    )
    assert degraded == [], (
        f"Tushare 本应命中却降级到 AKShare（降级入参 {degraded}）—— "
        "典型原因就是 ts_code 被拼成了双后缀"
    )
    assert isinstance(result, dict) and result.get("date"), (
        f"应返回带 date 的收益字典，实际 {result}"
    )
    assert result.get("source") != "akshare"


# ============================================================
# 5. 防"过度修复"：不得把场内码硬改成 .OF，也不得吞掉裸码补后缀
# ============================================================
def test_no_silent_code_rewrite_of_exchange_codes(recording_pro: _RecordingPro) -> None:
    """场内码的后缀必须被**保留**，而不是被统一成 .OF（那会查不到同一个标的）。"""
    for code in ("510300.SH", "161725.SZ"):
        got = _seen_ts_code(recording_pro, code)
        assert got.endswith(code.split(".")[1]), (
            f"{code} 的交易所后缀被改掉了（得到 {got}）—— "
            "裸码才补 .OF，已带后缀的必须保留原交易所"
        )


def test_bare_code_still_gets_of_suffix(recording_pro: _RecordingPro) -> None:
    """裸码仍然要补 .OF —— 修 A 不能把 B 改坏（这条是修复前就正确的行为）。"""
    for code in ("006547", "519736", "000001"):
        assert _seen_ts_code(recording_pro, code) == f"{code}.OF"

"""
P0-B 行为级守卫：AKShare 历史净值接口改名
=========================================

背景
----
生产 akshare 1.18.60 上 ``fund_open_fund_hist_em`` 已被移除
（实测 ``hasattr(ak, "fund_open_fund_hist_em")`` 为 False），调用点
``fund_history_returns.py`` 每次都抛

    AttributeError: module 'akshare' has no attribute 'fund_open_fund_hist_em'

被 except 吞成一条 ``⚠️ AKShare 错误``，09-17 起累计 27 次无人发现。

现役等价接口是
``fund_open_fund_info_em(symbol=code, indicator="单位净值走势", period="成立来")``，
已实测返回列与旧接口**完全一致**：``净值日期``(str, YYYY-MM-DD) /
``单位净值``(float64) / ``日增长率``(float64)，且 period="成立来" 覆盖完整历史
（519736 → 2014-05-09 至 2026-09-17 共 2968 行），3 年周期取数不受影响。
因此下游消费代码（``df['净值日期']`` / ``df['单位净值']``）无需改动。

这个文件锁什么
--------------
  - 优先用现役接口，且参数名是 ``symbol``（旧接口是 ``fund``，别混）
  - 旧接口仍在时（老版本 akshare）仍能取数
  - **hasattr 守卫**：接口全部消失时抛 ``AkNavApiUnavailable`` 并留下
    ``🚨 AKSHARE_API_MISSING`` 告警 —— 绝不是静默 pass
  - 新接口列名与下游计算链路真的对得上（端到端算出收益率）

不测什么
--------
  - 真实 AKShare 网络调用（用 fake akshare 模块注入）
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.fund_history_returns import (  # noqa: E402
    AkNavApiUnavailable,
    _ak_nav_history,
    _get_from_akshare,
)


class _FakeAk:
    """可控的 akshare 替身。

    available: 该假模块上「存在」的接口名集合
    df:        接口返回值
    raises:    {接口名: 要抛的异常}
    """

    def __init__(self, available=(), df=None, raises=None):
        self.calls = []
        self.available = set(available)
        self.df = df
        self.raises = raises or {}

        if "fund_open_fund_info_em" in self.available:
            self.fund_open_fund_info_em = self._info_em
        if "fund_open_fund_hist_em" in self.available:
            self.fund_open_fund_hist_em = self._hist_em

    def _info_em(self, **kwargs):
        self.calls.append(("fund_open_fund_info_em", kwargs))
        if self.raises.get("fund_open_fund_info_em"):
            raise self.raises["fund_open_fund_info_em"]
        return self.df

    def _hist_em(self, **kwargs):
        self.calls.append(("fund_open_fund_hist_em", kwargs))
        if self.raises.get("fund_open_fund_hist_em"):
            raise self.raises["fund_open_fund_hist_em"]
        return self.df


def test_ak_nav_history_prefers_info_em_and_passes_right_kwargs():
    """现役接口优先，且参数名是 symbol（不是旧接口的 fund）。"""
    fake = _FakeAk(available=("fund_open_fund_info_em",), df="DF")
    df, name = _ak_nav_history(fake, "519736")

    assert (df, name) == ("DF", "fund_open_fund_info_em")
    called_name, kwargs = fake.calls[0]
    assert called_name == "fund_open_fund_info_em"
    assert kwargs["symbol"] == "519736"
    assert kwargs["indicator"] == "单位净值走势"
    assert kwargs["period"] == "成立来"


def test_ak_nav_history_falls_back_to_legacy_hist_em():
    """旧接口仍在时（老版本 akshare）仍能正常取数，参数名是 fund。"""
    fake = _FakeAk(available=("fund_open_fund_hist_em",), df="OLD")
    df, name = _ak_nav_history(fake, "000001")

    assert (df, name) == ("OLD", "fund_open_fund_hist_em")
    assert fake.calls[0][1]["fund"] == "000001"


def test_ak_nav_history_raises_loudly_when_no_api_exists():
    """hasattr 守卫的核心：接口全部消失时抛明确异常，而不是静默 pass。"""
    fake = _FakeAk(available=())  # 两个接口都不存在

    with pytest.raises(AkNavApiUnavailable) as exc:
        _ak_nav_history(fake, "519736")

    msg = str(exc.value)
    assert "AKSHARE_API_MISSING" in msg
    assert "fund_open_fund_info_em=MISSING" in msg
    assert "fund_open_fund_hist_em=MISSING" in msg


def test_ak_nav_history_tries_next_candidate_when_first_raises():
    """第一个候选接口抛错时继续尝试下一个，而不是直接放弃。"""
    fake = _FakeAk(
        available=("fund_open_fund_info_em", "fund_open_fund_hist_em"),
        df="OLD",
        raises={"fund_open_fund_info_em": ValueError("boom")},
    )
    df, name = _ak_nav_history(fake, "000001")

    assert name == "fund_open_fund_hist_em"
    assert df == "OLD"
    assert [c[0] for c in fake.calls] == ["fund_open_fund_info_em", "fund_open_fund_hist_em"]


def test_get_from_akshare_end_to_end_with_new_api_columns(monkeypatch):
    """端到端：用新接口的真实列名（净值日期 / 单位净值）跑通收益率计算。

    这条同时锁住「新接口列名与下游消费一致」——换接口最容易翻车的地方。
    """
    import pandas as _pandas

    dates = _pandas.date_range(end="2026-09-17", periods=500, freq="D")
    df = _pandas.DataFrame({
        "净值日期": [d.strftime("%Y-%m-%d") for d in dates],
        "单位净值": [1.0 + i * 0.01 for i in range(len(dates))],
        "日增长率": [0.1] * len(dates),
    })
    fake = _FakeAk(available=("fund_open_fund_info_em",), df=df)
    monkeypatch.setitem(sys.modules, "akshare", fake)

    result = _get_from_akshare("519736")

    assert result is not None, "新接口列名对得上时应能算出收益"
    assert result["date"] == "2026-09-17"
    # 净值是单调上升的，各周期收益都应 > 0
    for period in ("1m", "3m", "6m", "1y"):
        assert result[period] is not None, f"{period} 不应为 None"
        assert result[period] > 0, f"{period} 应为正收益，实际 {result[period]}"


def test_get_from_akshare_warns_visibly_when_api_missing(monkeypatch, capsys):
    """接口整体消失时必须留下可见告警（🚨 + AKSHARE_API_MISSING），不能静默。"""
    fake = _FakeAk(available=())
    monkeypatch.setitem(sys.modules, "akshare", fake)

    result = _get_from_akshare("519736")
    out = capsys.readouterr().out

    assert result is None
    assert "AKSHARE_API_MISSING" in out
    assert "🚨" in out, "接口消失必须用 🚨 与普通 ⚠️ 区分，否则又会变成静默失败"

"""基金净值口径回归测试 —— 缓存键 + 列口径两层根因（P0 第二层）

事故背景
--------
`f929bd0`（晨报温度计改用单位净值）**单独不够**。生产全新进程实测发现同一个
``services.market_data.get_fund_nav(163406)`` 会因**同进程调用顺序**返回两个口径::

    直接调                                 → nav='2.2401'（单位净值）✅
    先 get_fund_nav_history(163406, 累计) 再调 → nav='8.5191'（累计净值）❌

根因**两层**：

  层 1 — 缓存键不分 indicator（``infra/data_source/providers/akshare_provider.py``）::
      cache_key = f"ak_fund_nav_{symbol}"        # ← 缺 indicator
  「单位净值走势」与「累计净值走势」返回**不同列名、不同数值**的帧，却共用同一个
  缓存键：先到的写进 ``_macro_cache``，后到的直接命中、拿到错误口径的帧。

  层 2 — 列口径静默兜底（``services/market_data.py``）::
      for cand in ["unit_nav", "单位净值", "nav"]: ...   # 累计帧都不匹配
      if nav_col is None:
          nav_col = num_cols[0]                          # ← 落到「累计净值」
  累计帧列名是「累计净值」，三个单位口径候选都不匹配 → ``num_cols[0]`` =
  「累计净值」→ **不报错、不告警**地当单位净值返回。

有分红历史的基金累计净值远高于单位净值（163406：8.5191 vs 2.2401，3.803×），
晨报因此显示 +118.6%（真实 −7.6%）。

本文件的三条用例分别钉死：
  - :func:`test_provider_cache_key_distinguishes_indicator` → 层 1（必红于修前）
  - :func:`test_market_data_rejects_accum_frame`            → 层 2（必红于修前）
  - :func:`test_e2e_accum_then_unit_in_same_process`        → 集成对撞（必红于修前）
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from infra.data_source.providers import akshare_provider as akp  # noqa: E402


# ============================================================
# 造帧：单位口径 vs 累计口径（列名/数值都不同）
# ============================================================

def _unit_frame() -> pd.DataFrame:
    """单位净值走势帧：[净值日期, 单位净值, 日增长率]，末值 2.2401。"""
    return pd.DataFrame({
        "净值日期": ["2026-09-16", "2026-09-17"],
        "单位净值": [2.10, 2.2401],
        "日增长率": [0.5, 0.8],
    })


def _accum_frame() -> pd.DataFrame:
    """累计净值走势帧：[净值日期, 累计净值]，末值 8.5191。"""
    return pd.DataFrame({
        "净值日期": ["2026-09-16", "2026-09-17"],
        "累计净值": [8.30, 8.5191],
    })


def _fake_call_with_timeout(fn, timeout, **kwargs):
    """替身 call_with_timeout：按 indicator 返回对应口径的帧。

    真实实现是 ``call_with_timeout(ak.fund_open_fund_info_em, 10, symbol=,
    indicator=)``，故这里按 kwargs["indicator"] 分派。
    """
    indicator = kwargs.get("indicator")
    if indicator == "累计净值走势":
        return _accum_frame()
    if indicator == "单位净值走势":
        return _unit_frame()
    return None


def _raise_network(*args, **kwargs):
    """任何真实网络调用都直接失败（保证测试离线）。"""
    raise RuntimeError("[test] 测试环境禁止真实网络请求")


class _StubAk:
    """AKShare 模块替身：只需提供 ``_fetch_fund_nav`` 会**取属性**的接口。

    ``_fetch_fund_nav`` 会先求值 ``ak.fund_open_fund_info_em``（再交给
    ``call_with_timeout``）。若 ``_get_ak`` 返回裸 ``object()``，这一步就
    AttributeError，真正的 ``call_with_timeout`` 替身根本轮不到执行，
    测试会**因错误的原因变红**。故这里给出同名接口；真实调用会被
    ``_fake_call_with_timeout`` 拦下，绝不落到这里。
    """

    def fund_open_fund_info_em(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("[test] 应被 call_with_timeout 替身拦下，不应真正调用")


@pytest.fixture
def _clear_caches():
    """清空与本测试相关的所有进程级缓存，保证用例互相独立。"""
    from services import fund_monitor, market_data

    akp._macro_cache.clear()
    market_data._nav_cache.clear()
    fund_monitor._nav_cache.clear()
    yield
    akp._macro_cache.clear()
    market_data._nav_cache.clear()
    fund_monitor._nav_cache.clear()


@pytest.fixture
def _akshare_stubbed(monkeypatch):
    """把 AKShare 数据源接管成"按 indicator 返回对应帧"，不打网络。

    - ``akp.call_with_timeout``：模块属性重绑定（provider 内部函数级引用按全局名解析）。
    - ``AkshareProvider._get_ak``：返回哑对象，避免真实 ``import akshare``。
    """
    monkeypatch.setattr(akp, "call_with_timeout", _fake_call_with_timeout)
    monkeypatch.setattr(akp.AkshareProvider, "_get_ak", lambda self: _StubAk(), raising=True)


# ============================================================
# 层 1：provider 缓存键必须区分 indicator（修前必红）
# ============================================================

def test_provider_cache_key_distinguishes_indicator(_clear_caches, _akshare_stubbed):
    """同进程先取累计、再取单位 → 第二次必须拿回**单位口径**帧。

    修前：缓存键 ``ak_fund_nav_163406`` 共用 → 第二次直接命中累计帧，
    列名是「累计净值」、末值 8.5191 → 本用例红。
    修后：键纳入 indicator → 第二次重新取到单位帧（列名「单位净值」、末值 2.2401）。
    """
    prov = akp.AkshareProvider()

    accum = prov._fetch_fund_nav("163406", indicator="累计净值走势")
    assert "累计净值" in list(accum.columns), "累计走势帧应含「累计净值」列"
    assert float(accum["累计净值"].iloc[-1]) == pytest.approx(8.5191)

    unit = prov._fetch_fund_nav("163406", indicator="单位净值走势")
    assert "单位净值" in list(unit.columns), (
        "单位走势查询拿回了累计帧（缓存键未区分 indicator）: "
        f"columns={list(unit.columns)}")
    assert "累计净值" not in list(unit.columns), (
        f"单位走势查询污染成累计帧: columns={list(unit.columns)}")
    assert float(unit["单位净值"].iloc[-1]) == pytest.approx(2.2401)


# ============================================================
# 层 2：market_data 拒绝累计口径帧（修前必红）
# ============================================================

def _stub_stocks_history(monkeypatch, frame: pd.DataFrame):
    """把底层数据源 stocks.get_fund_nav_history 固定为给定帧（隔离 provider）。"""
    monkeypatch.setattr(
        "infra.data_source.market.stocks.get_fund_nav_history",
        lambda code, indicator="单位净值走势": frame)
    monkeypatch.setattr("services.tushare_data.is_configured",
                        lambda: False, raising=False)
    monkeypatch.setattr("requests.get", _raise_network)


def test_market_data_rejects_accum_frame(_clear_caches, monkeypatch):
    """喂一个**只有累计净值**的帧 → 绝不返回 8.5191，而是 "N/A"。

    修前：列名「累计净值」三个单位候选都不匹配 → ``num_cols[0]`` = 累计净值
    → 静默返回 nav="8.5191" → 本用例红。
    修后：显式拒绝累计列 → 走降级（Tushare 未配置）→ nav="N/A"。
    """
    from services import market_data

    _stub_stocks_history(monkeypatch, _accum_frame())
    out = market_data.get_fund_nav("163406")

    assert out.get("nav") != "8.5191", (
        f"累计净值被当单位净值返回（层 2 未修）: {out}")
    assert out.get("nav") == "N/A", (
        f"应显式拒绝累计口径并返回 N/A，实际: {out}")


def test_market_data_accepts_unit_frame(_clear_caches, monkeypatch):
    """正常单位口径帧 → 返回 2.2401（正向保护，防止"拒绝一切"过度修正）。"""
    from services import market_data

    _stub_stocks_history(monkeypatch, _unit_frame())
    out = market_data.get_fund_nav("163406")
    assert out.get("nav") == "2.2401", f"正常单位帧应返回 2.2401，实际: {out}"


# ============================================================
# 集成对撞：同一进程先累计、后单位（修前必红）
# ============================================================

def test_e2e_accum_then_unit_in_same_process(_clear_caches, _akshare_stubbed, monkeypatch):
    """复刻生产 A/B 对撞：先走累计链路，再走单位链路 → 单位必须仍是单位。

    链路（真实，不打网络）::

        fund_monitor.get_fund_nav_history(163406, 累计)   → 8.5191
        market_data.get_fund_nav(163406)                  → 必须 2.2401

    修前：第二条命中被污染的 provider 缓存 → 拿回累计帧 → market_data 又静默
    取「累计净值」→ 返回 8.5191 → 本用例红。
    修后（两层都修）：第二条取回单位帧 → 返回 2.2401。

    ⚠️ 说明层级：本用例是**集成验收**（钉住"先累计后单位"这一整条路径）；
    层 1 单独由 :func:`test_provider_cache_key_distinguishes_indicator` 钉，
    层 2 单独由 :func:`test_market_data_rejects_accum_frame` 钉。二者缺一，
    对应那条单层用例就会红。
    """
    from services import fund_monitor, market_data

    monkeypatch.setattr("services.tushare_data.is_configured",
                        lambda: False, raising=False)
    monkeypatch.setattr("requests.get", _raise_network)

    accum = fund_monitor.get_fund_nav_history("163406", days=3, force_refresh=True)
    assert accum, "累计链路应能取到数据（前置条件）"
    assert float(accum[-1]["nav"]) == pytest.approx(8.5191), (
        f"累计链路本身应返回 8.5191，实际: {accum}")

    unit = market_data.get_fund_nav("163406")
    assert unit.get("nav") == "2.2401", (
        "同进程先累计后单位，单位查询被污染成累计口径: "
        f"{unit}")
    assert unit.get("nav") != "8.5191", (
        f"单位查询拿回累计值（层 1/层 2 至少一层未修）: {unit}")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-rfEX"]))

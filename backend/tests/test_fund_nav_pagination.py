"""回归验证：fund_nav 单次 10500 行截断 —— 排行榜只有 43% 样本（BUG）

事故（服务器实测，2026-09-13，nav_date=20260911）
------------------------------------------------
``get_fund_nav_by_date()`` 只发起**一次** Tushare 调用::

    _call_tushare("fund_nav", {"nav_date": nav_date}, FIELDS)

而 Tushare fund_nav 单次调用**最多返回 10500 行**（服务端默认上限）。同一天
在服务器上按 offset 实测的真实总量::

    offset=0     limit=10000 → 10000 行
    offset=10000 limit=10000 → 10000 行
    offset=20000 limit=10000 →  4338 行   ← 不满一页 = 最后一页
    合计 24338 行

即修复前只拿到 **10500 / 24338 = 43%**，后 13838 行被静默丢掉。后果：
``fund_rank_build.py`` 产出的 ``fund_rank_ts.json`` 排行榜一直基于**不到一半
的样本**排名（实测 ``total_funds`` 只有 628 量级）。

为什么这个 bug 一直没人发现：单次调用返回 10500 行，看起来"很多"，打印的
日志 ``[TUSHARE-FUND-BATCH] ... 10500 条基金净值`` 也毫无异常；被丢掉的
那一半不会报错、不会告警，只是安静地缺席。

修法：offset 翻页取全（``_fetch_fund_nav_rows``），终止判据与 share_float
那套一致（单页取不满 = 真的取完），另配页数/行数双上限防死循环。

本文件全部离线运行：不发起任何网络请求。_call_tushare 全部打桩；唯一触到
urllib 的用例（探测+取全不重复请求首屏）也把 urlopen 换成了假实现。

⚠️ 断言一律写**绝对字面量**（7 行 / 3 页 / offsets {0,3,6} / 24338），
不从 ``_FUND_NAV_PAGE_SIZE`` 等常量推导期望值 —— 否则把常量改了用例照样绿。
"""
import json
import sys
import urllib.request
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ============================================================
# fixtures / helpers
# ============================================================

@pytest.fixture
def td():
    import services.tushare_data as tushare_data
    tushare_data._ts_cache.clear()
    yield tushare_data
    tushare_data._ts_cache.clear()


def _nav_row(i: int) -> dict:
    """复刻 fund_nav 的一行（ts_code 唯一，便于断言翻页不重叠/不漏行）。"""
    return {
        "ts_code": f"{i:06d}.OF",
        "ann_date": "20260911",
        "nav_date": "20260911",
        "unit_nav": 1.0 + i * 1e-6,
        "accum_nav": 1.5 + i * 1e-6,
        "adj_nav": None,
    }


def _paged_fake(all_rows, page_size, calls=None):
    """返回一个**认得 offset/limit 参数**的 `_call_tushare` 打桩。

    这是本文件的关键：忽略 params 的打桩对任何请求都返回同一份全量数据 ——
    用它测翻页，无论被测代码翻不翻页都拿到全量，bug 会被测成"通过"。真实
    Tushare 是按 offset 切片的，打桩必须复刻这个行为。
    """
    def _call(api_name, params, fields=""):
        if calls is not None:
            calls.append(dict(params))
        offset = int(params.get("offset", 0) or 0)
        limit = int(params.get("limit", page_size) or page_size)
        return list(all_rows[offset:offset + limit])[:page_size]
    return _call


# ============================================================
# A. 核心：三页 fixture（满页 / 满页 / 不满页）必须被取满
# ============================================================

def test_three_pages_are_fully_drained(td, monkeypatch):
    """核心用例：第一页满、第二页满、第三页不满 —— 三页都要取回来。

    缺陷版本只发起一次调用 → 只能拿到第 1 页的 3 行，这条必然红。
    断言全部写死字面量：7 行 / 3 页 / offsets {0, 3, 6}。
    """
    rows = [_nav_row(i) for i in range(7)]
    calls = []
    monkeypatch.setattr(td, "_FUND_NAV_PAGE_SIZE", 3)
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 3, calls))

    out = td.get_fund_nav_by_date("20260911")

    assert len(out) == 7, f"7 行数据应全部取回，实际 {len(out)}（翻页没生效？）"
    assert [r["ts_code"] for r in out] == [f"{i:06d}.OF" for i in range(7)]
    assert len(calls) == 3, f"7 行 / 每页 3 → 应调用 3 次，实际 {len(calls)}"
    assert [c["offset"] for c in calls] == [0, 3, 6], calls
    assert {c["limit"] for c in calls} == {3}, calls


def test_real_market_size_24338_rows_needs_three_pages(td, monkeypatch):
    """按线上真实体量复刻：24338 行 / 每页 10000 → 10000 + 10000 + 4338。

    24338 是 2026-09-11 在服务器上实测的真实总量，直接写死在这里 —— 若哪天
    有人把 ``_FUND_NAV_PAGE_SIZE`` 调小到只剩一两页就能装下"看起来够多"的
    数据（比如 6000），这条会立刻红，而不是悄悄回到只取 43% 的老问题。
    """
    rows = [_nav_row(i) for i in range(24338)]
    calls = []
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 10000, calls))

    out = td.get_fund_nav_by_date("20260911")

    assert len(out) == 24338, f"应取回全部 24338 行，实际 {len(out)}"
    assert len(calls) == 3, f"24338 行 / 每页 10000 → 3 次调用，实际 {len(calls)}"
    assert [c["offset"] for c in calls] == [0, 10000, 20000], calls
    # 修复前只取第一页 = 10500 行，这里显式钉死"不是 10500"
    assert len(out) != 10500
    assert len(out) > 20000, f"只有 {len(out)} 行，明显还被截着"


def test_single_page_only_gets_43_percent_fault_injection(td, monkeypatch):
    """故障注入：只允许翻 1 页 → 确实只剩 10000 行，证明上面的用例是活的。

    这条不是测产品代码，是**证明 A 组用例构造的数据真的会触发截断** ——
    否则"取回 24338 行"可能只是"翻页开着也无所谓"的假阳性。
    """
    rows = [_nav_row(i) for i in range(24338)]
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 10000))
    monkeypatch.setattr(td, "_FUND_NAV_MAX_PAGES", 1)

    out, meta = td.get_fund_nav_by_date_with_meta("20260911")

    assert len(out) == 10000, f"只翻 1 页应只有 10000 行，实际 {len(out)}"
    assert meta["complete"] is False, "被上限截断时必须诚实标注 complete=False"
    assert meta["pages"] == 1, meta
    assert "上限" in meta["truncated_reason"], meta


# ============================================================
# B. 终态判定：不满一页 = 真的取完了
# ============================================================

def test_short_page_means_complete_and_stops_calling(td, monkeypatch):
    """唯一"取完了"的判定是单页取不满 —— 此时不再多打一次请求。

    这也是**兼容既有测试**的关键：老用例用忽略 params 的打桩返回 3~9 行，
    远小于 10000，于是只调用一次就结束，行为与修复前完全一致。
    """
    rows = [_nav_row(i) for i in range(2)]
    calls = []
    monkeypatch.setattr(td, "_FUND_NAV_PAGE_SIZE", 3)
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 3, calls))

    out, meta = td.get_fund_nav_by_date_with_meta("20260911")

    assert meta["complete"] is True, meta
    assert meta["pages"] == 1, f"取不满一页就该停，实际打了 {meta['pages']} 次"
    assert len(calls) == 1, calls
    assert calls[0]["offset"] == 0
    assert calls[0]["limit"] == 3
    assert len(out) == 2


def test_exactly_full_page_triggers_one_more_call(td, monkeypatch):
    """边界：行数正好等于页大小 → 无法判断后面还有没有，必须再翻一页确认。

    反面：如果只在 len(page) <= page_size 时停止，最后正好满页的数据会被
    误判为"已取完"，又回到静默丢数据的老问题。
    """
    rows = [_nav_row(i) for i in range(3)]
    calls = []
    monkeypatch.setattr(td, "_FUND_NAV_PAGE_SIZE", 3)
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 3, calls))

    out, meta = td.get_fund_nav_by_date_with_meta("20260911")

    assert meta["pages"] == 2, f"正好满页要再翻一次确认，实际 {meta['pages']}"
    assert meta["complete"] is True
    assert meta["rows"] == 3, "第二页为空，不能把已有行重复累加"
    assert {c["offset"] for c in calls} == {0, 3}
    assert len(out) == 3


def test_empty_result_is_complete_and_does_not_loop(td, monkeypatch):
    """空结果（非交易日）必须一次就停，不能翻满 12 页。"""
    calls = []
    monkeypatch.setattr(td, "_call_tushare", _paged_fake([], 10000, calls))

    out, meta = td.get_fund_nav_by_date_with_meta("20260101")

    assert out == []
    assert meta["complete"] is True
    assert meta["pages"] == 1
    assert len(calls) == 1


# ============================================================
# C. 防死循环：页数 / 行数双上限
# ============================================================

def test_always_full_pages_are_capped_by_max_pages(td, monkeypatch):
    """防死循环：上游每页都返回满页（offset 失效 / 数据异常）时必须停下。

    停在 7 页（临时把上限改成 7），complete=False，且 reason 里带"上限"。
    """
    def _always_full(api_name, params, fields=""):
        offset = int(params.get("offset", 0) or 0)
        return [_nav_row(offset + i) for i in range(10)]

    monkeypatch.setattr(td, "_FUND_NAV_PAGE_SIZE", 10)
    monkeypatch.setattr(td, "_FUND_NAV_MAX_PAGES", 7)
    monkeypatch.setattr(td, "_FUND_NAV_MAX_ROWS", 10 ** 9)  # 让页数上限成为生效的那个
    monkeypatch.setattr(td, "_call_tushare", _always_full)

    out, meta = td.get_fund_nav_by_date_with_meta("20260911")

    assert meta["pages"] == 7, f"必须被页数上限钉死，实际 {meta['pages']}"
    assert meta["complete"] is False
    assert "上限" in meta["truncated_reason"], meta
    assert len(out) == 70


def test_row_cap_is_honoured_and_reported(td, monkeypatch):
    """防死循环第二重：累计行数上限。命中同样要标 complete=False。"""
    rows = [_nav_row(i) for i in range(100)]

    monkeypatch.setattr(td, "_FUND_NAV_PAGE_SIZE", 10)
    monkeypatch.setattr(td, "_FUND_NAV_MAX_PAGES", 100)
    monkeypatch.setattr(td, "_FUND_NAV_MAX_ROWS", 25)
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 10))

    out, meta = td.get_fund_nav_by_date_with_meta("20260911")

    assert meta["rows"] == 30, meta          # 3 页 × 10 行，第 3 页后才超 25
    assert meta["pages"] == 3, meta
    assert meta["complete"] is False
    assert "25" in meta["truncated_reason"], meta


def test_default_page_budget_can_drain_real_market(td):
    """容量守卫：默认页配置必须装得下实测的 24338 行，否则线上必然被截断。

    这条直接对真实常量做算术，是**容量不变式**而不是行为断言 —— 若有人把
    页大小调小却忘了同步调大页数上限，线上会静默回到只取一部分的老问题。
    """
    capacity = td._FUND_NAV_PAGE_SIZE * td._FUND_NAV_MAX_PAGES
    assert capacity >= 24338, f"默认翻页容量 {capacity} < 实测 24338 行，线上会被截断"
    assert td._FUND_NAV_MAX_ROWS >= 24338, td._FUND_NAV_MAX_ROWS
    # 实测单页 10000 是服务端上限内的安全值，不能盲目调大到 10500 以上
    assert td._FUND_NAV_PAGE_SIZE <= 10500, td._FUND_NAV_PAGE_SIZE


# ============================================================
# D. 翻页正确性：不重叠、不漏行
# ============================================================

def test_pages_do_not_overlap_and_do_not_skip(td, monkeypatch):
    """翻页不得重复累加也不得跳行：每一行恰好出现一次。

    若有人误把 ``offset += page_size`` 写成 ``offset = 0``（每次都从 0 取），
    这条会立刻红。
    """
    rows = [_nav_row(i) for i in range(25)]
    monkeypatch.setattr(td, "_FUND_NAV_PAGE_SIZE", 10)
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 10))

    out, meta = td.get_fund_nav_by_date_with_meta("20260911")

    assert meta["rows"] == 25, f"行数应为 25，实际 {meta['rows']}（翻页重叠或漏行）"
    codes = [r["ts_code"] for r in out]
    assert len(set(codes)) == 25, "出现了重复行"
    assert codes == [f"{i:06d}.OF" for i in range(25)], "顺序被打乱或漏行"


# ============================================================
# E. 探测模式（max_pages=1）：控制 Tushare 调用次数
# ============================================================

def test_probe_mode_fetches_exactly_one_page(td, monkeypatch):
    """探测模式只翻一页 —— find_latest_trade_date 往前试 10 天就靠它控量。"""
    rows = [_nav_row(i) for i in range(24338)]
    calls = []
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 10000, calls))

    out, meta = td.get_fund_nav_by_date_with_meta("20260911", max_pages=1)

    assert len(out) == 10000, out and len(out)
    assert len(calls) == 1, f"探测只应打 1 次请求，实际 {len(calls)}"
    assert meta["pages"] == 1
    assert meta["complete"] is False, "只翻一页本来就没取全，不能谎报 complete"


def test_probe_mode_does_not_emit_truncation_warning(td, monkeypatch, capsys):
    """主动探测造成的"没取全"不算异常，不该刷告警 —— 否则找交易日会刷一屏。

    取全模式（max_pages=0）被上限截断时**必须**告警，这条同时钉死两者的区别。
    """
    rows = [_nav_row(i) for i in range(24338)]

    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 10000))
    monkeypatch.setattr(td, "_FUND_NAV_MAX_PAGES", 1)

    capsys.readouterr()
    td.get_fund_nav_by_date("20260911", max_pages=1)
    probe_out = capsys.readouterr().out
    assert "未取全" not in probe_out, f"探测模式不应打未取全告警: {probe_out}"

    capsys.readouterr()
    td.get_fund_nav_by_date("20260911")
    full_out = capsys.readouterr().out
    assert "未取全" in full_out, f"取全模式被截断必须告警: {full_out}"


def test_probe_then_full_does_not_refetch_first_page(td, monkeypatch):
    """探测 + 取全 只打 3 次 HTTP，不是 4 次 —— 调用次数缓解方案的实证。

    这是唯一一条触到 urllib 的用例：把 ``urlopen`` 换成假实现，走**真实**
    ``_call_tushare``（进而走真实的进程内缓存）。因为探测与取全的第一页
    params 完全相同（offset=0, limit=10000），缓存键一致 → 首屏不会重复请求。

    若哪天有人在 _call_tushare 的 cache_key 里漏掉 params（或探测时多传了
    额外参数），这里会立刻变成 4 次请求。
    """
    server = [_nav_row(i) for i in range(24338)]
    fields = ["ts_code", "ann_date", "nav_date", "unit_nav", "accum_nav", "adj_nav"]
    requests_seen = []

    class _FakeResp:
        def __init__(self, obj):
            self._obj = obj

        def read(self):
            return json.dumps(self._obj, ensure_ascii=False).encode("utf-8")

    def _fake_urlopen(req, *args, **kwargs):
        payload = json.loads(req.data.decode("utf-8"))
        params = payload.get("params") or {}
        offset = int(params.get("offset", 0) or 0)
        limit = int(params.get("limit", 10000) or 10000)
        requests_seen.append((offset, limit))
        window = server[offset:offset + limit]
        return _FakeResp({
            "code": 0,
            "data": {
                "fields": fields,
                "items": [[r[f] for f in fields] for r in window],
            },
        })

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(td, "_get_token", lambda: "dummy-token")

    probe = td.get_fund_nav_by_date("20260911", max_pages=1)
    assert len(probe) == 10000, len(probe)

    full = td.get_fund_nav_by_date("20260911")
    assert len(full) == 24338, len(full)

    assert requests_seen == [(0, 10000), (10000, 10000), (20000, 10000)], (
        f"探测+取全应共 3 次 HTTP 且首屏不重复请求，实际 {requests_seen}"
    )


# ============================================================
# F. 兼容性：签名与返回类型不变
# ============================================================

def test_return_type_is_plain_list_not_tuple(td, monkeypatch):
    """老调用方按 list 解包（``{n["ts_code"]: n for n in navs}``），
    返回 tuple 会静默错 —— 钉死返回类型。"""
    rows = [_nav_row(i) for i in range(4)]
    monkeypatch.setattr(td, "_FUND_NAV_PAGE_SIZE", 3)
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 3))

    out = td.get_fund_nav_by_date("20260911")

    assert isinstance(out, list)
    assert not isinstance(out, tuple), "老调用方按 list 解包，返回 tuple 会静默错"
    assert isinstance(out[0], dict)


def test_with_meta_returns_same_rows_as_plain_version(td, monkeypatch):
    """两个 API 的 rows 必须逐行一致，meta 只是附加信息。"""
    rows = [_nav_row(i) for i in range(7)]
    monkeypatch.setattr(td, "_FUND_NAV_PAGE_SIZE", 3)
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 3))

    plain = td.get_fund_nav_by_date("20260911")
    with_meta, meta = td.get_fund_nav_by_date_with_meta("20260911")

    assert plain == with_meta
    assert meta["rows"] == 7
    assert meta["complete"] is True
    assert set(meta) == {"complete", "pages", "rows", "truncated_reason"}, sorted(meta)

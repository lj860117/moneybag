"""回归验证：share_float 单次 6000 行截断（BUG-4）

事故（服务器实测，2026-09-04 ~ 2026-10-04 窗口）：
    Tushare share_float 单次调用**硬上限 6000 行**（limit 传 8000 / 20000
    实测仍只返回 6000）。而该窗口真实共 **41840 行、164 只票**，其中
    001257.SZ 一只票就独占 5964 行（5964 个基金股东）。于是单次调用聚合后
    只剩 **4 只票**（301563 / 920222 / 001257 / 603683），**另外 160 只被
    静默丢掉**。

    更糟的是丢掉的恰恰是最重要的：
        单页   Top1 = 301563.SZ  41.09%
        翻页全量 Top1 = 301507.SZ 138.00%
    一个"按影响最大优先排序"的预警，却把影响最大的 160 只票先丢了 ——
    这直接违背该信号存在的意义。

修法：offset 翻页取全（_fetch_share_float_rows）。offset 翻页的稳定性
已在服务器上验证（B[:5500] == A[500:]，同一 offset 重复调用结果一致），
因此**不做去重**：源数据里存在字段完全相同的两行（同一持股平台的两只基金、
同一笔股数），去重反而会漏加 float_share。

本文件全部离线运行：不发起任何网络请求，`_call_tushare` 全部打桩。
"""
import sys
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


def _row(ts_code, float_date, share=None, ratio=None, holder="某股东"):
    """复刻 share_float 的一行（float_share 单位是【股】）。"""
    return {
        "ts_code": ts_code,
        "float_date": float_date,
        "float_share": share,
        "float_ratio": ratio,
        "holder_name": holder,
        "share_type": "定向增发",
    }


def _paged_fake(all_rows, page_size, calls=None):
    """返回一个**认得 offset/limit 参数**的 `_call_tushare` 打桩。

    这是本文件的关键：项目里既有的 `_fake_tushare` 忽略 params，对任何请求
    都返回同一份数据 —— 用它测翻页，无论被测代码翻不翻页都拿到全量，
    bug 会被测成"通过"。真实 Tushare 是按 offset 切片的，打桩必须复刻
    这个行为，否则测不出截断。

    Args:
        all_rows: 服务端"全量"数据。
        page_size: 单次返回行数上限（模拟 Tushare 的 6000）。
        calls: 可选 list，会被追加每次调用的 params，供断言翻页次数。
    """
    def _call(api_name, params, fields=""):
        if calls is not None:
            calls.append(dict(params))
        offset = int(params.get("offset", 0) or 0)
        limit = int(params.get("limit", page_size) or page_size)
        # 模拟服务端：先按 offset 切片，再套单次返回上限
        return list(all_rows[offset:offset + limit])[:page_size]
    return _call


# ============================================================
# A. 单次截断：翻页确实能捞回被丢掉的股票
# ============================================================

def test_pagination_recovers_stocks_lost_by_single_call(td, monkeypatch):
    """核心用例：行数超过单次上限时，翻页聚合出的股票数必须 > 单页。

    构造（等比缩小真实场景）：
      * 001257.SZ 一只票独占 500 行（模拟它独占 5964 行把整页吃满）
      * 另外 60 只票各 1 行，float_ratio 依次 60.0 → 1.0（都比 001257 的 0.44 高）
      * 单次上限 500 行
      → 单页只能聚出 001257（1 只）；翻页后应聚出 61 只，且 Top1 是 60.0% 那只。
    """
    rows = [_row("001257.SZ", "20260930", 1743.0, 0.44 / 500, f"基金{i}") for i in range(500)]
    rows += [_row(f"60{i:04d}.SH", "20260930", 1000.0, 60.0 - i, "股东") for i in range(60)]

    monkeypatch.setattr(td, "_SHARE_FLOAT_PAGE_SIZE", 500)
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 500))

    items, meta = td.get_upcoming_unlocks_with_meta(limit=100)

    codes = {m["ts_code"] for m in items}
    assert len(codes) == 61, f"应聚出 61 只票，实际 {len(codes)}"
    assert meta["complete"] is True, meta
    assert meta["pages"] == 2, f"560 行 / 每页 500 → 应 2 页，实际 {meta['pages']}"
    assert meta["rows"] == 560, meta
    # 排序仍然是"按合计 float_ratio 降序"，Top1 必须是影响最大的那只
    assert items[0]["ts_code"] == "600000.SH", items[0]["ts_code"]
    assert abs(items[0]["float_ratio"] - 60.0) < 1e-9


def test_single_call_truncation_is_reproduced_without_pagination(td, monkeypatch):
    """反例固化：把翻页关掉（页大小调到极大=只取一页），股票确实会丢。

    这条不是测产品代码，是**证明上面那条用例构造的数据真的会触发截断** ——
    否则上面那条用例可能只是"翻页开着也无所谓"的假阳性。
    """
    rows = [_row("001257.SZ", "20260930", 1743.0, 0.44 / 500, f"基金{i}") for i in range(500)]
    rows += [_row(f"60{i:04d}.SH", "20260930", 1000.0, 60.0 - i, "股东") for i in range(60)]

    # 一页就装得下全部 560 行 → 只调用一次，等于"取全量"
    monkeypatch.setattr(td, "_SHARE_FLOAT_PAGE_SIZE", 100000)
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 100000))
    assert len({m["ts_code"] for m in td.get_upcoming_unlocks(limit=100)}) == 61

    # 一页只能装 500 行，且只允许翻 1 页 → 只剩 001257 一只票（复刻线上事故）
    monkeypatch.setattr(td, "_SHARE_FLOAT_PAGE_SIZE", 500)
    monkeypatch.setattr(td, "_SHARE_FLOAT_MAX_PAGES", 1)
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 500))
    items, meta = td.get_upcoming_unlocks_with_meta(limit=100)
    assert {m["ts_code"] for m in items} == {"001257.SZ"}, items
    assert meta["complete"] is False, "被上限截断时必须诚实标注 complete=False"


# ============================================================
# B. 终态判定：不满一页 = 真的取完了
# ============================================================

def test_short_page_means_complete_and_stops_calling(td, monkeypatch):
    """唯一"取完了"的判定是单页取不满 —— 此时不再多打一次请求。

    这也是**兼容既有测试**的关键：老用例用忽略 params 的打桩返回 3~9 行，
    远小于 6000，于是只调用一次就结束，行为与修复前完全一致。
    """
    rows = [_row("000001.SZ", "20261001", 100.0, 5.0, "A")]
    calls = []
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 6000, calls))

    items, meta = td.get_upcoming_unlocks_with_meta()

    assert meta["complete"] is True, meta
    assert meta["pages"] == 1, f"取不满一页就该停，实际打了 {meta['pages']} 次"
    assert len(calls) == 1, calls
    # 第一页确实带上了分页参数（便于 _call_tushare 的 params 级缓存区分）
    assert calls[0]["offset"] == 0
    assert calls[0]["limit"] == 6000
    assert len(items) == 1


def test_exactly_full_page_triggers_one_more_call(td, monkeypatch):
    """边界：行数正好等于页大小 → 无法判断后面还有没有，必须再翻一页确认。

    反面：如果只在 len(page) <= page_size 时停止，最后正好满页的数据会被
    误判为"已取完"，又回到静默丢数据的老问题。
    """
    # 三行必须是三个**不同的**解禁事件（股东名不同），否则会按"同一事件的
    # 多次公告"被去重掉，本用例要测的是翻页终态、不是去重
    rows = [_row("000001.SZ", "20261001", 100.0, 5.0, f"股东{i}") for i in range(3)]
    calls = []
    monkeypatch.setattr(td, "_SHARE_FLOAT_PAGE_SIZE", 3)
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 3, calls))

    items, meta = td.get_upcoming_unlocks_with_meta()

    assert meta["pages"] == 2, f"正好满页要再翻一次确认，实际 {meta['pages']}"
    assert meta["complete"] is True
    assert meta["rows"] == 3, "第二页为空，不能把已有行重复累加"
    assert meta["duplicate_rows"] == 0, meta
    assert {c["offset"] for c in calls} == {0, 3}
    assert len(items) == 1
    assert abs(items[0]["float_share"] - 300.0) < 1e-9
    assert items[0]["holder_count"] == 3


def test_empty_result_is_complete_and_does_not_loop(td, monkeypatch):
    calls = []
    monkeypatch.setattr(td, "_call_tushare", _paged_fake([], 6000, calls))

    items, meta = td.get_upcoming_unlocks_with_meta()

    assert items == []
    assert meta["complete"] is True
    assert meta["pages"] == 1
    assert len(calls) == 1


# ============================================================
# C. 防死循环：三重上限
# ============================================================

def test_infinite_full_pages_are_capped_by_max_pages(td, monkeypatch):
    """防死循环：上游每页都返回满页（模拟 offset 失效 / 数据异常）时，
    必须停在 MAX_PAGES，且把 complete 标成 False —— 不能假装取完了。"""
    def _always_full(api_name, params, fields=""):
        return [_row("000001.SZ", "20261001", 1.0, 0.0001, f"H{params.get('offset')}_{i}")
                for i in range(10)]

    monkeypatch.setattr(td, "_SHARE_FLOAT_PAGE_SIZE", 10)
    monkeypatch.setattr(td, "_SHARE_FLOAT_MAX_PAGES", 7)
    monkeypatch.setattr(td, "_SHARE_FLOAT_MAX_ROWS", 10 ** 9)  # 让页数上限成为生效的那个
    monkeypatch.setattr(td, "_call_tushare", _always_full)

    items, meta = td.get_upcoming_unlocks_with_meta()

    assert meta["pages"] == 7, f"必须被页数上限钉死，实际 {meta['pages']}"
    assert meta["complete"] is False
    assert "上限" in meta["truncated_reason"], meta
    assert meta["rows"] == 70


def test_row_cap_is_honoured_and_reported(td, monkeypatch):
    """防死循环第二重：累计行数上限。命中同样要标 complete=False。"""
    rows = [_row(f"{i:06d}.SZ", "20261001", 1.0, float(i), f"H{i}") for i in range(100)]

    monkeypatch.setattr(td, "_SHARE_FLOAT_PAGE_SIZE", 10)
    monkeypatch.setattr(td, "_SHARE_FLOAT_MAX_PAGES", 100)
    monkeypatch.setattr(td, "_SHARE_FLOAT_MAX_ROWS", 25)
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 10))

    items, meta = td.get_upcoming_unlocks_with_meta()

    assert meta["rows"] == 30, meta          # 3 页 × 10 行，第 3 页后才超 25
    assert meta["pages"] == 3, meta
    assert meta["complete"] is False
    assert "25" in meta["truncated_reason"], meta
    assert len(items) == 30


def test_pagination_does_not_duplicate_rows_across_pages(td, monkeypatch):
    """翻页不得重复累加：同一行只能被算一次。

    真实分页游标已验证稳定（B[:5500] == A[500:]），所以这里断言的是
    "切片不重叠" —— 若有人误把 `offset += len(page)` 写成 `offset = 0`
    （每次都从 0 取），这条会立刻红。
    """
    rows = [_row("000001.SZ", "20261001", 1.0, 1.0, f"H{i}") for i in range(25)]
    monkeypatch.setattr(td, "_SHARE_FLOAT_PAGE_SIZE", 10)
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 10))

    items, meta = td.get_upcoming_unlocks_with_meta()

    assert meta["rows"] == 25, f"行数应为 25，实际 {meta['rows']}（翻页重叠或漏行）"
    assert abs(items[0]["float_share"] - 25.0) < 1e-9, items[0]
    assert items[0]["holder_count"] == 25, items[0]


# ============================================================
# D. 顺序：聚合 → 排序 → 截断（不能被翻页破坏）
# ============================================================

def test_truncate_after_aggregate_still_holds_with_pagination(td, monkeypatch):
    """翻页改变了"取到哪些行"，但**不能**改变 聚合→排序→截断 的顺序。

    构造：000001.SZ 拆成 30 行各 1.0%（合计 30.0%，全市场第一），
    另有 10 只票各 1 行 5.0%；页大小小到需要翻页。
      * 正确：000001 以 30.0 排第 1。
      * 若顺序反了（先按行截断再聚合）：前 N 行全是 5.0% 的票，000001 出局。
    """
    rows = [_row("000001.SZ", "20261001", 100.0, 1.0, f"股东{i}") for i in range(30)]
    rows += [_row(f"3000{i:02d}.SZ", "20261001", 10.0, 5.0, "X") for i in range(10)]

    monkeypatch.setattr(td, "_SHARE_FLOAT_PAGE_SIZE", 7)
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 7))

    items, meta = td.get_upcoming_unlocks_with_meta(limit=10)

    assert meta["rows"] == 40, meta
    assert items[0]["ts_code"] == "000001.SZ", f"Top1 = {items[0]['ts_code']}（顺序错了）"
    assert abs(items[0]["float_ratio"] - 30.0) < 1e-9
    assert items[0]["holder_count"] == 30
    assert len(items) == 10


def test_meta_key_set_is_identical_on_empty_and_non_empty_paths(td, monkeypatch):
    """meta 的 key 集合必须稳定，否则调用方在空结果时 KeyError。

    空结果走的是 `if not rows: return [], meta` 早退分支，很容易忘记补
    "groups" —— 那种 bug 只在"某天全市场恰好没有解禁"时才炸，最难复现。
    """
    monkeypatch.setattr(td, "_call_tushare", _paged_fake([], 6000))
    _, meta_empty = td.get_upcoming_unlocks_with_meta()

    monkeypatch.setattr(td, "_call_tushare",
                        _paged_fake([_row("000001.SZ", "20261001", 1.0, 1.0, "A")], 6000))
    _, meta_full = td.get_upcoming_unlocks_with_meta()

    assert set(meta_empty) == set(meta_full), (sorted(meta_empty), sorted(meta_full))
    assert meta_empty["groups"] == 0
    assert meta_full["groups"] == 1


def test_get_upcoming_unlocks_wrapper_returns_plain_list(td, monkeypatch):
    """兼容：`get_upcoming_unlocks()` 的返回形状必须与修复前一致（list[dict]），
    元信息只在新 API `get_upcoming_unlocks_with_meta()` 里提供。"""
    rows = [_row("000001.SZ", "20261001", 100.0, 5.0, "A")]
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 6000))

    out = td.get_upcoming_unlocks()

    assert isinstance(out, list)
    assert not isinstance(out, tuple), "老调用方按 list 解包，返回 tuple 会静默错"
    assert out[0]["ts_code"] == "000001.SZ"


# ============================================================
# E. 按"解禁事件"去重（同一笔解禁的多次公告只算一次）
# ============================================================

def _row7(ts_code, float_date, share, ratio, holder, ann_date, share_type="首发原始股"):
    """带 ann_date 的完整一行（接口实际返回 7 个字段）。"""
    r = _row(ts_code, float_date, share, ratio, holder)
    r["ann_date"] = ann_date
    r["share_type"] = share_type
    return r


def test_same_unlock_announced_twice_is_counted_once(td, monkeypatch):
    """核心用例：原始公告 + 提示性公告 是同一笔解禁，只能算一次。

    真实形态（301507.SZ 杭州民生药业 238000000 股 float_date=20260907）：
        ann_date=20230904  ← IPO 时的原始公告
        ann_date=20260831  ← 解禁前 7 天的提示性公告
    六个事件字段完全相同，只有 ann_date 不同。不去重会得出 138.00% 总股本
    这种物理上不可能的数字，去重后是 69.00%。
    """
    rows = [
        _row7("301507.SZ", "20260907", 238000000.0, 66.75, "杭州民生药业", "20230904"),
        _row7("301507.SZ", "20260907", 238000000.0, 66.75, "杭州民生药业", "20260831"),
    ]
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 6000))

    items, meta = td.get_upcoming_unlocks_with_meta()

    assert len(items) == 1
    assert abs(items[0]["float_share"] - 238000000.0) < 1e-6, items[0]["float_share"]
    assert abs(items[0]["float_ratio"] - 66.75) < 1e-9
    assert items[0]["holder_count"] == 1, "同一股东只应数一次"
    assert meta["rows"] == 2 and meta["duplicate_rows"] == 1 and meta["rows_used"] == 1, meta


def test_dedupe_key_excludes_ann_date_on_purpose(td, monkeypatch):
    """反例固化：ann_date 若被加进去重键，去重会完全失效。

    这是本次最容易踩的坑 —— ann_date 看起来"能区分两行"，但它区分的是
    **公告**不是**解禁事件**。加进 key 就会让合计值重新变回 2 倍。
    """
    # 守卫：ann_date 必须被请求下来（否则下面这个判断在代码里无从核对），
    # 且**不能**出现在默认去重键里。先取默认值存下来 —— 本用例后面会临时
    # 改这个全局，断言必须对着默认值做，否则就是自己验自己。
    default_key = tuple(td._SHARE_FLOAT_DEDUPE_FIELDS)
    assert "ann_date" in td._SHARE_FLOAT_FIELDS
    assert "ann_date" not in default_key

    rows = [
        _row7("301507.SZ", "20260907", 238000000.0, 66.75, "杭州民生药业", "20230904"),
        _row7("301507.SZ", "20260907", 238000000.0, 66.75, "杭州民生药业", "20260831"),
    ]
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 6000))
    assert td.get_upcoming_unlocks()[0]["float_ratio"] == pytest.approx(66.75)

    # 把 ann_date 加进 key（错误做法）→ 合计值回到 2 倍
    monkeypatch.setattr(
        td, "_SHARE_FLOAT_DEDUPE_FIELDS", default_key + ("ann_date",),
    )
    assert td.get_upcoming_unlocks()[0]["float_ratio"] == pytest.approx(133.5)


def test_different_holders_with_identical_share_counts_are_not_merged(td, monkeypatch):
    """误伤反例：两只不同基金恰好持有相同股数 → 是两笔不同的解禁，不能合并。

    holder_name 在去重键里，所以它们不会被误删。这条守护的是"去重会不会
    把真实数据吃掉"这个主要风险。
    """
    rows = [
        _row7("001257.SZ", "20260930", 1743.0, 0.1, "长城久富核心成长混合", "20260831"),
        _row7("001257.SZ", "20260930", 1743.0, 0.1, "长城久嘉创新成长混合", "20260831"),
        _row7("001257.SZ", "20260930", 1053.0, 0.06, "长城医疗保健混合", "20260831"),
    ]
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 6000))

    items, meta = td.get_upcoming_unlocks_with_meta()

    assert meta["duplicate_rows"] == 0, f"不同股东被误删: {meta}"
    assert abs(items[0]["float_share"] - (1743.0 * 2 + 1053.0)) < 1e-6
    assert items[0]["holder_count"] == 3


def test_distinct_unlock_events_on_same_day_are_not_merged(td, monkeypatch):
    """反向验证：同股同日、同一股东但**不同股数/不同类型**是真·两笔，不能合并。"""
    rows = [
        _row7("000001.SZ", "20261001", 1000000.0, 5.0, "股东A", "20240901", "首发原始股"),
        _row7("000001.SZ", "20261001", 2000000.0, 8.0, "股东A", "20240901", "股权激励限售流通"),
    ]
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 6000))

    items, meta = td.get_upcoming_unlocks_with_meta()

    assert meta["duplicate_rows"] == 0, meta
    assert abs(items[0]["float_share"] - 3000000.0) < 1e-6
    assert abs(items[0]["float_ratio"] - 13.0) < 1e-9


def test_dedupe_works_across_page_boundaries(td, monkeypatch):
    """去重必须在翻页拼合**之后**做：两次公告可能被分页边界切开。

    若有人把去重挪进翻页循环里逐页做，这条会红。
    """
    rows = [
        _row7("000001.SZ", "20261001", 1000.0, 5.0, "A", "20240901"),
        _row7("000002.SZ", "20261001", 1000.0, 4.0, "B", "20240901"),
        # 下一页：与第 1 行同一笔解禁的提示性公告
        _row7("000001.SZ", "20261001", 1000.0, 5.0, "A", "20260920"),
    ]
    monkeypatch.setattr(td, "_SHARE_FLOAT_PAGE_SIZE", 2)
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 2))

    items, meta = td.get_upcoming_unlocks_with_meta()

    assert meta["pages"] == 2, meta
    assert meta["duplicate_rows"] == 1, f"跨页的重复没被去掉: {meta}"
    by_code = {m["ts_code"]: m for m in items}
    assert abs(by_code["000001.SZ"]["float_ratio"] - 5.0) < 1e-9
    assert abs(by_code["000002.SZ"]["float_ratio"] - 4.0) < 1e-9


def test_dedupe_keeps_first_occurrence_and_preserves_order(td, monkeypatch):
    """去重保留每组第一次出现的行、且不打乱顺序（保证 holder_names 顺序稳定）。"""
    rows = [
        _row7("000001.SZ", "20261001", 1000.0, 5.0, "A", "20240901"),
        _row7("000001.SZ", "20261001", 1000.0, 5.0, "A", "20260920"),
        _row7("000001.SZ", "20261001", 2000.0, 3.0, "B", "20240901"),
    ]
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 6000))

    items = td.get_upcoming_unlocks()

    assert items[0]["holder_names"] == ["A", "B"], items[0]["holder_names"]
    assert abs(items[0]["float_share"] - 3000.0) < 1e-6


def test_dedupe_tolerates_missing_and_blank_fields(td, monkeypatch):
    """脏值不得让去重抛异常，也不得把两笔不同的"空行"合并成一笔。"""
    rows = [
        {"ts_code": "000001.SZ", "float_date": None, "float_share": None,
         "float_ratio": None, "holder_name": None, "share_type": None, "ann_date": None},
        {"ts_code": "000001.SZ", "float_date": "", "float_share": "",
         "float_ratio": "", "holder_name": "", "share_type": "", "ann_date": ""},
    ]
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 6000))

    items, meta = td.get_upcoming_unlocks_with_meta()  # 不抛异常即通过

    # None 与 "" 都规范化成 ""，所以这两行会被判为同一笔（脏数据下可接受：
    # 它们对合计值的贡献都是 0，合并不会改变任何数字）
    assert meta["duplicate_rows"] == 1, meta
    assert abs(items[0]["float_share"]) < 1e-9


# ============================================================
# F. 与 signal_scout 的端到端串联
# ============================================================

def test_unlock_signals_cover_stocks_beyond_the_first_page(td, monkeypatch):
    """端到端：被 6000 行截断挡住的票，修复后能真的产出解禁信号。"""
    import services.signal_scout as ss

    ss._signal_cache.clear()
    ss._name_cache.clear()
    ss._name_map_attempt_ts = 0.0

    rows = [_row("001257.SZ", "20260930", 1743.0, 0.00088, f"基金{i}") for i in range(500)]
    rows += [_row(f"60{i:04d}.SH", "20260930", 1000000.0, 60.0 - i, "股东") for i in range(20)]

    monkeypatch.setattr(td, "_SHARE_FLOAT_PAGE_SIZE", 500)
    monkeypatch.setattr(td, "_call_tushare", _paged_fake(rows, 500))
    monkeypatch.setattr(td, "_get_stock_names", lambda: {})

    sigs = ss._collect_unlock_signals()

    got = {s["codes"][0] for s in sigs}
    assert len(sigs) == 10, f"_collect_unlock_signals 取前 10 条，实际 {len(sigs)}"
    assert "600000" in got, f"翻页捞回来的票没进信号: {sorted(got)}"
    # 修复前：单页只有 001257（0.44%），Top1 是它；修复后 Top1 应是 600000（60%）
    assert sigs[0]["codes"][0] == "600000", sigs[0]["title"]

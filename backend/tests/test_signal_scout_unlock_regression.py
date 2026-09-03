"""QA 独立回归验证：解禁聚合 / 推送门槛 / 股票-基金代码串号（任务 #4）

为什么单独写一个文件、不复用 /tmp/verify_unlock_fix.py：
  那份脚本是开发者自证的 happy-path 脚本（断言自己构造的 5 条数据），
  本文件是独立回归 —— 重点是**边界**和**反例**：
    * 聚合顺序（先截断再聚合 会怎么错）
    * 脏值（None/""/"abc"/"1,234"/负数/超大数/None 日期）
    * 命名空间的**反向**用例（只持股票 / 只持基金 / 两者都持）
    * 名称服务不可用时的降级
    * 缓存是否串用户

全部用例离线运行：不发起任何网络请求，Tushare 入口 `_call_tushare`
与持仓加载器全部打桩。
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
def ss(monkeypatch):
    """导入并"洗白" signal_scout 的进程级缓存，保证用例之间互不污染。

    ⚠️ 必须 stub 掉 _save_matched：match() 内部会调它把结果写到
    `DATA_DIR/<user_id>/signals/YYYY-MM-DD.json`。本文件的用例用的都是
    u_fund_only / user_A 这类假 user_id，一旦真的落盘就会在 data/ 下生成
    一堆假用户目录 —— 2026-09-04 实测生成了 12 个（u1/u_both/u_fund_only/
    user_A/user_B/...）。这些目录虽然被 .gitignore 挡住、不进 git，但：
      1. 与真实用户目录混在一起，肉眼分辨不出来；
      2. 假 user_id 哪天和真用户 id 撞了，会覆盖真实信号历史，且极难追查。
    更隐蔽的是 DATA_DIR 并不可靠：tests/test_regression_signal_and_cache.py
    里的 test_config_data_dir_defaults_to_project_root 会
    `sys.modules.pop("config")` 后 reload，把 sys.modules["config"] 换成
    DATA_DIR=项目根/data 的新模块；若本文件在它之后才首次 import
    signal_scout，`from config import DATA_DIR` 拿到的就是真实的 data/。
    所以光靠 conftest 的 tmp 隔离不够，必须在 fixture 层直接禁写。
    """
    import services.signal_scout as signal_scout

    signal_scout._signal_cache.clear()
    signal_scout._name_cache.clear()
    signal_scout._name_map_attempt_ts = 0.0
    signal_scout._enrich_cache.clear()

    writes = []
    monkeypatch.setattr(
        signal_scout, "_save_matched",
        lambda user_id, signals: writes.append(user_id),
        raising=True,
    )
    signal_scout._qa_writes = writes

    yield signal_scout

    signal_scout._signal_cache.clear()
    signal_scout._name_cache.clear()
    signal_scout._name_map_attempt_ts = 0.0
    signal_scout._enrich_cache.clear()
    if hasattr(signal_scout, "_qa_writes"):
        del signal_scout._qa_writes


def test_match_never_writes_fake_user_dirs_to_disk(ss, monkeypatch, tmp_path):
    """守卫用例：本文件的假 user_id 绝不能落到磁盘上。

    这条不是测业务逻辑，是测**测试自身**的卫生 —— 上面 fixture 里的
    _save_matched stub 一旦被误删，这条会立刻红，而不是等到有人发现
    data/ 下多出一堆假用户目录。
    """
    _set_holdings(monkeypatch, stocks=[("301563", "云汉芯城")], funds=[])
    monkeypatch.setattr(ss, "collect", lambda: [_unlock_signal("301563", "云汉芯城")])

    ss.match("u_should_not_persist")

    # 1) 走的是 stub，没有真的写文件
    assert ss._qa_writes == ["u_should_not_persist"]
    # 2) 保险起见：连 DATA_DIR 下也不该出现这个目录
    from config import DATA_DIR
    assert not (Path(DATA_DIR) / "u_should_not_persist").exists()


@pytest.fixture
def td():
    import services.tushare_data as tushare_data
    tushare_data._ts_cache.clear()
    yield tushare_data


def _fake_tushare(rows_by_api):
    """返回一个 `_call_tushare` 打桩函数，按 api_name 分发。"""
    def _call(api_name, params, fields=""):
        return list(rows_by_api.get(api_name, []))
    return _call


def _row(ts_code, float_date, share=None, ratio=None, holder="某股东"):
    return {
        "ts_code": ts_code,
        "float_date": float_date,
        "float_share": share,
        "float_ratio": ratio,
        "holder_name": holder,
        "share_type": "定向增发",
    }


def _set_holdings(monkeypatch, stocks=(), funds=()):
    """打桩持仓加载器。stocks/funds 均为 [(code, name), ...]"""
    import services.stock_monitor as stock_monitor
    import services.fund_monitor as fund_monitor

    stock_rows = [{"code": c, "name": n} for c, n in stocks]
    fund_rows = [{"code": c, "name": n} for c, n in funds]
    monkeypatch.setattr(stock_monitor, "load_stock_holdings", lambda uid: stock_rows, raising=True)
    monkeypatch.setattr(fund_monitor, "load_fund_holdings", lambda uid: fund_rows, raising=True)


def _unlock_signal(code6="301563", name="云汉芯城", ratio=181.77,
                   shares=3478.0, date="2026-09-30", holders=3):
    """复刻 _collect_unlock_signals() 的产出形状（type 决定命名空间）。"""
    label = f"{name}({code6})" if name else code6
    return {
        "type": "unlock",
        "title": f"解禁预警: {label} 解禁{ratio:.2f}%",
        "content": f"解禁日 {date}，合计解禁 {shares:,.0f} 万股，涉及 {holders} 个股东",
        "codes": [code6],
        "source": "Tushare",
        "time": date.replace("-", ""),
        "level": "danger",
        "tags": ["解禁"],
    }


# ============================================================
# A1. 聚合正确性
# ============================================================

def test_aggregation_sums_shares_and_ratio_for_same_code_same_day(td):
    """同股同日多行：float_share / float_ratio 求和，holder_count 计数。"""
    rows = [
        _row("002163.SZ", "20261001", 100.0, 5.0, "股东A"),
        _row("002163.SZ", "20261001", 200.0, 8.0, "股东B"),
        _row("002163.SZ", "20261001", 300.0, 7.0, "股东C"),
    ]
    monkeypatch_free = rows  # noqa
    import services.tushare_data as t
    t._call_tushare = _fake_tushare({"share_float": rows})

    merged = t.get_upcoming_unlocks()

    assert len(merged) == 1
    m = merged[0]
    assert abs(m["float_share"] - 600.0) < 1e-9, f"float_share={m['float_share']}"
    assert abs(m["float_ratio"] - 20.0) < 1e-9, f"float_ratio={m['float_ratio']}"
    assert m["holder_count"] == 3
    assert m["holder_names"] == ["股东A", "股东B", "股东C"]


def test_aggregation_does_not_merge_different_dates(td, monkeypatch):
    """同股不同日必须拆成两条，不能把跨日期的解禁加起来。"""
    rows = [
        _row("600519.SH", "20261001", 10.0, 1.0, "A"),
        _row("600519.SH", "20261015", 20.0, 2.0, "A"),
    ]
    monkeypatch.setattr(td, "_call_tushare", _fake_tushare({"share_float": rows}))

    merged = td.get_upcoming_unlocks()
    by_date = {m["float_date"]: m for m in merged}

    assert len(merged) == 2, f"同股不同日应拆成 2 条，实际 {len(merged)}"
    assert sorted(by_date) == ["20261001", "20261015"]
    # 按日期分别取值（返回顺序是按 ratio 降序，不能直接按索引断言）
    assert abs(by_date["20261001"]["float_ratio"] - 1.0) < 1e-9
    assert abs(by_date["20261015"]["float_ratio"] - 2.0) < 1e-9
    assert by_date["20261001"]["holder_count"] == 1


def test_aggregation_tolerates_dirty_values(td, monkeypatch):
    """脏值（None/空串/非数字/带千分位/负数/超大数/None 日期）不抛异常。

    这是 Tushare 的现实：同一字段在不同行里类型都不一样。聚合层必须
    吞掉脏值而不是崩掉整条 collect 链路（collect 的 try/except 会静默
    吞掉异常，导致"解禁信号凭空消失"且无人报警）。
    """
    rows = [
        _row("000001.SZ", "20261001", None, None),          # 全空
        _row("000001.SZ", "20261001", "", ""),              # 空串
        _row("000001.SZ", "20261001", "abc", "abc"),        # 非数字
        _row("000001.SZ", "20261001", "1,234", "1,234"),    # 带千分位
        _row("000001.SZ", "20261001", -50.0, -2.0),         # 负数
        _row("000001.SZ", "20261001", 1e12, 1e9),           # 超大数
        _row("000002.SZ", None, 5.0, 5.0),                  # 日期为 None
    ]
    monkeypatch.setattr(td, "_call_tushare", _fake_tushare({"share_float": rows}))

    merged = td.get_upcoming_unlocks()  # 不抛异常即通过

    by_code = {m["ts_code"]: m for m in merged}
    # 有效数值只有 -50 / 1e12 与 -2 / 1e9；千分位串按 0 累加（见 BUG-04）
    assert abs(by_code["000001.SZ"]["float_share"] - (1e12 - 50.0)) < 1e-3
    assert abs(by_code["000001.SZ"]["float_ratio"] - (1e9 - 2.0)) < 1e-3
    assert by_code["000002.SZ"]["float_date"] == ""      # None 日期 → ""，不是崩溃
    assert by_code["000002.SZ"]["holder_count"] == 1


def test_aggregation_happens_before_truncation(td, monkeypatch):
    """顺序反例：先截断 Top N 再聚合 → 本用例必须失败。

    构造：30 行各 1.0% 全属于 000001.SZ（合计 30.0%，全市场第一），
    另有 10 只票各 1 行 5.0%。limit=10。
      * 正确（聚合→排序→截断）：11 组，000001 以 30.0 排第 1 → 在结果里。
      * 错误（先按行截断前 10）：前 10 行全是 5.0% 的票，000001 一行都不剩
        → 结果里根本没有 000001。
    """
    rows = [_row("000001.SZ", "20261001", 100.0, 1.0, f"股东{i}") for i in range(30)]
    rows += [_row(f"3000{i:02d}.SZ", "20261001", 10.0, 5.0, "X") for i in range(10)]

    monkeypatch.setattr(td, "_call_tushare", _fake_tushare({"share_float": rows}))

    merged = td.get_upcoming_unlocks(limit=10)

    top = merged[0]
    assert top["ts_code"] == "000001.SZ", f"Top1 = {top['ts_code']}（聚合顺序错了）"
    assert abs(top["float_ratio"] - 30.0) < 1e-9, f"ratio={top['float_ratio']}"
    assert top["holder_count"] == 30
    assert len(merged) == 10


def test_limit_parameter_is_honoured(td, monkeypatch):
    rows = [_row(f"60000{i}.SH", "20261001", 10.0, float(i + 1)) for i in range(9)]
    monkeypatch.setattr(td, "_call_tushare", _fake_tushare({"share_float": rows}))

    assert len(td.get_upcoming_unlocks(limit=3)) == 3
    # 排序为降序
    merged = td.get_upcoming_unlocks(limit=9)
    ratios = [m["float_ratio"] for m in merged]
    assert ratios == sorted(ratios, reverse=True)


def test_empty_result_and_rows_with_blank_code(td, monkeypatch):
    monkeypatch.setattr(td, "_call_tushare", _fake_tushare({"share_float": []}))
    assert td.get_upcoming_unlocks() == []

    rows = [_row("   ", "20261001", 1.0, 1.0), _row("", "", 2.0, 2.0)]
    monkeypatch.setattr(td, "_call_tushare", _fake_tushare({"share_float": rows}))
    merged = td.get_upcoming_unlocks()
    # 空代码不合并（一个带空白一个不带），也不崩
    assert len(merged) == 2


# ============================================================
# A2. _collect_unlock_signals：标题/正文/名称降级
# ============================================================

def test_unlock_signal_carries_name_date_shares_holdercount(ss, td, monkeypatch):
    """⚠️ 单位：share_float.float_share 的原始单位是【股】，展示口径是【万股】。

    用例数据取 301563.SZ 2026-09-30 的**服务器实测值**（32 行，此处简化成 3 行）：
    合计 34,782,667.4 股 = 3,478.27 万股；合计 float_ratio = 41.09%（占总股本）。
    历史上这里把股数当万股直接拼进文案，正文变成"合计解禁 34,782,667.40 万股"。
    """
    rows = [
        _row("301563.SZ", "20260930", 10000000.0, 16.00, "A"),
        _row("301563.SZ", "20260930", 24782667.4, 25.09, "B"),
        _row("301563.SZ", "20260930", 0.0, 0.0, "C"),
    ]
    monkeypatch.setattr(td, "_call_tushare", _fake_tushare({"share_float": rows}))
    monkeypatch.setattr(td, "_get_stock_names", lambda: {"301563.SZ": "云汉芯城"})

    sigs = ss._collect_unlock_signals()

    assert len(sigs) == 1, f"3 行应聚合成 1 条信号，实际 {len(sigs)}"
    s = sigs[0]
    # float_ratio 口径 = 占【总股本】（Tushare 原值），不是占流通盘
    assert s["title"] == "解禁预警: 云汉芯城(301563) 解禁41.09%", s["title"]
    assert "解禁日 2026-09-30" in s["content"]
    assert "合计解禁 3,478.27 万股" in s["content"], s["content"]
    assert "涉及 3 个股东" in s["content"]
    assert s["codes"] == ["301563"]
    assert s["level"] == "danger"


def test_unlock_signal_degrades_to_code_when_name_service_raises(ss, td, monkeypatch):
    """名称服务抛异常：信号一条都不能丢，标题降级为纯代码且不能出现空括号。"""
    rows = [
        _row("301563.SZ", "20260930", 100.0, 60.0, "A"),
        _row("920222.BJ", "20261010", 100.0, 7.4623, "B"),
    ]
    monkeypatch.setattr(td, "_call_tushare", _fake_tushare({"share_float": rows}))

    def boom():
        raise RuntimeError("Tushare 500")
    monkeypatch.setattr(td, "_get_stock_names", boom)

    sigs = ss._collect_unlock_signals()

    assert len(sigs) == 2, f"名称服务挂了也不能丢信号，实际 {len(sigs)}"
    titles = [s["title"] for s in sigs]
    assert any("301563" in t for t in titles)
    assert not any("()" in t for t in titles), f"出现空括号: {titles}"
    assert all(s["content"] for s in sigs)


def test_unlock_signal_degrades_when_mapping_empty_or_partial(ss, td, monkeypatch):
    rows = [
        _row("301563.SZ", "20260930", 100.0, 60.0, "A"),
        _row("920222.BJ", "20261010", 100.0, 7.4623, "B"),
    ]
    monkeypatch.setattr(td, "_call_tushare", _fake_tushare({"share_float": rows}))
    monkeypatch.setattr(td, "_get_stock_names", lambda: {})  # 空映射

    sigs = ss._collect_unlock_signals()
    assert len(sigs) == 2
    assert all("()" not in s["title"] for s in sigs)

    # 部分映射：只有 301563 有名字
    ss._name_cache.clear()
    ss._name_map_attempt_ts = 0.0
    monkeypatch.setattr(td, "_get_stock_names", lambda: {"301563.SZ": "云汉芯城"})
    sigs2 = ss._collect_unlock_signals()
    by_code = {s["codes"][0]: s["title"] for s in sigs2}
    assert "云汉芯城(301563)" in by_code["301563"]
    assert by_code["920222"] == "解禁预警: 920222 解禁7.46%"


def test_unlock_date_formats(ss):
    assert ss._fmt_signal_date("20260930") == "2026-09-30"
    assert ss._fmt_signal_date("2026-09-30") == "2026-09-30"
    assert ss._fmt_signal_date(None) == "待定"
    assert ss._fmt_signal_date("") == "待定"
    assert ss._fmt_signal_date("2026/09/30") == "待定"
    assert ss._fmt_signal_date(20260930) == "2026-09-30"


# ============================================================
# A3. 推送门槛 _should_push
# ============================================================

@pytest.mark.parametrize("sig_type", sorted([
    "unlock", "holder_change", "fund_flow",
    "pledge_risk", "st_warning", "dividend", "announcement",
]))
def test_stock_event_danger_blocked_without_holding(ss, sig_type):
    """个股事件类 danger + 无持仓 → 不得推送（原逻辑会被 `or level==danger` 放行）。

    fund_flow 也在列：北向十大活跃成交股的 codes 是【股票】代码，
    用户没持有那只票时"北向活跃个股: XX"毫无行动价值，与解禁同理。
    """
    sig = {"type": sig_type, "level": "danger", "relevance": 30, "related_holding": ""}
    assert ss._should_push(sig) is False


@pytest.mark.parametrize("sig_type", ["news_policy", "news_market", "technical", "未知新类型"])
def test_macro_danger_still_pushed_without_holding(ss, sig_type):
    """宏观/市场类 danger + 无持仓 → 仍要推送，不能被误杀。

    注：fund_flow 已从此列表移出 —— 它的 codes 是北向**个股**的股票代码，
    属个股事件类（见 test_stock_event_danger_blocked_without_holding）。
    """
    sig = {"type": sig_type, "level": "danger", "relevance": 30, "related_holding": ""}
    assert ss._should_push(sig) is True


def test_stock_event_pushed_when_holding_hit(ss):
    sig = {"type": "unlock", "level": "danger", "relevance": 100, "related_holding": "云汉芯城"}
    assert ss._should_push(sig) is True


def test_should_push_relevance_edge_cases(ss):
    """relevance 字段的类型不稳定（来自 JSON 反序列化）时不得抛异常。"""
    assert ss._should_push({"type": "unlock", "level": "danger"}) is False          # 缺字段
    assert ss._should_push({"type": "unlock", "level": "danger", "relevance": None}) is False
    assert ss._should_push({"type": "unlock", "level": "danger", "relevance": "100"}) is True
    assert ss._should_push({"type": "unlock", "level": "danger", "relevance": 49.9}) is False
    assert ss._should_push({"type": "unlock", "level": "danger", "relevance": 50}) is True


def test_deliver_blocks_unrelated_unlock_end_to_end(ss, monkeypatch):
    """端到端：用户无股票持仓 → 两条 danger 解禁全部不推送。"""
    _set_holdings(monkeypatch, stocks=[], funds=[("110022", "易方达消费行业")])
    monkeypatch.setattr(ss, "collect", lambda: [
        _unlock_signal("301563", "云汉芯城", 181.77),
        _unlock_signal("920222", "某公司", 7.4623),
    ])

    result = ss.deliver("u_noholding")

    assert result == {"pushed": 0, "reason": "无重要信号"}, result
    assert "text" not in result


def test_deliver_pushes_and_shows_detail_when_holding_hit(ss, monkeypatch):
    """端到端：命中持仓 → 推送，文本含名称/解禁日/数量/持仓标注。"""
    _set_holdings(monkeypatch, stocks=[("301563", "云汉芯城")])
    monkeypatch.setattr(ss, "collect", lambda: [_unlock_signal("301563", "云汉芯城", 181.77)])

    result = ss.deliver("u_holding")
    text = result["text"]

    assert "信号侦察 (1条)" in text
    assert "云汉芯城(301563)" in text
    assert "解禁日 2026-09-30" in text
    assert "3,478 万股" in text
    assert "→ 云汉芯城" in text
    # 企微纯文本（铁律 #20）
    for md in ("**", "# ", "```", "["):
        assert md not in text, f"推送文本出现 Markdown 符号 {md!r}"


# ============================================================
# A4. 股票 / 基金代码串号
# ============================================================

def test_fund_only_holding_does_not_match_stock_unlock(ss, monkeypatch):
    """002163 只持基金 → 股票解禁不得命中，不得推送。"""
    _set_holdings(monkeypatch, stocks=[], funds=[("002163", "东方惠新灵活配置混合C")])
    monkeypatch.setattr(ss, "collect", lambda: [_unlock_signal("002163", "海南发展", 20.0)])

    matched = ss.match("u_fund_only")
    result = ss.deliver("u_fund_only", matched)

    assert all(m.get("relevance") != 100 for m in matched), matched
    assert result.get("pushed") == 0
    assert result.get("text") is None


def test_stock_only_holding_matches_stock_name_not_fund_name(ss, monkeypatch):
    """002163 只持股票 → 必须推送，related_holding 是股票名，正文不得出现基金名。"""
    _set_holdings(monkeypatch, stocks=[("002163", "海南发展")], funds=[])
    monkeypatch.setattr(ss, "collect", lambda: [_unlock_signal("002163", "海南发展", 20.0)])

    matched = ss.match("u_stock_only")
    result = ss.deliver("u_stock_only", matched)

    assert matched[0]["relevance"] == 100
    assert matched[0]["related_holding"] == "海南发展", matched[0]["related_holding"]
    assert "海南发展" in result["text"]
    assert "东方惠新" not in result["text"]


def test_both_holdings_stock_name_wins_for_stock_event(ss, monkeypatch):
    """同时持有股票 002163 和基金 002163 → 个股事件必须解析到股票名。"""
    _set_holdings(
        monkeypatch,
        stocks=[("002163", "海南发展")],
        funds=[("002163", "东方惠新灵活配置混合C")],
    )
    monkeypatch.setattr(ss, "collect", lambda: [_unlock_signal("002163", "海南发展", 20.0)])

    matched = ss.match("u_both")

    assert matched[0]["relevance"] == 100
    assert matched[0]["related_holding"] == "海南发展"


def test_macro_type_still_matches_fund_code(ss, monkeypatch):
    """回归：非个股事件类仍沿用「股票+基金」合并视图，行为没有回退。"""
    _set_holdings(monkeypatch, stocks=[], funds=[("002163", "东方惠新灵活配置混合C")])
    monkeypatch.setattr(ss, "collect", lambda: [{
        "type": "news_market",
        "title": "002163 相关市场消息",
        "content": "",
        "codes": ["002163"],
        "level": "info",
        "tags": [],
    }])

    matched = ss.match("u_macro_fund")
    assert matched[0]["relevance"] == 100
    assert matched[0]["related_holding"] == "东方惠新灵活配置混合C"


# ============================================================
# C. 额外找的刺
# ============================================================

def test_deliver_has_no_dangling_arrow_when_related_holding_empty(ss, monkeypatch):
    """标签命中的信号 relevance=50 但 related_holding='' → 文本不得出现 `→ ` 后空白。

    _should_push 允许 relevance>=50 推送，而标签匹配路径从不写
    related_holding，因此这条路径必然带空持仓名进 deliver。
    """
    _set_holdings(monkeypatch, stocks=[], funds=[])
    monkeypatch.setattr(ss, "collect", lambda: [{
        "type": "news_policy",
        "title": "央行宣布降准 0.5 个百分点",
        "content": "全面降准，释放长期资金约 1 万亿",
        "codes": [],
        "level": "warning",
        "tags": ["降准"],
    }])

    matched = ss.match("u_tag")
    assert matched[0]["relevance"] == 50
    assert matched[0]["related_holding"] == ""

    text = ss.deliver("u_tag", matched)["text"]

    assert " → " not in text, f"出现悬空箭头: {text!r}"
    assert not text.rstrip().endswith("→")
    assert "央行宣布降准" in text
    assert "释放长期资金" in text, "content 不应被丢弃"


def test_deliver_handles_signal_without_related_holding_key(ss, monkeypatch):
    """外部直接传 signals 给 deliver()（如 API 读历史 JSON）缺 key 时不得 KeyError。"""
    _set_holdings(monkeypatch)
    text = ss.deliver("u", [{
        "type": "news_policy", "title": "T", "content": "C",
        "codes": [], "level": "danger", "tags": [],
    }])["text"]
    assert "T" in text


def test_enrich_cache_is_keyed_by_user(ss, monkeypatch):
    """_enrich_cache 必须按 user_id 分桶，否则 A 的匹配结果会串给 B。"""
    from services.decision_context import DecisionContext

    ss._enrich_cache.clear()

    # 注意：match() 对无持仓用户也会返回 relevance=30 的"全市场信号"条目，
    # 因此不能用 matched_count 判断串号 —— 必须看 high_relevance /
    # related_holding 这两个带用户维度的字段。
    _set_holdings(monkeypatch, stocks=[("301563", "云汉芯城")], funds=[])
    monkeypatch.setattr(ss, "collect", lambda: [_unlock_signal("301563", "云汉芯城")])

    ctx_a = DecisionContext(user_id="user_A", question="解禁")
    ss.enrich(ctx_a)
    res_a = ctx_a.modules_results["signal_scout"]

    _set_holdings(monkeypatch, stocks=[], funds=[])  # B 空仓
    ctx_b = DecisionContext(user_id="user_B", question="解禁")
    ss.enrich(ctx_b)
    res_b = ctx_b.modules_results["signal_scout"]

    assert res_a["high_relevance"] == 1, f"A 应高相关命中 1 条，实际 {res_a['high_relevance']}"
    assert res_a["top_signals"][0]["related_holding"] == "云汉芯城"

    assert res_b["high_relevance"] == 0, f"B 空仓却拿到 A 的高相关结果: {res_b}"
    assert all(s.get("related_holding") == "" for s in res_b["top_signals"]), res_b["top_signals"]

    # 二次 enrich A（命中 _enrich_cache）：必须仍是 A 自己的结果
    ctx_a2 = DecisionContext(user_id="user_A", question="解禁")
    ss.enrich(ctx_a2)
    res_a2 = ctx_a2.modules_results["signal_scout"]
    assert res_a2["high_relevance"] == 1
    assert res_a2["top_signals"][0]["related_holding"] == "云汉芯城"

    ss._enrich_cache.clear()


def test_collect_output_is_user_agnostic(ss, td, monkeypatch):
    """_signal_cache 是公共缓存（key='all_signals'），产出里绝不能带用户维度字段。

    否则 A 用户先跑一次，B 用户就会拿到 A 的 relevance / related_holding。
    """
    rows = [_row("301563.SZ", "20260930", 100.0, 60.0, "A")]
    monkeypatch.setattr(td, "_call_tushare", _fake_tushare({"share_float": rows}))
    monkeypatch.setattr(td, "_get_stock_names", lambda: {"301563.SZ": "云汉芯城"})
    for name in ("_collect_news_signals", "_collect_holder_changes",
                 "_collect_fund_flow_signals", "_collect_technical_signals"):
        monkeypatch.setattr(ss, name, lambda: [])

    _set_holdings(monkeypatch, stocks=[("301563", "云汉芯城")], funds=[])
    ss.match("user_A")

    public = ss._signal_cache.get("all_signals")
    assert public is not None
    for s in public:
        assert "relevance" not in s, f"公共缓存被写入用户维度字段: {s}"
        assert "related_holding" not in s


def test_match_does_not_mutate_cached_signal_objects(ss, monkeypatch):
    """match() 用 {**sig} 复制，不能就地改公共信号对象。"""
    sig = _unlock_signal("301563", "云汉芯城")
    monkeypatch.setattr(ss, "collect", lambda: [sig])
    _set_holdings(monkeypatch, stocks=[("301563", "云汉芯城")], funds=[])

    ss.match("u1")
    assert "relevance" not in sig and "related_holding" not in sig


def _fund_flow_signal(code6="002163", name="海南发展"):
    """复刻 _collect_fund_flow_signals() 的产出形状。"""
    return {
        "type": "fund_flow",
        "title": f"北向活跃个股: {name}",
        "content": "来源: 沪深股通十大成交股（数据时点 2026-09-30）",
        "codes": [code6],
        "source": "北向",
        "time": "09:00",
        "level": "info",
        "tags": ["北向", "资金"],
    }


def test_fund_flow_stock_code_must_not_match_fund_holding(ss, monkeypatch):
    """BUG-01 反例：北向活跃个股 002163（股票）不得命中基金 002163 持仓。"""
    _set_holdings(monkeypatch, stocks=[], funds=[("002163", "东方惠新灵活配置混合C")])
    monkeypatch.setattr(ss, "collect", lambda: [_fund_flow_signal()])

    matched = ss.match("u_fund_only")
    result = ss.deliver("u_fund_only", matched)

    # info 级 + 无持仓命中 → relevance=0，根本不该进 matched
    assert matched == [], f"北向个股信号不该命中基金持仓: {matched}"
    assert result == {"pushed": 0, "reason": "无重要信号"}, result


def test_fund_flow_matches_stock_holding_and_shows_stock_name(ss, monkeypatch):
    """BUG-01 正例：持有股票 002163 → 北向信号命中，related_holding 是股票名。

    注意 level=info 的信号只有 relevance=100 才会进 matched（info 级不享受
    "全市场信号 relevance=30" 的兜底），因此命中持仓是它唯一的推送路径。
    """
    _set_holdings(
        monkeypatch,
        stocks=[("002163", "海南发展")],
        funds=[("002163", "东方惠新灵活配置混合C")],  # 同时持有同名基金，制造冲突
    )
    monkeypatch.setattr(ss, "collect", lambda: [_fund_flow_signal()])

    matched = ss.match("u_stock_holding")

    assert len(matched) == 1, f"持有该股却没匹配上: {matched}"
    assert matched[0]["relevance"] == 100
    assert matched[0]["related_holding"] == "海南发展", matched[0]["related_holding"]
    assert "东方惠新" not in str(matched[0])


def test_fund_flow_does_not_break_macro_danger_path(ss, monkeypatch):
    """回归：fund_flow 归入个股类后，宏观类 danger 放行路径不受影响。"""
    _set_holdings(monkeypatch, stocks=[], funds=[])
    monkeypatch.setattr(ss, "collect", lambda: [{
        "type": "news_market",
        "title": "美股暴跌，三大指数重挫",
        "content": "道指跌 3%",
        "codes": [],
        "level": "danger",
        "tags": [],
    }])

    result = ss.deliver("u_macro_danger")

    assert result.get("text") is not None, "宏观 danger 信号被误杀了"
    assert "美股暴跌" in result["text"]


@pytest.mark.parametrize("dirty", ["inf", "-inf", "nan", "1e400", "Infinity"])
def test_non_finite_float_share_does_not_drop_later_signals(ss, td, monkeypatch, dirty):
    """BUG-03：脏值不能污染同批次的其他信号。

    断言的是"信号没有被静默丢弃"，而不是"没有抛异常" —— 因为本函数的
    调用点被 try/except 包着只 print，抛异常的表现是**静默少信号**，
    只断言不抛异常会漏掉这个真实危害。
    """
    rows = [
        _row("000001.SZ", "20261001", 100.0, 9.0, "A"),
        _row("000002.SZ", "20261001", dirty, 8.0, "B"),   # 脏值
        _row("000003.SZ", "20261001", 100.0, 7.0, "C"),
    ]
    monkeypatch.setattr(td, "_call_tushare", _fake_tushare({"share_float": rows}))
    monkeypatch.setattr(td, "_get_stock_names", lambda: {})

    sigs = ss._collect_unlock_signals()
    got = [s["codes"][0] for s in sigs]

    assert got == ["000001", "000002", "000003"], f"脏值打断了循环，只产出 {got}"
    # 脏值行自身也必须产出（降级显示，不能消失）
    dirty_sig = next(s for s in sigs if s["codes"][0] == "000002")
    assert dirty_sig["content"], "脏值行的 content 不应为空"


def test_non_finite_values_do_not_poison_the_sum(td, monkeypatch):
    """BUG-03 配套：inf 进入求和会把整个合计值带成 inf，必须在聚合层拦掉。"""
    rows = [
        _row("000001.SZ", "20261001", 100.0, 5.0, "A"),
        _row("000001.SZ", "20261001", "inf", 3.0, "B"),
        _row("000001.SZ", "20261001", 200.0, 2.0, "C"),
    ]
    monkeypatch.setattr(td, "_call_tushare", _fake_tushare({"share_float": rows}))

    merged = td.get_upcoming_unlocks()
    m = merged[0]

    assert abs(m["float_share"] - 300.0) < 1e-9, f"inf 污染了合计值: {m['float_share']}"
    assert abs(m["float_ratio"] - 10.0) < 1e-9, f"inf 污染了合计比例: {m['float_ratio']}"


def test_fmt_number_never_raises(ss):
    """_fmt_number 是 BUG-03 的病根：任何输入都不得往外抛。"""
    for v in (float("inf"), float("-inf"), float("nan"), 1e400, 0, 1234.5, -50.0):
        out = ss._fmt_number(v)  # 不抛异常即通过
        assert isinstance(out, str) and out != ""
    assert ss._fmt_number(3478.0) == "3,478"
    assert ss._fmt_number(180.25) == "180.25"


def test_safe_float_treats_non_finite_as_default(ss):
    """_safe_float 必须把 inf/nan 一并当脏值，不能让它们流到下游算术里。"""
    assert ss._safe_float("inf", 0.0) == 0.0
    assert ss._safe_float(float("nan"), 7.0) == 7.0
    assert ss._safe_float("1e400", 0.0) == 0.0
    assert ss._safe_float("3.5") == 3.5          # 正常值不受影响
    assert ss._safe_float(None, 1.0) == 1.0
    assert ss._safe_float("abc", 2.0) == 2.0


def test_holder_count_matches_unique_holder_names(td, monkeypatch):
    """BUG-02：holder_count 必须与 holder_names 同口径（去重后的股东数）。"""
    rows = [
        _row("002163.SZ", "20261001", 100.0, 5.0, "同一股东"),
        _row("002163.SZ", "20261001", 200.0, 8.0, "同一股东"),
    ]
    monkeypatch.setattr(td, "_call_tushare", _fake_tushare({"share_float": rows}))

    merged = td.get_upcoming_unlocks()
    assert merged[0]["holder_count"] == len(merged[0]["holder_names"]) == 1
    assert merged[0]["holder_names"] == ["同一股东"]
    # 两笔的股数/比例仍然要加总（去重只影响股东计数，不影响数量）
    assert abs(merged[0]["float_share"] - 300.0) < 1e-9
    assert abs(merged[0]["float_ratio"] - 13.0) < 1e-9


def test_holder_count_wording_is_not_self_contradictory(ss, td, monkeypatch):
    """点 3 复核：股数按【全部行】加总 + 股东数按【去重名】计数，
    这两个口径组合出来的文案不能自相矛盾。

    场景：同一股东持有多笔**不同类型**的限售股（占 2 行）。
    正确文案必须是"合计解禁 300 万股，涉及 1 个股东" ——
    一个股东有两笔解禁是正常且常见的，不能报成 2 个股东。

    ⚠️ 构造数据用【股】为单位（源数据口径）：1,000,000 + 2,000,000 = 3,000,000 股
    = 300 万股。
    """
    rows = [
        _row("002163.SZ", "20261001", 1000000.0, 5.0, "同一股东"),
        _row("002163.SZ", "20261001", 2000000.0, 8.0, "同一股东"),
    ]
    monkeypatch.setattr(td, "_call_tushare", _fake_tushare({"share_float": rows}))
    monkeypatch.setattr(td, "_get_stock_names", lambda: {})

    content = ss._collect_unlock_signals()[0]["content"]

    assert "合计解禁 300 万股" in content, content
    assert "涉及 1 个股东" in content, content
    assert "涉及 2 个股东" not in content, f"同一股东被数成 2 个: {content}"


def test_holder_count_distinguishes_different_holders(ss, td, monkeypatch):
    """反向验证：不同股东仍然按名字数正确计数，没有被"一律算 1 个"。"""
    # 第 3 行与第 1 行 holder_name 相同（要测的就是"重复名只数一次"），但
    # share_type 必须不同 —— 现在上游会按"解禁事件"去重，六字段完全相同的
    # 两行会被判为同一笔解禁的两次公告而合并掉，那测的就不是本用例的意图了。
    row_a2 = _row("002163.SZ", "20261001", 1000000.0, 5.0, "股东A")  # 重复名
    row_a2["share_type"] = "股权激励限售流通"
    rows = [
        _row("002163.SZ", "20261001", 1000000.0, 5.0, "股东A"),
        _row("002163.SZ", "20261001", 1000000.0, 5.0, "股东B"),
        row_a2,
    ]
    monkeypatch.setattr(td, "_call_tushare", _fake_tushare({"share_float": rows}))
    monkeypatch.setattr(td, "_get_stock_names", lambda: {})

    sigs = ss._collect_unlock_signals()
    content = sigs[0]["content"]

    assert "涉及 2 个股东" in content, content
    assert "合计解禁 300 万股" in content, content  # 3 行都要加总


def test_fund_flow_classification_does_not_change_push_gating(ss, monkeypatch):
    """点 1 佐证：fund_flow 归入个股事件类，对【推送门槛】这条路径零影响。

    _collect_fund_flow_signals 产出的 level 恒为 "info"，而 _should_push 的
    第二条分支要求 level == "danger"，所以 fund_flow 无论在不在
    _HOLDING_REQUIRED_TYPES 里都走不到该分支 —— 归类修正只影响【代码命名
    空间】（match 用股票持仓还是股票+基金），不影响推送放行。
    这里用"人为把 level 抬成 danger"来把该分支逼出来，验证归类确实生效。
    """
    _set_holdings(monkeypatch, stocks=[], funds=[])

    info_sig = _fund_flow_signal()
    danger_sig = {**_fund_flow_signal(), "level": "danger"}

    # 原位行为：info 级无持仓 → 不推（relevance=0，两条分支都不满足）
    assert ss._should_push(info_sig) is False
    # 归类生效：danger 级但属个股事件类且无持仓 → 拦截（旧行为会放行）
    assert ss._should_push(danger_sig) is False
    # 命中持仓后照常放行，不因归类被误杀
    assert ss._should_push({**danger_sig, "relevance": 100}) is True

    # 宏观类 danger 不受牵连，仍放行
    assert ss._should_push({"type": "news_market", "level": "danger", "relevance": 0}) is True


def test_stock_event_types_default_to_fail_open_for_unknown_type(ss):
    """已知设计风险（非本次引入）：未登记的新类型走宏观分支 = danger 放行。

    _HOLDING_REQUIRED_TYPES 是黑名单的**反面** —— 未登记的类型被视为宏观类。
    新增个股事件类采集器却忘了登记时，会静默退化成"全市场 danger 直推"，
    正是本次要根除的骚扰。只能靠 code review 兜，这里把它固化成文档。
    """
    assert "some_new_stock_event" not in ss._HOLDING_REQUIRED_TYPES
    assert ss._should_push({"type": "some_new_stock_event", "level": "danger"}) is True


def test_aggregate_sum_overflow_is_degraded_not_crashed(ss, td, monkeypatch):
    """点 2 漏点（已知残留，非阻塞）：_finite_float 是**逐值**守卫，
    两个都有限的大数相加仍可能溢出成 inf。

    后果不是崩溃也不是丢信号，而是降级显示成 "0 万股" —— 数字静默错。
    需要 ~1e308 量级的输入才会触发，Tushare 现实数据不可达，故不阻塞；
    在此固化行为，若将来有人放宽数据源量程，这条会提醒补一层"求和后校验"。
    """
    rows = [
        _row("000001.SZ", "20261001", 1e308, 5.0, "A"),
        _row("000001.SZ", "20261001", 1e308, 3.0, "B"),
    ]
    monkeypatch.setattr(td, "_call_tushare", _fake_tushare({"share_float": rows}))
    monkeypatch.setattr(td, "_get_stock_names", lambda: {})

    merged = td.get_upcoming_unlocks()
    sigs = ss._collect_unlock_signals()

    # 求和溢出成 inf，但下游不崩、不丢信号，只是数字被降级
    assert merged[0]["float_share"] == float("inf")
    assert len(sigs) == 1, "溢出不得吞掉信号"
    assert sigs[0]["content"].endswith("涉及 2 个股东"), sigs[0]["content"]


def test_holder_field_omitted_when_no_holder_name(ss, td, monkeypatch):
    """BUG-02 配套：全部行都没有股东名 → 不显示股东数字段，
    不能出现"涉及 0 个股东"这种自相矛盾的文案。"""
    rows = [
        _row("000001.SZ", "20261001", 1000000.0, 9.0, ""),
        _row("000001.SZ", "20261001", 2000000.0, 3.0, None),
    ]
    monkeypatch.setattr(td, "_call_tushare", _fake_tushare({"share_float": rows}))
    monkeypatch.setattr(td, "_get_stock_names", lambda: {})

    sigs = ss._collect_unlock_signals()

    assert len(sigs) == 1
    content = sigs[0]["content"]
    assert "股东" not in content, f"无股东名时不该显示股东数字段: {content}"
    assert "合计解禁 300 万股" in content  # 数量仍要加总，不能因为没名字就丢


def test_float_share_is_converted_from_shares_to_wan_shares(ss, td, monkeypatch):
    """BUG-1 正例：float_share 单位是【股】，正文必须换算成【万股】。

    实测事故值：34,782,667.4 股（301563.SZ 2026-09-30）被原代码直接标成
    "34,782,667.40 万股"，放大 10000 倍；正确是 3,478.27 万股。
    """
    rows = [_row("301563.SZ", "20260930", 34782667.4, 41.09, "A")]
    monkeypatch.setattr(td, "_call_tushare", _fake_tushare({"share_float": rows}))
    monkeypatch.setattr(td, "_get_stock_names", lambda: {})

    content = ss._collect_unlock_signals()[0]["content"]

    assert "合计解禁 3,478.27 万股" in content, content
    assert "34,782,667" not in content, f"股数没有被换算: {content}"


def test_float_share_conversion_keeps_small_amounts_readable(ss, td, monkeypatch):
    """BUG-1 边界：小额解禁也走同一条换算路径，不能出现 0.00 万股这种无信息量文案。

    834,000 股 = 83.40 万股（603683.SH 2026-09-30 实测值）。
    """
    rows = [_row("603683.SH", "20260930", 834000.0, 0.2864, "A")]
    monkeypatch.setattr(td, "_call_tushare", _fake_tushare({"share_float": rows}))
    monkeypatch.setattr(td, "_get_stock_names", lambda: {})

    content = ss._collect_unlock_signals()[0]["content"]

    assert "合计解禁 83.40 万股" in content, content


def test_float_ratio_over_100_is_marked_not_silently_shown(ss, td, monkeypatch):
    """防线：解禁占【总股本】> 100% 是物理不可能的，必须标注而不是照常展示。

    历史实例：301507.SZ 曾算出 138.00%（真因是同一笔解禁的两次公告被重复
    累加；去重后 69.00%）。去重已在上游修掉，这里守的是"上游再出别的脏数据
    时，异常值仍然可见"。
    """
    rows = [_row("301507.SZ", "20260907", 492044944.0, 138.0, "A")]
    monkeypatch.setattr(td, "_call_tushare", _fake_tushare({"share_float": rows}))
    monkeypatch.setattr(td, "_get_stock_names", lambda: {})

    s = ss._collect_unlock_signals()[0]

    assert "数据存疑" in s["content"], f"异常占比没有被标注: {s['content']}"
    # 不钳制：真实值必须保留，把 138 改成 100 是凭空造数
    assert "138.00%" in s["title"], s["title"]
    assert "100.00%" not in s["title"], f"异常值被钳制了: {s['title']}"


def test_float_ratio_at_or_below_100_is_not_marked(ss, td, monkeypatch):
    """边界：正常占比不得被误标（含正好 100% 与刚好超过阈值的边界）。"""
    rows = [
        _row("000001.SZ", "20261001", 1000000.0, 100.0, "A"),
        _row("000002.SZ", "20261001", 1000000.0, 99.99, "B"),
        _row("000003.SZ", "20261001", 1000000.0, 5.0, "C"),
    ]
    monkeypatch.setattr(td, "_call_tushare", _fake_tushare({"share_float": rows}))
    monkeypatch.setattr(td, "_get_stock_names", lambda: {})

    sigs = {s["codes"][0]: s for s in ss._collect_unlock_signals()}

    assert "数据存疑" not in sigs["000001"]["content"], sigs["000001"]["content"]
    assert "数据存疑" not in sigs["000002"]["content"], sigs["000002"]["content"]
    assert "数据存疑" not in sigs["000003"]["content"], sigs["000003"]["content"]


def test_float_ratio_just_over_100_is_marked(ss, td, monkeypatch):
    """边界：刚过 100% 就要标（不能等到明显离谱才拦）。"""
    rows = [_row("000001.SZ", "20261001", 1000000.0, 100.01, "A")]
    monkeypatch.setattr(td, "_call_tushare", _fake_tushare({"share_float": rows}))
    monkeypatch.setattr(td, "_get_stock_names", lambda: {})

    content = ss._collect_unlock_signals()[0]["content"]

    assert "数据存疑" in content, content


def test_holder_change_content_uses_shares_and_wan_yuan(ss, monkeypatch):
    """BUG-2：change_vol 单位是【股】（不是万股），change_amount 单位是【元】。

    实测 stk_holdertrade：change_vol = 4016100.0（= 401.61 万股），
    change_amount 源数据恒为 None → 该字段必须隐藏，不能显示"变动金额: 0万元"。

    ⚠️ 夹具用 `in_de`（接口真实字段），不用 `change_type`（接口不返回）。
    """
    import services.tushare_data as td

    trades = [{
        "ts_code": "601199.SH",
        "ann_date": "20260902",
        "holder_name": "长城人寿保险股份有限公司",
        "holder_type": "C",
        "in_de": "DE",
        "change_vol": 4016100.0,
        # change_amount 在真实响应里**根本没有这个 key**（1333/1333 行缺失）
    }]
    monkeypatch.setattr(td, "get_holder_trades", lambda *a, **kw: trades)

    sigs = ss._collect_holder_changes()

    assert len(sigs) == 1
    content = sigs[0]["content"]
    assert "变动股数: 401.61 万股" in content, content
    assert "4,016,100" not in content, f"股数没有换算: {content}"
    assert "变动金额" not in content, f"源数据没有金额，不该显示 0: {content}"


def test_holder_change_shows_amount_only_when_source_provides_it(ss, monkeypatch):
    """BUG-2 反向验证：源数据真的给了 change_amount（单位【元】）时才显示，
    且要换算成万元 —— 不能被"隐藏 0"的逻辑连带隐藏掉真实金额。"""
    import services.tushare_data as td

    trades = [{
        "ts_code": "601199.SH",
        "ann_date": "20260902",
        "holder_name": "长城人寿保险股份有限公司",
        "in_de": "IN",                  # 接口真实方向字段（不是 change_type）
        "change_vol": 1000000.0,        # 100 万股
        "change_amount": 25600000.0,    # 2,560 万元（源单位是元）
    }]
    monkeypatch.setattr(td, "get_holder_trades", lambda *a, **kw: trades)

    sigs = ss._collect_holder_changes()

    content = sigs[0]["content"]
    assert "变动股数: 100 万股" in content, content
    assert "变动金额: 2,560 万元" in content, content
    assert "25,600,000" not in content, f"金额没有换算: {content}"


@pytest.mark.parametrize("amount", [0, 0.0, None, "", "0", "abc", -1.0])
def test_holder_change_hides_non_positive_amount(ss, monkeypatch, amount):
    """BUG-2 降级：amount <= 0 / 脏值 → 隐藏该字段（与 holder_count 同风格）。"""
    import services.tushare_data as td

    trades = [{
        "ts_code": "601199.SH", "ann_date": "20260902",
        "holder_name": "某股东", "in_de": "DE",
        "change_vol": 500000.0, "change_amount": amount,
    }]
    monkeypatch.setattr(td, "get_holder_trades", lambda *a, **kw: trades)

    content = ss._collect_holder_changes()[0]["content"]

    assert "变动股数: 50 万股" in content, content
    assert "变动金额" not in content, f"脏值/0 金额不该显示: {amount!r} -> {content}"


def test_holder_change_never_raises_on_dirty_vol(ss, monkeypatch):
    """BUG-2 容错：change_vol 脏值不得抛异常（调用点被 try/except 包着只 print，
    抛异常的表现是**整批增减持信号静默消失**）。"""
    import services.tushare_data as td

    trades = [
        {"ts_code": "000001.SZ", "holder_name": "A", "change_vol": None, "in_de": "DE"},
        {"ts_code": "000002.SZ", "holder_name": "B", "change_vol": "abc", "in_de": "DE"},
        {"ts_code": "000003.SZ", "holder_name": "C", "change_vol": "inf", "in_de": "DE"},
        {"ts_code": "000004.SZ", "holder_name": "D", "change_vol": -500000.0, "in_de": "DE"},
    ]
    monkeypatch.setattr(td, "get_holder_trades", lambda *a, **kw: trades)

    sigs = ss._collect_holder_changes()

    assert [s["codes"][0] for s in sigs] == ["000001", "000002", "000003", "000004"]
    assert "变动股数: -50 万股" in sigs[3]["content"]


# ============================================================
# B3：增减持方向必须用 in_de，不能用 change_type
# ============================================================

def test_direction_comes_from_in_de_not_change_type(ss, monkeypatch):
    """B3 反例夹具 1：锁死「不得再使用 change_type」。

    构造一行**同时**带 change_type="增持"（接口根本不返回的字段）和
    in_de="DE"（接口真实字段）。正确实现只能看 in_de ⇒ 结果是**减持**。

    这条用例的意义：如果哪天有人把 `change_type` 改回判定依据，或者把
    in_de 的优先级放到 change_type 之后，这条立刻变红。
    """
    import services.tushare_data as td

    trades = [{
        "ts_code": "601199.SH",
        "ann_date": "20260902",
        "holder_name": "长城人寿保险股份有限公司",
        "holder_type": "C",
        "change_type": "增持",   # ← 陷阱字段：接口不返回，不得被采用
        "in_de": "DE",           # ← 真实方向
        "change_vol": 4016100.0,
    }]
    monkeypatch.setattr(td, "get_holder_trades", lambda *a, **kw: trades)

    sig = ss._collect_holder_changes()[0]

    assert "减持" in sig["title"], f"被 change_type 带偏了: {sig['title']}"
    assert "增持" not in sig["title"], f"采用了不存在的 change_type: {sig['title']}"
    assert sig["level"] == "warning", f"DE 必须是 warning: {sig['level']}"
    assert "减持" in sig["tags"]


def test_in_de_in_maps_to_info_not_warning(ss, monkeypatch):
    """B3 正例：IN → 增持 + info（修复前会被误报成减持 + warning）。"""
    import services.tushare_data as td

    trades = [{
        "ts_code": "601199.SH", "ann_date": "20260902",
        "holder_name": "某股东", "in_de": "IN", "change_vol": 1000000.0,
    }]
    monkeypatch.setattr(td, "get_holder_trades", lambda *a, **kw: trades)

    sig = ss._collect_holder_changes()[0]

    assert "增持" in sig["title"], sig["title"]
    assert sig["level"] == "info", f"IN 不得是 warning: {sig['level']}"
    assert "增持" in sig["tags"]


@pytest.mark.parametrize("in_de", ["", None, "   ", "UNKNOWN", 0, "INDE", "D"])
def test_unknown_direction_is_not_defaulted_to_reduce(ss, monkeypatch, in_de):
    """B3 反例夹具 2：锁死「不得把未知方向默认成减持」。

    这正是原 bug 的形状 —— `if x == "增持": 增持 else: 减持` 会把空值、
    None、未枚举到的新取值一律默认成减持，并连带把 level 判成 warning
    （减持是利空，会进推送）。未知就必须如实说"方向未披露"、level 降级。
    """
    import services.tushare_data as td

    trades = [{
        "ts_code": "601199.SH", "ann_date": "20260902",
        "holder_name": "某股东", "in_de": in_de, "change_vol": 1000000.0,
    }]
    monkeypatch.setattr(td, "get_holder_trades", lambda *a, **kw: trades)

    sig = ss._collect_holder_changes()[0]

    assert "方向未披露" in sig["title"], f"{in_de!r} 应如实标注未知: {sig['title']}"
    assert "减持" not in sig["title"], f"{in_de!r} 被默认成减持了: {sig['title']}"
    assert "增持" not in sig["title"], f"{in_de!r} 被默认成增持了: {sig['title']}"
    assert sig["level"] != "warning", f"未知方向不得升级为 warning: {sig['level']}"
    # 未知方向不能当成增持/减持塞进 tags（下游按 tag 过滤会误伤）
    assert "减持" not in sig["tags"], f"未知方向混进了 tags: {sig['tags']}"
    assert "增持" not in sig["tags"], f"未知方向混进了 tags: {sig['tags']}"


def test_lowercase_in_de_is_normalised(ss, monkeypatch):
    """B3 边界：大小写/空格归一化（'de' / ' in ' 也要认）。

    上游字段值形态不稳定是常态，这里统一 strip().upper() 后再比对，
    避免因为 'de' 没被识别而走"方向未披露"分支。
    """
    import services.tushare_data as td

    trades = [
        {"ts_code": "000001.SZ", "holder_name": "A", "in_de": "de", "change_vol": 1.0},
        {"ts_code": "000002.SZ", "holder_name": "B", "in_de": " in ", "change_vol": 1.0},
    ]
    monkeypatch.setattr(td, "get_holder_trades", lambda *a, **kw: trades)

    sigs = ss._collect_holder_changes()

    assert "减持" in sigs[0]["title"] and sigs[0]["level"] == "warning", sigs[0]["title"]
    assert "增持" in sigs[1]["title"] and sigs[1]["level"] == "info", sigs[1]["title"]

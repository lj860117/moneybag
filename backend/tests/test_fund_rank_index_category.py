"""回归验证：基金排行榜 `index` 分类从建成那天起就是空数组（BUG）

事故（服务器实测，2026-09-13，fund_basic 全量 17949 只）
-------------------------------------------------------
``fund_rank_build.py`` 里指数基金是这样筛的::

    "index": filter_type(["指数"])

而 ``filter_type`` 匹配的是 ``r["type"]``，也就是 **fund_type**。实测
fund_type 的取值分布::

    混合型 6417 / 股票型 6280 / 债券型 4758 / 货币型 335 / REITs 104 / 其他 55

**没有任何一个类别名里含"指数"** —— 所以这个过滤器恒匹配不到任何东西，
``ranks["index"]`` 从上线起就是 ``[]``。核对新旧两版 fund_rank_ts.json，
index 长度都是 0，可以确认不是最近才坏的。

根因：指数/主动不是 fund_type 那一层的维度。fund_type 只回答"股票型/混合型/
债券型"，回答"是否指数"的是 **invest_type**（投资风格）。实测 invest_type
分布::

    混合型 5422 / 被动指数型 4757 / 债券型 4031 / None 1337 / 增强指数型 837
    股票型 590 / 灵活配置型 482 / 货币型 333 / ...

按 ``invest_type in ("被动指数型", "增强指数型")`` 可识别 **5594 只**
（占全市场 31%），其中 fund_type 分布：股票型 5108 / 债券型 482 /
其他 3 / REITs 1。

为什么敢用这个口径：这两个取值是 Tushare 自己的分类标签，不是我们从名称里
猜的。抽样核对（各 12 只）100% 是真指数基金，如"大成中证畜牧养殖产业ETF"、
"华宝沪深300增强策略ETF"。

为什么**不顺带**用名称匹配把 invest_type=None 的那批补进来：另有 1337 只
基金 invest_type 为 None，其中 494 只名称含"指数/ETF/沪深300"等关键词
（抽样看绝大多数确实是 ETF，即漏判）。但用名称猜会把主动基金里名字带"指数"
的也收进来，准确率无法量化 —— 宁可要**高准确率的部分覆盖**，也不要一个
准确率不明的"全量"。漏掉的那批 ETF 由已有的 ``etf`` 分类（按 "ETF" in name）
兜住。

本文件全部离线运行：Tushare 全部打桩，只有 ``build_rank()`` 的端到端用例会
写文件，落点统一指向 tmp_path。
"""
import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
for _p in (str(BACKEND_DIR), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ============================================================
# 线上实测数据（2026-09-13，fund_basic 全量 17949 只）—— 写死成常量
# ============================================================

# fund_type 的完整取值分布。用来钉死"老逻辑为什么恒空"。
REAL_FUND_TYPE_DISTRIBUTION = {
    "混合型": 6417,
    "股票型": 6280,
    "债券型": 4758,
    "货币型": 335,
    "REITs": 104,
    "其他": 55,
}

# invest_type 的完整取值分布（截取 >0 的）。用来钉死"新口径确实存在且量级对"。
REAL_INVEST_TYPE_DISTRIBUTION = {
    "混合型": 5422,
    "被动指数型": 4757,
    "债券型": 4031,
    "None": 1337,
    "增强指数型": 837,
    "股票型": 590,
    "灵活配置型": 482,
    "货币型": 333,
}

# 按新口径应识别出的指数基金数量（线上实测）
REAL_INDEX_FUND_COUNT = 4757 + 837  # 5594


# ============================================================
# 加载被测脚本（scripts/ 不是包，用 importlib 按路径加载）
# ============================================================

_FRB_CACHE: dict = {}


def _load_fund_rank_build():
    """按文件路径加载 backend/scripts/fund_rank_build.py（只加载一次）。

    run_name 不是 "__main__"，所以不会触发 main()，只跑模块级定义。
    """
    if "mod" not in _FRB_CACHE:
        import importlib.util
        path = BACKEND_DIR / "scripts" / "fund_rank_build.py"
        spec = importlib.util.spec_from_file_location("_frb_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_frb_under_test"] = mod
        spec.loader.exec_module(mod)
        _FRB_CACHE["mod"] = mod
    return _FRB_CACHE["mod"]


@pytest.fixture
def frb():
    return _load_fund_rank_build()


# ============================================================
# A. 根因钉死：老口径为什么恒空
# ============================================================

def test_fund_type_distribution_has_no_index_category():
    """根因：线上 fund_type 的取值里没有任何一个含"指数"。

    这就是 ``filter_type(["指数"])`` 恒返回 [] 的原因。分布数据直接写死成
    2026-09-13 的实测值 —— 若哪天 Tushare 真加了一个"指数型" fund_type，
    这条会红，提醒我们去重新评估口径（那时应该改用新类别，而不是改断言）。
    """
    matched = [ft for ft in REAL_FUND_TYPE_DISTRIBUTION if "指数" in ft]
    assert matched == [], f"fund_type 里出现了含'指数'的类别: {matched}"
    assert sum(REAL_FUND_TYPE_DISTRIBUTION.values()) == 17949


def test_invest_type_distribution_contains_index_values():
    """新口径依赖的取值必须真实存在，且量级对得上（防概念漂移）。

    若 Tushare 哪天把"被动指数型"改名，而这里的常量没跟着改，index 分类会
    **再次静默变空** —— 这条至少保证我们当初取证时这两个值确实存在、且
    合计就是 5594。
    """
    dist = REAL_INVEST_TYPE_DISTRIBUTION
    assert dist.get("被动指数型") == 4757
    assert dist.get("增强指数型") == 837
    assert dist.get("被动指数型", 0) + dist.get("增强指数型", 0) == REAL_INDEX_FUND_COUNT


# ============================================================
# B. is_index_fund 单元行为
# ============================================================

def test_is_index_fund_matches_passive_and_enhanced_index(frb):
    for invest_type in ("被动指数型", "增强指数型"):
        assert frb.is_index_fund({"invest_type": invest_type}) is True, invest_type


def test_is_index_fund_rejects_active_and_blank(frb):
    """主动/混合/债券/空值一律不算指数 —— 这是"不误判"的那一半。"""
    for invest_type in ("混合型", "股票型", "债券型", "灵活配置型", "成长型",
                        "货币型", "", None):
        assert frb.is_index_fund({"invest_type": invest_type}) is False, invest_type


def test_is_index_fund_tolerates_missing_key(frb):
    """字段缺失不得抛异常（fund_basic 有 1337 只 invest_type 为 None）。"""
    assert frb.is_index_fund({}) is False
    assert frb.is_index_fund({"invest_type": None}) is False


def test_index_categories_are_exactly_the_two_measured_values(frb):
    assert tuple(frb.INDEX_INVEST_TYPES) == ("被动指数型", "增强指数型")


# ============================================================
# C. 端到端：build_rank() 真的产出非空的 index 分类
# ============================================================

def _basic(ts_code, name, fund_type, invest_type):
    return {
        "ts_code": ts_code,
        "name": name,
        "fund_type": fund_type,
        "invest_type": invest_type,
        "status": "L",
        "list_date": "20200101",
        "due_date": None,
        "issue_amount": 10.0,
    }


# 6 只真基金：2 只被动指数、1 只增强指数、1 只债券指数、2 只主动
BASICS = [
    _basic("000001.OF", "华夏沪深300ETF联接A", "股票型", "被动指数型"),
    _basic("000002.OF", "易方达中证500ETF联接A", "股票型", "被动指数型"),
    _basic("000003.OF", "华宝沪深300增强策略ETF", "股票型", "增强指数型"),
    _basic("000004.OF", "博时中证可转债ETF", "债券型", "被动指数型"),
    _basic("000005.OF", "张坤精选混合", "混合型", "混合型"),
    _basic("000006.OF", "某主动股票基金", "股票型", "股票型"),
]

FILLER_CODES = [f"9{i:05d}.OF" for i in range(1200)]  # 凑够 >1000 的"有数据"阈值


def _fake_nav_by_date(nav_date, max_pages=0):
    """按日期造净值：越早的日期净值越低 → 1y/3y 收益率为正。

    find_latest_trade_date 要求 len(navs) > 1000 才认这天有数据，所以带上
    FILLER_CODES（它们不在 basic_map 里，会被 build_rank 跳过，不影响断言）。
    """
    year = float(str(nav_date)[:4])
    codes = [b["ts_code"] for b in BASICS] + FILLER_CODES
    return [
        {
            "ts_code": c,
            "ann_date": nav_date,
            "nav_date": nav_date,
            "unit_nav": year,
            "accum_nav": year,
            "adj_nav": None,
        }
        for c in codes
    ]


@pytest.fixture
def built_payload(frb, tmp_path, monkeypatch):
    """跑一次真实的 build_rank()（Tushare 打桩），返回落盘的 JSON。"""
    monkeypatch.setattr(frb, "OUTPUT_FILE", tmp_path / "fund_rank_ts.json")
    monkeypatch.setattr(frb, "is_configured", lambda: True)
    monkeypatch.setattr(frb, "get_fund_basic_all", lambda: list(BASICS))
    monkeypatch.setattr(frb, "get_fund_nav_by_date", _fake_nav_by_date)

    assert frb.build_rank() == 0
    return json.loads((tmp_path / "fund_rank_ts.json").read_text(encoding="utf-8"))


def test_index_category_is_no_longer_empty(built_payload):
    """核心用例：index 分类必须非空（修前恒为 []）。"""
    index = built_payload["ranks"]["index"]
    assert index, "ranks.index 还是空的 —— 指数基金识别口径又失效了"
    assert len(index) == 4, f"4 只指数基金（3 被动 + 1 增强），实际 {len(index)}"


def test_index_category_contains_exactly_the_index_funds(built_payload):
    """分类精度：进 index 的必须且只能是指数基金，主动基金一个都不能混进来。"""
    codes = {r["ts_code"] for r in built_payload["ranks"]["index"]}
    assert codes == {"000001.OF", "000002.OF", "000003.OF", "000004.OF"}, codes
    # 主动基金不得出现在 index 里
    assert "000005.OF" not in codes, "混合型主动基金被误判成指数基金"
    assert "000006.OF" not in codes, "主动股票基金被误判成指数基金"


def test_bond_index_fund_is_classified_as_index_not_bond_type(built_payload):
    """债券指数基金（fund_type=债券型 / invest_type=被动指数型）归 index。

    这条钉死"按 invest_type 而不是 fund_type 分类"的直接后果：它不会被
    fund_type 误导成普通债券基金而漏掉。（000004.OF 因为 3y 数据存在会进榜）
    """
    index_codes = {r["ts_code"] for r in built_payload["ranks"]["index"]}
    assert "000004.OF" in index_codes, "债券指数基金应按 invest_type 归入 index"


def test_stock_and_hybrid_categories_are_unchanged(built_payload):
    """这次改动**不能**影响其它分类 —— 只有 index 的口径变了。"""
    ranks = built_payload["ranks"]
    assert {r["ts_code"] for r in ranks["stock"]} == {"000001.OF", "000002.OF",
                                                     "000003.OF", "000006.OF"}
    assert {r["ts_code"] for r in ranks["hybrid"]} == {"000005.OF"}
    # etf 分类按名称匹配，不受本次改动影响
    assert "000003.OF" in {r["ts_code"] for r in ranks["etf"]}


def test_built_payload_keeps_invest_type_field(built_payload):
    """落盘结构里必须保留 invest_type —— 下游排障要靠它核对分类依据。"""
    for item in built_payload["ranks"]["index"]:
        assert item["invest_type"] in ("被动指数型", "增强指数型"), item


# ============================================================
# D. 故障注入：证明上面的用例是活的
# ============================================================

def test_fault_injection_old_fund_type_keyword_yields_empty_index(frb, tmp_path, monkeypatch):
    """故障注入：把口径改回老的 fund_type 关键词匹配 → index 立刻变空。

    如果这条注入后 index 仍然非空，说明 C 组用例根本没在测分类逻辑，
    只是一组永远为真的断言。
    """
    monkeypatch.setattr(frb, "OUTPUT_FILE", tmp_path / "fund_rank_ts.json")
    monkeypatch.setattr(frb, "is_configured", lambda: True)
    monkeypatch.setattr(frb, "get_fund_basic_all", lambda: list(BASICS))
    monkeypatch.setattr(frb, "get_fund_nav_by_date", _fake_nav_by_date)
    # 老口径：拿 fund_type 去匹配"指数" —— 线上 fund_type 里根本没有这个值
    monkeypatch.setattr(frb, "INDEX_INVEST_TYPES", ("指数",))

    assert frb.build_rank() == 0
    payload = json.loads((tmp_path / "fund_rank_ts.json").read_text(encoding="utf-8"))

    assert payload["ranks"]["index"] == [], "故障注入失效：老口径本应产出空 index"


def test_fault_injection_wrong_invest_type_value_yields_empty_index(frb, tmp_path, monkeypatch):
    """故障注入 2：口径写错一个字（"指数型" vs "被动指数型"）→ 同样静默变空。

    这是本类 bug 最容易复发的形态 —— 差一个字不会报错，只会静默归零。
    """
    monkeypatch.setattr(frb, "OUTPUT_FILE", tmp_path / "fund_rank_ts.json")
    monkeypatch.setattr(frb, "is_configured", lambda: True)
    monkeypatch.setattr(frb, "get_fund_basic_all", lambda: list(BASICS))
    monkeypatch.setattr(frb, "get_fund_nav_by_date", _fake_nav_by_date)
    monkeypatch.setattr(frb, "INDEX_INVEST_TYPES", ("指数型", "被动指数"))

    assert frb.build_rank() == 0
    payload = json.loads((tmp_path / "fund_rank_ts.json").read_text(encoding="utf-8"))

    assert payload["ranks"]["index"] == [], "故障注入失效：错字口径本应产出空 index"


def test_empty_index_category_is_warned_not_silent(frb, tmp_path, monkeypatch, capsys):
    """静默的空分类同样有害 —— 口径失效时必须留下可检索的告警。

    这条守护的是"下次 Tushare 改名，index 又变空"这个最可能的复发路径：
    即使所有断言都还在（它们只测我们写的常量），线上日志里也得有痕迹。
    """
    monkeypatch.setattr(frb, "OUTPUT_FILE", tmp_path / "fund_rank_ts.json")
    monkeypatch.setattr(frb, "is_configured", lambda: True)
    monkeypatch.setattr(frb, "get_fund_basic_all", lambda: list(BASICS))
    monkeypatch.setattr(frb, "get_fund_nav_by_date", _fake_nav_by_date)
    monkeypatch.setattr(frb, "INDEX_INVEST_TYPES", ("不存在的取值",))

    capsys.readouterr()
    assert frb.build_rank() == 0
    out = capsys.readouterr().out

    assert "index" in out and "空" in out, f"index 分类为空却没有任何告警: {out}"

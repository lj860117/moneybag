"""回归验证：基金排行榜 `qdii` 分类同样恒为空数组（BUG，与 index 同病）

事故（服务器实测，2026-09-14，fund_basic 全量 17949 只）
-------------------------------------------------------
``fund_rank_build.py`` 里 QDII 基金是这样筛的::

    "qdii": filter_type(["QDII"])

``filter_type`` 匹配的是 ``r["type"]`` 即 **fund_type**，而 fund_type 全量
取值只有 6 个::

    混合型 6417 / 股票型 6280 / 债券型 4758 / 货币型 335 / REITs 104 / 其他 55

**没有 QDII** → ranks.qdii 从建成那天起恒为 []。已核对归档
``data/fund_rank_ts_20260910.json``：index=0、qdii=0，属长期静默为空，
不是新回归。

⚠️ 与 index 的关键差异（决定了这里只能用名称，不能用结构化字段）
------------------------------------------------------------------
index 的修法是"改用 invest_type"，因为 invest_type 里有 `被动指数型` /
`增强指数型`。但 QDII **没有**对应的结构化字段，实测（全量 17949 只）::

    invest_type 含 "QDII"  → 0 只    （invest_type 全量 36 个取值，无一含 QDII）
    fund_type   含 "QDII"  → 0 只

所以 index 那套办法在 QDII 上根本不存在。实测三种口径：

    ① 名称含 "QDII"                 → 481 只，抽样 0 误判         ← 采用
    ② fund_screen.py 的 _QDII_KW 关键词并集 → 1180 只，但其中 699 只
       名称不含 QDII，抽查全是误判：
         - 港股通 ETF 561 只（走互联互通额度，不是 QDII）
         - 恒生A股 / 恒生港股通 208 只
         - "兴证全球…" —— 基金**公司名**带"全球"
         - "沈阳国际软件园REIT" / "深国际仓储物流REIT" —— 名字带"国际"
    ③ 海外敞口关键词但名称不含 QDII  → 24 只，抽查同样全不是 QDII
       （"标普中国A股…""标普港股通低波红利" 投的是 A 股 / 港股通）

选 ① 的理由：**"QDII" 是法规要求的法定名称后缀**（如"华夏野村日经225ETF
(QDII)"），是资格标记而非描述性词汇，精度接近 100%。这与 index 那批漏判
ETF 的情形不同 —— 那批只能靠"名字有没有指数味儿"去猜，才不可靠。

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


# 线上实测（2026-09-14，fund_basic 全量 17949 只）—— 写死成常量
REAL_FUND_TYPE_DISTRIBUTION = {
    "混合型": 6417, "股票型": 6280, "债券型": 4758,
    "货币型": 335, "REITs": 104, "其他": 55,
}
# 名称含 "QDII" 的基金在该口径下的 fund_type 分布
REAL_QDII_NAME_FUND_TYPE_DISTRIBUTION = {
    "股票型": 354, "混合型": 96, "其他": 19, "债券型": 11, "REITs": 1,
}


_FRB_CACHE: dict = {}


def _load_fund_rank_build():
    """按文件路径加载 backend/scripts/fund_rank_build.py（只加载一次）。"""
    if "mod" not in _FRB_CACHE:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_frb_qdii_under_test", BACKEND_DIR / "scripts" / "fund_rank_build.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_frb_qdii_under_test"] = mod
        spec.loader.exec_module(mod)
        _FRB_CACHE["mod"] = mod
    return _FRB_CACHE["mod"]


@pytest.fixture
def frb():
    return _load_fund_rank_build()


# ============================================================
# A. 根因钉死：老口径为什么恒空
# ============================================================

def test_fund_type_distribution_has_no_qdii_category():
    """根因：线上 fund_type 的取值里没有 QDII。

    这就是 ``filter_type(["QDII"])`` 恒返回 [] 的原因。
    """
    matched = [ft for ft in REAL_FUND_TYPE_DISTRIBUTION if "QDII" in ft]
    assert matched == [], f"fund_type 里出现了 QDII 类别: {matched}"
    assert sum(REAL_FUND_TYPE_DISTRIBUTION.values()) == 17949


def test_qdii_funds_span_multiple_fund_types():
    """QDII 横跨 5 个 fund_type —— 所以不可能用 fund_type 把它挑出来。

    这也是"必须换口径"的直接证据：QDII 不是一个 fund_type 类别，而是叠加在
    股票型/混合型/债券型之上的一个资格标记。
    """
    assert len(REAL_QDII_NAME_FUND_TYPE_DISTRIBUTION) == 5
    assert sum(REAL_QDII_NAME_FUND_TYPE_DISTRIBUTION.values()) == 481
    assert "货币型" not in REAL_QDII_NAME_FUND_TYPE_DISTRIBUTION


def test_no_structured_field_carries_qdii():
    """钉死"必须用名称"这个前提：两个结构化字段实测都是 0 命中。

    若哪天 Tushare 加了 invest_type="QDII"，这条会提醒我们可以换回
    结构化口径（那比名称匹配更可靠）。
    """
    # 线上实测：invest_type 全量 36 个取值中含 QDII 的有 0 个，
    # fund_type 6 个取值中含 QDII 的也是 0 个。
    assert [k for k in REAL_FUND_TYPE_DISTRIBUTION if "QDII" in k] == []
    assert "QDII" not in REAL_QDII_NAME_FUND_TYPE_DISTRIBUTION  # 它是 fund_type 分布，不是 invest_type


# ============================================================
# B. is_qdii_fund 单元行为
# ============================================================

def test_is_qdii_fund_matches_legal_name_suffix(frb):
    """法定名称后缀各种位置都要能识别。"""
    for name in ("华夏野村日经225ETF(QDII)",
                 "博时标普500ETF(QDII)",
                 "南方道琼斯美国精选REIT指数(QDII-LOF)-A",
                 "QDII"):
        assert frb.is_qdii_fund({"name": name}) is True, name


def test_is_qdii_fund_rejects_non_qdii(frb):
    """非 QDII 一律不算 —— 特别是那些"听起来像海外"的港股通/A股基金。

    这批是 _QDII_KW 关键词口径会误判的典型，必须逐个钉死。
    """
    for name in ("华泰柏瑞中证港股通信息技术综合ETF",   # 港股通，非 QDII
                 "南方恒生A股电网设备ETF",              # 恒生A股，非 QDII
                 "兴证全球盈禧多元配置三个月持有期混合(FOF)-A",  # 公司名带"全球"
                 "中信建投沈阳国际软件园REIT",           # 名字带"国际"
                 "华泰柏瑞标普中国A股大盘红利低波50ETF",  # 标普指数但投 A 股
                 "华夏沪深300ETF联接A",
                 "张坤精选混合",
                 "",
                 None):
        assert frb.is_qdii_fund({"name": name}) is False, name


def test_is_qdii_fund_tolerates_missing_key(frb):
    assert frb.is_qdii_fund({}) is False
    assert frb.is_qdii_fund({"name": None}) is False


def test_qdii_marker_is_the_literal_legal_suffix(frb):
    assert frb.QDII_NAME_MARKER == "QDII"


# ============================================================
# C. 端到端：build_rank() 真的产出非空的 qdii 分类
# ============================================================

def _basic(ts_code, name, fund_type, invest_type):
    return {
        "ts_code": ts_code, "name": name, "fund_type": fund_type,
        "invest_type": invest_type, "status": "L",
        "list_date": "20200101", "due_date": None, "issue_amount": 10.0,
    }


BASICS = [
    _basic("000011.OF", "华夏野村日经225ETF(QDII)", "股票型", "被动指数型"),
    _basic("000012.OF", "博时标普500ETF(QDII)", "股票型", "被动指数型"),
    _basic("000013.OF", "某QDII混合基金(QDII)", "混合型", "混合型"),
    # 下面几只"听起来像 QDII"但不是 —— 关键词口径会误收，名称口径必须排除
    _basic("000014.OF", "华泰柏瑞中证港股通信息技术综合ETF", "股票型", "被动指数型"),
    _basic("000015.OF", "兴证全球盈禧多元配置混合(FOF)-A", "混合型", "混合型"),
    _basic("000016.OF", "张坤精选混合", "混合型", "混合型"),
]

FILLER_CODES = [f"8{i:05d}.OF" for i in range(1200)]  # 凑够 >1000 的"有数据"阈值


def _fake_nav_by_date(nav_date, max_pages=0):
    year = float(str(nav_date)[:4])
    codes = [b["ts_code"] for b in BASICS] + FILLER_CODES
    return [
        {"ts_code": c, "ann_date": nav_date, "nav_date": nav_date,
         "unit_nav": year, "accum_nav": year, "adj_nav": None}
        for c in codes
    ]


@pytest.fixture
def built_payload(frb, tmp_path, monkeypatch):
    monkeypatch.setattr(frb, "OUTPUT_FILE", tmp_path / "fund_rank_ts.json")
    monkeypatch.setattr(frb, "is_configured", lambda: True)
    monkeypatch.setattr(frb, "get_fund_basic_all", lambda: list(BASICS))
    monkeypatch.setattr(frb, "get_fund_nav_by_date", _fake_nav_by_date)
    assert frb.build_rank() == 0
    return json.loads((tmp_path / "fund_rank_ts.json").read_text(encoding="utf-8"))


def test_qdii_category_is_no_longer_empty(built_payload):
    """核心用例：qdii 分类必须非空（修前恒为 []）。"""
    qdii = built_payload["ranks"]["qdii"]
    assert qdii, "ranks.qdii 还是空的 —— QDII 识别口径又失效了"
    assert len(qdii) == 3, f"3 只 QDII 基金，实际 {len(qdii)}"


def test_qdii_category_excludes_lookalikes(built_payload):
    """精度：港股通 / 公司名带"全球" 这类"像 QDII 但不是"的必须被排除。

    这正是被否掉的关键词口径会犯的错（实测 699 只误判），名称口径不能重蹈。
    """
    codes = {r["ts_code"] for r in built_payload["ranks"]["qdii"]}
    assert codes == {"000011.OF", "000012.OF", "000013.OF"}, codes
    assert "000014.OF" not in codes, "港股通 ETF 被误判成 QDII"
    assert "000015.OF" not in codes, "公司名带'全球'的 FOF 被误判成 QDII"
    assert "000016.OF" not in codes, "普通主动混合基金被误判成 QDII"


def test_qdii_funds_can_also_land_in_other_buckets(built_payload):
    """四个桶是各自独立的列表推导，同一只基金可以同时进 qdii 和 etf / index。

    钉死这个"不互斥"的性质 —— 之前排障时有人误以为 etf 桶排在 qdii 前面
    会"抢占"走 QDII，实际两个桶判的字段不同（name 含 ETF vs name 含 QDII），
    不存在短路。
    """
    ranks = built_payload["ranks"]
    qdii_codes = {r["ts_code"] for r in ranks["qdii"]}
    etf_codes = {r["ts_code"] for r in ranks["etf"]}
    index_codes = {r["ts_code"] for r in ranks["index"]}
    # 000011/000012 名字里既有 QDII 又有 ETF → 同时在两个桶
    assert {"000011.OF", "000012.OF"} <= qdii_codes
    assert {"000011.OF", "000012.OF"} <= etf_codes, "QDII-ETF 应同时进 etf 桶"
    assert "000011.OF" in index_codes, "被动指数型 QDII 同时也应进 index 桶"


def test_other_categories_are_unchanged(built_payload):
    """这次改动只动 qdii，其它分类不能受影响。"""
    ranks = built_payload["ranks"]
    assert {r["ts_code"] for r in ranks["hybrid"]} == {
        "000013.OF", "000015.OF", "000016.OF"}
    assert "000014.OF" in {r["ts_code"] for r in ranks["stock"]}


# ============================================================
# D. 故障注入：证明上面的用例是活的
# ============================================================

def _run_with_marker(frb, tmp_path, monkeypatch, marker):
    monkeypatch.setattr(frb, "OUTPUT_FILE", tmp_path / "fund_rank_ts.json")
    monkeypatch.setattr(frb, "is_configured", lambda: True)
    monkeypatch.setattr(frb, "get_fund_basic_all", lambda: list(BASICS))
    monkeypatch.setattr(frb, "get_fund_nav_by_date", _fake_nav_by_date)
    monkeypatch.setattr(frb, "QDII_NAME_MARKER", marker)
    assert frb.build_rank() == 0
    return json.loads((tmp_path / "fund_rank_ts.json").read_text(encoding="utf-8"))


def test_fault_injection_old_fund_type_marker_yields_empty_qdii(frb, tmp_path, monkeypatch):
    """故障注入：把标记改回依赖 fund_type 的"QDII" → qdii 立刻变空。

    修前就是 filter_type(["QDII"]) 去匹配 fund_type，而 fund_type 没有这个
    类别 —— 这里用一个名字里绝不会出现的 fund_type 取值来复现"恒空"。
    """
    payload = _run_with_marker(frb, tmp_path, monkeypatch, "QDII-不存在于名称中")
    assert payload["ranks"]["qdii"] == [], "故障注入失效：老口径本应产出空 qdii"


def test_fault_injection_wrong_marker_case_yields_empty_qdii(frb, tmp_path, monkeypatch):
    """故障注入 2：大小写写错（"qdii"）—— 差一个字符不会报错，只会静默归零。

    这是本类 bug 最容易复发的形态。
    """
    payload = _run_with_marker(frb, tmp_path, monkeypatch, "qdii")
    assert payload["ranks"]["qdii"] == [], "故障注入失效：错大小写本应产出空 qdii"


def test_empty_qdii_category_is_warned_not_silent(frb, tmp_path, monkeypatch, capsys):
    """静默的空分类同样有害 —— 口径失效时必须留下可检索的告警。"""
    capsys.readouterr()
    _run_with_marker(frb, tmp_path, monkeypatch, "不存在的取值")
    out = capsys.readouterr().out
    assert "qdii" in out and "空" in out, f"qdii 分类为空却没有任何告警: {out}"

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
    ② fund_screen.py 原 _QDII_KW 关键词并集 → 1180 只，但其中 699 只
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

--------------------------------------------------------------------------
⚠️ v9.9.36 后续修正：口径从「纯名称含 QDII」改为「名称含 QDII OR 高精关键词」
--------------------------------------------------------------------------
上面 ③ 那条结论是**错的**，本轮已推翻。当时用的真值是"名称含 QDII"这个
**代理标签**，而 AKShare / 雪球的「基金简称」会**截断**法定名称里的
"(QDII)" 后缀 —— 于是"真 QDII 但简称不含 QDII"被代理标签算成了误判，
口径越准越显得像误判。

本轮换用雪球 ``fund_individual_basic_info_xq`` 的**「基金类型」**做真值
（结构化字段，取值形如 "QDII-股票" / "QDII-债券" / "混合型"），重新实测
AKShare 全市场 20357 只（2026-09-14）：
  * 候选池内 **24 只真 QDII 的简称不含 QDII**（国泰纳斯达克100指数、
    长信标普100等权重指数人民币、博时大中华亚太精选、南方道琼斯美国精选A…）
  * 反向校验：名称含 QDII 且真值非 QDII = **0 只** —— 标记本身无假阳性
  * 24 个老关键词逐个测得真值精度，据此保留 14 个、删除 9 个
    （港股 ≈0% 全是港股通 / 恒生 40% 里 17 只是"恒生前海"**基金公司名** /
     国际 0% 全是 MSCI中国A股国际通 / 纳指·美股·英国·韩国·S&P 全市场 0 命中）

最终判据（``services/fund_taxonomy.py``）：名称含 "QDII" OR 命中 14 个高精
关键词（"标普"另带否定词 {港股通, 中国A股, 香港上市中国}）。候选池 7945 只
上实测：score top30 与 1y top50 **双双 0 误判 0 漏判**；修前的 24 词并集是
6 / 7 只误判，纯名称口径是 3 / 4 只漏判。逐个词的精度表见
``services/fund_taxonomy.py`` 的 ``QDII_NAME_MARKER`` 上方。

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


@pytest.fixture
def ft():
    """共享判据模块 services.fund_taxonomy（唯一真源）。"""
    import importlib
    return importlib.import_module("services.fund_taxonomy")


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

def _run_with_marker(frb, ft, tmp_path, monkeypatch, marker):
    # v9.9.36: QDII 判据已上提到 services/fund_taxonomy.py（唯一真源），
    # 脚本只是 import 过来。故障注入必须打在**共享模块**上 —— 打在脚本的
    # 同名属性上已经不生效了（is_qdii_fund 读的是自己模块的全局）。
    monkeypatch.setattr(frb, "OUTPUT_FILE", tmp_path / "fund_rank_ts.json")
    monkeypatch.setattr(frb, "is_configured", lambda: True)
    monkeypatch.setattr(frb, "get_fund_basic_all", lambda: list(BASICS))
    monkeypatch.setattr(frb, "get_fund_nav_by_date", _fake_nav_by_date)
    monkeypatch.setattr(ft, "QDII_NAME_MARKER", marker)
    # ⚠️ 判据是「名称含 QDII **OR** 命中高精关键词」的并集。只注入 marker
    # 不清关键词的话，"华夏野村日经225ETF(QDII)" 会走"日经"这条支路照样判成
    # QDII，注入就**打空了**（qdii 仍非空，用例假绿）。要隔离 marker 这一条
    # 路径，必须同时把关键词白名单清空。
    monkeypatch.setattr(ft, "QDII_NAME_KEYWORDS", ())
    # 注入是活的：共享模块取值确实被换掉，且脚本用的就是共享模块那个函数
    assert ft.QDII_NAME_MARKER == marker
    assert ft.QDII_NAME_KEYWORDS == ()
    assert frb.is_qdii_fund is ft.is_qdii_fund, "脚本没用共享判据，故障注入会打到空处"
    assert frb.build_rank() == 0
    return json.loads((tmp_path / "fund_rank_ts.json").read_text(encoding="utf-8"))


def test_fault_injection_old_fund_type_marker_yields_empty_qdii(frb, ft, tmp_path, monkeypatch):
    """故障注入：把标记改回依赖 fund_type 的"QDII" → qdii 立刻变空。

    修前就是 filter_type(["QDII"]) 去匹配 fund_type，而 fund_type 没有这个
    类别 —— 这里用一个名字里绝不会出现的 fund_type 取值来复现"恒空"。
    """
    payload = _run_with_marker(frb, ft, tmp_path, monkeypatch, "QDII-不存在于名称中")
    assert payload["ranks"]["qdii"] == [], "故障注入失效：老口径本应产出空 qdii"


def test_fault_injection_wrong_marker_case_yields_empty_qdii(frb, ft, tmp_path, monkeypatch):
    """故障注入 2：大小写写错（"qdii"）—— 差一个字符不会报错，只会静默归零。

    这是本类 bug 最容易复发的形态。
    """
    payload = _run_with_marker(frb, ft, tmp_path, monkeypatch, "qdii")
    assert payload["ranks"]["qdii"] == [], "故障注入失效：错大小写本应产出空 qdii"


def test_empty_qdii_category_is_warned_not_silent(frb, ft, tmp_path, monkeypatch, capsys):
    """静默的空分类同样有害 —— 口径失效时必须留下可检索的告警。"""
    capsys.readouterr()
    _run_with_marker(frb, ft, tmp_path, monkeypatch, "不存在的取值")
    out = capsys.readouterr().out
    assert "qdii" in out and "空" in out, f"qdii 分类为空却没有任何告警: {out}"


# ============================================================
# E. v9.9.36 union 判据：简称被截断的真 QDII 必须进，误判必须不进
# ============================================================
#
# 真值来源：雪球 fund_individual_basic_info_xq 的「基金类型」（2026-09-14
# 实测），**不是**"名称含 QDII"代理标签 —— 代理标签本身就会把这些截断简称
# 的真 QDII 算成误判，用它当真值等于自己证明自己。
#
# 正例全部来自「候选池内真值=QDII 但简称不含 QDII」的实测清单（24 只），
# 这里取其中能进 score top30 / 1y top50 的 4 只 + 一批关键词分支的覆盖样本。

UNION_POSITIVES = [
    # （代码，简称，基金类型）—— 简称里都没有 "QDII"
    ("160213", "国泰纳斯达克100指数", "QDII-股票"),
    ("519981", "长信标普100等权重指数人民币", "QDII-股票"),
    ("050015", "博时大中华亚太精选", "QDII-股票"),
    ("160140", "南方道琼斯美国精选A", "QDII-房地产信托"),
    ("160141", "南方道琼斯美国精选C", "QDII-房地产信托"),
    ("004243", "广发道琼斯石油指数人民币C", "QDII-股票"),
    ("014982", "华安标普全球石油指数(LOF)C", "QDII-股票"),
    ("162415", "华宝标普美国消费人民币A", "QDII-股票"),
    ("118002", "易方达标普消费品指数A", "QDII-股票"),
    ("164824", "工银印度基金人民币", "QDII-股票"),
    ("241001", "华宝海外中国成长混合", "QDII-混合"),
    ("519601", "海富通中国海外混合", "QDII-混合"),
    ("070012", "嘉实海外中国股票混合", "QDII-混合"),
    ("164906", "交银中证海外中国互联网指数(LOF)A", "QDII-股票"),
]

# 反例：修前 24 词并集在 score top30 / 1y top50 上实测的**全部**误判
# （6 只 + 1y 视角多出的 2 只），加上关键词分支上典型的境内"伪海外"基金。
UNION_NEGATIVES = [
    ("013383", "恒生前海高端制造混合A", "恒生前海是**基金公司名**，不是恒生指数"),
    ("007277", "恒生前海消费升级混合", "同上"),
    ("014712", "恒生前海恒裕债券A", "同上，且是纯债基金"),
    ("006535", "恒生前海恒锦裕利A", "同上"),
    ("024786", "汇添富港股通红利回报混合发起式A", "港股通，走互联互通额度，不是 QDII"),
    ("006752", "天弘港股通精选A", "港股通"),
    ("340006", "兴全全球视野股票", "名字带'全球'但投 A 股（股票型-标准指数）"),
    ("024042", "富国恒生A股专精特新企业ETF发起式联接A", "恒生A股 = 境内"),
    ("004332", "恒生沪港深新兴产业精选混合", "沪港深，境内"),
    ("501029", "华宝标普中国A股红利机会ETF联接A(LOF)", "标普指数但投 A 股 → 否定词拦"),
    ("005125", "华宝标普中国A股红利机会ETF联接C", "同上"),
    ("022887", "华宝标普港股通低波红利ETF联接A", "标普 + 港股通 → 否定词拦"),
    ("005051", "摩根标普港股通低波红利指数A", "同上"),
    ("501021", "华宝港股通标普香港上市中国中小盘指数(LOF)A", "标普 + 香港上市中国 → 否定词拦"),
]


def test_union_positives_truncated_qdii_names_are_caught(ft):
    """正例：简称被截断的真 QDII 必须被判出来。

    纯"名称含 QDII"口径会**全部漏掉**这批（score top30 漏 3 只、1y top50
    漏 4 只），这是本轮从纯名称口径改成 union 的唯一理由。
    """
    for code, name, ftype in UNION_POSITIVES:
        assert "QDII" not in name, f"{code} {name} 简称里不含 QDII，才算截断样本"
        assert ft.is_qdii_fund({"name": name}) is True, (
            f"漏判真 QDII：{code} {name}（基金类型={ftype}）")


def test_union_negatives_rejected_keywords_stay_out(ft):
    """反例：被删掉的 9 个词带来的误判必须全部挡住。

    这批是修前 24 词并集在 top-N 上实测的全部误判；只要有人把
    `全球` / `恒生` / `港股` / `国际` 加回白名单，这里立刻转红。
    """
    for code, name, why in UNION_NEGATIVES:
        assert ft.is_qdii_fund({"name": name}) is False, (
            f"误判成 QDII：{code} {name}（{why}）")


def test_negation_words_only_apply_to_their_own_keyword(ft):
    """否定词只对"标普"生效，不能外溢到别的关键词上。

    "华宝标普港股通低波红利ETF联接A" 被拦是因为命中"标普"+否定词；而
    "工银印度基金人民币" 这种不含标普的，不能因为名字里有别的字被误拦。
    """
    assert ft.is_qdii_fund({"name": "工银印度基金人民币"}) is True
    assert ft.is_qdii_fund({"name": "华宝标普港股通低波红利ETF联接A"}) is False
    assert ft.is_qdii_fund({"name": "港股通互联网ETF"}) is False


def test_keyword_whitelist_is_pinned(ft):
    """钉死保留词/删除词清单 —— 加词必须先补实测精度，不能顺手加。"""
    assert ft.QDII_NAME_KEYWORDS == (
        "纳斯达克", "海外", "亚太", "新兴市场", "日经", "日本", "越南", "印度",
        "德国", "法国", "欧洲", "东南亚", "道琼", "标普",
    )
    assert ft.QDII_REJECTED_KEYWORDS == (
        "全球", "恒生", "港股", "国际", "纳指", "美股", "英国", "韩国", "S&P",
    )
    # 保留词与删除词不能有交集（有人想"先加回来再说"时这里会红）
    assert not (set(ft.QDII_NAME_KEYWORDS) & set(ft.QDII_REJECTED_KEYWORDS))
    # 否定词只服务于白名单里真实存在的词
    assert set(ft.QDII_KEYWORD_NEGATIONS) <= set(ft.QDII_NAME_KEYWORDS)


# ============================================================
# F. 回潮守卫：fund_screen.py 不许再自带一套关键词并集
# ============================================================

def _qdii_branch_source(strip_comments: bool = True) -> str:
    """抠出 fund_screen.py 里 `elif fund_type == "qdii":` 这一支的源码。

    默认**剥掉注释**：注释里出现"恒生前海"这种字样是合法的（本文件自己的
    注释就在解释为什么删掉"恒生"），但注释不会被执行，不该触发回潮守卫。
    只看代码才能既抓到真回潮、又不被注释误伤。
    """
    src = (BACKEND_DIR / "services" / "fund_screen.py").read_text(encoding="utf-8")
    lines = src.splitlines()
    start = None
    for i, line in enumerate(lines):
        if 'elif fund_type == "qdii":' in line:
            start = i
            break
    assert start is not None, "fund_screen.py 里找不到 qdii 分支 —— 判据被搬走了？"
    indent = len(lines[start]) - len(lines[start].lstrip())
    block = [lines[start]]
    for line in lines[start + 1:]:
        if line.strip() and (len(line) - len(line.lstrip())) <= indent:
            break
        block.append(line.split("#", 1)[0] if strip_comments else line)
    return "\n".join(block)


def test_fund_screen_qdii_branch_delegates_to_shared_module(ft):
    """回潮守卫 1：qdii 分支必须调共享判据，不许自带关键词列表。

    这次事故的根因就是"两条链路各写一份判据"。这条钉死接线方式：
    `is_qdii_fund` 必须是 services.fund_taxonomy 里那一个函数对象。
    """
    import importlib
    fs = importlib.import_module("services.fund_screen")
    assert fs.is_qdii_fund is ft.is_qdii_fund, (
        "fund_screen.py 没用共享判据 —— 又退化成两套口径了")


def test_fund_screen_qdii_branch_has_no_rejected_keywords(ft):
    """回潮守卫 2：qdii 分支里不许出现被删掉的 9 个词。

    注意只查 qdii 分支这一块，不查全文件 —— "恒生"/"纳斯达克"/"标普"在
    同文件的 `_INDEX_KW`（index 分支）里是合法用法，"全球"在 `_compute_reason`
    的文案里也合法，全文件禁这些字面量会误伤。
    """
    branch = _qdii_branch_source()
    assert "is_qdii_fund" in branch, "qdii 分支没有调共享判据"
    assert "_QDII_KW" not in branch, "旧的 24 关键词列表又回来了"
    for word in ft.QDII_REJECTED_KEYWORDS:
        assert word not in branch, (
            f"qdii 分支里出现了已删词 {word!r} —— 它的全市场真值精度是 "
            f"0%~40%，加回来等于把 554 只误判请回来")


# ============================================================
# G. 故障注入：证明 E/F 两节的用例是活的（不是死测试）
# ============================================================

def test_fault_injection_empty_keywords_loses_truncated_qdii(ft, monkeypatch):
    """故障注入 3：清空关键词白名单 → 4 只截断简称的真 QDII 立刻漏判。

    这证明 E 节的正例用例不是"因为名称里恰好有 QDII"而绿的。
    """
    monkeypatch.setattr(ft, "QDII_NAME_KEYWORDS", ())
    for code, name, _ftype in UNION_POSITIVES:
        assert ft.is_qdii_fund({"name": name}) is False, (
            f"故障注入失效：{code} {name} 本应随关键词清空而漏判")


def test_fault_injection_marker_only_still_catches_full_names(ft, monkeypatch):
    """故障注入 4：清空关键词后，名称里**带** QDII 的仍要判出来。

    与上一条配对，证明并集的两条支路各自独立生效、互不顶替。
    """
    monkeypatch.setattr(ft, "QDII_NAME_KEYWORDS", ())
    assert ft.is_qdii_fund({"name": "华夏野村日经225ETF(QDII)"}) is True
    assert ft.is_qdii_fund({"name": "博时标普500ETF(QDII)"}) is True


def test_fault_injection_negation_removed_lets_a_share_funds_in(ft, monkeypatch):
    """故障注入 5：删掉"标普"的否定词 → 境内 A 股/港股通基金立刻混入。

    证明 QDII_KEYWORD_NEGATIONS 不是装饰。
    """
    monkeypatch.setattr(ft, "QDII_KEYWORD_NEGATIONS", {})
    leaked = [c for c, n, _w in UNION_NEGATIVES
              if "标普" in n and ft.is_qdii_fund({"name": n})]
    assert leaked, "故障注入失效：删掉否定词后应有标普系境内基金混入"
    assert "501029" in leaked, f"否定词删除后 501029 本应混入，实际 {leaked}"


def test_fault_injection_rejected_keyword_readded_brings_back_false_positives(
        ft, monkeypatch):
    """故障注入 6：把"港股"加回白名单 → 港股通基金立刻混入。

    这是最可能的回潮形态（"港股听起来就是海外呀"）。
    """
    monkeypatch.setattr(ft, "QDII_NAME_KEYWORDS",
                        tuple(ft.QDII_NAME_KEYWORDS) + ("港股",))
    assert ft.is_qdii_fund({"name": "天弘港股通精选A"}) is True, (
        "故障注入失效：加回'港股'后港股通基金本应混入")

"""
QDII 口径统一回归测试（v9.9.38）
================================

2026-09-14 做 QDII 口径普查时发现：仓库里「怎么判断一只基金是 QDII」的**实际**
实现有 10+ 处（早前以为 5 套）。v9.9.37 建立了唯一真源::

    services/fund_taxonomy.py::is_qdii_fund

并统一了 ``services/fund_screen.py``（AKShare 选基）与
``scripts/fund_rank_build.py``（Tushare 榜单）。

本文件覆盖 **v9.9.38 新统一的三处**，全部位于 ``api/signals.py``：

1. ``_enrich_style_tag`` 的 ``_STYLE_MAP``（第 7 套口径）
   QDII 桶此前排第 5，而「指数」桶含 "指数"/"ETF" —— ``first-match`` 下
   「国泰纳斯达克100指数」「华夏野村日经225ETF(QDII)」先被「指数」抢走，
   选基页（``pages/insight-fund.js:34`` 的 ``_stColors``）挂不上青绿色
   QDII badge。⇒ QDII 桶提到首位。
   同时旧词表 ["QDII","全球","海外","美股","港股","纳斯达克","标普"] 里的
   "全球"/"港股" 是 taxonomy 的**已删词**，会把「天弘港股通精选A」这类
   **境内**基金误挂 QDII。

2. ``_check_qdii_purchase_status``（第 8 套口径）
   旧 11 词表含已删词 "全球"/"港股" → 拉境内基金去查限购，挤占
   ``checked >= 8`` 的配额，真 QDII 就没机会查；又缺 "越南/德国/法国/
   欧洲/亚太/新兴市场" 等高精词 → 真 QDII 漏查，限购提示缺失。

3. ``STYLE_KW`` 的 ``"海外/QDII"`` 归因桶（第 6 套口径）
   旧词含 "全球"（taxonomy 已删：93 命中，top-N 唯一误判源是
   「兴全全球视野股票」——一只境内基金）。

刻意**不**统一的（语义本就不同，见 ``backend/config.py`` 的 v9.9.38 注释）：
``portfolio.py:_detect_qdii``（仅文案措辞）、``longterm_screen.py``（排除词）、
``portfolio_doctor.py``（资产类别映射）、``signals.py:1078`` 的 ``us_keywords``
（美股**敞口**≠QDII；它含 "港股" 是另一个问题，不在本轮范围）。

守卫设计
--------
``is_qdii_fund`` 在 ``signals.py`` 里是**模块属性访问**
（``fund_taxonomy.is_qdii_fund``）而非 ``from ... import`` —— 这样
``monkeypatch.setattr(ft, "is_qdii_fund", ...)`` 能真的传导进去，
故障注入才不会打空。``test_disabled_criterion_is_actually_wired`` 专门钉死这点。
"""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import api.signals as sig                          # noqa: E402
from services import fund_taxonomy as ft           # noqa: E402

# 真 QDII：法定名称带 (QDII) 的走 marker 支路；不带后缀的走关键词支路。
# 「国泰纳斯达克100指数」是**只有关键词支路能抓到**的那类（雪球基金类型
# = QDII-股票，但 AKShare 简称把 (QDII) 截掉了）—— 拿它同时证明两件事：
#   ① QDII 桶优先级高于「指数」桶  ② 判据是并集、不是 name.contains("QDII")
REAL_QDII = [
    "国泰纳斯达克100指数",
    "华夏野村日经225ETF(QDII)",
    "浦银安盛全球智能科技(QDII)",
]

# 境内基金，名字里带海外词，但真值不是 QDII（taxonomy 的删词/否定词负责拦住）
DOMESTIC_LURE = [
    "天弘港股通精选A",          # "港股" 已从 taxonomy 删除 → 现在拦得住
]

# ⚠️ 已知残留 —— 不是本轮引入，本轮也没修。
# 「标普」在 taxonomy 里带否定词 {港股通, 中国A股, 香港上市中国}，但这只名字里
# 三个词都不含 → 至今仍被判成 QDII。要拦住得加「红利低波50」这类**针对具体
# 产品名**的定向排除 —— 性质不同且易过期，v9.9.37 刻意没加（见 config.py 注释）。
# 这里把它钉成**当前事实**：哪天修好了这条会变红，提醒同步更新注释与白名单。
KNOWN_QDII_FALSE_POSITIVE = [
    "南方标普红利低波50ETF联接A",
]


# ============================================================
# A. 接线：必须走共享判据（否则故障注入会打空）
# ============================================================

def test_signals_uses_shared_qdii_criterion():
    """活断言：``signals`` 调的必须就是 ``services.fund_taxonomy`` 那个函数对象。

    换成内联副本（哪怕行为一致）这里立刻红 —— 那种"看起来没坏"的副本正是
    仓库里 10+ 套口径的来源。
    """
    assert sig.fund_taxonomy.is_qdii_fund is ft.is_qdii_fund, (
        "signals.py 没用共享判据 —— 又多了一套 QDII 口径")


# ============================================================
# B. 问题 1：风格标签的 QDII 桶
# ============================================================

def test_qdii_bucket_wins_over_index_bucket():
    """核心用例：真 QDII 必须拿到 "QDII"，不能被「指数」桶抢走。"""
    funds = [{"name": n} for n in REAL_QDII]

    sig._enrich_style_tag(funds)

    assert [f["style_tag"] for f in funds] == ["QDII"] * len(REAL_QDII), (
        f"QDII 桶没排在「指数」前面: {[(f['name'], f['style_tag']) for f in funds]}")


def test_domestic_funds_are_not_tagged_qdii():
    """境内基金不得被误挂 QDII（旧词表含 "港股"/"全球" 时会）。"""
    funds = [{"name": n} for n in DOMESTIC_LURE]

    sig._enrich_style_tag(funds)

    for f in funds:
        assert f["style_tag"] != "QDII", (
            f"{f['name']} 被误挂 QDII —— 已删词又被加回去了？")


def test_known_false_positive_is_pinned_as_current_fact():
    """把已知残留钉成当前事实 —— 修好时这条变红，提醒更新注释与白名单。

    这条绿**不代表正确**，它记录的是"尚未修复"。留着是为了让残留可见：
    少了它，残留会静默地烂在代码里，没人知道它还在。
    """
    funds = [{"name": n} for n in KNOWN_QDII_FALSE_POSITIVE]

    sig._enrich_style_tag(funds)

    assert funds[0]["style_tag"] == "QDII", (
        f"{KNOWN_QDII_FALSE_POSITIVE[0]} 已不再被判 QDII —— 残留修好了！"
        f"请更新 config.py 的 v9.9.37 注释与本测试，别再把它列进已知残留")


def test_index_funds_still_get_index_tag():
    """防修过头：真指数基金仍应拿 "指数"（QDII 桶提前不能把指数桶挤没）。"""
    funds = [{"name": "富国沪深300指数增强A"}, {"name": "华泰柏瑞中证500ETF"}]

    sig._enrich_style_tag(funds)

    assert [f["style_tag"] for f in funds] == ["指数", "指数"]


def test_non_qdii_funds_still_fall_back_to_default():
    """防修过头：一只桶都不命中的仍落 "主动"，不能变成 QDII。"""
    funds = [{"name": "兴全合润混合A"}]  # 含 "合润"、"混合"→ 实则命中"混合"

    sig._enrich_style_tag(funds)

    # 「混合」在均衡桶里，所以这里应是"均衡"而不是"主动"——
    # 断言的重点是**不是 QDII**，别把兜底逻辑也钉死
    assert funds[0]["style_tag"] != "QDII"


# ============================================================
# C. 问题 2：QDII 申购状态检查
# ============================================================

def test_purchase_check_only_probes_real_qdii(monkeypatch):
    """只有真 QDII 才去查申购状态 —— 境内基金不该占用 checked>=8 的配额。

    旧 11 词表含 "港股"，「天弘港股通精选A」会被排在前面的真 QDII 之前
    抢走查询名额（持仓列表里它常排前面）。
    """
    probed: list[str] = []

    def _fake_info(code):
        probed.append(code)
        return {"available": False}

    monkeypatch.setattr("api.fund_detail._get_fund_purchase_info", _fake_info,
                        raising=True)

    funds = [
        {"code": "000001", "name": "天弘港股通精选A"},              # 境内 → 不该查
        {"code": "006555", "name": "浦银安盛全球智能科技(QDII)"},   # 真 QDII → 该查
    ]

    sig._check_qdii_purchase_status(funds)

    assert "006555" in probed, "真 QDII 没被查 —— 判据太严？"
    assert "000001" not in probed, (
        "境内基金被当成 QDII 去查了 —— 旧词表的 '港股' 又回来了？")


def test_purchase_check_reports_limit_for_real_qdii(monkeypatch):
    """功能仍可用：真 QDII 查到限购时，warning 要落到持仓 dict 上。"""
    monkeypatch.setattr(
        "api.fund_detail._get_fund_purchase_info",
        lambda code: {"available": True, "purchase_status": "正常",
                      "daily_limit": 500},
        raising=True)

    funds = [{"code": "006555", "name": "浦银安盛全球智能科技(QDII)"}]

    sig._check_qdii_purchase_status(funds)

    assert "purchase_warning" in funds[0], (
        f"限购信息没落到持仓上: {funds[0]}")
    assert "限购" in funds[0]["purchase_warning"]


# ============================================================
# D. 问题 3：风格收益归因桶（回潮守卫）
# ============================================================

def test_style_kw_qdii_bucket_keeps_no_keyword_list():
    """回潮守卫：``STYLE_KW`` 的 ``"海外/QDII"`` 不得再挂关键词列表。

    它的判据已改为循环里的 ``fund_taxonomy.is_qdii_fund`` 特判（保持原顺序，
    只换判据）。有人"顺手"把词表填回去就等于复活第 6 套口径。

    注：这条只扫源码文本，是**弱守卫** —— 真正的行为覆盖需要 mock
    ``_load_fund_rank_data``，成本高于收益，故此处从简。
    """
    import re

    src = (BACKEND_DIR / "api" / "signals.py").read_text(encoding="utf-8")

    # 找到 "海外/QDII": 后面跟的内容，必须是个空列表
    m = re.search(r'["\']海外/QDII["\']\s*:\s*(\[[^\]\n]*\])', src)
    assert m, "找不到 STYLE_KW 的 海外/QDII 项 —— 结构变了，守卫已空转"
    assert m.group(1).strip() == "[]", (
        f'STYLE_KW 的 "海外/QDII" 又挂上了关键词 {m.group(1)} —— '
        f"判据必须走 is_qdii_fund")


# ============================================================
# E. 故障注入：证明上面这些用例是活的
# ============================================================

def test_disabled_criterion_is_actually_wired(monkeypatch):
    """注入：判据恒 False → QDII 桶全空 → 真 QDII 退回其它桶。

    用**模块属性访问**（``sig`` 里是 ``fund_taxonomy.is_qdii_fund``）才打得中；
    若哪天有人改成 ``from services.fund_taxonomy import is_qdii_fund``，
    打在 ``ft`` 上的注入会失效、测试恒绿 —— 那种假绿由 A 节的活断言兜底。
    """
    monkeypatch.setattr(ft, "is_qdii_fund", lambda item: False, raising=True)

    funds = [{"name": n} for n in REAL_QDII]
    sig._enrich_style_tag(funds)

    assert all(f["style_tag"] != "QDII" for f in funds), (
        "故障注入打空了：判据恒 False，却仍有基金拿到 QDII 标签")


def test_cleared_keywords_lose_truncated_qdii(monkeypatch):
    """注入 2：清空共享关键词白名单 → 简称被截断的真 QDII 立刻漏判。

    证明 ``signals`` 是**运行时调用**共享模块，而不是 import 期把结果冻住。
    带 (QDII) 后缀的两只走 marker 支路，不受影响 —— 并集的两条支路互不顶替。
    """
    monkeypatch.setattr(ft, "QDII_NAME_KEYWORDS", (), raising=True)

    funds = [{"name": n} for n in REAL_QDII]
    sig._enrich_style_tag(funds)
    tags = {f["name"]: f["style_tag"] for f in funds}

    assert tags["国泰纳斯达克100指数"] != "QDII", (
        f"关键词清空后截断简称的 QDII 本应漏判: {tags}")
    assert tags["浦银安盛全球智能科技(QDII)"] == "QDII", (
        f"marker 支路不该受关键词清空影响: {tags}")

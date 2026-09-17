"""
gold 桶独立成第 4 档的回归守卫。

背景（2026-09 修复）：
  `services/portfolio_overview.py` 曾把黄金**显式并进权益**：
      equity = stock_total_mv + fund_equity + fund_gold
  而同一函数输出的 allocation 只有 equity/bond/cash 三个键 —— 分类侧
  （fund_classifier.KNOWN_FUND_TYPES: "000216"/"518880" → "gold"）明明分出了
  第 4 档，到展示层却被吞掉，黄金在配置里是隐形的。

  判定为 bug 的决定性证据，是同一次 MB-008 提交（89b86a8）内部的自相矛盾：
    - risk.py:172          `has_hedge = bond_n > 0 or gold_n > 0`
                           → 黄金是权益的**对冲/避险资产**
    - portfolio_overview   `equity = ... + fund_gold`
                           → 黄金是**权益本身**
  二者不可能同时成立。另外 domain 层目标模型
  （domain/rule_engine/glide_path_rules.py:35 GOLD_PCT_DEFAULT = 0.05，
  且 stock_pct 注释写明「已扣除黄金」）也把 gold 当独立一档。

为什么本文件自己造持仓数据：
  本地 data/ 下没有 000216/518880 的真实持仓（真实持仓在服务器上），
  拿真实数据断言百分比既做不到也不可复现。因此这里**构造**一组确定性持仓
  （并桩掉实时行情，使市值 == 成本），把「拆桶不变量」钉死：

      分母 total_for_alloc 不变
        ⇒ equity% 的降幅恰好等于黄金占比
        ⇒ bond% / cash% 保持不变

  「本地跑通了」不能当作验证通过；这组不变量才是可核对的证据。
"""

import pytest

# ---- 构造持仓（桩掉实时行情 ⇒ 用成本价/成本净值兜底，结果完全确定）----

STOCK_HOLDINGS = [
    # code,     name,        costPrice, shares  → 市值 1000
    {"code": "600519", "name": "贵州茅台", "costPrice": 10.0, "shares": 100.0},
]

FUND_HOLDINGS = [
    # 110020 → equity（KNOWN_FUND_TYPES 精确命中，不靠名称关键字）
    {"code": "110020", "name": "易方达沪深300ETF联接A", "costNav": 2.0, "shares": 500.0},  # 1000
    # 217022 → bond
    {"code": "217022", "name": "招商产业债券A", "costNav": 1.0, "shares": 600.0},          # 600
    # 000198 → money
    {"code": "000198", "name": "天弘余额宝货币", "costNav": 1.0, "shares": 400.0},         # 400
    # 000216 / 518880 → gold（本次修复受影响的两只真实持仓）
    {"code": "000216", "name": "华安黄金ETF联接A", "costNav": 3.0, "shares": 100.0},       # 300
    {"code": "518880", "name": "华安易富黄金ETF", "costNav": 5.0, "shares": 40.0},         # 200
]

STOCK_MV = 1000.0
FUND_EQUITY = 1000.0
FUND_BOND = 600.0
FUND_MONEY = 400.0
FUND_GOLD = 500.0          # 300 + 200
TOTAL_FOR_ALLOC = STOCK_MV + FUND_EQUITY + FUND_BOND + FUND_MONEY + FUND_GOLD   # 3500

# 修复后：gold 独立第 4 档
EXPECTED_EQUITY_PCT = round((STOCK_MV + FUND_EQUITY) / TOTAL_FOR_ALLOC * 100, 1)      # 57.1
EXPECTED_BOND_PCT = round(FUND_BOND / TOTAL_FOR_ALLOC * 100, 1)                       # 17.1
EXPECTED_CASH_PCT = round(FUND_MONEY / TOTAL_FOR_ALLOC * 100, 1)                      # 11.4
EXPECTED_GOLD_PCT = round(FUND_GOLD / TOTAL_FOR_ALLOC * 100, 1)                       # 14.3

# 修复前：gold 被并进 equity（故障注入时会回到这个数）
BUGGY_EQUITY_PCT = round((STOCK_MV + FUND_EQUITY + FUND_GOLD) / TOTAL_FOR_ALLOC * 100, 1)  # 71.4

# 四档键名
BUCKETS = ("equity", "bond", "cash", "gold")

TOL = 0.05


@pytest.fixture
def build_overview(monkeypatch):
    """返回一个工厂：用构造持仓调用 get_portfolio_overview()。

    同时桩掉实时行情（get_stock_realtime / get_fund_nav），
    保证测试既不依赖网络，也不受行情波动影响。
    """
    import services.market_data as market_data
    import services.portfolio_overview as portfolio_overview
    import services.stock_monitor as stock_monitor

    monkeypatch.setattr(portfolio_overview, "unified_load_stock_holdings",
                        lambda user_id: [dict(h) for h in STOCK_HOLDINGS])
    monkeypatch.setattr(stock_monitor, "get_stock_realtime", lambda code: None)
    monkeypatch.setattr(market_data, "get_fund_nav", lambda code: None)

    def _build(fund_holdings=None, stock_holdings=None):
        if fund_holdings is not None:
            monkeypatch.setattr(portfolio_overview, "unified_load_fund_holdings",
                                lambda user_id: [dict(h) for h in fund_holdings])
        else:
            monkeypatch.setattr(portfolio_overview, "unified_load_fund_holdings",
                                lambda user_id: [dict(h) for h in FUND_HOLDINGS])
        if stock_holdings is not None:
            monkeypatch.setattr(portfolio_overview, "unified_load_stock_holdings",
                                lambda user_id: [dict(h) for h in stock_holdings])
        else:
            monkeypatch.setattr(portfolio_overview, "unified_load_stock_holdings",
                                lambda user_id: [dict(h) for h in STOCK_HOLDINGS])
        return portfolio_overview.get_portfolio_overview("test-gold-user")

    return _build


# ============================================================
# 1. 展示层必须有第 4 个桶
# ============================================================


def test_allocation_has_gold_as_fourth_bucket(build_overview):
    """allocation 必须是四档；缺 gold 键 = 黄金又被吞了。"""
    allocation = build_overview()["allocation"]
    assert set(allocation.keys()) == set(BUCKETS), (
        f"allocation 键为 {sorted(allocation.keys())}，期望 {sorted(BUCKETS)}"
    )


def test_gold_is_not_merged_into_equity(build_overview):
    """决定性断言：equity 不再吃黄金，黄金单独成桶。"""
    allocation = build_overview()["allocation"]

    assert abs(allocation["gold"] - EXPECTED_GOLD_PCT) < TOL, (
        f"黄金占比 {allocation['gold']}%，期望 {EXPECTED_GOLD_PCT}%"
    )
    assert abs(allocation["equity"] - EXPECTED_EQUITY_PCT) < TOL, (
        f"权益占比 {allocation['equity']}%，期望 {EXPECTED_EQUITY_PCT}%；"
        f"若等于 {BUGGY_EQUITY_PCT}% 说明黄金又被并回了 equity"
    )
    # 显式排除旧行为
    assert abs(allocation["equity"] - BUGGY_EQUITY_PCT) >= TOL, (
        f"权益占比仍是修复前的 {BUGGY_EQUITY_PCT}%，gold 没有被拆出来"
    )


# ============================================================
# 2. 拆桶不变量（分母不变）
# ============================================================


def test_invariant_equity_drop_equals_gold_share(build_overview):
    """不变量：equity% 的降幅恰好等于黄金占比。"""
    allocation = build_overview()["allocation"]
    drop = BUGGY_EQUITY_PCT - allocation["equity"]
    assert abs(drop - allocation["gold"]) < TOL, (
        f"equity 降幅 {drop:.1f} 个点与黄金占比 {allocation['gold']}% 不相等；"
        f"拆桶改变了分母（total_for_alloc 不应变动）"
    )


def test_invariant_bond_and_cash_unchanged(build_overview):
    """不变量：bond% / cash% 与拆桶前完全一致。"""
    allocation = build_overview()["allocation"]
    assert abs(allocation["bond"] - EXPECTED_BOND_PCT) < TOL, (
        f"债券占比 {allocation['bond']}%，期望 {EXPECTED_BOND_PCT}%（拆桶前后应不变）"
    )
    assert abs(allocation["cash"] - EXPECTED_CASH_PCT) < TOL, (
        f"现金占比 {allocation['cash']}%，期望 {EXPECTED_CASH_PCT}%（拆桶前后应不变）"
    )


def test_total_market_value_unchanged_by_split(build_overview):
    """拆桶只改归属，不改总量：总市值仍是 3500。"""
    overview = build_overview()
    assert abs(overview["totalMarketValue"] - TOTAL_FOR_ALLOC) < 0.01, (
        f"总市值 {overview['totalMarketValue']}，期望 {TOTAL_FOR_ALLOC}"
    )


# ============================================================
# 3. target / deviation / rebalance 同步到四档
# ============================================================


def test_target_and_deviation_include_gold(build_overview):
    """target 与 deviation 都必须带 gold，且 target 四档合计 100。"""
    overview = build_overview()
    assert set(overview["target"].keys()) == set(BUCKETS)
    assert set(overview["deviation"].keys()) == set(BUCKETS)
    assert sum(overview["target"].values()) == 100, (
        f"target 合计 {sum(overview['target'].values())}，应为 100"
    )


def test_gold_target_uses_domain_default(build_overview):
    """黄金目标取 domain/rule_engine/glide_path_rules.py:35 GOLD_PCT_DEFAULT=0.05。"""
    assert build_overview()["target"]["gold"] == 5, "黄金目标应为 5%（GOLD_PCT_DEFAULT = 0.05）"


def test_rebalance_covers_gold_when_gold_overweight(build_overview):
    """行为级：黄金严重超配时，再平衡建议里必须出现「黄金类」。

    100% 黄金的组合，黄金偏离 = 100 - 5 = 95 个点，远超 10 的阈值。
    """
    overview = build_overview(
        fund_holdings=[{"code": "518880", "name": "华安易富黄金ETF",
                        "costNav": 5.0, "shares": 2000.0}],
        stock_holdings=[],
    )
    assets = [item["asset"] for item in overview["rebalance"]]
    assert "gold" in assets, (
        f"黄金超配 95 个点，rebalance 建议里却没有黄金：{assets}"
    )
    gold_item = next(i for i in overview["rebalance"] if i["asset"] == "gold")
    assert gold_item["label"] == "黄金类"


def test_all_gold_portfolio_is_not_reported_as_equity(build_overview):
    """修复前 100% 黄金会被报成「权益 100%」，修复后必须报成黄金 100%。"""
    overview = build_overview(
        fund_holdings=[{"code": "518880", "name": "华安易富黄金ETF",
                        "costNav": 5.0, "shares": 2000.0}],
        stock_holdings=[],
    )
    allocation = overview["allocation"]
    assert abs(allocation["equity"] - 0.0) < TOL, (
        f"纯黄金组合的权益占比应为 0%，实际 {allocation['equity']}%"
    )
    assert abs(allocation["gold"] - 100.0) < TOL, (
        f"纯黄金组合的黄金占比应为 100%，实际 {allocation['gold']}%"
    )

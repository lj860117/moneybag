"""
KNOWN_FUND_TYPES 硬编码分类表的回归守卫。

背景（2026-09 核查）：
  `services/fund_classifier.py` 的 KNOWN_FUND_TYPES 曾把 519736（交银新成长混合，
  证监会分类「偏股混合型」）误记为 "bond"。生产 /api/fund/detail/519736 实测
  name = 交银新成长混合、top_holdings 全为个股（药明康德 10.48% 等），已纠正为 "equity"。

  纠正时踩到的陷阱（本文件的核心守卫目标）：
  `classify_fund()` 的「1. 尝试精确代码查询」分支对本表**优先短路**，返回体只有
  {type, keywords, is_mixed}，**不带 allocation**；而 `classify_and_allocate()` 的
  mixed 判据是 `fund_type == "mixed" and "allocation" in classification`。
  两者叠加 → 任何写成 "mixed" 的表条目都会让 equity/bond/money/gold 四桶全为 0，
  该笔持仓从股债配置分母里蒸发（比错成 bond 更糟）。

  因此本文件不只断言 519736 == equity，更断言「表内每个条目都必须分配到钱」，
  这样将来任何人往表里加 "mixed" 都会立刻变红，而不是静默归零。
"""

import pytest

from services.fund_classifier import (
    KNOWN_FUND_TYPES,
    classify_and_allocate,
    classify_fund,
)

# 生产 /api/fund/detail/519736 返回的 name（实测）
FUND_519736_NAME = "交银新成长混合"

# 四桶中用于「钱有没有被分配到」的字段
_BUCKETS = ("equity", "bond", "money", "gold")


# ============================================================
# 1. 519736 本体的分类
# ============================================================


def test_519736_classifies_as_equity() -> None:
    """519736 是偏股混合型，按项目惯例（risk.py:52 mixed→equity）并入 equity。"""
    result = classify_fund(code="519736", name=FUND_519736_NAME)
    assert result["type"] == "equity", (
        f"519736（{FUND_519736_NAME}）被判为 {result['type']}，期望 equity"
    )


def test_519736_is_not_bond() -> None:
    """显式反断言：历史上它是 bond，防止有人回滚回去。"""
    assert classify_fund(code="519736", name=FUND_519736_NAME)["type"] != "bond"


def test_519736_short_circuits_by_code_even_with_empty_name() -> None:
    """表是优先短路：即使名称为空，也应按代码命中表，而不是退化成 unknown。"""
    result = classify_fund(code="519736", name="")
    assert result["type"] == "equity"
    assert result["keywords"] == [], "命中 KNOWN_FUND_TYPES 时 keywords 应为空列表"


# ============================================================
# 2. 防归零回归（最关键）
# ============================================================


def test_519736_allocates_full_cost_to_equity() -> None:
    """
    防四桶归零：nav_cost=2.0 × shares=1000 = 2000 必须完整落进 equity 桶。

    这条锁住的正是「表条目写成 mixed → allocation 缺失 → 四桶全 0」的陷阱。
    """
    result = classify_and_allocate(
        code="519736",
        name=FUND_519736_NAME,
        nav_cost=2.0,
        shares=1000,
    )
    assert result["totalCost"] == pytest.approx(2000.0)
    assert result["equity"] == pytest.approx(2000.0), (
        f"equity 桶应为 2000.0，实际 {result['equity']}"
    )
    assert result["bond"] == 0, (
        f"519736 不该再进 bond 桶，实际 {result['bond']}"
    )


def test_519736_buckets_sum_to_total_cost() -> None:
    """无论类型是什么，四桶之和必须等于总成本 —— 不允许有金额凭空消失。"""
    result = classify_and_allocate(
        code="519736",
        name=FUND_519736_NAME,
        nav_cost=2.0,
        shares=1000,
    )
    bucket_sum = sum(result[b] for b in _BUCKETS)
    assert bucket_sum == pytest.approx(result["totalCost"]), (
        f"四桶之和 {bucket_sum} != 总成本 {result['totalCost']}，有金额从配置分母里蒸发"
    )


@pytest.mark.parametrize("code", sorted(KNOWN_FUND_TYPES))
def test_every_known_type_entry_allocates_all_its_money(code: str) -> None:
    """
    行为级守卫：表内**每一个**条目都必须把 100% 成本分配进桶。

    这是「防归零」的泛化版 —— 不只保护 519736，任何人往表里加一个
    下游无法消费的类型（当前就是 "mixed"，因为短路分支不吐 allocation）
    都会在这里立刻变红。
    """
    cost = 100.0
    result = classify_and_allocate(code=code, name="", nav_cost=1.0, shares=cost)
    bucket_sum = sum(result[b] for b in _BUCKETS)
    assert bucket_sum == pytest.approx(cost), (
        f"代码 {code}（KNOWN_FUND_TYPES={KNOWN_FUND_TYPES[code]!r}）只分配了 "
        f"{bucket_sum}/{cost}，四桶归零或漏分配"
    )


def test_known_types_never_contains_mixed() -> None:
    """
    表内不允许出现 "mixed"，直到短路分支会一并吐出 allocation 为止。

    这不是在断言"mixed 不好"，而是在断言"当前短路实现还撑不住 mixed"。
    要放开这个断言，必须同时改 classify_fund 的短路分支让它返回 allocation。
    """
    offenders = {c: t for c, t in KNOWN_FUND_TYPES.items() if t == "mixed"}
    assert not offenders, (
        f"KNOWN_FUND_TYPES 含 mixed 条目 {offenders}，但 classify_fund 的短路分支"
        f"不返回 allocation，会导致这些持仓四桶归零"
    )


# ============================================================
# 3. 其余条目未被碰坏
# ============================================================


@pytest.mark.parametrize(
    "code,expected",
    [
        ("110020", "equity"),
        ("050025", "equity"),
        ("008114", "equity"),
        ("510300", "equity"),
        ("510500", "equity"),
        ("510050", "equity"),
        ("159915", "equity"),
        ("159919", "equity"),
        ("519736", "equity"),
        ("217022", "bond"),
        ("003376", "bond"),
        ("000216", "gold"),
        ("518880", "gold"),
        ("000198", "money"),
        ("003474", "money"),
    ],
)
def test_known_types_short_circuit_with_expected_type(code: str, expected: str) -> None:
    """表内 15 条：类型正确，且 keywords 为空（证明走的是短路分支而非名称推断）。"""
    result = classify_fund(code=code, name="")
    assert result["type"] == expected, (
        f"代码 {code} 判为 {result['type']}，期望 {expected}"
    )
    assert result["keywords"] == [], (
        f"代码 {code} 未走 KNOWN_FUND_TYPES 短路（keywords={result['keywords']}）"
    )


def test_known_types_table_has_exactly_15_entries() -> None:
    """表规模守卫：新增/删除条目必须让人显式看到这条断言变红并确认过分类。"""
    assert len(KNOWN_FUND_TYPES) == 15, (
        f"KNOWN_FUND_TYPES 有 {len(KNOWN_FUND_TYPES)} 条，期望 15 条；"
        f"变更需同步更新本文件的 test_known_types_short_circuit_with_expected_type"
    )


def test_unknown_code_still_falls_through_to_name_inference() -> None:
    """反向守卫：不在表里的代码仍应走名称推断（确认短路没被误扩成"兜底全拦截"）。"""
    result = classify_fund(code="999999", name="某某灵活配置混合")
    assert result["type"] == "mixed"
    assert result["is_mixed"] is True
    assert "allocation" in result, "名称推断出的 mixed 必须带 allocation"

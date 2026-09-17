"""
monthly_rebalance_cron.py 黄金目标与推送文本的回归守卫。

本文件背景（FIX 2026-09-18，两条互相独立的缺陷）：

1) 黄金目标 10 vs 5
   `DEFAULT_TARGET` 原为 `{"stock":60,"bond":20,"cash":10,"gold":10}`，
   其中 gold=10 是一个**没有任何来源依据的孤立字面量**（原注释只写
   「默认 60/20/10/10 的稳健组合」）。而 domain 层两处权威源一致给 5：
     - domain/rule_engine/glide_path_rules.py:35  GOLD_PCT_DEFAULT = 0.05
       （_GLIDE_PATH_TABLE 每个年龄档 gold 均为 5）
     - domain/rule_engine/defaults.py  AllocationDefaults.MATRIX
       12 宫格每一格 gold 均为 5
   孤证不立，统一到 5（从 stock 扣 5：60 → 65，合计仍 100）。

2) 推送正文硬编码目标值
   原正文写死「目标：股60% · 基金20% · 现金10% · 黄金10%」，与
   DEFAULT_TARGET 是两份独立维护的同一个数字。更糟的是正常路径下
   `target = overview.get("target")` 拿到的是 portfolio_overview 的
   45/30/20/5，正文却仍在说 60/20/10/10 —— 这行文本在绝大多数情况下
   **本来就是错的**，只是没人发现。改为从 result["target"] 动态渲染。

断言纪律：全部为行为级 / 跨源一致性断言。
  - 「渲染出的文本里的目标数字 == result['target'] 的值」用一组**刻意不整
    齐**的目标值（12/34/21/9）来验证：硬编码 60/20/10/10 的话必红。
  - 黄金取值不写死 5，而是与 domain 层两处权威源对表，任一处改了这里先红。
"""

import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

from domain.rule_engine.defaults import AllocationDefaults  # noqa: E402
from domain.rule_engine import glide_path_rules as gp  # noqa: E402
from scripts import monthly_rebalance_cron as cron  # noqa: E402


def _result(target: dict, current: dict | None = None,
            deviations: dict | None = None, need_rebalance: bool = True) -> dict:
    """构造 analyze_user() 的返回体（正常路径的键名：equity，不是 stock）。"""
    return {
        "user": "LeiJiang",
        "available": True,
        "total": 100000,
        "current": current if current is not None else {"equity": 57.1, "bond": 17.1,
                                                        "cash": 11.4, "gold": 14.3},
        "target": target,
        "deviations": deviations if deviations is not None else {},
        "max_dev": 12.1,
        "need_rebalance": need_rebalance,
        "health_grade": "🟡 一般",
    }


# ============================================================
# 1. DEFAULT_TARGET：四档合计 100，黄金取 5
# ============================================================


def test_default_target_sums_to_100():
    assert sum(cron.DEFAULT_TARGET.values()) == 100, (
        f"DEFAULT_TARGET 合计 {sum(cron.DEFAULT_TARGET.values())}，应为 100：{cron.DEFAULT_TARGET}"
    )


def test_default_target_gold_is_5():
    assert cron.DEFAULT_TARGET["gold"] == 5, (
        f"DEFAULT_TARGET 黄金目标为 {cron.DEFAULT_TARGET['gold']}，应为 5"
    )


def test_default_target_gold_matches_glide_path_default():
    """黄金目标必须与 glide_path_rules.py:35 GOLD_PCT_DEFAULT 一致。"""
    expected = gp.GOLD_PCT_DEFAULT * 100
    assert cron.DEFAULT_TARGET["gold"] == expected, (
        f"DEFAULT_TARGET 黄金 {cron.DEFAULT_TARGET['gold']}%，"
        f"而 GOLD_PCT_DEFAULT = {gp.GOLD_PCT_DEFAULT}（即 {expected}%）"
    )


def test_domain_gold_targets_are_uniformly_5():
    """domain 层两处权威源的黄金目标必须恒为 5（跨源对表，任一处漂移这里先红）。"""
    glide_golds = {row[3] for row in gp._GLIDE_PATH_TABLE.values()}
    matrix_golds = {cell[3] for cell in AllocationDefaults.MATRIX.values()}

    assert glide_golds == {5}, f"glide path 各年龄档黄金目标不一致：{glide_golds}"
    assert matrix_golds == {5}, f"目标矩阵 12 宫格黄金目标不一致：{matrix_golds}"
    assert cron.DEFAULT_TARGET["gold"] in glide_golds | matrix_golds, (
        f"DEFAULT_TARGET 黄金 {cron.DEFAULT_TARGET['gold']}% 与 domain 层的 5% 对不上"
    )


# ============================================================
# 2. 推送正文的目标值必须来自 result["target"]，不能硬编码
# ============================================================


def test_push_text_target_equals_result_target():
    """用一组刻意不整齐的目标值：文本里的数字必须是 result['target'] 的值。"""
    target = {"equity": 12, "bond": 34, "cash": 21, "gold": 9}
    text = cron._render_message(_result(target=target))

    assert "股12%" in text, f"渲染目标股应为 12%，实际文本：\n{text}"
    assert "基金34%" in text, f"渲染目标基金应为 34%，实际文本：\n{text}"
    assert "现金21%" in text, f"渲染目标现金应为 21%，实际文本：\n{text}"
    assert "黄金9%" in text, f"渲染目标黄金应为 9%，实际文本：\n{text}"

    # 反向钉死：旧硬编码的 60/20/10/10 绝不能再出现
    assert "股60%" not in text, "正文仍在输出硬编码的「股60%」"
    assert "黄金10%" not in text, "正文仍在输出硬编码的「黄金10%」"


def test_push_text_target_tracks_changed_gold_target():
    """黄金目标一变，正文必须跟着变（证明是动态渲染而非写死）。"""
    text_a = cron._render_message(_result(target={"equity": 45, "bond": 30,
                                                  "cash": 20, "gold": 5}))
    text_b = cron._render_message(_result(target={"equity": 40, "bond": 30,
                                                  "cash": 20, "gold": 10}))
    assert "黄金5%" in text_a and "黄金10%" not in text_a
    assert "黄金10%" in text_b, "黄金目标改成 10 后正文没跟着变，说明还是硬编码"
    assert text_a != text_b


def test_push_text_target_accepts_stock_key_name():
    """兼容分支用的是 "stock" 键名，正文同样要认（_pct_of 的候选键）。"""
    text = cron._render_message(
        _result(target=dict(cron.DEFAULT_TARGET), current={"stock": 70, "bond": 20,
                                                           "cash": 5, "gold": 5})
    )
    assert "股65%" in text, f"DEFAULT_TARGET 股票目标 65 未渲染进正文：\n{text}"
    assert "黄金5%" in text, f"DEFAULT_TARGET 黄金目标 5 未渲染进正文：\n{text}"
    assert "当前：股70%" in text, f"current 用 stock 键时当前值未渲染：\n{text}"


def test_push_text_current_uses_equity_key_name():
    """正常路径 current 用 equity 键名（portfolio_overview 口径）。"""
    text = cron._render_message(
        _result(target={"equity": 45, "bond": 30, "cash": 20, "gold": 5})
    )
    assert "当前：股57.1%" in text, f"current 的 equity 键未渲染进正文：\n{text}"
    assert "黄金14.3%" in text, f"current 的 gold 键未渲染进正文：\n{text}"

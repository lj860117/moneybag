"""源码级回归测试：基金详情弹窗「决策辅助面板」的渲染闸门与追加逻辑。

背景（根因）：
    `pages/_components.js` 的 `window.showFundDetailModal` 里，决策辅助面板（走势预估 8 维面板、
    智能定投建议、持仓摘要等）曾由闸门 `if (d.holding_relation && d.advices) {` 控制。该闸门对任何人、
    任何时候都不成立：
      1) `d.advices` 只由 `GET /api/fund-holdings/detail/{code}` 产出，而弹窗只调
         `GET /api/fund/detail/{code}`（_fetchFundDetailPayload），两接口之间没有任何 merge，
         故 `d.advices` 恒为 undefined；
      2) `d.holding_relation` 对真实用户也为空（后端数据问题）。
    结果：后端已算好、载荷里确实带有的 `trend_direction/trend_score/trend_dimensions` 等 UI
    从不渲染。

同时有一个耦合缺陷：面板渲染后，通用详情用字面标题串匹配
`if(isMyHolding && body.innerHTML.includes('持仓决策辅助'))` 决定「追加 or 覆盖」。一旦面板标题行改为
条件输出，该匹配失效 → 走 `body.innerHTML = html` 把刚渲染的面板整个覆盖掉。

这些断言都是「结构级」的（匹配关键字/判据，不写死整行/空白），用于防止回退。
注意：源码级断言不能证明浏览器里真的渲染出来了，真机视觉复验另行进行。
"""

from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
COMPONENTS = BACKEND_DIR.parent / "pages" / "_components.js"


def _components_src() -> str:
    return COMPONENTS.read_text(encoding="utf-8")


def test_gate_no_longer_requires_holding_relation_and_advices():
    """闸门不得再是 `if (d.holding_relation && d.advices) {`。

    防的是：回退到「两个字段都得有」的永假闸门——它会把走势预估/定投等有真实数据的 UI 重新埋死。
    断言带尾随 `{`，以避免命中解释性注释里出现的同名字符串。
    """
    src = _components_src()
    assert "if (d.holding_relation && d.advices) {" not in src
    # 新闸门：只要「有任意可渲染内容」就渲染
    assert "if (hasDecisionPanel) {" in src


def test_trend_block_predicate_is_independent_of_holding():
    """走势预估子块的存在性判据必须独立于持仓（不能要求 holding_relation）。

    防的是：把走势面板重新挂在持仓闸门下，导致未持仓基金永远看不到走势预估。
    """
    src = _components_src()
    lines = src.splitlines()

    # hasTrend 必须仅由 trend_direction 派生，不得掺入 holding_relation
    trend_defs = [ln for ln in lines if "hasTrend" in ln and "trend_direction" in ln and "=" in ln]
    assert len(trend_defs) == 1, f"期望恰有一处 hasTrend 定义，实际: {trend_defs}"
    assert "holding_relation" not in trend_defs[0]

    # 走势子块的守卫必须是 hasTrend（与持仓无关）
    assert "if(hasTrend) {" in src
    # 防止回退成持仓门
    assert "if(hasHolding) {" not in src


def test_advices_length_access_is_guarded():
    """`d.advices.length` 的访问必须受守卫保护，不能裸访问。

    防的是：`d.advices` 为 undefined 时 `.length` 直接抛 TypeError，把整个弹窗打成
    「基金详情渲染失败」错误页。
    """
    src = _components_src()
    assert "if(d.advices.length)" not in src
    # 守卫基于 Array.isArray + length
    assert "Array.isArray(d.advices) && d.advices.length > 0" in src
    assert "if(hasAdvices) {" in src


def test_panel_append_uses_flag_not_fragile_title_match():
    """面板渲染后的「追加 or 覆盖」必须用标志位判断，脆弱字符串匹配必须消失。

    防的是：字面标题串匹配（对 body.innerHTML 做 includes 标题）在标题行改为条件输出后失效，
    进而 `body.innerHTML = html` 覆盖掉刚渲染好的决策面板。这正是本项目已吃过一次的教训
    （组合温度计死码）。
    """
    src = _components_src()
    assert "body.innerHTML.includes('持仓决策辅助')" not in src
    assert "let _panelRendered = false;" in src
    assert "_panelRendered = true;" in src
    assert "if(_panelRendered){" in src


def test_decision_title_only_when_holding_or_advices():
    """「🎯 持仓决策辅助」标题行只在 hasHolding || hasAdvices 时输出。

    防的是：给一只用户并未持有的基金顶一个「持仓决策辅助 / 持有观察」的误导标题。
    """
    src = _components_src()
    assert "if (hasHolding || hasAdvices) {" in src


def test_my_holding_number_fields_have_null_guards():
    """个人持仓摘要的数值字段（份额/成本均价）必须有 null 守卫。

    防的是：`my.shares`/`my.avg_cost` 缺值时 `undefined.toFixed(...)` 抛错打崩整个弹窗。
    """
    src = _components_src()
    assert "my.shares!=null?my.shares.toFixed(2)" in src
    assert "my.avg_cost!=null?'¥'+my.avg_cost.toFixed(4)" in src
    assert "if(hasMyHolding) {" in src


def test_is_my_holding_guard_still_declared():
    """`const isMyHolding = !!d.holding_relation;` 必须原样保留。

    防的是：误删该声明——纪律线子块（if(isMyHolding)）依赖它，且另有独立测试
    （test_regression_signal_and_cache.py::test_fund_detail_component_declares_is_my_holding_guard）
    钉死了这个字面串。
    """
    src = _components_src()
    assert "const isMyHolding = !!d.holding_relation;" in src
    assert "if(isMyHolding) {" in src

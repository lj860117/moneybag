"""源码级回归测试：基金详情弹窗「决策辅助面板」的渲染闸门、数据源与追加逻辑。

## 两层根因

**第一层（v9.9.40，闸门）**：`pages/_components.js` 的 `window.showFundDetailModal` 里，决策
辅助面板（走势预估 8 维 / 智能定投建议 / 持仓摘要等）曾由闸门
`if (d.holding_relation && d.advices) {` 控制，对任何人都不成立（`d.advices` 恒为 undefined、
`d.holding_relation` 对真实用户为空）→ 后端已算好的 UI 从不渲染。
另有一处耦合缺陷：面板渲染后用脆弱字面标题串匹配决定「追加 or 覆盖」，标题行条件化后会失效并
覆盖掉刚渲染的面板。

**第二层（v9.9.41，数据源）**：只放宽闸门**不足以**让面板全部活过来。面板要的
`advices` / `dca` / `action_direction` **只由** `GET /api/fund-holdings/detail/{code}`
（backend/api/holdings.py）产出，而弹窗只调 `GET /api/fund/detail/{code}`（_fetchFundDetailPayload），
该接口**历史上从不返回这三个字段**。所以 v9.9.40 只救出了持仓摘要 / 走势预估 8 维 / 纪律线，
「智能定投建议」面板与「建议列表」仍永不渲染，且 `${d.action_direction||'持有观察'}` 会
**无中生有**输出一个后端从未给出的判断。修法：新增 `_fetchFundDecisionPayload` 并行取决断面，
`_mergeDecisionPayload` 合并（my_holding/holding_relation 仅非 null 时覆盖），并删掉伪造兜底。

## 断言类型声明（不许把没验证的说成验证过）

- 本文件**全部**是**源码级结构断言**：只在源码文本里匹配结构/关键字/正则，**不执行 JS**，
  因此**无法证明浏览器里真的渲染出来了**。
- 也**没有行为断言**（无 JS 运行时、无 DOM）——「点开弹窗后走势/定投面板确实出现在页面上」
  这一层留给真机/浏览器视觉复验，本文件不声称验证过。
- 断言刻意匹配结构而非整行/空白，格式化不应让其变红。
"""

import re
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


# ==========================================================================
# v9.9.41 第二层根因：面板数据源接错接口。全部为**源码级结构断言**。
# ==========================================================================
def test_decision_payload_fetcher_exists_and_hits_holdings_endpoint():
    """必须存在 `_fetchFundDecisionPayload`，且 URL 打 `/fund-holdings/detail/`。

    防的是：回退到「只调 fund/detail」——该接口永不返回 advices/dca/action_direction，
    于是「智能定投建议」面板与「建议列表」永不渲染（v9.9.40 只救出了走势/持仓摘要/纪律线）。
    """
    src = _components_src()
    assert "function _fetchFundDecisionPayload" in src or "async function _fetchFundDecisionPayload" in src
    assert "/fund-holdings/detail/" in src


def test_decision_and_detail_fetches_are_parallel_in_promise_all():
    """决断面与 fund/detail 必须**并行**（同一个 `Promise.all`），而不是串行 await。

    防的是：改回串行取数——fund/detail 冷态本来就要 60s+，串行会让总延迟翻倍。
    断言方式：在 `Promise.all([` 之后的窗口里必须同时出现两个取数函数。
    """
    src = _components_src()
    assert "Promise.all([" in src, "未找到 Promise.all([ —— 决断面与详情可能被改成串行"
    idx = src.index("Promise.all([")
    window = src[idx : idx + 400]
    assert "_fetchFundDetailPayload" in window, "Promise.all 里没有 fund/detail 取数"
    assert "_fetchFundDecisionPayload" in window, "Promise.all 里没有决断面取数（可能被拆成串行）"


def test_merge_decision_payload_guards_null_holding_fields():
    """`_mergeDecisionPayload` 必须存在，且对 my_holding / holding_relation 有非 null 守卫。

    防的是：让决断面里未持仓基金的 `my_holding=null` 覆盖掉 fund/detail 已算好的好值。
    """
    src = _components_src()
    assert "function _mergeDecisionPayload" in src
    assert re.search(r"dec\.my_holding\s*!==?\s*null", src), "my_holding 合并缺少非 null 守卫"
    assert re.search(r"dec\.holding_relation\s*!==?\s*null", src), "holding_relation 合并缺少非 null 守卫"


def test_decision_fetch_has_catch_fallback_at_call_site():
    """决断面取数必须有 `.catch(...)` 兜底，不能把异常抛进渲染路径。

    防的是：决断面接口失败/超时（008655 冷态实测 17.8s）时，异常冒进渲染 try →
    整个弹窗被打成「基金详情渲染失败」，而 fund/detail 其实已经成功。
    """
    src = _components_src()
    assert re.search(
        r"_fetchFundDecisionPayload\s*\(\s*code\s*,\s*getProfileId\(\)\s*\)\s*\.catch\(", src
    ), "决断面取数调用点没有 .catch(...) 兜底"


def test_no_fabricated_action_direction_fallback():
    """不得存在 `|| '持有观察'` 这种伪造兜底。

    防的是：后端从未给出 action_direction 时，前端无中生有一个「持有观察」判断
    （旧写法 `${d.action_direction||'持有观察'}` 正是如此）。
    """
    src = _components_src()
    assert re.search(r"\|\|\s*'持有观察'", src) is None, (
        "action_direction 又出现了伪造兜底 ||'持有观察'"
    )


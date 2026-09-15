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

**第三层（v9.9.42，测试自身的假绿 + 漏合并）**：独立 QA 用 Node+vm 真渲染对照，证伪了本文件
3 条假绿测试：(1) 只断言字面串 `'/fund-holdings/detail/'` 存在于源码——但该串在三处注释里也有，
把真 URL 改回 `/fund/detail/` 照样绿；(2) 只断言 `if(_panelRendered){` 等字面存在——把 `+=`
削弱回 `=`（「面板刚渲染就被覆盖」缺陷复活）照样绿；(3) 只查 `Promise.all` 后 400 字符窗口出现
两个函数名——在 `Promise.all` 前插一行 `await` 详情（伪并行/串行）照样绿。修法：新增**去注释**
源码 `_components_src_no_comments()`，并对「await 序列」「确切分支语句」做断言。另修一处漏合并：
`_mergeDecisionPayload` 未合并 nav_pct_label / nav_percentile / timing_label（只有决断面产出，
fund/detail 实测为 None，而面板 tags 消费它们）。

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


def _strip_js_comments(src: str) -> str:
    """剥掉 JS 的 `//` 行注释与 `/* */` 块注释，**保留字符串字面量**（含 `https://` 之类）。

    手写状态机：跟踪 `'` `"` 反引号三种字符串与反斜杠转义，字符串内一律不判注释——
    否则 URL 里的 `//` 会被误当行注释、把后面整行（含真实代码）吃掉。纯函数，便于校验。
    """
    out: list[str] = []
    i, n = 0, len(src)
    state: str | None = None
    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if state is None:
            if ch in ("'", '"', "`"):
                state = ch
                out.append(ch)
                i += 1
                continue
            if ch == "/" and nxt == "/":
                nl = src.find("\n", i)
                if nl == -1:
                    break
                i = nl
                continue
            if ch == "/" and nxt == "*":
                close = src.find("*/", i + 2)
                if close == -1:
                    break
                i = close + 2
                continue
            out.append(ch)
            i += 1
        else:
            out.append(ch)
            if ch == "\\":
                if i + 1 < n:
                    out.append(src[i + 1])
                    i += 2
                    continue
            elif ch == state:
                state = None
            i += 1
    return "".join(out)


def _components_src_no_comments() -> str:
    """去注释后的 _components.js —— 只认真实代码，不认注释里复现的字面串。"""
    return _strip_js_comments(_components_src())



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
    """面板渲染后的「追加 or 覆盖」必须用标志位判断，且两分支语句**确切**。

    防的是两件事：
      1) 字面标题串匹配失效后 `body.innerHTML = html` 覆盖刚渲染的面板；
      2) 把追加 `+=` 削弱回 `=`（「面板刚渲染就被覆盖」缺陷原样复活）。
    旧版只断言 `if(_panelRendered){` / `_panelRendered = true;` 等**字面存在**——把 `+=` 改成 `=`
    也照样全绿（QA 注入 I7 实证），故此处改为钉住两个分支的确切语句。

    断言性质：**源码级结构断言**（正则匹配语句结构，不执行 JS）。
    """
    src = _components_src()
    assert "body.innerHTML.includes('持仓决策辅助')" not in src
    assert "let _panelRendered = false;" in src
    assert "_panelRendered = true;" in src
    # if 分支必须是「追加」（+=）——否则面板刚渲染就被覆盖
    assert re.search(
        r"if\s*\(\s*_panelRendered\s*\)\s*\{\s*body\.innerHTML\s*\+=\s*html\s*;", src
    ), "标志位为真时必须是追加（body.innerHTML += html），不能被削弱成覆盖"
    # 覆盖写法只能出现在 else 分支，且只此一处
    assert re.search(r"else\s*\{\s*body\.innerHTML\s*=\s*html\s*;", src), (
        "覆盖写法不在 else 分支，或两分支结构被改坏"
    )
    assert src.count("body.innerHTML = html;") == 1, "覆盖写法出现了不止一次"


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
    """决断面取数必须存在，且 URL 真打 `/fund-holdings/detail/`（**去注释后**断言）。

    防的是：把 URL 改回 `/fund/detail/`（= 整个数据源修复回退，定投/建议列表复活为永不渲染）。
    旧版只断言 `'/fund-holdings/detail/' in src`——该字面串在三处**注释**里也有，所以改回 URL
    照样全绿（QA 注入 I5 实证：pytest 全绿、只有 Node harness 红）。故本条改用**去注释**源码：
    注释里的同名字面串被剥掉，只有真实 URL 那行算数。

    断言性质：**源码级结构断言**（去注释后匹配字面串，不执行 JS）。
    """
    src_nc = _components_src_no_comments()
    assert (
        "function _fetchFundDecisionPayload" in src_nc
        or "async function _fetchFundDecisionPayload" in src_nc
    ), "决断面取数函数不存在"
    assert "/fund-holdings/detail/" in src_nc, (
        "去注释后的源码里没有 /fund-holdings/detail/ —— URL 可能被改回 /fund/detail/"
    )


def test_decision_and_detail_fetches_are_parallel_in_promise_all():
    """决断面与 fund/detail 必须**真并行**：都在 `await Promise.all([...])` 里，且**都不被单独 await**。

    防的是：伪并行——在 `Promise.all` 之前/之外插一行 `await` 取数（串行执行、延迟翻倍）。
    旧版只查 `Promise.all([` 后 400 字符窗口里出现两个函数名：在 `Promise.all` **之前**插
    `await` 详情不碰任何被断言的字符串，照样全绿（QA 注入 I4b 实证：pytest 全绿、Node 测出 605ms）。
    故本条改为对「await 序列」断言（去注释源码）。

    断言性质：**源码级结构断言**（匹配 await 用法，不执行 JS；真正的时序需行为测试/真机）。
    """
    src_nc = _components_src_no_comments()
    assert re.search(r"await\s+Promise\.all\s*\(\s*\[", src_nc), "未找到 `await Promise.all([`"
    # 只在 showFundDetailModal 内检查：_prefetchFundDetail（:471）有一处**合法**的
    # `await _fetchFundDetailPayload`，那是预取辅助函数、不是本弹窗的取数路径，不能误伤。
    start = src_nc.index("window.showFundDetailModal = async function")
    modal_body = src_nc[start:]
    # 弹窗内两个取数都不允许被单独 await —— 只能作为 Promise.all 的入参（内联调用，不带 await）
    assert re.search(r"await\s+_fetchFundDetailPayload", modal_body) is None, (
        "弹窗内出现单独 `await _fetchFundDetailPayload` —— 伪并行/串行"
    )
    assert re.search(r"await\s+_fetchFundDecisionPayload", modal_body) is None, (
        "弹窗内出现单独 `await _fetchFundDecisionPayload` —— 伪并行/串行"
    )


def test_merge_decision_payload_guards_null_holding_fields():
    """`_mergeDecisionPayload` 必须存在，且对 my_holding / holding_relation 有非 null 守卫。

    防的是：让决断面里未持仓基金的 `my_holding=null` 覆盖掉 fund/detail 已算好的好值。
    """
    src = _components_src()
    assert "function _mergeDecisionPayload" in src
    assert re.search(r"dec\.my_holding\s*!==?\s*null", src), "my_holding 合并缺少非 null 守卫"
    assert re.search(r"dec\.holding_relation\s*!==?\s*null", src), "holding_relation 合并缺少非 null 守卫"


def test_merge_decision_payload_includes_diagnostic_label_fields():
    """`_mergeDecisionPayload` 必须合并 nav_pct_label / nav_percentile / timing_label（非 null 守卫）。

    防的是：这三个字段只有决断面产出（fund/detail 实测为 None），而面板 tags 确实消费
    `d.nav_pct_label` / `d.timing_label`（nav_percentile 决定标签配色）——不合并则
    「净值百分位」「择时」标签永不渲染（后端早算好、数据就在手上）。

    断言性质：**源码级结构断言**（只核对合并函数里的守卫键，不执行 JS）。
    """
    src = _components_src()
    assert "function _mergeDecisionPayload" in src
    assert re.search(r"dec\.nav_pct_label\s*!==?\s*null", src), "nav_pct_label 未合并或缺非 null 守卫"
    assert re.search(r"dec\.nav_percentile\s*!==?\s*null", src), "nav_percentile 未合并或缺非 null 守卫"
    assert re.search(r"dec\.timing_label\s*!==?\s*null", src), "timing_label 未合并或缺非 null 守卫"


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


def test_decision_title_text_branches_on_has_holding():
    """标题文案必须按 hasHolding 分叉：持仓→「🎯 持仓决策辅助」，非持仓→「🎯 决策参考」。

    防的是：`advices` 现在对**所有**基金都有值（含未持仓），而标题输出时机仍为
    `hasHolding || hasAdvices`，若标题无条件写「持仓决策辅助」，未持仓基金的弹窗也会
    顶一个「持仓决策辅助」的误导标题 —— 正是 :481 注释原本要避免的形态。
    """
    src = _components_src()
    assert "🎯 持仓决策辅助" in src
    assert "🎯 决策参考" in src
    assert re.search(
        r"hasHolding\s*\?\s*'🎯 持仓决策辅助'\s*:\s*'🎯 决策参考'", src
    ), "标题文案没有按 hasHolding 分叉（期望 hasHolding ? '🎯 持仓决策辅助' : '🎯 决策参考'）"


def test_holding_decision_title_not_emitted_unconditionally():
    """「🎯 持仓决策辅助」不得被无条件直接写进标题 span（未持仓基金不得显示该文案）。

    防的是：标题改回无条件输出，令上一条的分叉失效——未持仓基金也会显示「持仓决策辅助」。
    """
    src = _components_src()
    assert "🎯 持仓决策辅助</span>" not in src, (
        "标题仍被无条件直接渲染为「持仓决策辅助」——未持仓基金会看到误导标题"
    )



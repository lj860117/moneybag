"""基金详情弹窗「决策辅助面板」回归测试：闸门 / 数据源 / 追加逻辑 / 合并守卫。

## 四层根因（按发现顺序）

**第一层（v9.9.40，闸门）**：`pages/_components.js` 的 `window.showFundDetailModal` 里，决策
辅助面板曾由闸门 `if (d.holding_relation && d.advices) {` 控制，对任何人都不成立 → 后端已算好的
UI 从不渲染；另有一处「面板刚渲染就被通用详情覆盖」的脆弱接线。

**第二层（v9.9.41，数据源）**：`advices`/`dca`/`action_direction` 只由
`GET /api/fund-holdings/detail/{code}` 产出，弹窗却只调 `/fund/detail/{code}`。修法：并行取决断面
+ `_mergeDecisionPayload` 合并 + 删 `${d.action_direction||'持有观察'}` 伪造兜底。

**第三层（v9.9.42，测试假绿 + 漏合并）**：3 条测试只断言字面串/窗口，被 QA 注入绕过；另
`_mergeDecisionPayload` 漏合并 `nav_pct_label`/`nav_percentile`/`timing_label`。

**第四层（v9.9.43，测试基础设施）**：QA 用真渲染又凿出 —— ① URL 断言只问「字面串出现过吗」，
在别处加一句多余字面串即可绕过（I9）；② 并行断言只认 `await _fetch*` 形态，`const x=…(); await x;`
真串行却全绿（I8）；③ `_components_src_no_comments()` 不识别正则字面量（正则里的双斜线被当注释
→ 吃掉后续代码，方向是**假红**）、块注释吞换行导致行号错位。本轮把这些换成**行为级断言**。

## 断言类型纪律（不许把没验证的说成验证过）

- **源码级结构断言**：只在源码文本里匹配结构/正则，**不执行 JS**——读作「代码文本如此」，不保证
  运行时行为。docstring 中标注「源码级结构断言」。
- **去注释后的源码级断言**：在 `_components_src_no_comments()`（剥 `//`、`/* */`，保留字符串
  /正则字面量）上匹配——防止注释里的同名字面串制造假绿。
- **行为级断言**：用 `node` + `vm` **真加载并执行** `pages/_components.js`（带最小 DOM/fetch 桩），
  对真实运行结果断言。docstring 中标注「行为级断言」。**环境无 node 时这些用例 `pytest.skip`**
  （不静默变绿）——见 `_run_node`。
- 行为级断言能挡住「逻辑被改坏但字符串还在」；源码级断言只能做低成本护栏。两者互补，不互相顶替。
- **真机/浏览器视觉复验仍未覆盖**：本文件不跑真浏览器，页面是否真的把面板画出来，留给真机复验。
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
COMPONENTS = BACKEND_DIR.parent / "pages" / "_components.js"

_NODE = shutil.which("node")
_API_BASE = "http://api.local/api"


def _components_src() -> str:
    return COMPONENTS.read_text(encoding="utf-8")


# ==========================================================================
# 去注释源码（供「不认注释里同名字面串」的断言使用）
# ==========================================================================
_REGEX_PREV_CHARS = set("(,=:[!&|?{};+-*%~^<>")
_REGEX_PREV_WORDS = (
    "return", "typeof", "instanceof", "in", "of", "new", "delete",
    "void", "do", "else", "case", "yield", "await",
)


def _regex_can_start(out: list[str]) -> bool:
    """判断一处 `/` 是否可能是正则字面量的起点（启发式，仅用于避免把正则里的 `//` 当注释）。"""
    k = len(out) - 1
    while k >= 0 and out[k].isspace():
        k -= 1
    if k < 0:
        return True
    if out[k] in _REGEX_PREV_CHARS:
        return True
    m = k
    while m >= 0 and (out[m].isalnum() or out[m] in "_$"):
        m -= 1
    word = "".join(out[m + 1 : k + 1])
    return word in _REGEX_PREV_WORDS


def _strip_js_comments(src: str) -> str:
    """剥掉 JS 的 `//` 行注释与 `/* */` 块注释，**保留字符串与正则字面量**。

    - 字符串（`'` `"` 反引号）内的 `//`、`/*` 一律不当注释（否则 URL 里 `https://` 会吃掉整行）。
    - 模板串 `${ ... }` 内的合法 JS 注释也会被剥（进入替换表达式后回到代码模式）。
    - 正则字面量（`/` 处于表达式起点）内的双斜线不当行注释——否则一个匹配 URL 的正则
      会吃掉后续代码。
    - 块注释展开为等量换行，使**去注释前后行数一致**（`splitlines()` 长度相等）。

    已知边界：正则起点的判断是启发式（依据前一个非空白字符/关键字），极端写法可能误判；当前
    `_components.js` 的正则字面量数量为 0，本函数主要在防御「将来引入正则后假红」。
    """
    out: list[str] = []
    i, n = 0, len(src)
    mode = "code"           # 'code' | 'str'
    quote = ""              # mode == 'str' 时的引号字符
    tpl_depth: list[int] = []  # 活动中的 ${ } 替换表达式的花括号深度栈
    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if mode == "str":
            out.append(ch)
            if quote == "`":
                if ch == "\\" and i + 1 < n:
                    out.append(src[i + 1])
                    i += 2
                    continue
                if ch == "`":
                    quote = ""
                    mode = "code"
                    i += 1
                    continue
                if ch == "$" and nxt == "{":
                    out.append("{")
                    i += 2
                    mode = "code"
                    tpl_depth.append(0)
                    continue
                i += 1
                continue
            if ch == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
                mode = "code"
            i += 1
            continue

        # ---- code 模式 ----
        if ch == "/" and nxt == "/":
            nl = src.find("\n", i)
            if nl == -1:
                break
            i = nl  # 保留行尾换行（下一轮 append）
            continue
        if ch == "/" and nxt == "*":
            close = src.find("*/", i + 2)
            if close == -1:
                break
            for _ in range(src.count("\n", i, close + 2)):
                out.append("\n")
            i = close + 2
            continue
        if ch in ("'", '"', "`"):
            mode = "str"
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "{":
            if tpl_depth:
                tpl_depth[-1] += 1
            out.append(ch)
            i += 1
            continue
        if ch == "}":
            if tpl_depth and tpl_depth[-1] == 0:
                out.append(ch)
                tpl_depth.pop()
                mode = "str"
                quote = "`"
                i += 1
                continue
            if tpl_depth:
                tpl_depth[-1] -= 1
            out.append(ch)
            i += 1
            continue
        if ch == "/" and _regex_can_start(out):
            j = i + 1
            in_class = False
            closed = False
            while j < n:
                c = src[j]
                if c == "\\":
                    j += 2
                    continue
                if c == "\n":
                    break
                if c == "[":
                    in_class = True
                elif c == "]":
                    in_class = False
                elif c == "/" and not in_class:
                    closed = True
                    break
                j += 1
            if closed:
                k = j + 1
                while k < n and src[k].isalpha():
                    k += 1
                for c in src[i:k]:
                    out.append(c)
                i = k
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _components_src_no_comments() -> str:
    """去注释后的 _components.js —— 只认真实代码，不认注释里复现的字面串。"""
    return _strip_js_comments(_components_src())


# ==========================================================================
# 作用域界定：只取 window.showFundDetailModal 的函数体
# ==========================================================================
def _extract_brace_block(src: str, open_brace_idx: int) -> str:
    """从 `{` 起做花括号配平，返回包含该块的子串。忽略字符串/模板/注释里的花括号。"""
    depth = 0
    i = open_brace_idx
    n = len(src)
    state = None
    while i < n:
        ch = src[i]
        if state:
            if ch == "\\" and state != "`":
                i += 2
                continue
            if ch == state:
                state = None
            i += 1
            continue
        nxt = src[i + 1] if i + 1 < n else ""
        if ch in ("'", '"', "`"):
            state = ch
            i += 1
            continue
        if ch == "/" and nxt == "/":
            nl = src.find("\n", i)
            i = n if nl == -1 else nl
            continue
        if ch == "/" and nxt == "*":
            close = src.find("*/", i + 2)
            i = n if close == -1 else close + 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[open_brace_idx : i + 1]
        i += 1
    return src[open_brace_idx:]


def _show_fund_detail_modal_body() -> str:
    """只返回 `window.showFundDetailModal` 的函数体（右界=函数结束，**不**延伸到文件末尾）。"""
    src = _components_src()
    marker = "window.showFundDetailModal = async function"
    start = src.index(marker)
    brace = src.index("{", start)
    return _extract_brace_block(src, brace)


# ==========================================================================
# Node 行为级执行
# ==========================================================================
_DEFAULT_DOC = (
    "const __document={createElement:()=>({style:{},classList:{add(){},remove(){}},"
    "set onclick(f){},set innerHTML(v){}}),body:{appendChild(){}},"
    "getElementById:()=>null,querySelector:()=>null};"
)
# 记录 body.innerHTML 的每次写入（用于「追加而非覆盖」断言）
_MODAL_DOC = (
    "let __cur='';const __writes=[];"
    "const __bodyEl={get innerHTML(){return __cur;},set innerHTML(v){__cur=v;__writes.push(v);}};"
    "const __document={createElement:()=>({style:{},classList:{add(){},remove(){}},"
    "set onclick(f){},set innerHTML(v){}}),body:{appendChild(){}},"
    "getElementById:()=>__bodyEl,querySelector:()=>null};"
)


def _run_node(script: str) -> str:
    if not _NODE:
        pytest.skip("环境无 node，跳过 JS 行为级断言")
    r = subprocess.run([_NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"node 执行失败:\n{r.stdout}\n{r.stderr}"
    return r.stdout


def _js_harness(body: str, fetch_js: str, doc_js: str = _DEFAULT_DOC) -> str:
    """拼一段 Node 脚本：vm 加载 pages/_components.js（最小 stub），再执行 body。

    body 内应 `console.log(JSON.stringify(...))` 输出结果（Python 取最后一行解析）。
    """
    return (
        "const fs=require('fs');const vm=require('vm');"
        "const SRC=fs.readFileSync(" + json.dumps(str(COMPONENTS)) + ",'utf8');"
        "const __sleep=(ms)=>new Promise(r=>setTimeout(r,ms));"
        "const __fetchCalls=[];"
        "const __fetchImpl=" + fetch_js + ";"
        + doc_js
        + "const __sandbox={"
        "API_BASE:" + json.dumps(_API_BASE) + ","
        "getProfileId:()=>'LeiJiang',getUserId:()=>'LeiJiang',"
        "AbortSignal:{timeout:(ms)=>({ms})},"
        "fetch:(url,opts)=>__fetchImpl(url,opts),"
        "console,setTimeout,clearTimeout,"
        "document:__document"
        "};"
        "__sandbox.window=__sandbox;__sandbox.globalThis=__sandbox;"
        "vm.createContext(__sandbox);"
        "vm.runInContext(SRC,__sandbox,{filename:'_components.js'});"
        "(async()=>{" + body + "})().catch(e=>{console.error(e&&(e.stack||e.message||e));process.exit(3);});"
    )


def _run_js(body: str, fetch_js: str, doc_js: str = _DEFAULT_DOC):
    out = _run_node(_js_harness(body, fetch_js, doc_js))
    return json.loads(out.strip().splitlines()[-1])


# 三个典型生产载荷（取自 QA 抓取的线上真实形状）
_DETAIL_HELD = (
    "{name:'测试基金',holding_relation:'🔵 已持仓',trend_direction:'flat',trend_score:5,"
    "trend_confidence:60,trend_dimensions:{'估值':{score:1,max:2}},"
    "my_holding:{shares:28.5,avg_cost:3.48},pnl_pct:2.03,"
    "nav_pct_label:null,nav_percentile:null,timing_label:null,"
    "industry_tag:'🤖 AI/科技',returns:{}}"
)
_DETAIL_ABSENT = (
    "{name:'未持仓基金',trend_direction:'flat',trend_score:1,trend_dimensions:{},"
    "nav_pct_label:null,nav_percentile:null,timing_label:null,returns:{}}"
)
_DECISION = (
    "{advices:[{type:'caution',icon:'🟡',text:'百分位83%偏高'}],"
    "dca:{multiplier:0.7,label:'📉 0.7x 减量',advice:'高位观望'},"
    "action_direction:'持有观察',my_holding:null,"
    "nav_pct_label:'历史高位 83% 🔴',nav_percentile:83,timing_label:'⚪ 正常'}"
)


# ==========================================================================
# 一、源码级结构断言（低成本护栏）
# ==========================================================================
def test_gate_no_longer_requires_holding_relation_and_advices():
    """闸门不得再是 `if (d.holding_relation && d.advices) {`。（源码级结构断言）

    防的是：回退到「两个字段都得有」的永假闸门。断言带尾随 `{`，避免命中解释性注释。
    """
    src = _components_src()
    assert "if (d.holding_relation && d.advices) {" not in src
    assert "if (hasDecisionPanel) {" in src


def test_trend_block_predicate_is_independent_of_holding():
    """走势预估子块的存在性判据必须独立于持仓。（源码级结构断言）"""
    src = _components_src()
    lines = src.splitlines()
    trend_defs = [ln for ln in lines if "hasTrend" in ln and "trend_direction" in ln and "=" in ln]
    assert len(trend_defs) == 1, f"期望恰有一处 hasTrend 定义，实际: {trend_defs}"
    assert "holding_relation" not in trend_defs[0]
    assert "if(hasTrend) {" in src
    assert "if(hasHolding) {" not in src


def test_advices_length_access_is_guarded():
    """`d.advices.length` 的访问必须受守卫保护。（源码级结构断言）"""
    src = _components_src()
    assert "if(d.advices.length)" not in src
    assert "Array.isArray(d.advices) && d.advices.length > 0" in src
    assert "if(hasAdvices) {" in src


def test_panel_append_uses_flag_not_fragile_title_match():
    """「追加 or 覆盖」必须用标志位判断，且两分支语句**确切**。（源码级结构断言）

    防：脆弱标题串匹配失效后覆盖面板；以及把 `+=` 削弱回 `=`。
    （运行时行为另由 test_behavior_panel_appended_not_overwritten 覆盖。）
    """
    src = _components_src()
    assert "body.innerHTML.includes('持仓决策辅助')" not in src
    assert "let _panelRendered = false;" in src
    assert "_panelRendered = true;" in src
    assert re.search(
        r"if\s*\(\s*_panelRendered\s*\)\s*\{\s*body\.innerHTML\s*\+=\s*html\s*;", src
    ), "标志位为真时必须是追加（body.innerHTML += html）"
    assert re.search(r"else\s*\{\s*body\.innerHTML\s*=\s*html\s*;", src), "覆盖写法不在 else 分支"
    assert src.count("body.innerHTML = html;") == 1, "覆盖写法出现了不止一次"


def test_decision_title_only_when_holding_or_advices():
    """「🎯 …」标题行只在 hasHolding || hasAdvices 时输出。（源码级结构断言）"""
    src = _components_src()
    assert "if (hasHolding || hasAdvices) {" in src


def test_my_holding_number_fields_have_null_guards():
    """个人持仓摘要的数值字段必须有 null 守卫。（源码级结构断言）"""
    src = _components_src()
    assert "my.shares!=null?my.shares.toFixed(2)" in src
    assert "my.avg_cost!=null?'¥'+my.avg_cost.toFixed(4)" in src
    assert "if(hasMyHolding) {" in src


def test_is_my_holding_guard_still_declared():
    """`const isMyHolding = !!d.holding_relation;` 必须原样保留。（源码级结构断言）"""
    src = _components_src()
    assert "const isMyHolding = !!d.holding_relation;" in src
    assert "if(isMyHolding) {" in src


def test_decision_payload_fetcher_url_is_anchored_to_assignment():
    """决断面 URL 必须**锚定到赋值语句**，而不是「全局字符出现过」。（源码级结构断言）

    防：I9 —— 把 `decisionUrl` 改回 `/fund/detail/`、却在别处加一句多余字面串
    `/fund-holdings/detail/` 来骗过 `in src`。（运行时行为另由
    test_behavior_decision_fetcher_url_and_single_request 覆盖。）
    """
    src = _components_src()
    assert re.search(r"function\s+_fetchFundDecisionPayload\s*\(", src)
    m = re.search(r"decisionUrl\s*=\s*([^;]*);", src)
    assert m, "找不到 decisionUrl 的赋值语句"
    rhs = m.group(1)
    # 锚定到**赋值语句**（不再用全局 `in src` 的启发式）；但接受等价拼接写法
    # （`'/fund-holdings' + '/detail/'`），故不写死整串字面量 —— 否则行为等价的改写会假红。
    assert "fund-holdings" in rhs and "detail" in rhs, (
        f"decisionUrl 赋值语句不再指向 fund-holdings/detail：{rhs!r}"
    )


def test_no_standalone_await_inside_modal():
    """弹窗函数体内，除 `await Promise.all([` 外**不得**有其它 await。（源码级结构断言）

    防：I8 —— `const x=_fetchFundDetailPayload(...); await x;` 这类伪并行（真串行）。
    作用域仅限 `window.showFundDetailModal` 函数体（`_prefetchFundDetail:471` 那处**合法**
    的 `await _fetchFundDetailPayload` 在其外，不误伤）。运行时行为另由
    test_behavior_detail_and_decision_fetch_are_parallel 覆盖。
    """
    region = _show_fund_detail_modal_body()
    assert re.search(r"\bawait\s+Promise\.all\s*\(\s*\[", region), "未找到 `await Promise.all([`"
    stripped = re.sub(r"\bawait\s+Promise\.all\s*\(\s*\[", "", region)
    assert not re.search(r"\bawait\b", stripped), (
        "弹窗内出现 Promise.all 之外的 await —— 伪并行/串行"
    )


def test_modal_region_is_scoped():
    """`_show_fund_detail_modal_body()` 的右界必须收窄到函数结束。（源码级结构断言）

    防：旧写法 `src_nc[start:]` 取到文件末尾——将来后面的函数出现 `await _fetch*` 会假红。
    这里用「不得包含其后的 window.loadFundPortfolio」证明边界真的收窄了。
    """
    region = _show_fund_detail_modal_body()
    assert "body.innerHTML" in region
    assert "_prefetchFundDetail" not in region
    assert "window.loadFundPortfolio" not in region, "模态框作用域延伸到文件末尾了"


def test_merge_decision_payload_guards_null_holding_fields():
    """合并守卫（源码级结构断言）：my_holding/holding_relation 有非 null 守卫。"""
    src = _components_src()
    assert "function _mergeDecisionPayload" in src
    assert re.search(r"dec\.my_holding\s*!==?\s*null", src)
    assert re.search(r"dec\.holding_relation\s*!==?\s*null", src)


def test_merge_decision_payload_includes_diagnostic_label_fields():
    """合并守卫（源码级结构断言）：nav_pct_label/nav_percentile/timing_label 已合并。"""
    src = _components_src()
    assert "function _mergeDecisionPayload" in src
    assert re.search(r"dec\.nav_pct_label\s*!==?\s*null", src)
    assert re.search(r"dec\.nav_percentile\s*!==?\s*null", src)
    assert re.search(r"dec\.timing_label\s*!==?\s*null", src)


def test_decision_fetch_has_catch_fallback_at_call_site():
    """决断面取数调用点必须有 `.catch(...)` 兜底。（源码级结构断言）"""
    src = _components_src()
    assert re.search(
        r"_fetchFundDecisionPayload\s*\(\s*code\s*,\s*getProfileId\(\)\s*\)\s*\.catch\(", src
    )


def test_no_fabricated_action_direction_fallback():
    """不得存在 `|| '持有观察'` 伪造兜底。（源码级结构断言）"""
    src = _components_src()
    assert re.search(r"\|\|\s*'持有观察'", src) is None


def test_decision_title_text_branches_on_has_holding():
    """标题文案按 hasHolding 分叉。（源码级结构断言）"""
    src = _components_src()
    assert "🎯 持仓决策辅助" in src
    assert "🎯 决策参考" in src
    assert re.search(r"hasHolding\s*\?\s*'🎯 持仓决策辅助'\s*:\s*'🎯 决策参考'", src)


def test_holding_decision_title_not_emitted_unconditionally():
    """「🎯 持仓决策辅助」不得被无条件直接写进标题 span。（源码级结构断言）"""
    src = _components_src()
    assert "🎯 持仓决策辅助</span>" not in src


# ==========================================================================
# 二、去注释器自测（纯 Python，防「将来引入正则后假红」）
# ==========================================================================
def test_strip_js_comments_regex_literal_keeps_following_code():
    """正则字面量里的 `//` 不得被当行注释（否则吃掉后续代码 → 假红）。

    当前 _components.js 正则数量为 0，这是防将来的护栏。
    """
    src = "const r = /https?:\\/\\//; var keep=1;"
    out = _strip_js_comments(src)
    assert "var keep=1;" in out, "正则字面量后的代码被吃掉了"
    assert "/https?:\\/\\//" in out, "正则字面量本体应保留"


def test_strip_js_comments_preserves_strings_with_slash_and_block_marker():
    """字符串里的 `//` 与 `/*` 不得被当注释。"""
    src = "const a='http://x'; const b='y/*z*/w'; var keep=2;"
    out = _strip_js_comments(src)
    assert "http://x" in out
    assert "y/*z*/w" in out
    assert "var keep=2;" in out


def test_strip_js_comments_handles_comment_inside_template_substitution():
    """模板串 `${ }` 内的合法 JS 注释应被剥，且模板本体不被破坏。"""
    src = "const t = `a${1 // c\n + 2}b`; var keep=3;"
    out = _strip_js_comments(src)
    assert "// c" not in out, "模板替换表达式内的行注释未剥"
    assert "var keep=3;" in out, "模板串后的代码被吃掉"
    assert "`a${1" in out and "}b`" in out, "模板本体被破坏"


def test_strip_js_comments_multiline_block_and_line_alignment():
    """块注释跨多行：去注释后行数须与原文一致（行号对齐），且后续代码保留。"""
    src = "x=1; /* l1\nl2\nl3 */ y=2;\n// whole line\nz=3;"
    out = _strip_js_comments(src)
    assert "y=2;" in out and "z=3;" in out
    assert len(src.splitlines()) == len(out.splitlines())


def test_strip_js_comments_real_file_line_alignment():
    """真实 _components.js：去注释前后 splitlines 长度相等。"""
    before = _components_src()
    after = _components_src_no_comments()
    assert len(before.splitlines()) == len(after.splitlines())


def test_no_comments_source_actually_removes_comments():
    """去注释源码确实剥掉了注释。

    不依赖 URL 的具体写法（写成拼接 `'/fund-holdings'+'/detail/'` 时不假红），只比较 `//`
    出现次数：注释被剥后应减少（字符串里的 `https://` 仍保留，故只要求严格减少）。
    """
    src = _components_src()
    src_nc = _components_src_no_comments()
    assert src_nc.count("//") < src.count("//"), "去注释后 // 数量未减少，注释未被剥"


# ==========================================================================
# 三、行为级断言（node + vm 真加载执行；无 node 时 skip）
# ==========================================================================
def test_behavior_decision_fetcher_url_and_single_request():
    """行为级断言：真调 `_fetchFundDecisionPayload`，抓 fetch 实参 URL；两次调用只发一次请求。"""
    res = _run_js(
        "await __sandbox._fetchFundDecisionPayload('006555','LeiJiang');"
        "await __sandbox._fetchFundDecisionPayload('006555','LeiJiang');"
        "console.log(JSON.stringify({calls:__fetchCalls}));",
        "(url,opts)=>{__fetchCalls.push(url);return Promise.resolve({ok:true,json:async()=>({})});}",
    )
    expected = f"{_API_BASE}/fund-holdings/detail/006555?userId=LeiJiang"
    assert res["calls"] == [expected], f"fetch 实际收到 {res['calls']}"


def test_behavior_detail_and_decision_fetch_are_parallel():
    """行为级断言：详情 600ms + 决断面 200ms，总耗时须≈max（并行），而非 sum（串行）。

    判定：450ms <= 总耗时 < 750ms。并行≈600、串行≈800、未取数≈0。
    """
    res = _run_js(
        "const t0=Date.now();"
        "await __sandbox.showFundDetailModal('006555','F');"
        "console.log(JSON.stringify({ms:Date.now()-t0}));",
        "(url,opts)=>{"
        "if(url.indexOf('/fund-holdings/detail/')>=0){"
        "return __sleep(200).then(()=>({ok:true,json:async()=>({advices:[{type:'buy',icon:'🟢',text:'x'}]})}));}"
        "return __sleep(600).then(()=>({ok:true,json:async()=>({name:'F',trend_direction:'flat'})}));"
        "}",
        _MODAL_DOC,
    )
    ms = res["ms"]
    assert 450 <= ms < 750, f"耗时 {ms}ms 不在并行区间 [450,750)（串行会≈800，未取数≈0）"


def test_behavior_panel_appended_not_overwritten():
    """行为级断言：面板先渲染，通用详情**追加**（第 2 次写入以第 1 次为前缀且更长）。"""
    res = _run_js(
        "await __sandbox.showFundDetailModal('006555','测试基金');"
        "console.log(JSON.stringify({writes:__writes}));",
        "(url,opts)=>{"
        "if(url.indexOf('/fund-holdings/detail/')>=0){return Promise.resolve({ok:true,json:async()=>("
        + _DECISION + ")});}"
        "return Promise.resolve({ok:true,json:async()=>(" + _DETAIL_HELD + ")});"
        "}",
        _MODAL_DOC,
    )
    writes = res["writes"]
    assert len(writes) >= 2, f"预期至少两次写入，实际 {len(writes)}"
    first, second = writes[0], writes[1]
    assert "持仓决策辅助" in first and "智能定投建议" in first, "第一次写入不含决策面板"
    assert second.startswith(first), "第二次写入没有以面板为前缀 —— 面板被覆盖了"
    assert len(second) > len(first), "第二次写入未变长 —— 面板可能被覆盖"


def test_behavior_merged_labels_render_on_panel():
    """行为级断言：合并来的 nav_pct_label/timing_label 出现在面板 HTML（后端标签不再丢失）。"""
    res = _run_js(
        "await __sandbox.showFundDetailModal('006555','测试基金');"
        "console.log(JSON.stringify({w:__writes[0]}));",
        "(url,opts)=>{"
        "if(url.indexOf('/fund-holdings/detail/')>=0){return Promise.resolve({ok:true,json:async()=>("
        + _DECISION + ")});}"
        "return Promise.resolve({ok:true,json:async()=>(" + _DETAIL_HELD + ")});"
        "}",
        _MODAL_DOC,
    )
    w = res["w"]
    assert "历史高位 83" in w, "净值百分位标签未合并/未渲染"
    assert "⚪ 正常" in w, "择时标签未合并/未渲染"


def test_behavior_holding_title_vs_non_holding_title():
    """行为级断言：持仓基金标题「持仓决策辅助」；未持仓基金标题「决策参考」。"""
    def _fetch_with(detail_literal: str) -> str:
        return (
            "(url,opts)=>{"
            "if(url.indexOf('/fund-holdings/detail/')>=0){return Promise.resolve({ok:true,json:async()=>("
            + _DECISION + ")});}"
            "return Promise.resolve({ok:true,json:async()=>(" + detail_literal + ")});"
            "}"
        )

    held = _run_js(
        "await __sandbox.showFundDetailModal('006555','持有');"
        "console.log(JSON.stringify({w:__writes[0]}));",
        _fetch_with(_DETAIL_HELD),
        _MODAL_DOC,
    )
    absent = _run_js(
        "await __sandbox.showFundDetailModal('001186','未持有');"
        "console.log(JSON.stringify({w:__writes[0]}));",
        _fetch_with(_DETAIL_ABSENT),
        _MODAL_DOC,
    )
    assert "🎯 持仓决策辅助" in held["w"], "持仓基金未显示「持仓决策辅助」"
    assert "🎯 决策参考" in absent["w"], "未持仓基金未显示「决策参考」"
    assert "🎯 持仓决策辅助" not in absent["w"], "未持仓基金错误显示了「持仓决策辅助」"


def test_behavior_merge_guard_matrix():
    """行为级断言：`_mergeDecisionPayload` 四个方向 —— null 不覆盖、非 null dec 赢、不凭空造键、0 边界。"""
    res = _run_js(
        "const merge=__sandbox._mergeDecisionPayload;"
        "const base={holding_relation:'H',my_holding:{shares:1},nav_pct_label:'B',nav_percentile:9,timing_label:'BT'};"
        "const decNull={my_holding:null,holding_relation:null,nav_pct_label:null,nav_percentile:null,"
        "timing_label:null,advices:[{a:1}],dca:{multiplier:1},action_direction:'加'};"
        "const r1=merge(base,decNull);"
        "const r2=merge(base,{my_holding:{shares:2},holding_relation:'X',nav_pct_label:'D',"
        "nav_percentile:0,timing_label:'DT'});"
        "const r3=merge({},{});"
        "const r4=merge({x:1},{nav_percentile:0});"
        "console.log(JSON.stringify({"
        "r1_my:r1.my_holding,r1_np:r1.nav_pct_label,r1_npc:r1.nav_percentile,r1_tl:r1.timing_label,"
        "r1_adv:r1.advices,r1_act:r1.action_direction,"
        "r2_my:r2.my_holding,r2_np:r2.nav_pct_label,r2_npc:r2.nav_percentile,r2_tl:r2.timing_label,"
        "r3_keys:Object.keys(r3),r4_has:('nav_percentile' in r4),r4_npc:r4.nav_percentile}));",
        "()=>Promise.resolve({ok:true,json:async()=>({})})",
    )
    # ① dec 为 null 时 base 保留
    assert res["r1_my"] == {"shares": 1}
    assert res["r1_np"] == "B"
    assert res["r1_npc"] == 9
    assert res["r1_tl"] == "BT"
    # dec 的 advices/action_direction 仍被采用
    assert res["r1_adv"] == [{"a": 1}]
    assert res["r1_act"] == "加"
    # ② dec 非 null 时 dec 赢
    assert res["r2_my"] == {"shares": 2}
    assert res["r2_np"] == "D"
    assert res["r2_tl"] == "DT"
    # ③ 两侧都无 → 不凭空产生键
    assert res["r3_keys"] == []
    # ④ 边界：nav_percentile=0 不能被当假值丢掉
    assert res["r2_npc"] == 0
    assert res["r4_has"] is True and res["r4_npc"] == 0


def test_behavior_decision_fetch_failure_still_renders_detail():
    """行为级断言：决断面失败时，通用详情仍渲染（不被打成「基金详情渲染失败」）。"""
    res = _run_js(
        "await __sandbox.showFundDetailModal('006555','F');"
        "console.log(JSON.stringify({last:__writes[__writes.length-1],n:__writes.length}));",
        "(url,opts)=>{"
        "if(url.indexOf('/fund-holdings/detail/')>=0){return Promise.reject(new Error('boom'));}"
        "return Promise.resolve({ok:true,json:async()=>(" + _DETAIL_HELD + ")});"
        "}",
        _MODAL_DOC,
    )
    assert res["n"] >= 1
    assert "基金详情渲染失败" not in res["last"], "决断面失败不应打崩整个弹窗"

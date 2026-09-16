"""净值 K 线弹窗「币种切换条」回归测试：外币判定必须失效安全。

## 缺陷（pages/insight-fund.js `_showFundKlineModal`）

旧实现先用**内联正则**判 QDII，并据此**先画**币种切换条，再等后端回
`is_qdii === false` 时撤掉::

    const isQdii = /QDII|纳指|标普|纳斯达克|S&P|海外|港股|美股|日经|越南|印度/i.test(name||'');
    ... ${isQdii ? `<div id="fxToggleBar">...` : ''} ...
    if(d.is_qdii === false && window._klineForeignCur){ bar.remove(); ... }

三个问题叠加：

1. **正则含 4 个真源已删的禁用词**（`services/fund_taxonomy.QDII_REJECTED_KEYWORDS`
   = 全球/恒生/港股/国际/纳指/美股/英国/韩国/S&P）：港股、纳指、美股、S&P。
   这些词的全市场真值精度是 0%~40%，加回来等于把 554 只误判请回来。
2. **零否定词**：taxonomy 给「标普」配了否定词 {港股通, 中国A股, 香港上市中国}，
   前端一个都没有 → 「华宝标普港股通低波红利A」这类**境内人民币**基金判成 USD。
3. **纠偏时机错了（最关键）**：撤除代码排在 `!d.ok / 空 data / fetch reject`
   的提前 return **之后**。后端其实**总会**回真 bool（`api/fund_detail.py:1876`
   `bool(is_qdii_fund(...))`，连 `ok:false` 支也带），但任何非正常路径都走不到撤除
   → 切换条原样留着。用户一点切换，`_fxSwitch` 设 `_klineFxRate = d.rate` 后
   `_renderKlineChart()` 重算，**整条 K 线 ÷ 7.2** —— 那是数值错误，不是显示错误。

## 修法（v9.9.44，失效安全）

- 是否外币**只由后端唯一真源** `services.fund_taxonomy.is_qdii_fund` 回传的
  `d.is_qdii` 决定；**初始不渲染**切换条，仅当 `d.is_qdii === true` 才动态插入。
- 内联正则降级为 `_guessFxCurrency()`：只在**已确认 QDII 之后**调用，作用仅是
  挑币种符号，**不再决定「是不是外币」**。
- `window._klineForeignCur` 初始为 `null`，只在插入切换条时赋值（防脏币种残留）。
- `_fxSwitch` 纵深防御：外币币种未确认时只允许切回 CNY。

失效安全的含义：失败/超时/空数据的默认态是「只有 CNY」；而「先画后撤」的写法
只要再加一条非正常路径就会被重新捅穿。

## 断言类型纪律

- **源码级结构断言**：在源码文本里匹配结构，不执行 JS —— 读作「代码文本如此」。
- **行为级断言**：用 node + vm **真加载并执行** `pages/insight-fund.js`（最小 DOM/fetch
  桩），断言真实运行结果。环境无 node 时这些用例 `pytest.skip`（不静默变绿）。
- **未覆盖**：真机/浏览器视觉复验。本文件不跑浏览器。
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 禁用词直接取自唯一真源 —— 真源改了这里跟着改，避免第二套硬编码表
from services.fund_taxonomy import QDII_REJECTED_KEYWORDS  # noqa: E402

INSIGHT = BACKEND_DIR.parent / "pages" / "insight-fund.js"
_NODE = shutil.which("node")
_API_BASE = "http://api.local/api"


def _src() -> str:
    return INSIGHT.read_text(encoding="utf-8")


def _modal_region() -> str:
    """`_showFundKlineModal` 函数体（右界=函数结束，不延伸到文件末尾）。"""
    src = _src()
    start = src.index("async function _showFundKlineModal")
    end = src.index("\n}\n", start)
    return src[start:end]


def _modal_code_lines() -> str:
    """模态框函数体，去掉 `//` 注释行（保留字符串/正则里的同名词）。"""
    return "\n".join(
        ln for ln in _modal_region().splitlines() if not ln.strip().startswith("//")
    )


# ==========================================================================
# Node 行为级执行
# ==========================================================================
# 记录：__writes = 所有 innerHTML 写入（含初始模板）；__inserts = insertAdjacentHTML 插入
_KLINE_DOC = (
    "const __writes=[];const __inserts=[];"
    "function __mkEl(tag){return {tagName:tag||'div',style:{},classList:{add(){},remove(){}},"
    "  set className(v){},get className(){return '';},set onclick(f){},"
    "  set innerHTML(v){this._h=v;__writes.push(v);},get innerHTML(){return this._h||'';},"
    "  querySelector:(sel)=>__mkEl('div'),"
    "  insertAdjacentHTML:(pos,h)=>{__inserts.push(h);},"
    "  appendChild(){},remove(){},getContext:()=>null};}"
    "const __area=__mkEl('div');"
    "const __document={createElement:(t)=>__mkEl(t),body:{appendChild(){}},"
    "  getElementById:(id)=>id==='klineChartArea'?__area:null,"
    "  querySelector:()=>null,querySelectorAll:()=>[]};"
)


def _run_node(script: str) -> str:
    if not _NODE:
        pytest.skip("环境无 node，跳过 JS 行为级断言")
    r = subprocess.run([_NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"node 执行失败:\n{r.stdout}\n{r.stderr}"
    return r.stdout


def _js_harness(body: str, fetch_js: str) -> str:
    return (
        "const fs=require('fs');const vm=require('vm');"
        "const SRC=fs.readFileSync(" + json.dumps(str(INSIGHT)) + ",'utf8');"
        "const __fetchImpl=" + fetch_js + ";"
        + _KLINE_DOC
        + "const __sandbox={API_BASE:" + json.dumps(_API_BASE) + ","
        "AbortSignal:{timeout:(ms)=>({ms})},"
        "fetch:(url,opts)=>__fetchImpl(url,opts),"
        "console,setTimeout,clearTimeout,Math,Date,JSON,"
        "document:__document};"
        "__sandbox.window=__sandbox;__sandbox.globalThis=__sandbox;"
        "vm.createContext(__sandbox);"
        "vm.runInContext(SRC,__sandbox,{filename:'insight-fund.js'});"
        "__sandbox._renderKlineChart=function(){};"
        "(async()=>{" + body + "})().catch(e=>{console.error(e&&(e.stack||e.message||e));process.exit(3);});"
    )


def _run_js(body: str, fetch_js: str):
    out = _run_node(_js_harness(body, fetch_js))
    return json.loads(out.strip().splitlines()[-1])


def _fetch_ok(payload: str) -> str:
    return "(url,opts)=>Promise.resolve({ok:true,json:async()=>(" + payload + ")})"


_FETCH_REJECT = "(url,opts)=>Promise.reject(new Error('boom'))"

_ROWS = "[{date:'2026-01-01',nav:1.0,cumNav:1.0},{date:'2026-01-02',nav:1.1,cumNav:1.1}]"
# 后端 is_qdii 由 services.fund_taxonomy.is_qdii_fund 判定后回传
_NAV_DOMESTIC = "{ok:true,code:'501029',is_qdii:false,data:" + _ROWS + "}"
_NAV_QDII_USD = "{ok:true,code:'050025',is_qdii:true,data:" + _ROWS + "}"
_NAV_QDII_JPY = "{ok:true,code:'513880',is_qdii:true,data:" + _ROWS + "}"
_NAV_OK_FALSE = "{ok:false,code:'050025',reason:'no data',is_qdii:false}"
_NAV_EMPTY = "{ok:true,code:'050025',is_qdii:true,data:[]}"

# 打开弹窗并回报：切换条是否被插入 / 币种是否被确认 / 初始模板里有没有切换条
_OPEN_AND_REPORT = (
    "await __sandbox._showFundKlineModal(%s,%s);"
    "console.log(JSON.stringify({"
    "inserted:__inserts.join('').indexOf('fxToggleBar')>=0,"
    "inInitial:__writes.join('').indexOf('fxToggleBar')>=0,"
    "cur:__sandbox._klineForeignCur}));"
)


def _open(code: str, name: str, fetch_js: str) -> dict:
    return _run_js(_OPEN_AND_REPORT % (json.dumps(code), json.dumps(name)), fetch_js)


# ==========================================================================
# 一、源码级结构断言
# ==========================================================================
def test_no_frontend_qdii_judgment_regex():
    """前端不得再自带一套 QDII 判据正则。（源码级结构断言）"""
    src = _src()
    assert "const isQdii = /" not in src, "内联 QDII 判据正则回来了"
    assert "${isQdii?" not in src, "切换条又回到初始模板里（先画后撤）了"


def test_toggle_bar_insertion_gated_on_backend_is_qdii_true():
    """切换条插入必须由后端 `is_qdii === true` 把门。（源码级结构断言）"""
    src = _src()
    m = re.search(
        r"if\s*\(\s*d\s*&&\s*d\.ok\s*&&\s*d\.data\s*&&\s*d\.data\.length\s*&&\s*"
        r"d\.is_qdii\s*===\s*true\s*\)\s*\{",
        src,
    )
    assert m, "找不到「后端确认 is_qdii===true 才插入」的条件"


def test_gate_condition_has_no_rejected_keywords():
    """把门条件里不得出现任何禁用词。（源码级结构断言）

    这是 `test_fund_rank_qdii_category.py:487` 那道守卫在前端的对应物。
    """
    gate = re.search(r"if\s*\(([^)]*d\.is_qdii[^)]*)\)", _modal_region())
    assert gate, "找不到含 is_qdii 的把门条件"
    cond = gate.group(1)
    for w in QDII_REJECTED_KEYWORDS:
        assert w not in cond, f"把门条件里出现了禁用词 {w!r}"


def test_rejected_keywords_absent_from_modal_logic():
    """模态框自身逻辑里不得出现禁用词，唯一例外是 `_guessFxCurrency` 挑币种符号。

    （源码级结构断言）`_guessFxCurrency` 只在**已确认 QDII 之后**调用，用 恒生/港股
    选 HKD 符号；删掉它们会让港股 QDII 显示 USD —— 那是拿一个 ÷7.2 换另一个 ÷7.2。
    所以守卫钉的是「禁用词不得出现在判定/把门逻辑里」，而不是全文件字符禁用
    （`pages/` 下 港股 18 处、美股 15 处，大多是行情/地区标签，全禁必假红）。
    """
    code = _modal_code_lines().replace("_guessFxCurrency(name)", "")
    for w in QDII_REJECTED_KEYWORDS:
        assert w not in code, (
            f"模态框逻辑里出现禁用词 {w!r} —— 只有 _guessFxCurrency 内允许"
        )


def test_guess_fx_currency_is_not_used_as_a_gate():
    """`_guessFxCurrency` 不得出现在任何 if 条件里（它不能决定"是不是外币"）。（源码级结构断言）"""
    src = _src()
    assert not re.search(r"if\s*\([^)]*_guessFxCurrency\s*\(", src)
    assert "function _guessFxCurrency" in src


def test_foreign_cur_initialized_to_null():
    """`window._klineForeignCur` 必须初始为 null，不能拿内联正则的猜测值。（源码级结构断言）"""
    src = _src()
    # 锚定到**真实的 if 语句**（注释里也复现了 `d.is_qdii === true`，不能只搜裸串）
    m_gate = re.search(r"if\s*\(\s*d\s*&&\s*d\.ok\s*&&\s*d\.data\s*&&\s*d\.data\.length", src)
    assert m_gate, "找不到把门 if 语句"
    i_gate = m_gate.start()
    # ① 初始必须是 null（不能拿内联正则的猜测值）
    assert "window._klineForeignCur = null;" in src
    assert src.index("window._klineForeignCur = null;") < i_gate, "null 初始化不在把门之前"
    # ② 猜测币种只允许在「后端确认 is_qdii===true」之后赋给全局
    assert src.count("window._klineForeignCur = fxCur;") == 1, "币种赋值点不止一处"
    assert src.index("window._klineForeignCur = fxCur;") > i_gate, (
        "币种在后端确认之前就被写进全局了（脏币种残留）"
    )


def test_fx_switch_rejects_unconfirmed_currency():
    """`_fxSwitch` 必须拒绝未确认的外币币种。（源码级结构断言）"""
    src = _src()
    assert re.search(
        r"if\s*\(\s*cur\s*!==\s*'CNY'\s*&&\s*cur\s*!==\s*window\._klineForeignCur\s*\)\s*return\s*;",
        src,
    ), "_fxSwitch 缺少「未确认币种直接 return」的防御"


# ==========================================================================
# 二、行为级断言（node + vm 真加载执行；无 node 时 skip）
# ==========================================================================
def test_behavior_domestic_fund_gets_no_toggle_bar():
    """行为级断言：境内人民币基金（后端 is_qdii:false）→ 不出现切换条，币种未确认。

    「华宝标普港股通低波红利A」正是旧正则会误判成 USD 的那只。
    """
    r = _open("501029", "华宝标普港股通低波红利A", _fetch_ok(_NAV_DOMESTIC))
    assert r["inserted"] is False, "境内基金出现了币种切换条"
    assert r["inInitial"] is False, "切换条被写进了初始模板（先画后撤回来了）"
    assert r["cur"] is None, f"境内基金却确认了外币币种 {r['cur']!r}"


def test_behavior_true_qdii_gets_toggle_bar():
    """行为级断言：后端确认 is_qdii:true → 动态插入切换条，币种确认为 USD。"""
    r = _open("050025", "博时标普500ETF(QDII)", _fetch_ok(_NAV_QDII_USD))
    assert r["inserted"] is True, "真 QDII 却没有币种切换条"
    assert r["cur"] == "USD", f"币种应为 USD，实际 {r['cur']!r}"
    assert r["inInitial"] is False, "切换条应来自动态插入，而非初始模板"


def test_behavior_ok_false_payload_gets_no_toggle_bar():
    """行为级断言：HTTP 200 但载荷 ok:false → 不出现切换条（被捅穿的那条路径）。

    旧实现的撤除代码排在「无数据」提前 return 之后，这条路径**根本走不到撤除**，
    而后端此时明明回了 `is_qdii:false` —— 这正是本轮最关键的回归。
    """
    r = _open("050025", "华宝标普港股通低波红利A", _fetch_ok(_NAV_OK_FALSE))
    assert r["inserted"] is False, "ok:false 路径下仍出现了切换条"
    assert r["cur"] is None, f"ok:false 路径下却确认了币种 {r['cur']!r}"


def test_behavior_fetch_rejection_gets_no_toggle_bar():
    """行为级断言：fetch reject（d=null，如超时）→ 不出现切换条。"""
    r = _open("050025", "华宝标普港股通低波红利A", _FETCH_REJECT)
    assert r["inserted"] is False, "请求失败路径下仍出现了切换条"
    assert r["cur"] is None, f"请求失败路径下却确认了币种 {r['cur']!r}"


def test_behavior_empty_data_gets_no_toggle_bar():
    """行为级断言：data 为空 → 不出现切换条（没有 K 线可切换）。"""
    r = _open("050025", "博时标普500ETF(QDII)", _fetch_ok(_NAV_EMPTY))
    assert r["inserted"] is False, "空数据下仍出现了切换条"
    assert r["cur"] is None


def test_behavior_currency_symbol_guessed_from_name():
    """行为级断言：已确认 QDII 时，币种符号按名称挑（日经→JPY）。"""
    r = _open("513880", "华夏野村日经225ETF(QDII)", _fetch_ok(_NAV_QDII_JPY))
    assert r["inserted"] is True
    assert r["cur"] == "JPY", f"日经 QDII 币种应为 JPY，实际 {r['cur']!r}"


def test_behavior_fx_switch_ignores_unconfirmed_currency():
    """行为级断言：_klineForeignCur 为 null 时，_fxSwitch('USD') 不得改变显示币种。

    防「脏币种残留 → 整条 K 线 ÷ 7.2」。
    """
    res = _run_js(
        "await __sandbox._showFundKlineModal('501029','华宝标普港股通低波红利A');"
        "const before=__sandbox._klineDisplayCur;"
        "await __sandbox._fxSwitch('USD');"
        "console.log(JSON.stringify({before:before,after:__sandbox._klineDisplayCur,"
        "cur:__sandbox._klineForeignCur}));",
        _fetch_ok(_NAV_DOMESTIC),
    )
    assert res["cur"] is None
    assert res["before"] == "CNY"
    assert res["after"] == "CNY", "未确认的外币币种却把显示币种改掉了"

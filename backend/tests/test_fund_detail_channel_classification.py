"""基金详情弹窗「场内 / 场外」标识回归测试：判据必须数据驱动，不得猜基金代码数字段。

## 缺陷（pages/_components.js，v9.5.128 引入的购买渠道引导块）

旧判据::

    const isLOF = name.includes('(LOF)') || name.includes('（LOF）') || code.startsWith('5');
    const isETF = name.includes('ETF') && !name.includes('联接');
    const isExchange = isLOF || isETF;

`code.startsWith('5')` 是个**代码段猜测**，不是数据：

* 5 开头的中国基金代码里既有场内（沪市 ETF 51xxxx / 沪市 LOF 50xxxx），
  **也大量是场外开放式基金**（519xxx 是最典型的场外段）。
* 铁证：`519736`（用户本人真实持仓，见 docs/HEALTH-CHECK-2026-04-19.md:6）是**场外**
  开放式基金（生产 `purchase_status='开放申购'`），却因 `startsWith('5')` 成立被前端
  打上「🏦 场内基金 / LOF」。

  ⚠️ 顺带发现的**独立**后端问题（不在本文件修复范围，已报 team-lead）：
  `docs/HEALTH-CHECK-2026-04-19.md:6` 与 `services/fund_classifier.py:26`
  （`KNOWN_FUND_TYPES["519736"] = "bond"`）都把 519736 当成「交银裕隆纯债A / 债券」，
  但生产 `/api/fund/detail/519736` 返回的是「交银新成长混合」，且 `top_holdings`
  全是股票（药明康德/宁德时代/宇通客车…）、`returns['2y']=34.89` —— **这是一只
  股票/混合型基金，把它硬编码成 `bond` 是分类错误**（影响
  `portfolio_overview.py:100 classify_and_allocate` 的股债配置）。
  本文件的结论**不依赖**这个争议：两个候选名字都无 ETF/LOF 标记、
  `purchase_status` 都是场外状态，判成场外是同一个结果。
  本语料采用生产实抓值 `交银新成长混合`。
* 收窄前缀解决不了：`519736`.startsWith('51') 与 .startsWith('50') 都是 false，
  但 .startsWith('5') 是 true；而 51 这个前缀本身又会撞上别的东西。**代码前缀
  这条路本身不可靠，换一个前缀只是换一种猜法。**

## 修法（v9.9.48）：改用后端已下发的数据字段

新增纯函数 `_classifyFundChannel(d)`，判据**全部来自数据**，不猜代码数字：

1. **`d.purchase.purchase_status === '场内交易'`**（天天基金「申购状态」，后端
   `/api/fund/detail/{code}` 与 `/api/fund-holdings/detail/{code}` **都已经下发**）。
   这是「能不能场外申购」的权威渠道状态：场外基金是 开放申购/限大额/暂停申购，
   而只能场内买卖的 ETF 是 场内交易。
2. **法定名称里的 ETF 标记**，排除「联接」——ETF 联接是**场外** feeder 基金。
3. **法定名称里的括号 LOF 标记**（正则覆盖 `(QDII-LOF)` / `(QDII-LOF-FOF)` 复合后缀）。

失败安全：`purchase` 缺失/为 null 时 ① 自动不成立，退化到名称标记；名称也没有
数据时默认**场外**（全市场绝大多数是场外，宁可漏标也不误标「场内」）。

## 实测背书（2026-09-17 生产 38 只样本，见 _CORPUS）

    purchase_status === '场内交易'   10/10 都是 ETF（510300/510500/159915/512880/
                                    518880/513100/510050/510180/159901/159922）
    非场内 28/28 无一是「场内交易」  （含 519xxx 场外段 8 只、ETF联接 3 只、
                                    LOF 10 只、普通场外 7 只）

## 断言类型纪律

- **源码级结构断言**：在源码文本里匹配结构，不执行 JS —— 读作「代码文本如此」。
- **行为级断言**：用 node + vm **真加载并执行** `pages/_components.js`（最小
  DOM/fetch 桩），断言真实运行结果。环境无 node 时这些用例 `pytest.skip`
  （不静默变绿）。
- **未覆盖**：真机/浏览器视觉复验。本文件不跑浏览器。
"""

import json
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
COMPONENTS = BACKEND_DIR.parent / "pages" / "_components.js"

_NODE = shutil.which("node")
_API_BASE = "http://api.local/api"


def _src() -> str:
    return COMPONENTS.read_text(encoding="utf-8")


# ==========================================================================
# 生产样本语料（2026-09-17 实抓 http://150.158.47.189:8000/api/fund/detail/{code}）
# 每行 = (code, 后端返回的 name, 后端返回的 purchase.purchase_status, 期望是否场内, 期望徽章)
# 期望徽章：'ETF' / 'LOF' / None（场外）
# ==========================================================================
_CORPUS: list = [
    # ---- ① 场内 ETF：后端 purchase_status 实测均为「场内交易」 ----
    ("510300", "华泰柏瑞沪深300ETF", "场内交易", True, "ETF"),
    ("510500", "南方中证500ETF", "场内交易", True, "ETF"),
    ("159915", "易方达创业板ETF", "场内交易", True, "ETF"),
    ("512880", "国泰中证全指证券公司ETF", "场内交易", True, "ETF"),
    ("518880", "华安易富黄金ETF", "场内交易", True, "ETF"),
    ("513100", "国泰纳斯达克100ETF(QDII)", "场内交易", True, "ETF"),
    ("510050", "华夏上证50ETF", "场内交易", True, "ETF"),
    ("510180", "华安上证180ETF", "场内交易", True, "ETF"),
    ("159901", "易方达深证100ETF", "场内交易", True, "ETF"),
    ("159922", "嘉实中证500ETF", "场内交易", True, "ETF"),
    # ---- ② 场内 LOF：法定名称带括号 LOF 标记（含复合后缀） ----
    ("161725", "招商中证白酒指数(LOF)A", "限大额", True, "LOF"),
    ("501029", "华宝标普中国A股红利机会ETF联接A(LOF)", "开放申购", True, "LOF"),
    ("502003", "易方达中证军工(LOF)A", "开放申购", True, "LOF"),
    ("502023", "鹏华国证钢铁行业指数(LOF)A", "开放申购", True, "LOF"),
    ("501057", "汇添富中证新能源汽车产业指数(LOF)A", "开放申购", True, "LOF"),
    ("160216", "国泰大宗商品(QDII-LOF)A", "限大额", True, "LOF"),
    ("163402", "兴全趋势投资混合(LOF)", "开放申购", True, "LOF"),
    ("160119", "南方中证500ETF联接(LOF)A", "开放申购", True, "LOF"),
    ("501018", "南方原油(QDII-LOF-FOF)-A", "暂停申购", True, "LOF"),
    ("164701", "汇添富黄金及贵金属(QDII-LOF-FOF)-A", "暂停申购", True, "LOF"),
    # ---- ③ 场外：必须判成非场内 ----
    # ★ 519736 就是被 startsWith('5') 误判的那只（用户本人真实持仓）
    ("519736", "交银新成长混合", "开放申购", False, None),
    ("519981", "长信标普100等权重指数人民币", "限大额", False, None),
    ("519068", "汇添富成长焦点混合", "开放申购", False, None),
    ("519674", "银河创新成长混合A", "开放申购", False, None),
    ("519983", "长信量化先锋混合A", "开放申购", False, None),
    ("519185", "万家精选混合A", "限大额", False, None),
    ("519150", "新华优选消费混合", "开放申购", False, None),
    ("519697", "交银优势行业混合", "开放申购", False, None),
    ("501310", "华宝沪港深价值指数A", "开放申购", False, None),
    # ETF 联接是场外 feeder —— 名称含 ETF 但一定有「联接」
    ("000051", "华夏沪深300ETF联接A", "开放申购", False, None),
    ("110020", "易方达沪深300ETF联接A", "开放申购", False, None),
    ("050025", "博时标普500ETF联接(QDII)-A-CNY", "暂停申购", False, None),
    # 普通场外
    ("000001", "华夏成长混合", "开放申购", False, None),
    ("002001", "华夏回报混合A", "限大额", False, None),
    ("217022", "招商产业债券A", "限大额", False, None),
    ("161603", "融通债券A/B", "开放申购", False, None),
    ("160213", "国泰纳斯达克100指数", "限大额", False, None),
    ("505888", "嘉实元和直投封闭混合", None, False, None),
]


# ==========================================================================
# Node 行为级执行
# ==========================================================================
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
    r = subprocess.run([_NODE, "-e", script], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"node 执行失败:\n{r.stdout}\n{r.stderr}"
    return r.stdout


def _js_harness(body: str, fetch_js: str, doc_js: str = _MODAL_DOC) -> str:
    return (
        "const fs=require('fs');const vm=require('vm');"
        "const SRC=fs.readFileSync(" + json.dumps(str(COMPONENTS)) + ",'utf8');"
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


def _run_js(body: str, fetch_js: str, doc_js: str = _MODAL_DOC):
    out = _run_node(_js_harness(body, fetch_js, doc_js))
    return json.loads(out.strip().splitlines()[-1])


_NOOP_FETCH = "()=>Promise.resolve({ok:true,json:async()=>({})})"


@lru_cache(maxsize=1)
def _classify_all() -> tuple:
    """对 _CORPUS 全量跑一遍真实 `_classifyFundChannel`（单次 node 调用）。

    进程内缓存 1 份；每条用例都是**新进程**，所以源码改动（故障注入）一定会被
    重新加载，不存在「缓存住了旧实现」导致假绿。
    """
    rows = [
        {"code": c, "name": n, "status": s}
        for (c, n, s, _ex, _badge) in _CORPUS
    ]
    res = _run_js(
        "const rows=JSON.parse(" + json.dumps(json.dumps(rows)) + ");"
        "console.log(JSON.stringify(rows.map(r=>__sandbox._classifyFundChannel("
        "{code:r.code,name:r.name,purchase:{purchase_status:r.status}}))));",
        _NOOP_FETCH,
    )
    return tuple(res)


def _render(code: str, name: str, purchase_status) -> str:
    """真跑 `showFundDetailModal`，返回弹窗 body 的所有 innerHTML 写入拼接。"""
    detail = {
        "code": code,
        "name": name,
        "nav": 1.0,
        "trend_direction": "flat",
        "trend_score": 5,
        "trend_dimensions": {},
        "returns": {},
        "purchase": {"available": True, "purchase_status": purchase_status,
                     "redeem_status": "开放赎回", "min_buy": None,
                     "daily_limit": None, "fee_rate": 0.15},
    }
    res = _run_js(
        "await __sandbox.showFundDetailModal(" + json.dumps(code) + "," + json.dumps(name) + ");"
        "console.log(JSON.stringify({w:__writes.join('')}));",
        "(url,opts)=>{"
        "if(url.indexOf('/fund-holdings/detail/')>=0){return Promise.reject(new Error('n/a'));}"
        "return Promise.resolve({ok:true,json:async()=>(" + json.dumps(detail) + ")});"
        "}",
    )
    return res["w"]


# ==========================================================================
# 一、源码级结构断言（低成本护栏）
# ==========================================================================
def test_no_code_prefix_heuristic():
    """不得再用基金代码数字段（startsWith('5')）判场内。（源码级结构断言）

    这是本轮缺陷的**根因**。只断言 `startsWith('5')` 会被「换成 51 / 50 等另一个
    猜测」绕过，所以再钉一条更宽的：整个文件不得出现 `code.startsWith(...)`。
    """
    src = _src()
    assert "startsWith('5')" not in src, "代码前缀猜测 startsWith('5') 回来了"
    assert not re.search(r"\bcode\s*\.\s*startsWith\s*\(", src), (
        "出现了基于基金代码前缀的场内/场外判据 —— 换前缀只是换一种猜法"
    )


def test_classifier_function_exists_and_is_used():
    """`_classifyFundChannel` 必须存在，且被购买渠道块真正调用。（源码级结构断言）"""
    src = _src()
    assert "function _classifyFundChannel(" in src, "找不到 _classifyFundChannel"
    assert "_classifyFundChannel(d)" in src, "渠道块没有真的调用分类器（死代码）"


def test_classifier_consumes_backend_purchase_status():
    """分类器必须消费后端下发的 purchase.purchase_status。（源码级结构断言）

    锚定到**判据表达式本身**，防止只在别处引用一次字段名来骗过 `in src`。
    """
    src = _src()
    m = re.search(r"function\s+_classifyFundChannel\s*\(", src)
    assert m, "找不到 _classifyFundChannel 定义"
    start = m.start()
    end = src.index("\n}\n", start)
    body = src[start:end]
    assert "purchase_status" in body, "分类器没有读 purchase_status"
    assert "'场内交易'" in body, "分类器没有把「场内交易」作为渠道状态判据"
    # 分类器的判据里**完全不出现** code —— 这正是「不再猜代码数字段」的含义
    assert "code" not in body, "分类器函数体里出现了 code —— 又回到代码启发式了"


def test_classifier_is_fail_safe_on_missing_purchase():
    """purchase 缺失时不得判为场内（失效安全）。（源码级结构断言）"""
    src = _src()
    m = re.search(r"function\s+_classifyFundChannel\s*\(", src)
    body = src[m.start(): src.index("\n}\n", m.start())]
    # 缺失时必须走 `|| ''` 归一，不能因为 undefined 抛错或误判
    assert re.search(r"purchase\s*\|\|\s*\{\}", body), "purchase 缺少空对象兜底"
    assert re.search(r"purchase_status\s*\|\|\s*''", body), "purchase_status 缺少空串兜底"


# ==========================================================================
# 二、行为级断言（node + vm 真加载执行；无 node 时 skip）
# ==========================================================================
@pytest.mark.parametrize(
    "idx,code,name,status,expect_exchange,expect_badge",
    [(i,) + row for i, row in enumerate(_CORPUS)],
    ids=[f"{c}-{n[:10]}" for (c, n, _s, _e, _b) in _CORPUS],
)
def test_behavior_corpus_classification(idx, code, name, status, expect_exchange, expect_badge):
    """行为级断言：38 只生产样本逐只锁定分类结果。

    语料是 2026-09-17 从生产 `/api/fund/detail` 实抓的（code / name /
    purchase_status 全是真值），不是编出来的形状。
    """
    got = _classify_all()[idx]
    assert got["isExchange"] is expect_exchange, (
        f"{code} {name}（purchase_status={status!r}）判成 isExchange={got['isExchange']}，"
        f"期望 {expect_exchange}"
    )
    if expect_badge == "ETF":
        assert got["isETF"] is True, f"{code} 应打 ETF 徽章"
    elif expect_badge == "LOF":
        assert got["isLOF"] is True, f"{code} 应打 LOF 徽章"
        assert got["isETF"] is False, f"{code} 不应打 ETF 徽章"


def test_behavior_otc_codes_are_never_exchange():
    """行为级断言：5 开头的场外段一只都不能被判成场内（本轮缺陷的核心断言）。"""
    all_got = _classify_all()
    otc_5 = [(i, r) for i, r in enumerate(_CORPUS) if r[0].startswith("5") and r[3] is False]
    assert len(otc_5) >= 8, f"语料里 5 开头的场外样本不足（{len(otc_5)} 只），断言强度不够"
    bad = [r[0] for i, r in otc_5 if all_got[i]["isExchange"]]
    assert bad == [], f"这些 5 开头的场外基金被判成场内：{bad}"


def test_behavior_corpus_has_no_exchange_status_false_positive():
    """行为级断言：purchase_status '场内交易' 在语料里对 ETF 是 100% 精确/召回。

    这是「purchase_status 比代码前缀可靠」的量化证据：10/10 ETF 命中、
    28/28 非场内不命中。任一侧破功说明该判据需要重新实测。
    """
    etf_rows = [r for r in _CORPUS if r[4] == "ETF"]
    non_etf = [r for r in _CORPUS if r[4] != "ETF"]
    assert len(etf_rows) == 10 and len(non_etf) == 28
    assert all(r[2] == "场内交易" for r in etf_rows), "语料里有 ETF 的 purchase_status 不是「场内交易」"
    assert not any(r[2] == "场内交易" for r in non_etf), (
        "语料里有非 ETF 基金的 purchase_status 是「场内交易」—— 判据不再可靠"
    )


def test_behavior_modal_renders_otc_for_519736():
    """行为级断言：519736（用户真实持仓）弹窗渲染「📱 场外基金」，没有「🏦 场内基金」。"""
    html = _render("519736", "交银新成长混合", "开放申购")
    assert "🏦 场内基金" not in html, "519736 被渲染成场内基金了（本轮缺陷）"
    assert "📱 场外基金" in html, "519736 没有渲染场外基金引导块"
    assert ">LOF</span>" not in html, "519736 被打上 LOF 徽章了"


def test_behavior_modal_renders_etf_for_510300():
    """行为级断言：510300 沪深300ETF（用户真实持仓）仍正确渲染「🏦 场内基金 / ETF」。"""
    html = _render("510300", "华泰柏瑞沪深300ETF", "场内交易")
    assert "🏦 场内基金" in html, "510300 没有被渲染成场内基金"
    assert ">ETF</span>" in html, "510300 没有打上 ETF 徽章"
    assert "📱 场外基金" not in html, "510300 被渲染成场外基金了"


def test_behavior_modal_renders_lof_for_161725():
    """行为级断言：161725 招商中证白酒指数(LOF)A 渲染「🏦 场内基金 / LOF」。"""
    html = _render("161725", "招商中证白酒指数(LOF)A", "限大额")
    assert "🏦 场内基金" in html, "161725 没有被渲染成场内基金"
    assert ">LOF</span>" in html, "161725 没有打上 LOF 徽章"


def test_behavior_exchange_only_status_without_name_marker_still_etf():
    """行为级断言：purchase_status=场内交易 但名称被截断丢失 ETF 时，仍判场内 ETF。

    防回归的点：项目里存在「基金名 [:12] 盲截」的历史缺陷（v9.9.x 已修过若干处），
    名称标记可能在某些调用路径上丢掉 —— 数据字段是它的兜底。
    """
    got = _run_js(
        "console.log(JSON.stringify(__sandbox._classifyFundChannel("
        "{code:'510300',name:'华泰柏瑞沪深300',purchase:{purchase_status:'场内交易'}})));",
        _NOOP_FETCH,
    )
    assert got["isExchange"] is True, "名称丢了 ETF 标记就不认场内了"
    assert got["isETF"] is True
    assert got["basis"] == "purchase_status"


def test_behavior_missing_purchase_is_fail_safe():
    """行为级断言：purchase 缺失 / 为 null / 状态为 null 时，一律不判场内（失效安全）。"""
    got = _run_js(
        "console.log(JSON.stringify(["
        "__sandbox._classifyFundChannel({code:'510300',name:'华泰柏瑞沪深300ETF'}),"
        "__sandbox._classifyFundChannel({code:'510300',name:'华泰柏瑞沪深300ETF',purchase:null}),"
        "__sandbox._classifyFundChannel({code:'519736',name:'交银新成长混合',purchase:{}}),"
        "__sandbox._classifyFundChannel({code:'519736',name:'交银新成长混合',"
        "purchase:{purchase_status:null}}),"
        "__sandbox._classifyFundChannel({}),"
        "__sandbox._classifyFundChannel(null)"
        "]));",
        _NOOP_FETCH,
    )
    # 前两只名称里有 ETF 标记 → 仍是场内（名称标记兜底），但 exchangeOnly 必须为 false
    assert got[0]["isExchange"] is True and got[0]["exchangeOnly"] is False
    assert got[1]["isExchange"] is True and got[1]["exchangeOnly"] is False
    # 后四只没有任何渠道证据 → 场外（失效安全）
    for i in (2, 3, 4, 5):
        assert got[i]["isExchange"] is False, f"第 {i} 个载荷无渠道数据却判成场内"
        assert got[i]["isETF"] is False and got[i]["isLOF"] is False

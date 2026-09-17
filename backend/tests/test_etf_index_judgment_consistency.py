"""ETF / 指数判定「跨口径一致性」防护测试。

## 为什么有这个文件

上一轮全仓口径审计（`backend/tests/` 之外）的结论是：**当前生产数据下没有
用户可见的 ETF 判定冲突**，但存在一处**已知且当前不显性**的分叉——「ETF 联接」
基金在两套口径里结论相反：

* 前端详情弹窗（`pages/_components.js:472-494` `_classifyFundChannel`）
  判 **isETF=False**（正确的：联接是场外 feeder 基金）；
* 后端榜单 `ranks.etf` 篮（`backend/scripts/fund_rank_build.py:294`，
  ``"ETF" in name`` 纯名称子串）判 **True**。

它今天不害人，是因为 `ranks.etf` 篮全仓只有测试消费，选基页 tab
（`pages/insight-fund.js:161,181`）根本没有 ETF 入口，`night_worker.py:1724`
唯一调用点写死 `category="stock"`。**但它是一个地雷**：一旦有人给那个篮接上
真实入口，同一个用户在持仓页和选基页就会看到相反答案。

本文件不消灭这个分叉（判据收敛是另一件事），而是**把分叉钉死成可执行的事实**：
分叉只允许发生在「ETF 联接」这一类上，且只允许是「前端 False / 后端 True」这一个
方向。出现任何新的分叉类别，或者方向反转，这里立刻变红。

## 关于 `ftype='etf'`

审计时曾假设存在「invest_type 缺失时回填 ftype='etf'」的分叉。**经查证该分叉不存在**：
`backend/scripts/fund_rank_build.py` 里根本没有名为 `ftype` 的变量，落盘字段只有
`type`（来自 `fund_basic.fund_type`，见 :264）和 `invest_type`（:265）。因此本文件
改为钉死**真实存在的那一条** invest_type 分叉，见
``test_invest_type_missing_drops_index_fund_from_rank_bucket``。

## 「Tushare 的 fund_type 在缺失时实际是什么值」——**未确认**

仓库没有记录，本地也没有 `fund_rank_ts.json`（生产数据在服务器），无法实测。
唯一相关证据是 `fund_rank_build.py:108-109` 那份 2026-09-13 全量分布：
混合型 6417 / 股票型 6280 / 债券型 4758 / 货币型 335 / REITs 104 / 其他 55，
合计正好 17949 = 全市场，**分布里没有 None 档**。但这只能说明当时取样时
fund_type 没有缺失，不能推出缺失时的取值。**结论：未确认，不猜。**
（`invest_type` 则有明确记录：1337 只为 None，见 :118。）

## 断言类型纪律（沿用 test_fund_detail_channel_classification.py）

* **行为级断言**：node + vm 真加载执行 `pages/_components.js`；python 侧
  用 importlib 真加载 `backend/scripts/fund_rank_build.py`。环境无 node 时
  相关用例 `pytest.skip`（**不静默变绿**）。
* **源码级结构断言**：匹配源码文本，读作「代码文本如此」。用于锁住那些
  无法直接调用的判据（如 `fund_screen._INDEX_KW` 是函数内局部变量）。
* **未覆盖**：浏览器视觉复验。
"""

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
COMPONENTS = REPO_ROOT / "pages" / "_components.js"
FRB_PATH = BACKEND_DIR / "scripts" / "fund_rank_build.py"
FUND_SCREEN = BACKEND_DIR / "services" / "fund_screen.py"

# 语料单一真源：直接复用隔壁用例里那份「2026-09-17 生产实抓」的 38 只样本，
# 不另抄一份，避免两份语料各自漂移。
SIBLING = Path(__file__).resolve().parent / "test_fund_detail_channel_classification.py"

_NODE = shutil.which("node")


# ==========================================================================
# 语料加载
# ==========================================================================

@lru_cache(maxsize=1)
def _corpus() -> tuple:
    """加载生产语料：每行 (code, name, purchase_status, 期望场内, 期望徽章)。"""
    spec = importlib.util.spec_from_file_location("_ch_corpus_src", SIBLING)
    assert spec is not None and spec.loader is not None, f"无法加载语料模块: {SIBLING}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_ch_corpus_src"] = mod
    spec.loader.exec_module(mod)
    return tuple(mod._CORPUS)


# ==========================================================================
# 前端口径：真跑 _classifyFundChannel
# ==========================================================================

_JS_DOC = (
    "let __cur='';"
    "const __bodyEl={get innerHTML(){return __cur;},set innerHTML(v){__cur=v;}};"
    "const __document={createElement:()=>({style:{},classList:{add(){},remove(){}},"
    "set onclick(f){},set innerHTML(v){}}),body:{appendChild(){}},"
    "getElementById:()=>__bodyEl,querySelector:()=>null};"
)


def _run_js(body: str) -> str:
    """在 vm 沙箱里真加载 pages/_components.js 并执行 body，返回 stdout。"""
    if not _NODE:
        pytest.skip("环境无 node，跳过 JS 行为级断言")
    script = (
        "const fs=require('fs');const vm=require('vm');"
        "const SRC=fs.readFileSync(" + json.dumps(str(COMPONENTS)) + ",'utf8');"
        "const __fetchImpl=()=>Promise.resolve({ok:true,json:async()=>({})});"
        + _JS_DOC
        + "const __sandbox={API_BASE:'http://api.local/api',"
        "getProfileId:()=>'LeiJiang',getUserId:()=>'LeiJiang',"
        "AbortSignal:{timeout:(ms)=>({ms})},"
        "fetch:(url,opts)=>__fetchImpl(url,opts),"
        "console,setTimeout,clearTimeout,document:__document};"
        "__sandbox.window=__sandbox;__sandbox.globalThis=__sandbox;"
        "vm.createContext(__sandbox);"
        "vm.runInContext(SRC,__sandbox,{filename:'_components.js'});"
        "(async()=>{" + body + "})().catch(e=>{console.error(e&&(e.stack||e.message||e));process.exit(3);});"
    )
    r = subprocess.run([_NODE, "-e", script], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"node 执行失败:\n{r.stdout}\n{r.stderr}"
    return r.stdout


@lru_cache(maxsize=1)
def _fe_classify_all() -> tuple:
    """对语料全量跑一遍真实 _classifyFundChannel（单次 node 调用）。

    每条用例都是新进程，源码改动（故障注入）一定会被重新加载，不存在
    「缓存住旧实现」导致假绿。
    """
    rows = [{"code": c, "name": n, "status": s} for (c, n, s, _e, _b) in _corpus()]
    out = _run_js(
        "const rows=JSON.parse(" + json.dumps(json.dumps(rows)) + ");"
        "console.log(JSON.stringify(rows.map(r=>__sandbox._classifyFundChannel("
        "{code:r.code,name:r.name,purchase:{purchase_status:r.status}}))));"
    )
    return tuple(json.loads(out.strip().splitlines()[-1]))


def _fe_of(code: str) -> dict:
    for (c, _n, _s, _e, _b), res in zip(_corpus(), _fe_classify_all()):
        if c == code:
            return res
    raise AssertionError(f"语料里没有 {code}")


# ==========================================================================
# 后端口径
# ==========================================================================

@lru_cache(maxsize=1)
def _frb():
    """按路径加载 backend/scripts/fund_rank_build.py（run_name 非 __main__）。"""
    spec = importlib.util.spec_from_file_location("_frb_consistency", FRB_PATH)
    assert spec is not None and spec.loader is not None, f"无法加载: {FRB_PATH}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_frb_consistency"] = mod
    spec.loader.exec_module(mod)
    return mod


def _be_etf_bucket(name: str) -> bool:
    """复刻 fund_rank_build.py:294 的 ranks.etf 入篮判据。

    ⚠️⚠️ 本函数是**逐字复刻**，不是行为级覆盖 —— 动 :294 的人必读 ⚠️⚠️

    ## 性质：结构级上锁，不是行为级覆盖
    :294 的判据是 `build_rank()` 里的一行内联列表推导，import 不到也无法直接
    调用，所以这里只能复刻。**本函数不会随着 :294 的修改自动跟着变**：

    * 你改了 :294 的判据 → 本函数**仍然返回旧口径的结果** → 上面那些
      行为级一致性断言**不会变红**（它们拿到的是过期的副本）；
    * 真正会红的是 ``test_backend_etf_bucket_caliber_source_lock``，它用正则
      锁住 :294 的源码原文。**所以改完 :294 请务必人工同步本函数。**

    ## 为什么明知如此还保留复刻（而不是正则从源码抽判据）
    这是失败模式的取舍，不是图省事：
    * **结构级锁（本方案）**的失败模式：源码改了、复刻没跟上 → **变红**；
    * **正则抽源码**的失败模式：源码改了、正则没匹配上 → **断言静默失效，恒绿**。

    本项目对恒绿零容忍（恒绿的守卫 = 空转的绿）。红的守卫再笨也是守卫，
    绿的假守卫比没有守卫更坏——它会让下一个人误以为这里有覆盖。两害相权取红。
    此做法与 ``test_fund_detail_channel_classification.py:234-265`` 的既有
    源码级结构断言一致，保持口径统一本身也是价值。
    """
    return "ETF" in (name or "")


_KW_RE = re.compile(r"_INDEX_KW\s*=\s*\[(.*?)\]", re.S)


def _index_kw() -> list:
    """从 fund_screen.py 源码里**现取** 19 词指数关键词表。

    _INDEX_KW 是函数内局部变量，import 不到，只能从源码取——好处是源码改
    了这里立刻跟着变，不会用一份抄错的副本测出假绿。
    """
    src = FUND_SCREEN.read_text(encoding="utf-8")
    m = _KW_RE.search(src)
    assert m is not None, "fund_screen.py 里找不到 _INDEX_KW —— 选基页指数口径已被改名/移除"
    return [x.strip().strip("\"'") for x in m.group(1).split(",") if x.strip()]


def _be_index_by_name(name: str) -> bool:
    return any(k in (name or "") for k in _index_kw())


# ==========================================================================
# 1. 一致性主体：两条 ETF 口径只允许在「联接」这一类上分叉
# ==========================================================================

# 已知且被接受的分叉集合（2026-09-17 生产语料实测）。
# 全部是「名称同时含 ETF 与 联接」的基金，方向一律 前端 False / 后端 True。
KNOWN_LIANJIE_DIVERGENCE = frozenset({"501029", "160119", "000051", "110020", "050025"})

# 语料规模下限：防止有人把语料删空让一致性用例「全绿」。
CORPUS_MIN_SIZE = 38


def test_corpus_is_production_sized():
    """语料必须从生产实抓、且没被偷偷删空。

    一致性断言的分母就是它——语料缩水会让上面的用例失去意义却仍然变绿。
    """
    assert len(_corpus()) >= CORPUS_MIN_SIZE, (
        f"语料只剩 {len(_corpus())} 只（要求 >= {CORPUS_MIN_SIZE}），一致性断言已失去意义"
    )


def test_etf_caliber_divergence_is_exactly_the_lianjie_class():
    """两条 ETF 口径的分叉集合必须**恰好**等于已知的那 5 只联接基金。

    这是本文件的主体断言。设计意图：
    * 出现任何**新类别**的分叉（比如哪天 LOF 也开始打架）→ 这里红；
    * 分叉**方向反转**（前端 True / 后端 False）→ 下面那条单独断言会红；
    * 任一端口径被改动 → 分叉集合变化 → 这里红。

    它不是恒真断言：38 只样本里 33 只必须一致、5 只必须分叉，两个方向都可证伪。
    """
    diverged = []
    for (code, name, _s, _e, _b), res in zip(_corpus(), _fe_classify_all()):
        if bool(res["isETF"]) != _be_etf_bucket(name):
            diverged.append(code)

    assert set(diverged) == set(KNOWN_LIANJIE_DIVERGENCE), (
        "两条 ETF 口径的分叉集合发生了变化：\n"
        f"  实测分叉: {sorted(diverged)}\n"
        f"  已知分叉: {sorted(KNOWN_LIANJIE_DIVERGENCE)}\n"
        "若新增的是另一类基金，说明出现了新的口径冲突（要修判据，不是改这条断言）；\n"
        "若减少了，说明某端口径被改动（确认是有意对齐后，再回来更新本常量）。"
    )


def test_lianjie_divergence_has_one_direction_only():
    """已知分叉的方向必须**恒为**「前端 False / 后端 True」，不许反转。

    方向反转意味着前端开始把联接基金标成「🏦 场内基金 / ETF」，并告诉用户
    「ETF 只能通过证券账户场内买卖」——那是对联接基金**逐字错误**的引导，
    会直接误导定投操作。
    """
    for code in sorted(KNOWN_LIANJIE_DIVERGENCE):
        name = next(n for (c, n, _s, _e, _b) in _corpus() if c == code)
        res = _fe_of(code)
        assert res["isETF"] is False, f"{code} {name}：前端不应判 ETF"
        assert res["isExchange"] is False or res["isLOF"] is True, (
            f"{code} {name}：前端不应把它显示为可场内交易的 ETF（isExchange={res['isExchange']}）"
        )
        assert _be_etf_bucket(name) is True, f"{code} {name}：后端 etf 篮当前确实收它"


# ==========================================================================
# 2. 显式记录已知分叉（用例保持绿色）
# ==========================================================================

def test_documented_lianjie_divergence_is_the_current_behavior():
    """把「000051 华夏沪深300ETF联接A」的分叉钉死为当前真实行为。

    ## 这是什么
    同一只基金，两条口径给出相反答案：
    * 前端 `_classifyFundChannel` → isETF=False、basis='otc'（判「场外基金」）
    * 后端 `ranks.etf` 篮 → True（因为名字里有 "ETF" 三个字母）

    ## 为什么现在不修
    后端那个篮当前**没有生产消费方**：选基页 tab 只有 all/stock/bond/index/qdii
    （`pages/insight-fund.js:161,181`），`night_worker.py:1724` 唯一调用点写死
    `category="stock"`。所以它今天不会让用户看错数。

    ## 触发条件（= 它什么时候变成真 bug）
    给 `ranks.etf` 篮接上任何用户可见入口（选基页加 ETF tab、晨报推荐改传
    category="etf"、持仓页用它做分组）。届时同一个用户在持仓/详情页看到
    「场外基金」、在选基页看到它躺在 ETF 篮里。

    ## 到时候该改什么
    修**判据对齐**（让两处共用同一套 ETF 定义），**不是**改这条测试。
    本用例是 tripwire：它变红 = 你已经动了判据，请先读完上面三段。

    ⚠️ 本用例断言的是**当前真实行为**，不是期望行为——所以它现在是绿的。
    """
    res = _fe_of("000051")
    assert res["isETF"] is False, "前端判据已变：000051 不再被判为非 ETF"
    assert res["isExchange"] is False
    assert res["isLOF"] is False
    assert res["basis"] == "otc", f"前端判据依据变了: {res['basis']}"

    name = next(n for (c, n, _s, _e, _b) in _corpus() if c == "000051")
    assert _be_etf_bucket(name) is True, "后端 etf 篮判据已变：不再收 000051"

    # 两句话把分叉本身写死，避免哪天两边悄悄对齐了却没人知道
    assert res["isETF"] != _be_etf_bucket(name)


def test_invest_type_missing_drops_index_fund_from_rank_bucket():
    """指数口径的 invest_type 分叉（真实存在，非假设）。

    ## 分叉内容
    * 榜单口径 `is_index_fund`（`fund_rank_build.py:148-160`）只看
      `invest_type ∈ (被动指数型, 增强指数型)`；
    * 选基页口径（`fund_screen.py:120-123` 的 19 词 `_INDEX_KW`）只看名称。

    `invest_type` 缺失时前者直接判 False，**基金静默掉出 index 篮**；而后者
    靠名字照样判它是指数基金。两条链路对同一只基金给出不同答案。

    ## 这不是假设
    `fund_rank_build.py:118` 的注释自己记着：全市场有 **1337 只**基金
    `invest_type` 为 None。这条用例覆盖的正是这个已存在的口子。

    ## 关于 ftype
    审计时假设的「invest_type 缺失回填 ftype='etf'」经查证**不存在**——
    fund_rank_build.py 里没有 ftype 变量，落盘字段只有 `type`（:264，来自
    fund_basic.fund_type）和 `invest_type`（:265）。

    ⚠️ 同样是断言当前真实行为（绿），不是期望行为。
    """
    frb = _frb()
    name = next(n for (c, n, _s, _e, _b) in _corpus() if c == "000051")

    # ① invest_type 齐全 → 两条口径都认它是指数基金
    assert frb.is_index_fund({"invest_type": "被动指数型"}) is True
    assert _be_index_by_name(name) is True, "选基页 19 词口径应认它是指数基金"

    # ② invest_type 缺失 → 榜单口径把它丢了，名称口径仍然认
    assert frb.is_index_fund({"invest_type": None}) is False
    assert frb.is_index_fund({}) is False
    assert _be_index_by_name(name) is True

    # ③ 把 ② 的分叉本身写死：这是本用例存在的理由
    assert frb.is_index_fund({"invest_type": None}) != _be_index_by_name(name)


# ==========================================================================
# 3. 源码级结构守卫：不让第 3 套口径悄悄长出来
# ==========================================================================

def test_frontend_etf_caliber_is_defined_exactly_once():
    """前端只允许有一处 ETF 判据。

    上一轮审计发现全仓散落 12 处「是否 ETF / 场内 / 指数」相关判据。收敛判据
    是另一件事，但**至少不能继续变多**：谁再在 `_components.js` 里写第二处
    `indexOf('ETF')`，这里立刻红。
    """
    src = COMPONENTS.read_text(encoding="utf-8")
    hits = src.count("indexOf('ETF')")
    assert hits == 1, (
        f"_components.js 里出现了 {hits} 处 indexOf('ETF') —— "
        "前端 ETF 判据必须只有 _classifyFundChannel 一处"
    )
    assert src.count("function _classifyFundChannel") == 1, (
        "_classifyFundChannel 必须只定义一次"
    )
    # 判据必须仍然是数据驱动：不许退回猜基金代码数字段
    assert "purchase_status" in src, "分类器没有把「场内交易」作为渠道状态判据"


def test_backend_etf_bucket_caliber_source_lock():
    """锁住 fund_rank_build.py:294 的 etf 篮判据原文。

    `_be_etf_bucket()` 是对这一行的逐字复刻；源码一改而复刻没跟上，这条先红，
    从而保证上面的行为级断言不会拿一份过期的副本测出假绿。
    """
    src = FRB_PATH.read_text(encoding="utf-8")
    pattern = r'"etf":\s*\[r for r in ranks_all if "ETF" in \(r\["name"\] or ""\)\]\[:200\]'
    assert re.search(pattern, src) is not None, (
        "fund_rank_build.py 的 etf 篮判据已不是 "
        "``\"ETF\" in (r[\"name\"] or \"\")`` —— 若是有意修改，请同步更新 "
        "_be_etf_bucket() 与本文件的分叉预期"
    )


def test_backend_index_caliber_still_uses_invest_type():
    """榜单的指数口径必须继续走 invest_type，不许退回按 fund_type 匹配。

    `fund_rank_build.py:104-124` 记着这个坑：fund_type 里根本没有含"指数"的
    类别，按它匹配恒为空，且会**静默**空掉很久没人发现。
    """
    frb = _frb()
    assert frb.INDEX_INVEST_TYPES == ("被动指数型", "增强指数型")
    assert frb.is_index_fund({"invest_type": "被动指数型"}) is True
    assert frb.is_index_fund({"invest_type": "增强指数型"}) is True
    assert frb.is_index_fund({"invest_type": "偏股混合型"}) is False

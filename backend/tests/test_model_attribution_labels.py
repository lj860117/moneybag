"""v9.9.19 模型归因收敛测试（防止「页面标签与实际模型不符」回归）

背景
----
全面 Flash 化（v9.9.18）之后，`MODEL_ROUTING["llm_heavy"]` 也解析成
`deepseek-v4-flash`，但 `api/holdings.py` 的 AI 深度体检仍写死
`source="ai_pro"`，前端 `pages/insight-fund.js` 又按 `source` 三分支映射成
「DeepSeek Pro / DeepSeek Flash / 数据摘要」。结果：stage1 基本都成功 →
全量用户看到的都是 "DeepSeek Pro"，而实际跑的是 Flash。

收敛规则（不是新造轮子，是复用 pages/chat.js:229 的既有模式）
------------------------------------------------------------
* `source` 只做**粗粒度枚举**（ai / rules / panel / data_fallback / none …），
  不再承载模型档位信息；
* 具体模型名一律由后端 `model` 字段 + 前端 `_formatModelName()` 渲染。

按字面 ban "pro" 会误伤两处，因此显式白名单：
  * `deepseek`  —— 厂商名。`fund_detail.py` 用它决定显示 🤖 图标，
                   flash 同样是 DeepSeek，这个值本身没错。
  * `v4_sync`   —— `services/fund_monitor.py` 里的「从 V4 旧版持仓同步」
                   溯源标记，跟 LLM 模型无关。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ── 扫描范围 ────────────────────────────────────────────────────────────────
# tests/ 自身要排除（本文件就含有这些字符串字面量）。
ROOT = Path(__file__).resolve().parents[2]

SCAN_DIRS = ("backend", "pages")

# 仓库历史遗留的嵌套垃圾目录（services/services、api/api …）与备份目录，
# 参与扫描会产生大量噪声。
EXCLUDE_PARTS = (
    "__pycache__",
    "node_modules",
    ".git",
    "tests",
    "main_v4_backup",
    "services/services",
    "api/api",
    "infra/infra",
    "domain/domain",
    "use_cases/use_cases",
)
EXCLUDE_SUFFIX = (".bak", ".orig", ".min.js")

# ── 规则 ────────────────────────────────────────────────────────────────────
# 模型档位标识：出现在 source 字段里就是归因失真
BANNED_TIER_MARKERS = ("pro", "flash", "r1", "v3")

# 具体模型 ID：同样禁止出现在 source 里
BANNED_MODEL_IDS = (
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "deepseek-chat",
    "deepseek-reasoner",
    "doubao-seed",
    "doubao-pro",
    "ep-",
    "qwen",
)

# 白名单：厂商名 + 非 LLM 溯源标记（见文件头说明）
ALLOWED_VALUES = frozenset({"deepseek", "doubao", "v4_sync"})

# 抓 source 字段/分支取值的正则（Python dict 字面量、赋值；JS 比较、对象字面量）
SOURCE_PATTERNS = (
    re.compile(r"""["']source["']\s*:\s*["']([^"']+)["']"""),          # "source": "x"
    re.compile(r"""["']source["']\s*=\s*["']([^"']+)["']"""),          # "source" = "x"
    re.compile(r"""\bsource\s*===\s*["']([^"']+)["']"""),              # source === 'x'
    re.compile(r"""\bsource\s*==\s*["']([^"']+)["']"""),               # source == 'x'
    re.compile(r"""\["']source["']\]\s*===\s*["']([^"']+)["']"""),     # d['source'] === 'x'
)


def _iter_source_files():
    """产出参与扫描的 .py / .js 文件路径。"""
    for dirname in SCAN_DIRS:
        base = ROOT / dirname
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in (".py", ".js"):
                continue
            rel = path.as_posix()
            if any(part in rel for part in EXCLUDE_PARTS):
                continue
            if rel.endswith(EXCLUDE_SUFFIX):
                continue
            yield path


def _collect_source_values():
    """返回 [(相对路径, 行号, source 取值)]。"""
    found = []
    for path in _iter_source_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pattern in SOURCE_PATTERNS:
                for value in pattern.findall(line):
                    found.append((path.relative_to(ROOT).as_posix(), lineno, value))
    return found


def _violation_reason(value: str) -> str:
    """返回违规原因；合规返回空串。"""
    low = value.strip().lower()
    if low in ALLOWED_VALUES:
        return ""
    for marker in BANNED_TIER_MARKERS:
        if marker in low:
            return "档位标识 %r" % marker
    for model_id in BANNED_MODEL_IDS:
        if model_id in low:
            return "具体模型 ID %r" % model_id
    return ""


# ── 用例 1：全仓扫描，source 字段不得承载模型档位 ──────────────────────────
def test_source_field_must_not_carry_model_tier():
    violations = []
    for rel_path, lineno, value in _collect_source_values():
        reason = _violation_reason(value)
        if reason:
            violations.append("%s:%d  source=%r  ← %s" % (rel_path, lineno, value, reason))

    assert not violations, (
        "source 字段里出现了模型档位/模型 ID，会导致前端标签与实际模型不符。\n"
        "收敛规则：source 只保留粗粒度枚举，模型名走 model 字段渲染。\n"
        "白名单：deepseek（厂商名）、v4_sync（非 LLM 溯源标记）。\n"
        + "\n".join(violations)
    )


def test_scan_actually_covered_files():
    """防呆：扫描器挂了（0 文件）时上面的用例会「假通过」。"""
    files = list(_iter_source_files())
    assert len(files) > 50, "扫描到的文件数异常少（%d），正则或路径规则可能失效" % len(files)
    values = _collect_source_values()
    assert len(values) > 20, "抓到的 source 取值异常少（%d），正则可能失效" % len(values)


# ── 用例 2：AI 体检返回的 source 必须是粗粒度 ai ───────────────────────────
_FAKE_FUNDS = [
    {
        "name": "测试基金A",
        "code": "000001",
        "nav_percentile": 62.0,
        "industry_tag": "科技",
        "returns": {"1y": 12.3, "3m": 4.5},
        "potential": {"level": "高"},
    },
    {
        "name": "测试基金B",
        "code": "000002",
        "nav_percentile": 38.0,
        "industry_tag": "消费",
        "returns": {"1y": -3.2, "3m": 1.1},
        "potential": None,
    },
]


class _FakeGateway:
    """替身 LLMGateway：记录调用次数，返回可配置的 model / content。"""

    def __init__(self, content: str = "体检结论：测试数据", model: str = "deepseek-v4-flash",
                 fallback_used: bool = False) -> None:
        self.content = content
        self.model = model
        self.fallback_used = fallback_used
        self.calls: list[dict] = []

    @classmethod
    def instance(cls) -> "_FakeGateway":
        return cls._CURRENT

    _CURRENT: "_FakeGateway"

    def call_sync(self, *args, **kwargs) -> dict:
        self.calls.append(kwargs)
        if not self.content:
            return {"content": "", "model": "", "fallback_used": False}
        return {
            "content": self.content,
            "model": self.model,
            "fallback_used": self.fallback_used,
        }


def _run_checkup(monkeypatch, content="体检结论：测试数据", model="deepseek-v4-flash"):
    """把 _compute_ai_checkup 的依赖全部打桩后执行，返回 (结果, 打桩网关)。"""
    from api import holdings
    from infra.llm import gateway as gateway_mod

    fake = _FakeGateway(content=content, model=model)
    _FakeGateway._CURRENT = fake

    monkeypatch.setattr(holdings, "_compute_holdings_enrich", lambda uid: {"funds": _FAKE_FUNDS})
    monkeypatch.setattr(holdings, "_build_market_context", lambda: "上证指数 3200 (+0.50%)")
    monkeypatch.setattr(gateway_mod, "LLMGateway", _FakeGateway)

    return holdings._compute_ai_checkup("test_user"), fake


def test_ai_checkup_source_is_coarse_grained(monkeypatch):
    """核心回归：source 必须是 "ai"，不能是 ai_pro / ai_flash 这类档位值。"""
    result, _ = _run_checkup(monkeypatch)

    assert result["status"] == "ok"
    assert result["source"] == "ai"
    assert _violation_reason(result["source"]) == "", (
        "source=%r 仍然带模型档位，前端会渲染出错误的模型名" % result["source"]
    )


def test_ai_checkup_model_passthrough_and_fallback_flag(monkeypatch):
    """model 字段原样透传（前端靠它渲染标签），并透出 fallback_used。"""
    result, _ = _run_checkup(monkeypatch, model="deepseek-v4-flash")

    assert result["model"] == "deepseek-v4-flash"
    assert result["fallback_used"] is False


def test_ai_checkup_no_duplicate_second_stage_call(monkeypatch):
    """P2-2：stage1 失败后不得再打第二段（同候选链，只会重复烧 token）。"""
    result, fake = _run_checkup(monkeypatch, content="", model="deepseek-v4-flash")

    # stage1 空内容 → 应该直接落到纯数据兜底，而不是再调一次 LLM
    assert result["status"] == "data_only"
    assert len(fake.calls) == 1, (
        "候選链已由 gateway 统一降级，业务层不该再手写第二段；"
        "实测调用了 %d 次" % len(fake.calls)
    )


def test_ai_checkup_fallback_module_string_removed():
    """P2-2：ai_checkup_fallback 这个 module 名随 stage2 一起删除，不留悬挂引用。"""
    import inspect

    from api import holdings

    src = inspect.getsource(holdings._compute_ai_checkup)
    assert "ai_checkup_fallback" not in src
    # 整个函数体里只允许有一次 LLM 调用
    assert src.count("gw.call_sync(") == 1, (
        "_compute_ai_checkup 里出现 %d 次 gw.call_sync，怀疑第二段降级又回来了"
        % src.count("gw.call_sync(")
    )


# ── 用例 3：前端不得再按 source 分支硬编码档位名 ───────────────────────────
def test_insight_fund_renders_model_from_model_field():
    ins_fund = ROOT / "pages" / "insight-fund.js"
    assert ins_fund.exists(), "pages/insight-fund.js 不存在，路径变了要同步改这个用例"

    text = ins_fund.read_text(encoding="utf-8", errors="ignore")

    assert "ai_pro" not in text, "insight-fund.js 仍在消费 ai_pro，档位硬编码回来了"
    assert "ai_flash" not in text, "insight-fund.js 仍在消费 ai_flash，档位硬编码回来了"
    assert "d.model" in text, "前端必须改用后端返回的 model 字段渲染模型名"

    # loading 文案与入口按钮都不该再写死档位名
    assert "DeepSeek Pro" not in text, "insight-fund.js 仍有硬编码的 'DeepSeek Pro'"


# =============================================================================
# P2-3：渲染 usdcny 的地方必须处理 proxy（离岸 CNH 兜底不得冒充在岸 USD/CNY）
# =============================================================================
# 背景
# ----
# 在岸主源（AKShare fx_spot_quote）不可用时，`get_forex_data()` 会落到 Tushare
# 离岸 USD/CNH，并在 usdcny 里置 `proxy: True`。渲染方若不判这个标记，就会把
# 离岸价当在岸价报出去 —— 和 dxy_proxy 拿 USDCNY 充数是同一类语义错误。
#
# `services/global_market.py` 的 snapshot summary 尤其危险：它会被
# `api/shared_helpers.py` 原样拼进 LLM 上下文，模型会照抄措辞写进结论再转述
# 给用户；`pages/insight.js` 则是直接给用户看的界面文字。
#
# 检测方式：同一行内既出现「美元/人民币」标签又出现 rate 取值 → 认定为渲染点；
# 再要求其 ±8 行内出现 proxy 判断。负向对照：修复前的
# global_market.py:564 / insight.js:370 都会被这条规则抓出来。

FX_RENDER_WINDOW = 8

# 已知合规的参照实现：晨报里判了 proxy，用来防「扫描器自己失效」
FX_REFERENCE_FILE = "backend/scripts/night_worker.py"
FX_OFFSHORE_MARKER = "离岸CNH兜底"


class _NoCache:
    """替身缓存：让 get_global_snapshot() 每次都真的重算，不受 600s TTL 影响。"""

    def get(self, *args, **kwargs):
        return None

    def set(self, *args, **kwargs):
        return None


def _is_comment_line(line: str) -> bool:
    """注释行判定（用于把「注释里提到了 proxy」从合规证据里剔掉）。

    这条是 v9.9.20 补的：加了 as_of 之后我发现，±8 行窗口里只要注释里写了
    「as_of 时点标注」，扫描就判合规 —— 负向对照（把模板变量删掉）居然照样
    全绿。注释不是代码，不能当证据，否则这条规则形同虚设。
    """
    stripped = line.strip()
    return stripped.startswith(("#", "//", "/*", "*", '"""', "'''"))


def _collect_fx_render_sites():
    """返回 [(相对路径, 行号, 代码里是否判 proxy, 代码里是否带 as_of)]。

    两个标记一起扫，是为了守住 v9.9.20 补的时点标注：P2-3 的教训就是「一处防了
    漏一处」，所以新增 as_of 时直接复用同一套扫描，不另起一个近似规则。

    窗口内只看**代码行**，注释行不算数（见 _is_comment_line）。
    """
    sites = []
    for path in _iter_source_files():
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        rel = path.relative_to(ROOT).as_posix()
        for idx, line in enumerate(lines):
            # 「渲染点」= 同一行里既有中文标签又真的在格式化 rate
            if "美元/人民币" not in line or "rate" not in line:
                continue
            lo = max(0, idx - FX_RENDER_WINDOW)
            hi = min(len(lines), idx + FX_RENDER_WINDOW + 1)
            code_window = [p for p in lines[lo:hi] if not _is_comment_line(p)]
            has_proxy = any("proxy" in probe for probe in code_window)
            has_as_of = any("as_of" in probe for probe in code_window)
            sites.append((rel, idx + 1, has_proxy, has_as_of))
    return sites


def _snapshot_summary(monkeypatch, usdcny: dict) -> str:
    """把 get_global_snapshot 的数据依赖全部打桩，返回生成的 summary。"""
    from services import global_market as gm

    monkeypatch.setattr(gm, "_global_cache", _NoCache())
    monkeypatch.setattr(gm, "get_us_indices", lambda: {"available": False})
    monkeypatch.setattr(
        gm,
        "get_forex_data",
        lambda: {
            "usdcny": usdcny,
            "dxy_proxy": None,
            "available": usdcny is not None,
            "degraded": bool(usdcny.get("proxy")),
            "degraded_reason": "在岸主源不可用" if usdcny.get("proxy") else "",
        },
    )
    monkeypatch.setattr(gm, "get_fed_rate", lambda: {"available": False})
    monkeypatch.setattr(gm, "get_global_pe", lambda: {"available": False})

    return gm.get_global_snapshot()["summary"]


def test_global_snapshot_summary_marks_offshore_proxy(monkeypatch):
    """P2-3 核心：离岸兜底价进 LLM 上下文时必须带币种标记。"""
    summary = _snapshot_summary(
        monkeypatch,
        {
            "rate": 6.7120,
            "name": "USD/CNH(离岸,代理USD/CNY)",
            "source": "tushare",
            "proxy": True,
        },
    )

    assert FX_OFFSHORE_MARKER in summary, (
        "离岸兜底价没有标出币种，模型会把 USD/CNH 当 USD/CNY 写进结论：\n" + summary
    )
    # 不能同时出现「裸」的在岸写法
    assert "美元/人民币: 6.7120" not in summary


def test_global_snapshot_summary_onshore_has_no_offshore_marker(monkeypatch):
    """反向：在岸价不得被误标成离岸（别为了合规把所有价都打上 CNH）。"""
    summary = _snapshot_summary(
        monkeypatch,
        {"rate": 6.7115, "name": "USD/CNY", "source": "akshare", "proxy": False},
    )

    assert "美元/人民币: 6.7115" in summary
    assert FX_OFFSHORE_MARKER not in summary


def test_all_usdcny_render_sites_handle_proxy():
    """防回归：任何新加的 usdcny 渲染点都必须处理 proxy。"""
    sites = _collect_fx_render_sites()

    # 防呆：扫描器挂了会「0 违规」假通过
    assert len(sites) >= 3, "抓到的汇率渲染点异常少（%d），检测规则可能失效" % len(sites)

    # 防呆 2：参照实现必须被识别到且判定为合规，否则说明规则本身就判错了
    ref_sites = [s for s in sites if s[0] == FX_REFERENCE_FILE]
    assert ref_sites, "没抓到参照实现 %s，扫描范围或规则变了" % FX_REFERENCE_FILE
    assert all(ok for _, _, ok, _ in ref_sites), (
        "参照实现 %s 被判为未处理 proxy，检测规则有问题" % FX_REFERENCE_FILE
    )

    violations = [
        "%s:%d" % (rel, lineno)
        for rel, lineno, ok, _ in sites
        if not ok
    ]
    assert not violations, (
        "以下位置渲染美元/人民币却没有处理 proxy，离岸 CNH 兜底价会被当成在岸价报出：\n"
        + "\n".join(violations)
        + "\n修法：proxy 为真时输出「💱 美元/人民币(离岸CNH兜底): {rate}」，"
        "参照 backend/scripts/night_worker.py。"
    )


def test_all_usdcny_render_sites_also_stamp_as_of():
    """v9.9.20：三处渲染点都必须带时点标注，且时点取自后端而不是现取当前时间。

    为什么连「不能现取时间」一起管：这个价可能来自缓存（外汇 300s / 快照 300s /
    API 层还有 4 小时文件缓存），渲染时现取 datetime.now() 会把几小时前的价说成
    「现在」，比不标更误导。所以扫描只认 as_of，不认 now()。
    """
    sites = _collect_fx_render_sites()

    assert len(sites) >= 3, "抓到的汇率渲染点异常少（%d），检测规则可能失效" % len(sites)

    violations = ["%s:%d" % (rel, lineno) for rel, lineno, _, ok in sites if not ok]
    assert not violations, (
        "以下位置渲染美元/人民币却没有标时点，用户无从判断这个价是什么时候的：\n"
        + "\n".join(violations)
        + "\n修法：渲染后端下发的 usdcny.as_of（形如「（截至 09-11 10:58）」），"
        "不要在这里调 datetime.now()/new Date()。"
    )

    # 三处文案必须落在同一个文件集合里，避免出现「晨报标了、前端没标」
    files_with_marker = {
        rel for rel, _, _, ok in sites if ok
    }
    for expected in ("backend/services/global_market.py",
                     "backend/scripts/night_worker.py",
                     "pages/insight.js"):
        assert expected in files_with_marker, (
            "%s 的汇率行没有时点标注，三处渲染点必须一致" % expected
        )


def test_fx_as_of_comes_from_backend_not_render_time(monkeypatch):
    """时点必须是「取数时刻」并随缓存返回，不能是渲染时才生成。"""
    from services import global_market as gm

    frozen = gm._fx_as_of_text()
    # 后端在取数时就把 as_of 写进结果里，缓存命中时返回的仍是同一个值
    summary = _snapshot_summary(
        monkeypatch,
        {"rate": 6.7115, "name": "USD/CNY", "source": "akshare",
         "proxy": False, "as_of": frozen},
    )
    assert frozen in summary, (
        "渲染时没有原样用后端下发的 as_of，时点标注失真：\n" + summary
    )
    # 在岸口径是「截至 MM-DD HH:MM」
    assert frozen.startswith("截至 "), "在岸时点文案口径变了：%r" % frozen
    # 离岸口径是数据源自己的交易日 +「收盘」，不能标成取数时刻
    offshore = gm._fx_as_of_text("20260910")
    assert offshore == "09-10 收盘", "离岸时点应标数据源交易日，实测 %r" % offshore

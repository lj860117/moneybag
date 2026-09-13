"""v9.9.26「假统计防复发」锁定套件。

本项目的铁律（本轮冲刺的核心原则）：

    算不出 / 没数据的地方必须是 ``None`` + 原因说明，绝不能返回占位数值；
    「没数据」也绝不能被伪装成「判断为中性 / 看多」。

本轮刚清理掉一批编造数值（清单见 ``backend/services/stats_registry.py`` 的
docstring）。本文件给这些清理**上锁**，防止复发。四组测试：

  (A) ``FORBIDDEN_REGRESSIONS`` 负向扫描：把每次事故的**可正则化形态**钉死。
      这是「不许再写这样」的清单，不是「曾经出现过」的清单。
  (B) 故障注入：对 (A) 的**每一条正则**断言它能匹配到一条合成正样本。
      没有 (B)，一条写错的正则（永远匹配不到任何东西）会静默变绿，
      整组负向断言就退化成死测试 —— 这是本仓明确踩过的坑。
  (C) 统计数字白名单扫描：生产源码里「收益/胜率/概率/年化…」语境下的百分比
      字面量，必须能在 ``stats_registry`` 里说清来源，否则失败。
  (D) 注册表自检：防止有人用 ``"": ""`` 这类敷衍条目把门焊开，也防止
      「待裁决清单」被当后门无限扩张。

## 三个刻意的设计（踩过坑，别改回去）

1. **必须先剥注释与 docstring 再断言**。
   本仓已 4 次因「注释里引用了被禁字面量」而误报。注释和 docstring 必须能
   引用历史写法来解释「为什么不能这么写」。断言目标是**实际代码**，不是文档措辞。

2. **不直接用 ``_code_only()`` 做逐行扫描，而是复用它的两个零件**
   （``_docstring_spans`` / ``_js_code_only``）拼出**行号保持**的
   ``_iter_code_lines()``。原因：``_code_only()`` 会把多行 docstring 内部的
   换行一起抹成空格 —— 实测 ``api/shared_helpers.py`` 整体漂移 57 行、
   ``pages/_components.js`` 漂移 54 行。内容没丢（已用 AST 逐个字符串常量
   源码片段核对过：0 处被吞），但**行号全错**，失败信息会指向无关的行，
   「未登记清单」也没法回溯。这里只替换「抹白」这一步，剥注释的规则完全复用。

3. **前端不能只信整文件字符级剥离**。``_js_code_only()`` 是保守的字符级实现，
   遇到「正则字面量里带引号」会失步，之后整片注释被当成代码（实测
   ``pages/analysis.js`` 的一行 ``// 黄金长期年化 5-8%`` 就漏了出来）。
   所以 JS 侧按行剥离，并用一个 ``/* */`` 跨行状态机补齐多行块注释。

全部离线：只读文件 + 调用纯函数，不发网络请求、不调 LLM、不写盘。
"""
from __future__ import annotations

import importlib.util
import io
import re
import sys
import tokenize
from functools import lru_cache
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
_REPO = _BACKEND.parent

from services.stats_registry import (  # noqa: E402
    SITE_JUSTIFICATIONS,
    STATS_SOURCES,
    UNVERIFIED_REPORTED,
    explain,
    is_justified,
    is_registered,
    unverified_max,
    unverified_reason,
)

# ============================================================
# 复用已有的剥注释助手（不重写）
#   来源：backend/tests/test_canned_reply_no_fake_confidence.py
# ============================================================
_HELPER_TEST = Path(__file__).resolve().parent / "test_canned_reply_no_fake_confidence.py"
if str(_HELPER_TEST.parent) not in sys.path:
    sys.path.insert(0, str(_HELPER_TEST.parent))
_spec = importlib.util.spec_from_file_location(
    "_mb_canned_reply_helpers", _HELPER_TEST)
_helpers = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _helpers
_spec.loader.exec_module(_helpers)
_docstring_spans = _helpers._docstring_spans
_js_code_only = _helpers._js_code_only


# ============================================================
# 扫描范围
# ============================================================
_EXCLUDED = ("backend/tests/", "backend/prompts/versions/", "__pycache__")


def _scan_targets(scope: str = ""):
    """生产源码扫描范围：backend/**/*.py（排除测试/版本化 prompt）+ 根 app.js + pages/**.js。"""
    for p in sorted(_BACKEND.rglob("*.py")):
        rel = p.relative_to(_REPO).as_posix()
        if any(x in rel for x in _EXCLUDED):
            continue
        if scope and not rel.startswith(scope):
            continue
        yield p
    for p in [_REPO / "app.js"] + sorted((_REPO / "pages").rglob("*.js")):
        rel = p.relative_to(_REPO).as_posix()
        if scope and not rel.startswith(scope):
            continue
        yield p


def _py_code_lines(src: str):
    """Python: 逐行给出 (行号, 该行代码)，注释截断、docstring 整行抹白，行号不变。"""
    doc_lines: set[int] = set()
    for (sr, _sc), (er, _ec) in _docstring_spans(src):
        for ln in range(sr, er + 1):
            doc_lines.add(ln)

    comment_col: dict[int, int] = {}
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            row, col = tok.start
            if row not in comment_col or col < comment_col[row]:
                comment_col[row] = col

    for lineno, line in enumerate(src.splitlines(), 1):
        if lineno in doc_lines:
            # docstring 容许引用历史写法，整行抹白（docstring 独占整行）
            yield lineno, " " * len(line)
            continue
        col = comment_col.get(lineno)
        yield lineno, (line[:col] if col is not None else line)


def _js_code_lines(src: str):
    """JS: 逐行给出 (行号, 该行代码)。按行复用 `_js_code_only`，再补 `/* */` 跨行状态。"""
    in_block = False
    for lineno, raw_line in enumerate(src.splitlines(), 1):
        line = raw_line
        if in_block:
            end = line.find("*/")
            if end < 0:
                yield lineno, ""
                continue
            line = " " * (end + 2) + line[end + 2:]
            in_block = False
        # 该行是否开启了一个未闭合的块注释（计数法；等价于「多出来的一个 /*」）
        if line.count("/*") > line.count("*/"):
            in_block = True
        yield lineno, _js_code_only(line)


@lru_cache(maxsize=None)
def _iter_code_lines(path_str: str) -> tuple:
    """返回 ((行号, 代码行), ...)；带缓存，避免 18 条规则 × 上百文件重复 AST/tokenize。"""
    path = Path(path_str)
    src = path.read_text(encoding="utf-8")
    gen = _js_code_lines(src) if path.suffix == ".js" else _py_code_lines(src)
    return tuple(gen)


def _code_lines(path: Path) -> tuple:
    return _iter_code_lines(str(path))


# ============================================================
# (A) 禁止回归模式清单
#    每项 = (正则, 人话说明, 作用域)
#    作用域 = ""             -> 全仓禁止（扫描 backend/**、app.js、pages/**.js）
#    作用域 = "backend/api/" -> 只在这个路径前缀下禁止（该形态在别处可能是合法用法）
# ============================================================
FORBIDDEN_REGRESSIONS: list[tuple[str, str, str]] = [
    # ---- 1. 规则回复 / prompt 模板里写死的置信度 ----
    (
        r'["\']confidence["\']\s*:\s*0\.\d',
        "规则回复 / 模板里写死的置信度小数（0.8~0.95）：规则回答是**确定性**的，"
        "不存在「有多大概率对」；OCR 模板也不许给示例数值诱导模型照抄。"
        "契约是显式布尔 deterministic=True / source=\"rule_engine\"。",
        "",
    ),
    (
        r'["\']confidence["\']\s*:\s*0\.95',
        "prompt 模板里教模型输出固定置信度 0.95（会诱导模型照抄高分）。",
        "",
    ),
    # ---- 2. 恒真闸门：拿编造的 confidence 做快速通道判断 ----
    (
        r'confidence["\']\]\s*(?:>=|>)\s*0\.\d',
        "拿规则回答里编造的 confidence 做 >= 0.7 快速通道闸门：所有取值恒 >= 0.7，"
        "该条件永远为真 = 等于没有闸门。应改用 rule_result.get(\"deterministic\")。",
        "",
    ),
    (
        r'["\']confidence["\']\s*:\s*(?:50|55)\s*[,}]',
        "把「缺失的置信度」写成 50 / 55 —— 50 是闸门阈值、55 是 trend 门槛，"
        "等价于宣称「刚好达标」，正是铁律禁止的「把没数据伪装成够用」。",
        "backend/api/",
    ),
    (
        r'get\(\s*["\']confidence["\']\s*,\s*(?:50|55)\b',
        "get(\"confidence\", 50/55)：用闸门阈值当兜底默认值。",
        "backend/api/",
    ),
    (
        r'get\(\s*["\']confidence["\']\s*,\s*0\.[1-9]',
        "get(\"confidence\", 0.x)：用一个小数当兜底默认值，凭空造出一份置信度。",
        "",
    ),
    # ---- 3. trend_confidence 的数字兜底 ----
    (
        r'trend_confidence["\']\s*,\s*-?\d',
        "get(\"trend_confidence\", <数字>)：默认 55 = 刚过门槛，把「缺失」伪装成"
        "「够用」；且键存在但值为 None 时 .get 返回 None，会在 `None < 50` 处"
        "直接 TypeError。必须走 services/signal.py:normalize_trend_confidence()。",
        "",
    ),
    # ---- 4. 前端同形态兜底 ----
    (
        r'confidence\s*(?:\|\||\?\?)\s*(?:50|55)\b',
        "JS 里 `confidence || 50`：50 是闸门阈值 → 伪造出「刚好达标」的置信度。",
        "",
    ),
    (
        r'\bconfidence\s*\|\|\s*0\.\d',
        "JS 里 `confidence || 0.x`：兜底出一份不存在的置信度。"
        "（注意 `|| 0` 是诚实的「无数据」，不在禁止之列。）",
        "",
    ),
    (
        r'advantage\s*\|\|\s*0',
        "`c.advantage || 0`：回测优势缺失时兜底成 0 → 把「不知道」渲染成「多赚 0%」。",
        "",
    ),
    # ---- 5. 无出处的收益 / 胜率 / 概率断言 ----
    (
        r'多赚[^"\'\n]{0,10}?\d+(?:\.\d+)?%',
        "「多赚 X%」类无出处收益断言（同一句在 shared_helpers.py / signals.py / "
        "quiz.js 各存一份副本——只改一处等于没改）。",
        "",
    ),
    (
        r'34\.6\s*%',
        "v9.9.24 清掉的「定投综合准确率 34.6%」——无回测支撑。",
        "",
    ),
    (
        r'3\.7\s*%',
        "v9.9.24 清掉的「双因子定投超额 +3.7%」——无回测支撑。",
        "",
    ),
    (
        r'赚钱概率\s*[>≥]\s*\d',
        "「3年赚钱概率>85%」类无出处概率断言。",
        "",
    ),
    (
        r'历史胜率高|买入历史胜率',
        "「市场低估+恐惧时买入历史胜率高」——无出处的胜率断言（weekend_push.py）。",
        "",
    ),
    (
        r'0\.25\s*,\s*0\.12\s*,\s*-0\.15',
        "services/portfolio.py 里硬编码的 returns {0.25, 0.12, -0.15}。",
        "",
    ),
    # ---- 6. `or` / `||` 兜底把合法的 0 变成编造值 ----
    (
        r'\bpct\b["\'\)\]]*\s+or\s+-?\d',
        "Python 里 `pct or 15`（含 `x.get(\"pct\") or 15` 形态）兜底："
        "`0 or 15` 会得到 15，把合法的 0 变成编造值。",
        "",
    ),
    (
        r'\bpct\b["\'\)\]]*\s*\|\|\s*-?\d',
        "JS 里 `pct || 15` 类兜底：0 会被吃掉，变成编造值。",
        "",
    ),
]
_RULE_IDS = [
    "conf_numeric_literal",
    "conf_template_095",
    "conf_truthy_gate",
    "conf_int_default_api",
    "conf_get_int_default_api",
    "conf_get_fraction_default",
    "trend_confidence_numeric_default",
    "js_conf_fallback_threshold",
    "js_conf_fallback_fraction",
    "js_advantage_fallback_zero",
    "copy_extra_gain_percent",
    "copy_346_accuracy",
    "copy_37_excess",
    "copy_win_probability_gt",
    "copy_hist_winrate",
    "hardcoded_returns_vector",
    "py_pct_or_fallback",
    "js_pct_or_fallback",
]
assert len(_RULE_IDS) == len(FORBIDDEN_REGRESSIONS), "规则与 id 数量必须一一对应"

# ============================================================
# (B) 故障注入样本：每条正则必须有能匹配上的合成正样本
# ============================================================
_POSITIVE_SAMPLES: dict[str, str] = {
    r'["\']confidence["\']\s*:\s*0\.\d':
        '    return {"text": text, "confidence": 0.8}',
    r'["\']confidence["\']\s*:\s*0\.95':
        '  模板要求：请只返回 {"confidence": 0.95, "text": "..."}',
    r'confidence["\']\]\s*(?:>=|>)\s*0\.\d':
        'if rule_result["confidence"] >= 0.7:',
    r'["\']confidence["\']\s*:\s*(?:50|55)\s*[,}]':
        '    {"confidence": 55, "text": "x"}',
    r'get\(\s*["\']confidence["\']\s*,\s*(?:50|55)\b':
        'conf = result.get("confidence", 55)',
    r'get\(\s*["\']confidence["\']\s*,\s*0\.[1-9]':
        'conf = payload.get("confidence", 0.9)',
    r'trend_confidence["\']\s*,\s*-?\d':
        't = result.get("trend_confidence", 55)',
    r'confidence\s*(?:\|\||\?\?)\s*(?:50|55)\b':
        'const conf = d.confidence || 50;',
    r'\bconfidence\s*\|\|\s*0\.\d':
        'const c = d.confidence || 0.5;',
    r'advantage\s*\|\|\s*0':
        'const adv = c.advantage || 0;',
    r'多赚[^"\'\n]{0,10}?\d+(?:\.\d+)?%':
        '💡 长期能比固定定投多赚15-20%',
    r'34\.6\s*%':
        '综合回测准确率 34.6%',
    r'3\.7\s*%':
        '双因子定投超额 +3.7%',
    r'赚钱概率\s*[>≥]\s*\d':
        '持有 3 年赚钱概率>85%',
    r'历史胜率高|买入历史胜率':
        '市场低估+恐惧时买入历史胜率高',
    r'0\.25\s*,\s*0\.12\s*,\s*-0\.15':
        'returns = [0.25, 0.12, -0.15]',
    r'\bpct\b["\'\)\]]*\s+or\s+-?\d':
        'ratio = item.get("pct") or 15',
    r'\bpct\b["\'\)\]]*\s*\|\|\s*-?\d':
        'const pct = p.pct || 5;',
}


# ============================================================
# (C) 申报语境里的统计字面量
# ============================================================
# 「申报语境」= 断言性的收益/胜率/概率/收益能力，而不是「解释一个概念」的教学文案。
# 刻意用的是一组**断言性**关键词（而不是 spec 里更宽的 收益/回撤/占比 全量）：
#   宽口径在本仓会命中 53 行，其中绝大多数是教学/帮助文案里的示例数字
#   （「回撤 30% 是什么意思」「估值百分位怎么看」）。把 53 行都登记进白名单，
#   等于把门焊开；窄口径只剩 15 行，每一条都能逐条说清或逐条上报。
# 宽口径的完整结果已写进交付报告。
_CLAIM_CONTEXT = re.compile(
    r"胜率|赚钱概率|盈利概率|收益概率|上涨概率|下跌概率|能多赚|多赚|年化"
    r"|超额|大概率|命中率|准确率|覆盖率|胜算"
)
_PERCENT = re.compile(r"\d+(?:\.\d+)?%")
# 0% 只有在**同行明确说明「还没有数据」**时才成立（诚实缺省）；这是铁律的正面用法。
_NO_DATA_MARKER = re.compile(r"无|没有|尚未|还没|待|缺|暂|观察期")


def _claim_context_hits() -> list[tuple[str, int, str, str]]:
    """返回 [(相对路径, 行号, 字面量, 该行代码), ...]。

    白名单文件 ``services/stats_registry.py`` 本身不参与 (C) 扫描：它按定义
    就是「字面量 + 理由」的清单（``("pages/analysis.js", "50%"): "...年化 50%..."``），
    扫它只会自己命中自己。它仍然参与 (A) —— 编造形态不许藏在白名单文件里。
    """
    hits: list[tuple[str, int, str, str]] = []
    for path in _scan_targets():
        rel = path.relative_to(_REPO).as_posix()
        if rel == "backend/services/stats_registry.py":
            continue
        for lineno, text in _code_lines(path):
            if not _PERCENT.search(text):
                continue
            if not _CLAIM_CONTEXT.search(text):
                continue
            for literal in _PERCENT.findall(text):
                hits.append((rel, lineno, literal, text.strip()))
    return hits


# ============================================================
# (A) 负向断言
# ============================================================

@pytest.mark.parametrize(
    "pattern,reason,scope", FORBIDDEN_REGRESSIONS, ids=_RULE_IDS)
def test_forbidden_regression_patterns_are_absent(pattern, reason, scope):
    """剥掉注释/docstring 后，生产源码里不许再出现这些编造数值形态。"""
    rx = re.compile(pattern)
    offenders: list[str] = []
    for path in _scan_targets(scope):
        rel = path.relative_to(_REPO).as_posix()
        for lineno, text in _code_lines(path):
            if rx.search(text):
                offenders.append(f"{rel}:{lineno}: {text.strip()[:150]}")
    assert not offenders, (
        f"禁止回归模式命中：{reason}\n"
        f"正则：{pattern!r}  作用域：{scope or '全仓'}\n"
        + "\n".join(f"  - {o}" for o in offenders[:20])
    )


def test_comment_and_docstring_stripper_does_not_eat_real_code():
    """自检：剥注释既不能吃掉真实代码，也不能漏掉注释。

    * 不能吃掉真实代码：否则 (A) 的负向断言会因为「看不见」而静默变绿。
    * 不能漏掉注释：本仓已有 4 次「注释里引用被禁字面量」的误报，
      注释必须能被剥掉，否则给人解释历史的注释会反过来把测试搞红。
    """
    chat = _BACKEND / "api" / "chat.py"
    joined = "\n".join(text for _ln, text in _code_lines(chat))
    assert 'rule_result.get("deterministic")' in joined, (
        "剥注释把 chat.py 的关键代码吃掉了 —— (A) 的负向断言会因此静默变绿")

    signals = _BACKEND / "api" / "signals.py"
    raw = signals.read_text(encoding="utf-8")
    assert "v9.9.24" in raw, "哨兵注释不见了，本测试需要更新"
    stripped = "\n".join(text for _ln, text in _code_lines(signals))
    assert "v9.9.24" not in stripped, (
        "signals.py 的注释没有被剥离 —— 注释里引用历史写法会把 (A) 误报成红")


def test_trend_panel_still_gated_by_confidence_sufficient_flag():
    """前端趋势面板必须继续看闸门标志，不许无条件渲染方向评分。"""
    src = (_REPO / "pages" / "_components.js").read_text(encoding="utf-8")
    assert "trend_confidence_sufficient" in src, (
        "pages/_components.js 不再引用 trend_confidence_sufficient —— "
        "趋势方向评分又变成无条件渲染了（缺失被当成判断成立）")


# ============================================================
# (B) 故障注入
# ============================================================

def test_every_forbidden_rule_has_a_positive_sample():
    """每条正则都必须配有合成正样本；否则 (B) 无法证明它有效。"""
    missing = [p for p, _, _ in FORBIDDEN_REGRESSIONS
               if p not in _POSITIVE_SAMPLES]
    assert not missing, (
        "以下正则没有故障注入样本，无法证明它匹配得到东西：\n"
        + "\n".join(f"  - {m!r}" for m in missing))
    extra = [p for p in _POSITIVE_SAMPLES
             if p not in {p for p, _, _ in FORBIDDEN_REGRESSIONS}]
    assert not extra, f"故障注入样本里有已不存在的规则：{extra}"


@pytest.mark.parametrize(
    "pattern,reason,scope", FORBIDDEN_REGRESSIONS, ids=_RULE_IDS)
def test_forbidden_rule_matches_synthetic_positive(pattern, reason, scope):
    """故障注入：每条正则必须能匹配到它的合成正样本。

    这条测试是 (A) 的「有效性证明」。没有它，一条永远匹配不到东西的正则
    （比如引号写错、转义写错）会让 (A) 永远为绿 —— 死测试。
    """
    sample = _POSITIVE_SAMPLES[pattern]
    assert re.search(pattern, sample), (
        f"正则匹配不到自己的合成正样本，说明规则已失效（(A) 会静默变绿）：\n"
        f"  正则：{pattern!r}\n  样本：{sample!r}\n  用途：{reason}")


@pytest.mark.parametrize("pattern,reason,scope", FORBIDDEN_REGRESSIONS, ids=_RULE_IDS)
def test_forbidden_rule_does_not_match_clean_code(pattern, reason, scope):
    """反向注入：正则不应该在「诚实写法」上误报（避免闸门太紧把人逼去绕过）。"""
    honest = (
        'rule_result.get("deterministic")\n'
        'trend_confidence=normalize_trend_confidence(result.get("trend_confidence"))\n'
        'const conf = d.confidence || 0;\n'
        'const adv = (typeof c.advantage === "number") ? c.advantage : null;\n'
        'ratio = item.get("pct")\n'
        '如果数据缺失，返回 None 并说明原因。\n'
    )
    assert not re.search(pattern, honest), (
        f"正则误伤了诚实写法：{pattern!r}（{reason}）")


# ============================================================
# (C) 统计数字白名单扫描
# ============================================================

def test_claim_context_stats_are_registered_or_site_justified():
    """申报语境里写死的百分比，必须能在注册表里说清来源（或显式列为待裁决）。"""
    unresolved: list[str] = []
    for rel, lineno, literal, text in _claim_context_hits():
        if is_justified(rel, literal):
            continue
        # 诚实缺省：0% 同行说明了「还没有数据」
        if literal.rstrip("%") in ("0", "0.0") and _NO_DATA_MARKER.search(text):
            continue
        if unverified_reason(rel, literal):
            continue
        unresolved.append(f"{rel}:{lineno}: {literal} :: {text[:150]}")
    assert not unresolved, (
        "以下百分比字面量出现在「收益/胜率/概率/年化」语境里，但没有可核查的来源。\n"
        "按铁律：算不出就返回 None + 原因；确属正当常量才登记进\n"
        "backend/services/stats_registry.py 的 STATS_SOURCES。\n"
        + "\n".join(f"  - {u}" for u in unresolved)
    )


def test_unverified_reported_list_is_honest_and_self_cleaning():
    """「待裁决清单」不是白名单：必须真的还能扫到、必须有出处说明、必须不重叠。"""
    assert len(UNVERIFIED_REPORTED) <= unverified_max(), (
        f"待裁决清单已膨胀到 {len(UNVERIFIED_REPORTED)} 条（上限 {unverified_max()}），"
        "它正在变成后门。请先裁决旧条目。")
    hit_keys = {(rel, literal) for rel, _ln, literal, _t in _claim_context_hits()}
    for (rel, literal), note in UNVERIFIED_REPORTED.items():
        assert (_REPO / rel).exists(), f"{rel} 不存在，条目已失效"
        assert not is_justified(rel, literal), (
            f"{rel} 的 {literal} 既在待裁决清单又在正当白名单里 —— 不能两头占")
        assert "无出处" in note or "待" in note, (
            f"{rel} 的 {literal} 说明必须写明「无出处/待裁决」，实际：{note!r}")
        assert (rel, literal) in hit_keys, (
            f"待裁决条目 {rel}:{literal} 已经扫不到了 —— 多半是被修好了，"
            "请把它从 UNVERIFIED_REPORTED 里删掉，让清单保持真实")


def test_site_justifications_have_no_stale_entries():
    """站点级登记必须都还真的能扫到 —— 防止「登记了一个早就删掉的数字」的假安全感。"""
    hit_keys = {(rel, literal) for rel, _ln, literal, _t in _claim_context_hits()}
    stale = sorted(k for k in SITE_JUSTIFICATIONS if k not in hit_keys)
    # 允许极少量「已修复但登记未清理」的残留，但不允许大规模失真
    assert len(stale) <= 3, (
        f"站点级登记里有过期条目（扫不到了），请清理：{stale}")


# ============================================================
# (D) 注册表自检
# ============================================================

def test_stats_sources_entries_are_substantive():
    """每条登记都必须非空，且写明来源（防止 `"": ""` 敷衍）。"""
    assert STATS_SOURCES, "白名单为空 —— 要么真没有常量，要么有人清空了它"
    for literal, reason in STATS_SOURCES.items():
        assert str(literal).strip(), "登记的字面量不能为空"
        assert reason and reason.strip(), f"{literal!r} 的理由为空"
        assert ("来源" in reason or "Source" in reason), (
            f"{literal!r} 的理由没写「来源」：{reason!r}")


def test_site_justifications_are_substantive():
    for (rel, literal), reason in SITE_JUSTIFICATIONS.items():
        assert (_REPO / rel).exists(), f"站点登记指向不存在的文件：{rel}"
        assert literal.strip() and reason.strip(), f"{rel}:{literal} 理由为空"
        assert ("来源" in reason or "口径" in reason
                or "定义" in reason or "资料" in reason), (
            f"{rel}:{literal} 的理由说不清来源：{reason!r}")


def test_is_registered_and_explain_behaviour():
    """辅助函数的正反两面：登记项为 True、编造值为 False。"""
    for literal in list(STATS_SOURCES)[:5]:
        assert is_registered(literal) is True
        assert explain(literal) == STATS_SOURCES[literal]
    for bad in ("99.9%", "0.95", "58%", "3年赚钱概率"):
        assert is_registered(bad) is False, f"{bad} 不该被登记"
        assert "未登记" in explain(bad), f"{bad} 的 explain 应明确说未登记"
    # 归一化：带 % 与不带 % 等价
    assert is_registered("365%") == is_registered("365")


def test_unverified_entries_are_not_in_trusted_whitelist():
    """待裁决项绝不能同时被当成「有来源」（防止有人顺手把它塞进白名单）。"""
    for (rel, literal) in UNVERIFIED_REPORTED:
        assert not is_justified(rel, literal), (
            f"{rel}:{literal} 在待裁决清单里，却又被 is_justified 判为有来源 —— "
            "这是把门焊开")

"""v9.9.26 P1-9 收尾：罐头（规则引擎）回答不得携带编造的"置信度"与无据统计。

背景（同一轮里第二次遇到同一形态）：
  `api/shared_helpers.py` 的规则引擎回答 `_rule_based_reply_structured` 有
  21 个 return 点，每一处都硬编码一个 `"confidence": 0.80~0.95`。而唯一的
  消费方 `api/chat.py` 拿它做快速路径闸门（`confidence >= 0.7`）——所有取值
  都 ≥ 0.7，所以这个条件**永远为真**：它既没有筛掉过任何一次回答，也不是任何
  测量值。一个看着像指标的常数，与刚清掉的 34.6% / 85% 属同一形态。

  规则回答的性质是**确定性**的：命中某个意图就查表/实时计算得出答案，不存在
  "有多大概率对"。所以契约改为显式布尔 `deterministic=True`。

本文件同时钉住三处无据/诱导性文案：
  - `scripts/weekend_push.py`「市场低估+恐惧时买入历史胜率高」
  - `api/shared_helpers.py`「智能定投比固定定投长期多赚约 15-20%」
  - OCR 的 JSON 模板教模型输出 `"confidence": 0.95`

## 两个刻意的测试设计（踩过坑，别改回去）

1. **扫源码时必须先剥掉注释与 docstring**（`_code_only`）。
   本轮有 4 次负面控制断言因为「注释里引用了被禁的字面量」而误报
   （`d.confidence||50`、`Math.round(d.confidence||0)`、「历史胜率高」…）。
   注释和 docstring 必须能引用历史写法，否则没法解释"为什么不能这么写"。
   断言目标是**实际代码**，不是文档措辞。

2. **行为探针只选"不取数就返回"的分支**。
   该函数很多分支会真实拉行情（get_index_daily / akshare / 北向资金…）。
   探针一旦踩上去，测试就变成联网测试——这是明确禁止的。契约本身已经被
   `_code_only` 的源码扫描全覆盖（21 个 return 点必须都走 `_rule_reply`），
   行为探针只需证明"命中即带 deterministic"。

全部离线：只读文件与源码 + 调用纯函数分支，不发网络请求、不调 LLM、不写盘。
"""
import ast
import io
import re
import sys
import tokenize
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

_REPO = _BACKEND.parent
_HELPERS = _BACKEND / "api" / "shared_helpers.py"
_CHAT = _BACKEND / "api" / "chat.py"
_WEEKEND = _BACKEND / "scripts" / "weekend_push.py"

from api.shared_helpers import _rule_based_reply_structured, _rule_reply


# ============================================================
# 工具：把源码里的注释与 docstring 抹成空格（保留其余字节与位置）
# ============================================================

def _blank_span(lines: list, start, end) -> None:
    """把 [(行,列), (行,列)] 区间的内容抹成空格（行列均为 1-based 行 / 0-based 列）。"""
    (sr, sc), (er, ec) = start, end
    if sr == er:
        line = lines[sr - 1]
        lines[sr - 1] = line[:sc] + " " * (ec - sc) + line[ec:]
        return
    lines[sr - 1] = lines[sr - 1][:sc] + " " * (len(lines[sr - 1]) - sc)
    for ln in range(sr + 1, er):
        lines[ln - 1] = " " * len(lines[ln - 1])
    lines[er - 1] = " " * ec + lines[er - 1][ec:]


def _docstring_spans(src: str) -> list:
    """找出模块/类/函数级 docstring 的位置区间。"""
    spans = []
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef,
                                ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None) or []
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            v = first.value
            spans.append(((v.lineno, v.col_offset),
                          (v.end_lineno or v.lineno, v.end_col_offset or 0)))
    return spans


def _js_code_only(src: str) -> str:
    """剥离 JS 的 `//` 与 `/* */` 注释（字符串/模板字面量内容原样保留）。

    为什么前端也要剥注释：同一轮里后端已经踩过 4 次「注释引用被禁字面量」
    的误报，前端注释同样是给后人解释历史用的，必须能引用旧写法。
    这是字符级扫描（不做完整 JS 解析）：字符串与模板字面量里的 `//`（如 URL）
    不会被误当注释，代价是正则字面量里的极少数形态可能判断保守——宁可漏剥
    也不要把真实代码剥掉。
    """
    out = []
    i, n = 0, len(src)
    quote = None
    while i < n:
        ch = src[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"`":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _code_only(path: Path) -> str:
    """返回剥掉注释（与 Python 的 docstring）后的源码。

    不生效的内容：字符串/模板字面量**保留**——面向用户的文案本身就是要断言
    的对象（「多赚约」「赚钱概率」正是写在字符串里的）。
    """
    src = path.read_text(encoding="utf-8")
    if path.suffix == ".js":
        return _js_code_only(src)

    lines = src.splitlines(keepends=True)

    for span in _docstring_spans(src):
        _blank_span(lines, span[0], span[1])

    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            _blank_span(lines, tok.start, tok.end)

    return "".join(lines)


# ============================================================
# 结构契约
# ============================================================

def test_rule_reply_has_no_numeric_confidence():
    r = _rule_reply("文本", "dca")
    assert r["deterministic"] is True
    assert "confidence" not in r, "规则回答不该再带 confidence 数字"
    assert r["source"] == "rule_engine"


def test_rule_reply_field_set_is_exactly_four():
    """锁定字段集合：新增字段要显式改这里，避免有人悄悄塞回一个指标。"""
    assert set(_rule_reply("t", "i").keys()) == {
        "text", "intent", "deterministic", "source"}


# ============================================================
# 行为：命中必须带 deterministic（只用不取数的分支）
# ============================================================

@pytest.mark.parametrize("msg", [
    "帮我预测一下目标价",     # safety_refusal：最高优先级硬拒绝，取数前就返回
    "我想设个财务目标",       # operation_goal
    "怎么设止盈线",           # operation_discipline
])
def test_hit_is_deterministic_without_confidence(msg):
    r = _rule_based_reply_structured(msg, "", "")
    assert r is not None, f"探针 {msg!r} 未命中，关键词表可能变了，请更新探针"
    assert r["deterministic"] is True
    assert "confidence" not in r


def test_unmatched_message_returns_none():
    """未命中仍然是 None（契约不变，调用方据此 fall through 到 LLM）。"""
    assert _rule_based_reply_structured("asdfghjkl 随便聊聊", "", "") is None


# ============================================================
# 负面控制：源码不许回退（剥注释后断言）
# ============================================================

_NUMERIC_CONF_RE = re.compile(r'"confidence"\s*:\s*[0-9.]')


def _dict_keys_in_function(src: str, func_name: str) -> list:
    """返回指定函数体内所有 dict 字面量的键值（用于结构化检查，不受注释/字符串干扰）。"""
    tree = ast.parse(src)
    target = None
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == func_name):
            target = node
            break
    assert target is not None, f"找不到函数 {func_name}"
    keys = []
    for node in ast.walk(target):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant):
                    keys.append(k.value)
    return keys


def test_rule_reply_function_carries_no_confidence_key():
    """AST 精确检查：规则回答函数体内任何 dict 都不许有 confidence 键。

    这里刻意不用文本扫描——`api/shared_helpers.py` 的 OCR 路径**合理地**使用
    confidence（值来自模型识别，缺失兜底 0，那是诚实的）。文本扫描会把这种
    合法用法和我自己写的模板说明文字一起误伤，所以把断言收敛到函数作用域。
    """
    src = _HELPERS.read_text(encoding="utf-8")
    keys = _dict_keys_in_function(src, "_rule_based_reply_structured")
    assert "confidence" not in keys, (
        "规则回答函数里又出现了 confidence 键——规则回答是确定性的，"
        "没有概率置信度可言。")
    assert "deterministic" in keys or "return _rule_reply" in src


def test_ocr_template_does_not_start_with_a_bare_number():
    """OCR 模板把 confidence 写成描述而非示例数值（避免诱导模型照抄高分）。"""
    code = _code_only(_HELPERS)
    assert '"confidence": 0.95' not in code
    assert "识别把握度" in code
    # 值位置若以数字开头，会被误读成"示例值"
    assert not _NUMERIC_CONF_RE.search(code), (
        "OCR 模板的 confidence 值位置以数字开头，容易被模型当成示例照抄")


def test_every_rule_reply_goes_through_the_single_constructor():
    code = _code_only(_HELPERS)
    assert 'return {"text": text, "confidence' not in code
    # 22 个 return 点都必须走统一构造入口
    # （22 = 原 21 处 + 本轮新增的「我持有 X 吗」确定性作答分支；新增分支请沿用
    #  _rule_reply，不要各自写 dict 字面量，并同步更新这个数字。）
    assert code.count("return _rule_reply(text,") == 22, (
        "规则回答的构造点数量变了：新增分支请沿用 _rule_reply，"
        "不要各自写 dict 字面量。")


def test_chat_fast_path_does_not_gate_on_fabricated_confidence():
    code = _code_only(_CHAT)
    assert '["confidence"]' not in code, "chat.py 仍在读规则回答的 confidence"
    assert ">= 0.7" not in code
    assert 'rule_result.get("deterministic")' in code


# ============================================================
# 文案：无据统计与诱导性示例不许回流
# ============================================================

@pytest.mark.parametrize("rel,forbidden", [
    # 后端
    ("backend/scripts/weekend_push.py", "历史胜率高"),
    ("backend/scripts/weekend_push.py", "买入历史胜率"),
    ("backend/api/shared_helpers.py", "多赚约"),
    ("backend/api/shared_helpers.py", "长期多赚"),
    ("backend/api/signals.py", "多赚15-20"),
    # 前端：同一句断言的副本（审计发现「多赚15-20%」前后端各有一份）
    ("pages/quiz.js", "赚钱概率>85%"),
    ("pages/quiz.js", "多赚约15-20%"),
    ("pages/chat.js", "多赚2-3%/年"),
])
def test_no_unsubstantiated_statistics_in_copy(rel, forbidden):
    """无出处的统计断言不许出现在面向用户的文案里（前后端一起扫）。

    这条断言的价值在于「副本」：同一句「长期多赚 15-20%」在本轮里先后出现在
    `shared_helpers.py`、`signals.py`、`quiz.js` 三处——只改后端等于没改。
    所以扫描范围必须覆盖 pages/*.js。
    """
    code = _code_only(_REPO / rel)
    assert forbidden not in code, (
        f"{rel} 出现无出处的统计断言 {forbidden!r}——"
        "与 34.6% / 85% 同类，必须删掉或标注来源。")


@pytest.mark.parametrize("rel,expr", [
    ("pages/chat.js", "c.advantage||0"),
    ("pages/chat.js", "c.advantage || 0"),
])
def test_backtest_advantage_missing_is_not_rendered_as_zero(rel, expr):
    """回测优势缺失时不得兜底成 0 —— 那会把「不知道」说成「两者收益一样」。"""
    assert expr not in _code_only(_REPO / rel)

"""单一事实来源守卫 —— 防「同一份契约在多处各自维护」。

## 为什么需要这个测试

2026-09-13 做了一次全局普查，4 个实锤缺陷共享同一个根因：
**同一份契约/清单/口径在多个地方各自维护，没有单一权威位置，也没有机械化守卫。**
清单是手工维护的 → 加文件就漏；口径是复制粘贴的 → 改一处漏一处。

| # | 缺陷 | 权威位置缺失的表现 |
|---|------|------------------|
| 1 | 线上 `sw.js` 冻在 `v9923-cache` | 部署清单里没有 `sw.js`（已由 `test_deploy_asset_coverage.py` 守住） |
| 2 | 线上 `close_review.md` 停在 8/30 旧版 | `backend/prompts/` 不在部署清单（同上） |
| 3 | 全部守卫不生效 | `backend/tests/` 不在任何自动化入口 |
| 4 | 模型同一次调用收到两个矛盾字数指令 | `close_review` 的字数被写了两遍 |

本文件守的是第 4 类：**同一约束的多处重复定义**。判据统一为
「约束只能有一个权威位置，其余位置必须引用而不是复述」。

## 本文件当前覆盖的三组契约

1. **版本号**：`backend/config.py` `APP_VERSION` 是权威；
   `sw.js` 的 `CACHE_NAME`、`index.html` 全部 `?v=` 必须与它一致。
   （此前无任何断言，所以 bump 脚本读 `head -1`、遇到混版会静默只替换一种。）
2. **close_review 字数**：`backend/prompts/close_review.md` 是权威；
   调用方 `backend/scripts/stock_monitor_cron.py` **不得**再复述数字
   —— 它原本写「800 字以内」，与 prompt 的「200-400 字」在同一次 `gw.call_sync`
   里同时进模型（system=prompt 文件，user=调用方 f-string），实测矛盾。
3. **`FUND_SCREEN_STALE_SECONDS`**：`backend/api/signals.py` 与
   `backend/scripts/cache_warmer.py` 两处各写一遍 `72 * 3600`，
   且 `cache_warmer.py:1303` 的注释自称"必须保持一致"——**却没有任何断言**。

## 反「闸门空转」设计

判据全部拆成**纯函数**，便于故障注入直接调用；另配正向对照测试，
确保判据不是「恰好没触发」的绿。解析器失效导致断言真空通过是本套件的头号敌人
（例如版本正则匹配不到任何 `?v=` 时会得到空集合 —— 必须显式断言非空）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"

CONFIG_PY = BACKEND / "config.py"
SW_JS = REPO_ROOT / "sw.js"
INDEX_HTML = REPO_ROOT / "index.html"
CLOSE_REVIEW_PROMPT = BACKEND / "prompts" / "close_review.md"
CLOSE_REVIEW_CALLER = BACKEND / "scripts" / "stock_monitor_cron.py"
SIGNALS_PY = BACKEND / "api" / "signals.py"
CACHE_WARMER_PY = BACKEND / "scripts" / "cache_warmer.py"

# 字数约束的两种写法：区间式（200-400 字）与上限式（800 字以内）
_LENGTH_RANGE_RE = re.compile(r"\d+\s*[-~至]\s*\d+\s*字")
_LENGTH_UPPER_RE = re.compile(r"\d+\s*字(?:以内|内|左右|以上)")


# ==========================================================================
# 通用工具
# ==========================================================================
def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _eval_const(node: ast.expr) -> object | None:
    """安全求值常量表达式：只支持字面量与算术运算，**不执行任何代码**。

    必须支持 BinOp —— 本仓的 `FUND_SCREEN_STALE_SECONDS` 写作 `72 * 3600`，
    只认字面量会让解析器静默返回 None，进而使断言变成"闸门空转"。
    """
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        v = _eval_const(node.operand)
        return -v if isinstance(v, (int, float)) else None
    if isinstance(node, ast.BinOp):
        left, right = _eval_const(node.left), _eval_const(node.right)
        if left is None or right is None:
            return None
        try:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.FloorDiv):
                return left // right
            if isinstance(node.op, ast.Mod):
                return left % right
        except Exception:
            return None
    return None


def _const_value(path: Path, name: str) -> object | None:
    """在**任意作用域**里查找 ``name`` 的赋值并求常量值。

    刻意不限模块级：`cache_warmer.py:1305` 的 `_FUND_SCREEN_STALE_SECONDS`
    是**函数内局部变量**，只扫模块级会漏掉它，守卫就变成空转。
    同时覆盖 `X = v`（Assign）与 `X: T = v`（AnnAssign）两种写法 ——
    本仓踩过只扫 Assign 漏掉 AnnAssign 的坑。
    """
    tree = ast.parse(_read(path))
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        if value is None:
            continue
        for t in targets:
            if isinstance(t, ast.Name) and t.id == name:
                v = _eval_const(value)
                if v is not None:
                    return v
    return None


def _app_version() -> str:
    v = _const_value(CONFIG_PY, "APP_VERSION")
    return v if isinstance(v, str) else ""


# ==========================================================================
# 判据 1：版本号（config.py 为权威）
# ==========================================================================
def find_version_drift(app_version: str, sw_cache_name: str, index_versions: list[str]) -> list[str]:
    """返回版本不一致的问题列表；空列表 = 三处一致。纯函数，便于故障注入。"""
    problems: list[str] = []
    if not app_version:
        return ["无法从 config.py 解析出 APP_VERSION（解析器失效，守卫已空转）"]

    expected_dotted = app_version
    m = re.search(r"moneybag-v(\d+)-cache", sw_cache_name or "")
    if not m:
        problems.append(
            f"sw.js 的 CACHE_NAME 无法解析出版本号（实际 {sw_cache_name!r}）；"
            "格式应为 moneybag-v<无点号4位>-cache"
        )
    else:
        expected_compact = app_version.replace(".", "")
        if m.group(1) != expected_compact:
            problems.append(
                f"sw.js CACHE_NAME 版本 v{m.group(1)} != config.py APP_VERSION "
                f"{expected_dotted}（期望 v{expected_compact}）；"
                "两处不一致时 Service Worker 缓存不会失效"
            )

    if not index_versions:
        problems.append(
            "index.html 里没有解析到任何 ?v= 查询串（解析器失效或版本标记被移除，守卫已空转）"
        )
    else:
        bad = sorted({v for v in index_versions if v != expected_dotted})
        if bad:
            problems.append(
                f"index.html 存在与 APP_VERSION {expected_dotted} 不一致的 ?v=：{bad}"
                f"（共 {len(index_versions)} 处 ?v=）"
            )
    return problems


def _index_versions() -> list[str]:
    return re.findall(r"\?v=([0-9][0-9.]*)", _read(INDEX_HTML))


def _sw_cache_name() -> str:
    m = re.search(r"CACHE_NAME\s*=\s*['\"]([^'\"]+)['\"]", _read(SW_JS))
    return m.group(1) if m else ""


# ==========================================================================
# 判据 2：close_review 字数（prompt 文件为权威）
# ==========================================================================
def find_length_duplication(prompt_text: str, caller_text: str) -> list[str]:
    """prompt 必须有且只有一处权威字数约束；调用方不得复述任何数字。纯函数。"""
    problems: list[str] = []
    prompt_hits = _LENGTH_RANGE_RE.findall(prompt_text) + _LENGTH_UPPER_RE.findall(prompt_text)
    if not prompt_hits:
        problems.append(
            "close_review.md 里解析不到任何数字字数约束（权威位置缺失或写法变了），"
            "本判据已空转"
        )

    caller_hits = _LENGTH_RANGE_RE.findall(caller_text) + _LENGTH_UPPER_RE.findall(caller_text)
    if caller_hits:
        problems.append(
            f"调用方 stock_monitor_cron.py 复述了字数约束 {caller_hits} —— "
            "字数只能由 close_review.md 定义。两处同时进模型会造成矛盾指令："
            "实测 prompt 写 200-400 字、调用方写 800 字以内，"
            "在同一次 gw.call_sync 里同时生效。改法：调用方改为引用（如"
            "「按 close_review 的格式与字数要求」），不要复述数字。"
        )
    return problems


# ==========================================================================
# 判据 3：FUND_SCREEN_STALE_SECONDS（两处必须相等）
# ==========================================================================
def find_stale_seconds_drift(signals_value: object, warmer_value: object) -> list[str]:
    """两处常量必须存在且相等。纯函数。"""
    problems: list[str] = []
    if signals_value is None:
        problems.append("api/signals.py 里找不到模块级 FUND_SCREEN_STALE_SECONDS")
    if warmer_value is None:
        problems.append("scripts/cache_warmer.py 里找不到模块级 _FUND_SCREEN_STALE_SECONDS")
    if signals_value is not None and warmer_value is not None and signals_value != warmer_value:
        problems.append(
            f"FUND_SCREEN_STALE_SECONDS 双写不一致："
            f"api/signals.py={signals_value} vs cache_warmer.py={warmer_value}；"
            "cache_warmer.py:1303 的注释自称'必须保持一致'，但两侧漂移会导致"
            "预热窗口与判定窗口错位（旧的过期、新的还新鲜）"
        )
    return problems


# ==========================================================================
# 测试：判据 1 版本号
# ==========================================================================
def test_version_authority_is_parseable():
    """权威位置必须能解析出来 —— 否则下面所有版本断言都是空转。"""
    v = _app_version()
    assert v, "config.py 里解析不到 APP_VERSION（不是字面量赋值？解析器需同步更新）"
    assert re.fullmatch(r"\d+\.\d+\.\d+", v), f"APP_VERSION 格式异常：{v!r}"


def test_no_version_drift_across_three_places():
    problems = find_version_drift(_app_version(), _sw_cache_name(), _index_versions())
    assert not problems, "版本号三处不一致：\n" + "\n".join(f"  - {p}" for p in problems)


def test_index_html_v_count_is_nonzero():
    """显式断言处数非空：正则失配时不得变成"0 处也算通过"。"""
    versions = _index_versions()
    assert versions, "index.html 里解析不到 ?v= 查询串，解析器已失效"


# ==========================================================================
# 测试：判据 2 字数
# ==========================================================================
def test_close_review_length_lives_only_in_the_prompt():
    problems = find_length_duplication(_read(CLOSE_REVIEW_PROMPT), _read(CLOSE_REVIEW_CALLER))
    assert not problems, "close_review 字数口径不唯一：\n" + "\n".join(f"  - {p}" for p in problems)


def test_close_review_caller_references_the_prompt_by_name():
    """调用方必须靠"引用"承接约束，不能靠复述数字。"""
    caller = _read(CLOSE_REVIEW_CALLER)
    assert "close_review" in caller, (
        "调用方 prompt 里不再提到 close_review，说明它既没复述数字、也没做引用，"
        "字数约束会彻底丢失"
    )


# ==========================================================================
# 测试：判据 3 常量双写
# ==========================================================================
def test_fund_screen_stale_seconds_matches_across_files():
    problems = find_stale_seconds_drift(
        _const_value(SIGNALS_PY, "FUND_SCREEN_STALE_SECONDS"),
        _const_value(CACHE_WARMER_PY, "_FUND_SCREEN_STALE_SECONDS"),
    )
    assert not problems, "常量双写漂移：\n" + "\n".join(f"  - {p}" for p in problems)


def test_stale_seconds_parser_really_parses_the_expr():
    """解析器必须能算出 `72 * 3600` —— 否则下面两条断言都是空转。

    本仓这两个常量都写成算术表达式（不是字面量），只认字面量的解析器会返回 None，
    于是 `find_stale_seconds_drift(None, None)` 报"找不到"，或（更糟）断言被写成
    "只要不是两个 None 就通过"时直接静默放行。
    """
    signals_val = _const_value(SIGNALS_PY, "FUND_SCREEN_STALE_SECONDS")
    warmer_val = _const_value(CACHE_WARMER_PY, "_FUND_SCREEN_STALE_SECONDS")
    assert signals_val is not None, (
        "解析不出 api/signals.py 的 FUND_SCREEN_STALE_SECONDS（值是否为算术表达式？）"
    )
    assert warmer_val is not None, (
        "解析不出 scripts/cache_warmer.py 的 _FUND_SCREEN_STALE_SECONDS"
        "（注意它在函数作用域内，值也是算术表达式）"
    )
    assert signals_val == 72 * 3600, f"解析值异常：{signals_val}"


# ==========================================================================
# 故障注入：证明判据真的会红（防死测试）
# ==========================================================================
def test_injection_detects_sw_cache_version_drift():
    """注入：sw.js 落后一个版本 → 判据必须报出。"""
    problems = find_version_drift("9.9.26", "moneybag-v9925-cache", ["9.9.26"])
    assert problems, "sw.js 版本落后却未被判据发现，判据是死的"
    assert any("sw.js" in p for p in problems), f"报错信息未指向 sw.js：{problems}"


def test_injection_detects_index_html_mixed_versions():
    """注入：index.html 里混进一个旧版本号 → 判据必须报出（bump 只替换一种时会静默漏改）。"""
    problems = find_version_drift("9.9.26", "moneybag-v9926-cache", ["9.9.26"] * 26 + ["9.9.24"])
    assert problems, "index.html 混版却未被发现，判据是死的"
    assert any("9.9.24" in p for p in problems), f"报错信息未点出混版值：{problems}"


def test_injection_detects_length_duplication_reintroduced():
    """注入：把「800 字以内」重新写回调用方 → 判据必须报出。

    这是本次实际踩中的原文，用它当注入样本，确保将来有人改回去会被拦住。
    """
    caller_with_bug = "请按 close_review 格式输出收盘复盘，800 字以内。"
    problems = find_length_duplication(_read(CLOSE_REVIEW_PROMPT), caller_with_bug)
    assert problems, "调用方重新复述 800 字却未被发现，判据是死的"
    assert any("800" in p for p in problems), f"报错信息未点出重复的数字：{problems}"


def test_injection_detects_missing_prompt_constraint():
    """注入：prompt 里删掉字数约束（权威位置消失）→ 判据必须报出，而不是静默通过。"""
    problems = find_length_duplication("这里没有任何字数要求。", _read(CLOSE_REVIEW_CALLER))
    assert problems, "权威位置缺失却未被发现 —— 这正是「闸门空转仍显绿」"


def test_injection_detects_stale_seconds_drift():
    """注入：两侧常量改成不同值 → 判据必须报出。"""
    problems = find_stale_seconds_drift(72 * 3600, 48 * 3600)
    assert problems, "常量不一致却未被发现，判据是死的"


# --------------------------------------------------------------------------
# 正向对照：判据不得误伤（防"宁可错杀"的假阳性）
# --------------------------------------------------------------------------
def test_clean_inputs_produce_no_problems():
    """三组判据在真实（已修复的）输入上都必须干净。"""
    assert not find_version_drift(_app_version(), _sw_cache_name(), _index_versions())
    assert not find_length_duplication(_read(CLOSE_REVIEW_PROMPT), _read(CLOSE_REVIEW_CALLER))
    assert not find_stale_seconds_drift(
        _const_value(SIGNALS_PY, "FUND_SCREEN_STALE_SECONDS"),
        _const_value(CACHE_WARMER_PY, "_FUND_SCREEN_STALE_SECONDS"),
    )


def test_other_prompts_length_phrases_are_not_false_positives():
    """本判据只针对 close_review，不得去管别的 prompt 的合法单点字数要求。

    仓内其它 prompt/服务里存在大量合法单点约束（system_prompt 300 字、
    panel_advisor 100 字等），它们没有"调用方 vs prompt"的双写问题，
    因此不应被本文件判为违规。
    """
    other = "简洁回答，300字以内。"
    # 判据只看"prompt 有约束 & 调用方也复述"；把 other 当 prompt 文本时，
    # 调用方若为空则不应报错
    assert not _LENGTH_UPPER_RE.findall(""), "空文本不应命中"
    assert _LENGTH_UPPER_RE.findall(other), "上限式正则应能命中 300字以内"

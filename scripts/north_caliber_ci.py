"""
North Caliber CI Guard — 北向资金口径守门
=========================================
静态检查：禁止把「北向成交额」类字段当作「净流入/净买入」来命名或使用。

## 为什么需要这个检查

2024-08-19 起，沪深交易所**停止披露北向资金的日频净买入额**，改为按季度公布。
自此：
  - Tushare `moneyflow_hsgt` 的 `north_money` / `hgt` / `sgt` 填的是
    **当日成交额**（买入额 + 卖出额，恒为正，2500~3300 亿量级），**不是净买入**；
  - Tushare `moneyflow_hsgt` 的 `ggt_ss` / `ggt_sz` / `ggt_ss_flow` / `ggt_sz_flow`
    是**港股通（南向）**字段 —— 内地资金买港股，和北向方向相反；
  - AKShare `stock_hsgt_hist_em` 的净买入列自 2024-08-16 起全为 NaN。

历史上因为混淆这三件事，产生过：
  1. 对成交额做相邻两日差分再求和 → 望远镜求和（sum of diffs = last − first），
     N 日"累计净流入"退化成首尾两天成交额之差，产出 ±600 亿的假日波动；
  2. 用南向 `ggt_ss_flow + ggt_sz_flow` 当北向 → **系统性方向相反**；
  3. 把 `north_money / 100` 塞进名为 `北向资金(亿)` 的列 → 成交额伪装成净流入。

这些都不是"写错一行"，而是**命名与内容不符**导致的连锁误用。本检查用机器守住
这个不变量，避免依赖人工 code review（历史证明人工复扫会漏）。

## 检查规则

在同一行（或紧邻上下文）中同时出现：
  - 成交额/南向类字段名：north_money / ggt_ss / ggt_sz / ggt_ss_flow / ggt_sz_flow
  - 净流入类命名：net_flow / 净流入 / 净流出 / 净买入 / 净卖出 / net_inflow / netflow
→ 判定为违规。

## 豁免机制

在该行末尾加 `# noqa: north-caliber` 即可豁免（JS 用 `// noqa: north-caliber`）。
**豁免必须经过 review**：豁免意味着你确认该处的字段语义确实是净流入，
或该行只是在解释口径而非使用数据。滥用豁免会让本检查失效 ——
如果你发现自己想豁免多处，说明设计有问题，应该改命名而不是加豁免。

## 只检查「可执行代码」，不检查注释与 docstring

注释和 docstring 里出现这些词通常是在**解释口径陷阱**（本次修复刻意留下了大量
这类说明，属于制度性知识，必须保留）。如果连解释都被判违规，人们会为了让 CI
变绿而删掉解释 —— 那是负作用。所以扫描前会剥掉：
  - Python: `#` 注释 + 三引号 docstring
  - JS: `//` 行注释
字符串字面量（如 `row.get("north_money")`）**仍会被检查** —— 那是代码。

Exit 0 = pass, Exit 1 = fail.

Usage: python scripts/north_caliber_ci.py
"""
from __future__ import annotations

import io
import re
import sys
import tokenize
from pathlib import Path
from typing import Dict, List, NamedTuple

REPO_ROOT = Path(__file__).resolve().parent.parent

# ── 待检查范围 ──
SCAN_DIRS = ["backend", "pages", "scripts"]
SCAN_SUFFIXES = {".py", ".js"}

# ── 排除范围（冻结产物 / 第三方 / 文档） ──
EXCLUDE_PARTS = {
    "__pycache__", "_archive", "node_modules", ".git",
    "venv", ".venv", "site-packages", "docs",
}

# 本守卫自身：它的字段表和说明必然包含这些词，自引用会永远报错
SELF_PATH = Path(__file__).resolve()

NOQA_MARKER = "noqa: north-caliber"

# 成交额 / 南向 字段名（这些字段**不是**北向净流入）
TURNOVER_OR_SOUTHBOUND_FIELDS = [
    "north_money",      # 当日北向成交额（百万元），不是净买入
    "ggt_ss_flow",      # 港股通(沪) — 南向
    "ggt_sz_flow",      # 港股通(深) — 南向
    "ggt_ss",           # 南向
    "ggt_sz",           # 南向
]

# 净流入类命名，分两类处理（这个区分很重要，是自检夹具逼出来的）：
#
# 1) ASCII 标识符 —— 用【邻近窗口】匹配。
#    历史「望远镜求和」bug 长这样，字段取值与净流入计算隔了 2~3 行：
#        nm_today = float(rows[i].get("north_money") or 0)
#        ...
#        net_flow_million = nm_today - nm_prev        ← 隔行，同行匹配抓不到
#    且必须含 total_inflow/inflow —— 「南向当北向」那个 bug 的变量名是
#    `total_inflow`，不带 net_ 前缀。
NET_FLOW_IDENTIFIERS = [
    "net_flow", "net_inflow", "netflow", "net_buy",
    "total_inflow", "inflow", "outflow",
]

# 2) 中文词 —— 只用【同行】匹配。
#    中文出现在跨行位置时，绝大多数是**散文式的口径说明**（例如
#    unavailable_reason 那段"停止披露北向日频净买入"），那是我们刻意保留的
#    制度性知识，不能报错。真正危险的中文误用是**列名/字段名写错**，
#    而列名一定和取值写在同一行，例如：
#        "北向资金净流入(亿)": result["north_money"] / 100
NET_FLOW_CJK_TERMS = [
    "净流入", "净流出", "净买入", "净卖出",
]

# 邻近窗口：覆盖典型「取字段 → 算净流入」的行距
PROXIMITY_WINDOW = 8

_FIELD_RE = re.compile("|".join(re.escape(f) for f in TURNOVER_OR_SOUTHBOUND_FIELDS))
_NETFLOW_ID_RE = re.compile("|".join(re.escape(t) for t in NET_FLOW_IDENTIFIERS))
_NETFLOW_CJK_RE = re.compile("|".join(re.escape(t) for t in NET_FLOW_CJK_TERMS))


class Violation(NamedTuple):
    path: str
    lineno: int
    line: str
    field: str
    term: str


def _iter_files() -> List[Path]:
    files: List[Path] = []
    for d in SCAN_DIRS:
        base = REPO_ROOT / d
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.suffix not in SCAN_SUFFIXES:
                continue
            if any(part in EXCLUDE_PARTS for part in p.parts):
                continue
            if p.resolve() == SELF_PATH:
                continue  # 自引用：本守卫的字段表/说明必然命中
            files.append(p)
    return sorted(files)


def _code_lines_py(text: str) -> Dict[int, str]:
    """返回 {行号: 该行的可执行代码}，已剥离 # 注释与三引号 docstring。

    解析失败时退化为「原样返回」，宁可多报也不漏报。
    """
    lines = text.splitlines()
    keep: Dict[int, str] = {i + 1: ln for i, ln in enumerate(lines)}
    try:
        drop_spans: List[tuple] = []
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                drop_spans.append((tok.start, tok.end))
            elif tok.type == tokenize.STRING:
                s = tok.string.lstrip("rRbBuUfF")
                if s.startswith('"""') or s.startswith("'''"):
                    drop_spans.append((tok.start, tok.end))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return keep  # 解析不了就不剥离

    # 把被 drop 的区间从对应行里挖掉
    for (srow, scol), (erow, ecol) in drop_spans:
        if srow == erow:
            ln = keep.get(srow, "")
            keep[srow] = ln[:scol] + " " * max(0, ecol - scol) + ln[ecol:]
        else:
            for r in range(srow, erow + 1):
                ln = keep.get(r, "")
                if r == srow:
                    keep[r] = ln[:scol]
                elif r == erow:
                    keep[r] = " " * min(ecol, len(ln)) + ln[ecol:]
                else:
                    keep[r] = ""
    return keep


def _code_lines_js(text: str) -> Dict[int, str]:
    """JS：剥掉 // 行注释（块注释在本仓库北向相关代码中未出现，从简）。"""
    out: Dict[int, str] = {}
    for i, ln in enumerate(text.splitlines(), start=1):
        idx = ln.find("//")
        out[i] = ln[:idx] if idx >= 0 else ln
    return out


def _scan_file(path: Path) -> List[Violation]:
    violations: List[Violation] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"[WARN] 无法读取 {path}: {e}")
        return violations

    raw_lines = {i + 1: ln for i, ln in enumerate(text.splitlines())}
    code = _code_lines_py(text) if path.suffix == ".py" else _code_lines_js(text)

    # 先定位所有「成交额/南向字段」出现的行
    field_hits = {ln: m.group(0) for ln, src in code.items()
                  if (m := _FIELD_RE.search(src))}
    if not field_hits:
        return violations

    seen: set = set()
    # 函数边界：避免把 A 函数的字段和 B 函数的净流入变量配成一对（窗口天然不精确）
    def_lines = set()
    if path.suffix == ".py":
        for ln, src in code.items():
            if re.match(r"\s*(async\s+)?def\s|\s*class\s", src):
                def_lines.add(ln)

    def _crosses_def(a: int, b: int) -> bool:
        lo, hi = min(a, b), max(a, b)
        return any(lo < d <= hi for d in def_lines)

    for f_line, field in sorted(field_hits.items()):
        # 同行：ASCII 标识符 + 中文词都查
        # 邻近行：只查 ASCII 标识符（中文跨行基本都是口径说明，见常量处注释）
        candidates = []
        same_src = code.get(f_line, "")
        m_cjk = _NETFLOW_CJK_RE.search(same_src)
        if m_cjk:
            candidates.append((f_line, m_cjk.group(0)))
        for n_line in range(f_line - PROXIMITY_WINDOW, f_line + PROXIMITY_WINDOW + 1):
            src = code.get(n_line)
            if not src:
                continue
            if n_line != f_line and _crosses_def(f_line, n_line):
                continue  # 跨函数，不配对
            m_id = _NETFLOW_ID_RE.search(src)
            if m_id:
                candidates.append((n_line, m_id.group(0)))

        for n_line, term in candidates:
            # 豁免：字段行或命名行任一带 noqa 即放行
            if (NOQA_MARKER in raw_lines.get(f_line, "")
                    or NOQA_MARKER in raw_lines.get(n_line, "")):
                continue
            # 每个「违规行」只报一次（否则窗口内多个字段行会产生笛卡尔积噪声）
            if n_line in seen:
                continue
            seen.add(n_line)
            violations.append(Violation(
                path=str(path.relative_to(REPO_ROOT)),
                lineno=n_line,
                line=raw_lines.get(n_line, "").strip()[:150],
                field=f"{field}（第 {f_line} 行）",
                term=term,
            ))
    return sorted(violations, key=lambda v: (v.path, v.lineno))


def check_north_caliber() -> bool:
    files = _iter_files()
    all_violations: List[Violation] = []
    for f in files:
        all_violations.extend(_scan_file(f))

    print("=" * 64)
    print("North Caliber Guard — 北向口径检查")
    print("=" * 64)
    print(f"扫描范围: {', '.join(SCAN_DIRS)}  (后缀 {', '.join(sorted(SCAN_SUFFIXES))})")
    print(f"已扫描文件: {len(files)}")

    if not all_violations:
        print("\n✅ PASS: 未发现把成交额/南向字段当净流入使用的代码")
        return True

    print(f"\n❌ FAIL: 发现 {len(all_violations)} 处口径违规\n")
    for v in all_violations:
        print(f"  {v.path}:{v.lineno}")
        print(f"    {v.line}")
        print(f"    ↑ 字段 `{v.field}` 与净流入类命名 `{v.term}` 出现在同一函数的邻近位置")
        print()

    print("-" * 64)
    print("为什么这是错的：")
    print("  · 2024-08-19 起沪深交易所停止披露北向【日频净买入】，改为按季度公布。")
    print("  · `north_money` / `hgt` / `sgt` 自此是【当日成交额】（买入额+卖出额，")
    print("    恒为正、2500~3300亿量级），**不是净买入**。对它做差分求和会望远镜化")
    print("    （连续差分之和 = 末值−首值），产出纯噪声。")
    print("  · `ggt_ss*` / `ggt_sz*` 是【港股通（南向）】字段，方向与北向相反，")
    print("    拿它当北向是系统性反向错误，比随机噪声更危险。")
    print()
    print("怎么修：")
    print("  · 若你要的是成交额 → 用 turnover 类命名（如 turnover_today），")
    print("    并走 services.tushare_data.get_northbound_flow()。")
    print("  · 若你要的是净流入 → 数据源已不提供，请把该维度标记为不可得")
    print("    （net_flow_available=False），不要用成交额替代。")
    print(f"  · 确认该处语义无误 → 行末加 `# {NOQA_MARKER}`（豁免需 review）。")
    print("-" * 64)
    return False


def main() -> int:
    print("\nNorth Caliber CI — Static Analysis\n")
    ok = check_north_caliber()
    print("\n" + "=" * 64)
    print("SUMMARY:")
    print(f"  {'✅ PASS' if ok else '❌ FAIL'}: North Caliber Guard")
    print("=" * 64)
    if ok:
        print("\n🎉 北向口径检查通过！")
        return 0
    print("\n💥 北向口径检查失败 —— 见上方说明")
    return 1


if __name__ == "__main__":
    sys.exit(main())

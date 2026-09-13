"""Prompt 治理回归测试（v9.9.26 P2）

背景：prompt 长期散落在 12+ 个 .py 里（内联字符串）与 prompts/ 下的 md 里，
换模型 / 调 prompt 时容易"改一处漏一处"，还出现过「没人加载的死 prompt 挂在线上目录」。
本文件把这类漂移固化成可测断言。

四个控制面（**全部离线**：只读文件 + 读源码文本，不发网络请求、不调 LLM）：

  1. 落盘一致性 —— 生产 prompt 存在、非空、且确实被某个加载器引用；
     反向断言不存在"没有任何代码引用"的孤儿 md（白名单显式豁免并注明原因）。
  2. 版本对齐 —— 代码级版本常量 ↔ versions/ 归档文件名一一对应；归档命名规范。
  3. 硬约束存在性 —— 防编造类 prompt 正文必须含关键约束句；
     已知缺口显式登记且被事实校验（缺口被补上时测试会提醒把它挪出缺口表）。
  4. 数值禁令 —— 禁止 LLM 现编数值的路径必须含禁令句；
     仍在向 LLM 索要数值的 prompt 必须显式白名单登记，否则新增一处即测试变红
     （P1-7「让 LLM 现编 7 个浮点权重」事故的防复发闸门）。

约定：**不为了迁就测试去改 prompt**。断言用的措辞全部来自各 prompt 现状；
发现 prompt 缺约束时应登记进"缺口/白名单"并在报告里提出，改 prompt 必须走
prompts/versions/CHANGELOG.md 定义的版本 + A/B 流程。
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROMPTS_DIR = BACKEND_DIR / "prompts"
VERSIONS_DIR = PROMPTS_DIR / "versions"

# 扫源码时的排除项：
#   _archive / __pycache__ —— 非生产代码
#   tests                  —— 测试自身出现文件名不算"已接线"（否则死 prompt 会被测试自己的
#                             白名单/断言"引用"掉，反向断言形同虚设）
_SOURCE_EXCLUDE_PARTS = {"_archive", "__pycache__", "tests", ".git", "node_modules"}

# ------------------------------------------------------------------
# 1. 落盘一致性
# ------------------------------------------------------------------

# 孤儿 md 白名单：**只列已经落盘、但当前无生产加载点**的 prompt，必须写明原因。
# 断言 set(白名单) == set(实际孤儿)，所以这里既不能漏登记、也不能留过期条目。
ORPHAN_WHITELIST = {
    "signal_extract.md": (
        "尚未接线。设计上属 signal_scout.enrich()（见 docs/moneybag-v4-ultimate-plan.md "
        "「Prompt工程」表），当前生产无任何加载点。待接线或删除，勿直接复用。"
    ),
    "weekly_report.md": (
        "尚未接线。设计上属周报 HEAVY 层，当前周报由规则实现产出，生产无加载点。"
        "待接线或删除。"
    ),
}


def _production_prompt_files() -> list[Path]:
    """prompts/ 下的生产 prompt（不含 versions/ 归档）。"""
    return sorted(
        p for p in PROMPTS_DIR.rglob("*.md")
        if "versions" not in p.relative_to(PROMPTS_DIR).parts
    )


def _production_prompt_names() -> set[str]:
    return {p.name for p in _production_prompt_files()}


def _source_corpus() -> str:
    """所有生产 .py 源码的拼接文本（供"文件名是否被引用"断言）。"""
    chunks = []
    for p in sorted(BACKEND_DIR.rglob("*.py")):
        if any(part in _SOURCE_EXCLUDE_PARTS for part in p.parts):
            continue
        chunks.append(p.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(chunks)


def _orphan_prompts() -> set[str]:
    corpus = _source_corpus()
    return {name for name in _production_prompt_names() if name not in corpus}


def test_production_prompts_exist_and_are_non_empty():
    files = _production_prompt_files()
    assert files, "prompts/ 下没有找到任何生产 prompt，落盘约定被破坏"
    empty = [p.name for p in files if not p.read_text(encoding="utf-8").strip()]
    assert not empty, f"以下 prompt 为空文件: {empty}"


def test_no_undocumented_orphan_prompt():
    """每个生产 prompt 都必须被某个加载器引用；孤儿必须显式登记在白名单里。"""
    orphans = _orphan_prompts()
    undocumented = orphans - set(ORPHAN_WHITELIST)
    assert not undocumented, (
        f"发现未登记的孤儿 prompt（无人加载）: {sorted(undocumented)}。"
        "要么接线到某个加载器，要么删除，要么登记进 ORPHAN_WHITELIST 并写明原因。"
    )


def test_orphan_whitelist_has_no_stale_entry():
    """白名单不能留过期条目：已接线的 prompt 必须从白名单里移除。"""
    orphans = _orphan_prompts()
    stale = set(ORPHAN_WHITELIST) - orphans
    assert not stale, (
        f"以下 prompt 已不再是孤儿，请从 ORPHAN_WHITELIST 移除: {sorted(stale)}"
    )


def test_whitelist_reasons_are_documented():
    for name, reason in ORPHAN_WHITELIST.items():
        assert len(reason.strip()) >= 10, f"{name} 的白名单豁免必须写明原因"


def test_ab_cases_only_reference_existing_prompts():
    """A/B 场景集不得指向已删除的 prompt（否则跑 A/B 会 FileNotFoundError）。"""
    cases_file = BACKEND_DIR / "scripts" / "prompt_ab_cases.json"
    data = json.loads(cases_file.read_text(encoding="utf-8"))
    for key, block in data.items():
        if key.startswith("_") or key == "scoring_rules":
            continue
        assert isinstance(block, dict), f"A/B 用例 {key} 结构异常"
        target = block.get("target_prompt_file", key)
        assert (PROMPTS_DIR / f"{target}.md").exists(), (
            f"A/B 用例 '{key}' 指向不存在的 prompt: {target}.md"
        )


# ------------------------------------------------------------------
# 2. 版本对齐
# ------------------------------------------------------------------

_ARCHIVE_NAME_RE = re.compile(r"^[a-z0-9_]+\.v\d+\.md$")


def test_version_archive_naming_convention():
    archives = [p.name for p in VERSIONS_DIR.glob("*.md") if p.name != "CHANGELOG.md"]
    assert archives, "versions/ 下没有任何归档"
    bad = [n for n in archives if not _ARCHIVE_NAME_RE.match(n)]
    assert not bad, f"归档命名不符合 {{name}}.v{{N}}.md 规范: {bad}"


def test_holding_diagnose_version_constant_matches_archive():
    """api/holdings.py 的版本常量必须与 versions/ 下的归档文件名对应。"""
    src = (BACKEND_DIR / "api" / "holdings.py").read_text(encoding="utf-8")
    ver_m = re.search(r'HOLDING_DIAGNOSE_PROMPT_VERSION\s*=\s*"([^"]+)"', src)
    file_m = re.search(r'HOLDING_DIAGNOSE_PROMPT_FILE\s*=\s*"([^"]+)"', src)
    assert ver_m, "api/holdings.py 缺少 HOLDING_DIAGNOSE_PROMPT_VERSION 常量"
    assert file_m, "api/holdings.py 缺少 HOLDING_DIAGNOSE_PROMPT_FILE 常量"

    filename = file_m.group(1)
    stem = filename[:-3] if filename.endswith(".md") else filename
    version = ver_m.group(1)

    assert (PROMPTS_DIR / filename).exists(), f"线上 prompt 缺失: {filename}"
    archive = VERSIONS_DIR / f"{stem}.{version}.md"
    assert archive.exists(), (
        f"版本常量 {version} 对应的归档不存在: {archive.name}；"
        "改 prompt 必须同步在 versions/ 建新版本（见 versions/CHANGELOG.md）"
    )


def test_holding_diagnose_body_matches_its_v1_archive():
    """线上正文必须与其归档逐字节一致 —— 改动 prompt 必须新增版本，而不是就地改。"""
    live = (PROMPTS_DIR / "holding_diagnose.md").read_bytes()
    archive = (VERSIONS_DIR / "holding_diagnose.v1.md").read_bytes()
    assert live == archive, (
        "prompts/holding_diagnose.md 与 versions/holding_diagnose.v1.md 不再一致；"
        "若确实要改正文，请建 holding_diagnose.v2.md 并同步版本常量，不要就地改。"
    )


def test_deleted_dead_prompt_stays_deleted():
    """portfolio_diagnose.md 是无人加载的死 prompt，已删除；归档必须保留。"""
    assert not (PROMPTS_DIR / "portfolio_diagnose.md").exists(), (
        "portfolio_diagnose.md 已判定为死 prompt 并删除，勿重新加回线上目录；"
        "若确要启用，请以新版本号重新落盘并接线到具体模块。"
    )
    assert (VERSIONS_DIR / "portfolio_diagnose.v1.md").exists(), (
        "portfolio_diagnose.v1.md 归档不应被删除（保留设计稿以备将来接线）"
    )


# ------------------------------------------------------------------
# 3. 硬约束存在性（防编造）
# ------------------------------------------------------------------

# 断言用的措辞均取自各 prompt 现状，不要为了过测试去改 prompt。
ANTI_FABRICATION_REQUIRED = {
    "holding_diagnose.md": ("绝不预测价格", "数据不足", "只给方向性建议"),
    "system_prompt.md": ("绝不编造", "数据不足"),
    "steward_bear_attack.md": ("禁止编造任何数字", "数据缺失"),
    "steward_arbitrate.md": ("置信度诚实", "数据不足"),
    "steward_final_review.md": ("置信度诚实", "fatal_risk"),
    "ops_analyst.md": ("禁止编造", "数据不足"),
    "close_review.md": ("不得编造任何数据点", "数据不足", "伪装成判断"),
}

# 内联在 .py 里的 prompt，同样纳入约束断言（简报 / 选股 regime）。
ANTI_FABRICATION_REQUIRED_IN_SOURCE = {
    "scripts/night_worker.py": ("禁止编造任何数据点", "禁止输出任何精确数字"),
    "services/stock_screen.py": ("不要输出任何权重",),
}

# 应当有防编造约束、但目前正文里确实没有的 prompt —— 登记为"已知缺口"。
# 不改 prompt（改 prompt 要走版本+A/B），只做缺口登记 + 事实校验。
#
# 现状：**空表**。close_review.md 原登记于此，已于 v9.9.26 补齐「数据诚信（铁律）」
# 约束句并迁入 ANTI_FABRICATION_REQUIRED（同步落 versions/close_review.v2.md 归档）。
ANTI_FABRICATION_KNOWN_GAPS: dict[str, str] = {}

_ANTI_FABRICATION_KEYWORDS = ("不编造", "禁止编造", "不要编造", "不得编造", "数据不足", "数据缺失")


def test_anti_fabrication_constraints_present_in_prompts():
    missing = []
    for name, phrases in ANTI_FABRICATION_REQUIRED.items():
        path = PROMPTS_DIR / name
        assert path.exists(), f"ANTI_FABRICATION_REQUIRED 指向不存在的 prompt: {name}"
        text = path.read_text(encoding="utf-8")
        for phrase in phrases:
            if phrase not in text:
                missing.append(f"{name} 缺少约束句 {phrase!r}")
    assert not missing, "防编造约束缺失:\n" + "\n".join(missing)


def test_anti_fabrication_constraints_present_in_source_prompts():
    missing = []
    for rel, phrases in ANTI_FABRICATION_REQUIRED_IN_SOURCE.items():
        path = BACKEND_DIR / rel
        assert path.exists(), f"ANTI_FABRICATION_REQUIRED_IN_SOURCE 指向不存在的文件: {rel}"
        text = path.read_text(encoding="utf-8", errors="ignore")
        for phrase in phrases:
            if phrase not in text:
                missing.append(f"{rel} 缺少约束句 {phrase!r}")
    assert not missing, "内联 prompt 防编造约束缺失:\n" + "\n".join(missing)


def test_anti_fabrication_known_gaps_are_still_real():
    """缺口登记不能过期：若某 prompt 已补上约束，应挪进 REQUIRED 表。

    缺口表当前为空（close_review.md 已补齐），这是"无已知缺口"的合法终态。
    此处显式 return 而非依赖"空 dict 的 for 循环天然不执行"——后者是隐式通过，
    一旦未来有人误把有缺口的内容挪进 REQUIRED 却忘了登记，隐式通过无法给出提示；
    显式写法把"空即通过"标记为有意为之。新增缺口时仍逐条做事实校验。
    """
    if not ANTI_FABRICATION_KNOWN_GAPS:
        return
    for name, reason in ANTI_FABRICATION_KNOWN_GAPS.items():
        text = (PROMPTS_DIR / name).read_text(encoding="utf-8")
        assert not any(k in text for k in _ANTI_FABRICATION_KEYWORDS), (
            f"{name} 已含防编造约束句，请把它从 ANTI_FABRICATION_KNOWN_GAPS 移到 "
            f"ANTI_FABRICATION_REQUIRED（原登记原因：{reason}）"
        )


# ------------------------------------------------------------------
# 4. 数值禁令
# ------------------------------------------------------------------

# 禁止 LLM 现编数值的路径：必须含禁令句（否则 LLM 又会开始现编权重/占比）。
NUMERIC_BAN_REQUIRED = {
    "services/stock_screen.py": {
        "require": ("不要输出任何权重",),
        "reason": (
            "P1-7：LLM 只做 regime 离散分类，7 维权重一律查 "
            "config.STOCK_FACTOR_WEIGHTS_BY_REGIME 固化表"
        ),
    },
}

# "向 LLM 索要数值"的白名单：只允许已登记、且理由成立的 prompt。
# 新增一处"让 LLM 现编数字"的 prompt 而未登记 → 测试变红（防 P1-7 复发）。
NUMERIC_REQUEST_WHITELIST = {
    "skills/global_market.md": (
        "『给出影响强度评分（1-10分）』：主观分级仅用于展示排序，不参与任何可计算/"
        "可复测的数值决策；迁移优先级低，登记为债务"
    ),
    "skills/macro_analysis.md": (
        "『给出置信度』：模型自报的不确定性，项目约定必须诚实（见 system_prompt.md 铁律B），"
        "不是编造的可计算指标"
    ),
    "skills/stock_monitor.md": (
        "『给出置信度（多空辩论）』：同 macro_analysis，自报不确定性且受诚实规则约束"
    ),
}

# 匹配"要求 LLM 产出数值"的指令动词+数值名词组合。
_NUMERIC_REQUEST_RE = re.compile(
    r"(输出|返回|给出|给)[^。\n]{0,24}(评分|分数|权重|占比|百分比|目标价|置信度|仓位)"
)
# 带这些词的是"禁止输出数值"的否定句，不算索要数值。
_NEGATION_KEYWORDS = ("绝不", "不要", "禁止", "不得", "严禁", "不给出", "不给", "不预测", "不得输出")


def _numeric_request_hits() -> dict[str, list[str]]:
    """返回 {相对 prompts/ 的路径: [命中行]}。"""
    hits: dict[str, list[str]] = {}
    for path in _production_prompt_files():
        rel = path.relative_to(PROMPTS_DIR).as_posix()
        for line in path.read_text(encoding="utf-8").splitlines():
            if _NUMERIC_REQUEST_RE.search(line) and not any(
                kw in line for kw in _NEGATION_KEYWORDS
            ):
                hits.setdefault(rel, []).append(line.strip())
    return hits


def test_numeric_ban_paths_carry_the_prohibition():
    for rel, spec in NUMERIC_BAN_REQUIRED.items():
        path = BACKEND_DIR / rel
        assert path.exists(), f"NUMERIC_BAN_REQUIRED 指向不存在的文件: {rel}"
        text = path.read_text(encoding="utf-8", errors="ignore")
        for phrase in spec["require"]:
            assert phrase in text, (
                f"{rel} 缺少数值禁令句 {phrase!r}（{spec['reason']}）"
            )


def test_numeric_requests_are_all_whitelisted():
    """任何向 LLM 索要数值的 prompt 都必须显式登记；反向断言不留过期条目。"""
    hits = _numeric_request_hits()
    undocumented = set(hits) - set(NUMERIC_REQUEST_WHITELIST)
    assert not undocumented, (
        "以下 prompt 在向 LLM 索要数值（评分/权重/占比/百分比等），未登记白名单：\n"
        + "\n".join(f"  {rel}: {hits[rel]}" for rel in sorted(undocumented))
        + "\n请改为「LLM 只做分类、数值查固化表」，或登记进 NUMERIC_REQUEST_WHITELIST 并写明理由。"
    )
    stale = set(NUMERIC_REQUEST_WHITELIST) - set(hits)
    assert not stale, (
        f"以下白名单条目已不再命中（已改造或已删除），请移除: {sorted(stale)}"
    )


def test_numeric_request_whitelist_reasons_are_documented():
    for rel, reason in NUMERIC_REQUEST_WHITELIST.items():
        assert len(reason.strip()) >= 10, f"{rel} 的数值白名单豁免必须写明理由"


# ------------------------------------------------------------------
# 5. 加载器收敛
# ------------------------------------------------------------------

def test_api_layer_has_single_named_prompt_loader():
    """api 层必须只有一套按文件名读取 prompt 的入口，避免再长出第三套 loader。"""
    src = (BACKEND_DIR / "api" / "shared_helpers.py").read_text(encoding="utf-8")
    assert "def _load_named_prompt(" in src, "api 层缺少 _load_named_prompt 统一入口"


# 体检 prompt 正文的开头（用于"是否还有内联副本"的计数断言）。
_HOLDING_DIAGNOSE_BODY_HEAD = "你是专业的基金投资组合分析师"


def _top_level_function_source(src: str, name: str) -> str:
    """离线切出某个顶层函数的源码文本（不 import 模块，避免拉起重量级依赖）。"""
    m = re.search(rf"^def {re.escape(name)}\(", src, re.M)
    assert m, f"源码里找不到顶层函数 {name}"
    start = m.start()
    nxt = re.search(r"^def ", src[start + 1:], re.M)
    return src[start:] if nxt is None else src[start:start + 1 + nxt.start()]


def test_holding_diagnose_prompt_is_wired_not_inlined():
    src = (BACKEND_DIR / "api" / "holdings.py").read_text(encoding="utf-8")
    assert "_load_holding_diagnose_prompt" in src, "体检 prompt 未走落盘加载"
    assert "_load_named_prompt" in src, "未复用 api 层统一加载入口"

    # 正文只允许出现一次 —— 作为 fail-open 的降级默认串。
    assert src.count(_HOLDING_DIAGNOSE_BODY_HEAD) == 1, (
        "体检 prompt 正文在 api/holdings.py 出现多次；除降级默认串外不应再有副本"
    )

    # 真正的体检函数体内不得再有内联正文（内联就是"改了不生效"的根因）。
    fn_src = _top_level_function_source(src, "_compute_ai_checkup")
    assert _HOLDING_DIAGNOSE_BODY_HEAD not in fn_src, (
        "_compute_ai_checkup 仍在函数体内内联体检 prompt 正文，外移不彻底"
    )
    assert "_load_holding_diagnose_prompt()" in fn_src, (
        "_compute_ai_checkup 没有调用落盘加载函数"
    )


def test_holding_diagnose_fallback_default_is_present():
    """md 读不到时必须回退到内联默认串，保证诊断不中断（fail-open）。"""
    src = (BACKEND_DIR / "api" / "holdings.py").read_text(encoding="utf-8")
    assert "_HOLDING_DIAGNOSE_SYSTEM_DEFAULT" in src, "缺少降级兜底默认串"


def test_holding_diagnose_fallback_matches_live_prompt():
    """降级默认串必须与线上 prompt 逐字节一致，否则 md 丢失时会静默退回旧正文。"""
    src = (BACKEND_DIR / "api" / "holdings.py").read_text(encoding="utf-8")
    const = None
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_HOLDING_DIAGNOSE_SYSTEM_DEFAULT":
                    const = node.value.value
    assert isinstance(const, str), "未找到 _HOLDING_DIAGNOSE_SYSTEM_DEFAULT 字符串常量"
    live = (PROMPTS_DIR / "holding_diagnose.md").read_text(encoding="utf-8")
    assert const == live, (
        "降级默认串与线上 prompt 正文不一致：md 读不到时会静默退回旧正文。"
        "改 prompt 时请同步更新 _HOLDING_DIAGNOSE_SYSTEM_DEFAULT。"
    )

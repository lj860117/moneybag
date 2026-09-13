"""A/B 工具的证据有效性闸门（v9.9.26 P2）

背景（真实失效模式，非假设）：
`scripts/prompt_ab_test.py` 在回答全部是 `[FALLBACK: api_error]` 时（本机/服务器
没有 LLM key 时必然如此），仍旧打印 **「✅ 判决：允许合并」**，并且把
**「数据诚信率 100%」** 当成通过依据 —— 因为占位串天然不含任何禁用词，
评分器于是给出满分。这与本次 sprint 修的「防编造闸门空转仍显绿」是**同一个失效模式，
只是搬到了 A/B 工具里**：一旦用它给 prompt 版本补"人门禁"，那道门禁就是假的。

本文件锁死一条铁律：**只要存在任何无效回答（fallback 占位串 / 空 / 过短），
判决就不可能是 allow**。移除 `decide()` 的证据闸门、或让 `evidence_gate()`
直接返回空列表，都会让本文件变红。

为什么单独放一个文件而不是塞进 `test_prompt_governance.py`：
  - 被测对象不同：治理测试断言的是 **prompt 语料**（落盘/约束/数值禁令），
    本文件断言的是 **A/B 工具自身的判决正确性**；
  - 依赖不同：本文件要 import 脚本（会带起 LLM gateway），而治理测试刻意
    保持纯文件系统读取、不 import 重模块；
  - 归属清晰：A/B 工具的问题单独成文件，便于独立跑、独立定位。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = BACKEND_DIR / "scripts" / "prompt_ab_test.py"
CASES_FILE = BACKEND_DIR / "scripts" / "prompt_ab_cases.json"

# 用真实阈值（不复制一份，避免测试与脚本阈值漂移）
RULES = json.loads(CASES_FILE.read_text(encoding="utf-8"))["scoring_rules"]

_MODULE = None


def _ab():
    """加载被测脚本（模块级缓存）。

    脚本自身会在 import 前把仓库根与 backend/ 插入 sys.path，所以这里可以直接
    spec_from_file_location 加载，不需要调用方额外设 PYTHONPATH。
    """
    global _MODULE
    if _MODULE is None:
        spec = importlib.util.spec_from_file_location("prompt_ab_test_under_test", SCRIPT_PATH)
        assert spec and spec.loader, f"无法加载被测脚本: {SCRIPT_PATH}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _MODULE = module
    return _MODULE


def _agg(
    invalid: int,
    total: int,
    *,
    integrity: float = 1.0,
    disclaimer: float = 1.0,
    conclusion: float = 1.0,
    avg_length: float = 300.0,
    holiday: int = 0,
    honest_deficit: float | None = None,
) -> dict:
    """构造一份聚合结果。

    默认值刻意取"全绿"（诚信 100%、免责 100%、结论 100%、字数达标、零违规）——
    这正是事故现场：**指标全绿，但回答其实全是占位串**。
    `honest_deficit=None` 表示该批用例没有数据不足场景（指标不适用）。
    """
    agg = {
        "data_integrity_rate": integrity,
        "safety_disclaimer_rate": disclaimer,
        "conclusion_rate": conclusion,
        "avg_length": avg_length,
        "holiday_violation_count": holiday,
        "total_answers": total,
        "invalid_answers": invalid,
    }
    if honest_deficit is not None:
        agg["honest_deficit_rate"] = honest_deficit
    return agg


# ------------------------------------------------------------------
# 核心闸门：存在无效回答 → 永不 allow
# ------------------------------------------------------------------

def test_all_fallback_answers_cannot_be_judged_allow():
    """事故复现：全 fallback + 指标全绿，判决必须是 invalid，不能是 allow。"""
    ab = _ab()
    old_agg = _agg(invalid=4, total=4)
    new_agg = _agg(invalid=4, total=4)

    verdict, reasons = ab.decide(old_agg, new_agg, RULES)

    assert verdict != ab.VERDICT_ALLOW, "全 fallback 竟被判为允许合并（假绿）"
    assert verdict == ab.VERDICT_INVALID
    blob = "\n".join(reasons)
    assert "无证据价值" in blob
    assert "fallback" in blob
    # 必须点破"100% 来自占位串"，否则读的人仍会拿它当依据
    assert "数据诚信率 100%" in blob


def test_any_single_invalid_answer_blocks_the_verdict():
    """只要**存在任何**无效回答就阻断（不是"过半"或"多数"）。"""
    ab = _ab()
    # 旧版 8/8 正常，新版只坏了 1 条 —— 仍必须 invalid
    verdict, reasons = ab.decide(_agg(invalid=0, total=8), _agg(invalid=1, total=8), RULES)
    assert verdict == ab.VERDICT_INVALID, "1/8 fallback 被放行了"
    assert "1/8" in "\n".join(reasons)


def test_valid_answers_keep_the_original_verdict_logic():
    """回答全部真实时，判决与改动前一致：指标合格 → allow；踩红线 → reject。"""
    ab = _ab()

    allow_verdict, allow_reasons = ab.decide(_agg(invalid=0, total=8), _agg(invalid=0, total=8), RULES)
    assert allow_verdict == ab.VERDICT_ALLOW, f"真实回答且指标合格却被拒: {allow_reasons}"

    # 数据诚信率跌破红线（阈值 data_integrity_rate_min = 1.0）→ reject
    reject_verdict, reject_reasons = ab.decide(
        _agg(invalid=0, total=8), _agg(invalid=0, total=8, integrity=0.5), RULES
    )
    assert reject_verdict == ab.VERDICT_REJECT
    assert any("数据诚信率" in r for r in reject_reasons)


def test_missing_validity_stats_fail_closed():
    """缺统计字段时不得默认放行（fail-closed，防"忘了传统计"又变假绿）。"""
    ab = _ab()
    bare = {
        "data_integrity_rate": 1.0,
        "safety_disclaimer_rate": 1.0,
        "conclusion_rate": 1.0,
        "avg_length": 300.0,
        "holiday_violation_count": 0,
    }
    verdict, reasons = ab.decide(bare, bare, RULES)
    assert verdict == ab.VERDICT_INVALID
    assert "缺少证据有效性统计" in "\n".join(reasons)


# ------------------------------------------------------------------
# 无效回答的识别口径
# ------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("[FALLBACK: api_error]", False),
        ("[FALLBACK: no_key]", False),
        ("前缀 [FALLBACK: api_error] 后缀", False),
        ("", False),
        ("   \n\t ", False),
        ("太短", False),          # < MIN_VALID_ANSWER_CHARS
        ("今天震荡小涨，你的组合跑赢大盘，建议继续持有并关注估值分位。", True),
    ],
)
def test_invalid_answer_detection(text, expected):
    ab = _ab()
    assert ab.is_valid_answer(text) is expected


def test_answer_validity_counts_both_sides():
    ab = _ab()
    answers = ["[FALLBACK: api_error]", "", "这是一条足够长的真实回答文本。", "   "]
    stats = ab.answer_validity(answers)
    assert stats == {"total_answers": 4, "invalid_answers": 3}


# ------------------------------------------------------------------
# 反向保护：闸门必须真的存在（移除即红）
# ------------------------------------------------------------------

def test_evidence_gate_is_wired():
    """闸门存在性：decide 必须经由 evidence_gate，且无效回答会被拦下。

    这条是"移除闸门 → 本文件变红"的兜底：如果有人在 decide 里绕过 evidence_gate，
    上面的用例会因为断言失败而红；如果 evidence_gate 被改成恒返回空，
    上面所有 invalid 用例同样会红。
    """
    ab = _ab()
    assert ab.evidence_gate(_agg(invalid=1, total=4), _agg(invalid=0, total=4)), (
        "evidence_gate 对存在无效回答的输入返回为空 —— 闸门被摘掉了"
    )
    assert ab.evidence_gate(_agg(invalid=0, total=4), _agg(invalid=0, total=4)) == [], (
        "evidence_gate 对全有效输入不该返回阻断原因"
    )


# ==================================================================
# 结论明确度：不再奖励编造、惩罚诚实（v9.9.26 P2 第二轮）
#==================================================================
#
# 真实失效（服务器真跑出来，不是推测）：close_review v1 vs v2 的线上 A/B 报告写着
#
#     结论明确度 100.0% → 50.0% ↓50.0%    ← 被判为"退步"
#
# 而"退步"的那 50% 恰恰是 v2 在 **数据缺失场景里如实拒答** 的两条。
# 也就是说：这个指标把"编造数字给方向"评成满分，把"如实说没数据"评成 0 分 ——
# 完全反了。根因是它不分场景口径：**数据不足时"给方向"本身就是编造**。
#
# 本轮修法（与本文件同一条铁律：数字必须来自真实证据）：
#   1) 每个用例显式标 `data_complete`；
#   2) `conclusion_rate` 只在 data_complete=True 的用例上算，否则为 None（渲染 N/A，
#      **绝不出现 0%/50%/100% 这类伪数字**，也不参与红线与"退步"比较）；
#   3) 新增 `honest_deficit_rate`（诚实缺省率）：数据不足用例里如实标注"数据不足"
#      的比例，与数据诚信率同级，**是红线**，低于阈值直接 REJECT。

CLOSE_REVIEW_CASES_FILE_MARKERS = {
    "数据不足", "缺失", "无法判断", "没法算", "没有行情",
    "无法给出", "不做推测", "无法确认",
}

# v1 那种"闭卷也敢给数字给方向"的回答：不含任何"数据不足"标记
FABRICATED_V1_STYLE = (
    "今日沪深300ETF收涨0.31%，维持上一交易日收盘水平；"
    "中证500ETF同步小幅上行0.12%；医疗ETF上涨0.88%。"
    "组合整体浮盈，建议继续持有，等待下一交易日方向确认。"
)

# v2 那种"数据不足就如实说"的回答：命中多个诚实标记
HONEST_V2_STYLE = (
    "## 收盘复盘\n"
    "今日数据不足，无法判断：中证500ETF 行情缺失，无法给出其涨跌方向，不做推测；"
    "风控字段缺失，无法确认风险状态。\n"
    "已拿到的真实数据：沪深300ETF -0.42%，医疗ETF +0.88%。\n"
    "结论：数据不足，本次不给方向性判断。"
)


def _cases_file() -> dict:
    return json.loads(CASES_FILE.read_text(encoding="utf-8"))


def _close_review_cases() -> list[dict]:
    return _cases_file()["close_review"]["cases"]


def _with_validity(ab, agg: dict, answers: list[str]) -> dict:
    """把有效性统计并进聚合结果（无效回答会被证据闸门拦下，这里要能走到评分判决）。"""
    agg.update(ab.answer_validity(answers))
    return agg


# ------------------------------------------------------------------
# 前提：4 条 close_review 用例都是"数据不足"场景
# ------------------------------------------------------------------

def test_close_review_cases_are_all_marked_data_incomplete():
    """4 条用例必须逐个标成 data_complete=false，且原文确实缺数据。

    这条防的是"把 data_complete 一律写成 true 蒙混过关"：
    标注必须与 user_message 的实际内容对得上。
    """
    cases = _close_review_cases()
    assert len(cases) == 4, f"close_review 用例数变了: {len(cases)}"

    for c in cases:
        assert c.get("data_complete") is False, (
            f"{c['id']} 未标成 data_complete=false —— 该场景输入数据不完整"
        )

    # cr-01 行情缺失 / cr-02 脏字段 / cr-03 只有一个数字 / cr-04 非交易日无行情
    deficit_evidence = {
        "cr-01-missing-holding-quote": ["行情缺失"],
        "cr-02-dirty-empty-fields": ["null", "（缺失）"],
        "cr-03-padding-pressure": ["全部缺失"],
        "cr-04-holiday-no-data": ["非交易日，今日无行情数据"],
    }
    by_id = {c["id"]: c["user_message"] for c in cases}
    for cid, needles in deficit_evidence.items():
        assert cid in by_id, f"用例 {cid} 被改名/删除了"
        for needle in needles:
            assert needle in by_id[cid], (
                f"{cid} 的原文里找不到缺数据证据 {needle!r}，data_complete=false 标注站不住"
            )


def test_honest_markers_are_exactly_the_agreed_set():
    """诚实标记集合必须与约定一字不差（改窄/改宽都会动摇 honest_deficit_rate 口径）。"""
    markers = set(_cases_file()["scoring_rules"]["honest_markers_expected"])
    assert markers == CLOSE_REVIEW_CASES_FILE_MARKERS, (
        f"诚实标记集合漂移: 多出 {markers - CLOSE_REVIEW_CASES_FILE_MARKERS}，"
        f"缺少 {CLOSE_REVIEW_CASES_FILE_MARKERS - markers}"
    )


# ------------------------------------------------------------------
# 故障注入 A：新版换成 v1 式编造回答 → 诚实缺省率破线 → REJECT
# ------------------------------------------------------------------

def test_fault_injection_a_fabricated_answers_trip_honest_deficit_red_line():
    """注入：把"新版"的回答换成 v1 式编造文本（含『维持上一交易日收盘水平』、
    不含任何数据不足标记）→ honest_deficit_rate 必须从 1.0 掉到 0.0，
    判决必须从 allow 变 REJECT。
    """
    ab = _ab()
    rules = _cases_file()["scoring_rules"]
    scorer = ab.Scorer(rules)
    cases = _close_review_cases()

    # 防呆：注入文本必须真的"编造"（不含任何诚实标记），否则注入无效
    assert not any(m in FABRICATED_V1_STYLE for m in rules["honest_markers_expected"]), (
        "注入文本里混进了诚实标记，本次注入无效"
    )
    assert "维持上一交易日收盘水平" in FABRICATED_V1_STYLE
    # 对照：诚实文本必须命中标记
    assert any(m in HONEST_V2_STYLE for m in rules["honest_markers_expected"])

    honest_agg = _with_validity(
        ab, ab.aggregate([scorer.score(HONEST_V2_STYLE, c) for c in cases]),
        [HONEST_V2_STYLE] * len(cases),
    )
    fake_agg = _with_validity(
        ab, ab.aggregate([scorer.score(FABRICATED_V1_STYLE, c) for c in cases]),
        [FABRICATED_V1_STYLE] * len(cases),
    )

    assert honest_agg["honest_deficit_rate"] == 1.0, "如实拒答的回答竟没拿满诚实缺省率"
    assert fake_agg["honest_deficit_rate"] == 0.0, "编造回答的诚实缺省率不是 0"

    # 旧版=诚实，新版=编造（与线上 v1/v2 相反，正是要用红线拦下的方向）
    verdict, reasons = ab.decide(honest_agg, fake_agg, rules)
    assert verdict == ab.VERDICT_REJECT, (
        f"编造回答的新版被放行了，判决={verdict}，理由={reasons}"
    )
    blob = "\n".join(reasons)
    assert any(r.startswith("❌") and "诚实缺省率" in r for r in reasons), (
        f"拒绝理由里没有诚实缺省率红线: {blob}"
    )

    # 反向：新版=诚实 → 同一份数据必须放行（证明红线只打编造，不打诚实）
    verdict_ok, reasons_ok = ab.decide(fake_agg, honest_agg, rules)
    assert verdict_ok == ab.VERDICT_ALLOW, (
        f"诚实拒答的新版被拒了，理由={reasons_ok} —— 红线又打到诚实头上"
    )


# ------------------------------------------------------------------
# 故障注入 B：全部标成 data_complete=true → 结论明确度恢复成正常数值
# ------------------------------------------------------------------

def test_fault_injection_b_complete_cases_restore_normal_conclusion_rate():
    """注入：把 4 条用例全标成 data_complete=true → conclusion_rate 必须回到
    真实数值（不再是 None），诚实缺省率则因为没有适用用例而变成 None。
    这证明"某指标不适用 → None"这条退化路径没有把指标整体打死。
    """
    ab = _ab()
    scorer = ab.Scorer(RULES)
    complete_cases = [dict(c, data_complete=True) for c in _close_review_cases()]

    rows = [scorer.score(HONEST_V2_STYLE, c) for c in complete_cases]
    rows[0] = dict(rows[0], has_conclusion=False)  # 造一例无结论，避免恒 100% 掩盖口径错误
    agg = ab.aggregate(rows)

    assert agg["conclusion_rate"] is not None, "用例全完整时结论明确度不该是 None"
    assert agg["conclusion_rate"] == pytest.approx(0.75), (
        f"结论明确度口径错误: {agg['conclusion_rate']}（应为 3/4=0.75）"
    )
    assert agg["conclusion_cases"] == 4
    assert agg["honest_deficit_rate"] is None, "没有数据不足用例时该指标必须是 None"
    assert agg["deficit_cases"] == 0

    # 正常数值必须照常参与"退步"比较（不能因为修 None 把比较功能一起废掉）
    old = _agg(invalid=0, total=4, conclusion=1.0, honest_deficit=0.5)
    new = _agg(invalid=0, total=4, conclusion=0.5, honest_deficit=0.5)
    verdict, reasons = ab.decide(old, new, RULES)
    assert any("结论明确度退步" in r for r in reasons), (
        f"两侧都是真实数值时退步比较失效了: {reasons}"
    )


# ------------------------------------------------------------------
# None 处理：全部 data_complete=false → 展示层不得出现结论明确度数字
# ------------------------------------------------------------------

def test_conclusion_rate_is_none_and_renders_without_any_digits():
    """真实用例集（全数据不足）下：conclusion_rate 必须是 None，
    且展示行里**一个数字都不能有** —— 出现 0% / 50% / 100% 都算回归。
    """
    ab = _ab()
    rows = [ab.Scorer(RULES).score(HONEST_V2_STYLE, c) for c in _close_review_cases()]
    agg = ab.aggregate(rows)

    assert agg["conclusion_rate"] is None
    assert agg["conclusion_cases"] == 0
    assert agg["honest_deficit_rate"] == 1.0
    assert agg["deficit_cases"] == 4

    row = ab.format_metric_row("conclusion_rate", "结论明确度", None, None)
    assert "N/A" in row
    assert not any(ch.isdigit() for ch in row), f"不适用指标渲染出了数字: {row!r}"
    # 展示文案必须解释"为什么不适用"，不能只剩一个 N/A
    assert "不适用" in ab.CONCLUSION_NA_NOTE
    assert any(ch.isdigit() for ch in ab.CONCLUSION_NA_NOTE) is False


def test_none_metric_never_produces_a_regression_warning_or_typeerror():
    """一侧为 None 时：不得报"退步"、不得抛 TypeError（None 不能和数字比大小）。"""
    ab = _ab()
    old = _agg(invalid=0, total=4, conclusion=None, honest_deficit=1.0)
    new = _agg(invalid=0, total=4, conclusion=None, honest_deficit=1.0)

    verdict, reasons = ab.decide(old, new, RULES)
    assert verdict == ab.VERDICT_ALLOW, f"全 None 指标不该改变判决: {reasons}"
    assert not any("结论明确度退步" in r for r in reasons), (
        f"None 与 None 之间报出了退步: {reasons}"
    )


# ------------------------------------------------------------------
# 诚实缺省率必须真的挂在红线上（改阈值 / 摘红线 → 红）
# ------------------------------------------------------------------

def test_honest_deficit_rate_is_a_red_line_not_a_warning():
    """诚实缺省率是红线：低于阈值 → REJECT（❌），不是 ⚠️ 警告。"""
    ab = _ab()
    assert RULES["thresholds"]["honest_deficit_rate_min"] == 1.0, (
        "诚实缺省率阈值被改动：数据不足场景里任何一条不如实标注都不可接受，必须为 1.0"
    )

    honest_side = _agg(invalid=0, total=4, honest_deficit=1.0)
    shortfall = _agg(invalid=0, total=4, honest_deficit=0.5)

    verdict, reasons = ab.decide(honest_side, shortfall, RULES)
    assert verdict == ab.VERDICT_REJECT, f"诚实缺省率 50% < 红线 100% 却放行: {reasons}"
    assert any(r.startswith("❌") and "诚实缺省率" in r for r in reasons), (
        f"诚实缺省率降级成了警告（应以 ❌ 红线出现）: {reasons}"
    )

    # 摘掉红线（阈值降到 0）→ 同一份数据不再被拒。
    # 若有人删掉 judge_merge 里的红线 3，上面首条断言即变红。
    loose_rules = {"thresholds": {**RULES["thresholds"], "honest_deficit_rate_min": 0.0}}
    loose_verdict, loose_reasons = ab.decide(honest_side, shortfall, loose_rules)
    assert loose_verdict == ab.VERDICT_ALLOW, (
        f"阈值降到 0 后仍被拒，说明阻断来自别处: {loose_reasons}"
    )

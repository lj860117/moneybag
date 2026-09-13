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
) -> dict:
    """构造一份聚合结果。

    默认值刻意取"全绿"（诚信 100%、免责 100%、结论 100%、字数达标、零违规）——
    这正是事故现场：**指标全绿，但回答其实全是占位串**。
    """
    return {
        "data_integrity_rate": integrity,
        "safety_disclaimer_rate": disclaimer,
        "conclusion_rate": conclusion,
        "avg_length": avg_length,
        "holiday_violation_count": holiday,
        "total_answers": total,
        "invalid_answers": invalid,
    }


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

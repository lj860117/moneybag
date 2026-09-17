#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
推送质检「预期类 issue 不计入 FAIL」防回归测试（v9.9.47）。

## 背景

生产定局走 text 通道（`WXWORK_FORCE_MARKDOWN` 未设，上限 2048），晨报
3600+ 字节**必然**被 send_markdown 按 1800 字节预算拆成多条。分片是**无损**
的 —— 内容一个字节都不丢，没有任何动作需要人去做。

但 `evaluate_push_quality` 原来是 `total_issues > 0 ⇒ FAIL`，于是每天 22:00
准时 FAIL 一次。天天 FAIL 却无需处理 = 训练人忽略它，等真出事（内容被硬
截断 / QDII 未标注）时反而看不见。

所以：长度类 issue **仍然显示、仍然扣分，但不参与 FAIL 判定**。

## 为什么只放行长度类，不放行所有 ⚠️

曾评估过「一刀切放掉所有 ⚠️」，被否决 —— ⚠️ 里混着两类东西：

  - 长度超限 → 会无损分片，无需处理          ← 放行
  - QDII 未标注 T+2 / AI 模板化 / 空行超标 → **必须处理**
    （2026-09-15 才修过 QDII 未标注，是真 bug）  ← 绝不许放行

一刀切等于拿漏报换清净。本文件的用例就是围着这条线钉的。

## 用例组织（两头都钉，缺一不可）

1. 只有长度类        → PASS，但 issues 非空、文案仍含条数（信息不能消失）
2. 长度类 + QDII     → FAIL（防「长度超了就整体跳过检查」）
3. 只有 QDII（短）   → FAIL（证明 QDII 不在预期类里）
4. 基金名称为空（短）→ FAIL（证明 check_push_format 的**非长度**检查照常拦）
5. 源码护栏          → 预期类的判定条件里不许出现非长度项

## 关于 fixture

`tests/fixtures/2026-09-17_briefing_LeiJiang.txt` 是服务器
`/opt/moneybag/data/logs/pushes/` 的**真实文件**（3610 字节，只读拉取，
未做任何脱敏或裁剪），不是合成内容。
"""
import inspect
import os
import pathlib
import shutil
import sys

import pytest

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import daily_push_quality_check as qc  # noqa: E402


# 真实晨报 fixture（服务器 /opt/moneybag/data/logs/pushes/ 原样拷贝，未裁剪）
REAL_SAMPLE = os.path.join(
    os.path.dirname(__file__), "fixtures", "2026-09-17_briefing_LeiJiang.txt"
)
REAL_CONTENT = pathlib.Path(REAL_SAMPLE).read_text(encoding="utf-8")

# 09-16 是**天然**同时命中两类 issue 的真实存档（无需人工构造）：
#   长度类（3759B 超 text 上限）+ 非长度类（括号不匹配 30 vs 28）。
# 生产日志里这一天确实是 FAIL/90 —— 它是最好的「放行不过头」标尺。
MIXED_SAMPLE = os.path.join(
    os.path.dirname(__file__), "fixtures", "2026-09-16_briefing_LeiJiang.txt"
)
MIXED_CONTENT = pathlib.Path(MIXED_SAMPLE).read_text(encoding="utf-8")

QDII_ISSUE = "⚠️ QDII 基金未标注 T+2 披露延迟"
EMPTY_NAME_ISSUE = "❌ 基金名称显示为空"


def _run(
    tmp_path,
    content: str,
    name: str = "2026-09-17_briefing_LeiJiang.txt",
    date: str = "2026-09-17",
) -> dict:
    """把 content 当成一份真实存档，跑完整的 evaluate_push_quality()。

    走的是**真实代码路径**（不是 mock 掉某个检查）：唯一被替换的是存档
    目录，用于把文本喂进去。通道由 `_text_channel` fixture 固定为生产默认
    的 text（2048 / 1800）。
    """
    (tmp_path / name).write_text(content, encoding="utf-8")
    original_dir = qc.PUSH_ARCHIVE_DIR
    qc.PUSH_ARCHIVE_DIR = str(tmp_path)
    try:
        return qc.evaluate_push_quality(date, "LeiJiang")
    finally:
        qc.PUSH_ARCHIVE_DIR = original_dir


def _all_issues(results: dict) -> list:
    return [i for p in results["pushes"] for i in p["issues"]]


@pytest.fixture(autouse=True)
def _text_channel(monkeypatch):
    """生产默认通道：text（2048 / 1800）。"""
    monkeypatch.delenv("WXWORK_FORCE_MARKDOWN", raising=False)


# ------------------------------------------------------------------
# ① 只有长度类 → PASS，但信息必须还在
# ------------------------------------------------------------------

def test_real_0917_briefing_passes_with_length_issue_only(tmp_path):
    """真实 09-17 晨报：PASS / 95 分，但 issue 仍然报出来（不是静默）。"""
    results = _run(tmp_path, REAL_CONTENT)

    assert results["status"] == "PASS", results
    # 扣分保留：1 条 issue 扣 5 分 —— 信息不丢，只是不再判 FAIL
    assert results["score"] == 95, results
    assert results["total_issues"] == 1
    assert results["expected_issues"] == 1
    assert results["blocking_issues"] == 0

    issues = _all_issues(results)
    assert issues, "预期类也必须显示出来 —— 静默就是漏报回归"
    joined = " | ".join(issues)
    assert "2048" in joined, f"必须报 text 通道上限 2048：{joined}"
    assert "4096" not in joined, f"不得出现 markdown 的 4096：{joined}"
    assert "拆分为 ≥3 条" in joined, f"分片条数信息不得丢失：{joined}"


def test_real_0917_has_no_other_issues(tmp_path):
    """真实 09-17 晨报除了长度，没有触发别的检查。

    这条是**事实记录**，不是期望值：它证明 09-15 修的 QDII 标注在生产上
    是生效的（正文里确实有「净值披露延迟约 T+2」）。哪天这里多出一条，
    说明有新的真问题，不要为了让数字好看而放宽断言。
    """
    issues = _all_issues(_run(tmp_path, REAL_CONTENT))

    assert len(issues) == 1, f"真实晨报应只有 1 条（长度类），实际：{issues}"
    assert QDII_ISSUE not in issues


# ------------------------------------------------------------------
# ② 长度类 + 非长度类 → 必须 FAIL（防放行过头）
# ------------------------------------------------------------------

def test_length_plus_qdii_still_fails(tmp_path):
    """★ 最关键的一条：长度类不得掩盖 QDII 未标注。

    构造方式是**真实内容 + 一处真实回归**：把 09-17 真实晨报里那句
    「净值披露延迟约 T+2」换成不含标注词的说法 —— 这正是 2026-09-14 出过
    的事（判据只认 "T+1"/"延迟"，AI 换个措辞就挂不上）。正文里的
    `浦银安盛全球智能科技(QDII)A` 仍在，所以 QDII 检查会命中。

    如果有人把"排除逻辑"写成「只要长度超了就整体跳过检查」，这条会变绿。
    """
    broken = REAL_CONTENT.replace("净值披露延迟约 T+2", "净值披露稍慢")
    assert "T+2" not in broken and "延迟" not in broken, "前提：标注词确实被去掉了"
    assert "(QDII)A" in broken, "前提：QDII 持仓仍在正文里"

    results = _run(tmp_path, broken)

    assert results["status"] == "FAIL", (
        f"长度类不得掩盖 QDII 真问题：{results}"
    )
    issues = _all_issues(results)
    assert QDII_ISSUE in issues, issues
    assert any("消息超长" in i for i in issues), issues
    # 2 条 issue：1 条预期（长度）+ 1 条阻塞（QDII）
    assert results["total_issues"] == 2
    assert results["expected_issues"] == 1
    assert results["blocking_issues"] == 1
    assert results["score"] == 90


def test_real_0916_briefing_length_plus_bracket_mismatch_still_fails(tmp_path):
    """★ 真实存档、两类 issue 天然共存 → 必须 FAIL（零人工构造）。

    09-16 真实晨报（3707B）同时命中：
      - 长度类：3759 字节 > text 上限 2048（会无损分片，放行）
      - 非长度类：括号不匹配（开放 30 / 闭合 28 ← **真问题**）

    生产日志里这一天的结果就是 `FAIL / 90 分 / 2 处问题`。修复后它**必须
    仍然是 FAIL/90** —— 因为剩下那 1 条阻塞是真问题。

    这条比 `test_length_plus_qdii_still_fails` 更硬：那一条的 QDII 是我手工
    改出来的回归，这一条两类 issue 都是线上原样、一个字没动。
    """
    results = _run(
        tmp_path, MIXED_CONTENT,
        name="2026-09-16_briefing_LeiJiang.txt", date="2026-09-16",
    )

    assert results["status"] == "FAIL", (
        f"真实 09-16 有括号不匹配（真问题），不得因长度被放行：{results}"
    )
    issues = _all_issues(results)
    assert any("括号不匹配" in i for i in issues), issues
    assert any("消息超长" in i for i in issues), issues
    # 长度那条被放行，括号那条没被放行
    assert results["total_issues"] == 2
    assert results["expected_issues"] == 1
    assert results["blocking_issues"] == 1
    assert results["score"] == 90


# ------------------------------------------------------------------
# ③ 非长度类单独出现 → 必须 FAIL（证明它们不在预期类里）
# ------------------------------------------------------------------

def test_qdii_alone_still_fails(tmp_path):
    """短文本只触发 QDII 未标注 → 必须 FAIL（QDII 绝不在预期类里）。"""
    content = "☀️ 早安！\n\n持仓：\n- 浦银安盛全球智能科技(QDII)A 今日 +1.20%\n"

    results = _run(tmp_path, content)

    assert results["status"] == "FAIL", results
    assert results["expected_issues"] == 0
    assert results["blocking_issues"] >= 1
    assert QDII_ISSUE in _all_issues(results)


def test_empty_fund_name_still_fails(tmp_path):
    """基金名称为空（check_push_format 的**非长度**检查）照常判 FAIL。

    这条钉的是：放行只放长度类，check_push_format 里其它检查一个都没松。
    """
    content = "☀️ 早安！\n\n🔴 () 今日 -1.20%\n"

    results = _run(tmp_path, content)

    assert results["status"] == "FAIL", results
    assert results["expected_issues"] == 0
    assert any(EMPTY_NAME_ISSUE in i for i in _all_issues(results))


def test_excessive_blank_lines_still_fail(tmp_path):
    """空行超标（真阳性：AI prompt 泄漏导致正文膨胀）照常判 FAIL。"""
    content = ("段落\n\n" * (qc.MAX_BLANK_LINE_RUNS + 1)) + "结尾\n"

    results = _run(tmp_path, content)

    assert results["status"] == "FAIL", results
    assert results["expected_issues"] == 0
    assert any("分段可能不合理" in i for i in _all_issues(results))


# ------------------------------------------------------------------
# ④ 源码护栏 + 接口兼容
# ------------------------------------------------------------------

def test_only_length_branches_mark_issues_expected():
    """源码护栏：`expected.append` 只许出现在长度判定那一段。

    防止以后有人顺手把 QDII / 模板化 / 空行 也塞进预期类（那是用漏报换
    清净）。行为级用例只能证明"当前这几条不 FAIL"，拦不住新增的放行。
    """
    src = inspect.getsource(qc.check_push_format_classified)

    length_section = (
        src.split("# 检查2：消息是否太长", 1)[1]
           .split("# 检查3：分段是否合理", 1)[0]
    )
    outside = src.replace(length_section, "")

    assert "expected.append" not in outside, (
        "预期类只允许出现在「消息是否太长」这一节；"
        "QDII / 模板化 / 空行 / 基金名称为空 都不许被放行"
    )
    assert length_section.count("expected.append(") == 3, (
        f"预期类应恰好 3 处（超上限 / 超分段预算 / 过 3600 线），"
        f"实际 {length_section.count('expected.append(')} 处"
    )


def test_check_push_format_delegates_not_duplicates():
    """check_push_format 必须是**转发**，不许复制一份判定逻辑。

    两份实现迟早漂移：只改其中一份时，另一份会静默地把旧行为（比如写死
    4096）带回线上。这条从源码层面钉死「只有一份实现」。
    """
    thin = inspect.getsource(qc.check_push_format)

    assert "check_push_format_classified" in thin, "必须转发到 classified 版本"
    # 转发壳里不该再出现任何判定常量 —— 出现就说明判定被复制了一份
    for token in ("WECOM_MARKDOWN_LIMIT", "MARKDOWN_CHUNK_BUDGET",
                  "LENGTH_ALERT_BYTES", "effective_channel()"):
        assert token not in thin, (
            f"{token} 出现在 check_push_format 里：判定逻辑被复制了，"
            f"两份实现会漂移 —— 判定只许留在 check_push_format_classified"
        )


def test_check_push_format_still_returns_plain_list(tmp_path):
    """向后兼容：check_push_format 的签名和返回类型没变（仍是 list）。"""
    f = tmp_path / "2026-09-17_briefing_LeiJiang.txt"
    f.write_text(REAL_CONTENT, encoding="utf-8")

    issues = qc.check_push_format(str(f))

    assert isinstance(issues, list)
    assert len(issues) == 1


def test_classified_returns_expected_as_subset(tmp_path):
    """check_push_format_classified 的 expected 必须是 issues 的子集。"""
    f = tmp_path / "2026-09-17_briefing_LeiJiang.txt"
    f.write_text(REAL_CONTENT, encoding="utf-8")

    issues, expected = qc.check_push_format_classified(str(f))

    assert isinstance(issues, list) and isinstance(expected, list)
    assert len(expected) == 1
    for item in expected:
        assert item in issues, "expected 必须是 issues 的子集"

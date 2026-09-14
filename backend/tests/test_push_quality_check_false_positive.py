#!/usr/bin/env python3
"""
推送质检（`scripts/daily_push_quality_check.py`）误报防回归测试。

背景（FIX 2026-09-14）：2026-09-14 早 8:30 推给 LeiJiang 的质检告警是：

    结论：FAIL  总分：85/100
    ⚠️ QDII 基金未标注 T+1 延迟
    ⚠️ 估值数据未标注时间
    ⚠️ 分段可能不合理：12 处空行

其中后两条是**误报**（第 1 条是真问题，另案处理，本文件不覆盖）：

1. 「估值数据未标注时间」：旧规则是 `if "估算" in content or "估值" in content`，
   把「基金估算净值」和「市场估值水平」两个完全不同的概念混为一谈。晨报里
   唯一命中「估值」的是 AI 研判的「估值百分位67.5%适中」——这是**市场估值
   分位指标**（类似 PE 百分位，见 `services/glossary.py` 的「估值百分位」
   词条、`services/portfolio.py:357` 的 `估值百分位: {val_pct}%`），
   根本不是一个需要标注时间戳的净值数字。

2. 「分段可能不合理」：旧阈值 `content.count("\\n\\n") > 10`。实测服务器上
   `data/logs/pushes/*_briefing_*.txt` 共 106 份，空行分布 10/11/12/13/26
   = 63/5/30/6/2 份，**正常样本上限 13**（且都在企微字节预算内），
   旧阈值 10 会误伤 43/106 = 40.6%。

本仓铁律：测试要能**精确红**，不能恒绿。因此本文件分两组：

- `TestNoOverCorrection`（防修过头）：这些用例在**修 bug 之前就是绿的**，
  修完之后必须**仍然绿**。它们的作用是防止后人为了消误报把规则一把删掉。
- `TestNoFalsePositiveRegression`（防误报回潮）：这些用例在**修 bug 之前
  是红的**（已回退到纯净 e6ae250 实测确认），修完之后转绿。
  如果哪天它们在新代码上也绿了，说明是死测试，不是修好了。

`tests/fixtures/2026-09-14_briefing_LeiJiang.txt` 是服务器
`/opt/moneybag/data/logs/pushes/` 的**真实样本**（2157 字节，md5
d82b94611f094cc97aa9f09dc4e5acdc），未做任何脱敏或裁剪。
"""

import os
import pathlib
import tempfile
import unittest

import pytest

from scripts.daily_push_quality_check import (
    MAX_BLANK_LINE_RUNS,
    check_data_source,
    check_push_format,
)

# 真实样本 fixture（服务器 /opt/moneybag/data/logs/pushes/ 原样拷贝）
REAL_SAMPLE = os.path.join(
    os.path.dirname(__file__), "fixtures", "2026-09-14_briefing_LeiJiang.txt"
)
REAL_SAMPLE_CONTENT = pathlib.Path(REAL_SAMPLE).read_text(encoding="utf-8")

TIMESTAMP_ISSUE = "⚠️ 估值数据未标注时间"
SEGMENT_ISSUE_PREFIX = "⚠️ 分段可能不合理"
QDII_ISSUE = "⚠️ QDII 基金未标注 T+1 延迟"


def _write(tmp_dir, content, name="push.txt"):
    """把内容写成一份推送存档文件并返回路径。"""
    p = pathlib.Path(tmp_dir) / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def _data_source_issues(tmp_dir, content):
    """对给定正文跑 check_data_source()，返回 issue 列表。"""
    return check_data_source(_write(tmp_dir, content))


def _segment_issues(tmp_dir, content):
    """只取 check_push_format() 里的「分段」类 issue。"""
    return [
        i for i in check_push_format(_write(tmp_dir, content))
        if i.startswith(SEGMENT_ISSUE_PREFIX)
    ]


class _TmpDirCase(unittest.TestCase):
    """给 unittest 用例提供一个临时目录（对标 pytest 的 tmp_path）。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp_dir = self._td.name

    def tearDown(self):
        self._td.cleanup()


class TestNoOverCorrection(_TmpDirCase):
    """
    防修过头：修完 bug 后这些**仍然必须告警**。

    这组用例在修复前就是绿的 —— 它们锁住的是「规则不能因为误报就被删掉」。
    """

    def test_estimated_nav_without_timestamp_is_still_flagged(self):
        """真正的「基金估算净值」没有时间戳时，必须告警。"""
        content = "☀️ 早安！\n\n你的基金估算净值 1.2345，今日参考。\n"

        self.assertIn(TIMESTAMP_ISSUE, _data_source_issues(self.tmp_dir, content))

    def test_estimated_nav_phrase_variants_are_still_flagged(self):
        """各种「估算净值」写法都必须命中，不能只认一种措辞。"""
        for phrase in ("估算净值 1.2345", "实时估值 2.1000", "估算涨幅 +1.20%",
                       "盘中估值 0.9876", "净值估算：1.2345", "估值 1.2345"):
            with self.subTest(phrase=phrase):
                content = f"☀️ 早安！\n\n持仓参考：{phrase}\n"
                self.assertIn(TIMESTAMP_ISSUE,
                              _data_source_issues(self.tmp_dir, content))

    def test_estimated_nav_with_timestamp_is_not_flagged(self):
        """标注了时间戳就不该告警（证明规则在校验时间戳，不是恒真）。"""
        content = "☀️ 早安！\n\n估算净值 1.2345（估值时间 09-14 15:00）\n"

        self.assertNotIn(TIMESTAMP_ISSUE, _data_source_issues(self.tmp_dir, content))

    def test_excessive_blank_lines_are_still_flagged(self):
        """空行明显超标（新阈值的 1.5 倍）必须仍然告警。"""
        blanks = int(MAX_BLANK_LINE_RUNS * 1.5) + 1  # 1.5 倍，向上取整
        content = ("段落\n\n" * blanks) + "结尾\n"

        self.assertEqual(content.count("\n\n"), blanks)
        self.assertGreater(blanks, MAX_BLANK_LINE_RUNS)
        issues = _segment_issues(self.tmp_dir, content)
        self.assertEqual(len(issues), 1, f"应恰好命中 1 条分段告警，实际 {issues}")


class TestNoFalsePositiveRegression(_TmpDirCase):
    """
    防误报回潮：这些用例在修复前**必须是红的**。

    已回退到纯净 e6ae250 实测确认：本组 4 个用例修复前全部 FAIL。
    """

    def test_valuation_percentile_is_not_flagged(self):
        """「估值百分位67.5%适中」是市场估值指标，不是基金估算净值。"""
        content = "📝 【AI研判】\n- 恐贪指数46属中性，估值百分位67.5%适中\n"
        issues = _data_source_issues(self.tmp_dir, content)

        self.assertNotIn(TIMESTAMP_ISSUE, issues)
        self.assertEqual(issues, [], f"不该有任何告警，实际 {issues}")

    def test_normal_blank_line_count_is_not_flagged(self):
        """真实晨报的 12 处空行完全正常，不该告警。"""
        self.assertEqual(REAL_SAMPLE_CONTENT.count("\n\n"), 12)
        self.assertEqual(_segment_issues(self.tmp_dir, REAL_SAMPLE_CONTENT), [])

    def test_real_sample_has_only_the_qdii_issue(self):
        """
        真实样本跑完整 check_data_source()，只能剩「QDII 未标注 T+1」1 条。

        另两条（估值时间 / 分段）必须为 0。
        """
        issues = check_data_source(REAL_SAMPLE)

        self.assertEqual(issues, [QDII_ISSUE])
        self.assertNotIn(TIMESTAMP_ISSUE, issues)


@pytest.mark.parametrize("phrase", [
    "估值百分位67.5%适中",
    "估值百分位83.8%，高",
    "估值分位高达88.5%",
    "市场估值偏高，外资还在疯狂卖",
    "整体估值偏贵，机构普遍看多",
    "现在估值已经很高",
    "估值83%以上，提示风险",
    "估值比过去89%的时间都贵",
    "指数增强A浮亏较多但估值合理",
    "浮亏合计超16%，估值修复仍需时间",
    "估值矛盾「估值合理」",
])
def test_corpus_market_valuation_phrases_are_not_flagged(tmp_path, phrase):
    """
    语料实证：106 份存档里「估值」共 61 处，**全部**是市场估值语境。

    上面每一条都取自服务器 `data/logs/pushes/*.txt` 的真实上下文（含 AI
    研判里的原话）。旧规则对这 61 处 100% 误报，新规则必须 0 命中。
    """
    content = f"📝 【AI研判】\n一句话：{phrase}，建议持有。\n"
    issues = _data_source_issues(tmp_path, content)

    assert TIMESTAMP_ISSUE not in issues, f"{phrase!r} 不该触发估值时间告警"
    assert issues == [], f"{phrase!r} 不该有任何告警，实际 {issues}"


def test_threshold_is_evidence_based():
    """
    阈值护栏：MAX_BLANK_LINE_RUNS 必须留在实测正常带（上限 13）之上、
    真实异常样本（26）之下。改动这个常量时本用例会提醒重跑实测。
    """
    assert MAX_BLANK_LINE_RUNS >= 14, (
        "阈值跌回实测正常带（106 份存档正常样本空行上限 13）会造成大面积误报"
    )
    assert MAX_BLANK_LINE_RUNS <= 25, (
        "阈值高过 25 会放过真实异常样本（2026-07-03 两份 26 处空行，"
        "实测为 AI prompt 泄漏导致的正文膨胀，是真阳性）"
    )


if __name__ == "__main__":
    unittest.main()

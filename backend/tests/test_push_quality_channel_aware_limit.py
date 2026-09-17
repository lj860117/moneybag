#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
推送质检「通道感知上限」漏报回归测试（FIX 2026-09-17）。

## 背景：这是一次**漏报**，不是误报

`scripts/daily_push_quality_check.py::check_push_format()` 判断「消息是否太长」
时分三级。修复前前两级写死的是 **markdown 通道**的常量：

    if   sent_bytes > WECOM_MARKDOWN_LIMIT:   # 4096
    elif sent_bytes > MARKDOWN_CHUNK_BUDGET:  # 3900
    elif sent_bytes > LENGTH_ALERT_BYTES:     # 3600  ← 只有这一级调用了
                                              #          effective_channel()

而生产**默认走 text 通道**（`_force_text()` 默认 True，上限 2048 / 分段预算
1800），只有显式 `WXWORK_FORCE_MARKDOWN=1` 才是 markdown。

于是 text 通道下 `sent_bytes` 落在 **2049 ~ 3600** 这个区间时：

    > 4096 ? 否    > 3900 ? 否    > 3600 ? 否
    ⇒ 三个分支全不命中 ⇒ **一行都不报，质检完全静默**

而这段区间恰恰是 text 通道已经超上限、推送时**必然被拆成多条**的区间。
换句话说：最需要被看见的情况，监控一个字都不说。这比「报了警但措辞不准」
严重得多 —— 后者至少还会有人去看。

生产铁证（2026-09-17）：
    晨报 body 3420B + 信封 52B = **3472B**，走 text 通道（上限 2048）
    ⇒ 必然拆成 2 条；质检却只报「接近告警线 3600」，措辞还写成
      「距通道上限 **4096** 还剩 X 字节」—— 4096 根本不是生产用的通道。
    即：**漏报 + 基准写错**同时发生。

## 本文件锁住的三件事

1. text 通道（默认）sent_bytes = 2100 **必须**产出 issue（修复前静默 —— 这是
   本文件最核心的回归点，见 `test_text_channel_2100_must_not_be_silent`）。
2. markdown 通道 sent_bytes = 2100 **必须**静默（证明 ① 的告警来自通道上限
   变小，而不是「把阈值一律调低」）。
3. text 通道 sent_bytes = 3472（09-17 真实值）产生的 issue 里，出现的上限数字
   必须是 **2048**，绝不能是 4096。

本仓铁律：测试要能**精确红**。本文件每条用例都经过故障注入验证 —— 把
`daily_push_quality_check.py` 的判定改回写死的 4096 / 3900 后，本文件
**必须**变红（实测数字见提交说明）。
"""
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import daily_push_quality_check as qc  # noqa: E402
from services import wxwork_push as wp  # noqa: E402


# ------------------------------------------------------------------
# 工具
# ------------------------------------------------------------------

_UNIT = "中文字符测试内容，"  # 9 个汉字 = 27 字节


def make_body(target_bytes: int) -> str:
    """生成 UTF-8 字节数**精确等于** target_bytes 的正文。

    刻意不含 "\\n\\n"：check_push_format 里还有一条「分段空行 > 20」的检查，
    本文件只关心长度判定，不能让空行检查串味进来。
    """
    unit_bytes = len(_UNIT.encode("utf-8"))
    out: list = []
    size = 0
    while size + unit_bytes <= target_bytes:
        out.append(_UNIT)
        size += unit_bytes
    rest = target_bytes - size
    if rest:
        out.append("x" * rest)
    body = "".join(out)
    assert wp.byte_len(body) == target_bytes, "make_body 必须字节精确"
    assert "\n\n" not in body, "正文不得含空行，避免触发另一条无关的段检查"
    return body


def length_issues(tmp_path, sent_bytes: int) -> list:
    """构造一份 sent_bytes 字节的存档，返回 check_push_format 的全部 issue。

    传的是 **sent_bytes**（含 52 字节信封），函数内部会减去信封还原 body，
    这样用例里的数字和运维日志里的「发送字节数」是同一个口径。
    """
    body = make_body(sent_bytes - wp.PUSH_ENVELOPE_OVERHEAD_BYTES)
    f = tmp_path / "2026-09-17_briefing_LeiJiang.txt"
    f.write_text(body, encoding="utf-8")
    return qc.check_push_format(str(f))


@pytest.fixture
def text_channel(monkeypatch):
    """生产默认通道：text（2048 / 1800）。"""
    monkeypatch.delenv("WXWORK_FORCE_MARKDOWN", raising=False)
    assert wp.effective_channel() == ("text", 2048, 1800)
    return "text"


@pytest.fixture
def markdown_channel(monkeypatch):
    """显式开启的 markdown 通道（4096 / 3900）。"""
    monkeypatch.setenv("WXWORK_FORCE_MARKDOWN", "1")
    assert wp.effective_channel() == ("markdown", 4096, 3900)
    return "markdown"


# ------------------------------------------------------------------
# 核心回归点：修复前 text 通道 2049~3600 完全静默
# ------------------------------------------------------------------

def test_text_channel_2100_must_not_be_silent(tmp_path, text_channel):
    """★ 本文件的存在理由：text 通道 sent=2100 修复前**一行都不报**。

    2100 > text 上限 2048 ⇒ 推送时必然拆成 2 条。修复前三个阈值
    （>4096 / >3900 / >3600）全不成立，质检静默 —— 这是漏报，不是误报。
    """
    issues = length_issues(tmp_path, 2100)

    assert issues, (
        "text 通道 sent=2100 字节（> 上限 2048）必须报出来，"
        "静默 = 漏报回归"
    )
    assert any(m in i for i in issues for m in ("❌", "⚠️")), f"issues={issues}"


def test_text_channel_2100_reports_text_limit_2048_not_4096(tmp_path, text_channel):
    """报出来的必须是 text 通道的 2048，绝不能再出现 markdown 的 4096。"""
    issues = length_issues(tmp_path, 2100)
    joined = " | ".join(issues)

    assert "2048" in joined, f"必须给出 text 通道上限 2048，实际：{joined}"
    assert "4096" not in joined, f"不得再拿 markdown 的 4096 当基准：{joined}"
    assert "text" in joined, f"必须点名实际通道，实际：{joined}"


def test_markdown_channel_2100_stays_silent(tmp_path, markdown_channel):
    """负面控制：markdown 通道下 2100 字节完全安全，必须**静默**。

    这条证明上一条的告警来自「通道上限变小」，而不是「阈值一律调低」——
    如果删掉这条，把阈值随便调低到 2000 也能让上一条变绿，那是假绿。
    """
    assert 2100 < wp.WECOM_MARKDOWN_LIMIT      # 4096，确实没超上限
    assert 2100 < wp.LENGTH_ALERT_BYTES        # 3600，也没到告警线
    assert length_issues(tmp_path, 2100) == []


def test_real_0917_briefing_3472_reports_2048_never_4096(tmp_path, text_channel):
    """2026-09-17 生产真值：body 3420B + 信封 52B = 3472B。

    走 text 通道（上限 2048）必然拆 2 条；修复前质检只报「接近告警线 3600」，
    措辞还写「距通道上限 4096 还剩 X 字节」—— 通道写错。
    """
    issues = length_issues(tmp_path, 3472)
    joined = " | ".join(issues)

    assert issues, f"3472 字节（> 2048）必须报出来，实际静默：{issues}"
    assert "2048" in joined, f"必须报 text 通道上限 2048，实际：{joined}"
    assert "4096" not in joined, f"绝不能出现 markdown 的 4096：{joined}"
    assert "text" in joined, f"必须点名 text 通道：{joined}"


# ------------------------------------------------------------------
# 边界钉死：两个通道、四个分界点
# ------------------------------------------------------------------

@pytest.mark.parametrize(
    "sent_bytes, expect_silent",
    [
        (1800, True),   # == text 分段预算，不超 ⇒ 静默
        (1801, False),  # > 分段预算 ⇒ ⚠️ 会分段
        (2048, False),  # == text 上限，仍 > 1800 ⇒ ⚠️ 会分段
        (2049, False),  # > text 上限 ⇒ ❌ 超长
        (3472, False),  # 09-17 真值
        (3600, False),  # 修复前这里是静默的（>3600 不成立）
    ],
)
def test_text_channel_boundaries(tmp_path, text_channel, sent_bytes, expect_silent):
    """text 通道（2048 / 1800）的边界。

    3600 这条最关键：修复前 `>3600` 不成立、前两级又是 4096/3900，
    于是「恰好 3600」和「3472」都是静默的 —— 覆盖它等于覆盖漏报区间的右端。
    """
    issues = length_issues(tmp_path, sent_bytes)
    assert bool(issues) is not expect_silent, (
        f"text 通道 sent={sent_bytes} 期望 "
        f"{'静默' if expect_silent else '报出'}，实际 issues={issues}"
    )


@pytest.mark.parametrize(
    "sent_bytes, expect_silent",
    [
        (2100, True),   # 远低于 3600
        (3600, True),   # == 告警线，不超 ⇒ 静默
        (3601, False),  # > 3600 ⇒ 告警线（markdown 下这一级才活着）
        (3900, False),  # == 分段预算，仍 > 3600 ⇒ 告警线
        (3901, False),  # > 分段预算 ⇒ ⚠️ 会分段
        (4096, False),  # == 上限，仍 > 3900 ⇒ ⚠️ 会分段
        (4097, False),  # > 4096 ⇒ ❌ 超长
    ],
)
def test_markdown_channel_boundaries(tmp_path, markdown_channel,
                                     sent_bytes, expect_silent):
    """markdown 通道（4096 / 3900）的边界 —— 修复前的旧语义在这里仍然成立。

    保留这组用例是为了证明：本次修复**只**收紧了 text 通道，
    没有把 markdown 通道的行为一起改坏。
    """
    issues = length_issues(tmp_path, sent_bytes)
    assert bool(issues) is not expect_silent, (
        f"markdown 通道 sent={sent_bytes} 期望 "
        f"{'静默' if expect_silent else '报出'}，实际 issues={issues}"
    )


# ------------------------------------------------------------------
# 静态护栏：禁止再把通道常量写死回去
# ------------------------------------------------------------------

def test_check_push_format_must_not_hardcode_markdown_constants():
    """源码护栏：check_push_format 里不得再出现 markdown 通道的写死常量。

    行为级用例只能证明「当前字节数下会红」，拦不住后人把 2048 直接写成
    另一个魔数。这条直接从函数源码层面钉死「必须经 effective_channel() 取」。
    """
    src = inspect.getsource(qc.check_push_format)

    assert "effective_channel()" in src, (
        "check_push_format 必须调用 effective_channel() 取实际通道"
    )
    assert "WECOM_MARKDOWN_LIMIT" not in src, (
        "不得写死 markdown 通道上限 4096 —— 生产默认 text（2048），"
        "写死 4096 会让 2049~3600 字节的推送完全静默（2026-09-17 漏报）"
    )
    assert "MARKDOWN_CHUNK_BUDGET" not in src, (
        "不得写死 markdown 分段预算 3900 —— 理由同上，text 通道预算是 1800"
    )


def test_alert_line_3600_semantics_are_untouched():
    """3600 这条「体量偏大」预警线的判定式必须原样保留。

    它的语义是**与通道无关**的提示，本次只修通道基准，不动这条线。
    （text 通道下 3600 > 上限 2048，所以它会被上面的分支先吃掉、实际不
    触发 —— 这是正确的，不要为了让这条活着而扭曲判定顺序。）
    """
    src = inspect.getsource(qc.check_push_format)

    assert "LENGTH_ALERT_BYTES" in src
    assert qc.LENGTH_ALERT_BYTES == 3600

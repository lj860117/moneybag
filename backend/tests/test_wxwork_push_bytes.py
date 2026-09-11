#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B 系列（推送截断修复）回归测试。

覆盖：
  B1 _split_message / send_markdown 改按【字节】判断 + 分段无损
  B2 daily_push_quality_check 改按【字节】且补回信封开销
  B3 send_text_capped 证据链截断留记录
  B4 send_markdown 走真 markdown 通道（msgtype=markdown，上限 4096）
  B5 _length_guard 只告警不裁剪

⚠️ 全程离线：任何测试都不得真的调用企微接口（会打扰真实用户）。
   需要验证发送行为时一律 monkeypatch wxwork_push._send_raw。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import wxwork_push as wp  # noqa: E402


# ------------------------------------------------------------------
# 工具
# ------------------------------------------------------------------

def make_text(target_bytes: int, unit: str = "中文字符测试内容，") -> str:
    """生成 UTF-8 字节数**精确等于** target_bytes 的中文文本。

    先用 27 字节的中文 unit 填满，余下的零头用单字节 ASCII 补齐，
    这样才能精确复刻真实存档的字节数（2035 / 2142 / 2172 等）。
    """
    unit_bytes = len(unit.encode("utf-8"))
    out = []
    size = 0
    while size + unit_bytes <= target_bytes:
        out.append(unit)
        size += unit_bytes
    rest = target_bytes - size
    if rest:
        out.append("x" * rest)
    text = "".join(out)
    assert wp.byte_len(text) == target_bytes, "make_text 必须字节精确"
    return text


class _Recorder:
    """替换 _send_raw，记录每次实际发出的 (msgtype, content)，不碰网络。"""

    def __init__(self):
        self.calls = []

    def __call__(self, content, user_id="", markdown=False):
        self.calls.append({
            "content": content,
            "user_id": user_id,
            "msgtype": "markdown" if markdown else "text",
        })
        return {"ok": True, "data": {"errcode": 0}}


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(wp, "_send_raw", rec)
    monkeypatch.setattr(wp, "_record_event", lambda event: None)
    monkeypatch.delenv("WXWORK_FORCE_TEXT", raising=False)
    return rec


# ------------------------------------------------------------------
# 常量自证
# ------------------------------------------------------------------

def test_channel_limits_are_bytes_not_chars():
    """通道上限必须是字节，且 markdown 上限必须大于 text 上限。"""
    assert wp.WECOM_TEXT_LIMIT == 2048
    assert wp.WECOM_MARKDOWN_LIMIT == 4096
    assert wp.WECOM_MARKDOWN_LIMIT > wp.WECOM_TEXT_LIMIT


def test_chunk_budgets_leave_headroom_for_tag():
    """分段预算必须给 (i/N) 分片标记留余量，否则加了标记就顶穿上限。"""
    assert wp.TEXT_CHUNK_BUDGET < wp.WECOM_TEXT_LIMIT
    assert wp.MARKDOWN_CHUNK_BUDGET < wp.WECOM_MARKDOWN_LIMIT
    # 最坏情况标记 "(12/12)\n" 约 8 字节，两个预算都留得下
    assert wp.WECOM_TEXT_LIMIT - wp.TEXT_CHUNK_BUDGET >= 8
    assert wp.WECOM_MARKDOWN_LIMIT - wp.MARKDOWN_CHUNK_BUDGET >= 8


def test_envelope_overhead_is_52_bytes_and_derivable():
    """信封开销必须是可推导的，不能是个拍脑袋的魔数。

    send_daily_report_to 拼装：title + "\\n\\n" + report + "\\n\\n" + "⏰ " + 时间戳
    """
    title = "☀️ 钱袋子早安简报"
    stamp = "⏰ 2026-09-11 08:30"
    derived = wp.byte_len(title) + 2 + 2 + wp.byte_len(stamp)
    assert derived == 52, f"推导出的信封开销 {derived} 字节，与常量不符"
    assert wp.PUSH_ENVELOPE_OVERHEAD_BYTES == derived


def test_byte_len_counts_utf8_bytes():
    assert wp.byte_len("") == 0
    assert wp.byte_len("abc") == 3
    assert wp.byte_len("中") == 3
    assert wp.byte_len("☀️") == 6      # U+2600 + U+FE0F
    assert wp.byte_len("中文abc") == 9


# ------------------------------------------------------------------
# B1：_split_message 按字节 + 无损
# ------------------------------------------------------------------

@pytest.mark.parametrize("budget", [64, 200, 1800, 3900])
@pytest.mark.parametrize("multiplier", [1.5, 3.2, 7.0])
def test_split_is_lossless_and_within_budget(budget, multiplier):
    """① 每段字节数 <= 预算；② "".join(chunks) == 原文（一个字符都不丢）。"""
    text = make_text(int(budget * multiplier))
    chunks = wp._split_message(text, budget)

    assert len(chunks) >= 2, "输入远超预算，必须分片"
    for c in chunks:
        assert c, "不得产生空段"
        assert wp.byte_len(c) <= budget, f"段超预算：{wp.byte_len(c)} > {budget}"
    assert "".join(chunks) == text, "分段后拼接必须与原文无损相等"


def test_split_handles_multibyte_and_emoji_without_breaking_chars():
    """切点必须落在字符边界上，绝不把汉字/emoji 切成半个。"""
    text = ("📊 组合温度计：市场情绪偏暖。\n\n" * 120) + "结尾🔚"
    for budget in (100, 333, 1000):
        chunks = wp._split_message(text, budget)
        assert "".join(chunks) == text
        for c in chunks:
            assert wp.byte_len(c) <= budget
            c.encode("utf-8").decode("utf-8")  # 能无损 round-trip 即未切坏字符


def test_split_is_byte_based_not_char_based():
    """负面控制：900 个汉字 = 2700 字节 > 1800 预算，但字符数 900 < 1800。

    旧的按字符判断在这里**不会**分片（2700 字节直发 → 被企微截断）；
    按字节判断必须分片。这条用来钉死「单位必须是字节」。
    """
    text = "测" * 900
    assert len(text) == 900 < 1800, "字符数确实没超旧阈值（旧逻辑不会分片）"
    assert wp.byte_len(text) == 2700 > wp.TEXT_CHUNK_BUDGET

    chunks = wp._split_message(text, wp.TEXT_CHUNK_BUDGET)
    assert len(chunks) >= 2
    for c in chunks:
        assert wp.byte_len(c) <= wp.TEXT_CHUNK_BUDGET
    assert "".join(chunks) == text


def test_split_prefers_paragraph_boundary():
    """有段落分隔时，应在 \\n\\n 处切，而不是把段落拦腰斩断。"""
    para = "这是第一段的内容。" * 20
    text = "\n\n".join([para] * 8)
    chunks = wp._split_message(text, 500)
    assert len(chunks) >= 2
    assert "".join(chunks) == text
    # 分隔符被上一段吸收 → 下一段顶部不应以换行开头
    for c in chunks[1:]:
        assert not c.startswith("\n")


def test_split_rejects_non_positive_budget():
    with pytest.raises(ValueError):
        wp._split_message("abc", 0)


def test_split_short_text_returns_single_chunk():
    for budget in (10, 1800, 3900):
        assert wp._split_message("短文本", budget) == ["短文本"]


# ------------------------------------------------------------------
# B1：_truncate_bytes 安全截断
# ------------------------------------------------------------------

def test_truncate_bytes_never_splits_a_character():
    text = "中文测试内容" * 100
    for limit in (1, 2, 3, 4, 5, 10, 100, 999):
        out = wp._truncate_bytes(text, limit)
        assert wp.byte_len(out) <= limit
        out.encode("utf-8").decode("utf-8")  # 未切坏字符
        assert text.startswith(out)


# ------------------------------------------------------------------
# B4：send_markdown 走真 markdown 通道
# ------------------------------------------------------------------

def test_send_markdown_defaults_to_text_msgtype(recorder, monkeypatch):
    """2026-09-12 反转：默认必须走 text 通道。

    起因：B4 改成真 markdown 通道后，用户实测收到「暂不支持此消息类型，
    请在企业微信中查看」—— 部分接收端不渲染 markdown，等于什么都读不到。
    长度问题由按字节分段解决，不依赖 markdown 的 4096 上限。
    """
    monkeypatch.delenv("WXWORK_FORCE_MARKDOWN", raising=False)
    wp.send_markdown("**标题**\n\n正文内容", user_id="LeiJiang")
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["msgtype"] == "text"
    assert recorder.calls[0]["user_id"] == "LeiJiang"


def test_send_markdown_uses_markdown_only_when_explicitly_enabled(recorder, monkeypatch):
    """显式 WXWORK_FORCE_MARKDOWN=1 才走 markdown —— 能力保留，默认关闭。"""
    monkeypatch.setenv("WXWORK_FORCE_MARKDOWN", "1")
    wp.send_markdown("**标题**\n\n正文内容", user_id="LeiJiang")
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["msgtype"] == "markdown"


def test_send_markdown_preserves_markdown_syntax(recorder):
    """B4 之后不再把 ** 剥掉 —— 那是旧实现「假装 markdown」的根源。"""
    wp.send_markdown("**🚨 预警**\n\n> 引用行", user_id="LeiJiang")
    sent = recorder.calls[0]["content"]
    assert "**🚨 预警**" in sent
    assert "> 引用行" in sent


def test_send_markdown_does_not_mutate_content(recorder):
    """普通正文必须原样透传，一个字都不许改。"""
    body = "💱 美元/人民币: 7.1234（截至 09-11 11:21）\n\n【操作建议】\n• 持仓波动较大"
    wp.send_markdown(body, user_id="LeiJiang")
    assert recorder.calls[0]["content"] == body


def test_to_wecom_markdown_removes_unmatched_bold():
    """落单的 ** 会让企微吞掉后面整段 —— 必须消掉。"""
    assert wp._to_wecom_markdown("前面**粗体**后面**落单").count("**") == 2
    assert wp._to_wecom_markdown("**落单在结尾").count("**") == 0
    # 成对的不许动
    assert wp._to_wecom_markdown("**a** 和 **b**") == "**a** 和 **b**"


def test_to_wecom_markdown_strips_code_fence_but_keeps_text():
    """代码围栏要去掉，但其中文字要留下（旧实现整段删除 = 内容丢失）。"""
    out = wp._to_wecom_markdown("说明\n```python\nprint(1)\n```\n结束")
    assert "```" not in out
    assert "print(1)" in out, "代码正文不得被丢掉"


def test_send_markdown_splits_when_over_text_budget(recorder):
    """超 1800 字节必须分片，每段 ≤ text 上限 2048，且内容无损（扣掉分片标记）。"""
    body = make_text(9000)
    wp.send_markdown(body, user_id="LeiJiang")

    assert len(recorder.calls) >= 5, (
        f"9000 字节按 1800 预算应切 ≥5 段，实际 {len(recorder.calls)} 段"
    )
    for call in recorder.calls:
        assert call["msgtype"] == "text"
        assert wp.byte_len(call["content"]) <= wp.WECOM_TEXT_LIMIT

    # 去掉 "(i/N)" 标记后拼回来，必须与原文无损相等
    import re
    joined = "".join(
        re.sub(r"\n\(\d+/\d+\)$", "", c["content"]) for c in recorder.calls
    )
    assert joined == body


def test_default_channel_is_text_and_chunks_at_1800(recorder, monkeypatch):
    """默认（不设 WXWORK_FORCE_MARKDOWN）走 text 通道并按 1800 字节分段。

    2026-09-12 前这里是 WXWORK_FORCE_TEXT=1 逃生开关；反转默认后，
    text 成为常态，开关改为反向的 WXWORK_FORCE_MARKDOWN。
    """
    monkeypatch.delenv("WXWORK_FORCE_MARKDOWN", raising=False)
    body = make_text(5000)
    wp.send_markdown(body, user_id="LeiJiang")
    assert len(recorder.calls) >= 3
    assert all(c["msgtype"] == "text" for c in recorder.calls)
    for c in recorder.calls:
        assert wp.byte_len(c["content"]) <= wp.WECOM_TEXT_LIMIT


# ------------------------------------------------------------------
# 断言③：2026-09-11 晨报（2194 字节）改后应单条装下、不分片
# ------------------------------------------------------------------

def test_real_0911_briefing_splits_losslessly(recorder):
    """复刻 2026-09-11 LeiJiang 晨报的字节画像：body 2142B + 信封 52B = 2194B。

    B1 之前：2194B > text 上限 2048B，且旧逻辑按【字符】判断（914 字符 < 1800）
            → 判定为「不分段」→ 单条直发被硬截断，丢【操作建议】+ 免责声明。
    现在：按【字节】分段，2194B 切成 2 段，每段 ≤2048B，内容一个字不少。
    """
    body = make_text(2142)
    assert abs(wp.byte_len(body) - 2142) <= 4

    content = "☀️ 钱袋子早安简报" + "\n\n" + body + "\n\n⏰ 2026-09-11 08:30"
    total = wp.byte_len(content)
    assert 2190 <= total <= 2198, f"复刻字节数偏离实测：{total}"

    wp.send_markdown(content, user_id="LeiJiang")
    assert len(recorder.calls) == 2, (
        f"2194 字节按 1800 预算应切 2 段，实际 {len(recorder.calls)} 段"
    )
    for call in recorder.calls:
        assert call["msgtype"] == "text"
        assert wp.byte_len(call["content"]) <= wp.WECOM_TEXT_LIMIT

    # 扣掉 "(i/N)" 分片标记后必须与原文完全一致 —— 分段是无损的
    import re
    joined = "".join(
        re.sub(r"\n\(\d+/\d+\)$", "", c["content"]) for c in recorder.calls
    )
    assert joined == content, "分段必须无损，一个字都不少"


def test_old_char_logic_would_not_have_split_the_0911_briefing():
    """负面控制：钉死「按字符判断」正是当年漏掉这个 bug 的根因。

    2194 字节 / 914 字符 —— 字符数远低于旧的 1800 阈值，所以旧的
    `if len(plain) <= MAX_CHUNK` 判定为「不分段」，直接单条 2194 字节发出去被截断。
    """
    body = make_text(2142)
    content = "☀️ 钱袋子早安简报" + "\n\n" + body + "\n\n⏰ 2026-09-11 08:30"
    assert len(content) < 1800, "旧逻辑的判据（字符数）确实不触发分片"
    assert wp.byte_len(content) > wp.WECOM_TEXT_LIMIT, "但字节数确实超了 text 上限"


# ------------------------------------------------------------------
# B5：长度护栏 —— 只告警，绝不静默砍
# ------------------------------------------------------------------

def test_length_guard_levels(monkeypatch):
    # 护栏会落事件记录；测试里拦掉，保证不往真实存档目录写任何东西
    monkeypatch.setattr(wp, "_record_event", lambda event: None)
    assert wp._length_guard(1000, source="t") == "ok"
    assert wp._length_guard(wp.LENGTH_ALERT_BYTES, source="t") == "ok"
    assert wp._length_guard(wp.LENGTH_ALERT_BYTES + 1, source="t") == "warn"
    assert wp._length_guard(wp.MARKDOWN_CHUNK_BUDGET, source="t") == "warn"
    assert wp._length_guard(wp.MARKDOWN_CHUNK_BUDGET + 1, source="t") == "split"


def test_length_guard_records_event_but_never_truncates(monkeypatch, recorder):
    """告警必须落事件记录，且内容一个字都不少地发出去。"""
    events = []
    monkeypatch.setattr(wp, "_record_event", events.append)

    body = make_text(3700)
    wp.send_markdown(body, user_id="LeiJiang")

    assert events, "越过告警线必须落一条事件"
    assert events[0]["kind"] == "length_alert"
    assert events[0]["level"] == "warn"
    assert events[0]["bytes"] == wp.byte_len(body)

    # 扣掉 "(i/N)" 分片标记后必须与原文一致 —— 告警只记录，绝不动内容
    import re
    sent = "".join(
        re.sub(r"\n\(\d+/\d+\)$", "", c["content"]) for c in recorder.calls
    )
    assert sent == body, "护栏绝不能裁剪内容"


def test_length_guard_alert_line_leaves_headroom():
    """告警线要给通道上限留余量（团队定 3600，给 4096 留 ~496 字节）。"""
    assert wp.LENGTH_ALERT_BYTES < wp.WECOM_MARKDOWN_LIMIT
    assert wp.WECOM_MARKDOWN_LIMIT - wp.LENGTH_ALERT_BYTES >= 400


# ------------------------------------------------------------------
# B3：send_text_capped —— 证据链宁可截断，但必须留记录
# ------------------------------------------------------------------

def test_send_text_capped_passthrough_when_short(recorder):
    wp.send_text_capped("短消息", user_id="LeiJiang", source="t")
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["msgtype"] == "text"
    assert recorder.calls[0]["content"] == "短消息"


def test_send_text_capped_records_truncation_event(monkeypatch, recorder):
    """超 2048 字节：内容照截，但必须落一条 kind=truncated 的事件。"""
    events = []
    monkeypatch.setattr(wp, "_record_event", events.append)

    body = make_text(3000)
    orig_bytes = wp.byte_len(body)
    wp.send_text_capped(body, user_id="LeiJiang", source="closing_review_hc")

    assert len(events) == 1
    ev = events[0]
    assert ev["kind"] == "truncated"
    assert ev["source"] == "closing_review_hc"
    assert ev["orig_bytes"] == orig_bytes
    assert ev["limit_bytes"] == wp.WECOM_TEXT_LIMIT
    assert ev["kept_bytes"] <= wp.WECOM_TEXT_LIMIT
    assert ev["lost_tail"], "必须记录被切掉的内容片段，便于复盘"

    # 内容确实被截断了（证据链场景的既定取舍），但截断是字节安全的
    sent = recorder.calls[0]["content"]
    assert wp.byte_len(sent) <= wp.WECOM_TEXT_LIMIT
    sent.encode("utf-8").decode("utf-8")
    assert body.startswith(sent)


def test_send_text_has_no_length_protection_by_design(recorder):
    """send_text 本身**不做**长度保护（裸发），必须显式改用 capped / markdown。"""
    body = make_text(3000)
    wp.send_text(body, user_id="LeiJiang")
    assert wp.byte_len(recorder.calls[0]["content"]) > wp.WECOM_TEXT_LIMIT


# ------------------------------------------------------------------
# B2：daily_push_quality_check 按字节 + 补信封
# ------------------------------------------------------------------

def _import_quality_check():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    import daily_push_quality_check as qc
    return qc


def test_quality_check_is_byte_based_not_char_based(tmp_path):
    """负面控制：1200 字符 < 旧的 2048「字符」阈值 → 旧检查放行；
    但 3600 字节 + 52 信封 = 3652 > 告警线 3600 → 新检查必须报出来。

    复刻的正是 2026-09-11 BuLuoGeLi 晨报那一类：
    字符数看着很安全，字节数其实早就撞线了（body 2035B → 发送 2087B）。
    """
    qc = _import_quality_check()
    body = "测" * 1200
    assert len(body) == 1200 < 2048, "旧检查（按字符）确实会放行（负面控制成立）"
    assert wp.byte_len(body) == 3600

    f = tmp_path / "2026-09-11_briefing_BuLuoGeLi.txt"
    f.write_text(body, encoding="utf-8")

    issues = qc.check_push_format(str(f))
    assert any("字节" in i for i in issues), f"必须按字节报出，实际 issues={issues}"


def test_quality_check_counts_envelope_overhead(tmp_path):
    """B2 的关键：档案里只有 body，不补 52 字节信封就会系统性漏判。

    body = 4050B：
      不补信封 → 4050 < 4096，放行（错）
      补上信封 → 4102 > 4096，必须报 ❌
    """
    qc = _import_quality_check()
    body = make_text(4050)
    assert wp.byte_len(body) < wp.WECOM_MARKDOWN_LIMIT, "不补信封时确实不超（前提成立）"
    assert wp.byte_len(body) + wp.PUSH_ENVELOPE_OVERHEAD_BYTES > wp.WECOM_MARKDOWN_LIMIT

    f = tmp_path / "2026-09-20_briefing_LeiJiang.txt"
    f.write_text(body, encoding="utf-8")
    issues = qc.check_push_format(str(f))
    assert any(("❌" in i and "字节" in i) for i in issues), f"issues={issues}"


def test_quality_check_flags_multi_message_split(tmp_path):
    """超过分段预算（3900）会拆成多条：内容无损，但必须让人知道。"""
    qc = _import_quality_check()
    body = make_text(3850)  # +52 = 3902 > 3900
    f = tmp_path / "2026-09-21_briefing_LeiJiang.txt"
    f.write_text(body, encoding="utf-8")
    issues = qc.check_push_format(str(f))
    assert any("分段" in i for i in issues), f"issues={issues}"
    assert not any("❌" in i for i in issues)


def test_quality_check_warns_before_hard_limit(tmp_path):
    qc = _import_quality_check()
    body = make_text(3650)  # +52 = 3702 > 3600 告警线，但 < 3900
    f = tmp_path / "2026-09-12_briefing_LeiJiang.txt"
    f.write_text(body, encoding="utf-8")
    issues = qc.check_push_format(str(f))
    assert any("告警线" in i for i in issues), f"issues={issues}"
    assert not any("❌" in i for i in issues)


def test_quality_check_passes_normal_length(tmp_path):
    qc = _import_quality_check()
    body = make_text(1500)  # +52 = 1552，远低于 3600
    f = tmp_path / "2026-09-04_briefing_LeiJiang.txt"
    f.write_text(body, encoding="utf-8")
    issues = qc.check_push_format(str(f))
    assert not any(("字节" in i and "长" in i) for i in issues), issues

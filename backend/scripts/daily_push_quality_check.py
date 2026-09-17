#!/usr/bin/env python3
"""
每日推送质量评估脚本
- 检查今日所有推送内容（存档在 /opt/moneybag/data/logs/pushes/）
- 评估：截断、幻觉、数据源、AI分析质量、推送格式
- 有问题发企微告警
"""
import os
import sys
import json
import re
import math
import datetime
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import PUSH_ARCHIVE_DIR
from services.persistence import atomic_write_json  # 铁律：JSON 落盘禁止裸 open().write()
from services.wxwork_push import (
    send_markdown,
    byte_len,
    effective_channel,
    WECOM_MARKDOWN_LIMIT,
    MARKDOWN_CHUNK_BUDGET,
    LENGTH_ALERT_BYTES,
    PUSH_ENVELOPE_OVERHEAD_BYTES,
)


# ===========================================================================
# 质检判定常量（2026-09-14 误报修复）
# ===========================================================================

# ---------- 检查2：「基金估算净值」语境 ----------
# 只有命中这些写法，才要求正文标注估值时间。
#
# 为什么用**白名单（正向匹配）**而不是「黑名单剔除估值百分位之类」：
# 黑名单要跟着 AI 的措辞不断补漏，而 AI 研判是自由文本，措辞几乎不受控
# （实测 106 份存档里出现过 估值百分位 / 估值分位 / 估值偏高 / 估值偏贵 /
# 估值高位 / 估值合理 / 估值修复 / 估值比过去89%的时间都贵 ……）。黑名单
# 每漏一个就是一次新误报 —— 2026-09-14 那次告警正是这么来的。白名单反过来
# 只对「确实在报一个净值/涨跌数字」的写法敏感，对市场估值类描述天然免疫。
#
# 实证（服务器 data/logs/pushes/*_briefing_*.txt 106 份）：
#   「估值/估算」共出现 61 处，**全部**是市场估值语境，0 处基金估算净值
#   （估算净值 / 实时估值 / 盘中估值 这些词在语料里一次都没出现过）。
#   即：旧规则 100% 误报，从未真正抓到过一次真问题。
_FUND_EST_NAV_RE = re.compile(
    # 明确的「估算净值」类措辞
    r"(?:估算净值|净值估算|实时估值|盘中估值|场内估值|基金估值"
    r"|估算涨幅|估算涨跌|估算收益率)"
    # 或「估算/估值」后紧跟一个 4 位小数的净值数字（1.2345 / 0.9876）
    # —— 用 3~4 位小数是为了避开「估值83%以上」「估值分位88.5%」这类
    #    市场估值描述（它们是整数或 1 位小数，且中间还隔着"百分位/分位"）。
    r"|(?:估算|估值)\s*[0-9]+\.[0-9]{3,4}"
)

# ---------- 检查3：分段空行阈值 ----------
# 实测数据（2026-09-14 取自服务器 /opt/moneybag/data/logs/pushes/
# *_briefing_*.txt，共 106 份，覆盖 2026-06-29 ~ 2026-09-14）：
#
#   空行数分布： 10 → 63 份 | 11 → 5 | 12 → 30 | 13 → 6 | 26 → 2
#   正常带 10~13（104/106 份），min 10 / 中位数 10 / 均值 11.08 / p95 = 13
#   且这 104 份全部落在企微字节预算内（最大 2172B，含信封 52B 仍远低于
#   4096B 通道上限）—— 换句话说「正常晨报的空行上界就是 13」。
#
#   仅有的 2 份 26 空行是 2026-07-03 的 LeiJiang / BuLuoGeLi 两份，
#   **是真阳性**：正文把 AI 的 prompt 原文泄漏了进去（开头即
#   "好的，用户让我基于提供的市场数据写一段小结…"），67 行 / 4.7KB，
#   是正常晨报的 2 倍多。这个必须继续报出来。
#
# 旧阈值 10 的问题：会误伤 43/106 = 40.6% 的正常晨报（11 空行即触发），
# 2026-09-14 那份 12 空行就炸了 —— 而 09-11 的 10 空行刚好躲过，所以
# 看起来像"偶发"，实则是阈值压在正常带的下沿上。
#
# 取 20 的依据：比实测正常带上界 13 高 7（留足后续加板块的余量），
# 又比真阳性 26 低 6（不会放过 prompt 泄漏）。取值区间 14~25 都成立，
# 20 取中间偏保守，兼顾"不误报"与"不放过"。
#
# 曾评估过改用「空行数 / 非空行数」密度比以适配长报告，实测分离度太差故弃用：
# 正常样本最高 0.444（2026-07-16），而真阳性只有 0.634（2026-07-03），
# 可用区间窄到 0.45~0.63，任何取值都离某一侧太近。
MAX_BLANK_LINE_RUNS = 20


def check_truncation(content: str) -> list:
    """
    检查推送内容是否截断
    
    Returns:
        list: 检测到的问题列表
    """
    issues = []
    
    # 检查1：末尾是否不完整（以 "..." 结尾）
    if content.rstrip().endswith("..."):
        issues.append("⚠️ 内容可能截断：末尾有 '...'")
    
    # 检查2：括号是否匹配
    open_parens = content.count("（") + content.count("(")
    close_parens = content.count("）") + content.count(")")
    if open_parens != close_parens:
        issues.append(f"⚠️ 括号不匹配：开放 {open_parens}，闭合 {close_parens}")
    
    # 检查3：引号是否匹配
    quotes = content.count("\"") + content.count("'")
    if quotes % 2 != 0:
        issues.append("⚠️ 引号不匹配：奇数个引号")
    
    # 检查4：是否以不完整的中文字符结尾（如 "+0."）
    if re.search(r'[0-9]\.$', content.rstrip()):
        issues.append("⚠️ 内容可能截断：末尾有不完整数字（如 '+0.'）")
    
    return issues


def check_hallucination(push_file: str, actual_data: dict) -> list:
    """
    检查推送内容是否有幻觉（AI生成的数字 vs 实际数据）
    
    Args:
        push_file: 推送存档文件路径
        actual_data: 实际数据（从API获取）
    
    Returns:
        list: 检测到的问题列表
    """
    issues = []
    
    with open(push_file, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 检查1：基金涨跌幅是否准确
    fund_mentions = re.findall(r'([^\n\s]+)\((\d{6})\)[^\n]*?([+-]?\d+\.\d+)%', content)
    for fund_name, fund_code, mentioned_pct in fund_mentions:
        actual_pct = actual_data.get("funds", {}).get(fund_code, {}).get("change_pct")
        if actual_pct is not None:
            diff = abs(float(mentioned_pct) - actual_pct)
            if diff > 0.5:  # 误差超过 0.5%
                issues.append(
                    f"⚠️ 涨跌幅不匹配：AI 说 {fund_name} {mentioned_pct}%，"
                    f"实际 {actual_pct:.2f}%（差 {diff:.2f}%）"
                )
    
    # 检查2：板块描述是否准确
    sector_mentions = re.findall(r'(科技|消费|医药|金融|地产|新能源)板块[^\n]*?([+-]?\d+\.\d+)%', content)
    for sector_name, mentioned_pct in sector_mentions:
        actual_pct = actual_data.get("sectors", {}).get(sector_name, {}).get("change_pct")
        if actual_pct is not None:
            diff = abs(float(mentioned_pct) - actual_pct)
            if diff > 1.0:  # 误差超过 1%
                issues.append(
                    f"⚠️ 板块涨跌幅不匹配：AI 说 {sector_name} 板块 {mentioned_pct}%，"
                    f"实际 {actual_pct:.2f}%（差 {diff:.2f}%）"
                )
    
    return issues


def check_data_source(push_file: str) -> list:
    """
    检查数据源是否准确
    
    Returns:
        list: 检测到的问题列表
    """
    issues = []
    
    with open(push_file, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 检查1：QDII基金是否标注净值披露延迟
    #
    # 2026-09-14 修正一（文案错误）：原来写的是 "T+1"，但 QDII 投的是境外
    # 市场 —— 境外收盘晚 + 时差 + 汇率折算，净值普遍 **T+2** 才披露。告警
    # 文案本身是错的，会把修的人往错误方向带（往正文里塞 "T+1" 反而把事实
    # 说错）。此处改为 T+2。
    #
    # 2026-09-14 修正二（判据漏认）：判据原来只认 "T+1" / "延迟"。生成层
    # 现成的一句文案是「QDII 净值**滞后** 2 天」（services/fund_signal/
    # render.py）——「滞后」两个字都挂不上，照抄过去质检照样报。所以判据
    # 追加认 "T+2"。
    #
    # ⚠️ 判据只**放宽**不收紧：历史存档里按旧文案标注过 "T+1" 的一律仍判
    # 合规。收紧会让上百份历史存档一夜之间集体变 FAIL，那是新的一轮误报。
    qdii_mentions = re.findall(r'([^\n\s]+)\(QDII\)', content)
    if qdii_mentions:
        if not any(k in content for k in ("T+2", "T+1", "延迟")):
            issues.append("⚠️ QDII 基金未标注 T+2 披露延迟")
    
    # 检查2：基金估算净值是否标注了时间戳
    # 2026-09-14 误报修复：旧规则是
    #     if "估算" in content or "估值" in content:
    # 把「基金估算净值」和「市场估值水平」两个概念混为一谈。晨报里唯一命中
    # 「估值」的是 AI 研判的「估值百分位67.5%适中」——那是**市场估值分位
    # 指标**（见 services/glossary.py 的「估值百分位」词条、
    # services/portfolio.py:357 的 `估值百分位: {val_pct}%`），
    # 描述的是"当前估值在历史里贵不贵"，根本不是一个需要标注时间戳的
    # 净值数字。实测 106 份存档里「估值/估算」61 处全是这一类，
    # 旧规则 100% 误报。改为只在 _FUND_EST_NAV_RE 命中时才要求时间戳。
    if _FUND_EST_NAV_RE.search(content):
        # 检查是否有时间戳
        if "估值时间" not in content and "数据时间" not in content:
            issues.append("⚠️ 估值数据未标注时间")
    
    return issues


def check_ai_quality(push_file: str) -> list:
    """
    检查 AI 分析质量
    
    Returns:
        list: 检测到的问题列表
    """
    issues = []
    
    with open(push_file, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 检查1：是否过于模板化（每次都说"科技板块强势"）
    template_phrases = ["科技板块强势", "市场情绪较好", "建议关注"]
    phrase_count = sum(1 for phrase in template_phrases if phrase in content)
    if phrase_count >= 2:
        issues.append(f"⚠️ AI 分析可能模板化：检测到 {phrase_count} 处模板用语")
    
    # 检查2：是否给出具体建议
    if "建议" in content or "推荐" in content:
        # 检查建议是否具体（包含具体基金代码/名称）
        if not re.search(r'\d{6}|[^\n\s]+\([^\n\s]+\)', content):
            issues.append("⚠️ AI 建议不够具体（缺少具体基金/股票）")
    
    # 检查3：盈亏锚点是否准确
    if "浮盈" in content or "浮亏" in content:
        # 检查是否有具体数字
        if not re.search(r'[+-]?\d+\.\d+%', content):
            issues.append("⚠️ 盈亏锚点缺少具体数字")
    
    return issues


def check_push_format(push_file: str) -> list:
    """
    检查推送格式是否正确（**完整** issue 列表，含预期类）。

    保持原有签名与返回类型，既有调用方 / 测试不受影响。需要把预期类单独
    分出来时用 check_push_format_classified()。

    Returns:
        list: 检测到的问题列表
    """
    issues, _expected = check_push_format_classified(push_file)
    return issues


def check_push_format_classified(push_file: str) -> tuple:
    """
    检查推送格式，并把「预期类」issue 单独分出来。

    预期类 = **长度**引发的、send_markdown 会按字节**无损分片**处理掉、不需要
    任何人处理的那几条：

      ① 超通道上限    → 拆成多条
      ② 仅超分段预算  → 拆成多条
      ③ 过 3600 告警线 → 只是「体量偏大」的提前预警，不产生任何分片动作

    生产定局走 text 通道（上限 2048），晨报 3472 字节必然拆多条 —— 这是**预期
    结果**，不是故障。所以这三条仍然显示、仍然扣分，但**不参与 FAIL 判定**：
    天天为此告警会训练人忽略它（告警疲劳），等真出事反而看不见。

    ⚠️ 只有长度类能进预期类。QDII 未标注 T+2 / AI 模板化 / 分段空行 /
    基金名称为空 这些是真问题，**一个都不许塞进来** —— 那是用漏报换清净。
    （2026-09-15 才修过 QDII 未标注，它是真 bug，必须继续报 FAIL。）

    Returns:
        tuple: (issues, expected_issues)。expected_issues 是 issues 的子集。
    """
    issues = []
    expected = []

    with open(push_file, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 检查1：基金名称是否显示（不是空的 "🔴 ()"）
    if re.search(r'🔴\s*\(\s*\)', content):
        issues.append("❌ 基金名称显示为空（'🔴 ()'）")
    
    if re.search(r'🟡\s*\(\s*\)', content):
        issues.append("❌ 基金名称显示为空（'🟡 ()'）")
    
    # 检查2：消息是否太长
    # v9.9.20 (B2)：原来写的是 `len(content) > 2048`，两个错误叠在一起：
    #   ① 单位错 —— 用「字符数」比「字节上限」。中文 3 字节/字，实测晨报 2.38~2.43
    #      字节/字符，字符判断会把真实体积系统性低估约 2.4 倍；
    #   ② 漏算信封 —— 档案里只有 archive_push 存的 body，不含 send_daily_report_to
    #      拼上的 title + "\n\n" + "\n\n⏰ 时间戳"（实测 52 字节）。
    #   两者叠加的后果：2026-09-11 BuLuoGeLi 晨报 body=2035B「通过检查」，
    #   实际发送 2087B > 2048B 被截断，监控却每晚 22:00 稳定全绿 —— bug 藏了很久。
    body_bytes = byte_len(content)
    sent_bytes = body_bytes + PUSH_ENVELOPE_OVERHEAD_BYTES

    # 2026-09-17（漏报修复）：上限 / 分段预算必须取**实际生效**的通道。
    #
    # 生产默认走 text 通道（`_force_text()` 默认 True，上限 2048 / 分段预算
    # 1800），只有显式 `WXWORK_FORCE_MARKDOWN=1` 才切 markdown（4096 / 3900）。
    # 前两级判定原来写死的是 markdown 的 4096 / 3900，后果是 **text 通道下
    # sent_bytes 落在 2049~3600 时三个分支全不命中 → 一行都不报、完全静默**。
    #
    # 铁证：2026-09-17 真实晨报存档 3610B（含 28B 的 "=== ... ===" 头行）
    # + 信封 52B = 3662B，text 通道（上限 2048）必然按 1800 预算拆成 3 条，
    # 质检却只报「接近告警线 3600」（3600 < 3662 才勉强报出来），还写成
    # 「距通道上限 4096 还剩 X 字节」—— 那个 4096 根本不是生产用的通道。
    # **漏报 + 错误基准**比「报了警但措辞不准」严重得多，所以这里统一改成
    # effective_channel()。
    #
    # （存档里的字节数每次都在变，别把 3662 / 3 条当定值 —— 这里只是记录
    # 2026-09-17 那一份的实测值，用于说明「text 通道下必然拆多条」。）
    #
    # ⚠️ 注意判定顺序：`LENGTH_ALERT_BYTES = 3600` 这条与通道无关的「体量偏大」
    # 预警线语义保持不变，但 text 通道下 3600 > 2048，所以 2049~3600 会被
    # 上面的「超上限 / 会分段」分支先吃掉，3600 分支在 text 下实际不触发 ——
    # 这是**正确**的，不要为了让 3600 分支活着而扭曲判定顺序。
    channel, channel_limit, chunk_budget = effective_channel()

    # 「会拆成几条」是运维真正要的信息：只说「会分段」，他还得自己拿计算器除。
    # 预算用 effective_channel() 给的 chunk_budget，绝不写死 1800 —— text 是
    # 1800、markdown 是 3900，写死就是下一个「拿错通道当基准」。
    #
    # 2026-09-17：这段原来只挂在最下面那个 `> LENGTH_ALERT_BYTES` 分支里，
    # 而 text 通道下要进那一层需 sent_bytes > 3600，可 text 上限只有 2048 ——
    # 恒不成立，是**100% 死代码**（留着会让下一个人误以为「超上限会提示分片」
    # 是已实现的功能）。现在挪到真正会触发的两级上。
    parts = math.ceil(sent_bytes / chunk_budget)
    split_note = f"将按 {chunk_budget} 字节预算无损拆分为 ≥{parts} 条"

    if sent_bytes > channel_limit:
        # 2026-09-17：这一级**刻意是 ⚠️ 而不是 ❌**，别改回去。
        #
        # send_markdown 会按 chunk_budget 无损分片，每一片都 < channel_limit，
        # 所以「总量超通道上限」**只意味着会拆成多条，内容一个字节都不丢**。
        # 生产已定局走 text 通道（WXWORK_FORCE_MARKDOWN 未设，上限 2048），
        # 09-17 晨报 3472 字节必然拆 2 条 —— 这是**预期结果**，不需要任何人
        # 处理。天天报 ❌ 却无需处理 = 训练人忽略告警（告警疲劳），等真出事
        # （内容被硬截断）时反而看不见。❌ 留给真事故。
        #
        # 真被截断时 `_record_event` 会落 truncation 事件，那是另一条发现
        # 路径，**不靠这条告警兜底** —— 所以下面那句「必须排查」只是提示，
        # 不构成把级别留在 ❌ 的理由。
        #
        # ⚠️ 降级的是**级别**，不是**是否上报**：这一级仍然必须产 issue。
        # 它曾经整段静默过（2049~3600 三阈值全不命中），那是更严重的事故。
        msg = (
            f"⚠️ 消息超长：{sent_bytes} 字节（body {body_bytes}B + 信封 "
            f"{PUSH_ENVELOPE_OVERHEAD_BYTES}B）> 企微 {channel} 通道上限 "
            f"{channel_limit} 字节 —— send_markdown 会按字节无损分段，"
            f"{split_note}（内容不丢，但用户会收到多条）；"
            f"若真被截断说明有调用方绕过了分段逻辑，必须排查"
        )
        issues.append(msg)
        expected.append(msg)  # 预期类①：无损分片，内容不丢
    elif sent_bytes > chunk_budget:
        msg = (
            f"⚠️ 消息会分段：{sent_bytes} 字节（body {body_bytes}B + 信封 "
            f"{PUSH_ENVELOPE_OVERHEAD_BYTES}B）> 企微 {channel} 通道分段预算 "
            f"{chunk_budget} 字节，{split_note}（内容无损，但阅读体验受损）"
        )
        issues.append(msg)
        expected.append(msg)  # 预期类②：同样是分片，同样无需处理
    elif sent_bytes > LENGTH_ALERT_BYTES:
        # 预期类③：3600 是「体量偏大」的提前预警，本身不产生任何分片动作，
        # 也没有任何需要人去做的动作，与 ①② 同属长度类，一并放行。
        #
        # 注（不是忘了处理）：text 通道下这一级恒不触发 —— 3600 > 上限 2048，
        # sent_bytes > 3600 必然先被上面的「超上限」分支吃掉。它只在
        # markdown（4096/3900）下才可能命中（3601~3900）。
        # 上限必须取**实际生效**的通道：生产默认 text（2048），写死 markdown 的
        # 4096 会把「早就超上限必须分片」说成「还剩几百字节」，完全误导。
        if sent_bytes <= channel_limit:
            headroom = (f"距 {channel} 通道上限 {channel_limit} 仅剩 "
                        f"{channel_limit - sent_bytes} 字节")
        else:
            headroom = (f"已超 {channel} 通道上限 {channel_limit} 字节 "
                        f"{sent_bytes - channel_limit} 字节")
        msg = (
            f"⚠️ 消息接近告警线：{sent_bytes} 字节（body {body_bytes}B + 信封 "
            f"{PUSH_ENVELOPE_OVERHEAD_BYTES}B）> {LENGTH_ALERT_BYTES} 字节，"
            f"{headroom}"
        )
        issues.append(msg)
        expected.append(msg)  # 预期类③

    # 检查3：分段是否合理
    # 2026-09-14 误报修复：旧阈值写死 `> 10`，而实测 106 份
    # 晨报的正常带就是 10~13（p95 = 13），等于把阈值压在正常带下沿上 ——
    # 误伤率 43/106 = 40.6%。阈值与依据见 MAX_BLANK_LINE_RUNS 的注释。
    # ⚠️ 这条**不是**预期类：空行 26 处是真阳性（AI prompt 泄漏导致正文膨胀），
    # 必须继续参与 FAIL 判定。
    blank_runs = content.count("\n\n")
    if blank_runs > MAX_BLANK_LINE_RUNS:
        issues.append(f"⚠️ 分段可能不合理：{blank_runs} 处空行")

    return issues, expected


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def resolve_date_arg(date_arg) -> str:
    """
    把日期参数解析成 YYYY-MM-DD。

    v9.9.24 (P0-1)：cron 一直传的是字面量 `--date today`（见 setup_cron.sh / 
    docs/ops/crontab.production.txt），而旧代码 `date_str = args.date` 原样透传，
    glob 变成 `today_*_LeiJiang.txt` → 永远匹配不到任何存档 → 走进
    "空结果 = 100 分 = 通过" 分支，这个检查从上线起就没真正跑过一次。

    支持：None / "" / "today" / "yesterday" / "YYYY-MM-DD"。
    其它格式直接抛 ValueError（由 main 转成退出码 2，不静默降级）。
    """
    raw = (date_arg or "").strip().lower()
    today = datetime.date.today()
    if raw in ("", "today", "now"):
        return today.strftime("%Y-%m-%d")
    if raw == "yesterday":
        return (today - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    if _DATE_RE.match(raw):
        # 校验是真日期（拦住 2026-13-45 这种）
        datetime.datetime.strptime(raw, "%Y-%m-%d")
        return raw
    raise ValueError(
        f"无法解析的日期参数 {date_arg!r}：只支持 today / yesterday / YYYY-MM-DD"
    )


def evaluate_push_quality(date_str: str, user_id: str = "LeiJiang") -> dict:
    """
    评估指定日期的推送质量
    
    Args:
        date_str: 日期字符串（如 "2026-06-16"）
        user_id: 用户ID
    
    Returns:
        dict: 评估结果
    """
    results = {
        "date": date_str,
        "user_id": user_id,
        "pushes": [],
        "total_issues": 0,
        "score": 100,
        "status": "PASS",
        "issues": [],
        "checks_skipped": [],
        # v9.9.47：预期类 issue 条数（长度类，会无损分片、无需处理）。
        # 它们照常进 total_issues、照常扣分，但**不参与 FAIL 判定**。
        "expected_issues": 0,
        "blocking_issues": 0,
    }
    
    # 查找今日的推送存档
    push_dir = Path(PUSH_ARCHIVE_DIR)
    push_files = sorted(push_dir.glob(f"{date_str}_*_{user_id}.txt"))
    
    if not push_files:
        # v9.9.24 (P0-1)：这里原来是 `results["error"] = ...; return`，
        # 而 score 仍保持初始值 100、total_issues 保持 0 → 调用方看到的是
        # "0 问题 / 100 分 / ✅ 通过"。**没有数据 ≠ 通过**，一次推送都没有的
        # 一天（推送挂了 / 存档路径不一致 / 日期没解析）必须判 FAIL。
        results["status"] = "FAIL"
        results["score"] = 0
        results["total_issues"] = 1
        results["archive_dir"] = str(push_dir)
        results["error"] = f"未找到 {date_str} 的推送存档"
        results["issues"].append(
            f"❌ 未找到 {date_str} 的推送存档（目录 {push_dir}，"
            f"匹配 {date_str}_*_{user_id}.txt）：无法验证推送质量，"
            f"可能是推送任务根本没执行 / 存档路径不一致 / 日期解析错误"
        )
        return results
    
    results["archive_dir"] = str(push_dir)
    
    # 评估每个推送
    for push_file in push_files:
        push_type = push_file.stem.split("_")[1]
        
        with open(push_file, "r", encoding="utf-8") as f:
            content = f.read()
        
        # 运行所有检查
        issues = []
        expected: list = []
        issues.extend(check_truncation(content))
        
        # 获取实际数据（用于幻觉检查）
        # TODO(v9.9.24 P0-2)：actual_data 恒为空 → check_hallucination 里的
        # `actual_pct is not None` 永远不成立，幻觉检查同样是空跑。这里先如实
        # 记进 checks_skipped（不上报成 issue，避免 P0-2 落地前天天刷告警），
        # 由 P0-2 接真实数据源后消除。
        actual_data = {}
        if not actual_data:
            results["checks_skipped"].append("hallucination")
        issues.extend(check_hallucination(str(push_file), actual_data))
        
        issues.extend(check_data_source(str(push_file)))
        issues.extend(check_ai_quality(str(push_file)))

        # v9.9.47：只有 check_push_format 会产生预期类（长度 / 无损分片），
        # 用 classified 版本把条数单独记下来。
        fmt_issues, fmt_expected = check_push_format_classified(str(push_file))
        issues.extend(fmt_issues)
        expected.extend(fmt_expected)

        # 记录结果
        push_result = {
            "file": push_file.name,
            "type": push_type,
            "issues": issues,
            "expected_issues": expected,
            "issue_count": len(issues),
        }
        results["pushes"].append(push_result)
        results["total_issues"] += len(issues)
        results["expected_issues"] += len(expected)
        results["score"] -= len(issues) * 5  # 每个问题扣 5 分

    results["score"] = max(0, results["score"])
    results["checks_skipped"] = sorted(set(results["checks_skipped"]))

    # v9.9.47：FAIL 只看「非预期」issue。
    #
    # 长度类（超上限 / 超分段预算 / 过 3600 线）在 text 通道下是**必然结果**
    # —— send_markdown 会无损分片，内容一个字节都不丢，没有任何动作需要人做。
    # 把它们算进 FAIL 会让质检天天 FAIL，最后没人看。
    #
    # ⚠️ 反过来，绝不能写反成「长度超了就整体跳过检查」：QDII 未标注 /
    # AI 模板化 / 基金名称为空 / 空行超标 这些是真问题，必须照常判 FAIL。
    blocking = results["total_issues"] - results["expected_issues"]
    results["blocking_issues"] = blocking
    if blocking > 0:
        results["status"] = "FAIL"

    return results


def send_alert_if_needed(results: dict):
    """
    如果有问题，发企微告警
    """
    # v9.9.24 (P0-1)：原来只判 `total_issues == 0`，而"找不到存档"时
    # total_issues 恒为 0 → 走 ✅ 分支。改为认 status，且 fatal（无存档）
    # 也要告警 —— 「没检查到」本身就是最该被看见的告警。
    #
    # v9.9.47：status == PASS 就**不发告警**，即使 total_issues > 0 ——
    # 多出来的那些是预期类（长度超限 → 无损分片），发了就是告警疲劳。
    if results.get("status") == "PASS":
        expected_n = results.get("expected_issues", 0)
        if expected_n:
            print(
                f"✅ 推送质量检查通过（{expected_n} 条预期类提示："
                f"长度超限会由 send_markdown 无损分片，内容不丢，无需处理）"
            )
        else:
            print("✅ 所有推送质量检查通过")
        return

    # 生成告警消息
    alert_msg = f"📊 {results['date']} 推送质量评估\n\n"
    alert_msg += f"结论：{results.get('status', 'FAIL')}\n"
    alert_msg += f"总分：{results['score']}/100\n"
    alert_msg += (
        f"检测到 {results['total_issues']} 处问题"
        f"（其中 {results.get('expected_issues', 0)} 条为预期类："
        f"长度超限会无损分片，无需处理；"
        f"{results.get('blocking_issues', 0)} 条需要处理）：\n\n"
    )
    
    # 无存档 / 其它致命问题（不属于任何单个 push）
    for fatal in results.get("issues", []):
        alert_msg += f"{fatal}\n"
    if results.get("issues"):
        alert_msg += "\n"
    
    for push in results["pushes"]:
        if push["issue_count"] > 0:
            alert_msg += f"❌ {push['type']}（{push['file']}）\n"
            for issue in push["issues"]:
                alert_msg += f"  {issue}\n"
            alert_msg += "\n"
    
    alert_msg += "⚠️ 请及时修复\n"
    
    # 发送告警
    # v9.9.20 (B2)：修参数顺序写反的 bug —— 原来是 send_markdown("LeiJiang", alert_msg)，
    # 而签名是 send_markdown(content, user_id="")，等于把 "LeiJiang" 当正文、
    # 把整段告警文本当 userId 发出去。这个告警其实从来没正常工作过。
    try:
        # v9.9.24 (P0-1)：send_markdown 返回 {"ok": ...}，原来只看「有没有抛异常」，
        # 发送失败也会打 ✅ 告警已发送 —— 又一处"假成功"。
        ret = send_markdown(alert_msg, user_id="LeiJiang")
        if isinstance(ret, dict) and not ret.get("ok", True):
            print(f"❌ 告警发送失败：{ret.get('error') or ret}")
        else:
            print("✅ 告警已发送")
    except Exception as e:
        print(f"❌ 告警发送失败：{e}")


def main():
    """
    主函数
    """
    import argparse
    
    parser = argparse.ArgumentParser(description="每日推送质量评估")
    parser.add_argument(
        "--date", type=str, default=None,
        help="评估日期：today / yesterday / YYYY-MM-DD（默认 today）",
    )
    parser.add_argument("--user", type=str, default="LeiJiang", help="用户ID")
    parser.add_argument("--alert", action="store_true", help="有问题发企微告警")
    parser.add_argument("--out", type=str, default=None, help="结果 JSON 落盘路径（原子写）")
    
    args = parser.parse_args()
    
    # 确定评估日期（v9.9.24 P0-1：cron 传的是字面量 "today"，必须 resolve）
    try:
        date_str = resolve_date_arg(args.date)
    except ValueError as e:
        parser.error(str(e))  # 退出码 2，不静默降级成"通过"
        return
    
    print(f"📊 开始评估 {date_str} 的推送质量...")
    
    # 评估推送质量
    results = evaluate_push_quality(date_str, args.user)
    
    # 打印结果
    print(json.dumps(results, ensure_ascii=False, indent=2))
    
    if results.get("checks_skipped"):
        print(
            f"⚠️ 以下检查被跳过（未取到真实数据，结果不完整）："
            f"{', '.join(results['checks_skipped'])}"
        )
    
    if args.out:
        atomic_write_json(Path(args.out), results)
        print(f"📝 结果已写入 {args.out}")
    
    # 有问题发告警
    if args.alert:
        send_alert_if_needed(results)
    
    # v9.9.24 (P0-1)：退出码必须能反映结论，否则 cron 永远看不到失败
    #
    # v9.9.47：退出码只认 status。原来这里还有 `or total_issues > 0`，
    # 而预期类也会让 total_issues > 0 —— 不去掉的话，长度超限（PASS）
    # 照样退出 1，等于白改。
    if results.get("status") == "FAIL":
        print(
            f"❌ 推送质量检查未通过：{results['total_issues']} 处问题"
            f"（{results.get('blocking_issues', 0)} 条需处理 / "
            f"{results.get('expected_issues', 0)} 条预期类），"
            f"score={results['score']}/100，date={date_str}"
        )
        sys.exit(1)

    print(
        f"✅ 所有推送质量检查通过（score={results['score']}/100"
        f"，{results.get('expected_issues', 0)} 条预期类提示："
        f"长度超限会无损分片，无需处理）"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()

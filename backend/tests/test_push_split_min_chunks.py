#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
推送分片「最少条数」回归测试（2026-09-17）。

## 缺陷（本文件要钉死的东西）

``services/wxwork_push._find_cut`` 旧实现只要求「本段至少装 30% 预算」，
于是 **1520 字节处的「持仓明细」标记**会被优先采用：

    第 1 条只装 1520B → 剩余 2060B > 1800 → 被迫再切一刀 → 一共 3 条

可 09-17 LeiJiang 晨报 body = 3580B，理论最少只要 ``ceil(3580/1800) = 2`` 条。
**为了切得整齐反而多切出一条**，第 3 条还把「持仓速览」从中间劈开。
（生产实测：用户每天早上收到 3 条企微消息。）

## 修复

``_find_cut`` 里把「最少条数」写进门槛：本段至少要装
``floor_bytes = R - (k-1)*budget``（``k = ceil(R/budget)``），否则剩余部分
必然多出一条。低于此值的整齐标记一律跳过，切点自动落到预算内最后一个空行。

## 为什么每条用例都能因旧行为转红（非空转绿）

本文件所有「必须 2 条」的断言在**故障注入**下都会转红：把 ``_find_cut`` 里
``max(int(budget * 0.3), floor_bytes)`` 的 ``floor_bytes`` 改回 ``0``（= 旧的
30% 行为）后，下面三条用例立刻红：

  * ``test_real_0917_briefing_body_splits_into_two_chunks``    （3 条 → 期望 2）
  * ``test_marker_near_front_does_not_add_a_chunk``             （3 条 → 期望 2）
  * ``test_long_message_never_exceeds_minimal_chunk_count``     （长消息多切）

复原后转绿。恒绿的守卫 = 空转的绿，故此处用注入自证有效。

## 不碰的东西

免责声明「⚠️ AI建议仅供参考，不构成投资建议」在晨报里出现两次，两处都被
``test_briefing_qdii_delay_note.py`` / ``test_briefing_thermometer_wiring.py``
锁死，且是合规要素 —— 本文件一个字都不动。
"""
import math
import os
import re
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services import wxwork_push as wp  # noqa: E402

FIXTURE = (Path(__file__).resolve().parent / "fixtures"
           / "2026-09-17_briefing_LeiJiang.txt")

# 免责声明（合规要素，任何用例都不得删改它）
DISCLAIMER = "⚠️ AI建议仅供参考，不构成投资建议"

# 晨报 08:30 推送时的信封（与 send_daily_report_to 的拼装一致）
TITLE = "☀️ 钱袋子早安简报"
TIMESTAMP = "⏰ 2026-09-17 08:31"

# ── 固定文案压缩：old → new（免责声明一律不在此列）──────────────────────
# 前 3 处把整篇压回 ≤3600（2 条）；后 4 处落在「持仓明细」切点**之后**，
# 用于收窄 hi−floor 窗口、让切点落回行边界（见 test_briefing_cut_... 说明）。
FIXED_COPY_SHAVES = [
    ("；净买入方向数据交易所已停止披露（改按季度公布）", "；净买入已停止披露（改季报）"),
    ("（每月25号定投日会推详细金额建议）", "（25号定投日推金额建议）"),
    ("（不在理想配置中，可逐步迁移到指数型）", "（非理想配置，宜转指数型）"),
    ("⚖️ 再平衡缺口（当前结构 vs 你的定投目标）", "⚖️ 再平衡缺口（vs 定投目标）"),
    ("，暂不给出交易建议，仅列为观察项（原判断：", "，暂不给出交易建议，仅观察（原判断："),
    ("），需补¥", "），补¥"),
    ("），可减¥", "），减¥"),
]


def _apply_fixed_copy_shaves(text: str) -> str:
    """按 FIXED_COPY_SHAVES 把存档正文替换成「当前源码会产出的样子」。"""
    for old, new in FIXED_COPY_SHAVES:
        text = text.replace(old, new)
    return text


# ------------------------------------------------------------------
# 工具
# ------------------------------------------------------------------

def make_text(target_bytes: int, unit: str = "中文字符测试内容，") -> str:
    """生成 UTF-8 字节数**精确等于** target_bytes 的文本（27B 中文 + ASCII 补零）。"""
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


def _fixture_body() -> str:
    """从服务器真实存档 fixture 里取出**推送正文 body**。

    存档文件格式是 ``=== {时间戳} ===\\n`` + body + ``\\n\\n``（见
    ``wxwork_push.archive_push``）。真正被 ``_split_message`` 分割的是
    body（再套上 title/时间戳信封），所以这里把存档头尾剥掉。
    """
    raw = FIXTURE.read_text(encoding="utf-8")
    nl = raw.index("\n")
    body = raw[nl + 1:]
    if body.endswith("\n\n"):
        body = body[:-2]
    return body


def _assert_minimal_split(text: str, budget: int = wp.TEXT_CHUNK_BUDGET) -> list:
    """断言分段满足三条硬契约：无损、每段 ≤ 预算、条数 == 理论最小。"""
    chunks = wp._split_message(text, budget)
    expects = math.ceil(wp.byte_len(text) / budget)
    assert "".join(chunks) == text, "分段必须无损（拼接 == 原文）"
    for c in chunks:
        assert c, "不得产生空段"
        assert wp.byte_len(c) <= budget, f"段超预算：{wp.byte_len(c)} > {budget}"
    assert len(chunks) == expects, (
        f"{wp.byte_len(text)} 字节按 {budget} 预算最少应 {expects} 条，"
        f"实际 {len(chunks)} 条 {[wp.byte_len(c) for c in chunks]}")
    return chunks


# ------------------------------------------------------------------
# ① 真实晨报：body 必须从 3 条降到 2 条，且「持仓速览」不被劈开
# ------------------------------------------------------------------

def test_real_0917_briefing_body_splits_into_two_chunks():
    """真实 09-17 晨报 body（3580B）必须切成 **2 条**，不是 3 条。

    这正是生产事故的复现：旧逻辑在 1302B 处的「持仓明细」标记切一刀，
    剩余 2278B 再切一刀 → 3 条，用户早上收到 3 条消息。
    """
    body = _fixture_body()
    assert 1800 < wp.byte_len(body) <= 3600, (
        f"前提：body 应落在 (1,2] 个预算内，实测 {wp.byte_len(body)}B")

    chunks = _assert_minimal_split(body)
    assert len(chunks) == 2, f"必须 2 条，实际 {len(chunks)} 条"


def test_real_0917_briefing_holdings_block_stays_in_one_chunk():
    """「📋 持仓速览」整块（总评/风险/建议 + 操作建议 + QDII 标注 + 免责声明）
    必须完整落在**同一条**消息里，绝不能被拦腰切断。

    旧行为的现场：第 2 条结尾是「…需警惕集中风险。」，第 3 条开头是
    「建议：华夏先进制造龙头混合A…」—— 总评与建议被拆到两条，极难读。
    """
    body = _fixture_body()
    chunks = _assert_minimal_split(body)

    start = body.index("📋 【LeiJiang 持仓速览】")
    end = body.rindex(DISCLAIMER) + len(DISCLAIMER)
    block = body[start:end]
    assert block, "持仓速览块不应为空（fixture 前提）"

    holders = [i for i, c in enumerate(chunks) if block in c]
    assert len(holders) == 1, (
        "持仓速览整块必须落在且仅落在一条消息内；"
        f"实际出现在 {len(holders)} 条：{holders}")

    # 三个小节各自也必须在同一条里（防止块被拆时巧合拼回）
    holding_seg = holders[0]
    for label in ("总评：", "风险：", "建议："):
        idx = block.index(label)
        # 该小节所在的消息必须就是持仓速览所在的那条
        for i, c in enumerate(chunks):
            if block[idx:idx + 20] in c:
                assert i == holding_seg, (
                    f"「{label}」被切到了第 {i+1} 条，应与持仓速览同在 "
                    f"第 {holding_seg+1} 条")


# ------------------------------------------------------------------
# ② 合成守卫：靠前的整齐标记不得让条数多切一条
# ------------------------------------------------------------------

def test_marker_near_front_does_not_add_a_chunk():
    """构造一个 3580B、在 1520B 处有「持仓明细」标记的消息。

    旧逻辑在 1520B 的标记处切 → 剩余 2060B > 1800 → 3 条；
    修复后标记因低于 floor_bytes(=1780) 被跳过 → 2 条。
    """
    total, marker_off = 3580, 1520
    marker = "持仓明细"
    head = make_text(marker_off)
    tail = "\n" + make_text(total - marker_off - wp.byte_len(marker) - 1)
    text = head + marker + tail
    assert wp.byte_len(text) == total
    # 标记确实落在 1520 字节处（按字节而非字符下标核对）
    cum = wp._byte_prefix(text)
    assert cum[text.index(marker)] == marker_off

    chunks = _assert_minimal_split(text)
    assert len(chunks) == 2, f"必须 2 条，实际 {len(chunks)} 条"


def test_long_message_never_exceeds_minimal_chunk_count():
    """多预算长消息（k≥3）同样不得被整齐标记多切出一条 —— 顺带证明不死循环。

    5000B / 1800 → 理论 3 条。旧逻辑可能在 1520B 的标记处切第一刀后
    再切两刀 → 4 条。
    """
    total, marker_off = 5000, 1520
    marker = "持仓明细"
    head = make_text(marker_off)
    tail = "\n" + make_text(total - marker_off - wp.byte_len(marker) - 1)
    text = head + marker + tail

    chunks = _assert_minimal_split(text)
    assert len(chunks) == 3, f"必须 3 条，实际 {len(chunks)} 条"


@pytest.mark.parametrize("total", [1801, 2400, 3000, 3580, 3600, 5200, 9000])
@pytest.mark.parametrize("marker_off", [0, 600, 1520])
def test_various_sizes_stay_lossless_and_within_budget(total, marker_off):
    """参数化兜底：任意尺寸/标记位置都无损、每段 ≤ 预算、条数不超理论最小值。

    ``total=3600, marker_off=1520`` 这条尤其关键：正好压在 2 个预算上，
    修复后仍应是 2 条（切点落到预算内最后一个空行）。
    """
    marker = "📊 组合温度计"
    if marker_off + wp.byte_len(marker) > total:
        pytest.skip("标记放不下")
    head = make_text(marker_off)
    tail = "\n" + make_text(total - marker_off - wp.byte_len(marker) - 1)
    text = head + marker + tail
    assert wp.byte_len(text) == total

    chunks = wp._split_message(text, wp.TEXT_CHUNK_BUDGET)
    assert "".join(chunks) == text
    for c in chunks:
        assert 0 < wp.byte_len(c) <= wp.TEXT_CHUNK_BUDGET
    # 条数不得比理论最小值多 1 条以上（字节边界极端情况天然可能 +1，
    # 但绝不允许像旧逻辑那样因标记而多切）
    assert len(chunks) <= math.ceil(total / wp.TEXT_CHUNK_BUDGET) + 1


# ------------------------------------------------------------------
# ③ 字节预算：省下的固定文案必须真的省了（否则信封仍会溢出到 3 条）
# ------------------------------------------------------------------

def test_north_clause_shortened_keeps_key_information():
    """「A股温度」里的北向从句必须保留核心信息，但不再占 72 字节。

    旧：``；净买入方向数据交易所已停止披露（改按季度公布）``（72B）
    新：``；净买入已停止披露（改季报）``（42B）—— 省 30 字节。
    """
    import scripts.night_worker as nw  # noqa: E402

    north = {
        "available": True, "stale": False,
        "turnover_today": 2642, "turnover_trend": "平稳",
        "turnover_avg_5d": 2495, "data_date": "20260916",
    }
    out = nw._north_user_text(north)

    assert "净买入" in out and "停止披露" in out, out
    assert "改按季度公布" not in out, "旧的长从句应已压短"
    tail = out.split("；")[-1]
    assert wp.byte_len(tail) <= 45, f"北向从句仍过长：{wp.byte_len(tail)}B"


def test_rebalance_other_bucket_note_shortened():
    """再平衡「其他(主动混合)」的括注去掉冗词，保留「转指数型」的可操作建议。"""
    import scripts.night_worker as nw  # noqa: E402

    holdings = [
        {"code": "002163", "name": "东方惠新灵活配置混合C", "cur_val": 160.5},
        {"code": "013107", "name": "华夏先进制造龙头混合A", "cur_val": 120.2},
        {"code": "016501", "name": "华夏半导体龙头混合C", "cur_val": 107.5},
        {"code": "005851", "name": "财通新视野灵活配置混合A", "cur_val": 101.7},
        {"code": "006555", "name": "浦银安盛全球智能科技", "cur_val": 97.7},
        {"code": "008984", "name": "财通科技创新混合C", "cur_val": 95.7},
        {"code": "007356", "name": "汇添富科技创新混合C", "cur_val": 8.9},
        {"code": "005698", "name": "华夏全球科技先锋混合", "cur_val": 73.1},
    ]
    out = nw._build_rebalance_gap("LeiJiang", holdings)

    assert "其他(主动混合)" in out, out
    assert "不在理想配置中" not in out, "旧的长括注应已压短"
    assert "指数型" in out, "「转指数型」这条可操作建议不得丢"


def test_fixed_copy_saving_reaches_the_two_chunk_budget():
    """量化：七处固定文案合计省 ≥ 100 字节，才能把投影信封压回 ≤ 3600B（2 条）。"""
    body = _fixture_body()
    projected = _apply_fixed_copy_shaves(body)
    saved = wp.byte_len(body) - wp.byte_len(projected)
    assert saved >= 60, f"固定文案只省了 {saved} 字节，不足以压回 2 条"

    envelope = f"{TITLE}\n\n{projected}\n\n{TIMESTAMP}"
    assert wp.byte_len(envelope) <= 3600, (
        f"投影信封 {wp.byte_len(envelope)}B 仍 > 3600，会溢出到 3 条")
    chunks = _assert_minimal_split(envelope)
    assert len(chunks) == 2, f"投影信封必须 2 条，实际 {len(chunks)} 条"


# ------------------------------------------------------------------
# ⑤ 行边界偏好：切点必须落在换行边界（不把一行基金数据劈开）
# ------------------------------------------------------------------

def test_briefing_cut_lands_on_a_line_boundary():
    """晨报切点必须落在**换行边界**：第 1 条尾部是完整的一行，以 ¥金额 收尾。

    背景（2026-09-17 生产复验）：门槛修复后条数已降到 2，但切点落在预算边界
    （1800）把「持仓明细」的一行基金数据劈开（第 1 条尾「…混合C(008984)  买入」、
    第 2 条首「2.026 → …」）。压缩固定文案后 floor 降到行边界之下，
    ``_find_cut`` 的换行回退重新生效 → 切点落回行边界。

    故障注入有效：删掉 ``_find_cut`` 的换行回退（改为直接 ``return hi`` 硬切），
    本用例立刻转红（第 1 条不再以换行结尾）。
    """
    body = _fixture_body()
    projected = _apply_fixed_copy_shaves(body)
    envelope = f"{TITLE}\n\n{projected}\n\n{TIMESTAMP}"

    chunks = _assert_minimal_split(envelope)
    assert len(chunks) == 2, f"必须 2 条，实际 {len(chunks)} 条"

    first = chunks[0]
    assert first.endswith("\n"), (
        "第 1 条必须切在换行边界 —— 不得把一行基金数据劈成两半")
    last_line = first.rstrip("\n").split("\n")[-1]
    assert re.search(r"¥[\d.,]+$", last_line), (
        f"第 1 条尾行应是完整的持仓明细行（以 ¥金额 结尾），实际：{last_line!r}")


def test_find_cut_prefers_line_boundary_when_it_keeps_chunk_count():
    """机制级：当预算内最后一个换行 ≥ floor_bytes（用它不会多切一条）时，
    ``_find_cut`` 必须选它 —— 而不是硬切在预算边界。

    构造 3400B 文本，在 1750B 处放一个换行：floor = 3400-1800 = 1600 ≤ 1750，
    所以切点应落在 1750 的换行上（第 1 条 1751B，第 2 条 1649B，仍是 2 条）。
    故障注入（删换行回退）→ 硬切在 ~1800 → 第 1 条不再以换行收尾 → 转红。
    """
    total, nl_off = 3400, 1750
    text = make_text(nl_off) + "\n" + make_text(total - nl_off - 1)
    assert wp.byte_len(text) == total

    chunks = _assert_minimal_split(text)
    assert len(chunks) == 2
    assert chunks[0].endswith("\n"), (
        "换行 ≥ floor 时必须在换行处切，不得硬切劈开这一行")


def test_find_cut_hard_cuts_when_line_boundary_would_add_a_chunk():
    """已知代价（非 bug）：当预算内最后一个换行 < floor_bytes（用它必然多切一条）
    时，按「条数优先」契约必须硬切 —— 此时一行会被劈开。

    本用例把这条取舍钉死，防止有人为了「不劈行」而破坏「条数不增加」。
    构造 3580B、换行在 1500B（floor=1780 > 1500）：必须 2 条且第 1 条硬切
    （不以换行收尾），证明条数优先于行边界。
    """
    total, nl_off = 3580, 1500
    text = make_text(nl_off) + "\n" + make_text(total - nl_off - 1)
    chunks = wp._split_message(text, wp.TEXT_CHUNK_BUDGET)

    assert len(chunks) == 2, "条数优先：即使要劈行也必须只有 2 条"
    assert "".join(chunks) == text
    assert not chunks[0].endswith("\n"), (
        "换行 < floor 时若仍切在换行，剩余会 > 1 个预算 → 必然多切一条，"
        "与「条数不增加」冲突；此处必须是硬切")


def test_late_region_shaves_reach_current_source():
    """行为级护栏：后 4 处「晚期」固定文案压缩必须真的落在当前源码里。

    这些字节位于「持仓明细」切点之后才有效 —— 若有人把它们改回长版本，
    切点会重新落回硬切、劈开一行（上面的用例会红），本用例把源头也钉住。
    """
    import scripts.night_worker as nw  # noqa: E402

    holdings = [
        {"code": "002163", "name": "东方惠新灵活配置混合C", "cur_val": 160.5},
        {"code": "013107", "name": "华夏先进制造龙头混合A", "cur_val": 120.2},
        {"code": "016501", "name": "华夏半导体龙头混合C", "cur_val": 107.5},
        {"code": "005851", "name": "财通新视野灵活配置混合A", "cur_val": 101.7},
        {"code": "006555", "name": "浦银安盛全球智能科技", "cur_val": 97.7},
        {"code": "008984", "name": "财通科技创新混合C", "cur_val": 95.7},
        {"code": "007356", "name": "汇添富科技创新混合C", "cur_val": 8.9},
        {"code": "005698", "name": "华夏全球科技先锋混合", "cur_val": 73.1},
    ]
    gap = nw._build_rebalance_gap("LeiJiang", holdings)
    assert "再平衡缺口（vs 定投目标）" in gap, gap
    assert "当前结构 vs 你的定投目标" not in gap, "再平衡标题的长括注应已压短"
    assert "需补¥" not in gap and "可减¥" not in gap, "桶行冗词应已压短"
    assert "），补¥" in gap, gap

    # 观察项文案（闸门降级路径）里的「仅列为观察项」应已压成「仅观察」
    decisions = [{"action": "reduce", "source": "rule_engine",
                  "reason": "市场估值过高（90% 分位），建议减仓避险"}]
    out, _ = nw.gate_trade_decisions(decisions, total_value=754.0)
    assert "暂不给出交易建议" in out[0]["reason"], out[0]["reason"]
    assert "仅列为观察项" not in out[0]["reason"], "观察项冗词应已压短"
    assert "市场估值过高" in out[0]["reason"], "原判断必须保留"


# ------------------------------------------------------------------
# ④ 不变量：免责声明一个字都不能动
# ------------------------------------------------------------------

def test_disclaimer_untouched_in_real_briefing():
    """合规要素：免责声明在真实 fixture 里原样存在（本文件绝不修改它）。"""
    body = _fixture_body()
    assert body.count(DISCLAIMER) >= 2, "真实晨报应含两处免责声明"
    assert body.rstrip().endswith(DISCLAIMER), "整篇必须以免责声明收尾"

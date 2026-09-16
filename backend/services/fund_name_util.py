"""
钱袋子 — 基金名**展示截断**唯一真源（fund name shortening single source of truth）

为什么要有这个文件
------------------
2026-09-16 线上事故：晨报「持仓明细」对基金名做裸 `name[:12]` 盲截，
基金名 `浦银安盛全球智能科技(QDII)A` 被截成 `浦银安盛全球智能科技(Q`：

1. 用户看到残缺基金名，丢掉 QDII 这个关键信息；
2. 留下一个未闭合的 `(`，全文括号计数失衡，`scripts/daily_push_quality_check.py
   ::check_truncation()` 报「⚠️ 括号不匹配：开放 30，闭合 28」，整篇晨报
   score=90 FAIL。

裸切片当时散落在至少 3 处（scripts/night_worker.py 持仓明细、
api/shared_helpers.py 选基推荐 TOP3、scripts/monthly_report.py 家庭重叠基金），
修一处漏两处等于没修 —— 所以判据本体**只此一份**，放在这里。

设计约束（违反任何一条都会重新制造多套口径）
--------------------------------------------
1. **纯函数模块**：无 IO、无 logging、无 argparse / CLI 参数、不碰 sys.path、
   无模块级副作用。这样 `api/`（在线请求路径）和 `scripts/`（cron 脚本）
   都能安全 import —— 反向不成立：**不允许 api 反向 import scripts**，
   scripts 是带 CLI 副作用的可执行脚本，被 api 拉起来会把整条 cron 链路
   拖进请求路径。
2. **不许放宽 limit**。09-16 推送已 3759 字节（企微 4096 上限），
   放宽宽度是拿一个新事故换旧事故。做法是**截断后回退到括号配平的位置**。
3. **截断后为空时返回空串**，由调用方回落到基金代码（与 night_worker 持仓
   明细的既有行为一致），本模块不替调用方决定显示什么。
"""
from __future__ import annotations

from typing import List, Optional

# 基金名截断时用于配平括号的配对表：半角与全角**各自**配平，不允许混配
# （`(QDII）` 这种跨角混用同样视为不配平，回退丢弃）。
BRACKET_PAIRS = {"(": ")", "（": "）", ")": "(", "）": "（"}
BRACKETS = "()（）"


def balanced_bracket_prefix_len(text: str) -> int:
    """返回 text 从头算起、括号完全配平的最长前缀长度。

    配平用栈判定：遇到闭括号而栈为空（多余的闭括号）、或闭括号与栈顶开括号
    半角/全角不一致时立即停止；只有栈为空时才推进"已配平位置"。

    例：`浦银安盛全球智能科技(Q` 停在 10（即 `浦银安盛全球智能科技`），
    而不是停在 12 留下半截 `(Q`。

    Args:
        text: 待检查的字符串。

    Returns:
        int: 括号配平的最长前缀长度（0 表示从第一个字符起就不配平）。
    """
    stack: List[str] = []
    balanced_len = 0
    for idx, ch in enumerate(text):
        if ch in BRACKETS:
            if ch in "(（":
                stack.append(ch)
                continue
            if not stack or BRACKET_PAIRS[stack.pop()] != ch:
                break
        elif stack:
            # 括号内部出现普通字符 ⇒ 这对括号还没闭合，不能算配平
            continue
        balanced_len = idx + 1
    return balanced_len


def shorten_fund_name(name: Optional[str], limit: int = 12) -> str:
    """把基金名截到 limit 个字符，并保证结果里半角/全角括号各自配平。

    Args:
        name: 基金全名，允许为 None 或空串。
        limit: 最大字符数，默认 12（与原有展示宽度一致）。

    Returns:
        str: 截断且括号配平后的基金名；空名/全括号无法配平时返回空串，
            由调用方回退显示基金代码。
    """
    if not name or limit <= 0:
        return ""
    cut = str(name)[:limit]
    return cut[:balanced_bracket_prefix_len(cut)].rstrip()

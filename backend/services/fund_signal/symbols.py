"""
标的代码归一化 —— B6 唯一实现。

设计依据：docs/design/signal-scout-fund-account.md §3.5

⚠️ `tushare_data.get_fund_portfolio()` 的 B6 补丁必须 `from services.fund_signal.symbols
import normalize_symbol`，禁止在别处另写一份正则 —— 港股 00981.HK / 0981.HK 的重复
去重只能有这一个实现，否则两处口径会再次分叉。
"""
import re

# 港股：任意位数字 + .HK（大小写不敏感）。数字部分 >5 位属异常，原样返回并告警。
# 用 \d+ 而非 \d{1,5}：6 位及以上的异常代码也要能进入分支，才能触发告警
# （若正则只匹配 1~5 位，6 位 .HK 会静默落到「原样返回」，与设计 §3.5 的告警要求相悖）。
_HK_RE = re.compile(r"^(\d+)\.HK$", re.IGNORECASE)

# 已带交易所后缀的 A 股代码（大小写不敏感，输出统一大写后缀）。
_A_SUFFIXED_RE = re.compile(r"^(\d{6})\.(SH|SZ|BJ)$", re.IGNORECASE)

# 6 位纯数字 A 股代码。
_A_BARE_RE = re.compile(r"^\d{6}$")


def normalize_symbol(raw: str) -> str:
    """fund_portfolio.symbol → 统一标的键。顺序敏感，逐条按设计 §3.5：

    1. 港股（以 .HK 结尾）：数字部分去前导零后补到 5 位。
       '00981.HK' / '0981.HK' / '981.HK' → '00981.HK'。
       数字部分 >5 位（异常）→ 原样返回并告警。
    2. A 股（6 位纯数字）补后缀：6/9→.SH；0/2/3→.SZ；4/8→.BJ。
    3. 已带 .SH/.SZ/.BJ → 原样返回（后缀统一大写）。
       ⚠️ 绝不对 A 股去前导零 —— '000001.SZ' ≠ '1.SZ'，前导零是代码的一部分。
    4. 其余（美股 ticker 等）→ 原样返回，不做任何补零或截断。

    Args:
        raw: Tushare fund_portfolio.symbol 原始值，可能为 None / 空串 / 脏值。

    Returns:
        归一化后的标的键。空输入原样返回（""）。
    """
    s = str(raw or "").strip()
    if not s:
        return s

    # 1. 港股：去前导零 + 补 5 位 + 大写后缀
    m = _HK_RE.match(s)
    if m:
        digits = m.group(1)
        if len(digits) > 5:
            print(f"[FUND_SIGNAL] 港股代码数字部分超过 5 位（异常），原样返回: {s}")
            return s
        return f"{int(digits):05d}.HK"

    # 2. A 股 6 位纯数字 → 补后缀（不做前导零处理）
    if _A_BARE_RE.match(s):
        head = s[0]
        if head in ("6", "9"):
            return f"{s}.SH"
        if head in ("0", "2", "3"):
            return f"{s}.SZ"
        if head in ("4", "8"):
            return f"{s}.BJ"
        return s

    # 3. 已带 A 股后缀 → 原样返回（后缀统一大写）
    m = _A_SUFFIXED_RE.match(s)
    if m:
        return f"{m.group(1)}.{m.group(2).upper()}"

    # 4. 其余（美股 ticker 等）→ 原样返回
    return s

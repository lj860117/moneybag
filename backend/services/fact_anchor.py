"""
v9.9.24 事实锚点校验（Fact Anchor）— LLM 输出里的关键数字必须在数据包里有出处

为什么存在：
    prompt 里写了"禁止编造数字"，但模型照编。9/10 早安简报线上出现过
    "异常涨幅数字「229.7%」"——质检规则抓到了，却只在正文顶部加一行 ⚠️，
    可疑数字照样推给了用户。标注不是拦截。

    本模块做的是闭环的另一半：拿"传给 LLM 的数据包"当事实锚点，
    正文里的关键数字必须在锚点里找得到出处，找不到的按严重度处理。

设计取舍（防止误杀）：
    1. 只查"像事实"的数字：带小数点的百分比（2.35%）、大额金额（42亿/3.2万元）。
       整数百分比（"留 30% 仓位"）属建议性表述，不查出出处，误杀风险远大于收益。
    2. 锚点取数据包里的**全部**数字，允许 2% 或 0.05 的容差，
       覆盖四舍五入与一步推导（成本 1.5 / 现价 1.6 → 浮盈 6.7%）。
    3. 长周期限定语（近3年 / 成立以来 / 累计）豁免大额百分比。
    4. 只按句删除命中句，不整段降级 —— 除非命中 critical 规则。

用法：
    from services.fact_anchor import guard_fact_anchors

    clean, findings = guard_fact_anchors(llm_text, data_packet, fallback="（今日研判暂缺）")
    for f in findings:
        log(f"  ⚠️ {f.rule}: {f.number}{f.unit} —— {f.sentence[:30]}")
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional, Union

logger = logging.getLogger(__name__)

SEVERITY_CRITICAL = "critical"   # 整段降级为兜底文案
SEVERITY_MAJOR = "major"         # 删除命中句
SEVERITY_MINOR = "minor"         # 仅记录

# 长周期限定语：命中视为合规表述（近3年 / 成立以来 / 累计…）
LONG_PERIOD_RE = re.compile(
    r'(?:近\s*(?:\d+|[一二三四五六七八九十半两])\s*年'
    r'|成立以来(?:累计)?'
    r'|累计(?:收益|涨幅|回报|收益率)?)'
)

# 数字前不允许是数字/小数点/千分位，保证取到完整整数部分（与 _inject_hallucination_label 同源）
_DOT_VARIANTS = '.．·'
_PCT_RE = re.compile(r'(?<![\d.．·,，])(\d+(?:[.．·]\d+)?)\s*(%|％)')
# 金额只认 亿/万元/万，不认裸"元"——"每月定投 2000 元"是建议不是事实，查了必误杀
_MONEY_RE = re.compile(r'(?<![\d.．·,，])(\d[\d,]*(?:\.\d+)?)\s*(亿元|万元|亿|万)')

# 夸张阈值：单段涨幅超过此值且无长周期限定语 → critical
EXAGGERATED_PCT = 200.0
# 金额低于此量级不查出出处（万元计）
MIN_MONEY_ANCHORED = 1.0

_SENTENCE_SPLIT_RE = re.compile(r'(?<=[。！？；;\n])')


@dataclass(frozen=True)
class FactFinding:
    """一条事实锚点告警。

    Attributes:
        rule: 命中的规则名，用于日志复盘（R1_EXAGGERATED_PCT / R2_UNSOURCED_PCT / R3_UNSOURCED_MONEY）。
        severity: critical / major / minor。
        number: 命中的数字原文（已规整全角小数点与千分位）。
        unit: 数字单位（% / 亿 / 万元 …）。
        sentence: 命中句原文，供日志与按句删除使用。
        nearest: 数据包里最接近的锚点值，None 表示数据包里没有可比数字。
    """

    rule: str
    severity: str
    number: str
    unit: str
    sentence: str
    nearest: Optional[float] = None

    def __str__(self) -> str:
        near = f"，数据包最接近 {self.nearest}" if self.nearest is not None else "，数据包无同类数字"
        return f"[{self.rule}/{self.severity}] {self.number}{self.unit}{near}"


def _to_float(raw: str) -> Optional[float]:
    """把数字原文转成 float，失败返回 None。"""
    try:
        return float(raw.replace('．', '.').replace('·', '.').replace(',', '').replace('，', ''))
    except (TypeError, ValueError):
        return None


def _iter_packet_numbers(packet: Any) -> Iterable[float]:
    """从数据包里递归抽出全部数字，作为事实锚点。

    Args:
        packet: 传给 LLM 的数据包。可以是 str、dict、list 或任意嵌套结构。

    Yields:
        数据包中出现的每个数字（float）。
    """
    if packet is None:
        return
    if isinstance(packet, (int, float)) and not isinstance(packet, bool):
        yield float(packet)
        return
    if isinstance(packet, str):
        for m in re.finditer(r'\d[\d,]*(?:\.\d+)?', packet):
            v = _to_float(m.group(0))
            if v is not None:
                yield v
        return
    if isinstance(packet, dict):
        for k, v in packet.items():
            yield from _iter_packet_numbers(k)
            yield from _iter_packet_numbers(v)
        return
    if isinstance(packet, (list, tuple, set)):
        for v in packet:
            yield from _iter_packet_numbers(v)


def build_anchors(packet: Any) -> list[float]:
    """构建事实锚点集合（去重后的有序列表）。"""
    seen: dict[float, None] = {}
    for v in _iter_packet_numbers(packet):
        seen.setdefault(v, None)
    return list(seen)


def _nearest(anchors: list[float], value: float) -> Optional[float]:
    """找出离 value 最近的锚点；无锚点返回 None。"""
    if not anchors:
        return None
    return min(anchors, key=lambda a: abs(a - value))


def _is_sourced(anchors: list[float], value: float) -> tuple[bool, Optional[float]]:
    """判断 value 能否在锚点里找到出处。

    容差取 max(0.05, |v|*1%)：0.05 兜住"1.20 vs 1.2"这类写法差异，
    1% 兜住四舍五入（2.35 vs 2.346）。不给更大的容差 —— 放宽到 2% 时，
    数据包里的净值 3.856 会把编造的"跌了 3.87%"判成有出处，等于失明。
    """
    near = _nearest(anchors, value)
    if near is None:
        return False, None
    tol = max(abs(value) * 0.01, 0.05)
    return abs(near - value) <= tol, near


# 数据包里同一个金额可能是"42亿"也可能是"420000万"或"4200000000"，
# 逐个量级试一遍，避免单位写法不同就把真数字判成无出处
_MONEY_SCALES = (1.0, 1e4, 1e8, 1e-4, 1e-8)


def _is_sourced_money(anchors: list[float], value: float) -> tuple[bool, Optional[float]]:
    """金额出处判定：允许单位量级差异（亿 / 万 / 元）。"""
    for scale in _MONEY_SCALES:
        ok, near = _is_sourced(anchors, value * scale)
        if ok:
            return True, near
    return False, _nearest(anchors, value)


def check_fact_anchors(
    text: str,
    packet: Any,
    anchors: Optional[list[float]] = None,
) -> list[FactFinding]:
    """扫描 LLM 输出，找出无出处 / 夸大的关键数字。

    Args:
        text: LLM 输出正文。
        packet: 传给 LLM 的数据包（用于构建锚点）。
        anchors: 预先构建好的锚点，传了就不再解析 packet。

    Returns:
        FactFinding 列表；无问题返回空列表。
    """
    if not text:
        return []

    if anchors is None:
        anchors = build_anchors(packet)

    # 拿不到锚点就不该自作主张删内容 —— 没有证据时的"宁杀错"会误伤正常推送
    if not anchors:
        return []

    findings: list[FactFinding] = []

    for sentence in _SENTENCE_SPLIT_RE.split(text):
        if not sentence.strip():
            continue

        # ---- R1：夸张涨幅（不需要锚点，>200% 且无长周期限定语即 critical） ----
        for m in _PCT_RE.finditer(sentence):
            raw = m.group(1)
            v = _to_float(raw)
            if v is None:
                continue
            if v > EXAGGERATED_PCT and not LONG_PERIOD_RE.search(sentence[:m.start()]):
                findings.append(FactFinding(
                    rule="R1_EXAGGERATED_PCT",
                    severity=SEVERITY_CRITICAL,
                    number=raw.replace('．', '.').replace('·', '.'),
                    unit="%",
                    sentence=sentence,
                ))

        # ---- R2：带小数点的百分比必须在数据包里有出处 ----
        # 整数百分比（"留 30% 仓位"）是建议性表述，不查，避免误杀
        for m in _PCT_RE.finditer(sentence):
            raw = m.group(1)
            if '.' not in raw and '．' not in raw and '·' not in raw:
                continue
            v = _to_float(raw)
            if v is None:
                continue
            ok, near = _is_sourced(anchors, v)
            if not ok:
                findings.append(FactFinding(
                    rule="R2_UNSOURCED_PCT",
                    severity=SEVERITY_MAJOR,
                    number=raw.replace('．', '.').replace('·', '.'),
                    unit="%",
                    sentence=sentence,
                    nearest=near,
                ))

        # ---- R3：大额金额（亿/万元/万）必须在数据包里有出处 ----
        for m in _MONEY_RE.finditer(sentence):
            raw, unit = m.group(1), m.group(2)
            v = _to_float(raw)
            if v is None or v < MIN_MONEY_ANCHORED:
                continue
            ok, near = _is_sourced_money(anchors, v)
            if not ok:
                findings.append(FactFinding(
                    rule="R3_UNSOURCED_MONEY",
                    severity=SEVERITY_MAJOR,
                    number=raw.replace(',', '').replace('，', ''),
                    unit=unit,
                    sentence=sentence,
                    nearest=near,
                ))

    # 同一数字可能命中多条规则，去重时保留最严重的一条
    return _dedupe(findings)


def _dedupe(findings: list[FactFinding]) -> list[FactFinding]:
    """同一数字只保留一条（按严重度 critical > major > minor）。"""
    order = {SEVERITY_CRITICAL: 0, SEVERITY_MAJOR: 1, SEVERITY_MINOR: 2}
    best: dict[tuple[str, str], FactFinding] = {}
    for f in findings:
        key = (f.number, f.unit)
        prev = best.get(key)
        if prev is None or order.get(f.severity, 9) < order.get(prev.severity, 9):
            best[key] = f
    return list(best.values())


def guard_fact_anchors(
    text: str,
    packet: Any = None,
    fallback: str = "",
    anchors: Optional[list[float]] = None,
    log: Optional[Callable[[str], None]] = None,
    context: str = "",
) -> tuple[str, list[FactFinding]]:
    """事实锚点守卫：先检测，再按严重度拦截或降级。

    - 命中 critical → 整段降级为 fallback（脏内容一个字都不出）
    - 命中 major    → 删除命中句，其余正文保留
    - 无命中        → 原样返回

    Args:
        text: LLM 输出正文。
        packet: 传给 LLM 的数据包。
        fallback: critical 时的兜底文案；为空则用默认兜底。
        anchors: 预先构建的锚点，可选。
        log: 日志函数（cron 里传 print，night_worker 里传 log）。
        context: 日志上下文前缀（如 "LeiJiang/close_review"）。

    Returns:
        (处理后的文本, findings)
    """
    emit = log or (lambda msg: logger.warning(msg))
    prefix = f"[fact_anchor]{(' ' + context) if context else ''}"

    try:
        findings = check_fact_anchors(text, packet, anchors=anchors)
    except Exception as e:  # noqa: BLE001 – 校验器本身绝不能阻断推送链路
        emit(f"{prefix} 校验异常，放行原文: {e}")
        return text, []

    if not findings:
        return text, []

    for f in findings:
        emit(f"{prefix} ⚠️ {f} | 句: {f.sentence.strip()[:40]}")

    critical = [f for f in findings if f.severity == SEVERITY_CRITICAL]
    if critical:
        return (fallback or "（AI 输出含无法核实的数据，已拦截）"), findings

    drop = {f.sentence for f in findings}
    cleaned = ''.join(s for s in _SENTENCE_SPLIT_RE.split(text) if s not in drop)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
    if len(cleaned) < 10:
        return (fallback or "（AI 输出含无法核实的数据，已拦截）"), findings
    return cleaned, findings

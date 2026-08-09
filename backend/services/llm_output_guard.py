"""
v9.5.44 LLM 输出守卫 — 通用 prompt 泄漏过滤与质量兜底

设计原则（来自 v9.5.43 教训）：
  1. 任何 LLM 输出在送达用户前必须过一层守卫
  2. 黑名单关键词 + 三段结构验证 + 兜底降级
  3. 行为模式：先过滤 → 再验证 → 有问题 → 可选降级文案

使用方法：
  from services.llm_output_guard import LLMOutputGuard

  # 通用对话输出（宽松模式）
  clean = LLMOutputGuard.filter_chat(raw_reply)

  # 持仓诊断三段式（严格模式）
  clean = LLMOutputGuard.filter_diagnosis(raw_reply)

  # 新闻/信号解读（轻量模式）
  clean = LLMOutputGuard.filter_analysis(raw_reply, fallback="暂无解读")
"""
from __future__ import annotations
import re


# ============================================================
# 通用 prompt 泄漏关键词（所有场景共享）
# ============================================================
_COMMON_LEAK_KEYWORDS = [
    # 指令复读
    '我们被要求', '我们被告知', '被要求诊断',
    '你是投资组合诊断师', '你是持仓诊断师', '你是理财助手',
    '你是风险分析助手', '你是量化分析师', '你是 A 股分析助手',
    '请基于上面', '基于上面列出', '根据以上数据', '以上数据表明',
    '直接输出', '只输出结果', '不要复述', '不需要复述',
    '只根据名称', '只根据', '严格只输出',
    '用户让我', '用户提供了', '现在分析数据',
    '首先理解用户', '首先，理解用户', '深层需求', '铁律要求',

    # 数据格式泄漏
    '持仓列表是', '持仓明细是', '持仓数据是', '持仓数据：',
    '括号内为代码', '方括号为类型', '方括号内', '方括号类型',

    # prompt 要求文字
    '需给出总评', '需要给出总评',
    '150字以内', '150字内', '200字以内', '300字以内',
    '输出要求', '输出格式', '按以下格式', '按格式输出',

    # 思考链/自言自语（这些是模型的"想"，不该出现在输出里）
    '让我分析', '让我看看', '我来分析', '我们分析',
    '需要分析', '需要看名字', '需要判断', '需要确认',
    '可以指出', '看基金名称', '看股票名称',
    '不太确定', '暂时不评论', '需要更多信息',
    '思考：', '分析：我', '首先我', '接下来我',
]

# 晨报/分析场景里常见的 prompt 复述模式（整段文本级）
_PROMPT_REPLAY_PATTERNS = [
    re.compile(r'用户让我.*?(?:写|生成).{0,20}(?:小结|总结|微信消息|晨报)'),
    re.compile(r'用户提供了.*?(?:数据快照|宏观数据|市场数据)'),
    re.compile(r'(?:现在|接下来).{0,6}(?:分析|看看)数据'),
    re.compile(r'首先.{0,10}(?:理解|分析)用户'),
    re.compile(r'深层需求'),
    re.compile(r'铁律要求'),
]

# 持仓诊断专用（更严格）
_DIAGNOSIS_EXTRA_KEYWORDS = [
    '不需要管方括号', '不需要引用',
    '可能是', '可能存在', '可以谨慎说',
    '我们分析', '我来判断',
]

# 原始数据格式复读检测（RE pattern）
_RAW_HOLDINGS_PATTERN = re.compile(
    r'\([0-9]{6}\)\s*(?:盈亏[+\-\d.%]+\s*)?\[\s*\]'
)


# ============================================================
# 核心过滤函数
# ============================================================

def _filter_lines(text: str, extra_keywords: list[str] = None) -> str:
    """行级过滤：删除含泄漏关键词的行"""
    keywords = _COMMON_LEAK_KEYWORDS + (extra_keywords or [])
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned.append(line)
            continue
        if any(kw in stripped for kw in keywords):
            continue
        if _RAW_HOLDINGS_PATTERN.search(stripped):
            continue
        cleaned.append(line)
    result = '\n'.join(cleaned).strip()
    # 压缩连续空行
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result


def _has_diagnosis_structure(text: str) -> tuple[bool, bool, bool]:
    """检测三段结构（总评/风险/建议）"""
    has_summary = any(kw in text for kw in ['总评：', '总评:', '组合风格', '组合呈现', '组合明显', '整体'])
    has_risk = any(kw in text for kw in ['风险：', '风险:', '集中风险', '主要风险', '风险点'])
    has_advice = any(kw in text for kw in ['建议：', '建议:', '可考虑', '可适度', '建议添加', '操作建议'])
    return has_summary, has_risk, has_advice


def _is_thinking_chain(text: str) -> bool:
    """判断是否是思考链残留"""
    thinking_markers = ['可能', '需要', '让我', '看名字', '思考', '首先', '其次', '最后我认为']
    return sum(1 for m in thinking_markers if m in text) >= 2


def _looks_like_prompt_replay(text: str) -> bool:
    """判断整段文本是否在复述用户提示词/中间推理。"""
    normalized = ' '.join((text or '').split())
    if not normalized:
        return False
    if any(pattern.search(normalized) for pattern in _PROMPT_REPLAY_PATTERNS):
        return True
    suspicious_markers = [
        '用户让我', '用户提供了', '现在分析数据',
        '首先理解用户', '首先，理解用户', '深层需求', '铁律要求',
    ]
    return sum(1 for marker in suspicious_markers if marker in normalized) >= 2


# ============================================================
# 对外 API
# ============================================================

class LLMOutputGuard:
    """
    LLM 输出守卫 — 三个级别的过滤策略

    - filter_chat：对话回复（宽松）— 只过滤明显 prompt 泄漏
    - filter_analysis：分析/解读（中等）— 过滤 + 最短长度检查
    - filter_diagnosis：持仓诊断（严格）— 过滤 + 三段验证 + 思考链检测
    """

    @staticmethod
    def filter_chat(text: str, fallback: str = "") -> str:
        """
        对话回复过滤（宽松）
        适用：/api/chat、AI 对话、scenario 场景分析
        只过滤明显 prompt 泄漏行，不要求三段结构
        """
        if not text:
            return fallback or text

        cleaned = _filter_lines(text)

        # 过滤后太短 → 降级
        if len(cleaned.strip()) < 20:
            return fallback or "（AI 回复异常，请重试）"

        return cleaned

    @staticmethod
    def filter_analysis(text: str, fallback: str = "", min_len: int = 20) -> str:
        """
        分析/解读输出过滤（中等）
        适用：新闻解读、信号解读、策略建议、policy 场景
        过滤 + prompt 复读检测 + 最短长度检查
        """
        if not text:
            return fallback or text

        cleaned = _filter_lines(text)

        if _looks_like_prompt_replay(cleaned) or _is_thinking_chain(cleaned):
            return fallback or "（分析暂时不可用）"

        if len(cleaned.strip()) < min_len:
            return fallback or "（分析暂时不可用）"

        return cleaned

    @staticmethod
    def filter_diagnosis(text: str, retry_fallback: str = None) -> str:
        """
        持仓诊断输出过滤（严格）
        适用：持仓诊断、晨报 phase2、收盘复盘、个性化诊断
        三重防御：行过滤 → 三段验证 → 思考链检测
        返回：(cleaned_text, is_degraded)
        """
        if not text:
            return "（AI 诊断输出为空，建议手动查看持仓页详情）"

        # 第一层：行过滤
        cleaned = _filter_lines(text, extra_keywords=_DIAGNOSIS_EXTRA_KEYWORDS)

        # 过滤后太短 → 直接降级
        if len(cleaned.strip()) < 30:
            return retry_fallback or "（AI 诊断输出异常，已过滤。建议手动查看持仓页详情）"

        # 第二层：三段结构验证
        has_summary, has_risk, has_advice = _has_diagnosis_structure(cleaned)

        # 第三层：思考链检测
        if not (has_summary or has_risk or has_advice):
            if _is_thinking_chain(cleaned):
                return retry_fallback or "（AI 诊断思考链异常，已过滤。建议手动查看持仓页详情）"

        return cleaned

    @staticmethod
    def needs_retry(filtered_text: str) -> bool:
        """
        判断过滤后的文本是否需要重试 LLM
        用于 night_worker / 重要诊断场景
        """
        degraded_markers = ["（AI 诊断", "（AI 回复", "（分析暂时", "（AI 输出"]
        return any(m in filtered_text for m in degraded_markers)


# ============================================================
# 便捷函数（兼容 night_worker 旧接口）
# ============================================================

def filter_prompt_leak(text: str) -> str:
    """
    向后兼容：等同于 filter_diagnosis，供 night_worker 直接调用
    (v9.5.43 _filter_prompt_leak 的公共版本)
    """
    return LLMOutputGuard.filter_diagnosis(text)

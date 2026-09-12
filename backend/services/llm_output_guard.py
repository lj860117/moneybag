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

    # v9.9.10: prompt 指令复读（明确无歧义的元话术，正常面向用户文案不会出现）
    '我们需要输出', '我们要输出', '必须输出', '严格 JSON',
    '请输出', '不要输出任何', '只输出 JSON',

    # v9.9.10: 内部状态枚举 / 内部字段名。
    # 这些是系统内部标识，中文用户文案里不会出现，命中即可安全丢弃。
    # （"输入："、"用户问题："这类可能与正常中文共存的词不进这里，
    #   由推送侧 _PROMPT_ECHO_MARKERS 按场景处理，避免误伤对话场景）
    'high_vol_bear', 'high_vol_bull', 'trending_bull', 'trending_bear',
    'oscillating', 'rotation', 'modules_results', 'gate_decision', 'market_data',
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
# v9.9.10: JSON 键值形态泄漏检测
# ============================================================
# 背景：线上出现过 LLM 输出被 max_tokens 截断 → 解析失败 → 未闭合的
#   {"direction": "bearish", "confidence": 58, "conclusion": "...", "reasoning": "
# 被当成结论文本。旧防线一律以字面 "{" 为触发条件，而清理逻辑又会把 "{" 删掉，
# 导致泄漏片段变成「无花括号的裸键值串」，从此所有下游正则全部失明。
#
# 因此改为按「键值形态」判定，不再依赖花括号；字段名采用白名单，
# 避免误伤中文正常文本（如 "某政策"：全面落地 —— 字段名不匹配白名单）。
_JSON_LEAK_FIELDS = (
    'direction', 'confidence', 'conclusion', 'reasoning',
    'regime', 'action', 'summary', 'signal',
)
_JSON_KV_LEAK_PATTERN = re.compile(
    r'"(?:{})"\s*:\s*(?:"[^"]*"|\d+(?:\.\d+)?|[a-z_]+)'.format('|'.join(_JSON_LEAK_FIELDS))
)


# v9.9.24: 「硬泄漏」标记 —— 命中即说明这段输出根本不是面向用户的人话，
# 应整段拦截（降级为兜底文案），而不是按行删掉几行就放行。
# 与 _COMMON_LEAK_KEYWORDS 的区别：这里只放**中文用户文案里绝不可能出现**的
# 内部枚举名 / JSON 契约词 / 显式指令复读，误杀面为零才可以进这张表。
HARD_LEAK_MARKERS = [
    # 内部状态枚举（regime 值）
    'high_vol_bear', 'high_vol_bull', 'trending_bull', 'trending_bear',
    'oscillating', 'rotation',
    # 内部字段名
    'modules_results', 'gate_decision', 'market_data',
    # JSON 契约 / 指令复读
    '严格 JSON', '只输出 JSON', '我们需要输出', '我们要输出',
    '必须输出', '不要输出任何', '按以下格式', '输出格式',
    '用户提供了', '深层需求', '铁律要求',
    '输入：', '输入:', '用户问题：', '用户问题:',
]


def looks_like_json_leak(text: str) -> bool:
    """判断文本是否含泄漏的 JSON 键值片段（含无花括号的截断形态）。

    与花括号无关，只看 key: value 形态 + 字段名白名单，用于替代
    `if "{" in text` / `r'\\{\\s*"[a-z_]+":'` 这两类失明的判定。

    Args:
        text: 待检测文本。

    Returns:
        True 表示命中 JSON 泄漏特征，应整段丢弃或降级。
    """
    if not text:
        return False
    return bool(_JSON_KV_LEAK_PATTERN.search(text))


# ============================================================
# v9.9.10: prompt 复述 / 思维链残留特征词
# ============================================================
# 背景：模型会把喂给它的 system prompt 原文复述进 reasoning（如
# "我们需要输出严格 JSON"、"输入：用户问题收盘复盘，市场状态 high_vol_bear"），
# 而 bullet 兜底分支会按标点切片把这些原文直接当要点推给用户。
#
# 这里做成共享常量，是因为 services/steward.py 与
# scripts/stock_monitor_cron.py 各有一份几乎相同的 sanitizer ——
# 之前就是因为两份实现各自演进，才出现"修了一处漏了六处"。
PROMPT_ECHO_MARKERS = [
    # 指令复述
    '我们需要输出', '我们要输出', '必须输出', '严格 JSON', '请输出',
    '不要输出任何', '只输出 JSON', '按以下格式', '输出格式',
    '用户让我', '用户提供了', '现在分析数据', '首先理解', '首先，理解',
    '输入：', '输入:', '用户问题：', '用户问题:',
    # 内部状态枚举（regime 值，用户不该看到原始枚举名）
    'high_vol_bear', 'high_vol_bull', 'trending_bull', 'trending_bear',
    'oscillating', 'rotation',
    'market_data', 'modules_results', 'gate_decision',
]


def looks_like_prompt_echo(segment: str) -> bool:
    """判断一个片段是否是模型复述的 prompt / 内部状态，而非面向用户的人话。

    Args:
        segment: 单个 bullet 候选片段或句子。

    Returns:
        True 表示应丢弃该片段。
    """
    if not segment:
        return False
    return any(marker in segment for marker in PROMPT_ECHO_MARKERS)


def strip_prompt_echo(text: str) -> str:
    """按句剔除含 prompt 复述 / 内部状态枚举的句子。

    整句切掉比逐词替换更干净，不会留下半句话。保留原标点，
    因此不会破坏 1.406% / 0.856% 这类小数（防误杀要求）。

    Args:
        text: 待清理文本。

    Returns:
        剔除命中句子后的文本。
    """
    if not text:
        return text or ""
    import re as _re
    sentences = _re.split(r'(?<=[。；;])', text)
    return ''.join(s for s in sentences if not looks_like_prompt_echo(s))


def strip_json_leak(text: str) -> str:
    """移除文本中泄漏的 JSON 键值片段（含无花括号的截断形态）。

    先清掉带花括号的完整/残缺结构，再整段切除无花括号的裸键值串。
    无法安全裁剪时返回原文本，由调用方按 looks_like_json_leak 决定降级。

    Args:
        text: 待清理文本。

    Returns:
        清理后的文本。
    """
    if not text:
        return text or ""

    # 1) 带花括号的结构（保持旧行为）
    cleaned = re.sub(r'\{[^{}]*?["\'][^}]*?\}', '', text)
    # 2) 未闭合的开头花括号（截断场景）
    cleaned = re.sub(r'\{\s*"[a-z_]+"\s*:\s*["\']?[^"\']*$', '', cleaned)
    # 3) 无花括号的裸键值串：从首个命中切到末个命中
    matches = list(_JSON_KV_LEAK_PATTERN.finditer(cleaned))
    if matches:
        cleaned = cleaned[:matches[0].start()] + cleaned[matches[-1].end():]

    return ' '.join(cleaned.split()).strip()


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
        # v9.9.10: 整行是泄漏的 JSON 键值片段（无花括号截断形态也能命中）
        if looks_like_json_leak(stripped):
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
    def filter_analysis(text: str, fallback: str = "", min_len: int = 10) -> str:
        """
        分析/解读输出过滤（中等）
        适用：新闻解读、信号解读、策略建议、policy 场景
        过滤 + prompt 复读检测 + 最短长度检查

        min_len 默认 10（v9.9.10 由 20 下调）：20 会把 LLM 合法的短结论
        整段降级成兜底文案，例如"高波震荡偏弱，防御为上，控制仓位。"（17 字）
        就是仲裁 JSON 里 conclusion 字段的正常取值。长度阈值的作用是拦
        "被过滤后只剩残渣"，不是判断"内容是否有价值"，10 字足以挡住残渣。
        调高此值前请先确认不会再次吃掉正常的中文短结论。
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
    def filter_push(text: str, extra_keywords: list = None) -> str:
        """
        整条推送文本过滤（宽松，仅删行，不做整段降级）

        适用：已拼装完成的整条消息（如收盘复盘 msg_parts 合并后的正文）。
        与 filter_analysis 的区别：不做 prompt 复读/思考链的"整段替换"，
        否则一条误判会让整条推送变成占位文案，损失远大于泄漏本身。

        Args:
            text: 已拼装的推送正文。
            extra_keywords: 调用方附加的行级黑名单。

        Returns:
            删除命中行之后的文本；无命中时原样返回。
        """
        if not text:
            return text or ""
        return _filter_lines(text, extra_keywords=extra_keywords)

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
    def has_hard_leak(text: str) -> bool:
        """是否存在「硬泄漏」——命中即应整段拦截，不做行级修补。

        判定依据（满足任一）：
          1. 含泄漏的 JSON 键值片段（含无花括号的截断形态）
          2. 含内部枚举名 / 内部字段名 / 显式指令复读

        与 filter_* 系列的区别：filter_* 只按行删命中行，泄漏严重时会
        留下"删了但没删干净"的半截内容；而出现硬泄漏标记时，说明模型
        把 system prompt 当正文吐出来了，整段都不可信。

        Args:
            text: 待检测文本。

        Returns:
            True 表示应整段降级为兜底文案。
        """
        if not text:
            return False
        if looks_like_json_leak(text):
            return True
        return any(marker in text for marker in HARD_LEAK_MARKERS)

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

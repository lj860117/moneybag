"""
AI 回答校验器 — 在 LLM 输出发给前端前做质量检查
=================================================
检查规则：
1. 持仓查询：有数据时不能说"无法访问"
2. 空账号：不能凭空提及持仓
3. 安全校验：危险问题必须包含拒绝关键词
4. 内部推理：过滤 LLM 内部思考痕迹
"""
from __future__ import annotations
import re


def validate_response(reply: str, user_msg: str, portfolio_ctx: str) -> dict[str, object]:
    """校验 LLM 回答质量

    Returns:
        {"valid": True, "reply": reply}
        {"valid": False, "reply": cleaned_reply, "issues": [...]}
    """
    issues = []
    cleaned = reply

    # ── 1. 过滤内部推理痕迹 ──
    _INTERNAL_PATTERNS = [
        r"我们分析用户[问的]*问题",
        r"根据[我的]*角色设定",
        r"需要简明扼要",
        r"作为.*?AI.*?助[手理]",
        r"我来分析一下这个问题",
        r"让我[先来]?[看看查查]",
    ]
    for pat in _INTERNAL_PATTERNS:
        match = re.search(pat, cleaned)
        if match:
            # 删除包含内部推理的整句
            sentence_end = cleaned.find("。", match.start())
            if sentence_end > 0 and sentence_end - match.start() < 60:
                cleaned = cleaned[:match.start()] + cleaned[sentence_end + 1:]
            issues.append(f"internal_reasoning: {pat}")

    # ── 2. 禁止说"无法访问账户" ──
    _FORBIDDEN_PHRASES = [
        "我无法访问你的账户",
        "我无法查看你的",
        "我没有权限访问",
        "无法获取你的真实",
        "我不能直接查看",
    ]
    for phrase in _FORBIDDEN_PHRASES:
        if phrase in cleaned:
            cleaned = cleaned.replace(phrase, "当前钱袋子系统记录显示")
            issues.append(f"forbidden_phrase: {phrase}")

    # ── 3. 空账号校验：无持仓时不能凭空提及 ──
    if "没有任何持仓" in portfolio_ctx or "没有持仓" in portfolio_ctx:
        _HALLUCINATION_SIGNALS = ["持仓预警", "止损提醒", "建议减仓", "仓位过重"]
        for sig in _HALLUCINATION_SIGNALS:
            if sig in cleaned:
                issues.append(f"empty_account_hallucination: {sig}")

    # ── 4. 安全校验：危险问题必须包含拒绝 ──
    msg_lower = user_msg.lower()
    _DANGER_KEYWORDS = ["目标价", "满仓", "全仓", "梭哈", "稳赚", "保本", "借钱炒股"]
    is_danger = any(k in msg_lower for k in _DANGER_KEYWORDS)
    if is_danger:
        _REFUSAL_SIGNALS = ["不能预测", "不建议满仓", "不能承诺", "不能建议", "风险"]
        has_refusal = any(s in cleaned for s in _REFUSAL_SIGNALS)
        if not has_refusal:
            issues.append("missing_safety_refusal")
            # 强制追加拒绝声明
            cleaned += "\n\n⚠️ 我不能预测具体价格，也不建议满仓或借钱投资。可以帮你做风险检查。"

    return {
        "valid": len(issues) == 0,
        "reply": cleaned.strip(),
        "issues": issues,
    }

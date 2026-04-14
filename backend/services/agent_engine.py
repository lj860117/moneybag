"""
钱袋子 — Agent 决策引擎（Plan-and-Execute 模式）
职责：
  1. 收集数据（Python 计算，不调 LLM）
  2. 规则预筛（0 成本快速判断是否有异常）
  3. 有异常才调 DeepSeek（省 token）
  4. 自动选择场景 Skill
  5. 注入记忆 + 数据 → DeepSeek 分析
  6. 写决策日志 + 信号文件
  7. 返回结构化结果

参考：Redis 2026 Plan-and-Execute + A_Share_investment_Agent 辩论机制
"""
import os
import json
import time
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from config import DATA_DIR

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "agent_engine",
    "scope": "private",
    "input": ["user_id"],
    "output": "analysis_result",
    "cost": "llm_heavy",
    "tags": ["决策引擎", "Plan-and-Execute", "仲裁"],
    "description": "Agent决策引擎：数据收集→规则预筛→场景Skill→DS分析→决策日志",
    "layer": "analysis",
    "priority": 2,
}

# ---- Skill 文件路径 ----
SKILLS_DIR = Path(__file__).parent.parent / "prompts" / "skills"
SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "system_prompt.md"

# ---- 场景→Skill 映射 ----
SKILL_MAP = {
    "stock_alert": "stock_monitor.md",
    "fund_alert": "fund_monitor.md",
    "macro_change": "macro_analysis.md",
    "global_event": "global_market.md",
    "risk_breach": "risk_alert.md",
    "rebalance": "allocation.md",
    "policy_news": "policy_impact.md",
    "daily_review": "stock_monitor.md",  # 默认用股票盯盘
}


def _load_skill(skill_key: str) -> str:
    """加载场景 Skill prompt"""
    filename = SKILL_MAP.get(skill_key, "stock_monitor.md")
    fp = SKILLS_DIR / filename
    if fp.exists():
        return fp.read_text(encoding="utf-8")
    return ""


def _load_system_prompt() -> str:
    """加载 10 Skill system prompt"""
    if SYSTEM_PROMPT_PATH.exists():
        return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    return "你是钱袋子AI投顾。"


def _classify_alerts(alerts: list) -> str:
    """根据预警类型选择最合适的 Skill"""
    if not alerts:
        return "daily_review"

    types = set()
    for a in alerts:
        msg = a.get("msg", "").lower()
        level = a.get("level", "")
        atype = a.get("type", "")

        if atype in ("price_drop", "price_rise", "volume", "rsi", "macd", "breakthrough"):
            types.add("stock_alert")
        elif atype in ("fund_drop", "fund_drawdown", "fund_deviation"):
            types.add("fund_alert")
        elif atype in ("unlock", "insider"):
            types.add("stock_alert")
        elif "风控" in msg or level == "danger":
            types.add("risk_breach")
        elif "政策" in msg or "关税" in msg:
            types.add("policy_news")
        elif "美股" in msg or "美联储" in msg:
            types.add("global_event")

    # 优先级：风控 > 全球 > 政策 > 股票 > 基金
    for priority in ["risk_breach", "global_event", "policy_news", "stock_alert", "fund_alert"]:
        if priority in types:
            return priority

    return "daily_review"


def run_analysis_cycle(
    user_id: str,
    market_context: str = "",
    portfolio_context: str = "",
    alerts: list = None,
    memory_summary: str = "",
    force_deepseek: bool = False,
    model: str = "deepseek-chat",
) -> dict:
    """
    决策引擎核心循环

    Args:
        user_id: 用户 ID
        market_context: 市场数据摘要（_build_market_context 输出）
        portfolio_context: 持仓数据摘要（_build_portfolio_context 输出）
        alerts: 预警列表（规则预筛结果）
        memory_summary: 用户记忆摘要（agent_memory.build_memory_summary 输出）
        force_deepseek: 是否强制调用 DeepSeek（收盘复盘时 True）
        model: 使用的模型

    Returns:
        {analysis, source, skill_used, confidence, signals, ...}
    """
    alerts = alerts or []
    result = {
        "user_id": user_id,
        "timestamp": datetime.now().isoformat(),
        "alerts_count": len(alerts),
        "alerts": alerts,
        "skill_used": None,
        "analysis": "",
        "source": "none",
        "confidence": 0,
        "direction": "neutral",
    }

    # Step 1: 判断是否需要调 DeepSeek
    has_alerts = len(alerts) > 0
    if not has_alerts and not force_deepseek:
        result["analysis"] = "无异动，跳过深度分析"
        result["source"] = "skip"
        return result

    # Step 2: 选择场景 Skill
    skill_key = _classify_alerts(alerts) if alerts else "daily_review"
    skill_prompt = _load_skill(skill_key)
    result["skill_used"] = skill_key

    # Step 3: 组装完整 prompt
    system_prompt = _load_system_prompt()

    # 组装场景指令
    scene_instruction = f"\n\n## 当前场景分析指令\n{skill_prompt}" if skill_prompt else ""

    # 组装预警数据
    alerts_text = ""
    if alerts:
        alert_lines = [f"  [{a.get('level','info')}] {a.get('msg','')}" for a in alerts[:10]]
        alerts_text = f"\n\n## ⚠️ 触发的预警信号\n" + "\n".join(alert_lines)

    # 组装记忆
    memory_text = f"\n\n## 📝 用户记忆\n{memory_summary}" if memory_summary else ""

    full_system = f"""{system_prompt}{scene_instruction}

## 实时市场数据（45+ 维度）
{market_context}

## 用户持仓与风控
{portfolio_context}{alerts_text}{memory_text}"""

    # 用户消息
    if force_deepseek and not alerts:
        user_msg = "请对我的全部持仓做一次收盘复盘分析。按10 Skill框架输出，包含多空辩论和置信度。"
    elif alerts:
        user_msg = f"检测到 {len(alerts)} 条预警信号，请深度分析这些异动并给出操作建议。按10 Skill框架输出，包含多空辩论和置信度。"
    else:
        user_msg = "请对当前市场和我的持仓做一次综合分析。"

    # Step 4: 调用 DeepSeek
    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key:
        result["analysis"] = "未配置 LLM API Key"
        result["source"] = "no_key"
        return result

    try:
        import httpx

        model_base = "https://api.deepseek.com/v1"
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                f"{model_base}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": full_system},
                        {"role": "user", "content": user_msg},
                    ],
                    "max_tokens": 1200,
                    "temperature": 0.7,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                reply = data["choices"][0]["message"]["content"]
                result["analysis"] = reply
                result["source"] = "ai"

                # 尝试从回答中提取置信度
                import re
                conf_match = re.search(r"置信度[：:]\s*(\d+)%", reply)
                if conf_match:
                    result["confidence"] = int(conf_match.group(1))

                # 提取方向
                if "偏多" in reply[:100] or "买入" in reply[:100]:
                    result["direction"] = "bullish"
                elif "偏空" in reply[:100] or "减仓" in reply[:100]:
                    result["direction"] = "bearish"
                else:
                    result["direction"] = "neutral"
            else:
                result["analysis"] = f"DeepSeek 返回 {resp.status_code}"
                result["source"] = "error"
    except Exception as e:
        result["analysis"] = f"分析失败: {e}"
        result["source"] = "error"

    # B2修复：保存上下文接力（之前 save_context 从未被调用）
    if result.get("source") == "ai" and result.get("analysis"):
        try:
            from services.agent_memory import save_context
            save_context(user_id, {
                "last_analysis": result["analysis"][:300],
                "market_phase": result.get("direction", "neutral"),
                "confidence": result.get("confidence", 0),
                "skill_used": result.get("skill_used", ""),
                "alerts_count": result.get("alerts_count", 0),
            })
        except Exception as e:
            print(f"[AGENT] save_context failed: {e}")

    return result


def save_signal_file(user_id: str, result: dict):
    """保存信号文件（供 Claude 和前端读取）"""
    d = DATA_DIR / user_id / "monitor"
    d.mkdir(parents=True, exist_ok=True)

    # 最新信号
    fp = d / "latest_signal.json"
    fp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # 每日存档
    date_str = datetime.now().strftime("%Y-%m-%d")
    archive_dir = d / "archive"
    archive_dir.mkdir(exist_ok=True)
    archive_fp = archive_dir / f"{date_str}.json"

    # 追加到当天的存档
    daily = []
    if archive_fp.exists():
        try:
            daily = json.loads(archive_fp.read_text(encoding="utf-8"))
        except Exception:
            daily = []
    daily.append(result)
    archive_fp.write_text(json.dumps(daily, ensure_ascii=False, indent=2), encoding="utf-8")

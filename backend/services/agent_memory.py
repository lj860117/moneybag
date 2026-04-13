"""
钱袋子 — Agent 记忆系统
职责：
  1. 用户偏好管理（风险偏好、关注行业、排除标的）
  2. 决策日志（每次 AI 建议 + 结果追踪）
  3. 自定义规则（"茅台跌5%提醒我"）
  4. 上下文接力（上次分析结论，供下次注入）

存储：data/{userId}/memory/ 目录，JSON 文件
"""
import json
import time
from pathlib import Path
from datetime import datetime
from config import DATA_DIR

MEMORY_DIR = DATA_DIR  # data/ 根目录


def _user_memory_dir(user_id: str) -> Path:
    """获取用户记忆目录，不存在则创建"""
    d = MEMORY_DIR / user_id / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ============================================================
# 1. 用户偏好（长期不变，用户主动设置）
# ============================================================

def get_preferences(user_id: str) -> dict:
    """读取用户偏好"""
    f = _user_memory_dir(user_id) / "preferences.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {
        "risk_profile": "稳健型",
        "focus_industries": [],
        "exclude_stocks": [],
        "notes": "",
        "updated_at": None,
    }


def save_preferences(user_id: str, prefs: dict) -> dict:
    """保存用户偏好"""
    prefs["updated_at"] = datetime.now().isoformat()
    f = _user_memory_dir(user_id) / "preferences.json"
    f.write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")
    return prefs


# ============================================================
# 2. 决策日志（自动记录每次 AI 分析结果）
# ============================================================

_MAX_DECISIONS = 100  # 最多保留 100 条


def get_decisions(user_id: str, limit: int = 20) -> list:
    """读取决策日志（最近 N 条）"""
    f = _user_memory_dir(user_id) / "decisions.json"
    if f.exists():
        all_d = json.loads(f.read_text(encoding="utf-8"))
        return all_d[-limit:]
    return []


def add_decision(user_id: str, decision: dict) -> dict:
    """添加一条决策记录"""
    f = _user_memory_dir(user_id) / "decisions.json"
    all_d = []
    if f.exists():
        try:
            all_d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            all_d = []

    decision.setdefault("id", f"d_{int(time.time())}_{len(all_d)}")
    decision.setdefault("time", datetime.now().isoformat())
    decision.setdefault("result_tracked", False)
    all_d.append(decision)

    # 超过上限则裁剪
    if len(all_d) > _MAX_DECISIONS:
        all_d = all_d[-_MAX_DECISIONS:]

    f.write_text(json.dumps(all_d, ensure_ascii=False, indent=2), encoding="utf-8")
    return decision


def track_decision_result(user_id: str, decision_id: str, result: dict) -> bool:
    """追踪决策结果（事后验证建议是否正确）"""
    f = _user_memory_dir(user_id) / "decisions.json"
    if not f.exists():
        return False
    all_d = json.loads(f.read_text(encoding="utf-8"))
    for d in all_d:
        if d.get("id") == decision_id:
            d["result"] = result
            d["result_tracked"] = True
            d["result_time"] = datetime.now().isoformat()
            f.write_text(json.dumps(all_d, ensure_ascii=False, indent=2), encoding="utf-8")
            return True
    return False


# ============================================================
# 3. 自定义规则（用户设置的预警条件）
# ============================================================

def get_rules(user_id: str) -> list:
    """读取自定义规则"""
    f = _user_memory_dir(user_id) / "rules.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return []


def add_rule(user_id: str, rule: dict) -> dict:
    """添加自定义规则"""
    f = _user_memory_dir(user_id) / "rules.json"
    rules = get_rules(user_id)

    rule.setdefault("id", f"r_{int(time.time())}_{len(rules)}")
    rule.setdefault("created_at", datetime.now().isoformat())
    rule.setdefault("active", True)
    rule.setdefault("triggered_count", 0)
    rules.append(rule)

    f.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    return rule


def remove_rule(user_id: str, rule_id: str) -> bool:
    """删除自定义规则"""
    f = _user_memory_dir(user_id) / "rules.json"
    rules = get_rules(user_id)
    new_rules = [r for r in rules if r.get("id") != rule_id]
    if len(new_rules) == len(rules):
        return False
    f.write_text(json.dumps(new_rules, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def check_rules(user_id: str, holdings_data: dict) -> list:
    """检查自定义规则是否触发"""
    rules = get_rules(user_id)
    triggered = []

    for rule in rules:
        if not rule.get("active"):
            continue

        rule_type = rule.get("type", "")
        code = rule.get("code", "")
        threshold = rule.get("threshold", 0)

        # 价格预警
        if rule_type == "price_drop":
            for h in holdings_data.get("holdings", []):
                if h.get("code") == code:
                    change = h.get("changePct") or h.get("change_pct") or 0
                    if change <= -abs(threshold):
                        triggered.append({
                            "rule": rule,
                            "msg": f"⚠️ {h.get('name',code)} 跌 {change:.1f}%，触发你的 -{abs(threshold)}% 预警",
                            "level": "warning",
                        })
                        rule["triggered_count"] = rule.get("triggered_count", 0) + 1

        elif rule_type == "price_rise":
            for h in holdings_data.get("holdings", []):
                if h.get("code") == code:
                    change = h.get("changePct") or h.get("change_pct") or 0
                    if change >= abs(threshold):
                        triggered.append({
                            "rule": rule,
                            "msg": f"🎯 {h.get('name',code)} 涨 {change:.1f}%，触发你的 +{abs(threshold)}% 提醒",
                            "level": "info",
                        })
                        rule["triggered_count"] = rule.get("triggered_count", 0) + 1

        elif rule_type == "target_price":
            for h in holdings_data.get("holdings", []):
                if h.get("code") == code:
                    price = h.get("price", 0)
                    direction = rule.get("direction", "above")
                    if direction == "above" and price >= threshold:
                        triggered.append({
                            "rule": rule,
                            "msg": f"🎯 {h.get('name',code)} 到达目标价 ¥{threshold}（当前 ¥{price}）",
                            "level": "info",
                        })
                    elif direction == "below" and price <= threshold:
                        triggered.append({
                            "rule": rule,
                            "msg": f"⚠️ {h.get('name',code)} 跌破 ¥{threshold}（当前 ¥{price}）",
                            "level": "warning",
                        })

    # 保存触发计数
    if triggered:
        f = _user_memory_dir(user_id) / "rules.json"
        f.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")

    return triggered


# ============================================================
# 4. 上下文接力（上次分析结论）
# ============================================================

def get_context(user_id: str) -> dict:
    """读取上次分析上下文"""
    f = _user_memory_dir(user_id) / "context.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {"last_analysis": "", "market_phase": "", "updated_at": None}


def save_context(user_id: str, ctx: dict) -> dict:
    """保存当前分析上下文"""
    ctx["updated_at"] = datetime.now().isoformat()
    f = _user_memory_dir(user_id) / "context.json"
    f.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding="utf-8")
    return ctx


# ============================================================
# 5. 记忆摘要（供 DeepSeek prompt 注入）
# ============================================================

def build_memory_summary(user_id: str) -> str:
    """构建完整记忆摘要，注入 DeepSeek system prompt"""
    lines = []

    # 用户偏好
    prefs = get_preferences(user_id)
    if prefs.get("risk_profile"):
        lines.append(f"【用户偏好】风险类型：{prefs['risk_profile']}")
    if prefs.get("focus_industries"):
        lines.append(f"  关注行业：{', '.join(prefs['focus_industries'])}")
    if prefs.get("exclude_stocks"):
        lines.append(f"  排除标的：{', '.join(prefs['exclude_stocks'])}")
    if prefs.get("notes"):
        lines.append(f"  备注：{prefs['notes']}")

    # 最近 3 条决策
    decisions = get_decisions(user_id, limit=3)
    if decisions:
        lines.append("\n【近期决策记录】")
        for d in decisions:
            action = d.get("action", "分析")
            summary = d.get("summary", "")[:80]
            t = d.get("time", "")[:10]
            tracked = "→ 结果：" + d["result"].get("summary", "")[:40] if d.get("result_tracked") and d.get("result") else ""
            lines.append(f"  {t} {action}：{summary} {tracked}")

    # 自定义规则
    rules = get_rules(user_id)
    active_rules = [r for r in rules if r.get("active")]
    if active_rules:
        lines.append("\n【自定义预警规则】")
        for r in active_rules[:5]:
            lines.append(f"  - {r.get('description', r.get('type', ''))}")

    # 上次结论
    ctx = get_context(user_id)
    if ctx.get("last_analysis"):
        lines.append(f"\n【上次分析结论】{ctx['last_analysis'][:200]}")
    if ctx.get("market_phase"):
        lines.append(f"  市场阶段判断：{ctx['market_phase']}")

    return "\n".join(lines) if lines else ""

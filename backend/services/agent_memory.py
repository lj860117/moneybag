"""
钱袋子 — Agent 记忆系统
职责：
  1. 用户偏好管理（风险偏好、关注行业、排除标的）
  2. 决策日志（每次 AI 建议 + 结果追踪）
  3. 自定义规则（"茅台跌5%提醒我"）
  4. 上下文接力（上次分析结论，供下次注入）
  5. 用户画像（年龄/家庭/投资年限/禁忌/目标）     — 2026-04-19 新增
  6. 情绪追踪（最近 10 次提问情绪 tag）          — 2026-04-19 新增
  7. 长期铁律（用户告诉过 AI 的不可违反事实）    — 2026-04-19 新增

存储：data/{userId}/memory/ 目录，JSON 文件
"""
import json
import time
from pathlib import Path
from datetime import datetime
from config import DATA_DIR

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "agent_memory",
    "scope": "private",
    "input": ["user_id"],
    "output": "memory_summary",
    "cost": "cpu",
    "tags": ["记忆", "偏好", "决策日志", "规则"],
    "description": "Agent记忆系统：偏好/决策日志/自定义规则/上下文接力",
    "layer": "data",
    "priority": 1,
}

MEMORY_DIR = DATA_DIR  # data/ 根目录

# ============================================================
# 家庭主账号配置（2026-04-19 V7.4.3）
# ============================================================
# 这个账号会汇总所有成员的待审记忆 + 负责接收所有家庭级提醒
# 好处：配偶/孩子用 AI 时不会被"家庭管理"细节打扰
# 默认 LeiJiang 是家庭主账号。若将来家庭结构变化，改这里即可
import os as _os
FAMILY_ADMIN = _os.environ.get("MB_FAMILY_ADMIN", "LeiJiang")

# 家庭其他成员列表（这些账号的自动提炼会路由到 FAMILY_ADMIN 队列）
# 生效规则：只要 user_id 在这个列表里，auto_extract 就存到 FAMILY_ADMIN 的 pending_insights
_FAMILY_MEMBERS = {
    "LeiJiang": "self",         # 主账号本人
    "BuLuoGeLi": "spouse",      # 配偶
}


def _route_to_admin(user_id: str) -> str:
    """
    决定待审记忆应该路由到哪个账号。
    主账号 → 自己；家庭成员 → FAMILY_ADMIN；外人 → 自己
    """
    if user_id == FAMILY_ADMIN:
        return FAMILY_ADMIN
    if user_id in _FAMILY_MEMBERS:
        return FAMILY_ADMIN
    return user_id  # 陌生账号走自己队列


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
#
# 2026-04-19 V7.5：升级为 热/冷 分层
#   - decisions.json         （热）：近 30 天原文，进 summary
#   - decisions_archive.json （冷）：30+ 天前原文，保留可追溯，不进 summary
#   - archive_summary.json   （月摘要）：LLM 按月总结，少量字符注入 summary
#
# 老代码零改动：get_decisions/add_decision 语义不变，上限仍是 _MAX_DECISIONS
# ============================================================

_MAX_DECISIONS = 100              # 热区上限（保留防洪）
_ARCHIVE_DAYS = 30                # 超过多少天算冷数据
_MAX_ARCHIVE_SUMMARIES = 24       # 月摘要最多保留 24 个月


def get_decisions(user_id: str, limit: int = 20) -> list:
    """读取决策日志（最近 N 条热区）— 行为与旧版一致"""
    f = _user_memory_dir(user_id) / "decisions.json"
    if f.exists():
        all_d = json.loads(f.read_text(encoding="utf-8"))
        return all_d[-limit:]
    return []


def get_archived_decisions(user_id: str, limit: int = 500) -> list:
    """读取归档的冷决策（供追溯/回看，不进 LLM summary）"""
    f = _user_memory_dir(user_id) / "decisions_archive.json"
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            return data[-limit:]
        except Exception:
            return []
    return []


def get_archive_summaries(user_id: str) -> list:
    """读取按月归档的 LLM 摘要（注入 summary 的核心数据）"""
    f = _user_memory_dir(user_id) / "archive_summary.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return []
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


def archive_old_decisions(user_id: str, days: int = None) -> dict:
    """把超过 days 天的决策从热区移到冷区归档。

    返回 {moved: int, hot_remaining: int, cold_total: int}
    这是纯数据迁移，不调 LLM；LLM 摘要由 summarize_archive_month 单独做
    """
    if days is None:
        days = _ARCHIVE_DAYS
    hot_f = _user_memory_dir(user_id) / "decisions.json"
    cold_f = _user_memory_dir(user_id) / "decisions_archive.json"
    if not hot_f.exists():
        return {"moved": 0, "hot_remaining": 0, "cold_total": 0}

    try:
        hot = json.loads(hot_f.read_text(encoding="utf-8"))
    except Exception:
        hot = []

    cold = []
    if cold_f.exists():
        try:
            cold = json.loads(cold_f.read_text(encoding="utf-8"))
        except Exception:
            cold = []

    # 切分：time 字段早于 cutoff 的移走
    cutoff = datetime.now().timestamp() - days * 86400
    keep, move = [], []
    for d in hot:
        ts_str = d.get("time", "")
        try:
            ts = datetime.fromisoformat(ts_str).timestamp() if ts_str else 0
        except ValueError:
            ts = 0
        if ts and ts < cutoff:
            move.append(d)
        else:
            keep.append(d)

    if move:
        cold.extend(move)
        cold_f.write_text(json.dumps(cold, ensure_ascii=False, indent=2), encoding="utf-8")
        hot_f.write_text(json.dumps(keep, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "moved": len(move),
        "hot_remaining": len(keep),
        "cold_total": len(cold),
    }


def summarize_archive_month(user_id: str, year_month: str) -> dict:
    """对冷区指定月份（"2026-03"）的决策做 LLM 摘要，写入 archive_summary.json。

    幂等：同月已摘要过则跳过（除非 force=True，目前不提供，避免 token 浪费）
    返回 {ok, month, total, win_rate, summary} 或 {ok:False, reason}
    """
    cold = get_archived_decisions(user_id, limit=10000)
    if not cold:
        return {"ok": False, "reason": "no_archive"}

    # 筛出该月的决策
    month_decisions = []
    for d in cold:
        ts = d.get("time", "")
        if ts.startswith(year_month):
            month_decisions.append(d)

    if not month_decisions:
        return {"ok": False, "reason": "no_data_in_month"}

    # 幂等：已存在则跳过
    existing = get_archive_summaries(user_id)
    for s in existing:
        if s.get("month") == year_month:
            return {"ok": True, "month": year_month, "skipped": True}

    # 统计胜率（用 result 字段，可能没有）
    with_result = [d for d in month_decisions if d.get("result_tracked")]
    wins = sum(1 for d in with_result if (d.get("result") or {}).get("win"))
    win_rate = (wins / len(with_result)) if with_result else None

    # 构造 LLM 输入：每条决策摘一行
    briefs = []
    for d in month_decisions[:80]:  # 最多 80 条，足够月度摘要
        action = d.get("action", "")
        summ = (d.get("summary") or "")[:60]
        briefs.append(f"{d.get('time','')[:10]} {action}：{summ}")

    system = (
        "你是决策复盘助手。下面是用户某个月的交易/分析决策流水，"
        "请用 80 字以内总结：主要仓位动作、教训、值得坚持的做法。"
        "不要列表，用一句话叙述，口吻像投资顾问做季度回顾。"
    )
    prompt = f"月份：{year_month}，共 {len(month_decisions)} 条决策\n\n" + "\n".join(briefs)

    summary_text = ""
    try:
        from services.llm_gateway import LLMGateway
        result = LLMGateway.instance().call_sync(
            prompt=prompt, system=system,
            model_tier="llm_light",
            user_id=user_id, module="archive_summary",
            max_tokens=200,
        )
        summary_text = (result.get("content") or "").strip()
    except Exception as e:
        print(f"[ARCHIVE] LLM 摘要失败 {user_id}/{year_month}: {e}")
        # 降级：不调 LLM 也给一个纯统计摘要
        summary_text = f"{len(month_decisions)} 次决策"
        if win_rate is not None:
            summary_text += f"，胜率 {win_rate * 100:.0f}%"

    record = {
        "month": year_month,
        "total": len(month_decisions),
        "with_result": len(with_result),
        "win_rate": win_rate,
        "summary": summary_text[:300],
        "created_at": datetime.now().isoformat(),
    }

    existing.append(record)
    # 按月份排序保留最近 24 个
    existing.sort(key=lambda x: x.get("month", ""))
    if len(existing) > _MAX_ARCHIVE_SUMMARIES:
        existing = existing[-_MAX_ARCHIVE_SUMMARIES:]

    f = _user_memory_dir(user_id) / "archive_summary.json"
    f.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"ok": True, **record}


def track_decision_result(user_id: str, decision_id: str, result: dict) -> bool:
    """追踪决策结果（事后验证建议是否正确）

    V7.5：同时查热区和冷区，让 30 天后也能补上结果
    """
    for fname in ("decisions.json", "decisions_archive.json"):
        f = _user_memory_dir(user_id) / fname
        if not f.exists():
            continue
        try:
            all_d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
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
    """构建完整记忆摘要，注入 DeepSeek system prompt

    2026-04-19 扩充：加入用户画像 / 情绪状态 / 长期铁律
    2026-04-19 V7.4.2：改为"隐性上下文"语气，AI 内在知道但不主动复述
    2026-04-19 V7.4.4：去规则化重写 — 像朋友介绍，不像系统文档
    """
    lines = []

    # 总原则：一句话说完，放最前
    lines.append(
        "下面是关于当前用户的背景了解，仅用于帮你更自然地回应他。"
        "像朋友聊天一样用它，不要复述、不要引用、不要列表分点讲这些事实。"
    )

    # ===== 用户画像（叙事化）=====
    profile = get_profile(user_id)
    if profile and profile.get("available"):
        bits = []
        nick = profile.get("nickname") or ""
        if nick:
            bits.append(f"他叫{nick}")
        if profile.get("age"):
            bits.append(f"{profile['age']}岁")
        if profile.get("family"):
            bits.append(profile["family"])
        if profile.get("income_level"):
            bits.append(profile["income_level"])
        if bits:
            lines.append("\n" + "，".join(bits) + "。")

        tail = []
        if profile.get("invest_horizon"):
            tail.append(f"投资周期{profile['invest_horizon']}")
        if profile.get("drawdown_tolerance"):
            tail.append(f"回撤容忍度{profile['drawdown_tolerance']}")
        if profile.get("life_goals"):
            tail.append(f"最在意{', '.join(profile['life_goals'])}")
        if tail:
            lines.append("，".join(tail) + "。")

        # 家庭情境：自然叙述，不要标题
        if profile.get("notes"):
            lines.append(f"\n{profile['notes']}")

    # ===== 近期生活事件（纯背景，不要列表）=====
    try:
        upcoming = get_upcoming_events(user_id, days_ahead=30, for_user=user_id)
        if upcoming:
            evt_lines = []
            for evt in upcoming[:5]:
                days = evt["days_until"]
                label = "今天" if days == 0 else f"{days}天后"
                years = f"{evt['years_passed']}周年" if evt.get("years_passed") else ""
                phrase = f"{label}是{evt['title']}"
                if years:
                    phrase += f"（{years}）"
                evt_lines.append(phrase)
            lines.append(
                "\n日程上近期有这些事：" + "，".join(evt_lines) + "。"
                "除非他主动聊到相关话题或情绪需要安抚，"
                "否则不要提起这些日期；提及时语气要自然，像关心他的朋友。"
            )
    except Exception as e:
        print(f"[MEMORY] life_events 失败: {e}")

    # ===== 他的投资风格（融合 preferences + ironies）=====
    prefs = get_preferences(user_id)
    ironies = get_ironies(user_id)

    style_bits = []
    if prefs.get("risk_profile"):
        style_bits.append(f"风格偏{prefs['risk_profile']}")
    if prefs.get("focus_industries"):
        style_bits.append(f"关注{', '.join(prefs['focus_industries'])}")
    if prefs.get("exclude_stocks"):
        style_bits.append(f"不碰{', '.join(prefs['exclude_stocks'])}")
    if style_bits:
        lines.append("\n投资上，他" + "，".join(style_bits) + "。")
    if prefs.get("notes"):
        lines.append(prefs["notes"])

    # 把铁律融成叙述
    if ironies:
        iron_texts = [i.get("text", "").strip() for i in ironies[:8] if i.get("text")]
        iron_texts = [t for t in iron_texts if t]
        if iron_texts:
            lines.append(
                "\n他明确告诉过你的几件事（当成他本人的原则来尊重）：\n"
                + "；\n".join(iron_texts) + "。"
            )

    # ===== 当前心理状态（给 AI 一个语气调节的暗示）=====
    emotion = get_emotion_summary(user_id)
    if emotion and emotion.get("dominant") and emotion.get("dominant") != "neutral":
        tone_hint = emotion.get("hint", "")
        lines.append(
            f"\n他最近几次聊天偏{emotion['dominant']}的状态，"
            + (f"回应时可以{tone_hint}。" if tone_hint else "回应时注意语气匹配。")
        )

    # ===== 上次结论 + 决策（简短融入）=====
    ctx = get_context(user_id)
    if ctx.get("last_analysis"):
        lines.append(f"\n上次和他聊的结论大概是：{ctx['last_analysis'][:150]}")

    decisions = get_decisions(user_id, limit=2)
    if decisions:
        d_bits = []
        for d in decisions:
            action = d.get("action", "")
            summary = d.get("summary", "")[:60]
            t = d.get("time", "")[:10]
            if summary:
                d_bits.append(f"{t}{action}：{summary}")
        if d_bits:
            lines.append("最近的几次分析：" + "；".join(d_bits) + "。")

    # ===== 历史归档摘要（V7.5 分层压缩）=====
    # 把近 3 个月的月度摘要融进背景，帮 AI 了解长期轨迹
    try:
        archive_sums = get_archive_summaries(user_id)
        if archive_sums:
            recent_months = archive_sums[-3:]  # 只取最近 3 个月，避免 summary 膨胀
            arch_bits = []
            for s in recent_months:
                month = s.get("month", "")
                summ = (s.get("summary") or "").strip()
                if summ:
                    arch_bits.append(f"{month}：{summ}")
            if arch_bits:
                lines.append("再早一些的交易轨迹：" + " / ".join(arch_bits))
    except Exception as e:
        print(f"[MEMORY] archive summary 注入失败: {e}")

    # ===== 自定义预警规则（简短列出，这些是规则性的，保留）=====
    rules = get_rules(user_id)
    active_rules = [r for r in rules if r.get("active")]
    if active_rules:
        rule_descs = [r.get("description", r.get("type", "")) for r in active_rules[:5] if r.get("description") or r.get("type")]
        if rule_descs:
            lines.append("\n他设了这几条提醒：" + "；".join(rule_descs) + "。")

    # ===== V7.4.4: 家庭成员账号补充"另一半的理解"（隐性注入，无标题）=====
    if user_id != FAMILY_ADMIN and user_id in _FAMILY_MEMBERS:
        try:
            admin_ironies = get_ironies(FAMILY_ADMIN)
            shareable = [i for i in admin_ironies if i.get("source") != "self_only"]
            if shareable:
                admin_texts = [i.get("text", "").strip() for i in shareable[:8] if i.get("text")]
                admin_texts = [t for t in admin_texts if t]
                if admin_texts:
                    # 改成极其自然的背景陈述，不提来源，不列表，不标题
                    lines.append(
                        "\n补充几条你背景里应该了解的家庭财务共识（当作你对这家人的整体了解，"
                        "不要分点列出，不要提及来源，和她当下的表达冲突时以她为准）：\n"
                        + "；".join(admin_texts) + "。"
                    )
        except Exception as e:
            print(f"[MEMORY] 家庭共识注入失败: {e}")

    return "\n".join(lines) if lines else ""


# ============================================================
# 6. 用户画像（2026-04-19 新增）— 谁在问 AI
# ============================================================

DEFAULT_PROFILE = {
    "available": False,  # 没填过则 False，不注入
    "nickname": "",
    "age": None,
    "family": "",              # "单身" / "已婚无娃" / "已婚有娃-1个" / "已婚有娃-2个" 等
    "income_level": "",        # "月薪万以下" / "月薪 1-3 万" / "月薪 3-5 万" / "月薪 5 万以上"
    "invest_horizon": "",      # "短期<1年" / "中期 1-3 年" / "长期 3-10 年" / "养老 10 年+"
    "life_goals": [],          # ["买房", "养老", "育儿教育", "改善生活"]
    "drawdown_tolerance": "",  # "-5% 以内" / "-10% 以内" / "-20% 也能接受" / "深度回撤无所谓"
    "notes": "",               # 用户自填补充
    "updated_at": None,
}


def get_profile(user_id: str) -> dict:
    """读取用户画像"""
    f = _user_memory_dir(user_id) / "profile.json"
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            # 确保有 available 字段
            data.setdefault("available", True)
            return data
        except Exception:
            pass
    return dict(DEFAULT_PROFILE)


def save_profile(user_id: str, profile: dict) -> dict:
    """保存用户画像（保留已有字段 + 合并新字段）"""
    current = get_profile(user_id)
    # 合并（只更新传入的字段）
    for k, v in profile.items():
        if v is not None and v != "":
            current[k] = v
    # 只要有至少一个主字段填了，就算 available
    has_content = any([
        current.get("nickname"), current.get("age"),
        current.get("family"), current.get("invest_horizon"),
        current.get("life_goals"),
    ])
    current["available"] = bool(has_content)
    current["updated_at"] = datetime.now().isoformat()
    f = _user_memory_dir(user_id) / "profile.json"
    f.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


# ============================================================
# 7. 情绪追踪（2026-04-19 新增）— 用户最近心理状态
# ============================================================

_EMOTION_LEXICON = {
    "焦虑": ["焦虑", "担心", "怕", "害怕", "怎么办", "崩了", "吓人", "睡不着", "恐慌", "慌", "紧张", "着急"],
    "犹豫": ["犹豫", "不知道", "该不该", "能不能", "要不要", "好纠结", "拿不定主意", "摇摆"],
    "果断": ["一定", "坚决", "干脆", "明天就", "立即", "直接", "就这么定", "马上", "直接买", "直接卖"],
    "兴奋": ["棒", "牛", "太好了", "赚了", "爽", "发财", "狂喜", "赢麻", "起飞"],
    "失望": ["唉", "完了", "亏", "割肉", "套牢", "失望", "难受", "心累", "跌惨", "垃圾"],
    "理性": ["根据", "分析", "数据", "估值", "PE", "PB", "基本面", "技术面", "长期", "策略"],
}

_MAX_EMOTION = 20  # 最多存最近 20 条情绪记录


def tag_emotion(text: str) -> str:
    """根据文本内容打情绪标签，返回最匹配的一个或 'neutral'"""
    if not text:
        return "neutral"
    scores = {k: 0 for k in _EMOTION_LEXICON}
    for emotion, words in _EMOTION_LEXICON.items():
        for w in words:
            if w in text:
                scores[emotion] += 1
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "neutral"


def record_emotion(user_id: str, text: str) -> str:
    """记录一次提问的情绪（自动打 tag 并落盘）"""
    if not user_id or not text:
        return "neutral"
    tag = tag_emotion(text)
    f = _user_memory_dir(user_id) / "emotions.json"
    records = []
    if f.exists():
        try:
            records = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            records = []
    records.append({
        "tag": tag,
        "text": text[:100],  # 存前 100 字
        "time": datetime.now().isoformat(),
    })
    if len(records) > _MAX_EMOTION:
        records = records[-_MAX_EMOTION:]
    try:
        f.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return tag


def get_emotion_summary(user_id: str) -> dict:
    """汇总最近 10 次情绪分布"""
    f = _user_memory_dir(user_id) / "emotions.json"
    if not f.exists():
        return {}
    try:
        records = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}
    recent = records[-10:]
    if not recent:
        return {}
    from collections import Counter
    counter = Counter(r["tag"] for r in recent)
    dominant_tag, dominant_count = counter.most_common(1)[0]
    if dominant_tag == "neutral" and len(counter) > 1:
        # 如果 neutral 只是微弱多数，看第二名
        for tag, cnt in counter.most_common(2):
            if tag != "neutral":
                dominant_tag, dominant_count = tag, cnt
                break
    # 心理 hint
    hints = {
        "焦虑": "语气温和 + 强调风控 + 提止损位，少讲复杂估值",
        "犹豫": "给明确结论 + 列 3 条理由，少给选项",
        "果断": "提醒风险 + 建议分批而不是 all in",
        "兴奋": "泼点冷水 + 讲过往教训，避免顶部追高",
        "失望": "安抚 + 讲长期视角 + 避免割肉建议",
        "理性": "正常专业回答即可，可深入数据分析",
        "neutral": "",
    }
    return {
        "dominant": dominant_tag,
        "dominant_count": dominant_count,
        "sample_size": len(recent),
        "distribution": dict(counter),
        "hint": hints.get(dominant_tag, ""),
    }


# ============================================================
# 8. 长期铁律（2026-04-19 新增）— 用户明确告诉过 AI 的事
# ============================================================

_MAX_IRONIES = 20


def get_ironies(user_id: str) -> list:
    """读取用户的长期铁律（明确不可违反的指示）"""
    f = _user_memory_dir(user_id) / "ironies.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def add_irony(user_id: str, text: str, source: str = "manual") -> dict:
    """添加一条铁律。text 是用户原话或 AI 提炼，source 说明来源"""
    if not text:
        return {}
    ironies = get_ironies(user_id)
    # 去重（简单用前 30 字匹配）
    key = text[:30]
    for iron in ironies:
        if iron.get("text", "")[:30] == key:
            return iron  # 已存在
    new_irony = {
        "id": f"iron_{int(time.time())}_{len(ironies)}",
        "text": text[:200],
        "source": source,  # manual / auto_extract / chat
        "created_at": datetime.now().isoformat(),
        "active": True,
    }
    ironies.append(new_irony)
    if len(ironies) > _MAX_IRONIES:
        # 保留最新
        ironies = ironies[-_MAX_IRONIES:]
    f = _user_memory_dir(user_id) / "ironies.json"
    f.write_text(json.dumps(ironies, ensure_ascii=False, indent=2), encoding="utf-8")
    return new_irony


def remove_irony(user_id: str, iron_id: str) -> bool:
    """删除铁律"""
    ironies = get_ironies(user_id)
    new_list = [i for i in ironies if i.get("id") != iron_id]
    if len(new_list) == len(ironies):
        return False
    f = _user_memory_dir(user_id) / "ironies.json"
    f.write_text(json.dumps(new_list, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


# ============================================================
# 9. 生活事件（2026-04-19 新增）— 生日/纪念日/孩子事件
# ============================================================
#
# 数据结构：data/{userId}/memory/life_events.json
# [
#   {
#     "id": "evt_xxx",
#     "title": "老婆生日",
#     "date": "1985-02-12",     # 固定日期（公历 or 农历月日）
#     "is_lunar": true,          # 是否农历
#     "repeat_yearly": true,     # 每年重复
#     "remind_days_before": 7    # 提前几天提醒
#   }
# ]

def get_life_events(user_id: str) -> list:
    """读生活事件列表"""
    f = _user_memory_dir(user_id) / "life_events.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_life_events(user_id: str, events: list) -> list:
    """整体覆盖保存生活事件"""
    f = _user_memory_dir(user_id) / "life_events.json"
    f.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    return events


def add_life_event(user_id: str, title: str, date_str: str, is_lunar: bool = False,
                   repeat_yearly: bool = True, remind_days_before: int = 7,
                   visible_to: list = None, secret_from: list = None) -> dict:
    """添加一个生活事件（date_str 格式 YYYY-MM-DD）
    visible_to: 允许知道的用户列表（空 = 所有人）
    secret_from: 需要瞒着的用户列表（比如结婚纪念日惊喜，secret_from=[老婆]）
    """
    events = get_life_events(user_id)
    event = {
        "id": f"evt_{int(time.time())}_{len(events)}",
        "title": title,
        "date": date_str,
        "is_lunar": is_lunar,
        "repeat_yearly": repeat_yearly,
        "remind_days_before": remind_days_before,
        "visible_to": visible_to or [],
        "secret_from": secret_from or [],
        "created_at": datetime.now().isoformat(),
    }
    events.append(event)
    save_life_events(user_id, events)
    return event


def remove_life_event(user_id: str, event_id: str) -> bool:
    """删除生活事件"""
    events = get_life_events(user_id)
    new_list = [e for e in events if e.get("id") != event_id]
    if len(new_list) == len(events):
        return False
    save_life_events(user_id, new_list)
    return True


def _lunar_to_solar_this_year(lunar_month: int, lunar_day: int, year: int) -> str:
    """农历月日 → 今年的公历日期（YYYY-MM-DD）。失败返回 None"""
    try:
        from zhdate import ZhDate
        d = ZhDate(year, lunar_month, lunar_day).to_datetime()
        return d.strftime("%Y-%m-%d")
    except Exception as e:
        print(f"[LIFE_EVENTS] 农历转换失败 {year}-{lunar_month}-{lunar_day}: {e}")
        return None


def get_upcoming_events(user_id: str, days_ahead: int = 30, for_user: str = None) -> list:
    """返回未来 N 天内会到的事件（农历自动算公历）
    for_user: 以哪个用户视角查（用于过滤 secret_from）；默认 user_id 自己
    """
    events = get_life_events(user_id)
    target_user = for_user or user_id
    today = datetime.now().date()
    upcoming = []
    for e in events:
        # 2026-04-19 V7.4.2: 可见性过滤
        visible_to = e.get("visible_to") or []
        secret_from = e.get("secret_from") or []
        if visible_to and target_user not in visible_to:
            continue  # 白名单限制且不在其中 → 跳过
        if target_user in secret_from:
            continue  # 这个事件对当前用户保密（比如结婚纪念日惊喜）

        date_str = e.get("date", "")
        if not date_str or len(date_str) < 8:
            continue
        try:
            # date 格式 YYYY-MM-DD，其中 YYYY 是原始年（用来算月日）
            parts = date_str.split("-")
            if len(parts) != 3:
                continue
            orig_year, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        except (ValueError, IndexError):
            continue

        # 计算"今年"和"明年"该事件的公历日期
        candidates = []
        for year in (today.year, today.year + 1):
            if e.get("is_lunar"):
                solar = _lunar_to_solar_this_year(m, d, year)
                if solar:
                    candidates.append(solar)
            else:
                # 公历直接拼
                try:
                    _ = datetime(year, m, d).date()
                    candidates.append(f"{year:04d}-{m:02d}-{d:02d}")
                except ValueError:
                    # 比如 2 月 29 闰年日，非闰年跳过
                    continue

        for cand in candidates:
            try:
                cand_date = datetime.strptime(cand, "%Y-%m-%d").date()
            except ValueError:
                continue
            days_until = (cand_date - today).days
            if 0 <= days_until <= days_ahead:
                # 原始年份 → 计算当时多少周年
                years_passed = cand_date.year - orig_year if orig_year > 1900 else None
                upcoming.append({
                    "id": e.get("id"),
                    "title": e.get("title", ""),
                    "upcoming_date": cand,
                    "days_until": days_until,
                    "years_passed": years_passed,
                    "is_lunar": e.get("is_lunar", False),
                    "remind_days_before": e.get("remind_days_before", 7),
                })
                break  # 一个事件只返回最近一次

    upcoming.sort(key=lambda x: x["days_until"])
    return upcoming


# ============================================================
# 10. 自动记忆积累（2026-04-19 V7.4.2）— AI 从对话自己提炼
# ============================================================
#
# 工作流：
#   1. 用户每次问 AI → 对话结束后异步调一次 LLM 让它判断
#      "这段对话有没有暴露用户的新习惯/偏好/原则"
#   2. 如果有 → 存进 pending_insights.json（待审队列）
#   3. 前端有红点提示 → 用户点"接受"/"拒绝"
#      - 接受：写入对应的 profile / ironies / preferences
#      - 拒绝：从队列删除
#   4. 默认不自动入库（避免 AI 幻觉污染用户画像）

_MAX_PENDING = 30
_AUTO_EXTRACT_COOLDOWN = 60  # 同用户 60 秒内不重复提炼（省 token）


def get_pending_insights(user_id: str) -> list:
    """读待审记忆队列"""
    f = _user_memory_dir(user_id) / "pending_insights.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def add_pending_insight(user_id: str, insight: dict) -> dict:
    """添加一条待审记忆"""
    pending = get_pending_insights(user_id)

    # 简单去重（前 30 字匹配）
    key = insight.get("text", "")[:30]
    for p in pending:
        if p.get("text", "")[:30] == key:
            return p

    insight.setdefault("id", f"ins_{int(time.time())}_{len(pending)}")
    insight.setdefault("created_at", datetime.now().isoformat())
    insight.setdefault("status", "pending")
    pending.append(insight)

    if len(pending) > _MAX_PENDING:
        # 保留最近的
        pending = pending[-_MAX_PENDING:]

    f = _user_memory_dir(user_id) / "pending_insights.json"
    f.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
    return insight


def approve_insight(user_id: str, insight_id: str) -> dict:
    """批准一条待审记忆 → 写入对应模块 → 从队列移除

    2026-04-19 V7.4.3:
    - 按 insight.source_user（真实来源）写入画像 / ironies / preferences
    - user_id 只是"操作人"（通常是 FAMILY_ADMIN），不影响写入的目标账号
    """
    pending = get_pending_insights(user_id)
    target = None
    for i, p in enumerate(pending):
        if p.get("id") == insight_id:
            target = pending.pop(i)
            break
    if not target:
        return {"ok": False, "reason": "not_found"}

    # 根据 category 写入对应模块
    cat = target.get("category", "irony")
    text = target.get("text", "")
    # 🎯 关键：写入真实来源的画像（不是操作人的）
    write_to_user = target.get("source_user") or user_id
    result = {"ok": True, "category": cat, "text": text, "written_to": write_to_user}

    try:
        if cat == "irony":
            add_irony(write_to_user, text, source="auto_extract")
        elif cat == "preference":
            prefs = get_preferences(write_to_user)
            existing = prefs.get("notes", "")
            prefs["notes"] = (existing + "\n" if existing else "") + text
            save_preferences(write_to_user, prefs)
        elif cat == "profile_note":
            profile = get_profile(write_to_user)
            existing = profile.get("notes", "")
            profile["notes"] = (existing + "\n" if existing else "") + text
            save_profile(write_to_user, profile)
        else:
            result["ok"] = False
            result["reason"] = f"unknown_category: {cat}"
    except Exception as e:
        result["ok"] = False
        result["reason"] = str(e)

    # 从队列移除
    f = _user_memory_dir(user_id) / "pending_insights.json"
    f.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def reject_insight(user_id: str, insight_id: str) -> bool:
    """拒绝一条待审记忆"""
    pending = get_pending_insights(user_id)
    new_list = [p for p in pending if p.get("id") != insight_id]
    if len(new_list) == len(pending):
        return False
    f = _user_memory_dir(user_id) / "pending_insights.json"
    f.write_text(json.dumps(new_list, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


# 冷却缓存（避免每次对话都调 LLM 提炼）
_extract_cooldown = {}  # user_id → last_extract_time


def _should_skip_extract(user_id: str) -> bool:
    """判断是否在冷却期"""
    last = _extract_cooldown.get(user_id, 0)
    return time.time() - last < _AUTO_EXTRACT_COOLDOWN


def auto_extract_insight(user_id: str, user_msg: str, ai_reply: str) -> dict:
    """
    让 LLM 看一轮对话，自动提炼一条用户"新习惯/偏好/原则"
    返回 dict：{extracted: bool, insight: {...} or None}
    失败/无信息时 extracted=False
    """
    if not user_id or not user_msg:
        return {"extracted": False}

    # 冷却期跳过
    if _should_skip_extract(user_id):
        return {"extracted": False, "reason": "cooldown"}

    _extract_cooldown[user_id] = time.time()

    # 调 LLM（走现有 llm_gateway，省 token 用 llm_light）
    try:
        from services.llm_gateway import LLMGateway
    except Exception as e:
        print(f"[AUTO_EXTRACT] llm_gateway unavailable: {e}")
        return {"extracted": False, "reason": "no_llm"}

    system = (
        "你是'记忆提炼助手'。只分析用户的一轮对话，判断：\n"
        "用户是否暴露了 *新的、具体的、长期的* 习惯/偏好/原则/禁忌？\n\n"
        "严格规则：\n"
        "1. 如果没有 → 只输出 NONE（不要解释）\n"
        "2. 如果有 → 输出一行 JSON：{\"category\":\"irony|preference|profile_note\",\"text\":\"...20字以内...\"}\n"
        "   - irony: 明确禁忌/原则（不买医药 / 不碰杠杆）\n"
        "   - preference: 偏好（喜欢定投 / 关注消费板块）\n"
        "   - profile_note: 家庭/工作等情境（老人生病 / 刚升职）\n"
        "3. 不要提炼临时情绪（今天烦躁）、市场评论（大盘涨好）、问句（该买吗）\n"
        "4. 只有高置信度的新信息才提炼，宁可遗漏不可乱编\n"
    )
    prompt = (
        f"用户说：{user_msg[:300]}\n"
        f"AI 答：{ai_reply[:300]}\n\n"
        "提炼："
    )

    try:
        result = LLMGateway.instance().call_sync(
            prompt=prompt,
            system=system,
            model_tier="llm_light",
            user_id=user_id,
            module="auto_extract",
            max_tokens=100,
        )
    except Exception as e:
        print(f"[AUTO_EXTRACT] LLM 调用失败: {e}")
        return {"extracted": False, "reason": "llm_error"}

    content = (result.get("content") or "").strip()
    if not content or content.upper().startswith("NONE"):
        return {"extracted": False}

    # 解析 JSON
    try:
        import re as _re
        # 找 JSON 部分
        m = _re.search(r"\{[^{}]+\}", content)
        if not m:
            return {"extracted": False, "reason": "no_json"}
        data = json.loads(m.group(0))
        cat = data.get("category", "").strip()
        text = data.get("text", "").strip()
        if not cat or not text or cat not in ("irony", "preference", "profile_note"):
            return {"extracted": False, "reason": "bad_format"}

        # 加入待审队列（2026-04-19 V7.4.3: 路由到家庭主账号）
        target_user = _route_to_admin(user_id)
        insight = add_pending_insight(target_user, {
            "category": cat,
            "text": text,
            "source_user": user_id,          # 记录真实来源（比如 BuLuoGeLi）
            "routed_to": target_user,         # 落盘到哪个账号
            "source_user_msg": user_msg[:150],
            "source_ai_reply": ai_reply[:150],
        })
        route_info = f" → {target_user}" if target_user != user_id else ""
        print(f"[AUTO_EXTRACT] {user_id} 提炼: [{cat}] {text}{route_info}")
        return {"extracted": True, "insight": insight, "routed_to": target_user}
    except Exception as e:
        print(f"[AUTO_EXTRACT] 解析失败 content={content!r}: {e}")
        return {"extracted": False, "reason": "parse_error"}

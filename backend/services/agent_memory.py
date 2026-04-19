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
    """构建完整记忆摘要，注入 DeepSeek system prompt

    2026-04-19 扩充：加入用户画像 / 情绪状态 / 长期铁律
    """
    lines = []

    # 🆕 用户画像（最重要，放最前）
    profile = get_profile(user_id)
    if profile and profile.get("available"):
        p_lines = ["【用户画像】"]
        if profile.get("nickname"):
            p_lines.append(f"  昵称：{profile['nickname']}")
        if profile.get("age"):
            p_lines.append(f"  年龄：{profile['age']}岁")
        if profile.get("family"):
            p_lines.append(f"  家庭：{profile['family']}")
        if profile.get("income_level"):
            p_lines.append(f"  收入水平：{profile['income_level']}")
        if profile.get("invest_horizon"):
            p_lines.append(f"  投资周期：{profile['invest_horizon']}")
        if profile.get("life_goals"):
            p_lines.append(f"  核心目标：{', '.join(profile['life_goals'])}")
        if profile.get("drawdown_tolerance"):
            p_lines.append(f"  回撤容忍：{profile['drawdown_tolerance']}")
        # 家庭情境（notes）单独显眼展示
        if profile.get("notes"):
            p_lines.append(f"\n【家庭情境】{profile['notes']}")
        if len(p_lines) > 1:
            lines.extend(p_lines)

    # 🆕 未来 30 天内的生活事件（生日/纪念日）
    try:
        upcoming = get_upcoming_events(user_id, days_ahead=30)
        if upcoming:
            lines.append("\n【📅 近期生活事件】")
            for evt in upcoming[:5]:
                days = evt["days_until"]
                label = "今天" if days == 0 else f"{days} 天后"
                years = f"（{evt['years_passed']}周年）" if evt.get("years_passed") else ""
                lunar_tag = "[农历]" if evt.get("is_lunar") else ""
                lines.append(f"  {label}（{evt['upcoming_date']}）{lunar_tag}{evt['title']}{years}")
            lines.append("  💡 若接近提醒阈值，回答末尾可顺带提一句")
    except Exception as e:
        print(f"[MEMORY] life_events 失败: {e}")

    # 用户偏好
    prefs = get_preferences(user_id)
    if prefs.get("risk_profile"):
        lines.append(f"【投资偏好】风险类型：{prefs['risk_profile']}")
    if prefs.get("focus_industries"):
        lines.append(f"  关注行业：{', '.join(prefs['focus_industries'])}")
    if prefs.get("exclude_stocks"):
        lines.append(f"  排除标的：{', '.join(prefs['exclude_stocks'])}")
    if prefs.get("notes"):
        lines.append(f"  备注：{prefs['notes']}")

    # 🆕 长期铁律（用户告诉过 AI 的不可违反事实）
    ironies = get_ironies(user_id)
    if ironies:
        lines.append("\n【长期铁律（用户明确告诉过我的事）】")
        for i, iron in enumerate(ironies[:6], 1):
            txt = iron.get("text", "")[:80]
            lines.append(f"  {i}. {txt}")

    # 🆕 最近情绪状态
    emotion = get_emotion_summary(user_id)
    if emotion and emotion.get("dominant"):
        lines.append(f"\n【最近状态】{emotion['dominant']}（近 {emotion['sample_size']} 次提问）")
        if emotion.get("hint"):
            lines.append(f"  💡 建议语气：{emotion['hint']}")

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
                   repeat_yearly: bool = True, remind_days_before: int = 7) -> dict:
    """添加一个生活事件（date_str 格式 YYYY-MM-DD）"""
    events = get_life_events(user_id)
    event = {
        "id": f"evt_{int(time.time())}_{len(events)}",
        "title": title,
        "date": date_str,
        "is_lunar": is_lunar,
        "repeat_yearly": repeat_yearly,
        "remind_days_before": remind_days_before,
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


def get_upcoming_events(user_id: str, days_ahead: int = 30) -> list:
    """返回未来 N 天内会到的事件（农历自动算公历）"""
    events = get_life_events(user_id)
    today = datetime.now().date()
    upcoming = []
    for e in events:
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

"""
User Preference Service -- user identity, constraints, emotions, life events.

Migrated from services/agent_memory.py (M2 W1).

Responsibility:
  - User preferences (risk profile, focus industries, exclusions)
  - User profile (demographics, investment horizon, goals)
  - Ironies (long-term user-declared constraints)
  - Emotion tracking (recent emotional state tagging)
  - Life events (birthdays, anniversaries, milestones)
  - Pending insights (auto-extracted memories awaiting approval)

Storage: data/{userId}/memory/ directory, JSON files.

Invariant #9: No cross-imports between domain services.
Invariant #5: All file IO through infra/store (future; currently raw file IO for compat).
Design doc: docs/design/02-code-audit.md section 4.2
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import DATA_DIR

# ============================================================
# Family admin config (from V7.4.3)
# ============================================================
FAMILY_ADMIN = os.environ.get("MB_FAMILY_ADMIN", "LeiJiang")
_FAMILY_MEMBERS: Dict[str, str] = {
    "LeiJiang": "self",
    "BuLuoGeLi": "spouse",
}


def _route_to_admin(user_id: str) -> str:
    """Route pending insights to family admin queue."""
    if user_id == FAMILY_ADMIN:
        return FAMILY_ADMIN
    if user_id in _FAMILY_MEMBERS:
        return FAMILY_ADMIN
    return user_id


def _user_memory_dir(user_id: str) -> Path:
    """Get user memory directory, create if absent."""
    d = DATA_DIR / user_id / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ============================================================
# 1. User preferences (long-term, explicitly set)
# ============================================================

def get_preferences(user_id: str) -> dict:
    """Read user preferences."""
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
    """Save user preferences."""
    prefs["updated_at"] = datetime.now().isoformat()
    f = _user_memory_dir(user_id) / "preferences.json"
    f.write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")
    return prefs


# ============================================================
# 2. User profile (demographics, from V7.4.2)
# ============================================================

DEFAULT_PROFILE: Dict[str, Any] = {
    "available": False,
    "nickname": "",
    "age": None,
    "family": "",
    "income_level": "",
    "invest_horizon": "",
    "life_goals": [],
    "drawdown_tolerance": "",
    "notes": "",
    "updated_at": None,
}


def get_profile(user_id: str) -> dict:
    """Read user profile."""
    f = _user_memory_dir(user_id) / "profile.json"
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            data.setdefault("available", True)
            return data
        except Exception:
            pass
    return dict(DEFAULT_PROFILE)


def save_profile(user_id: str, profile: dict) -> dict:
    """Save user profile (incremental merge)."""
    current = get_profile(user_id)
    for k, v in profile.items():
        if v is not None and v != "":
            current[k] = v
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
# 3. Ironies (long-term user-declared constraints, from V7.4.2)
# ============================================================

_MAX_IRONIES = 20


def get_ironies(user_id: str) -> list:
    """Read long-term ironies (user-declared rules)."""
    f = _user_memory_dir(user_id) / "ironies.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def add_irony(user_id: str, text: str, source: str = "manual") -> dict:
    """Add one irony. Deduplicates on first 30 chars."""
    if not text:
        return {}
    ironies = get_ironies(user_id)
    key = text[:30]
    for iron in ironies:
        if iron.get("text", "")[:30] == key:
            return iron
    new_irony = {
        "id": f"iron_{int(time.time())}_{len(ironies)}",
        "text": text[:200],
        "source": source,
        "created_at": datetime.now().isoformat(),
        "active": True,
    }
    ironies.append(new_irony)
    if len(ironies) > _MAX_IRONIES:
        ironies = ironies[-_MAX_IRONIES:]
    f = _user_memory_dir(user_id) / "ironies.json"
    f.write_text(json.dumps(ironies, ensure_ascii=False, indent=2), encoding="utf-8")
    return new_irony


def remove_irony(user_id: str, iron_id: str) -> bool:
    """Remove irony by id."""
    ironies = get_ironies(user_id)
    new_list = [i for i in ironies if i.get("id") != iron_id]
    if len(new_list) == len(ironies):
        return False
    f = _user_memory_dir(user_id) / "ironies.json"
    f.write_text(json.dumps(new_list, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


# ============================================================
# 4. Emotion tracking (from V7.4.2)
# ============================================================

_EMOTION_LEXICON: Dict[str, List[str]] = {
    "焦虑": ["焦虑", "担心", "怕", "害怕", "怎么办", "崩了", "吓人", "睡不着", "恐慌", "慌", "紧张", "着急"],
    "犹豫": ["犹豫", "不知道", "该不该", "能不能", "要不要", "好纠结", "拿不定主意", "摇摆"],
    "果断": ["一定", "坚决", "干脆", "明天就", "立即", "直接", "就这么定", "马上", "直接买", "直接卖"],
    "兴奋": ["棒", "牛", "太好了", "赚了", "爽", "发财", "狂喜", "赢麻", "起飞"],
    "失望": ["唉", "完了", "亏", "割肉", "套牢", "失望", "难受", "心累", "跌惨", "垃圾"],
    "理性": ["根据", "分析", "数据", "估值", "PE", "PB", "基本面", "技术面", "长期", "策略"],
}

_MAX_EMOTION = 20


def tag_emotion(text: str) -> str:
    """Tag text with emotion label, return best match or 'neutral'."""
    if not text:
        return "neutral"
    scores: Dict[str, int] = {k: 0 for k in _EMOTION_LEXICON}
    for emotion, words in _EMOTION_LEXICON.items():
        for w in words:
            if w in text:
                scores[emotion] += 1
    best = max(scores, key=scores.get)  # type: ignore[arg-type]
    return best if scores[best] > 0 else "neutral"


def record_emotion(user_id: str, text: str) -> str:
    """Record one interaction's emotion (auto-tag and persist)."""
    if not user_id or not text:
        return "neutral"
    tag = tag_emotion(text)
    f = _user_memory_dir(user_id) / "emotions.json"
    records: list = []
    if f.exists():
        try:
            records = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            records = []
    records.append({
        "tag": tag,
        "text": text[:100],
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
    """Summarize recent 10 emotions distribution."""
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
    counter = Counter(r["tag"] for r in recent)
    dominant_tag, dominant_count = counter.most_common(1)[0]
    if dominant_tag == "neutral" and len(counter) > 1:
        for tag_name, cnt in counter.most_common(2):
            if tag_name != "neutral":
                dominant_tag, dominant_count = tag_name, cnt
                break
    hints: Dict[str, str] = {
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
# 5. Life events (birthdays, anniversaries, from V7.4.2)
# ============================================================

def get_life_events(user_id: str) -> list:
    """Read life events list."""
    f = _user_memory_dir(user_id) / "life_events.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_life_events(user_id: str, events: list) -> list:
    """Bulk save life events (replaces entire list)."""
    f = _user_memory_dir(user_id) / "life_events.json"
    f.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    return events


def add_life_event(
    user_id: str,
    title: str,
    date_str: str,
    is_lunar: bool = False,
    repeat_yearly: bool = True,
    remind_days_before: int = 7,
    visible_to: Optional[list] = None,
    secret_from: Optional[list] = None,
) -> dict:
    """Add a life event (date_str format YYYY-MM-DD)."""
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
    """Remove life event by id."""
    events = get_life_events(user_id)
    new_list = [e for e in events if e.get("id") != event_id]
    if len(new_list) == len(events):
        return False
    save_life_events(user_id, new_list)
    return True


def _lunar_to_solar_this_year(lunar_month: int, lunar_day: int, year: int) -> Optional[str]:
    """Convert lunar month/day to solar date for given year. Returns None on failure."""
    try:
        from zhdate import ZhDate
        d = ZhDate(year, lunar_month, lunar_day).to_datetime()
        return d.strftime("%Y-%m-%d")
    except Exception as e:
        print(f"[LIFE_EVENTS] lunar conversion failed {year}-{lunar_month}-{lunar_day}: {e}")
        return None


def get_upcoming_events(
    user_id: str,
    days_ahead: int = 30,
    for_user: Optional[str] = None,
) -> list:
    """Return events within next N days (auto-converts lunar dates).

    for_user: viewer perspective for visible_to/secret_from filtering.
    """
    events = get_life_events(user_id)
    target_user = for_user or user_id
    today = datetime.now().date()
    upcoming: list = []

    for e in events:
        visible_to = e.get("visible_to") or []
        secret_from = e.get("secret_from") or []
        if visible_to and target_user not in visible_to:
            continue
        if target_user in secret_from:
            continue

        date_str = e.get("date", "")
        if not date_str or len(date_str) < 8:
            continue
        try:
            parts = date_str.split("-")
            if len(parts) != 3:
                continue
            orig_year, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        except (ValueError, IndexError):
            continue

        candidates: list = []
        for year in (today.year, today.year + 1):
            if e.get("is_lunar"):
                solar = _lunar_to_solar_this_year(m, d, year)
                if solar:
                    candidates.append(solar)
            else:
                try:
                    _ = datetime(year, m, d).date()
                    candidates.append(f"{year:04d}-{m:02d}-{d:02d}")
                except ValueError:
                    continue

        for cand in candidates:
            try:
                cand_date = datetime.strptime(cand, "%Y-%m-%d").date()
            except ValueError:
                continue
            days_until = (cand_date - today).days
            if 0 <= days_until <= days_ahead:
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
                break

    upcoming.sort(key=lambda x: x["days_until"])
    return upcoming


# ============================================================
# 6. Pending insights (auto-extracted, awaiting user approval)
# ============================================================

_MAX_PENDING = 30


def get_pending_insights(user_id: str) -> list:
    """Read pending insights queue."""
    f = _user_memory_dir(user_id) / "pending_insights.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def add_pending_insight(user_id: str, insight: dict) -> dict:
    """Add a pending insight. Deduplicates on first 30 chars."""
    pending = get_pending_insights(user_id)
    key = insight.get("text", "")[:30]
    for p in pending:
        if p.get("text", "")[:30] == key:
            return p
    insight.setdefault("id", f"ins_{int(time.time())}_{len(pending)}")
    insight.setdefault("created_at", datetime.now().isoformat())
    insight.setdefault("status", "pending")
    pending.append(insight)
    if len(pending) > _MAX_PENDING:
        pending = pending[-_MAX_PENDING:]
    f = _user_memory_dir(user_id) / "pending_insights.json"
    f.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
    return insight


def approve_insight(user_id: str, insight_id: str) -> dict:
    """Approve a pending insight -> write to target module -> remove from queue.

    V7.4.3: writes to insight.source_user (real originator), not user_id (operator).
    """
    pending = get_pending_insights(user_id)
    target = None
    for i, p in enumerate(pending):
        if p.get("id") == insight_id:
            target = pending.pop(i)
            break
    if not target:
        return {"ok": False, "reason": "not_found"}

    cat = target.get("category", "irony")
    text = target.get("text", "")
    write_to_user = target.get("source_user") or user_id
    result: Dict[str, Any] = {"ok": True, "category": cat, "text": text, "written_to": write_to_user}

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

    f = _user_memory_dir(user_id) / "pending_insights.json"
    f.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def reject_insight(user_id: str, insight_id: str) -> bool:
    """Reject a pending insight."""
    pending = get_pending_insights(user_id)
    new_list = [p for p in pending if p.get("id") != insight_id]
    if len(new_list) == len(pending):
        return False
    f = _user_memory_dir(user_id) / "pending_insights.json"
    f.write_text(json.dumps(new_list, ensure_ascii=False, indent=2), encoding="utf-8")
    return True

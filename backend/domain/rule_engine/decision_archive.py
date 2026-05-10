"""
Decision Archive -- decisions, rules, context relay, auto-extraction.

Migrated from services/agent_memory.py (M2 W1).

Responsibility:
  - Decision log (hot/cold stratification, hot < 30d, cold > 30d)
  - Archive summaries (monthly LLM-generated summaries)
  - Decision result tracking
  - Custom alert rules (price_drop/price_rise/target_price)
  - Context relay (last analysis conclusion)
  - Auto-extract queue (deferred extraction for nightly batch)
  - Sync extraction (_extract_one_pair_sync for cron)

Storage: data/{userId}/memory/ directory, JSON files.

Invariant #3: LLM calls go through infra/llm/gateway (summarize_archive_month,
              _extract_one_pair_sync use services.llm_gateway via lazy import).
Design doc: docs/design/02-code-audit.md section 4.2
            docs/design/03-rule-engine.md
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from config import DATA_DIR

# Import family routing from user_preference_service (co-located in domain/services)
# NOTE: This import is from a sibling domain service. Per invariant #9, domain services
# should not cross-import. However, _route_to_admin and _user_memory_dir are shared
# infrastructure helpers, not business logic. In M3+ they should move to a shared
# domain utility module. For now, we re-export them here to avoid duplication.
from domain.services.user_preference_service import (
    _route_to_admin,
    _user_memory_dir,
    add_pending_insight,
)


# ============================================================
# 1. Decision log (hot/cold stratification, from V7.5)
# ============================================================

_MAX_DECISIONS = 100
_ARCHIVE_DAYS = 30
_MAX_ARCHIVE_SUMMARIES = 24


def get_decisions(user_id: str, limit: int = 20) -> list:
    """Read recent N decisions from hot zone."""
    f = _user_memory_dir(user_id) / "decisions.json"
    if f.exists():
        all_d = json.loads(f.read_text(encoding="utf-8"))
        return all_d[-limit:]
    return []


def get_archived_decisions(user_id: str, limit: int = 500) -> list:
    """Read cold zone decisions (for traceability, not injected into LLM)."""
    f = _user_memory_dir(user_id) / "decisions_archive.json"
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            return data[-limit:]
        except Exception:
            return []
    return []


def get_archive_summaries(user_id: str) -> list:
    """Read monthly LLM-generated archive summaries."""
    f = _user_memory_dir(user_id) / "archive_summary.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def add_decision(user_id: str, decision: dict) -> dict:
    """Add one decision record to hot zone."""
    f = _user_memory_dir(user_id) / "decisions.json"
    all_d: list = []
    if f.exists():
        try:
            all_d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            all_d = []

    decision.setdefault("id", f"d_{int(time.time())}_{len(all_d)}")
    decision.setdefault("time", datetime.now().isoformat())
    decision.setdefault("result_tracked", False)
    all_d.append(decision)

    if len(all_d) > _MAX_DECISIONS:
        all_d = all_d[-_MAX_DECISIONS:]

    f.write_text(json.dumps(all_d, ensure_ascii=False, indent=2), encoding="utf-8")
    return decision


def archive_old_decisions(user_id: str, days: Optional[int] = None) -> dict:
    """Move decisions older than N days from hot to cold zone.

    Pure data migration, no LLM call. Returns {moved, hot_remaining, cold_total}.
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

    cold: list = []
    if cold_f.exists():
        try:
            cold = json.loads(cold_f.read_text(encoding="utf-8"))
        except Exception:
            cold = []

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
    """Generate LLM summary for cold zone decisions of a given month.

    Idempotent: skips if month already summarized.
    Returns {ok, month, total, win_rate, summary} or {ok:False, reason}.
    """
    cold = get_archived_decisions(user_id, limit=10000)
    if not cold:
        return {"ok": False, "reason": "no_archive"}

    month_decisions = [d for d in cold if d.get("time", "").startswith(year_month)]
    if not month_decisions:
        return {"ok": False, "reason": "no_data_in_month"}

    existing = get_archive_summaries(user_id)
    for s in existing:
        if s.get("month") == year_month:
            return {"ok": True, "month": year_month, "skipped": True}

    with_result = [d for d in month_decisions if d.get("result_tracked")]
    wins = sum(1 for d in with_result if (d.get("result") or {}).get("win"))
    win_rate = (wins / len(with_result)) if with_result else None

    briefs: list = []
    for d in month_decisions[:80]:
        action = d.get("action", "")
        summ = (d.get("summary") or "")[:60]
        briefs.append(f"{d.get('time', '')[:10]} {action}：{summ}")

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
        print(f"[ARCHIVE] LLM summary failed {user_id}/{year_month}: {e}")

    # LLM 未返回内容（无 key / 超时 / content 为空）时降级到统计文本
    if not summary_text:
        summary_text = f"{len(month_decisions)} 次决策"
        if win_rate is not None:
            summary_text += f"，胜率 {win_rate * 100:.0f}%"

    record: Dict[str, Any] = {
        "month": year_month,
        "total": len(month_decisions),
        "with_result": len(with_result),
        "win_rate": win_rate,
        "summary": summary_text[:300],
        "created_at": datetime.now().isoformat(),
    }

    existing.append(record)
    existing.sort(key=lambda x: x.get("month", ""))
    if len(existing) > _MAX_ARCHIVE_SUMMARIES:
        existing = existing[-_MAX_ARCHIVE_SUMMARIES:]

    f = _user_memory_dir(user_id) / "archive_summary.json"
    f.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"ok": True, **record}


def track_decision_result(user_id: str, decision_id: str, result: dict) -> bool:
    """Track decision result (post-facto verification).

    V7.5: searches both hot and cold zones.
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
# 2. Custom alert rules (price alerts & triggers)
# ============================================================

def get_rules(user_id: str) -> list:
    """Read custom alert rules."""
    f = _user_memory_dir(user_id) / "rules.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return []


def add_rule(user_id: str, rule: dict) -> dict:
    """Add a custom alert rule."""
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
    """Remove a custom alert rule."""
    f = _user_memory_dir(user_id) / "rules.json"
    rules = get_rules(user_id)
    new_rules = [r for r in rules if r.get("id") != rule_id]
    if len(new_rules) == len(rules):
        return False
    f.write_text(json.dumps(new_rules, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def check_rules(user_id: str, holdings_data: dict) -> list:
    """Evaluate all active rules against current holdings. Returns triggered alerts."""
    rules = get_rules(user_id)
    triggered: list = []

    for rule in rules:
        if not rule.get("active"):
            continue

        rule_type = rule.get("type", "")
        code = rule.get("code", "")
        threshold = rule.get("threshold", 0)

        if rule_type == "price_drop":
            for h in holdings_data.get("holdings", []):
                if h.get("code") == code:
                    change = h.get("changePct") or h.get("change_pct") or 0
                    if change <= -abs(threshold):
                        triggered.append({
                            "rule": rule,
                            "msg": f"⚠️ {h.get('name', code)} 跌 {change:.1f}%，触发你的 -{abs(threshold)}% 预警",
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
                            "msg": f"\U0001f3af {h.get('name', code)} 涨 {change:.1f}%，触发你的 +{abs(threshold)}% 提醒",
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
                            "msg": f"\U0001f3af {h.get('name', code)} 到达目标价 ¥{threshold}（当前 ¥{price}）",
                            "level": "info",
                        })
                    elif direction == "below" and price <= threshold:
                        triggered.append({
                            "rule": rule,
                            "msg": f"⚠️ {h.get('name', code)} 跌破 ¥{threshold}（当前 ¥{price}）",
                            "level": "warning",
                        })

    if triggered:
        f = _user_memory_dir(user_id) / "rules.json"
        f.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")

    return triggered


# ============================================================
# 3. Context relay (last analysis conclusion)
# ============================================================

def get_context(user_id: str) -> dict:
    """Read last analysis context."""
    f = _user_memory_dir(user_id) / "context.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {"last_analysis": "", "market_phase": "", "updated_at": None}


def save_context(user_id: str, ctx: dict) -> dict:
    """Save current analysis context."""
    ctx["updated_at"] = datetime.now().isoformat()
    f = _user_memory_dir(user_id) / "context.json"
    f.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding="utf-8")
    return ctx


# ============================================================
# 4. Auto-extract queue (deferred extraction, V7.6)
# ============================================================

_MAX_EXTRACT_QUEUE = 200
_AUTO_EXTRACT_COOLDOWN = 60
_extract_cooldown: Dict[str, float] = {}


def _should_skip_extract(user_id: str) -> bool:
    """Check if extraction is in cooldown period."""
    last = _extract_cooldown.get(user_id, 0)
    return time.time() - last < _AUTO_EXTRACT_COOLDOWN


def add_to_extract_queue(user_id: str, user_msg: str, ai_reply: str) -> dict:
    """Queue a conversation pair for nightly batch extraction."""
    if not user_id or not user_msg:
        return {"queued": False}

    f = _user_memory_dir(user_id) / "extract_queue.json"
    queue: list = []
    if f.exists():
        try:
            queue = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            queue = []

    queue.append({
        "user_msg": user_msg[:500],
        "ai_reply": ai_reply[:500],
        "time": datetime.now().isoformat(),
    })

    if len(queue) > _MAX_EXTRACT_QUEUE:
        queue = queue[-_MAX_EXTRACT_QUEUE:]

    f.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"queued": True, "queue_size": len(queue)}


def get_extract_queue(user_id: str) -> list:
    """Read extraction queue."""
    f = _user_memory_dir(user_id) / "extract_queue.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def clear_extract_queue(user_id: str) -> int:
    """Clear extraction queue after batch processing. Returns count cleared."""
    f = _user_memory_dir(user_id) / "extract_queue.json"
    if f.exists():
        try:
            n = len(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            n = 0
        f.write_text("[]", encoding="utf-8")
        return n
    return 0


def auto_extract_insight(
    user_id: str,
    user_msg: str,
    ai_reply: str,
    sync: bool = False,
) -> dict:
    """Record a conversation worth extracting later.

    V7.6 default: sync=False (deferred, 0 tokens during daytime).
    sync=True: immediate LLM extraction (old behavior, test/manual only).
    """
    if not user_id or not user_msg:
        return {"extracted": False}

    if not sync:
        r = add_to_extract_queue(user_id, user_msg, ai_reply)
        return {
            "extracted": False,
            "queued": r.get("queued", False),
            "queue_size": r.get("queue_size", 0),
            "mode": "deferred",
        }

    return _extract_one_pair_sync(user_id, user_msg, ai_reply)


def _extract_one_pair_sync(user_id: str, user_msg: str, ai_reply: str) -> dict:
    """Actually call LLM for extraction (used by cron or sync mode)."""
    if _should_skip_extract(user_id):
        return {"extracted": False, "reason": "cooldown"}

    _extract_cooldown[user_id] = time.time()

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
            prompt=prompt, system=system,
            model_tier="llm_light",
            user_id=user_id, module="auto_extract",
            max_tokens=100,
        )
    except Exception as e:
        print(f"[AUTO_EXTRACT] LLM call failed: {e}")
        return {"extracted": False, "reason": "llm_error"}

    content = (result.get("content") or "").strip()
    if not content or content.upper().startswith("NONE"):
        return {"extracted": False}

    try:
        import re as _re
        m = _re.search(r"\{[^{}]+\}", content)
        if not m:
            return {"extracted": False, "reason": "no_json"}
        data = json.loads(m.group(0))
        cat = data.get("category", "").strip()
        text = data.get("text", "").strip()
        if not cat or not text or cat not in ("irony", "preference", "profile_note"):
            return {"extracted": False, "reason": "bad_format"}

        target_user = _route_to_admin(user_id)
        insight = add_pending_insight(target_user, {
            "category": cat, "text": text,
            "source_user": user_id,
            "routed_to": target_user,
            "source_user_msg": user_msg[:150],
            "source_ai_reply": ai_reply[:150],
        })
        route_info = f" → {target_user}" if target_user != user_id else ""
        print(f"[AUTO_EXTRACT] {user_id} extracted: [{cat}] {text}{route_info}")
        return {"extracted": True, "insight": insight, "routed_to": target_user}
    except Exception as e:
        print(f"[AUTO_EXTRACT] parse failed content={content!r}: {e}")
        return {"extracted": False, "reason": "parse_error"}


def batch_extract_for_user(user_id: str, max_items: int = 10) -> dict:
    """Nightly cron batch processing for a user's extract queue.

    max_items limits per-user per-run token consumption.
    """
    queue = get_extract_queue(user_id)
    if not queue:
        return {"user": user_id, "processed": 0, "extracted": 0}

    if len(queue) > max_items:
        processed_items = queue[-max_items:]
    else:
        processed_items = queue

    extracted = 0
    for item in processed_items:
        _extract_cooldown.pop(user_id, None)
        r = _extract_one_pair_sync(user_id, item.get("user_msg", ""), item.get("ai_reply", ""))
        if r.get("extracted"):
            extracted += 1

    clear_extract_queue(user_id)

    return {
        "user": user_id,
        "queue_len_before": len(queue),
        "processed": len(processed_items),
        "extracted": extracted,
        "discarded": max(0, len(queue) - len(processed_items)),
    }

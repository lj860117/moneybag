"""
每日深度复盘 cron — 每天 08:10 跑（night_worker 之后，早安简报之前）

V7.6 新增：AI 在用户醒来前回看"刚过去 24 小时"的对话和情绪，
生成明天对话时可用的"上下文更新"。

时间窗口：读取 昨天 06:00 到 今天 06:00 的数据
  理由：雷老板的工作日分界线是早上 06:00（MEMORY.md 里的偏好）
  所以凌晨 1-6 点的使用算"昨天"，和日报逻辑一致

职责：
  1. 扫所有活跃用户
  2. 对每个用户：
     - 读过去 24h 的 emotion 记录
     - 读过去 24h 的 decisions
     - 读待审 pending_insights（时刻都有意义）
     - LLM 融合：生成一段 150 字内的"用户近一天状态"
  3. 写入 context.last_analysis（下次 build_memory_summary 会自动注入）

建议 crontab（重要：必须在 night_worker 跑完之后！）：
  10 8 * * * cd /opt/moneybag/backend && python -m scripts.daily_reflection_cron >> /var/log/moneybag/daily_reflection.log 2>&1

注意：
  - 08:10 跑完，08:30 早安简报就能带上 handover 内容
  - 不要在 01:00-07:30 跑，会和 night_worker 的 R1 并发冲突
"""
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, date, timedelta

_BACKEND = Path(__file__).parent.parent
sys.path.insert(0, str(_BACKEND))

from config import DATA_DIR
from services.agent_memory import (
    _user_memory_dir,
    get_decisions,
    get_context,
    save_context,
    get_emotion_summary,
    get_pending_insights,
)

# 工作日分界线（MEMORY.md: 凌晨 6 点前算"昨天"）
_DAY_BOUNDARY_HOUR = 6


def _reflection_window():
    """返回本次复盘要覆盖的时间窗口 (start_iso, end_iso)

    规则：早上 06:00 是日界线
    - 08:10 跑时，窗口 = 昨天 06:00 → 今天 06:00（刚过去完整 24h）
    """
    now = datetime.now()
    # 今天的 06:00
    today_boundary = datetime.combine(now.date(), datetime.min.time()).replace(hour=_DAY_BOUNDARY_HOUR)
    # 昨天的 06:00
    yesterday_boundary = today_boundary - timedelta(days=1)
    return yesterday_boundary.isoformat(), today_boundary.isoformat()


def discover_active_users(days: int = 3) -> list:
    """扫出近 N 天有活动（有 emotion/decisions）的用户"""
    users = []
    if not DATA_DIR.exists():
        return users
    cutoff = datetime.now().timestamp() - days * 86400
    for d in DATA_DIR.iterdir():
        if not d.is_dir():
            continue
        mem_dir = d / "memory"
        if not mem_dir.exists():
            continue
        # 有任何文件在 cutoff 之后修改过
        for f in mem_dir.iterdir():
            if f.is_file() and f.stat().st_mtime > cutoff:
                users.append(d.name)
                break
    return users


def _emotions_in_window(user_id: str, start_iso: str, end_iso: str) -> list:
    """读窗口期的情绪记录"""
    f = _user_memory_dir(user_id) / "emotions.json"
    if not f.exists():
        return []
    try:
        records = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [r for r in records
            if start_iso <= r.get("time", "") < end_iso]


def _decisions_in_window(user_id: str, start_iso: str, end_iso: str) -> list:
    """读窗口期的决策"""
    all_d = get_decisions(user_id, limit=100)
    return [d for d in all_d
            if start_iso <= d.get("time", "") < end_iso]


def run_for_user(user_id: str, dry_run: bool = False) -> dict:
    """为单个用户跑一次复盘"""
    start_iso, end_iso = _reflection_window()
    emo_today = _emotions_in_window(user_id, start_iso, end_iso)
    dec_today = _decisions_in_window(user_id, start_iso, end_iso)
    pending = get_pending_insights(user_id)

    # 完全没活动就跳过
    if not emo_today and not dec_today and not pending:
        return {"user": user_id, "skipped": "no_activity",
                "window": [start_iso[:16], end_iso[:16]]}

    # 构造 LLM 输入
    emo_lines = []
    for e in emo_today[-8:]:
        tag = e.get("tag", "")
        text = (e.get("text", "") or "")[:60]
        emo_lines.append(f"[{tag}] {text}")

    dec_lines = []
    for d in dec_today[-5:]:
        action = d.get("action", "")
        summ = (d.get("summary", "") or "")[:50]
        dec_lines.append(f"{action}：{summ}")

    pending_lines = []
    for p in pending[-5:]:
        cat = p.get("category", "")
        text = (p.get("text", "") or "")[:40]
        source = p.get("source_user", "self")
        pending_lines.append(f"[{cat}·{source}] {text}")

    system = (
        "你是一个夜班助理，帮主人复盘过去 24 小时的使用状况。"
        "基于下面的情绪记录、决策流水和待审洞察，用 150 字以内的叙述"
        "告诉接班的 AI：这个用户最近情绪如何、关注什么、有什么苗头要关心。"
        "不要列表，不要 emoji，像值班同事留个 handover 便条那样写。"
    )
    window_label = f"{start_iso[:10]} 06:00 到 {end_iso[:10]} 06:00"
    prompt_parts = [f"用户：{user_id}", f"时间窗口：{window_label}"]
    if emo_lines:
        prompt_parts.append("情绪记录：\n" + "\n".join(emo_lines))
    if dec_lines:
        prompt_parts.append("决策流水：\n" + "\n".join(dec_lines))
    if pending_lines:
        prompt_parts.append("待审的新洞察：\n" + "\n".join(pending_lines))
    prompt_parts.append("请写 handover：")
    prompt = "\n\n".join(prompt_parts)

    if dry_run:
        return {"user": user_id, "dry_run": True,
                "window": [start_iso[:16], end_iso[:16]],
                "emotion_count": len(emo_today),
                "decision_count": len(dec_today),
                "pending_count": len(pending),
                "prompt_preview": prompt[:300]}

    # 调 LLM（允许降级）
    reflection = ""
    try:
        from services.llm_gateway import LLMGateway
        r = LLMGateway.instance().call_sync(
            prompt=prompt, system=system,
            model_tier="llm_light",
            user_id=user_id, module="daily_reflection",
            max_tokens=250,
        )
        reflection = (r.get("content") or "").strip()
    except Exception as e:
        print(f"[REFLECTION] {user_id} LLM 失败: {e}")

    # 降级：纯统计
    if not reflection:
        bits = []
        summary = get_emotion_summary(user_id)
        if summary.get("dominant"):
            bits.append(f"情绪主基调 {summary['dominant']}")
        if dec_today:
            bits.append(f"{len(dec_today)} 次决策")
        if pending_lines:
            bits.append(f"{len(pending_lines)} 条新洞察待审")
        reflection = ("近一天：" + "，".join(bits) + "。") if bits else ""

    # 写入 context
    if reflection:
        ctx = get_context(user_id)
        ctx["last_analysis"] = reflection[:400]
        ctx["last_reflection_date"] = date.today().isoformat()
        ctx["last_reflection_window"] = [start_iso[:16], end_iso[:16]]
        save_context(user_id, ctx)

    return {
        "user": user_id,
        "window": [start_iso[:16], end_iso[:16]],
        "emotion_count": len(emo_today),
        "decision_count": len(dec_today),
        "pending_count": len(pending),
        "reflection": reflection,
    }


def main():
    parser = argparse.ArgumentParser(description="钱袋子每日深度复盘 cron")
    parser.add_argument("--user", type=str, help="只跑指定用户")
    parser.add_argument("--dry-run", action="store_true", help="不调 LLM，只打印上下文")
    parser.add_argument("--days", type=int, default=3, help="活跃用户定义：近 N 天有文件修改")
    args = parser.parse_args()

    start_iso, end_iso = _reflection_window()
    print(f"===== 每日复盘 cron 启动 @ {datetime.now().isoformat()} =====")
    print(f"复盘窗口：{start_iso[:16]} → {end_iso[:16]}")

    if args.user:
        users = [args.user]
    else:
        users = discover_active_users(days=args.days)
        print(f"近 {args.days} 天活跃用户：{len(users)} 个 → {users}")

    total_reflected = 0
    for uid in users:
        try:
            r = run_for_user(uid, dry_run=args.dry_run)
            if r.get("skipped"):
                print(f"[{uid}] 跳过：{r['skipped']}")
                continue
            if args.dry_run:
                print(f"[{uid}] DRY: 情绪 {r['emotion_count']}，决策 {r['decision_count']}，"
                      f"待审 {r['pending_count']}")
                print(f"  prompt 预览: {r['prompt_preview'][:150]}")
            else:
                if r.get("reflection"):
                    total_reflected += 1
                    print(f"[{uid}] ✅ {r['reflection'][:100]}...")
                else:
                    print(f"[{uid}] 无复盘内容")
        except Exception as e:
            print(f"[ERROR] {uid}: {e}")

    print(f"===== 完成：生成 {total_reflected} 份复盘 =====")


if __name__ == "__main__":
    main()

"""
每日深度复盘 cron — 每天 02:30 跑

V7.6 新增：AI 在用户睡觉时回看今天的对话和情绪，生成明天对话时可用的"上下文更新"。

职责：
  1. 扫所有活跃用户
  2. 对每个用户：
     - 读今天的 emotion 记录
     - 读今天的 decisions
     - 读今天的 extract_queue（如果 02:00 auto_extract 已跑过则为空）
     - LLM 融合：生成一段 200 字内的"用户今天状态"
  3. 写入 context.last_analysis（下次 build_memory_summary 会自动注入）

建议 crontab：
  30 2 * * * cd /opt/moneybag/backend && python -m scripts.daily_reflection_cron >> /var/log/moneybag/daily_reflection.log 2>&1

注意：运行顺序建议 02:00 auto_extract → 02:30 daily_reflection，
这样 daily_reflection 能看到 auto_extract 刚生成的 pending_insights（未审批的也能看到趋势）
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


def _today_emotions(user_id: str) -> list:
    """读今天记录的情绪"""
    f = _user_memory_dir(user_id) / "emotions.json"
    if not f.exists():
        return []
    try:
        records = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []
    today_str = date.today().isoformat()
    return [r for r in records if r.get("time", "").startswith(today_str)]


def _today_decisions(user_id: str) -> list:
    """读今天的决策"""
    all_d = get_decisions(user_id, limit=50)
    today_str = date.today().isoformat()
    return [d for d in all_d if d.get("time", "").startswith(today_str)]


def run_for_user(user_id: str, dry_run: bool = False) -> dict:
    """为单个用户跑一次复盘"""
    emo_today = _today_emotions(user_id)
    dec_today = _today_decisions(user_id)
    pending = get_pending_insights(user_id)

    # 完全没活动就跳过
    if not emo_today and not dec_today and not pending:
        return {"user": user_id, "skipped": "no_activity"}

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
        "你是一个夜班助理，帮主人复盘今天的使用状况。"
        "基于下面的情绪记录、决策流水和待审洞察，用 150 字以内的叙述"
        "告诉明天的 AI：这个用户今天情绪如何、关注什么、有什么苗头要关心。"
        "不要列表，不要 emoji，像值班同事留个 handover 便条那样写。"
    )
    prompt_parts = [f"用户：{user_id}"]
    if emo_lines:
        prompt_parts.append("今天的情绪记录：\n" + "\n".join(emo_lines))
    if dec_lines:
        prompt_parts.append("今天的决策流水：\n" + "\n".join(dec_lines))
    if pending_lines:
        prompt_parts.append("今天待审的新洞察：\n" + "\n".join(pending_lines))
    prompt_parts.append("请写 handover：")
    prompt = "\n\n".join(prompt_parts)

    if dry_run:
        return {"user": user_id, "dry_run": True,
                "emotion_count": len(emo_today),
                "decision_count": len(dec_today),
                "pending_count": len(pending),
                "prompt_preview": prompt[:200]}

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
        reflection = ("今天：" + "，".join(bits) + "。") if bits else ""

    # 写入 context
    if reflection:
        ctx = get_context(user_id)
        ctx["last_analysis"] = reflection[:400]
        ctx["last_reflection_date"] = date.today().isoformat()
        save_context(user_id, ctx)

    return {
        "user": user_id,
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

    print(f"===== 每日复盘 cron 启动 @ {datetime.now().isoformat()} =====")

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
                print(f"  prompt 预览: {r['prompt_preview']}")
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

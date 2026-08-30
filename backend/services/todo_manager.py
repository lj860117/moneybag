"""
钱袋子 — 待办管理器（Phase 3 Batch 1）
========================================
管理用户待办任务。

功能：
- create_todo: 创建待办项（幂等 + 总量上限）
- update_todo: 更新待办项
- delete_todo: 删除待办项
- get_todos: 获取待办列表
- mark_done: 标记待办完成

--------------------------------------------------------------------
FIX 2026-08-30（P0 数据 bug：todos 无限膨胀）
--------------------------------------------------------------------
现象：生产环境单个用户 JSON 达 49MB，其中 todos 有 153,093 条（33.9MB），
      从 2026-05-16 累积 106 天 ≈ 1444 条/天，0% 完全重复但内容全是
      同一条规则（weekly_review 等）反复生成。
根因：本模块原 create_todo() 是"裸 append"——既无幂等检查也无数量上限，
      而调用方 services/cfo_dashboard.py 在**构建仪表板（读操作）时**就调
      create_todo()，配合 55 秒一次的后台预热线程，每天堆上千条。
      连带后果：JSON 涨到 49MB 后写入变慢，进程被 SIGKILL，
      atomic_write_json() 的 except 分支来不及执行 → data/users/*.tmp 孤儿文件。
修复：本模块加两道护栏（防御性，与调用方解耦无关，任何调用方都受保护）：
      1) 幂等去重 —— 按规则语义分两种模式：
         · calendar_week（日历节律型，如 weekly_review）：同一 ISO 自然周内
           已有同规则记录（无论状态）→ 不新建
         · rolling_hours（催办型，如 accounting_overdue）：滚动窗口内已有
           status == "open" 的同规则待办 → 不新建
         命中时不新建、不写盘，直接返回已存在的那条
      2) 硬上限 —— todos 数组长度不超过 TODO_MAX_ENTRIES
"""
import uuid
from datetime import datetime, timedelta
from typing import Optional, Any
from services.persistence import load_user, save_user, user_write_lock

# ---- MODULE_META ----
MODULE_META = {
    "name": "todo_manager",
    "scope": "private",
    "input": ["user_id", "todo_data"],
    "output": "todos",
    "cost": "io",
    "tags": ["待办", "任务管理", "Phase3"],
    "description": "创建和管理用户待办任务（幂等去重 + 总量上限）",
    "layer": "service",
    "priority": 2,
}

# ============================================================
# 护栏常量（FIX 2026-08-30）—— 集中在此，禁止散落到业务代码
# ============================================================

#: 去重模式常量
DEDUP_MODE_CALENDAR_WEEK = "calendar_week"
DEDUP_MODE_ROLLING_HOURS = "rolling_hours"

#: 每条规则的**去重模式**。两种语义刻意并存，别用一套逻辑硬套：
#:
#:   "calendar_week"  —— 日历节律型规则（每周复盘、配置检查）。
#:       去重键 = rule_triggered + created_at 的 ISO 年-周。
#:       同一自然周内**无论状态**（open/completed/skipped）已有同规则记录 → 不新建；
#:       跨到下一个自然周 → 允许新建。
#:       为什么不用滚动小时数：用户周五 10:00 创建并完成，下周五 09:00 开首页时
#:       只过了 167h < 168h，会被压制 → 这一周的提醒直接丢了。日历语义的规则用
#:       滚动窗口天生对不齐，必须按自然周切。
#:       "周五做完周六不再烦" + "下周准点再提醒" 两件事因此同时成立。
#:
#:   "rolling_hours"  —— 催办型规则（记账逾期、行为预警）。
#:       只看 status == "open"，窗口 = TODO_DEDUP_WINDOW_HOURS。
#:       "没做完就该一直推"；做完了又重新逾期就该再提，所以只看 open 是对的。
TODO_DEDUP_MODE: dict[str, str] = {
    # 「本周末和家人做一次财务小复盘」——天然一周一条，也是膨胀最严重的规则
    "weekly_review": DEDUP_MODE_CALENDAR_WEEK,
    # 配置偏离 / 未设目标属于"结构性问题"，按自然周提醒一次
    "allocation_deviation_gt_15": DEDUP_MODE_CALENDAR_WEEK,
    "no_target_config": DEDUP_MODE_CALENDAR_WEEK,
    # 其余规则走默认（rolling_hours）
}

#: 未在 TODO_DEDUP_MODE 中声明的规则的默认去重模式。
TODO_DEDUP_MODE_DEFAULT: str = DEDUP_MODE_ROLLING_HOURS

#: rolling_hours 模式下的窗口（小时），按 rule_triggered 单独定义。
#: 含义：同一 user_id + 同一 rule_triggered，在窗口内只允许存在一条
#: status == "open" 的待办；命中时不新建、直接返回已存在的那条
#: （返回值仍是非 None 的 dict，调用方无需改动）。
#: 未列出的规则走 TODO_DEDUP_WINDOW_DEFAULT_HOURS（按天）。
#:
#: 注意：走 calendar_week 模式的规则（weekly_review /
#: allocation_deviation_gt_15 / no_target_config）**不读这张表**，
#: 所以这里刻意不为它们保留条目，避免留下"看着生效实际没用"的死配置。
TODO_DEDUP_WINDOW_HOURS: dict[str, int] = {
    # 记账逾期需要较强的推动力，按天提醒
    "accounting_overdue": 24,           # 1 天
    # 行为预警（追高/FOMO 等）按天，保留一定敏感度
    "behavior_alert_fomo": 24,          # 1 天
    "behavior_alert": 24,               # 1 天
    # 用户手工创建（API POST /api/todos）不做去重，见 create_todo(force=...)
}

#: 未在 TODO_DEDUP_WINDOW_HOURS 中显式声明的规则，默认按天去重。
TODO_DEDUP_WINDOW_DEFAULT_HOURS: int = 24

#: todos 数组硬上限。超出时按"先淘汰已关闭 → 再淘汰已过期 open →
#: 最后淘汰最老的活跃 open"的顺序丢弃最老条目。
TODO_MAX_ENTRIES: int = 500


def get_dedup_mode(rule_triggered: str) -> str:
    """返回指定规则的去重模式（calendar_week / rolling_hours）。"""
    return TODO_DEDUP_MODE.get(rule_triggered, TODO_DEDUP_MODE_DEFAULT)


def get_dedup_window_hours(rule_triggered: str) -> int:
    """
    返回指定规则在 rolling_hours 模式下的幂等窗口（小时）。

    注意：对 calendar_week 模式的规则这个值不参与判断，仅为兼容旧调用保留。
    """
    return TODO_DEDUP_WINDOW_HOURS.get(
        rule_triggered, TODO_DEDUP_WINDOW_DEFAULT_HOURS
    )


def _parse_dt(value: Any) -> Optional[datetime]:
    """宽松解析 ISO 时间字符串，失败返回 None（绝不抛异常）。"""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", ""))
    except (ValueError, TypeError):
        return None


def _iso_week_key(moment: datetime) -> tuple[int, int]:
    """
    返回 ISO 自然周标识 (ISO 年, ISO 周号)。

    用 isocalendar() 而不是 strftime("%Y-%W")：ISO 周以周一为一周之始，
    且跨年时 ISO 年会正确处理（如 2026-12-31 可能属于 2027 年第 1 周），
    避免年末年初出现"同一周被算成两周"或反之。
    """
    iso = moment.isocalendar()
    return (iso[0], iso[1])


def _find_duplicate(
    todos: list[dict],
    rule_triggered: str,
    now: datetime,
) -> Optional[dict]:
    """
    按规则对应的去重模式查找已存在的同规则待办。

    calendar_week 模式：
        同一 ISO 自然周内存在同规则记录即命中，**不区分状态**
        （已完成也算，这样"周五做完周六不再烦"成立）。
    rolling_hours 模式：
        只看 status == "open" 且 created_at 在窗口内
        （"没做完就一直推"，做完后重新逾期可再提醒）。

    Args:
        todos: 用户全部待办
        rule_triggered: 规则代码
        now: 当前时间（由调用方传入，保证同一次调用时间基准一致）

    Returns:
        命中的待办（同周/窗口内最新的一条）；没有则 None
    """
    mode = get_dedup_mode(rule_triggered)

    candidate: Optional[dict] = None
    candidate_at: Optional[datetime] = None

    if mode == DEDUP_MODE_CALENDAR_WEEK:
        current_week = _iso_week_key(now)
        for todo in todos:
            if todo.get("rule_triggered") != rule_triggered:
                continue
            # 刻意不过滤 status：同一自然周内已完成的也应压制新建
            created_at = _parse_dt(todo.get("created_at"))
            if created_at is None:
                # 时间戳缺失/损坏 → 保守视为"本周已存在"，宁可漏建也不膨胀
                if candidate is None:
                    candidate = todo
                continue
            if _iso_week_key(created_at) != current_week:
                continue
            if candidate_at is None or created_at > candidate_at:
                candidate = todo
                candidate_at = created_at
        return candidate

    # ── rolling_hours（默认）：只看 open ──
    cutoff = now - timedelta(hours=get_dedup_window_hours(rule_triggered))
    for todo in todos:
        if todo.get("status") != "open":
            continue
        if todo.get("rule_triggered") != rule_triggered:
            continue
        created_at = _parse_dt(todo.get("created_at"))
        if created_at is None:
            # 时间戳缺失/损坏 → 保守地视为"窗口内存在"，宁可不新建也不膨胀
            if candidate is None:
                candidate = todo
            continue
        if created_at < cutoff:
            continue
        if candidate_at is None or created_at > candidate_at:
            candidate = todo
            candidate_at = created_at

    return candidate


def _drop_priority(todo: dict, now: datetime) -> int:
    """
    超限淘汰优先级，数字越小越先被丢弃。

    0 = 已关闭（completed / skipped），最先丢
    1 = open 但 due_by 已过期（僵尸待办）
    2 = open 且未过期（活跃待办，最后才丢）
    """
    if todo.get("status") != "open":
        return 0
    due_by = _parse_dt(todo.get("due_by"))
    if due_by is not None and due_by < now:
        return 1
    return 2


def _enforce_todo_cap(
    todos: list[dict],
    max_entries: int = TODO_MAX_ENTRIES,
) -> int:
    """
    就地裁剪 todos 到 max_entries 以内。

    淘汰顺序：已关闭 → 已过期 open → 活跃 open，同优先级内按 created_at 升序
    （最老的先丢）。原地修改传入的 list，不做 deepcopy（49MB 大文件场景下
    多份拷贝会打爆内存）。

    Args:
        todos: 待办列表（会被原地修改）
        max_entries: 保留上限

    Returns:
        被丢弃的条目数
    """
    overflow = len(todos) - max_entries
    if overflow <= 0:
        return 0

    now = datetime.now()
    # (淘汰优先级, created_at, 原始下标) 升序排序，取前 overflow 个丢弃
    ranked = [
        (_drop_priority(todo, now), str(todo.get("created_at") or ""), idx)
        for idx, todo in enumerate(todos)
    ]
    ranked.sort()
    drop_indexes = {item[2] for item in ranked[:overflow]}

    todos[:] = [todo for idx, todo in enumerate(todos) if idx not in drop_indexes]
    return overflow


def create_todo(
    user_id: str,
    title: str,
    rule_triggered: str,
    due_by_days: Optional[int] = None,
    metadata: Optional[dict[str, Any]] = None,
    force: bool = False,
) -> Optional[dict]:
    """
    创建待办项（幂等 + 总量上限）。

    幂等语义（FIX 2026-08-30）：
        按 get_dedup_mode(rule_triggered) 选择两种去重语义之一：

        - "calendar_week"（日历节律型，如 weekly_review）：
            同一 ISO 自然周内已有同规则记录（**无论 open / completed / skipped**）
            → 不新建。跨到下一个自然周 → 允许新建。
            这样"周五做完、周六不再烦"和"下周准点再提醒"同时成立，
            且不会像滚动 168h 那样出现边界漂移（周五 10:00 → 下周五 09:00
            只过 167h 会被误压制，导致这一周的提醒丢失）。

        - "rolling_hours"（催办型，如 accounting_overdue）：
            只看 status == "open"，窗口内已有则不新建。
            "没做完就一直推"；做完后重新逾期可以再提醒。

        命中已有待办时**不新建、不写盘**，直接返回已存在的那条。

        为什么命中时完全不写盘（连 due_by 都不刷新）：
        调用方 cfo_dashboard._generate_todos() 位于"读仪表板"路径上，被 55 秒
        一次的后台预热线程反复调用。如果命中去重仍然 save_user()，就还是在读
        路径上写一个十几 MB 的 JSON —— 那正是把进程写到被 SIGKILL、进而留下
        data/users/*.tmp 孤儿文件的原因。所以命中即短路返回，让稳定态下的读
        路径真正零写入。需要强制新建的场景请传 force=True。

    Args:
        user_id: 用户ID
        title: 待办标题
        rule_triggered: 触发规则代码（如 "allocation_deviation_gt_15"）
        due_by_days: 多少天后截止（可选）
        metadata: 扩展元数据（可选）
        force: True 时跳过幂等检查强制新建（用于用户手工创建等显式意图，
               总量上限仍然生效）

    Returns:
        新建的待办项，或已存在的同规则待办项；
        **仅当抢锁超时**时返回 None（此时明确放弃了写入，日志里有 warning）
    """
    # ── RMW 临界区：load → 判重 → append → save 必须整段在锁内 ──
    # 丢更新发生在 load 和 save 之间的窗口，只锁 save_user() 是无效的。
    # 幂等检查也必须在锁内：否则两个线程同时判定"不存在"然后都创建，幂等会漏。
    with user_write_lock(user_id) as acquired:
        if not acquired:
            # 超时不硬等（这条路径上有 API 请求，卡死比丢一条待办严重）。
            # 降级：做一次无锁只读判重 —— 若本来就该去重，返回已有那条即为
            # 完全正确的结果，连写都不需要。
            snapshot = load_user(user_id).get("todos")
            if isinstance(snapshot, list) and not force:
                existing = _find_duplicate(snapshot, rule_triggered, datetime.now())
                if existing is not None:
                    return existing
            print(
                f"[TODO] ⚠️ 抢锁超时，放弃创建待办: user={user_id}, "
                f"rule={rule_triggered}"
            )
            return None

        user_data = load_user(user_id)
        todos = user_data.get("todos")
        if not isinstance(todos, list):
            todos = []
            user_data["todos"] = todos

        now = datetime.now()

        # ── 护栏 1：幂等去重（模式由 TODO_DEDUP_MODE 决定）──
        if not force:
            existing = _find_duplicate(todos, rule_triggered, now)
            if existing is not None:
                # 命中 → 零写入短路返回（不走到 save，保住"读路径零写盘"）
                return existing

        due_by = None
        if due_by_days:
            due_by = (now + timedelta(days=due_by_days)).isoformat()

        todo = {
            "id": f"todo_{uuid.uuid4().hex[:8]}",
            "title": title,
            "rule_triggered": rule_triggered,
            "created_at": now.isoformat(),
            "due_by": due_by,
            "status": "open",
            "metadata": metadata or {},
        }

        todos.append(todo)

        # ── 护栏 2：硬上限（原地裁剪，不复制大对象）──
        dropped = _enforce_todo_cap(todos, TODO_MAX_ENTRIES)
        if dropped:
            print(
                f"[TODO] ⚠️ {user_id} todos 超过上限 {TODO_MAX_ENTRIES}，"
                f"已淘汰最老的 {dropped} 条"
            )

        user_data["todos"] = todos
        save_user(user_data)

        return todo


def update_todo(
    user_id: str,
    todo_id: str,
    **kwargs
) -> Optional[dict]:
    """
    更新待办项。
    
    Args:
        user_id: 用户ID
        todo_id: 待办ID
        **kwargs: 要更新的字段（title, status, due_by, metadata 等）
    
    Returns:
        更新后的待办项，如果不存在返回 None；抢锁超时也返回 None
    """
    # RMW 临界区：load → 改字段 → save 必须整段在锁内
    with user_write_lock(user_id) as acquired:
        if not acquired:
            print(f"[TODO] ⚠️ 抢锁超时，放弃更新待办: user={user_id}, todo={todo_id}")
            return None

        user_data = load_user(user_id)
        todos = user_data.get("todos", [])

        for todo in todos:
            if todo.get("id") == todo_id:
                # 更新允许的字段
                for key in ["title", "status", "due_by", "metadata"]:
                    if key in kwargs:
                        todo[key] = kwargs[key]

                save_user(user_data)
                return todo

        return None


def delete_todo(user_id: str, todo_id: str) -> bool:
    """
    删除待办项。
    
    Args:
        user_id: 用户ID
        todo_id: 待办ID
    
    Returns:
        是否成功删除（抢锁超时返回 False）
    """
    # RMW 临界区：load → 过滤 → save 必须整段在锁内
    with user_write_lock(user_id) as acquired:
        if not acquired:
            print(f"[TODO] ⚠️ 抢锁超时，放弃删除待办: user={user_id}, todo={todo_id}")
            return False

        user_data = load_user(user_id)
        todos = user_data.get("todos", [])

        original_count = len(todos)
        user_data["todos"] = [t for t in todos if t.get("id") != todo_id]

        if len(user_data["todos"]) < original_count:
            save_user(user_data)
            return True

        return False


def get_todos(
    user_id: str,
    status_filter: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    """
    获取待办列表。
    
    Args:
        user_id: 用户ID
        status_filter: 状态过滤（"open", "completed", "skipped"，None 表示全部）
        limit: 最多返回条数
    
    Returns:
        待办列表
    """
    user_data = load_user(user_id)
    todos = user_data.get("todos", [])
    
    if status_filter:
        todos = [t for t in todos if t.get("status") == status_filter]
    
    # 按创建时间倒序
    todos.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    
    return todos[:limit]


def get_todo_by_id(user_id: str, todo_id: str) -> Optional[dict]:
    """
    按 ID 获取单个待办项。
    
    Returns:
        待办项，不存在则返回 None
    """
    user_data = load_user(user_id)
    todos = user_data.get("todos", [])
    
    for todo in todos:
        if todo.get("id") == todo_id:
            return todo
    
    return None


def mark_done(user_id: str, todo_id: str) -> Optional[dict]:
    """
    标记待办项完成。
    
    Args:
        user_id: 用户ID
        todo_id: 待办ID
    
    Returns:
        更新后的待办项
    """
    return update_todo(user_id, todo_id, status="completed")


def mark_skipped(user_id: str, todo_id: str) -> Optional[dict]:
    """
    标记待办项为已跳过。
    
    Args:
        user_id: 用户ID
        todo_id: 待办ID
    
    Returns:
        更新后的待办项
    """
    return update_todo(user_id, todo_id, status="skipped")


def get_overdue_todos(user_id: str) -> list[dict]:
    """
    获取所有超期的待办项（due_by 已过期且状态为 open）。
    
    Returns:
        超期待办列表
    """
    user_data = load_user(user_id)
    todos = user_data.get("todos", [])
    
    now = datetime.now()
    overdue = []
    
    for todo in todos:
        if todo.get("status") == "open" and todo.get("due_by"):
            try:
                due_time = datetime.fromisoformat(todo["due_by"])
                if due_time < now:
                    overdue.append(todo)
            except (ValueError, TypeError):
                pass
    
    return overdue


def get_open_count(user_id: str) -> int:
    """获取未完成的待办数量"""
    user_data = load_user(user_id)
    todos = user_data.get("todos", [])
    return len([t for t in todos if t.get("status") == "open"])


def clear_old_todos(user_id: str, keep_days: int = 30) -> int:
    """
    清理已完成/已跳过且超过 N 天的待办项。
    
    Args:
        user_id: 用户ID
        keep_days: 保留天数（默认 30）
    
    Returns:
        删除的数量（抢锁超时返回 0）
    """
    # RMW 临界区：load → 重建列表 → save 必须整段在锁内
    with user_write_lock(user_id) as acquired:
        if not acquired:
            print(f"[TODO] ⚠️ 抢锁超时，放弃清理旧待办: user={user_id}")
            return 0

        user_data = load_user(user_id)
        todos = user_data.get("todos", [])

        cutoff = datetime.now() - timedelta(days=keep_days)

        new_todos = []
        for todo in todos:
            status = todo.get("status", "open")
            if status == "open":
                # 开放的待办保留
                new_todos.append(todo)
            else:
                # 已完成/已跳过的，如果超过 keep_days 则删除
                try:
                    created_time = datetime.fromisoformat(todo.get("created_at", ""))
                    if created_time >= cutoff:
                        new_todos.append(todo)
                except (ValueError, TypeError):
                    # 解析失败的保留
                    new_todos.append(todo)

        deleted_count = len(todos) - len(new_todos)
        user_data["todos"] = new_todos

        if deleted_count > 0:
            save_user(user_data)

        return deleted_count


__all__ = [
    "create_todo",
    "update_todo",
    "delete_todo",
    "get_todos",
    "get_todo_by_id",
    "mark_done",
    "mark_skipped",
    "get_overdue_todos",
    "get_open_count",
    "clear_old_todos",
    "get_dedup_mode",
    "get_dedup_window_hours",
    "TODO_DEDUP_MODE",
    "TODO_DEDUP_MODE_DEFAULT",
    "TODO_DEDUP_WINDOW_HOURS",
    "TODO_DEDUP_WINDOW_DEFAULT_HOURS",
    "TODO_MAX_ENTRIES",
    "DEDUP_MODE_CALENDAR_WEEK",
    "DEDUP_MODE_ROLLING_HOURS",
]

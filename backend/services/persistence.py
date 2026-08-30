"""
钱袋子 — 数据持久化
用户数据文件读写（Phase 0 升级：原子写 + 损坏恢复）

FIX 2026-08-30：新增 user_write_lock() 解决"丢更新"（lost update）
------------------------------------------------------------------
背景：atomic_write_json() 解决的是 **torn write**（写一半崩掉导致 JSON 损坏），
     它保证不了 **lost update**（丢更新）。实测证据：5 个线程并发
     create_todo()，即使用 5 个完全不同的 rule_triggered（不涉及任何去重），
     8 轮实验最终落盘都只有 1 条 —— 另外 4 条被静默覆盖。

根因：所有调用方都是 load_user() → 改 → save_user() 这个
     read-modify-write（RMW）模式。丢更新发生在 **load 和 save 之间的窗口**，
     不在 save 内部。所以只给 save_user() 加锁是**无效**的：那只能让两次写不
     交错（而 os.replace 已经保证了这点），5 个线程照样各自 load 到同一份旧快照。
     锁必须覆盖**整个 load-modify-save 临界区**。

生产暴露面：钱袋子存在真实且持续的并发写 —— uvicorn 主线程、
     cfo_dashboard._prewarm_loop()（55s 一轮）、_bg_refresh_cfo()，
     以及 night_worker / cache_warmer / 各 cron **独立进程**。
     因此锁必须跨进程（fcntl.flock），纯 threading.Lock 挡不住。
"""
import json
import hashlib
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime
from typing import Optional
from config import USERS_DIR

try:
    import fcntl  # POSIX 专有；Linux/macOS 均可用（生产是 Linux）
    _FCNTL_AVAILABLE = True
except ImportError:  # pragma: no cover - Windows 兜底，不应在生产出现
    fcntl = None
    _FCNTL_AVAILABLE = False

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "persistence",
    "scope": "private",
    "input": ["user_id"],
    "output": "user_data",
    "cost": "cpu",
    "tags": ["持久化", "用户IO", "SHA256"],
    "description": "用户数据文件读写（SHA256路径隔离 + 原子写 + RMW 跨进程锁）",
    "layer": "data",
    "priority": 1,
}

# ============================================================
# RMW 并发锁（FIX 2026-08-30）
# ============================================================

#: 抢锁超时（秒）。超时后**放弃写入并打日志**，绝不硬等 ——
#: 这条路径上有 API 请求，卡死比丢一条待办严重得多。
USER_LOCK_TIMEOUT_SECONDS: float = 10.0

#: flock 轮询间隔（秒）。用 LOCK_NB + 轮询而不是阻塞式 flock，
#: 因为阻塞式 flock 没有超时机制。
_LOCK_POLL_INTERVAL: float = 0.05

#: per-user 进程内线程锁。flock 是 per open-file-description 语义，
#: 同进程多线程各自 open 也能互斥，但叠一层线程锁更稳、且能省掉大量
#: open/flock syscall（同进程内争抢时直接在用户态排队）。
_thread_locks: dict[str, threading.Lock] = {}

#: 保护 _thread_locks 字典本身的创建（避免两个线程各建一把锁）
_thread_locks_guard = threading.Lock()

#: 线程本地的持锁深度，用于支持**可重入**：
#: 若同一线程已持有某 user 的锁，嵌套进入时直接放行。
#: 不这么做的话，嵌套调用（如在 user_write_lock 内再调 update_todo）
#: 会在 threading.Lock 或 flock 上自锁死。
_local = threading.local()


def _lock_depths() -> dict:
    """返回当前线程的 {user_id: 持锁深度} 字典。"""
    depths = getattr(_local, "depths", None)
    if depths is None:
        depths = {}
        _local.depths = depths
    return depths


def _get_thread_lock(user_id: str) -> threading.Lock:
    """取（或创建）指定用户的进程内线程锁。"""
    lock = _thread_locks.get(user_id)
    if lock is None:
        with _thread_locks_guard:
            lock = _thread_locks.get(user_id)
            if lock is None:
                lock = threading.Lock()
                _thread_locks[user_id] = lock
    return lock


def _lock_file_path(user_id: str) -> Path:
    """
    锁文件路径：<用户文件>.lock，与用户文件同目录。

    注意：`abc.json` → `abc.json.lock`，因此
    - prune_todos.py 的 glob("*.json") 匹配不到它
    - housekeeping_cron.py 只清 *.tmp 和带日期的归档，也匹配不到它
    锁文件是常驻的基础设施，**不能被任何清理脚本删掉**。
    """
    return _user_file(user_id).with_suffix(".json.lock")


@contextmanager
def user_write_lock(
    user_id: str,
    timeout: Optional[float] = None,
):
    """
    保护单个用户数据的 read-modify-write 临界区（跨进程 + 跨线程）。

    用法：
        with user_write_lock(uid) as acquired:
            if not acquired:
                return None          # 没拿到锁 → 放弃写入，不要硬等
            data = load_user(uid)
            data["todos"].append(...)
            save_user(data)

    yield 的是 bool（是否成功拿到锁），调用方**必须检查**。超时不抛异常、
    不硬等，而是打 warning 日志后 yield False，让调用方决定降级行为 ——
    因为这条路径上有 API 请求，卡死 10 秒比丢一条待办严重得多。

    ⚠️ 锁的作用域约定（重要）：
        锁**只在调用方的 RMW 临界区**使用。`load_user()` / `save_user()`
        **内部一律不加锁**。原因：如果 save_user() 内部也抢同一把锁，
        那么"锁内调 save_user"就会自锁死。保持"锁由调用方显式持有"这一条
        单一约定，比在底层函数里藏锁更容易推理。

    ⚠️⚠️ 在 async def 中使用本锁必须走 `await asyncio.to_thread(同步函数)`，
        **不可在事件循环里直接 `with`** —— flock 阻塞会冻住整个事件循环，
        导致全站请求卡死（不是只影响当前请求，是所有用户的所有请求一起卡）。
        参考 `api/user.py::_persist_ocr_result` 的写法：把临界区抽成同步内部
        函数，再 `await asyncio.to_thread(它)`。
        本项目目前只有 `api/user.py::ocr_receipt` 一个 async 写端点，
        新增 async 写端点时务必照此模式。

    ⚠️ 临界区边界：把**昂贵操作放在锁外**（网络请求 / OCR / LLM / 大量计算），
        锁内只做 load → 改 → save。持锁等网络会让超时形同虚设、把正常请求
        全顶成 503。若锁外算好的数据依赖用户状态，锁内必须**重新 load 并
        重做判定**（参考 `services/monthly_snapshot.py` 的幂等重检）。

    可重入：同一线程对同一 user 嵌套进入时直接放行（深度计数），
    因此 `with user_write_lock(uid): update_todo(uid, ...)` 不会死锁。

    两层锁的分工：
        1. threading.Lock（per-user）—— 挡同进程多线程，省 syscall
        2. fcntl.flock（排他）—— 挡独立进程（night_worker / cache_warmer / cron）

    Args:
        user_id: 用户ID
        timeout: 抢锁总超时（秒），线程锁与 flock 共享同一个 deadline。
            None 表示读取模块级 USER_LOCK_TIMEOUT_SECONDS。
            ⚠️ 刻意用 None 而不是把常量写进默认值：Python 的默认参数在
            **函数定义时**求值，写成 `timeout=USER_LOCK_TIMEOUT_SECONDS`
            会导致运维改模块常量完全不生效（实测踩过：改成 1s 仍等了 10s）。

    Yields:
        True 表示已持锁；False 表示超时未获取（调用方应放弃写入）
    """
    if timeout is None:
        timeout = USER_LOCK_TIMEOUT_SECONDS

    depths = _lock_depths()
    # ── 可重入：本线程已持有该用户的锁 → 直接放行 ──
    if depths.get(user_id, 0) > 0:
        depths[user_id] += 1
        try:
            yield True
        finally:
            depths[user_id] -= 1
        return

    deadline = time.monotonic() + timeout
    thread_lock = _get_thread_lock(user_id)

    # ── 第 1 层：进程内线程锁 ──
    if not thread_lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
        print(f"[PERSISTENCE] ⚠️ 抢线程锁超时（{timeout}s），放弃写入: user={user_id}")
        yield False
        return

    lock_fd = None
    flock_held = False
    try:
        # ── 第 2 层：跨进程 flock ──
        if _FCNTL_AVAILABLE:
            lock_path = _lock_file_path(user_id)
            try:
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
                while True:
                    try:
                        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        flock_held = True
                        break
                    except (BlockingIOError, OSError):
                        if time.monotonic() >= deadline:
                            print(
                                f"[PERSISTENCE] ⚠️ 抢文件锁超时（{timeout}s），"
                                f"放弃写入: user={user_id}, lock={lock_path.name}"
                            )
                            yield False
                            return
                        time.sleep(_LOCK_POLL_INTERVAL)
            except OSError as e:
                # 锁文件创建失败（磁盘满/权限）→ 明确告警。
                # 此时退化为"仅线程锁保护"，同进程内仍安全，跨进程失去保护。
                print(
                    f"[PERSISTENCE] ⚠️ 无法创建锁文件，退化为仅线程锁: "
                    f"user={user_id}, error={e}"
                )
        else:  # pragma: no cover
            print("[PERSISTENCE] ⚠️ fcntl 不可用，跨进程锁失效（仅线程锁生效）")

        depths[user_id] = depths.get(user_id, 0) + 1
        try:
            yield True
        finally:
            depths[user_id] -= 1
    finally:
        # 异常路径也必须释放锁 + 关闭 fd，绝不泄漏
        if lock_fd is not None:
            try:
                if flock_held:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(lock_fd)
            except OSError:
                pass
        thread_lock.release()


# ---- 持久化工具 ----
def _user_file(user_id: str) -> Path:
    safe_id = hashlib.sha256(user_id.encode()).hexdigest()[:16]
    return USERS_DIR / f"{safe_id}.json"

def _init_phase3_fields(data: dict) -> dict:
    """
    Phase 3 字段初始化。如果用户数据不包含 Phase 3 字段，自动添加。
    
    新增字段：
    - behavior_events: 行为事件列表（最多 500 条）
    - todos: 待办任务列表
    - monthly_snapshots: 月度快照字典 {YYYY-MM: snapshot_data}
    
    这个函数在 load_user() 和 save_user() 中被调用，确保 Phase 3 字段总是存在。
    """
    if "behavior_events" not in data:
        data["behavior_events"] = []
    
    if "todos" not in data:
        data["todos"] = []
    
    if "monthly_snapshots" not in data:
        data["monthly_snapshots"] = {}
    
    return data

def load_user(user_id: str) -> dict:
    """安全读取用户 JSON（损坏时尝试从 .bak 恢复）"""
    f = _user_file(user_id)
    backup = f.with_suffix(".json.bak")

    # 1. 尝试读主文件
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            # Phase 3: 确保新字段存在
            data = _init_phase3_fields(data)
            return data
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"[PERSISTENCE] ⚠️ 用户文件损坏: {f}, error: {e}")

    # 2. 主文件不存在或损坏 → 尝试从备份恢复
    if backup.exists():
        try:
            data = json.loads(backup.read_text(encoding="utf-8"))
            # Phase 3: 确保新字段存在
            data = _init_phase3_fields(data)
            print(f"[PERSISTENCE] 🔄 从备份恢复: {backup}")
            atomic_write_json(f, data)  # 恢复主文件
            return data
        except (json.JSONDecodeError, UnicodeDecodeError):
            print(f"[PERSISTENCE] 🔴 备份也损坏: {backup}")

    # 3. 全新用户
    data = {
        "userId": user_id,
        "portfolio": None,
        "ledger": [],
        "createdAt": datetime.now().isoformat(),
        "updatedAt": datetime.now().isoformat(),
    }
    # Phase 3: 新用户也要初始化 Phase 3 字段
    data = _init_phase3_fields(data)
    return data

def save_user(data: dict):
    """原子写用户 JSON（tmp + fsync + rename，防断电损坏）"""
    data["updatedAt"] = datetime.now().isoformat()
    f = _user_file(data["userId"])
    atomic_write_json(f, data)

def atomic_write_json(filepath: Path, data: dict):
    """原子写 JSON：先写 tmp，再 rename（POSIX rename 是原子操作）
    
    Phase 0 新增 — 三方 AI 审查共识：
    直接 write_text() 不是原子操作，写到一半断电/崩溃会导致 JSON 损坏。
    即使已改为 uvicorn ×1，night_worker 仍是独立进程，存在并发写可能。
    """
    filepath = Path(filepath)
    dir_path = filepath.parent
    dir_path.mkdir(parents=True, exist_ok=True)

    # 1. 写入同目录临时文件（同分区才能 rename）
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())  # 确保数据落盘
        os.replace(tmp_path, str(filepath))  # 原子替换
    except Exception:
        # 写入失败 → 清理临时文件，原文件不受影响
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

def backup_user_files():
    """备份所有用户 JSON（凌晨 06:00 维护任务调用）"""
    import shutil
    count = 0
    for f in USERS_DIR.glob("*.json"):
        if not f.name.endswith(".bak"):
            shutil.copy2(f, f.with_suffix(".json.bak"))
            count += 1
    if count:
        print(f"[PERSISTENCE] 📦 备份 {count} 个用户文件")


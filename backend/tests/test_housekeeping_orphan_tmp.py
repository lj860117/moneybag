"""
housekeeping_cron 孤儿 .tmp 收集的回归测试
==========================================
锁定一个**当前靠隐式行为成立、且极易被静默破坏**的不变式：

    collect_orphan_tmp() 必须能收到「点开头」的孤儿临时文件。

为什么需要专门守它（2026-08-30）：
  scripts/cache_warmer.py 落盘用
      tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=CACHE_DIR)
  所以它被 SIGKILL 后留下的孤儿**100% 都是点开头**，
  形如 data/_cache/.market_context.a1b2c3.tmp。

  而 Python 各种"看起来等价"的文件遍历方式在这点上**行为不同**：
      pathlib.Path.rglob("*.tmp")        → 匹配点开头 ✅
      glob.glob("**/*.tmp", recursive=1) → 不匹配点开头 ❌
      shell  ls *.tmp / find -name       → 不匹配点开头 ❌

  也就是说，有人把 rglob 改成 glob（或改成 shell 一行）就会让缓存孤儿的
  收集率从 100% 掉到 0%，而且是**静默失效** —— 脚本照跑、照样打印
  "命中 0 个孤儿"、看起来一切正常。这正是本轮反复治的那类病：
  一个没有任何东西守着的隐式不变式。

  本文件把这个隐式依赖变成显式契约。
"""
import os
import sys
import time
import tempfile
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _load_collect_orphan_tmp():
    """独立加载 housekeeping_cron.collect_orphan_tmp（避免依赖 config 的目录副作用）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_hk_under_test", _BACKEND / "scripts" / "housekeeping_cron.py"
    )
    # housekeeping_cron 顶层 import config，会创建目录；用临时 DATA_DIR 隔离
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.collect_orphan_tmp


def _make_tree(root: Path) -> dict:
    """构造一棵覆盖各种边界的测试目录树。"""
    old = time.time() - 5 * 86400          # 5 天前 → 超龄
    cache = root / "_cache"
    cache.mkdir(parents=True, exist_ok=True)
    users = root / "users"
    users.mkdir(parents=True, exist_ok=True)

    made = {}

    # 1) 点开头 + 嵌套子目录 + 超龄 —— cache_warmer 被 SIGKILL 的真实形状
    p = cache / ".market_context.a1b2c3.tmp"
    p.write_text("x" * 1024, encoding="utf-8")
    os.utime(p, (old, old))
    made["dot_nested_old"] = p

    # 2) 点开头 + 顶层 + 超龄
    p = root / ".toplevel.zz11.tmp"
    p.write_text("x", encoding="utf-8")
    os.utime(p, (old, old))
    made["dot_top_old"] = p

    # 3) 普通命名 + 超龄 —— persistence.atomic_write_json 的形状
    p = users / "tmpABCDEF.tmp"
    p.write_text("x", encoding="utf-8")
    os.utime(p, (old, old))
    made["plain_old"] = p

    # 4) 点开头但很新 —— 可能正在写入，绝不能删
    p = cache / ".market_context.fresh99.tmp"
    p.write_text("writing", encoding="utf-8")
    made["dot_fresh"] = p

    # 5) 正式缓存文件（非 .tmp）+ 超龄 —— 绝不能删
    p = cache / "market_context.json"
    p.write_text("{}", encoding="utf-8")
    os.utime(p, (old, old))
    made["real_cache"] = p

    return made


def test_collects_dot_prefixed_orphan_tmp():
    """核心断言：点开头 + 嵌套 + 超龄的孤儿必须被收到。

    这条挂了通常意味着有人把 pathlib.rglob 换成了 glob/shell 写法，
    导致 cache_warmer 的孤儿（全是点开头）一个都收不到。
    """
    collect_orphan_tmp = _load_collect_orphan_tmp()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        made = _make_tree(root)
        hits = collect_orphan_tmp(root, 1.0, time.time())
        hit_paths = {p for p, _size, _age in hits}

        assert made["dot_nested_old"] in hit_paths, (
            "点开头 + 嵌套子目录的超龄孤儿未被收集 —— "
            "collect_orphan_tmp 必须用 pathlib.Path.rglob，"
            "glob.glob() 和 shell glob 都不匹配点开头文件，"
            "而 cache_warmer 的孤儿 100% 是点开头（prefix=f'.{name}.'）"
        )
        assert made["dot_top_old"] in hit_paths, "顶层的点开头超龄孤儿未被收集"
        assert made["plain_old"] in hit_paths, "普通命名的超龄孤儿未被收集"


def test_does_not_collect_fresh_or_non_tmp():
    """反向断言：新鲜的 .tmp 和正式文件都不能被收集。

    1 天阈值的作用是"绝不误删正在写入的文件"，这条守住它不被调小。
    """
    collect_orphan_tmp = _load_collect_orphan_tmp()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        made = _make_tree(root)
        hits = collect_orphan_tmp(root, 1.0, time.time())
        hit_paths = {p for p, _size, _age in hits}

        assert made["dot_fresh"] not in hit_paths, (
            "刚创建的 .tmp 被收集了 —— 可能正在写入，误删会损坏数据；"
            "min_age_days 阈值不可调小到 0"
        )
        assert made["real_cache"] not in hit_paths, (
            "正式缓存文件（非 .tmp）被收集了 —— 只应清理 *.tmp"
        )
        assert len(hit_paths) == 3, f"应恰好收集 3 个超龄孤儿，实际 {len(hit_paths)}"


def test_pathlib_rglob_matches_dotfiles_but_glob_does_not():
    """把"为什么必须用 pathlib"这个前提本身也断言下来。

    如果哪天 Python 改了 pathlib 的行为（或有人换了实现），
    这条会先挂，从而解释清楚上面那条为什么挂。
    """
    import glob as glob_mod

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        sub = root / "_cache"
        sub.mkdir()
        (sub / ".dot.abc.tmp").write_text("x", encoding="utf-8")
        (sub / "plain.tmp").write_text("x", encoding="utf-8")

        via_pathlib = {p.name for p in root.rglob("*.tmp")}
        via_glob = {Path(p).name for p in
                    glob_mod.glob(str(root / "**" / "*.tmp"), recursive=True)}

        assert ".dot.abc.tmp" in via_pathlib, (
            "pathlib.rglob 不再匹配点开头文件 —— "
            "collect_orphan_tmp 的实现前提已失效，必须改为显式处理点开头文件"
        )
        assert ".dot.abc.tmp" not in via_glob, (
            "glob.glob 现在能匹配点开头了 —— 本测试的对照前提变了，"
            "可放宽 collect_orphan_tmp 的实现约束"
        )
        assert "plain.tmp" in via_pathlib and "plain.tmp" in via_glob


# ============================================================
# 归档清理的归因数据保护白名单
# ============================================================
# 为什么必须守（2026-08-30）：
#   collect_dated_archives() 的判据是「名字含日期 + 后缀在 ARCHIVED_LOG_SUFFIXES」。
#   而下面这三类**业务归因数据**恰好同时满足这两个条件：
#       data/decision_logs/{date}.jsonl   ← AI 投资建议记录，V8 复盘归因依据
#       data/audit/{date}.jsonl           ← 审计日志
#       data/logs/pushes/{date}_*.txt     ← 推送存档，质量评估要读
#   如果没有白名单，它们会在第 90 天被**静默删除**，且无别处备份。
#   这三条断言把"哪些目录不能删"变成可执行契约。
# ============================================================

def _load_hk_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_hk_under_test2", _BACKEND / "scripts" / "housekeeping_cron.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_archive_tree(root: Path) -> dict:
    """构造归档清理场景：3 类受保护数据 + 2 类应被清的运行日志。"""
    old = time.time() - 200 * 86400   # 200 天前，远超 90 天阈值
    made = {}

    def _mk(relpath: str, key: str):
        p = root / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("data", encoding="utf-8")
        os.utime(p, (old, old))
        made[key] = p

    # 🔴 必须保护
    _mk("decision_logs/2026-01-15.jsonl", "decision_log")
    _mk("audit/2026-01-15.jsonl", "audit_log")
    _mk("logs/pushes/2026-01-15_morning_LeiJiang.txt", "push_archive")
    # ✅ 应该被清（运行日志）
    _mk("night_worker/2026-01-15.log", "night_worker_log")
    _mk("logs/2026-01-15.log", "weekend_push_log")
    # ✅ 不该被碰（无日期 → logrotate 的地盘）
    _mk("night_worker/night_worker.log", "active_log")
    return made


def test_attribution_data_is_protected_from_archive_sweep():
    """核心断言：决策日志 / 审计 / 推送存档 绝不能被归档清理收走。"""
    hk = _load_hk_module()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        made = _make_archive_tree(root)
        hits = {p for p, _s, _a in hk.collect_dated_archives(root, 90.0, time.time())}

        for key, why in (
            ("decision_log", "data/decision_logs/{date}.jsonl 是 V8 复盘的归因依据"),
            ("audit_log", "data/audit/{date}.jsonl 是审计日志，另有自己的 30 天清理策略"),
            ("push_archive", "data/logs/pushes/ 是推送存档，质量评估脚本要读"),
        ):
            assert made[key] not in hits, (
                f"归因数据被归档清理命中了：{made[key].name} —— {why}。"
                f"检查 housekeeping_cron.PROTECTED_FROM_ARCHIVE_SWEEP 是否被改动"
            )


def test_runtime_logs_are_still_collected():
    """反向断言：白名单不能过度保护 —— 真正的运行日志仍要能清。

    这条防的是"为了安全把整个 data/ 都加进白名单"这种过度收缩，
    那样脚本就等于什么都不做了。
    """
    hk = _load_hk_module()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        made = _make_archive_tree(root)
        hits = {p for p, _s, _a in hk.collect_dated_archives(root, 90.0, time.time())}

        assert made["night_worker_log"] in hits, (
            "night_worker/{date}.log 应被清理 —— 它是运行日志不是归因数据"
        )
        assert made["weekend_push_log"] in hits, (
            "logs/{date}.log 应被清理 —— 注意 logs/ 只有 pushes/ 子目录受保护，"
            "logs/ 本身的运行日志仍可清（嵌套保护，不是整个 logs/ 免删）"
        )
        assert made["active_log"] not in hits, (
            "无日期的活跃日志不该被收 —— 那是 logrotate 的地盘"
        )
        assert len(hits) == 2, f"应恰好收 2 个运行日志，实际 {len(hits)}"


def test_protected_list_is_not_empty_and_covers_known_dirs():
    """守住白名单本身不被清空或漏项。"""
    hk = _load_hk_module()
    protected = set(hk.PROTECTED_FROM_ARCHIVE_SWEEP)
    for required in ("decision_logs", "audit", "logs/pushes"):
        assert required in protected, (
            f"保护白名单缺少 {required} —— 该目录存的是业务归因数据，"
            f"移除保护会导致第 90 天静默删除"
        )


def test_json_suffix_stays_out_of_archive_sweep():
    """`.json` 必须不在清理后缀里 —— 大量归因数据用带日期的 .json。

    data/audit/history/{date}.json、data/judgments/{YYYY-MM}.json、
    data/precomputed/*_2026-*.json 都是这个形状，
    一旦 .json 进了 ARCHIVED_LOG_SUFFIXES，它们会被当过期归档删掉。
    """
    hk = _load_hk_module()
    assert ".json" not in hk.ARCHIVED_LOG_SUFFIXES, (
        "`.json` 被加进了 ARCHIVED_LOG_SUFFIXES —— "
        "这会删掉 data/audit/history/、data/judgments/、data/precomputed/ 里的"
        "带日期业务数据。如确需清理 .json，必须先把这些目录加进"
        "PROTECTED_FROM_ARCHIVE_SWEEP"
    )


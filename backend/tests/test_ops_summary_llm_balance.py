"""
运维巡检「DeepSeek 余额恒定滞后一天」根因回归测试
================================================

事故回顾（2026-09-13 ~ 09-16，服务器实测）：

    | 快照文件                  | 快照里写的余额 | 其实是哪天的值 |
    |---------------------------|----------------|----------------|
    | snapshot_2026-09-13.json  | ¥16.88         | 09-12 的值     |
    | snapshot_2026-09-14.json  | ¥15.23         | 09-13 的值     |
    | snapshot_2026-09-15.json  | ¥10.59         | 09-14 的值     |
    | snapshot_2026-09-16.json  | ¥9.87          | 09-15 的值     |

根因是两件事叠加：
  1. cron 顺序：ops_summary.py 08:03 生成快照，llm_balance_monitor.py 08:05
     才把当天的余额写进日志 —— 快照永远早于余额写入。
  2. `collect_llm_balance()` 从**日志文本**反解析余额，遍历所有行、无脑后写
     覆盖。日志是多天滚动追加的，当天的行还没写出来时，它就静默拿昨天的行
     充数，没有任何 stale 标记。

真实后果：09-15 余额 ¥9.87 触发低余额告警并推了企微，当天已充值；
09-16 08:05:02 监控实测 ¥104.78。但 09-16 的巡检日报仍写「deepseek 余额
¥9.87」，用户看了会误判要再充值。

修复取向：**取最近一次，但必须把它的日期报出来**。日志是追加写的，所以
「最近一次」= 日志里最后一条该 provider 的余额行（按行序取，不按日期大小
排序）；行首解析不出日期的行一律丢弃。`balance_asof` 记下这批余额的真实日期，
`stale` = 它 != 目标日期 —— 既不假（日期标出来了）也不缺（有数）。

⚠️ 本文件最关键的是第 2 节「只有昨天的行 → 必须取到，且 stale=True、
asof=昨天」。那是 9dfd22c「只认当天」回归的直接复现：生产 cron 08:03 生成
快照、08:05 才写当天余额，只认当天会让日报天天报"未取到"。反向地，第 3 节
「昨天的行 + 今天的行 → 必须取今天的」保证"最近一次"确实按日志顺序取最新，
不会退回拿旧数冒充的老口径。

设计原则（与 test_ops_error_dedup.py 一致）：
  - **不复制实现里的正则/常量**，全部真实调用 `scripts/ops_summary.py` 的
    `collect_llm_balance()`，实现一改测试立刻能感知。
  - 扫描范围通过 monkeypatch `_candidate_dirs()` 与 `_BACKEND_DIR` 钉死在
    `tmp_path` 内，绝不碰生产 `data/` 与 `backend/logs/`。
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
OPS_SUMMARY_PATH = BACKEND_DIR / "scripts" / "ops_summary.py"

# 固定锚点日期，避免测试依赖真实跑测日期
TODAY = date(2026, 9, 16)
YESTERDAY = date(2026, 9, 15)

_MODULE: ModuleType | None = None


def _load_ops_summary() -> ModuleType:
    """以文件路径方式加载 scripts/ops_summary.py（scripts/ 不是包）。"""
    global _MODULE
    if _MODULE is None:
        spec = importlib.util.spec_from_file_location(
            "_mb_ops_summary_balance_sut", OPS_SUMMARY_PATH
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["_mb_ops_summary_balance_sut"] = module
        spec.loader.exec_module(module)
        _MODULE = module
    return _MODULE


@pytest.fixture
def ops_env(monkeypatch, tmp_path):
    """返回一个 `write_log(lines) -> dict` 的工厂。

    把日志根目录钉死在 tmp_path/logs/ 下：`collect_llm_balance()` 的候选目录
    是 `_candidate_dirs("logs") + [_BACKEND_DIR / "logs"]`，两处都指向 tmp。
    """
    mod = _load_ops_summary()
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mod, "_candidate_dirs", lambda sub: [tmp_path / sub], raising=True)
    monkeypatch.setattr(mod, "_BACKEND_DIR", tmp_path, raising=True)

    def write_log(lines: list[str]) -> None:
        (log_dir / "llm_balance_monitor.log").write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
        )

    # ⚠️ 默认必须是 None，不能是 TODAY：collect() 不传参时要真的走到
    # `collect_llm_balance()` 的「target_date 缺省 = date.today()」分支。
    # 曾经写成 `target=TODAY`，于是 test_default_target_date_is_today 拿
    # 2026-09-16 当目标去比对 date.today() 写的行 —— 在 09-17 及以后必红。
    def collect(target=None):
        return mod.collect_llm_balance(target)

    return SimpleNamespace(log_dir=log_dir, write_log=write_log, collect=collect)


def _balance_line(day: date, provider: str = "deepseek", amount: str = "104.78",
                  threshold: str = "10.00") -> str:
    """拼一行真实格式的余额日志（形如 llm_balance_monitor.py:375 的产出）。"""
    return (
        f"{day.isoformat()} 08:05:02,123 [INFO] [{provider}] "
        f"当前余额: ¥{amount}（阈值 ¥{threshold}）"
    )


# ── 1. 只有当天的余额行：正常取到 ────────────────────────────────
def test_only_today_line_is_collected(ops_env):
    ops_env.write_log([
        f"{TODAY.isoformat()} 08:05:01,000 [INFO] ===== LLM 余额监控启动 =====",
        _balance_line(TODAY, "deepseek", "104.78"),
    ])
    r = ops_env.collect(TODAY)

    assert r["checked"] is True
    assert r["balances"] == {"deepseek": "¥104.78"}
    assert r["stale"] is False
    assert r["balance_asof"] == TODAY.isoformat()


def test_multiple_providers_same_day_are_all_collected(ops_env):
    ops_env.write_log([
        _balance_line(TODAY, "deepseek", "104.78"),
        _balance_line(TODAY, "doubao", "52.10"),
    ])
    r = ops_env.collect(TODAY)

    assert r["balances"] == {"deepseek": "¥104.78", "doubao": "¥52.10"}
    assert r["stale"] is False
    assert r["balance_asof"] == TODAY.isoformat()


def test_default_target_date_is_today(ops_env):
    """不传 target_date 时按 date.today() 判定，当天的行照样能取到。"""
    ops_env.write_log([_balance_line(date.today(), "deepseek", "77.77")])
    r = ops_env.collect()

    assert r["balances"] == {"deepseek": "¥77.77"}
    assert r["stale"] is False
    assert r["balance_asof"] == date.today().isoformat()


def test_date_separator_variants_normalized(ops_env):
    """`2026/9/16` `2026.9.16` 之类的分隔符变体也要认成当天。"""
    ops_env.write_log([f"2026/9/16 08:05:02,123 [INFO] [deepseek] 当前余额: ¥88.88（阈值 ¥10.00）"])
    r = ops_env.collect(TODAY)

    assert r["balances"] == {"deepseek": "¥88.88"}
    assert r["stale"] is False


# ── 2. 只有昨天的余额行：取到，但必须标 stale + asof（9dfd22c 回归点）──
def test_only_yesterday_line_is_collected_but_stale(ops_env):
    """9dfd22c 回归复现：今天的行还没写出来，日志里只有昨天的。

    「只认当天」的写法在这里返回空 → 生产 08:03 天天报"未取到"。正确行为是照
    取昨天的数，但把日期亮出来（stale=True / asof=昨天），让日报写成
    「DeepSeek 余额 ¥9.87（截至 09-15）」—— 有数，且不假。
    """
    ops_env.write_log([
        f"{YESTERDAY.isoformat()} 08:05:02,123 [INFO] ===== LLM 余额监控启动 =====",
        _balance_line(YESTERDAY, "deepseek", "9.87"),   # 09-16 日报里那个数
    ])
    r = ops_env.collect(TODAY)

    assert r["checked"] is True
    assert r["balances"] == {"deepseek": "¥9.87"}   # 有数，不再留空
    assert r["stale"] is True                        # 但不是今天的
    assert r["balance_asof"] == YESTERDAY.isoformat()


def test_latest_line_three_days_ago_is_collected_but_stale(ops_env):
    """跨天场景：日志里最新一条是 3 天前的 —— 照样取到，asof 如实报 3 天前。"""
    ops_env.write_log([
        _balance_line(date(2026, 9, 10), "deepseek", "60.00"),
        _balance_line(date(2026, 9, 13), "deepseek", "31.15"),   # 最后一条
    ])
    r = ops_env.collect(TODAY)

    assert r["balances"] == {"deepseek": "¥31.15"}
    assert r["stale"] is True
    assert r["balance_asof"] == "2026-09-13"


def test_week_old_line_is_collected_but_stale(ops_env):
    """不只是昨天：任何非当天的行都照样取，只是 asof 如实标旧。"""
    ops_env.write_log([_balance_line(date(2026, 9, 9), "deepseek", "31.15")])
    r = ops_env.collect(TODAY)

    assert r["balances"] == {"deepseek": "¥31.15"}
    assert r["stale"] is True
    assert r["balance_asof"] == "2026-09-09"


# ── 3. 昨天的行 + 今天的行：必须取今天的 ─────────────────────────
def test_yesterday_then_today_takes_today(ops_env):
    """多天滚动追加的日志里，最后一条是今天的 —— 必须取今天的。"""
    ops_env.write_log([
        _balance_line(YESTERDAY, "deepseek", "9.87"),
        _balance_line(TODAY, "deepseek", "104.78"),
    ])
    r = ops_env.collect(TODAY)

    assert r["balances"] == {"deepseek": "¥104.78"}   # 不是 ¥9.87
    assert r["stale"] is False
    assert r["balance_asof"] == TODAY.isoformat()


def test_today_then_yesterday_takes_yesterday(ops_env):
    """「最近一次」按日志行序，不按日期大小：最后一条是谁就报谁。

    正常追加写的日志不会出现「旧的写在新的后面」；真出现了也以行序为准 ——
    按日期挑最大的，等于凭空造出一个日志里并不存在的"最新值"。
    """
    ops_env.write_log([
        _balance_line(TODAY, "deepseek", "104.78"),
        _balance_line(YESTERDAY, "deepseek", "9.87"),   # 后写 → 算"最近一次"
    ])
    r = ops_env.collect(TODAY)

    assert r["balances"] == {"deepseek": "¥9.87"}
    assert r["stale"] is True
    assert r["balance_asof"] == YESTERDAY.isoformat()


def test_older_provider_reported_with_conservative_asof(ops_env):
    """昨天 deepseek + 今天 doubao：两个都报，asof 取较旧的那个（09-15）。

    asof 取最旧而不是最新，是为了不给 deepseek 贴上 09-16 的日期 —— 那正是
    「拿旧数冒充当天」的事故形态。宁可少报新鲜度，也不多报。
    """
    ops_env.write_log([
        _balance_line(YESTERDAY, "deepseek", "9.87"),
        _balance_line(TODAY, "doubao", "52.10"),
    ])
    r = ops_env.collect(TODAY)

    assert r["balances"] == {"deepseek": "¥9.87", "doubao": "¥52.10"}
    assert r["balance_asof"] == YESTERDAY.isoformat()
    assert r["stale"] is True


# ── 4. 畸形行 / 缺字段的行：不能抛异常 ──────────────────────────
def test_malformed_lines_do_not_raise(ops_env):
    malformed = [
        "",                                                     # 空行
        "当前余额: ¥1.00（阈值 ¥10.00）",                        # 没有时间戳
        f"{TODAY.isoformat()} 08:05:02,123 [INFO] 当前余额",     # 没有冒号
        f"{TODAY.isoformat()} 08:05:02,123 [INFO] 当前余额: ",   # 没有金额
        f"{TODAY.isoformat()} 08:05:02,123 [INFO] [deepseek] 当前余额: ¥",  # 空金额
        f"{TODAY.isoformat()} 08:05:02,123 [INFO] [] 当前余额: ¥1.00（阈值 ¥10.00）",
        "2026-13-45 08:05:02,123 [INFO] [deepseek] 当前余额: ¥1.00（阈值 ¥10.00）",  # 非法日期
        "2026-09-1 08:05:02,123 [INFO] [deepseek] 当前余额: ¥1.00（阈值 ¥10.00）",   # 日期缺位
        f"{TODAY.isoformat()} 08:05:02,123 [INFO] [deepseek] 当前余额: ¥abc（阈值）",
        "这不是日志，只是一行普通文本",
    ]
    ops_env.write_log(malformed + [_balance_line(TODAY, "deepseek", "104.78")])
    r = ops_env.collect(TODAY)

    # 畸形行被跳过，正常行照常取到
    assert r["balances"] == {"deepseek": "¥104.78"}
    assert r["stale"] is False
    assert r["balance_asof"] == TODAY.isoformat()


def test_undated_balance_line_never_becomes_latest(ops_env):
    """行首没日期的余额行即使写在最后，也绝不能当「最近一次」——
    无法证明它是哪天写的，就不能拿来更新余额/asof。"""
    ops_env.write_log([
        _balance_line(TODAY, "deepseek", "104.78"),
        "当前余额: ¥1.00（阈值 ¥10.00）",     # 无日期，且写在最后
    ])
    r = ops_env.collect(TODAY)

    assert r["balances"] == {"deepseek": "¥104.78"}
    assert r["balance_asof"] == TODAY.isoformat()
    assert r["stale"] is False


def test_malformed_only_leaves_stale(ops_env):
    """整份日志都是畸形行时：不抛异常，且如实报「未取到」。"""
    ops_env.write_log([
        "当前余额: ¥1.00（阈值 ¥10.00）",
        f"{TODAY.isoformat()} 08:05:02,123 [INFO] 当前余额",
        "2026-13-45 08:05:02,123 [INFO] [deepseek] 当前余额: ¥1.00（阈值 ¥10.00）",
        "",
    ])
    r = ops_env.collect(TODAY)

    assert r["balances"] == {}
    assert r["stale"] is True
    assert r["balance_asof"] is None


# ── 5. 日志文件根本不存在 ───────────────────────────────────────
def test_no_log_file_at_all(ops_env):
    r = ops_env.collect(TODAY)   # 没调用 write_log，tmp 下没有日志文件

    assert r["checked"] is False
    assert r["balances"] == {}
    assert r["stale"] is True
    assert r["balance_asof"] is None


# ── 6. 欠费（arrears）口径保持原样：本次只修余额，不给它加日期过滤 ──
def test_arrears_not_filtered_by_date(ops_env):
    ops_env.write_log([
        f"{YESTERDAY.isoformat()} 08:05:02,123 [WARNING] [qwen] 可用性探测返回 400: Arrearage",
    ])
    r = ops_env.collect(TODAY)

    assert "qwen" in r["arrears"]

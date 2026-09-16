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

修复取向：**不依赖执行顺序**。行首日期 != 快照目标日期的余额行一律丢弃；
取不到当天的行就 `balances` 留空 + `stale=True`，让下游能说"当日未取到余额"。

⚠️ 本文件最关键的是第 2 节「只有昨天的行 → 必须报未取到」，那正是本次事故
的复现。若哪天有人为了"让报告好看"把旧数回填进来，这条必须红。反向地，
第 3 节「昨天的行 + 今天的行 → 必须取今天的」防止退回到"后写覆盖"的老口径。

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

    def collect(target=TODAY):
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


# ── 2. 只有昨天的余额行：必须报「当日未取到」（本次事故回归点）────
def test_only_yesterday_line_is_stale(ops_env):
    """事故复现：今天的行还没写出来，日志里只有昨天的 —— 不许拿昨天的数充数。"""
    ops_env.write_log([
        f"{YESTERDAY.isoformat()} 08:05:02,123 [INFO] ===== LLM 余额监控启动 =====",
        _balance_line(YESTERDAY, "deepseek", "9.87"),   # 09-16 日报里那个冒牌货
    ])
    r = ops_env.collect(TODAY)

    assert r["checked"] is True          # 日志文件是找到了的
    assert r["balances"] == {}           # 但当天没有余额 → 留空，不拿旧数
    assert r["stale"] is True
    assert r["balance_asof"] is None


def test_week_old_line_is_stale(ops_env):
    """不只是昨天：任何非当天的行都不许冒充当天的。"""
    ops_env.write_log([_balance_line(date(2026, 9, 9), "deepseek", "31.15")])
    r = ops_env.collect(TODAY)

    assert r["balances"] == {}
    assert r["stale"] is True
    assert r["balance_asof"] is None


# ── 3. 昨天的行 + 今天的行：必须取今天的 ─────────────────────────
def test_yesterday_then_today_takes_today(ops_env):
    """多天滚动追加的日志里，后写覆盖的旧口径会踩坑：今天的必须赢。"""
    ops_env.write_log([
        _balance_line(YESTERDAY, "deepseek", "9.87"),
        _balance_line(TODAY, "deepseek", "104.78"),
    ])
    r = ops_env.collect(TODAY)

    assert r["balances"] == {"deepseek": "¥104.78"}   # 不是 ¥9.87
    assert r["stale"] is False
    assert r["balance_asof"] == TODAY.isoformat()


def test_today_then_yesterday_still_takes_today(ops_env):
    """顺序反过来（今天的先写、昨天的后写）也不能被覆盖 —— 不再依赖写入顺序。"""
    ops_env.write_log([
        _balance_line(TODAY, "deepseek", "104.78"),
        _balance_line(YESTERDAY, "deepseek", "9.87"),
    ])
    r = ops_env.collect(TODAY)

    assert r["balances"] == {"deepseek": "¥104.78"}
    assert r["stale"] is False


def test_yesterday_provider_not_leaked_when_only_today_other_provider(ops_env):
    """昨天的 deepseek 不能因为『今天只有 doubao』就混进来。"""
    ops_env.write_log([
        _balance_line(YESTERDAY, "deepseek", "9.87"),
        _balance_line(TODAY, "doubao", "52.10"),
    ])
    r = ops_env.collect(TODAY)

    assert r["balances"] == {"doubao": "¥52.10"}
    assert "deepseek" not in r["balances"]
    assert r["stale"] is False


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


def test_malformed_only_leaves_stale(ops_env):
    """整份日志都是畸形行时：不抛异常，且如实报「当日未取到」。"""
    ops_env.write_log([
        "当前余额: ¥1.00（阈值 ¥10.00）",
        f"{TODAY.isoformat()} 08:05:02,123 [INFO] 当前余额",
        "2026-13-45 08:05:02,123 [INFO] [deepseek] 当前余额: ¥1.00（阈值 ¥10.00）",
        "",
    ])
    r = ops_env.collect(TODAY)

    assert r["balances"] == {}
    assert r["stale"] is True


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

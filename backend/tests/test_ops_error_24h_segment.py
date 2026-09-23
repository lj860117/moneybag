"""运维日报 24h 窗口失效回归测试（FIX 2026-09-22，Bug 5）
=========================================================

事故形态
--------
一批核心 cron 日志是「**每次运行先打一行带日期的标题，后面几十行全裸**」：

    health_check.log → `🔍 数据源健康巡检 (2026-09-21 01:20:00)`   （日期 + 时刻）
    ops_summary.log  → `🔍 运行态势快照 2026-09-21`                （只有日期）

而这些文件又是**按天 `>>` 追加**的 —— 只要今天追加过一行，文件 mtime 就刷新成
今天。于是旧段落里的裸行在 `_resolve_line_times()` 里只能「继承文件 mtime」，
24h cutoff 对它们**形同虚设**（等于 grep 全文）。

实测佐证（`data/ops/snapshot_2026-09-22.json`）：error_logs_24h 的第 1 条是
`/var/log/moneybag/ops_summary.log` 的 `❌ 周度自检: 距今 8 天（阈值 7 天）`
—— 今天已是 9 天，这行只可能是 9-21 写的，却出现在 9-22 的 24h 窗口里。
9-22 当日真实错误为 0，日报却报了「N 条错误 / N 个根因」。

本文件锁住四件事，缺一不可
--------------------------
1. **段头时刻生效**：旧段落（段头日期 = 3 天前）的裸行必须老化，即使文件
   mtime 是「刚刚」（模拟按天追加）。这是本次的主修复。
2. **不过度过滤**：今天的段落里有真错误 → 必须计入（假绿比虚高危险得多）。
3. **汇总行回声抑制**：同一段落里「明细行 + 汇总行」只算 1 条（同一次失败
   不该膨胀成两个根因）；但**段落里只剩汇总行**时必须照常计入 —— 汇总行
   常常是唯一的落盘证据，无条件丢掉就是静默丢告警（这条与
   `test_ops_error_dedup.py::test_nonzero_summary_line_still_counted` 同源）。
4. **兜底不回退**：整份文件既没有行内时间戳、也没有段头时，仍退回「继承文件
   mtime」的老口径 —— 老文件整体跳过、新文件整体计入，行为与改动前一致。

设计原则（与 test_ops_error_dedup.py 一致）：
  - **绝不复制实现里的正则/常量到本文件**。所有断言都真实调用
    `scripts/ops_summary.py` 的 `collect_error_logs()`，实现一改测试立刻能
    感知，不会退化成「改了实现还绿」的死测试。
  - 扫描范围通过 monkeypatch `_candidate_log_dirs()` + `DATA_DIR` /
    `_LEGACY_DATA_DIR` 钉死在 `tmp_path` 内，绝不碰生产 `data/`。
  - **不写死历史日期**：一律用「N 天前 / N 分钟前」相对时刻。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Sequence

import pytest

from scripts import ops_summary as ops


# ── 打桩日志的形态（与 datasource_health_check.main() / ops_summary.main() 对齐）──

DETAIL_ERR = "  ❌ [akshare_optimized] [降级]实时行情(优化): 异常: 调用超时（>30秒）"
DETAIL_OK = "  ✅ [akshare_optimized] [降级]实时行情(优化): 5564 行"
SUMMARY_ERR = "  ✅ 正常: 13    ❌ 异常: 1（其中需告警: 1）"
SUMMARY_OK = "  ✅ 正常: 14    ❌ 异常: 0（其中需告警: 0）"


def _days_ago(n: int) -> datetime:
    """返回「N 天前」这一时刻（必定落在 24h 窗口外，n >= 2 时）。"""
    return datetime.now() - timedelta(days=n)


def _minutes_ago(n: int) -> datetime:
    """返回「N 分钟前」这一时刻。

    ⚠️ 只适合**带时刻**的段头（`🔍 数据源健康巡检 (YYYY-MM-DD HH:MM:SS)`）
    或用来摆文件 mtime。段头**只有日期**的（快照 / 运维日报）请用
    `_today_anchor()`，原因见它的 docstring —— 刚过零点时本函数会跨到昨天，
    而「只有日期」的段头会被解析成那天的 00:00，于是「今天」变成「24h 前」。
    """
    return datetime.now() - timedelta(minutes=n)


def _today_anchor() -> datetime:
    """返回「今天」的时刻，专供**只有日期**的段头（`运行态势快照` / `AI 运维巡检日报`）。

    为什么不能直接用 `_minutes_ago(30)`：段头只有日期时，实现会把整段解析成
    那一天的 00:00:00。若测试恰好跑在本地时间刚过零点（比如 00:04），
    `_minutes_ago(30)` 的日期是**昨天** 23:34 → 段头解析成昨天 00:00 →
    距今 24h04m → 被**正确地**老化出窗口，于是「今天的段落必须计入」的断言
    反而红了。这不是实现的错（昨天 00:00 确实已满 24h），是打桩选错了时刻。

    用 `datetime.now()` 取当天即可：即使现在是 00:04，今天 00:00 也才过去
    4 分钟，稳稳落在 24h 窗口内。
    """
    return datetime.now()


def _health_header(when: datetime) -> str:
    """数据源健康巡检的段落头：`🔍 数据源健康巡检 (YYYY-MM-DD HH:MM:SS)`。"""
    return "🔍 数据源健康巡检 (" + when.strftime("%Y-%m-%d %H:%M:%S") + ")"


def _snapshot_header(when: datetime) -> str:
    """运行态势快照的段落头：`🔍 运行态势快照 YYYY-MM-DD`（只有日期）。"""
    return "🔍 运行态势快照 " + when.strftime("%Y-%m-%d")


def _health_segment(when: datetime, broken: bool, with_detail: bool = True) -> List[str]:
    """生成一段数据源健康巡检日志。

    Args:
        when: 段落起始时刻（写进段头）。
        broken: True 时该段落含 1 条 ❌ 明细行。
        with_detail: False 时只保留汇总行，明细行不落盘（模拟明细被截断/丢失）。
    """
    lines = [
        _health_header(when),
        "   检查 14 个数据源...",
        "  ✅ [akshare] 基金净值: 1096 行",
        "  ✅ [akshare] 恐贪指数: 值=52",
    ]
    if with_detail:
        lines.append(DETAIL_ERR if broken else DETAIL_OK)
    lines.append("  " + "=" * 40)
    lines.append(SUMMARY_ERR if broken else SUMMARY_OK)
    return lines


def _analyst_header(when: datetime) -> str:
    """AI 运维巡检日报的段落头：`🔍 AI 运维巡检日报 YYYY-MM-DD`（只有日期）。

    段头由 `ops_analyst.main()` 打印（FIX 2026-09-22 补）；ops_analyst.log 同样
    是按天 `>>` 追加的，正文行 `[OPS_ANALYST] …` 没有任何行内时间戳。
    """
    return "🔍 AI 运维巡检日报 " + when.strftime("%Y-%m-%d")


def _analyst_segment(when: datetime, broken: bool) -> List[str]:
    """生成一段 AI 运维巡检日报日志。

    Args:
        when: 段落起始时刻（写进段头）。
        broken: True 时含「LLM 调用异常 + Traceback 栈」。

    注：纯中文的 `[OPS_ANALYST] LLM 调用异常: …` 其实**不命中**任何错误关键字
    （keywords 是 Traceback / ERROR / ❌ / Exception / failed / Failed，全是英文
    或符号），真正会被日报计数的是随崩溃打到同一份日志（`2>&1`）的 Traceback
    栈 —— 所以打桩里两种行都保留，缺一就不能反映线上真实形态。
    """
    lines = [_analyst_header(when)]
    if broken:
        lines.append(
            "[OPS_ANALYST] LLM 调用异常: HTTPSConnectionPool(host='api.deepseek.com') "
            "Max retries exceeded"
        )
        lines.append("Traceback (most recent call last):")
        lines.append('  File "scripts/ops_analyst.py", line 465, in call_llm')
        lines.append(
            "requests.exceptions.ConnectionError: "
            "('Connection aborted.', ConnectionResetError(54))"
        )
    else:
        lines.append("[OPS_ANALYST] 报告已落盘: /opt/moneybag/data/ops/report_x.json（healthy）")
    return lines


def _snapshot_segment(when: datetime, stale_days: int) -> List[str]:
    """生成一段元巡检快照日志（含一条 ❌ 陈旧告警）。"""
    return [
        _snapshot_header(when),
        "  ✅ 数据源健康巡检: 距今 0 天（阈值 1 天）",
        "  ❌ 周度自检: 距今 " + str(stale_days) + " 天（阈值 7 天）",
        "  💾 磁盘剩余 42.1GB",
    ]


@pytest.fixture
def env(monkeypatch, tmp_path):
    """把 collect_error_logs 的扫描范围钉死在 tmp_path 内。"""
    real_tmp = tmp_path.resolve()

    def _under_tmp(path: Path) -> bool:
        try:
            return os.path.commonpath([str(real_tmp), str(path.resolve())]) == str(real_tmp)
        except (OSError, ValueError):  # pragma: no cover - 极端路径解析失败
            return False

    real_candidates = ops._candidate_log_dirs

    def _scoped() -> List[Path]:
        return [d for d in real_candidates() if _under_tmp(d)]

    monkeypatch.setattr(ops, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ops, "_LEGACY_DATA_DIR", tmp_path / "legacy")
    monkeypatch.setattr(ops, "_candidate_log_dirs", _scoped)
    return tmp_path


def _write(path: Path, lines: Sequence[str], mtime: datetime | None = None) -> Path:
    """写日志并把 mtime 摆成指定时刻（None = 保持系统当前时间）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if mtime is not None:
        ts = mtime.timestamp()
        os.utime(path, (ts, ts))
    return path


# ============================================================
# 1. 段头时刻生效：旧段落必须老化（本次主修复）
# ============================================================
def test_old_segment_aged_out_even_if_file_mtime_is_now(env):
    """3 天前那段巡检的 ❌ 不得计入 —— 即使文件 mtime 是「刚刚」（按天追加）。

    修复前：裸行继承文件 mtime = 现在 → 24h 窗口内 → 计入（虚高）。
    修复后：裸行继承段头时刻 = 3 天前 → 老化 → 不计入。
    """
    _write(
        env / "night_worker" / "health_check.log",
        _health_segment(_days_ago(3), broken=True)
        + _health_segment(_minutes_ago(30), broken=False),
        mtime=datetime.now(),  # 模拟今天又被 >> 追加过一行
    )

    result = ops.collect_error_logs()
    assert result["count_24h"] == 0, (
        f"3 天前的巡检错误仍被计入 24h（{result['count_24h']} 条）—— "
        f"段头时刻没生效，24h 窗口对按天追加的日志形同虚设：{result['files']}"
    )
    assert result["root_cause_count"] == 0


def test_old_snapshot_segment_aged_out(env):
    """`🔍 运行态势快照 YYYY-MM-DD` 这种「只有日期」的段头同样要能老化。

    线上那条误报就是 ops_summary.log 里的 `❌ 周度自检: 距今 8 天`。
    """
    _write(
        env / "logs" / "ops_summary.log",
        _snapshot_segment(_days_ago(3), 8) + _snapshot_segment(_today_anchor(), 9),
        mtime=datetime.now(),
    )

    result = ops.collect_error_logs()
    assert result["count_24h"] == 1, (
        f"应只剩今天那 1 条，实际 {result['count_24h']} 条：{result['files']}"
    )
    assert "距今 9 天" in result["files"][0]["line"], (
        f"保留下来的必须是今天那条（距今 9 天），实际：{result['files'][0]['line']}"
    )


# ============================================================
# 2. 不过度过滤：今天的真错误必须计入（假绿比虚高危险得多）
# ============================================================
def test_today_segment_real_error_still_counted(env):
    """今天的巡检段落里有 ❌ 明细 → **明细行**必须在 findings 里。

    ⚠️ 用「明细行在不在」判定而不是条数：条数口径本次正好也改了（回声抑制
    把汇总行去掉了），用条数断言会把「防假绿」和「回声抑制」两件事混在一起。
    这条锁的是「段头解析不得把新账算成旧账」—— 假绿比虚高危险得多。
    """
    _write(
        env / "night_worker" / "health_check.log",
        _health_segment(_minutes_ago(30), broken=True),
        mtime=datetime.now(),
    )

    result = ops.collect_error_logs()
    kept = [f["line"] for f in result["files"]]
    assert result["count_24h"] >= 1, (
        f"今天的真错误被误老化（{result['count_24h']} 条）—— 这是假绿，"
        f"比虚高危险得多：{result['files']}"
    )
    assert any("调用超时" in line for line in kept), (
        f"今天的 ❌ 明细行不见了 —— 段头解析把新账判成了旧账：{kept}"
    )


# ============================================================
# 3. 汇总行回声抑制（有条件，不是无条件丢弃）
# ============================================================
def test_summary_line_is_echo_when_detail_present(env):
    """同段落里「明细行 + 汇总行」只计 1 条 —— 一次失败不该膨胀成两个根因。"""
    _write(
        env / "night_worker" / "health_check.log",
        _health_segment(_minutes_ago(30), broken=True, with_detail=True),
        mtime=datetime.now(),
    )

    result = ops.collect_error_logs()
    assert result["count_24h"] == 1, (
        f"明细 + 汇总应只计 1 条，实际 {result['count_24h']} 条：{result['files']}"
    )
    assert result["root_cause_count"] == 1


def test_summary_line_kept_when_no_detail_in_segment(env):
    """段落里**只剩汇总行**（明细未落盘）→ 必须计入。

    ⚠️ 这条是回声抑制的反向闸门：汇总行常常是那次失败唯一的落盘证据，
    无条件丢掉就是静默丢告警 —— 与
    `test_ops_error_dedup.py::test_nonzero_summary_line_still_counted` 同源。
    """
    _write(
        env / "night_worker" / "health_check.log",
        _health_segment(_minutes_ago(30), broken=True, with_detail=False),
        mtime=datetime.now(),
    )

    result = ops.collect_error_logs()
    assert result["count_24h"] == 1, (
        f"段落里只剩汇总行时必须保留，实际 {result['count_24h']} 条 —— "
        f"丢掉就是静默丢告警：{result['files']}"
    )
    assert "异常: 1" in result["files"][0]["line"]


def test_ops_analyst_old_segment_aged_out(env):
    """ops_analyst.log（按天追加）里 3 天前的 Traceback 必须老化。

    段头 `🔍 AI 运维巡检日报 YYYY-MM-DD` 是 FIX 2026-09-22 补的（ops_analyst.main()
    打印）。没有它时，`[OPS_ANALYST] …` 这类裸行只能继承文件 mtime 而永不老化。
    """
    _write(
        env / "logs" / "ops_analyst.log",
        _analyst_segment(_days_ago(3), broken=True)
        + _analyst_segment(_today_anchor(), broken=False),
        mtime=datetime.now(),  # 模拟今天又被 >> 追加过
    )

    result = ops.collect_error_logs()
    assert result["count_24h"] == 0, (
        f"3 天前的 ops_analyst 错误仍被计入 24h（{result['count_24h']} 条）—— "
        f"段头没生效：{result['files']}"
    )


def test_ops_analyst_today_traceback_still_counted(env):
    """今天的 ops_analyst 段落里崩溃 → Traceback 必须计入（防假绿）。"""
    _write(
        env / "logs" / "ops_analyst.log",
        _analyst_segment(_today_anchor(), broken=True),
        mtime=datetime.now(),
    )

    result = ops.collect_error_logs()
    kept = [f["line"] for f in result["files"]]
    assert result["count_24h"] >= 1, (
        f"今天的 Traceback 被误老化（{result['count_24h']} 条）—— 假绿：{result['files']}"
    )
    assert any("Traceback" in line for line in kept), (
        f"今天的 Traceback 行不见了：{kept}"
    )


# ============================================================
# 4. 兜底不回退：没有段头也没有行内时间戳时，仍按文件 mtime 判
# ============================================================
def test_no_header_no_timestamp_falls_back_to_mtime_fresh(env):
    """整份文件都没有时间戳/段头、且 mtime 很新 → 按老口径整体计入。"""
    _write(
        env / "night_worker" / "plain.log",
        ["❌ Something went wrong without any header"],
        mtime=_minutes_ago(10),
    )

    result = ops.collect_error_logs()
    assert result["count_24h"] == 1, (
        f"兜底口径回退了：无段头文件按 mtime 判时应计入，实际 {result['count_24h']} 条"
    )


def test_no_header_no_timestamp_falls_back_to_mtime_stale(env):
    """整份文件都没有时间戳/段头、且 mtime 是 3 天前 → 按老口径整体跳过。"""
    _write(
        env / "night_worker" / "plain_old.log",
        ["❌ Something went wrong without any header"],
        mtime=_days_ago(3),
    )

    result = ops.collect_error_logs()
    assert result["count_24h"] == 0, (
        f"兜底口径回退了：老文件按 mtime 判时应跳过，实际 {result['count_24h']} 条"
    )


# ============================================================
# 5. 纯中文错误行：靠**生产侧**补 ❌，而不是加宽关键字表
# ============================================================
def test_ops_analyst_err_helper_prefixes_error_mark(capsys):
    """`ops_analyst._err()` 必须给错误行补 ❌ 前缀（FIX 2026-09-22）。

    背景：ops_summary 的错误关键字表是
    `("Traceback", "ERROR", "❌", "Exception", "failed", "Failed")` —— 全是英文
    或符号。ops_analyst 的错误行原本是纯中文（`[OPS_ANALYST] LLM 调用异常: …`），
    一个字都命中不了 → 真实错误在 24h 统计里隐形。

    修法选**生产侧**补前缀，而不是加宽关键字表：关键字表一宽，历史日志里所有
    含该词的正常行都会被回溯算成错误，存量数据无法收敛。所以这里锁住的是
    「生产者必须自己带 ❌」，关键字表保持不动。
    """
    from scripts import ops_analyst

    ops_analyst._err("[OPS_ANALYST] LLM 调用异常: 连接被拒绝")
    out = capsys.readouterr().out.strip()
    assert out.startswith("❌ "), f"_err() 未补 ❌ 前缀，纯中文错误行会漏报：{out!r}"
    assert "LLM 调用异常" in out


def test_chinese_error_line_only_counted_with_prefix(env):
    """同一条纯中文错误行：不带 ❌ → 不计入（盲区实证）；带 ❌ → 计入。

    前半段是**已知盲区的实证**，不是期望行为 —— 正因如此才要求生产侧补 ❌
    （见上一条测试）。若将来有人加宽关键字表让前半段翻转成「计入」，那是一次
    需要重新评估全量历史日志的 deliberate 决策，本测试会红，请连同
    `ops_summary.collect_error_logs` 的 `keywords` 一起评审，不要直接删。
    """
    body = "[OPS_ANALYST] 推送服务导入失败: No module named 'services.wxwork_push'"

    _write(
        env / "logs" / "ops_analyst.log",
        [_analyst_header(_today_anchor()), body],
        mtime=datetime.now(),
    )
    assert ops.collect_error_logs()["count_24h"] == 0, (
        "纯中文错误行竟然被计入了 —— 关键字表被加宽过，请确认是否经过评审"
    )

    _write(
        env / "logs" / "ops_analyst.log",
        [_analyst_header(_today_anchor()), "❌ " + body],
        mtime=datetime.now(),
    )
    result = ops.collect_error_logs()
    assert result["count_24h"] == 1, (
        f"生产侧补了 ❌ 就必须计入，实际 {result['count_24h']} 条：{result['files']}"
    )

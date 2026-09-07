"""
运维日报「告警计数虚高」根因回归测试
====================================

事故回顾（2026-09-07 ~ 09-08）：
    night_worker 的 stderr 被同时 tee 进 `data/night_worker/2026-09-07.log`
    和 `cron.log`，两份日志内容逐字相同；legacy 目录 `backend/data/
    night_worker/` 里还躺着同款历史错误。旧 `collect_error_logs()` 按
    「文件 × 行」累加，一条真实错误被数成 2~3 条 —— 15 条 ALLOC_PCTS 被报成
    30 条、24h 计数虚高到 35，直接把日报顶到 critical（阈值 ≥10），而真实
    独立故障只有 4 个。

本文件锁住两件事，缺一不可：

1. **去重生效**：同一批错误出现在多份日志，只计 1 条（防虚高刷屏）
2. **不过度去重**：不同错误内容 / 不同 profile 的同型错误必须各自计数
   （⚠️ 这条更重要 —— 只做去重不做区分，等于"为了把告警变绿而把真故障
   洗掉"，比虚高危险得多）

设计原则（与 test_night_worker_regressions.py 一致）：
  - **绝不复制实现里的正则/常量到本文件**。所有断言都真实调用
    `scripts/ops_summary.py` 的 `collect_error_logs()` 与 `_error_fingerprint()`，
    实现一改测试立刻能感知，不会退化成"改了实现还绿"的死测试。
  - 扫描范围通过 monkeypatch `_candidate_log_dirs()` + `DATA_DIR` /
    `_LEGACY_DATA_DIR` 钉死在 `tmp_path` 内，绝不碰生产 `data/`。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import List, Sequence

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
OPS_SUMMARY_PATH = BACKEND_DIR / "scripts" / "ops_summary.py"

# 被测模块按路径加载（scripts/ 不是包，普通 import 需要把 scripts/ 塞进
# sys.path，会污染整场 pytest 的模块解析）。加载一次后缓存复用。
_OPS_MODULE: ModuleType | None = None


def _load_ops_summary() -> ModuleType:
    """以文件路径方式加载 scripts/ops_summary.py（进程内只加载一次）。"""
    global _OPS_MODULE
    if _OPS_MODULE is None:
        spec = importlib.util.spec_from_file_location(
            "_mb_ops_summary_sut", OPS_SUMMARY_PATH
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["_mb_ops_summary_sut"] = module
        spec.loader.exec_module(module)
        _OPS_MODULE = module
    return _OPS_MODULE


class _OpsEnv:
    """隔离后的被测环境句柄。"""

    def __init__(self, mod: ModuleType, tmp: Path) -> None:
        self.mod = mod
        self.tmp = tmp


@pytest.fixture
def env_factory(monkeypatch, tmp_path):
    """返回工厂：`env_factory(legacy_same=True/False) -> _OpsEnv`。

    Args:
        legacy_same: True 时让 `_LEGACY_DATA_DIR` 与 `DATA_DIR` 指向同一目录，
            用于模拟线上「新老两处 data 目录其实是同一个」的情形。

    做两件隔离：
      1. 把 `DATA_DIR` / `_LEGACY_DATA_DIR` 指向 tmp_path（模块级名字，
         `collect_error_logs` 运行时按全局名查找，monkeypatch 可生效）
      2. 把 `_candidate_log_dirs()` 换成"只保留 tmp_path 子树内目录"的版本 ——
         仍然调用真实实现拼出候选列表，仅剔除线上的 `/var/log/moneybag`，
         否则真实线上日志会混进断言，本地与服务器结果不一致。
    """
    real_tmp = tmp_path.resolve()

    def _is_under_tmp(path: Path) -> bool:
        try:
            resolved = path.resolve()
        except OSError:  # pragma: no cover - 极端路径解析失败，按不在 tmp 处理
            return False
        try:
            return os.path.commonpath([str(real_tmp), str(resolved)]) == str(real_tmp)
        except ValueError:
            return False

    def _make(legacy_same: bool = False) -> _OpsEnv:
        mod = _load_ops_summary()
        monkeypatch.setattr(mod, "DATA_DIR", tmp_path)
        monkeypatch.setattr(
            mod, "_LEGACY_DATA_DIR", tmp_path if legacy_same else tmp_path / "legacy"
        )

        real_candidates = mod._candidate_log_dirs

        def _scoped_candidates() -> List[Path]:
            return [d for d in real_candidates() if _is_under_tmp(d)]

        monkeypatch.setattr(mod, "_candidate_log_dirs", _scoped_candidates)
        return _OpsEnv(mod=mod, tmp=tmp_path)

    return _make


def _write(path: Path, lines: Sequence[str]) -> Path:
    """把日志行写进临时文件（自动建父目录），返回文件路径。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ============================================================
# 1. 去重生效：同一批错误 tee 进两份日志 → 只计 1 条
# ============================================================
def test_tee_duplicated_logs_counted_once(env_factory):
    """两份日志内容相同（时间戳不同）→ count_24h 必须等于独立错误数而非行数。

    事故现场：15 条 ALLOC_PCTS 被 tee 成两份后报成 30 条。
    """
    env = env_factory()
    bodies = [f"❌ ALLOC_PCTS 未定义：诊断 #{i:02d} 失败" for i in range(15)]

    night_log = _write(
        env.tmp / "night_worker" / "2026-09-07.log",
        [f"[{i:02d}:00:01] {b}" for i, b in enumerate(bodies)],
    )
    cron_log = _write(
        env.tmp / "logs" / "cron.log",
        [f"[2026-09-07 {i:02d}:00:01] {b}" for i, b in enumerate(bodies)],
    )
    assert night_log.exists() and cron_log.exists()

    result = env.mod.collect_error_logs()
    assert result["count_24h"] == 15, (
        f"15 条错误 tee 成两份被数成 {result['count_24h']} 条 —— "
        f"去重退化，日报会重新虚高到 critical"
    )
    assert len(result["files"]) == 15


def test_duplicate_occurrences_recorded_in_also_in(env_factory):
    """重复出处必须记进 also_in（保留"这条错误还出现在哪"的可追溯性）。

    只记**别的文件**：同一个文件内重复出现不该污染 also_in。
    """
    env = env_factory()
    lines = ["[03:00:00] ❌ 错误甲", "[03:00:01] ❌ 错误乙"]

    night_log = _write(env.tmp / "night_worker" / "2026-09-07.log", lines)
    cron_log = _write(env.tmp / "logs" / "cron.log", lines)

    result = env.mod.collect_error_logs()
    assert result["count_24h"] == 2

    expected = {str(night_log), str(cron_log)}
    for finding in result["files"]:
        also_in = finding.get("also_in", [])
        assert len(also_in) == 1, f"also_in 应为 1 个重复出处，实际 {also_in}"
        assert also_in[0] != finding["file"], "also_in 不应包含主文件自身"
        assert {finding["file"], also_in[0]} == expected


# ============================================================
# 2. 目录去重：DATA_DIR 与 _LEGACY_DATA_DIR 同一处时不翻倍
# ============================================================
def test_same_data_dir_does_not_double_count(env_factory):
    """新老 data 目录 resolve 到同一路径时，同一份日志只能被扫一遍。"""
    env = env_factory(legacy_same=True)
    _write(
        env.tmp / "night_worker" / "2026-09-07.log",
        ["[01:00:00] ❌ 错误甲", "[01:00:01] ❌ 错误乙"],
    )

    result = env.mod.collect_error_logs()
    assert result["count_24h"] == 2, (
        f"DATA_DIR 与 _LEGACY_DATA_DIR 指向同一目录时应计 2 条，"
        f"实际 {result['count_24h']} 条 —— 目录去重失效"
    )


# ============================================================
# 3. 不过度去重（最重要的一条）
# ============================================================
def test_distinct_errors_are_not_merged(env_factory):
    """内容不同的错误必须各自计数，哪怕它们被 tee 进多份日志。

    ⚠️ 这是防"为了把告警变绿而把真故障洗掉"的护栏。若有人把去重写成
    「取前 N 个字」「只留关键字」这类激进口径，这条会第一个红。
    """
    env = env_factory()
    lines = [
        "[03:00:00] ❌ 保守型/fund ALLOC_PCTS missing",
        "[03:00:01] ❌ 保守型/stock ALLOC_PCTS missing",   # 同 profile 不同资产
        "[激进型] ❌ 触发止损",                              # 行首业务标签不同
        "[保守型] ❌ 触发止损",
        "[03:00:02] ❌ 数据源 基金净值 不可用",               # 完全不同的错误
    ]
    _write(env.tmp / "night_worker" / "2026-09-07.log", lines)
    _write(env.tmp / "logs" / "cron.log", lines)  # 同样 tee 一份

    result = env.mod.collect_error_logs()
    assert result["count_24h"] == 5, (
        f"5 条互不相同的错误被合并成 {result['count_24h']} 条 —— "
        f"过度去重，真故障会被洗掉"
    )


def test_business_bracket_tags_are_preserved_in_fingerprint(env_factory):
    """行首方括号是业务标签（非时间戳）时不得被剥掉。

    若一律剥 `[xxx]`，`[保守型] ❌ X` 与 `[激进型] ❌ X` 会塌缩成同一指纹，
    五档风险的同类故障只剩 1 条 —— 典型过度去重。
    """
    env = env_factory()
    fp = env.mod._error_fingerprint

    assert fp("[保守型] ❌ ALLOC_PCTS missing") != fp("[激进型] ❌ ALLOC_PCTS missing")
    assert fp("[LeiJiang] ❌ 诊断失败") == "[LeiJiang] ❌ 诊断失败"


# ============================================================
# 4. 正常容错重试仍被排除（去重不能顺手把这条老规则吃掉）
# ============================================================
def test_retry_lines_still_excluded(env_factory):
    """`failed ... timed out, retry in Xs` 属正常容错，去重后仍不得计入。"""
    env = env_factory()
    _write(
        env.tmp / "night_worker" / "2026-09-07.log",
        [
            "[03:00:00] fetch attempt 2 failed: timed out, retry in 5s",
            "[03:00:01] fetch attempt 3 failed: 超时，准备重试",
            "[03:00:02] ❌ 真正的错误",
        ],
    )

    result = env.mod.collect_error_logs()
    assert result["count_24h"] == 1, (
        f"容错重试被计入错误（{result['count_24h']} 条）—— 去重改动吃掉了重试排除规则"
    )
    assert result["files"][0]["keyword"] == "❌"


# ============================================================
# 5. _error_fingerprint 边界 case
# ============================================================
@pytest.mark.parametrize(
    "line, expected",
    [
        ("", ""),                                        # 空串
        ("   ", ""),                                     # 纯空白
        ("[]", "[]"),                                    # 纯空方括号：必须有限步返回
        ("[a][b] 消息", "[a][b] 消息"),                    # 非时间戳方括号：原样保留
        ("[02:57:40] ❌ X", "❌ X"),                       # 真实日志格式 [HH:MM:SS]
        ("[2026-09-07 03:15:22] ❌ X", "❌ X"),            # 日期 + 时间
        ("[2026-09-07T03:15:22] [ERROR] ❌ X", "❌ X"),    # 时间戳 + 日志级别两层
        ("[ERROR] ❌ X", "❌ X"),                          # 只有级别
        ("[保守型] ❌ X", "[保守型] ❌ X"),                 # 业务标签保留
        ("普通一行没有方括号", "普通一行没有方括号"),          # 无方括号
        ("[02:57:40]", "[02:57:40]"),                     # 整行只有时间戳
    ],
)
def test_error_fingerprint_cases(env_factory, line, expected):
    """指纹函数的边界行为（含空串 / 纯 [] / 多块方括号 / 业务标签）。"""
    env = env_factory()
    assert env.mod._error_fingerprint(line) == expected


def test_error_fingerprint_terminates_on_degenerate_input(env_factory):
    """退化输入必须在有限步内返回，不得死循环（历史实现是 `while True`）。"""
    env = env_factory()
    fp = env.mod._error_fingerprint

    assert fp("[]" * 50) == "[]" * 50
    assert fp("[1]" * 50 + "tail").endswith("tail")


def test_error_fingerprint_dedups_tee_but_keeps_profiles(env_factory):
    """同一条错误不同时间戳 → 同指纹；不同 profile/资产 → 不同指纹。"""
    env = env_factory()
    fp = env.mod._error_fingerprint

    assert fp("[03:00:01] ❌ 保守型/fund ALLOC_PCTS missing") == fp(
        "[2026-09-07 03:00:02] ❌ 保守型/fund ALLOC_PCTS missing"
    )
    assert fp("❌ 保守型/fund X") != fp("❌ 保守型/stock X")
    assert fp("❌ 保守型/fund X") != fp("❌ 激进型/fund X")

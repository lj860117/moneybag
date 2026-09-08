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
        legacy_path: 显式指定 `_LEGACY_DATA_DIR`，优先级高于 legacy_same。
            用于构造「字符串不同但 resolve 后相同」的路径（`tmp/.`），
            那是唯一能把「目录去重」和「指纹去重」区分开的场景。

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

    def _make(legacy_same: bool = False, legacy_path: Path | None = None) -> _OpsEnv:
        mod = _load_ops_summary()
        if legacy_path is not None:
            legacy = legacy_path
        else:
            legacy = tmp_path if legacy_same else tmp_path / "legacy"
        monkeypatch.setattr(mod, "DATA_DIR", tmp_path)
        monkeypatch.setattr(mod, "_LEGACY_DATA_DIR", legacy)

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


def test_also_in_never_contains_the_source_file_itself(env_factory):
    """同一文件内同一错误重复出现时，also_in 不得把主文件自己记进去。

    对应实现里的守卫 `str(f) != _dup.get("file")`（ops_summary.py:350）。缺了它，
    同一文件内第 2 次命中同一指纹会把主文件塞进 also_in —— 日报会显示"这条错误
    还出现在它自己所在的文件"，纯噪音，且会让人误判成跨文件重复而放松警惕。

    本条是变异测试补出来的：去掉上述守卫后，原有用例全绿（原有用例只有
    "跨文件"场景），只有本条会红。
    """
    env = env_factory()
    _write(
        env.tmp / "night_worker" / "2026-09-07.log",
        ["[03:00:00] ❌ 错误甲", "[03:00:01] ❌ 错误甲"],  # 同文件内重复，仅时间戳不同
    )

    result = env.mod.collect_error_logs()
    assert result["count_24h"] == 1, (
        f"同一文件内 2 次同一错误应只计 1 条，实际 {result['count_24h']} 条"
    )
    also_in = result["files"][0].get("also_in", [])
    assert also_in == [], f"also_in 不应包含主文件自身，实际 {also_in}"


# ============================================================
# 2. 目录去重：DATA_DIR 与 _LEGACY_DATA_DIR 同一处时不翻倍
# ============================================================
def test_same_data_dir_keeps_total_count_correct(env_factory):
    """新老 data 目录指向同一处时，最终计数不受影响（端到端口径）。

    ⚠️ 这条**不足以证明目录去重生效**：去掉目录去重后它依然会绿 —— 因为
    指纹去重会兜住。它验证的是「最终计数对」，真正的目录去重隔离验证见
    下一条 `test_directory_dedup_prevents_rescan_of_same_dir`。
    """
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


def test_directory_dedup_prevents_rescan_of_same_dir(env_factory, tmp_path):
    """目录去重生效的**隔离**验证：同一目录被扫两遍会污染 also_in。

    构造法：让 `_LEGACY_DATA_DIR` 取 `tmp/legacy/..` —— pathlib 会保留 `..`
    （但会吞掉 `.`），所以 `rglob` 出来的路径字符串是
    `tmp/legacy/../night_worker/a.log`，与 `tmp/night_worker/a.log` 不同；
    而 `resolve()` 后两者完全相同。于是：

      - 目录去重**在** → 该目录只扫一遍 → 1 条 finding，`also_in == []`
      - 目录去重**不在** → 扫两遍，两条路径字符串不同 → 命中指纹去重分支，
        计数仍是 1，但 `also_in` 会被塞进那条 `tmp/legacy/../...` 路径

    所以 `also_in == []` 是唯一能把「目录去重」与「指纹去重」区分开的断言：
    去掉目录去重后本条必红，而上面那条端到端用例不会红（它被指纹去重兜住）。
    """
    # legacy/ 必须真实存在，否则 `legacy/../night_worker` 会被 exists() 挡掉，
    # 那样本条就失去区分能力了
    (tmp_path / "legacy").mkdir(parents=True, exist_ok=True)
    env = env_factory(legacy_path=tmp_path / "legacy" / "..")
    _write(env.tmp / "night_worker" / "2026-09-07.log", ["[01:00:00] ❌ 错误甲"])

    result = env.mod.collect_error_logs()
    assert result["count_24h"] == 1
    also_in = result["files"][0].get("also_in", [])
    assert also_in == [], (
        f"目录去重生效时同目录不应被扫两遍，also_in 应为空，实际 {also_in}"
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
# 4b. 零值健康汇总行排除（P1）
# ============================================================
def test_zero_value_summary_line_excluded(env_factory):
    """`✅ 正常: 13    ❌ 异常: 0` 这类零值汇总行不是错误，不得计入。

    它含 `❌` 所以会被关键字命中，但值是 0 —— 健康证明，不是告警。
    """
    env = env_factory()
    _write(
        env.tmp / "night_worker" / "health_check.log",
        ["✅ 正常: 13    ❌ 异常: 0"],
    )

    result = env.mod.collect_error_logs()
    assert result["count_24h"] == 0, (
        f"零值健康汇总行被算成错误（{result['count_24h']} 条）"
    )


def test_nonzero_summary_line_still_counted(env_factory):
    """`❌ 异常: 15` 是真报警，必须照常计入。

    ⚠️ 这是 P1 的护栏：排除规则只认「值为 0」这一种形态，一旦有人把它
    做成通用的汇总行排除，这条会红。
    """
    env = env_factory()
    _write(
        env.tmp / "night_worker" / "health_check.log",
        ["✅ 正常: 3    ❌ 异常: 15"],
    )

    result = env.mod.collect_error_logs()
    assert result["count_24h"] == 1, (
        f"非零汇总行是真报警，必须计入，实际 {result['count_24h']} 条"
    )


# ============================================================
# 4c. 独立根因数（P0）
# ============================================================
def test_fanout_collapses_to_one_root_cause(env_factory):
    """5 档风险 × 3 类资产 = 15 条同源 ALLOC_PCTS → 15 条错误 / 1 个根因。

    这是日报改双指标的核心场景：一个 `ALLOC_PCTS` NameError 在 15 个
    (档位, 资产) 组合上各报一次，按条数判会直接顶到 critical（15 ≥ 10），
    但真实故障只有 1 个。
    """
    env = env_factory()
    profiles = ("保守型", "稳健型", "平衡型", "进取型", "激进型")
    assets = ("fund", "stock", "mixed")
    lines = [
        f"[03:00:{i:02d}] ❌ {p}/{a}: name 'ALLOC_PCTS' is not defined"
        for i, (p, a) in enumerate((p, a) for p in profiles for a in assets)
    ]
    _write(env.tmp / "night_worker" / "2026-09-07.log", lines)

    result = env.mod.collect_error_logs()
    assert result["count_24h"] == 15, (
        f"15 个 (档位,资产) 组合应各计 1 条，实际 {result['count_24h']} 条"
    )
    assert result["root_cause_count"] == 1, (
        f"15 条同源 ALLOC_PCTS 应收敛成 1 个根因，实际 {result['root_cause_count']} 个"
    )


def test_distinct_root_causes_are_not_merged(env_factory):
    """不同根因不得被合并 —— 防根因聚合写激进（比虚高更危险的方向）。

    `name 'ALLOC_PCTS' is not defined` 与 `name '_P' is not defined` 是
    两个不同的故障（两个不同的改名漏改），哪怕它们都发生在同一个
    (档位, 资产) 组合上，根因数也必须是 2。
    """
    env = env_factory()
    _write(
        env.tmp / "night_worker" / "2026-09-07.log",
        [
            "[03:00:00] ❌ 保守型/fund: name 'ALLOC_PCTS' is not defined",
            "[03:00:01] ❌ 保守型/fund: name '_P' is not defined",
        ],
    )

    result = env.mod.collect_error_logs()
    assert result["count_24h"] == 2
    assert result["root_cause_count"] == 2, (
        f"两个不同变量名是两个根因，实际被合并成 {result['root_cause_count']} 个"
    )


def test_root_cause_keeps_module_tags_and_function_names(env_factory):
    """根因聚合**严禁**抹掉模块标签与函数名 —— 过度去重的护栏。

    `get_fund_daily_hist` 与 `get_stock_daily_hist` 是两条不同的数据链路，
    即便资产词归一化也要保持不同；`STOCK_PROVIDER` / `TUSHARE` 这类模块标签
    里的资产词不能被吃掉。
    """
    env = env_factory()
    rcf = env.mod._root_cause_fingerprint
    fp = env.mod._error_fingerprint

    assert rcf(fp("[STOCK_PROVIDER] ❌ 拉取出错")).count("STOCK_PROVIDER") == 1
    assert rcf(fp("[TUSHARE] ❌ 拉取失败")).count("TUSHARE") == 1
    assert rcf(fp("get_fund_daily_hist(#) 失败")) != rcf(fp("get_stock_daily_hist(#) 失败"))


def test_root_cause_normalizes_digits(env_factory):
    """行内数字归一化：`重试 3 次` 与 `重试 5 次` 属同一个根因。"""
    env = env_factory()
    rcf = env.mod._root_cause_fingerprint
    fp = env.mod._error_fingerprint

    assert rcf(fp("❌ 拉取失败，重试 3 次")) == rcf(fp("❌ 拉取失败，重试 5 次"))


def test_findings_carry_the_error_line(env_factory):
    """findings 必须带原文（截断 200 字），否则日报说不出「是什么错误」。

    只存 `{file, keyword}` 时，事后既无法人工审计去重对不对，也无法看出
    报错内容 —— 只能报一个数字。
    """
    env = env_factory()
    _write(
        env.tmp / "night_worker" / "2026-09-07.log",
        ["[03:00:00] ❌ 保守型/fund: name 'ALLOC_PCTS' is not defined"],
    )

    result = env.mod.collect_error_logs()
    entry = result["files"][0]
    assert "ALLOC_PCTS" in (entry.get("line") or ""), (
        f"findings 缺少错误原文，无法审计/定位：{entry}"
    )


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
        ("[1/4] ❌ 拉取失败", "[1/4] ❌ 拉取失败"),          # 进度计数：不是时间戳
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


def test_progress_counter_brackets_are_not_stripped(env_factory):
    """`[1/4]` / `[2/4]` 进度计数不得被当成时间戳剥掉（P2）。

    真实 cron.log 里有 `[1/4]…[4/4] 拉全量基金名单...` 这类进度行。若被误判
    成时间戳，`[1/4] ❌ 拉取失败` 与 `[2/4] ❌ 拉取失败` 会塌缩成同一个指纹
    —— 静默丢告警（告警变绿而故障还在），比虚高危险得多。

    ⚠️ 本条正是「以数字开头 + 只含数字与分隔符」那种宽松时间戳口径的护栏。
    """
    env = env_factory()
    fp = env.mod._error_fingerprint

    assert fp("[1/4] ❌ 拉取失败") == "[1/4] ❌ 拉取失败"
    assert fp("[2/4] ❌ 拉取失败") == "[2/4] ❌ 拉取失败"
    assert fp("[1/4] ❌ 拉取失败") != fp("[2/4] ❌ 拉取失败"), (
        "进度计数被当成时间戳剥掉了 —— 不同步骤的报错会塌缩成一条，静默丢告警"
    )
    # 真时间戳仍必须被剥（不能为了修 [1/4] 把时间戳一起废掉）
    assert fp("[02:57:40] ❌ 拉取失败") == fp("[02:57:41] ❌ 拉取失败")


def test_prefix_stripping_is_bounded(env_factory):
    """前缀剥离必须有界 —— 这条能区分「有界循环」与 `while True`。

    构造连续 `_MAX_PREFIX_BRACKETS + 2` 个真时间戳方括号 + 尾巴：
      - 有界循环 → 只剥掉 `_MAX_PREFIX_BRACKETS` 层 → 剩 2 个方括号
      - `while True` → 全部剥光 → 只剩 tail

    上界值从模块常量读取，不硬编码 6，改常量不会让本条假红。
    """
    env = env_factory()
    fp = env.mod._error_fingerprint
    limit = env.mod._MAX_PREFIX_BRACKETS

    out = fp("[01:00]" * (limit + 2) + "tail")
    assert out.endswith("tail"), f"尾巴被吃掉了：{out!r}"
    assert out.count("[") == 2, (
        f"应只剥掉 {limit} 层（剩 2 个方括号），实际剩 {out.count('[')} 个：{out!r}"
    )

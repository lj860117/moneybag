"""
晨报缓存文件名大小写归一化 —— 行为级守卫
=========================================

背景（2026-09-17 生产事故）：
    data/briefings/ 里同一个用户同一天存在两份缓存：
        leijiang_20260917.json   07:50  ← cron briefing_hallucination_check.py
                                         （--user 默认小写 leijiang）
        LeiJiang_20260917.json   13:05  ← API GET /api/steward/briefing?userId=LeiJiang
    Linux 文件名大小写敏感，于是 **cron 预生成的缓存从来没被 API 命中过**，
    每次访问都现算（冷态 59s / 热态 3s），一天还堆出多份只有大小写不同的重复文件。

修法：steward.brief_cache_key() 归一化（strip + lower），读写作废各自拼文件名的做法。

本文件的断言刻意用「现算路径有没有被调用」来证明命中/未命中，而不是只看返回内容：
    — 看返回内容分不出「命中缓存」和「现算出一个长得一样的结果」，那是假绿。

故障注入：把 brief_cache_key() 里的 .lower() 去掉，本文件用例必须变红。
"""
import json
import os
import sys
import tempfile
import time
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import services.steward as steward_module


def _fs_is_case_sensitive() -> bool:
    """本机文件系统是否大小写敏感（Linux ext4 是，macOS 默认 APFS 不是）。

    大小写不敏感的文件系统里 "leijiang_X.json" 和 "LeiJiang_X.json" 是同一个
    文件，"两份缓存并存"这个事故现场根本无法复现。相关用例必须 skip 而不是
    假装通过 —— 否则就是一个恒绿的空转守卫，比没有更糟。
    生产是 Linux（大小写敏感），这些用例在 CI/生产机上会真正生效。
    """
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "case_test.tmp").write_text("x", encoding="utf-8")
        return not (Path(d) / "CASE_TEST.TMP").exists()


FS_CASE_SENSITIVE = _fs_is_case_sensitive()

requires_case_sensitive_fs = pytest.mark.skipif(
    not FS_CASE_SENSITIVE,
    reason="需要大小写敏感的文件系统（生产为 Linux）；本机大小写不敏感，"
           "两种大小写是同一个文件，事故现场无法复现",
)


# ------------------------------------------------------------------
# fixtures / 工具
# ------------------------------------------------------------------

@pytest.fixture
def brief_dir(tmp_path, monkeypatch):
    """把 steward 的缓存目录指向临时目录，避免测试写生产 data/briefings/。"""
    d = tmp_path / "briefings"
    d.mkdir()
    monkeypatch.setattr(steward_module, "_BRIEF_DIR", d)
    return d


class _FakeRunner:
    """记录 run() 被调用次数的假 PipelineRunner。"""

    def __init__(self):
        self.calls = []

    def run(self, pipeline_name, ctx):
        self.calls.append(pipeline_name)
        return ctx


def _make_steward(monkeypatch):
    """构造一个不拉起真实依赖的 Steward（只挂假 runner）。

    不用 Steward()：它的 __init__ 会拉起 PipelineRunner + ModuleRegistry 等重依赖。
    """
    steward = steward_module.Steward.__new__(steward_module.Steward)
    runner = _FakeRunner()
    steward.runner = runner

    # 切断现算路径上所有可能打网络的调用，保证用例离线可跑、秒级完成
    classify_calls = []

    def _fake_classify():
        classify_calls.append(1)
        return {"regime": "oscillating", "confidence": 50, "description": "", "params": {}}

    monkeypatch.setattr(steward_module, "classify_regime", _fake_classify)
    monkeypatch.setattr(steward_module, "_generate_one_line", lambda ctx: "stub one line")
    monkeypatch.setitem(
        sys.modules,
        "services.geopolitical",
        types.SimpleNamespace(get_geopolitical_events=lambda: {"available": False}),
    )
    return steward, runner, classify_calls


def _write_cache(brief_dir, filename, payload=None, age_hours=0.0):
    """写一个缓存文件，并可按 age_hours 把 mtime 往回拨（模拟过期）。"""
    fp = brief_dir / filename
    data = payload if payload is not None else {
        "regime": "oscillating",
        "one_line": "cron 预生成的晨报",
    }
    fp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    if age_hours:
        past = time.time() - age_hours * 3600
        os.utime(fp, (past, past))
    return fp


def _today():
    return datetime.now().strftime("%Y%m%d")


# ------------------------------------------------------------------
# 1. 归一化键本身
# ------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("LeiJiang", "leijiang"),
    ("leijiang", "leijiang"),
    ("LEIJIANG", "leijiang"),
    ("  LeiJiang  ", "leijiang"),
    ("BuLuoGeLi", "buluogeli"),
])
def test_brief_cache_key_normalizes(raw, expected):
    assert steward_module.brief_cache_key(raw) == expected


def test_brief_cache_path_uses_normalized_key(brief_dir):
    assert steward_module.brief_cache_path("LeiJiang", "20260917").name == "leijiang_20260917.json"


def test_brief_cache_candidates_order(brief_dir):
    """归一化键优先，原样键兜底；已归一化的输入只返回一项，避免重复 stat。"""
    assert [p.name for p in steward_module.brief_cache_candidates("LeiJiang", "20260917")] == [
        "leijiang_20260917.json", "LeiJiang_20260917.json",
    ]
    assert [p.name for p in steward_module.brief_cache_candidates("leijiang", "20260917")] == [
        "leijiang_20260917.json",
    ]


# ------------------------------------------------------------------
# 2. 核心回归：大写 userId 必须命中 cron 写的小写缓存，且不现算
# ------------------------------------------------------------------

def test_uppercase_user_id_hits_lowercase_cache_without_recompute(brief_dir, monkeypatch):
    """cron 写 leijiang_*.json，API 用 userId=LeiJiang 必须命中、一次都不许现算。

    断言"没现算"而不是"返回内容对"：runner.run 和 classify_regime 只要被调用过
    就说明走了现算路径（这正是事故表现），返回内容长得一样也判红。
    """
    _write_cache(brief_dir, f"leijiang_{_today()}.json")
    steward, runner, classify_calls = _make_steward(monkeypatch)

    result = steward.briefing("LeiJiang")

    assert result.get("from_cache") is True, f"必须命中缓存，实际返回: {result}"
    assert result.get("one_line") == "cron 预生成的晨报"
    assert runner.calls == [], f"命中缓存时不得调用 PipelineRunner，实际: {runner.calls}"
    assert classify_calls == [], "命中缓存时不得调用 classify_regime"


def test_uppercase_user_id_miss_recomputes_and_writes_lowercase(brief_dir, monkeypatch):
    """无缓存 → 必须现算，且写出的文件名是小写（归一化键），不是原样大小写。"""
    steward, runner, classify_calls = _make_steward(monkeypatch)

    result = steward.briefing("LeiJiang")

    assert runner.calls == ["fast"], f"未命中必须走 fast 管线现算，实际: {runner.calls}"
    assert classify_calls, "未命中必须调用 classify_regime"
    assert result.get("from_cache") is None, "现算结果不应带 from_cache 标记"

    written = sorted(p.name for p in brief_dir.glob("*.json"))
    assert written == [f"leijiang_{_today()}.json"], (
        f"写入必须用归一化小写键，实际目录: {written}"
    )


# ------------------------------------------------------------------
# 3. TTL 必须原样保留（4 小时，不许为了让缓存命中而放宽）
# ------------------------------------------------------------------

def test_stale_cache_over_ttl_is_recomputed(brief_dir, monkeypatch):
    """超过 4 小时的缓存必须作废重算 —— 归一化不得顺手把 TTL 也放宽了。

    CACHE_TTL_HOURS=4 是产品取舍（11:30 后要拿到更新的北向/融资数据），
    不是 bug。这个用例钉死它，防止后人"顺手"改成 24h 换速度。
    """
    stale = _write_cache(brief_dir, f"leijiang_{_today()}.json", age_hours=5.0)
    steward, runner, _ = _make_steward(monkeypatch)

    result = steward.briefing("LeiJiang")

    assert result.get("from_cache") is None, "5 小时前的缓存已过 TTL，不能再用"
    assert runner.calls == ["fast"]
    # 注意：不能断言 "stale 文件不存在"。过期缓存被删后，重算会以同一个归一化键
    # 重新写出同名文件（这正是期望行为）。要断言的是"内容被刷新了"——mtime 回到现在。
    assert stale.exists(), "重算后应以归一化键重新写出当日缓存"
    assert time.time() - stale.stat().st_mtime < 60, "重算后的缓存 mtime 应该是刚刚"


def test_fresh_cache_within_ttl_is_used(brief_dir, monkeypatch):
    """3 小时前的缓存仍在 TTL 内，必须命中。"""
    _write_cache(brief_dir, f"leijiang_{_today()}.json", age_hours=3.0)
    steward, runner, _ = _make_steward(monkeypatch)

    result = steward.briefing("LeiJiang")

    assert result.get("from_cache") is True
    assert runner.calls == []


# ------------------------------------------------------------------
# 4. 存量兼容：归一化前写的大写文件仍然读得到
# ------------------------------------------------------------------

def test_legacy_uppercase_file_is_still_readable(brief_dir, monkeypatch):
    """只存在旧的大写文件时，必须仍能命中，不能让存量数据一夜之间全失效。"""
    _write_cache(brief_dir, f"LeiJiang_{_today()}.json")
    steward, runner, _ = _make_steward(monkeypatch)

    result = steward.briefing("LeiJiang")

    assert result.get("from_cache") is True
    assert runner.calls == []


@requires_case_sensitive_fs
def test_legacy_file_expired_then_recompute_writes_lowercase(brief_dir, monkeypatch):
    """命中存量大写文件但已过期 → 删掉它，重算并写成小写（分裂只收敛不扩散）。"""
    legacy = _write_cache(brief_dir, f"LeiJiang_{_today()}.json", age_hours=5.0)
    steward, runner, _ = _make_steward(monkeypatch)

    result = steward.briefing("LeiJiang")

    assert result.get("from_cache") is None
    assert not legacy.exists(), "过期的存量大写文件应被删掉"
    written = sorted(p.name for p in brief_dir.glob("*.json"))
    assert written == [f"leijiang_{_today()}.json"], (
        f"重算必须写归一化小写键，而不是把大写文件再写回去，实际: {written}"
    )


@requires_case_sensitive_fs
def test_normalized_file_wins_over_legacy_when_both_exist(brief_dir, monkeypatch):
    """两种大小写同时存在时以归一化键为准（原样键只是兜底，不抢优先级）。"""
    _write_cache(brief_dir, f"leijiang_{_today()}.json", payload={"one_line": "归一化键"})
    _write_cache(brief_dir, f"LeiJiang_{_today()}.json", payload={"one_line": "存量大写"})
    steward, runner, _ = _make_steward(monkeypatch)

    result = steward.briefing("LeiJiang")

    assert result.get("one_line") == "归一化键"
    assert runner.calls == []


# ------------------------------------------------------------------
# 5. briefing_history：往期晨报同样受大小写分裂影响
# ------------------------------------------------------------------

def test_briefing_history_reads_lowercase_files_for_uppercase_user(brief_dir):
    """userId=LeiJiang 查往期，必须能读到 cron 写的小写历史文件。

    修复前 glob 用的是原样 user_id（LeiJiang_*），cron 写的小写文件一条都读不到，
    表现是「往期晨报是空的」。
    """
    _write_cache(brief_dir, f"leijiang_{_today()}.json")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    _write_cache(brief_dir, f"leijiang_{yesterday}.json")

    result = steward_module.Steward.briefing_history(None, "LeiJiang", days=7)

    dates = [item["date"] for item in result]
    assert _today() in dates, f"往期晨报读不到 cron 写的小写文件: {dates}"
    assert yesterday in dates


def test_briefing_history_still_reads_legacy_uppercase_files(brief_dir):
    """存量大写历史文件也不能丢。"""
    _write_cache(brief_dir, f"LeiJiang_{_today()}.json")

    result = steward_module.Steward.briefing_history(None, "LeiJiang", days=7)

    assert [item["date"] for item in result] == [_today()]


@requires_case_sensitive_fs
def test_briefing_history_does_not_double_count(brief_dir):
    """两种大小写指向同一批数据时，不能重复返回两条。"""
    _write_cache(brief_dir, f"leijiang_{_today()}.json")
    _write_cache(brief_dir, f"LeiJiang_{_today()}.json")

    result = steward_module.Steward.briefing_history(None, "LeiJiang", days=7)

    assert len(result) == 1, f"同一天不应返回两条，实际 {len(result)} 条"


# ------------------------------------------------------------------
# 6. 源码护栏：不许任何地方再各自拼晨报缓存文件名
# ------------------------------------------------------------------

def test_no_other_module_builds_briefing_cache_filename():
    """除 services/steward.py 外，任何文件都不许再拼 briefing 缓存文件名。

    本项目为「同一逻辑散落多处导致分裂」付过学费（fund_name_util.py 就是为此
    抽出来的）。这条护栏保证新增调用点只能复用 brief_cache_path()/candidates()，
    不会再长出第三套大小写。
    """
    import re
    # 三种历史拼法，任一出现都说明又有人绕开了 brief_cache_path()：
    #   data_dir / "briefings" / f"..."
    #   _BRIEF_DIR / f"..."
    #   brief_dir / f"..."
    patterns = [
        re.compile(r'"briefings"\s*/\s*f"'),
        re.compile(r'_BRIEF_DIR\s*/\s*f"'),
        re.compile(r'\bbrief_dir\s*/\s*f"'),
    ]
    backend_dir = Path(steward_module.__file__).resolve().parent.parent
    offenders = []
    for fp in backend_dir.rglob("*.py"):
        if "/tests/" in str(fp) or fp.name == "steward.py":
            continue
        try:
            src = fp.read_text(encoding="utf-8")
        except Exception:
            continue
        for pat in patterns:
            if pat.search(src):
                offenders.append(f"{fp.relative_to(backend_dir)}: {pat.pattern}")
    assert not offenders, (
        "以下文件绕开了 services.steward.brief_cache_path()/brief_cache_candidates() "
        f"自行拼晨报缓存文件名：{offenders}"
    )

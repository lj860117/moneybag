"""
``scripts/cleanup_morning_report_cache.py`` 的行为级测试（2026-09-17）
====================================================================

为什么要有这个文件
------------------
这个脚本要挂 cron 去**删除生产数据**，而它原来的形态有三处不适合自动化：

1. 保留期写死 ``timedelta(days=7)`` —— 改需求就得改代码；
2. 走 ``input("确认删除? (yes/no): ")`` —— cron 里跑会被 EOFError 崩掉；
3. 没有 dry-run —— 挂 cron 前无法在服务器上预演。

改完之后的验收不能只看「跑一次绿了」，因为**在 180 天保留期下，首次运行
本来就该删 0 个**（服务器上最早的文件是 20260521，180 天前是 20260321，
20260521 > 20260321）。「删 0 个」既可能是「逻辑正确」，也可能是「逻辑坏了
但碰巧没命中」——所以每条性质都必须用**可控的小保留期**证明它真的会删，
再用 180 天证明它真的不删。

被测的三条性质
--------------
* 保留期可配置（``--days`` > 环境变量 > 默认 180）
* 非交互（``--yes``）；且非交互**没给** ``--yes`` 时必须拒绝执行、不删任何东西
* ``--dry-run`` 只打印不删

以及一条**硬安全边界**::

    data/briefings/_legacy_uppercase_backup/ 下的 26 个待观察备份文件
    必须不被扫到、不被删。

这条不能只靠「``glob`` 不递归所以理论上安全」——那正是本项目反复踩过的
「闸门空转仍显绿」。这里用真实子目录 + 真实 ``main()`` 调用 + 删前删后文件
清单对比来证明。

故障注入方向（保证这些断言不是恒绿）
------------------------------------
把 ``cleanup_morning_report_cache.py`` 里的 ``DEFAULT_RETENTION_DAYS`` 改回
``7``，:func:`test_default_retention_is_180_days` 与
:func:`test_180_days_keeps_the_whole_recent_archive` 必须转红；
把 ``brief_dir.glob`` 改成 ``brief_dir.rglob``，
:func:`test_backup_subdirectory_survives_aggressive_cleanup` 必须转红；
把 ``if not fp.is_file(): continue`` 删掉，
:func:`test_directory_named_like_json_is_not_deleted` 必须转红。

运行方式::

    cd /Users/leijiang/WorkBuddy/moneybag-for-claudecode && env -u PYTHONPATH \\
        /Users/leijiang/.workbuddy/binaries/python/envs/default/bin/python \\
        -m pytest backend/tests/test_cleanup_morning_report_cache.py -v
"""
import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import config

# ---------------------------------------------------------------------------
# 加载被测脚本
# ---------------------------------------------------------------------------
# scripts/ 下没有 __init__.py，且**刻意不往 sys.path 里塞含 ".." 的路径**
# （未归一化的 ".." 会让 Path(__file__).parent.parent.parent 算错目录，
#  2026-09-17 真事故：把 stock_monitor_cron 的 MONITOR_DIR 建到了
#  backend/tests/data/monitor）。这里用 importlib 按绝对路径加载最干净。
_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "cleanup_morning_report_cache.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "cleanup_morning_report_cache", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None, (
    f"无法加载脚本: {_SCRIPT_PATH}")
cleanup = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cleanup)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
class _FakeStdin:
    """替代 ``sys.stdin``，可控 ``isatty()`` 与 ``readline()``。"""

    def __init__(self, text: str = "", isatty: bool = True) -> None:
        self._text = text
        self._isatty = isatty

    def isatty(self) -> bool:
        return self._isatty

    def readline(self) -> str:
        return self._text


def _days_ago(n: int) -> str:
    """返回 n 天前的 ``YYYYMMDD``（相对运行时刻，测试不会随时间腐烂）。"""
    return (datetime.now() - timedelta(days=n)).strftime("%Y%m%d")


def _touch(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_text("{}", encoding="utf-8")
    return path


@pytest.fixture
def brief_dir(tmp_path, monkeypatch):
    """把 config.DATA_DIR 指到 tmp，返回建好的 briefings 目录。"""
    data_dir = tmp_path / "data"
    target = data_dir / "briefings"
    target.mkdir(parents=True)
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir), raising=True)
    return target


@pytest.fixture
def non_interactive(monkeypatch):
    """把 stdin 伪装成「没有人在旁边」（cron 的真实形态）。"""
    monkeypatch.setattr(sys, "stdin", _FakeStdin("", isatty=False))


# ---------------------------------------------------------------------------
# 1. 保留期可配置
# ---------------------------------------------------------------------------
def test_default_retention_is_180_days():
    """默认保留期必须是 180 天（用户拍板值）。

    故障注入方向：把 DEFAULT_RETENTION_DAYS 改回 7，本用例必红。
    """
    assert cleanup.DEFAULT_RETENTION_DAYS == 180, (
        f"默认保留期被改成 {cleanup.DEFAULT_RETENTION_DAYS}，"
        f"用户拍板的是 180 天")
    assert cleanup.resolve_retention_days(None, env={}) == 180


def test_cli_days_overrides_default():
    assert cleanup.resolve_retention_days(7, env={}) == 7
    assert cleanup.resolve_retention_days(30, env={}) == 30


def test_env_overrides_default():
    env = {cleanup.RETENTION_DAYS_ENV: "30"}
    assert cleanup.resolve_retention_days(None, env=env) == 30


def test_cli_wins_over_env():
    """优先级：--days > 环境变量 > 默认。"""
    env = {cleanup.RETENTION_DAYS_ENV: "30"}
    assert cleanup.resolve_retention_days(7, env=env) == 7


@pytest.mark.parametrize("bad", ["abc", "0", "-3", "7.5"])
def test_invalid_retention_raises(bad):
    """非法保留期必须报错，而不是被静默当成默认或当成 0（0 天 = 全删）。"""
    with pytest.raises(ValueError):
        cleanup.resolve_retention_days(bad, env={})
    with pytest.raises(ValueError):
        cleanup.resolve_retention_days(None, env={cleanup.RETENTION_DAYS_ENV: bad})


def test_empty_env_var_is_treated_as_unset():
    """空的 ``BRIEF_CACHE_RETENTION_DAYS=`` 视为「没设」，回落默认 180。

    刻意不报错：cron 里 ``.env`` 导出空值是常见形态，为它报警太吵；
    而回落方向是**保留更久**，属于安全方向。
    """
    env = {cleanup.RETENTION_DAYS_ENV: "   "}
    assert cleanup.resolve_retention_days(None, env=env) == 180


def test_main_rejects_invalid_retention_with_usage_exit(brief_dir, capsys):
    """保留期非法 → 退出码 2，且一个文件都不许删。"""
    old = _touch(brief_dir, f"LeiJiang_{_days_ago(400)}.json")

    rc = cleanup.main(["--days", "0", "--yes"])

    captured = capsys.readouterr()
    assert rc == cleanup.EXIT_USAGE, f"非法保留期应返回 2，实际 {rc}"
    assert old.exists(), "保留期非法时绝不许删文件"
    assert "保留期" in (captured.err + captured.out), (
        f"报错信息里应当说明是保留期非法: {captured.err!r}")


# ---------------------------------------------------------------------------
# 2. 非交互
# ---------------------------------------------------------------------------
def test_decide_action_matrix():
    """动作决策矩阵：dry-run 最优先，其次 --yes，都没有且非交互 → 拒绝。"""
    assert cleanup.decide_action(True, True, False) == cleanup.ACTION_DRY_RUN
    assert cleanup.decide_action(False, True, True) == cleanup.ACTION_DRY_RUN
    assert cleanup.decide_action(True, False, False) == cleanup.ACTION_DELETE
    assert cleanup.decide_action(False, False, True) == cleanup.ACTION_PROMPT
    assert cleanup.decide_action(False, False, False) == cleanup.ACTION_REFUSE


def test_non_interactive_without_yes_refuses_and_deletes_nothing(
        brief_dir, non_interactive, capsys):
    """cron 忘了加 --yes → 必须拒绝执行（返回 2），绝不能静默删。

    这是本脚本最关键的安全性质：宁可每周报错被看见，也不要「静默删错」。
    """
    old = _touch(brief_dir, f"LeiJiang_{_days_ago(400)}.json")

    rc = cleanup.main([])

    assert rc == cleanup.EXIT_USAGE, f"非交互无 --yes 应返回 2，实际 {rc}"
    assert old.exists(), "拒绝执行时不许删任何文件"
    err = capsys.readouterr().err
    assert "--yes" in err, f"报错信息里应当给出修复指引: {err!r}"


def test_yes_flag_deletes_without_prompt(brief_dir, non_interactive, capsys):
    """--yes：非交互下照删，不询问。"""
    old = _touch(brief_dir, f"LeiJiang_{_days_ago(400)}.json")
    recent = _touch(brief_dir, f"LeiJiang_{_days_ago(1)}.json")

    rc = cleanup.main(["--yes", "--days", "7"])

    assert rc == cleanup.EXIT_OK
    assert not old.exists(), "400 天前的文件应当被删"
    assert recent.exists(), "1 天前的文件应当保留"


def test_assume_yes_env_var_works(brief_dir, non_interactive, monkeypatch):
    """BRIEF_CACHE_CLEANUP_YES=1 等价于 --yes（cron 可用环境变量开关）。"""
    monkeypatch.setenv(cleanup.ASSUME_YES_ENV, "1")
    old = _touch(brief_dir, f"LeiJiang_{_days_ago(400)}.json")

    rc = cleanup.main(["--days", "7"])

    assert rc == cleanup.EXIT_OK
    assert not old.exists()


def test_interactive_confirm_deletes(brief_dir, monkeypatch):
    """有人在旁边 + 输 yes → 删。"""
    monkeypatch.setattr(sys, "stdin", _FakeStdin("yes\n", isatty=True))
    old = _touch(brief_dir, f"LeiJiang_{_days_ago(400)}.json")

    rc = cleanup.main(["--days", "7"])

    assert rc == cleanup.EXIT_OK
    assert not old.exists()


def test_interactive_cancel_keeps_files(brief_dir, monkeypatch):
    """有人在旁边 + 输 no → 返回 1，一个都不删。"""
    monkeypatch.setattr(sys, "stdin", _FakeStdin("no\n", isatty=True))
    old = _touch(brief_dir, f"LeiJiang_{_days_ago(400)}.json")

    rc = cleanup.main(["--days", "7"])

    assert rc == cleanup.EXIT_CANCELLED
    assert old.exists(), "取消时不许删文件"


# ---------------------------------------------------------------------------
# 3. dry-run
# ---------------------------------------------------------------------------
def test_dry_run_deletes_nothing_but_lists_files(
        brief_dir, non_interactive, capsys):
    """dry-run：退出码 0，清单里有名字，但文件一个都不许少。"""
    old = _touch(brief_dir, f"LeiJiang_{_days_ago(400)}.json")
    recent = _touch(brief_dir, f"LeiJiang_{_days_ago(1)}.json")

    rc = cleanup.main(["--dry-run", "--days", "7"])

    out = capsys.readouterr().out
    assert rc == cleanup.EXIT_OK
    assert old.exists(), "dry-run 绝不许删文件"
    assert recent.exists()
    assert old.name in out, f"dry-run 应当列出将要删除的文件: {out}"


def test_dry_run_env_var_works(brief_dir, non_interactive, monkeypatch):
    monkeypatch.setenv(cleanup.DRY_RUN_ENV, "1")
    old = _touch(brief_dir, f"LeiJiang_{_days_ago(400)}.json")

    rc = cleanup.main(["--days", "7"])

    assert rc == cleanup.EXIT_OK
    assert old.exists()


def test_dry_run_needs_no_yes_flag(brief_dir, non_interactive):
    """dry-run 不删东西，因此不该被「非交互必须 --yes」这条拦住。"""
    rc = cleanup.main(["--dry-run", "--days", "7"])
    assert rc == cleanup.EXIT_OK


# ---------------------------------------------------------------------------
# 4. 硬安全边界：备份子目录必须活下来
# ---------------------------------------------------------------------------
def test_backup_subdirectory_survives_aggressive_cleanup(
        brief_dir, non_interactive, capsys):
    """``_legacy_uppercase_backup/`` 下 26 个备份在**最激进**的清理下也不能丢。

    用 ``--days 1``（保留期压到最小）制造最大删除压力，同时放一个**同级**
    老文件作为对照组：如果它没被删，说明这次运行根本没生效，本用例就是假绿。

    故障注入方向：把脚本里的 ``brief_dir.glob`` 改成 ``rglob``，本用例必红。
    """
    backup = brief_dir / "_legacy_uppercase_backup"
    backup.mkdir()
    backup_files = [
        _touch(backup, f"LeiJiang_202401{i:02d}.json") for i in range(1, 27)
    ]
    assert len(backup_files) == 26

    control = _touch(brief_dir, f"LeiJiang_{_days_ago(400)}.json")

    rc = cleanup.main(["--yes", "--days", "1"])

    assert rc == cleanup.EXIT_OK
    survivors = [p for p in backup_files if p.exists()]
    assert len(survivors) == 26, (
        f"备份目录被删了 {26 - len(survivors)} 个文件！"
        f"存活: {[p.name for p in survivors][:5]} …")
    assert not control.exists(), (
        "对照组（同级老文件）没被删 —— 说明这次清理根本没生效，"
        "上面那条 26 个存活的断言就是假绿")


def test_backup_subdirectory_is_reported_as_skipped(
        brief_dir, non_interactive, capsys):
    """被跳过的子目录要打印出来 —— 让「没扫到」在服务器 dry-run 里可核对。

    这条把安全性质从「代码里写着不递归」升级成「输出里能看见确实没扫」。
    """
    backup = brief_dir / "_legacy_uppercase_backup"
    backup.mkdir()
    for i in range(1, 27):
        _touch(backup, f"LeiJiang_202401{i:02d}.json")
    _touch(brief_dir, f"LeiJiang_{_days_ago(400)}.json")

    rc = cleanup.main(["--dry-run", "--days", "1"])

    out = capsys.readouterr().out
    assert rc == cleanup.EXIT_OK
    assert "_legacy_uppercase_backup" in out, (
        f"被跳过的子目录应当出现在输出里: {out}")
    assert "(26 个文件)" in out, f"应当报告子目录内文件数: {out}"


def test_directory_named_like_json_is_not_counted_as_cache_file(
        brief_dir, non_interactive, capsys):
    """``glob("*.json")`` **会匹配到目录** —— 必须靠 ``is_file()`` 挡住。

    ⚠️ 断言口径说明（2026-09-17 故障注入 C 的教训）：这里**不能**只断言
    「目录还在」。``Path.unlink()`` 作用在目录上必然抛 ``IsADirectoryError``
    并被脚本 catch，所以「目录还在」是个**恒真**断言——注入 C（删掉
    ``is_file()``）实测它照样全绿，等于没守。

    真正的可观测差异是**计数**：少了 ``is_file()``，这个目录会被当成
    「格式错误的缓存」计入待删除清单（还会计入 total_size）。所以断言
    落在「无效缓存 = 0」上，这条在注入 C 下确实会红。
    """
    weird_dir = brief_dir / "archive.json"
    weird_dir.mkdir()
    _touch(weird_dir, "inner.json")
    _touch(brief_dir, f"LeiJiang_{_days_ago(1)}.json")

    rc = cleanup.main(["--dry-run", "--days", "1"])

    out = capsys.readouterr().out
    assert rc == cleanup.EXIT_OK
    assert weird_dir.is_dir(), "名为 *.json 的目录被当成文件处理了"
    assert (weird_dir / "inner.json").exists()
    assert "无效缓存（格式错误）: 0" in out, (
        f"名为 *.json 的目录被计入了待删除清单 —— is_file() 保险失效: {out}")


def test_glob_is_not_recursive_source_level():
    """源码级断言：扫描必须是非递归的 ``glob``，不能是 ``rglob``。

    与上面行为级用例互补：行为级证明「现在不会删」，源码级防止有人
    日后顺手改成 rglob（这种改动很可能不会立刻被行为级用例发现，
    因为备份文件名换个日期就可能不在删除窗口内）。
    """
    source = _SCRIPT_PATH.read_text(encoding="utf-8")
    assert "brief_dir.glob(" in source, "扫描入口变了，请复核是否仍为非递归"
    assert "rglob(" not in source.split("def skipped_subdirectories")[0], (
        "扫描阶段出现了 rglob —— 会递归进 _legacy_uppercase_backup/")


# ---------------------------------------------------------------------------
# 5. 分类逻辑与 180 天的实际效果
# ---------------------------------------------------------------------------
def test_180_days_keeps_the_whole_recent_archive(brief_dir, non_interactive):
    """复刻服务器现状：最早文件 20260521，180 天保留期 → **一个都不删**。

    这是「改完 180 天后首次运行删 0 个」这条预期结论的可执行版本。
    用相对天数构造（最早 = 今天往前 120 天），避免测试随时间腐烂。

    故障注入方向：把 DEFAULT_RETENTION_DAYS 改回 7，本用例必红
    （120 天前的文件会落进 7 天窗口外，被删）。
    """
    files = [
        _touch(brief_dir, f"LeiJiang_{_days_ago(n)}.json")
        for n in (0, 1, 30, 60, 119, 120)
    ]

    rc = cleanup.main(["--yes"])  # 不传 --days → 走默认 180

    assert rc == cleanup.EXIT_OK
    survivors = [p for p in files if p.exists()]
    assert len(survivors) == len(files), (
        f"180 天保留期下不应删任何近期文件，实际删了 "
        f"{len(files) - len(survivors)} 个: "
        f"{[p.name for p in files if not p.exists()]}")


def test_seven_days_still_deletes_when_asked(brief_dir, non_interactive):
    """对照组：同一个目录，换成 7 天保留期就该删。

    没有这条，一个「无论如何都返回 0 且什么都不做」的实现也能骗过上一条。
    """
    old = _touch(brief_dir, f"LeiJiang_{_days_ago(120)}.json")
    recent = _touch(brief_dir, f"LeiJiang_{_days_ago(1)}.json")

    rc = cleanup.main(["--yes", "--days", "7"])

    assert rc == cleanup.EXIT_OK
    assert not old.exists(), "7 天保留期下 120 天前的文件应当被删"
    assert recent.exists()


def test_invalid_and_future_files_are_deleted(brief_dir, non_interactive):
    """格式错误与未来日期的文件仍在删除范围内（沿用原脚本语义）。"""
    invalid = _touch(brief_dir, "no_date_here.json")
    future = _touch(brief_dir, "LeiJiang_20991231.json")
    keep = _touch(brief_dir, f"LeiJiang_{_days_ago(1)}.json")

    rc = cleanup.main(["--yes", "--days", "7"])

    assert rc == cleanup.EXIT_OK
    assert not invalid.exists()
    assert not future.exists()
    assert keep.exists()


def test_missing_brief_dir_is_not_an_error(tmp_path, monkeypatch, capsys):
    """缓存目录不存在 → 退出码 0（cron 不该每周因为这个报警）。"""
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path / "nonexistent"))

    rc = cleanup.main(["--yes"])

    assert rc == cleanup.EXIT_OK
    assert "无需清理" in capsys.readouterr().out


def test_empty_brief_dir_is_not_an_error(brief_dir, non_interactive, capsys):
    rc = cleanup.main(["--yes"])
    assert rc == cleanup.EXIT_OK
    assert "未找到缓存文件" in capsys.readouterr().out

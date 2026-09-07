#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「静默失效」防护栏回归测试 —— 2026-09-07/08 系列事故锁定。

主题：代码在出错时**什么都不说** —— 要么静默返回空、要么静默用错误默认值顶替、
要么静默污染变量。用户看到的是"没数据"/"没异动"/"今天没涨跌"，
实际是数据链路断了。这类 bug 的共同点是：不出异常、不打日志、测试全绿。

本文件覆盖 6 处（代号沿用排查时的编号）：

    D. fund_monitor.get_fund_nav_history 的降级分支是死代码
       `return cached.get("data", []) if isinstance(cached, dict) else []`
       —— MemoryCache.get() 返回裸 list 且过期即删除，这个分支恒返回 []，
       数据源全挂时回撤/预警整段从推送里消失。
       修：MemoryCache 新增 get_stale()（过期也返回、不删除），降级改用它。

    C. 收盘复盘吃到 run_scan 刚写入的旧净值
       _NAV_TTL = 3600，而 run_scan() 与第 4 段持仓预警只隔几分钟。
       修：get_fund_nav_history 增加 force_refresh 参数。

    G. 管家结论（neutral/观望）与 AI 诊断（止盈/减仓）在同一条推送里打架
       两者是两条互不知情的推理链，诊断 prompt 里根本没有管家说了什么。
       修：(a) prompt 注入管家 direction/conclusion 并要求显式说明分歧；
           (b) 推送侧兜底插入冲突提示行。

    H. night_worker 组合温度计的两处静默
       H1: `if t.get("type") != "BUY": continue` —— SELL 完全跳过，
           卖出后份额/成本不扣减，已清仓的基金仍按全额展示。
       H2: `cur_val = cur_nav * shares if cur_nav > 0 else cost_amount`
           —— 净值取不到时静默用成本顶替，浮盈恒显示 0.0%，
           看起来像"今天没涨跌"，实为数据缺失。

    项目5. stock_monitor_cron 里 `name = alert.get("name", ...)`
           覆盖了外层 `name`（用户名），后续所有日志/推送里的"用户名"
           变成基金名，直接误导线上排障。

    项目6. load_fund_holdings 传错 user_id（哈希串）时静默返回 []
           无任何提示，排查时极易误判成"数据被误删"。

与 test_drawdown_metric_regression.py（回撤主题）分开：那边是"数字算错"，
这边是"错了却不吭声"，定位时的心智模型不同。

运行方式（本地，务必用这条）::

    cd backend && env -u PYTHONPATH python3 -m pytest tests/test_silent_failure_guardrails.py -v -rfEX

为什么必须 `env -u PYTHONPATH`：托管 python 的 PYTHONPATH 指向 WorkBuddy 的
sitecustomize.py shim，会拦截 config.py 模块级的 USERS_DIR.mkdir()，把大量用例
打成 ERROR —— 那是环境问题不是项目 bug，不要去改 config.py。

服务器（venv 依赖齐全，没有 shim）::

    cd /opt/moneybag/backend && DATA_DIR=/opt/moneybag/data PYTHONPATH=/opt/moneybag/backend \
        /opt/moneybag/venv/bin/python3 -m pytest tests/test_silent_failure_guardrails.py -q -rfEX
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import re
import time
from pathlib import Path
from unittest import mock

import pytest

from infra.cache.memory_cache import MemoryCache
from services import fund_monitor
from services.fund_monitor import _nav_cache, get_fund_nav_history

_BACKEND_DIR = Path(fund_monitor.__file__).parent          # backend/services
_BACKEND_ROOT = _BACKEND_DIR.parent                         # backend


# ============================================================
# D. MemoryCache.get_stale —— 过期也返回、且不删除
# ============================================================

def test_get_stale_returns_expired_entry_and_keeps_it():
    """get_stale 必须能取到已过期的条目，且**不删除**它。

    这是 D 的根因：旧降级分支用 get()，而 get() 一发现过期就 del 掉再返回
    None —— 于是"缓存未过期"时函数早从正常路径 return 了，真走到降级分支时
    键要么不存在要么已被删，必然取不到值，降级 100% 从未生效。
    """
    cache = MemoryCache(default_ttl=60)
    cache.set("k", [1, 2, 3], ttl=1)
    time.sleep(1.1)  # 让它过期

    # get_stale 仍拿得到，且不删除
    assert cache.get_stale("k") == [1, 2, 3]
    assert cache.get_stale("k") == [1, 2, 3], "get_stale 不得删除条目（要可重复调用）"


def test_get_still_deletes_expired_entry():
    """对照用例：get() 的行为必须与 get_stale() 相反（过期即删、返回 None）。

    两条用例成对存在，才能锁住"两个方法的行为差异"。只测其中一个，
    将来有人把 get() 改成不删除也发现不了。
    """
    cache = MemoryCache(default_ttl=60)
    cache.set("k", [1, 2, 3], ttl=1)
    time.sleep(1.1)

    assert cache.get("k") is None, "get() 过期必须返回 None"
    assert cache.get_stale("k") is None, "get() 过期必须已删除条目，get_stale 也应取不到"


def test_get_stale_returns_none_for_missing_key():
    """键不存在时 get_stale 返回 None（不能抛异常）。"""
    cache = MemoryCache(default_ttl=60)
    assert cache.get_stale("never_set") is None


_RAISE = object()  # 哨兵：表示 L1 数据源应抛异常


class _FakeDF:
    """最小 DataFrame 替身，满足 `df.empty` / `df.tail(n)` / `iterrows()`。

    get_fund_nav_history 只用到这三个成员，没必要为此依赖 pandas/akshare。
    """

    def __init__(self, rows: list):
        self._rows = rows

    @property
    def empty(self) -> bool:
        return len(self._rows) == 0

    def tail(self, n: int) -> "_FakeDF":
        return _FakeDF(self._rows[-n:])

    def iterrows(self):
        for i, r in enumerate(self._rows):
            yield i, r


@contextlib.contextmanager
def _nav_sources(akshare_result=_RAISE):
    """接管 get_fund_nav_history 的三条数据源，使其行为完全可控。

    get_fund_nav_history 内部是**函数级 import**，patch 不到 fund_monitor 的
    模块属性上，所以这里直接接管数据源本体：

        L1 akshare  : infra.data_source.market.stocks.get_fund_nav_history
        L2 Tushare  : services.tushare_data.is_configured（False ⇒ 整段跳过）
        L3 天天基金 : requests.get（抛异常 ⇒ EM 循环 break，all_items 为空）

    Args:
        akshare_result: L1 的返回值。默认 `_RAISE` 表示抛 RuntimeError；
            传 `_FakeDF([...])` 可让 L1 成功返回数据。

    Yields:
        L1 的 mock 对象，便于断言"数据源到底有没有被调用"。
    """
    if akshare_result is _RAISE:
        ak_patch = mock.patch("infra.data_source.market.stocks.get_fund_nav_history",
                              side_effect=RuntimeError("akshare down"))
    else:
        ak_patch = mock.patch("infra.data_source.market.stocks.get_fund_nav_history",
                              return_value=akshare_result)

    with ak_patch as ak, \
            mock.patch("services.tushare_data.is_configured", return_value=False, create=True), \
            mock.patch("requests.get", side_effect=RuntimeError("network down")):
        yield ak


def test_nav_history_fallback_uses_stale_cache(capsys):
    """数据源全挂时，get_fund_nav_history 必须降级用陈旧缓存而不是返回 []。

    这是 D 的核心：旧的降级分支写的是
    `return cached.get("data", []) if isinstance(cached, dict) else []`，
    而 MemoryCache.get() 返回裸 list 且过期即删，所以那个分支恒返回 [] ——
    数据源一挂，回撤/预警整段从推送里消失，且**不打任何日志**。

    故障注入方向：把实现改回旧的 `isinstance(..., dict)` 版本，本用例应变红
    （返回 [] 而不是预置的 3 天数据）。
    """
    code = "999999"  # 不存在的基金，保证三源全失败
    days = 30
    key = f"{code}_{days}"
    stale = [{"date": "2026-09-01", "nav": 1.0, "rate": 0.0},
             {"date": "2026-09-02", "nav": 1.1, "rate": 0.0},
             {"date": "2026-09-03", "nav": 1.2, "rate": 0.0}]

    _nav_cache.set(key, stale, ttl=1)
    time.sleep(1.1)  # 让缓存过期，模拟「数据源挂了且缓存也已过期」的最坏情况

    with _nav_sources():  # 三源全失败
        got = get_fund_nav_history(code, days=days)

    assert got == stale, (
        f"数据源全失败时应降级用陈旧缓存（{len(stale)} 天），实际返回 {got!r}")

    # 降级必须留下痕迹，否则又是一次静默失效
    captured = capsys.readouterr()
    assert "降级" in captured.out and code in captured.out, (
        f"降级用陈旧缓存必须打印 [FUND_MONITOR] 日志，实际 stdout：\n{captured.out}")


def test_nav_history_returns_empty_and_logs_when_no_stale_cache(capsys):
    """数据源全挂**且**没有陈旧缓存时，返回 [] 并明确说明「无陈旧缓存可用」。

    故障注入方向：把「无陈旧缓存」的日志/分支删掉，本用例应变红。
    """
    code = "999998"
    days = 30
    _nav_cache._data.pop(f"{code}_{days}", None)  # 确保干净

    with _nav_sources():
        got = get_fund_nav_history(code, days=days)

    assert got == [], f"无陈旧缓存时应返回 []，实际 {got!r}"

    captured = capsys.readouterr()
    assert "无陈旧缓存" in captured.out, (
        f"必须明确打印「无陈旧缓存可用」，否则排障时无法区分"
        f"「没缓存」和「有缓存但没用上」，实际 stdout：\n{captured.out}")


# ============================================================
# C. force_refresh 绕过缓存
# ============================================================

def test_force_refresh_bypasses_cache():
    """force_refresh=True 必须跳过读缓存。

    故障注入方向：把 `if not force_refresh:` 改回无条件读缓存，本用例应变红。
    """
    code = "888888"
    key = f"{code}_30"
    sentinel = [{"date": "2026-09-01", "nav": 1.0, "rate": 0.0}]

    # 预置缓存：无论如何都不该被命中，因为数据源被 mock 成返回另一份数据
    _nav_cache.set(key, sentinel, ttl=3600)

    fresh_df = _FakeDF([
        {"累计净值": 1.5, "净值日期": "2026-09-07", "日增长率": 0.0},
        {"累计净值": 1.4, "净值日期": "2026-09-04", "日增长率": 0.0},
    ])

    with _nav_sources(fresh_df) as ak:
        got_forced = get_fund_nav_history(code, days=30, force_refresh=True)
        assert ak.called, "force_refresh=True 必须真的去问数据源"

    assert got_forced != sentinel, "force_refresh=True 不应命中预置缓存"
    got_dates = [r.get("date") for r in got_forced or []]
    assert "2026-09-07" in got_dates, (
        f"force_refresh=True 应取到数据源返回的最新数据（含 2026-09-07），"
        f"实际 {got_forced!r}")
    assert "2026-09-01" not in got_dates, (
        f"force_refresh=True 不应命中预置缓存里的 2026-09-01，实际 {got_forced!r}")


def test_default_call_still_uses_cache():
    """默认调用（force_refresh=False）仍走缓存 —— 不能为了修 C 把缓存整体废掉。

    这条是防"修过头"：force_refresh 的默认行为必须与修复前一致。
    """
    code = "777777"
    key = f"{code}_30"
    cached = [{"date": "2026-09-01", "nav": 1.0, "rate": 0.0}]
    _nav_cache.set(key, cached, ttl=3600)

    got = get_fund_nav_history(code, days=30)
    assert got == cached, "默认参数应命中缓存（force_refresh 默认 False）"


def test_close_review_call_site_uses_force_refresh():
    """静态断言：收盘复盘链路的调用点必须带 force_refresh=True。

    AST 扫描而不是跑真实流程 —— 避免依赖网络/数据，且能精确定位行号。
    """
    cron_path = _BACKEND_ROOT / "scripts" / "stock_monitor_cron.py"
    src = cron_path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(cron_path))

    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = getattr(fn, "attr", None) or getattr(fn, "id", None)
        if name != "get_fund_nav_history":
            continue
        kwargs = {kw.arg for kw in node.keywords if kw.arg}
        hits.append((node.lineno, "force_refresh" in kwargs))

    assert hits, f"{cron_path} 里没找到 get_fund_nav_history 调用，扫描逻辑失效了"
    forced = [lineno for lineno, has in hits if has]
    assert forced, (
        "收盘复盘链路必须至少有一处 get_fund_nav_history(..., force_refresh=True)，"
        f"否则 21:02 的复盘会吃到 run_scan 几分钟前写入的 1 小时缓存。"
        f"当前所有调用点：{hits}")


# ============================================================
# H. night_worker 组合温度计：SELL 扣减 + 净值缺失告警
# ============================================================

def _thermometer_with_txns(txns: list, nav_by_code: dict, tmp_path: Path) -> str:
    """用内存用户文件驱动 _build_portfolio_thermometer。

    Args:
        txns: transactions 列表。
        nav_by_code: {code: 最新净值}；不提供或给 0 表示"数据源返回空"。
        tmp_path: pytest 提供的临时目录。

    Returns:
        _build_portfolio_thermometer 的输出文本。
    """
    import config

    uid = "qa_silent"
    safe = hashlib.sha256(uid.encode()).hexdigest()[:16]
    users_dir = tmp_path / "users"
    users_dir.mkdir(parents=True, exist_ok=True)
    (users_dir / f"{safe}.json").write_text(
        json.dumps({"userId": uid, "portfolio": {"transactions": txns}}, ensure_ascii=False),
        encoding="utf-8")

    import scripts.night_worker as nw

    def _fake_hist(code, days=60, force_refresh=False):
        nv = nav_by_code.get(code)
        if not nv:
            return []
        return [{"date": "2026-09-07", "nav": nv, "rate": 0.0},
                {"date": "2026-09-04", "nav": nv, "rate": 0.0}]

    with mock.patch.dict("os.environ", {"USERS_DIR": str(users_dir)}), \
         mock.patch.object(nw, "config", config), \
         mock.patch("services.fund_monitor.get_fund_nav_history", side_effect=_fake_hist):
        return nw._build_portfolio_thermometer(uid)


def test_sell_reduces_shares_and_cost(tmp_path):
    """H1：SELL 必须扣减份额与成本。

    场景：买入 1000 份 @1.0（成本 1000）→ 卖出 500 份 @1.2（到账 600）。
    采用「加权平均成本 + 按剩余份额比例结转」口径：
        剩余份额 = 500，剩余成本 = 1000 × 500/1000 = 500
        当前净值 1.2 → 市值 600，浮盈 = (600-500)/500 = +20%

    故障注入方向：把实现改回 `if t.get("type") != "BUY": continue`，
    份额会变成 1000、成本 1000、市值 1200、浮盈 +20% —— 份额与市值全错。
    """
    txns = [
        {"type": "BUY", "code": "000001", "name": "测试基金A",
         "amount": 1000.0, "shares": 1000.0, "nav": 1.0},
        {"type": "SELL", "code": "000001", "name": "测试基金A",
         "amount": 600.0, "shares": 500.0, "nav": 1.2},
    ]
    out = _thermometer_with_txns(txns, {"000001": 1.2}, tmp_path)

    # 剩余 500 份 × 1.2 = 600 元市值
    assert "¥600.0" in out, f"市值应为 600（剩余 500 份 × 1.2），实际输出：\n{out}"
    # 剩余成本 500，浮盈 +20.0%
    assert "+20.0%" in out, f"浮盈应为 +20.0%，实际输出：\n{out}"


def test_fully_sold_fund_disappears(tmp_path):
    """H1：全部卖出后该基金不应再出现在温度计里。

    故障注入方向：改回跳过 SELL，已清仓基金会带着全额份额继续展示。
    """
    txns = [
        {"type": "BUY", "code": "000002", "name": "已清仓基金",
         "amount": 1000.0, "shares": 1000.0, "nav": 1.0},
        {"type": "SELL", "code": "000002", "name": "已清仓基金",
         "amount": 1100.0, "shares": 1000.0, "nav": 1.1},
        # 另一只仍持有，避免 total_cost==0 导致整体返回 ""
        {"type": "BUY", "code": "000003", "name": "持有中基金",
         "amount": 500.0, "shares": 500.0, "nav": 1.0},
    ]
    out = _thermometer_with_txns(txns, {"000002": 1.1, "000003": 1.0}, tmp_path)

    assert "已清仓基金" not in out, f"已全部卖出的基金不应再展示，实际输出：\n{out}"
    assert "持有中基金" in out, f"仍持有的基金应正常展示，实际输出：\n{out}"


def test_missing_nav_is_flagged_not_silently_zero(tmp_path, capsys):
    """H2：净值取不到时不得静默用成本顶替成 0.0%，必须显式告警。

    故障注入方向：把实现改回 `cur_val = cur_nav * shares if cur_nav > 0
    else cost_amount` + `float_pct = ... else 0.0`，输出会变成
    "▲0.0%"，且不出现任何"缺失"字样 —— 本用例应变红。
    """
    txns = [
        {"type": "BUY", "code": "000004", "name": "净值缺失基金",
         "amount": 1000.0, "shares": 1000.0, "nav": 1.0},
    ]
    # nav_by_code 里没有 000004 → 数据源返回空
    out = _thermometer_with_txns(txns, {}, tmp_path)

    assert "净值缺失" in out, (
        f"净值取不到时必须显式标注缺失，不能伪装成正常行，实际输出：\n{out}")
    assert "▲0.0%" not in out, (
        f"不得把「取不到数」显示成「持平 0.0%」，实际输出：\n{out}")

    captured = capsys.readouterr()
    assert "[HOLDINGS]" in captured.out and "000004" in captured.out, (
        f"净值缺失必须打 [HOLDINGS] 告警日志，实际 stdout：\n{captured.out}")


# ============================================================
# 项目5. stock_monitor_cron 不得在循环里覆盖外层 name
# ============================================================

def _name_assignments_in(func_node: ast.AST) -> list:
    """收集函数体内所有对裸名 `name` 的赋值语句。

    Args:
        func_node: ast.FunctionDef / AsyncFunctionDef 节点。

    Returns:
        ast.Assign 节点列表（含 AnnAssign 之外的普通赋值）。
    """
    found = []
    for sub in ast.walk(func_node):
        if not isinstance(sub, ast.Assign):
            continue
        for tgt in sub.targets:
            if isinstance(tgt, ast.Name) and tgt.id == "name":
                found.append(sub)
    return found


def _enclosing_loops(func_node: ast.AST) -> set:
    """返回函数内所有 `for` / `while` 节点的 id 集合（用于判断语句是否在循环里）。

    Args:
        func_node: 函数定义节点。

    Returns:
        循环节点的 id() 集合。
    """
    return {id(n) for n in ast.walk(func_node)
            if isinstance(n, (ast.For, ast.AsyncFor, ast.While))}


def test_cron_does_not_reassign_outer_name_variable():
    """AST 扫描：run_close_review 里 `name`（用户名）只能被赋值一次。

    背景：run_close_review 开头 `name = p.get("name", uid)` 是**用户名**；
    持仓预警渲染循环里原本写 `name = alert.get("name", ...)` 把它覆盖成基金名，
    之后所有日志/推送里的"用户名"都变成一只基金 ——
    排查"哪个用户推送失败"时看到的是基金名，直接误导排障。

    为什么只扫 run_close_review：其它函数（run_scan 的 L587、行业匹配的
    L184）里的 `name` 都是各自作用域内的**局部**变量，没有"覆盖外层用户名"
    的问题，全文件扫描会产生误报。
    """
    cron_path = _BACKEND_ROOT / "scripts" / "stock_monitor_cron.py"
    src = cron_path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(cron_path))

    target = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == "run_close_review":
            target = node
            break

    assert target is not None, (
        f"{cron_path} 里找不到 run_close_review()，扫描前提失效，"
        f"请检查函数是否被重命名")

    assigns = _name_assignments_in(target)
    assert len(assigns) == 1, (
        f"run_close_review 里对 `name` 的赋值应恰好 1 次（初始化用户名），"
        f"实际 {len(assigns)} 次："
        + "".join(f"\n  {cron_path.name}:{a.lineno} `name = ...`"
                  for a in assigns)
        + "\n多出来的赋值会覆盖用户名，污染后续所有日志与推送。")

    # 那唯一一次赋值必须取的是用户名（profile 的 name 字段），不是基金名
    init_src = ast.get_source_segment(src, assigns[0])
    assert "p.get(\"name\"" in (init_src or "") or "p.get('name'" in (init_src or ""), (
        f"run_close_review 里唯一的 `name = ...` 应是用户名初始化 "
        f"（形如 name = p.get(\"name\", uid)），实际是：{init_src!r}")


def test_cron_alert_render_uses_fund_name():
    """正向断言：持仓预警渲染处应使用 fund_name 局部变量。"""
    cron_path = _BACKEND_ROOT / "scripts" / "stock_monitor_cron.py"
    src = cron_path.read_text(encoding="utf-8")
    assert "fund_name = alert.get(\"name\"" in src or "fund_name = alert.get('name'" in src, (
        f"持仓预警渲染处应使用 fund_name 局部变量，{cron_path} 里没找到")


# ============================================================
# 项目6. load_fund_holdings 未知 user_id 必须打告警
# ============================================================

def test_load_fund_holdings_warns_on_unknown_user(capsys, monkeypatch, tmp_path):
    """传一个不存在的 user_id 时必须打告警，而不是静默返回 []。

    踩坑背景：用户文件按 sha256(user_id)[:16] 命名，而 _fund_file 按用户名原文
    拼文件名。传错成哈希串时整条链路静默返回 []，极易误判成"数据被误删"。
    """
    monkeypatch.setattr(fund_monitor.config, "USERS_DIR", tmp_path / "users")
    (tmp_path / "users").mkdir(parents=True, exist_ok=True)

    result = fund_monitor.load_fund_holdings("definitely_not_a_real_user")

    captured = capsys.readouterr()
    assert result == [], "未知用户仍应返回空（不改成抛异常，避免破坏现有调用方）"
    assert "[FUND_MONITOR]" in captured.out, f"未打告警日志，stdout：\n{captured.out}"
    assert "definitely_not_a_real_user" in captured.out, (
        f"告警里应带上出问题的 user_id，stdout：\n{captured.out}")


def test_load_fund_holdings_no_warn_for_known_user(capsys, monkeypatch, tmp_path):
    """对照用例：user_id 确实存在时**不得**打告警（避免日志噪音淹没真问题）。"""
    uid = "real_user"
    safe = hashlib.sha256(uid.encode()).hexdigest()[:16]
    users_dir = tmp_path / "users"
    users_dir.mkdir(parents=True, exist_ok=True)
    (users_dir / f"{safe}.json").write_text(
        json.dumps({"userId": uid, "portfolio": {"transactions": []}}, ensure_ascii=False),
        encoding="utf-8")

    monkeypatch.setattr(fund_monitor.config, "USERS_DIR", users_dir)
    fund_monitor.load_fund_holdings(uid)

    captured = capsys.readouterr()
    assert "未知 user_id" not in captured.out, (
        f"已知用户不应打「未知 user_id」告警，stdout：\n{captured.out}")


def test_load_fund_holdings_no_warn_for_default(capsys, monkeypatch, tmp_path):
    """对照用例：user_id="default" 是向后兼容路径，不得打告警。"""
    monkeypatch.setattr(fund_monitor.config, "USERS_DIR", tmp_path / "users")
    fund_monitor.load_fund_holdings("default")
    captured = capsys.readouterr()
    assert "未知 user_id" not in captured.out, (
        f"default 用户不应打告警，stdout：\n{captured.out}")


# ============================================================
# G. 管家结论与 AI 诊断的一致性兜底
# ============================================================

def test_steward_says_hold_detection():
    """_steward_says_hold 只在管家判 neutral/未知时才为 True。"""
    from scripts.stock_monitor_cron import _steward_says_hold, _ACTION_WORD_RE

    assert _steward_says_hold({"direction": "neutral"}) is True
    assert _steward_says_hold({}) is True, "字段缺失时保守返回 True（宁可多提示一句）"
    assert _steward_says_hold({"direction": "bullish"}) is False
    assert _steward_says_hold({"direction": "bearish"}) is False
    assert _steward_says_hold(None) is True

    # 动作词正则要能命中事故里的"止盈"
    assert _ACTION_WORD_RE.search("若继续放量下跌，先按纪律止盈一部分")
    assert _ACTION_WORD_RE.search("建议减仓一半") is not None
    # 不能误伤纯描述性文本
    assert _ACTION_WORD_RE.search("今天市场没什么大动静") is None


def test_close_review_prompt_injects_steward_context():
    """诊断 prompt 必须**真的把**管家结论插进去 —— 否则两条链各说各话。

    注意断言的是**使用点**而不是定义点：只检查 `_rv_direction` 存在是不够的
    （定义了却没插进 prompt 同样无效，且静态扫描照样全绿）。所以这里用 AST
    找到 prompt 这个 f-string，确认 `steward_ctx` 确实作为占位符出现在里面。

    故障注入方向：把 prompt 里的 `{steward_ctx}` 删掉，本用例应变红。
    """
    cron_path = _BACKEND_ROOT / "scripts" / "stock_monitor_cron.py"
    src = cron_path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(cron_path))

    prompt_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "prompt":
                    prompt_node = node.value
    assert prompt_node is not None, (
        f"{cron_path} 里找不到 `prompt = ...` 赋值，扫描前提失效")

    assert isinstance(prompt_node, ast.JoinedStr), (
        f"prompt 应是 f-string，实际是 {type(prompt_node).__name__}")

    # f-string 里所有被替换的表达式名
    placeholders = {
        v.value.id
        for v in prompt_node.values
        if isinstance(v, ast.FormattedValue)
        and isinstance(v.value, ast.Name)
    }
    assert "steward_ctx" in placeholders, (
        f"prompt 里必须插值 {{steward_ctx}}（把管家结论喂给 LLM），"
        f"实际插值变量：{sorted(placeholders)}")

    # 指令层面：必须要求 LLM 在有分歧时显式说明
    prompt_text = "".join(
        v.value for v in prompt_node.values if isinstance(v, ast.Constant))
    assert "管家今日结论" in prompt_text, "注入上下文应标明来源是管家"
    assert "显式说明" in prompt_text, (
        "指令里必须要求 LLM 在与管家分歧时显式说明理由，"
        "否则光注入上下文、LLM 依然会各说各话")


def test_push_side_conflict_guard_is_wired():
    """推送侧兜底：管家说观望 + AI 说操作 ⇒ 必须插入冲突提示行。

    同样断言**使用点**：`_steward_says_hold` / `_ACTION_WORD_RE` 定义在那里
    没用，必须真的出现在 if 判断里。

    故障注入方向：把这段 if 删掉，本用例应变红。
    """
    cron_path = _BACKEND_ROOT / "scripts" / "stock_monitor_cron.py"
    src = cron_path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(cron_path))

    # 找到 source 里的 if 表达式（AST 层 robust，不受格式/换行影响）
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test_src = ast.get_source_segment(src, node.test) or ""
        if "_steward_says_hold(" in test_src and "_ACTION_WORD_RE.search(" in test_src:
            found = True
            # 分支体里必须真的往 msg_parts 追加一句冲突提示
            body_src = "".join(ast.get_source_segment(src, b) or ""
                               for b in node.body)
            assert "msg_parts.append" in body_src, (
                f"冲突判断命中后必须往 msg_parts 追加提示，实际分支体：{body_src!r}")
            break

    assert found, (
        f"{cron_path} 里找不到 `_steward_says_hold(review) and "
        f"_ACTION_WORD_RE.search(safe_diag)` 的实际判断，"
        f"推送侧一致性兜底没有接线")

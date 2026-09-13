#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""持仓链「非原子写 → 损坏读空 → 覆盖写」三环修复的故障注入测试。

背景（真实事故形态，非推测）
----------------------------
`stock_monitor` / `fund_monitor` 两条持仓链此前是三环相扣：

  1. `save_*_holdings` 用裸 `f.write_text(json.dumps(...))`（**根因**）——
     写到一半进程被杀 / 磁盘满 → 磁盘上留下半个 JSON。
     本仓铁律：`scripts/daily_push_quality_check.py`、`scripts/ops_analyst.py`
     都明写「JSON 落盘禁止裸 open().write()」。
  2. `load_*_holdings` 的 `except Exception: return []`（**放大器**）——
     「文件损坏」和「这个用户没有持仓」返回同一个 `[]`，调用方无法分辨。
  3. `add_*_holding` 拿到 `[]` → append 一只 → 用这 1 条**覆盖**原文件
     （**执行者**）——原有 N 只持仓永久消失。

本文件锁死修复后的三条性质，每条都注明「摘掉哪一处会转红」，不是死测试：

  A. `load_state` 把 corrupt / missing / ok 三态分开（对应放大器）
  B. 写路径对 corrupt **fail-closed**：原文件字节不变 + 留下 `.corrupt-<ts>` 备份
     （对应执行者；并在 test_guard_is_load_bearing_* 里正向证伪"
     摘掉守卫就会覆盖"）
  C. 落盘是**原子**的：写入中途失败时旧文件完好、不留半截主文件（对应根因；
     换回裸 write_text 时 C 转红）

硬约束：只用 `tmp_path`，不碰 `data/`；不碰 `pages/`。
"""
from __future__ import annotations

import json

import pytest

from services import fund_monitor as fm
from services import holdings_store as hs
from services import persistence
from services import stock_monitor as sm
from services.holdings_store import (
    LOAD_STATE_CORRUPT,
    LOAD_STATE_MISSING,
    LOAD_STATE_OK,
)

# 故意的坏 JSON：缺右花括号 + 截断（模拟"写到一半被杀"）
BROKEN_JSON = '[{"code": "000001", "name": "平安银行", "shares": 10'
LEGACY_HOLDINGS = [{"code": "000001", "name": "平安银行", "costPrice": 10.0, "shares": 100}]


def _isolate_stock(monkeypatch, tmp_path):
    """把股票持仓文件钉到 tmp_path（所有 CRUD 都走 _stock_file → 同一处生效）。"""
    target = tmp_path / "stock_holdings.json"
    monkeypatch.setattr(sm, "_stock_file", lambda user_id="default": target)
    # 行业查询会打网络，测试里禁掉（与本次修复无关）
    monkeypatch.setattr(sm, "_fetch_industry_safe", lambda code: "")
    return target


def _isolate_fund(monkeypatch, tmp_path):
    """把基金持仓文件钉到 tmp_path。"""
    target = tmp_path / "fund_holdings.json"
    monkeypatch.setattr(fm, "_fund_file", lambda user_id="default": target)
    monkeypatch.setattr(fm, "_get_fund_name", lambda code: "测试基金")
    return target


# ============================================================
# A. 三态可区分（放大器）
# ============================================================

def test_load_marks_corrupt_distinct_from_missing(monkeypatch, tmp_path):
    """损坏 与 文件不存在 必须是两种可区分状态。

    故障注入：把 `load_holdings` 里解析失败分支的状态固定成 "ok"
    （即退回旧的 `except: return []` 语义）→ 下面三段断言全部转红。
    """
    f = _isolate_stock(monkeypatch, tmp_path)

    # 1) 文件不存在 → missing
    missing = sm.load_stock_holdings("default")
    assert missing.load_state == LOAD_STATE_MISSING
    assert missing.missing is True and missing.corrupt is False

    # 2) 损坏 → corrupt
    f.write_text(BROKEN_JSON, encoding="utf-8")
    corrupt = sm.load_stock_holdings("default")
    assert corrupt.load_state == LOAD_STATE_CORRUPT
    assert corrupt.corrupt is True and corrupt.missing is False
    assert corrupt.load_state != missing.load_state

    # 3) 合法空数组 → ok（这才是「真的没有持仓」）
    f.write_text("[]", encoding="utf-8")
    ok = sm.load_stock_holdings("default")
    assert ok.load_state == LOAD_STATE_OK
    assert ok.corrupt is False


def test_holdings_list_keeps_legacy_list_semantics(monkeypatch, tmp_path):
    """向后兼容硬要求：仍是 list，`== []` / `bool()` / `len` / 迭代语义不变。

    故障注入：把 HoldingsList 换成普通 list + 一个独立状态变量 → 旧调用方
    仍需能跑，但 `.load_state` 断言转红（本仓已有 60+ 处 `load_*_holdings(...) or []`）。
    """
    f = _isolate_stock(monkeypatch, tmp_path)

    f.write_text("[]", encoding="utf-8")
    empty = sm.load_stock_holdings("default")
    assert isinstance(empty, list) is True
    assert empty == []
    assert bool(empty) is False
    assert len(empty) == 0

    payload = [
        {"code": "000001", "name": "平安银行"},
        {"code": "600519", "name": "贵州茅台"},
    ]
    f.write_text(json.dumps(payload), encoding="utf-8")
    loaded = sm.load_stock_holdings("default")
    assert isinstance(loaded, list) is True
    assert bool(loaded) is True
    assert len(loaded) == 2
    assert loaded == payload
    assert [h["code"] for h in loaded] == ["000001", "600519"]
    # 旧写法 `load_*_holdings(uid) or []` 必须照常工作
    assert (loaded or []) == payload


# ============================================================
# B. 写路径 fail-closed（执行者）
# ============================================================

def test_add_refuses_to_overwrite_corrupt_stock_file(monkeypatch, tmp_path):
    """损坏时 add 必须拒绝写入：返回 error、原文件字节不变、留有 .corrupt 备份。

    故障注入：摘掉 `add_stock_holding` 里的 `corrupt_write_refusal(...)` 三行
    → load 得到 [] → append → 覆盖写 → (b)「原文件字节完全未变」必红。
    """
    f = _isolate_stock(monkeypatch, tmp_path)
    f.write_text(BROKEN_JSON, encoding="utf-8")

    res = sm.add_stock_holding("600519", name="贵州茅台", cost_price=1500.0, shares=10)

    # (a) 约定：返回 {"error": ...}，不抛未捕获异常给路由
    assert isinstance(res, dict) and "error" in res
    assert "损坏" in res["error"]
    assert "未做任何修改" in res["error"]

    # (b) 原文件字节完全未变（仍是那份坏 JSON，没被新内容覆盖）
    assert f.read_text(encoding="utf-8") == BROKEN_JSON

    # (c) 生成了 .corrupt-<ts> 备份，且备份内容 = 原损坏内容
    backups = list(tmp_path.glob("stock_holdings.json.corrupt-*"))
    assert len(backups) == 1, f"应恰有一份损坏备份，实际 {backups}"
    assert backups[0].read_text(encoding="utf-8") == BROKEN_JSON


@pytest.mark.parametrize("op", ["remove", "update"])
def test_remove_and_update_refuse_on_corrupt_stock_file(monkeypatch, tmp_path, op):
    """remove / update 同样 fail-closed（否则会先误报「不在持仓中」再覆盖写）。"""
    f = _isolate_stock(monkeypatch, tmp_path)
    f.write_text(BROKEN_JSON, encoding="utf-8")

    if op == "remove":
        res = sm.remove_stock_holding("000001")
    else:
        res = sm.update_stock_holding("000001", shares=999)

    assert "error" in res and "损坏" in res["error"]
    assert f.read_text(encoding="utf-8") == BROKEN_JSON
    assert list(tmp_path.glob("stock_holdings.json.corrupt-*"))


def test_guard_is_load_bearing_removing_it_destroys_holdings(monkeypatch, tmp_path):
    """正向故障注入：把守卫摘掉 → 坏文件被覆盖 —— 证明上一条测试不是恒真。

    这不是"测试测试"，而是本仓要求的「摘掉判据后必须转红」的自证：
    若 `corrupt_write_refusal` 恒返回 None，`add` 就会把坏文件覆盖成只含新持仓的
    文件（正是线上事故形态）。
    """
    f = _isolate_stock(monkeypatch, tmp_path)
    f.write_text(BROKEN_JSON, encoding="utf-8")
    monkeypatch.setattr(sm, "corrupt_write_refusal", lambda *a, **k: None)

    res = sm.add_stock_holding("600519", name="贵州茅台", cost_price=1500.0, shares=10)

    assert "error" not in res            # 守卫缺失时不会拒绝
    # 坏文件被覆盖：这正是「原有持仓永久消失」的发生方式
    assert f.read_text(encoding="utf-8") != BROKEN_JSON
    assert json.loads(f.read_text(encoding="utf-8"))[0]["code"] == "600519"


def test_add_succeeds_and_stays_valid_on_healthy_file(monkeypatch, tmp_path):
    """对照组：文件健康时 add 必须照常工作（守卫不能把正常路径也拦住）。"""
    f = _isolate_stock(monkeypatch, tmp_path)
    f.write_text(json.dumps(LEGACY_HOLDINGS), encoding="utf-8")

    res = sm.add_stock_holding("600519", name="贵州茅台", cost_price=1500.0, shares=10)

    assert res.get("ok") is True
    on_disk = json.loads(f.read_text(encoding="utf-8"))
    assert [h["code"] for h in on_disk] == ["000001", "600519"]
    assert not list(tmp_path.glob("stock_holdings.json.corrupt-*"))


# ============================================================
# B'. 基金侧同构断言
# ============================================================

def test_fund_load_state_and_write_guard(monkeypatch, tmp_path):
    """基金链同构：corrupt 可区分 + add 拒绝覆盖 + 备份 + 原文件不变。"""
    f = _isolate_fund(monkeypatch, tmp_path)

    # missing
    assert fm.load_fund_holdings("default").load_state == LOAD_STATE_MISSING

    f.write_text(BROKEN_JSON, encoding="utf-8")
    loaded = fm.load_fund_holdings("default")
    assert loaded.load_state == LOAD_STATE_CORRUPT
    assert isinstance(loaded, list) and loaded == []

    res = fm.add_fund_holding("000001", name="测试基金", cost_nav=1.5, shares=1000)

    assert "error" in res and "损坏" in res["error"]
    assert f.read_text(encoding="utf-8") == BROKEN_JSON
    backups = list(tmp_path.glob("fund_holdings.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == BROKEN_JSON

    # 守卫摘掉 → 覆盖（自证）
    monkeypatch.setattr(fm, "corrupt_write_refusal", lambda *a, **k: None)
    res2 = fm.add_fund_holding("000001", name="测试基金", cost_nav=1.5, shares=1000)
    assert "error" not in res2
    assert f.read_text(encoding="utf-8") != BROKEN_JSON


# ============================================================
# C. 原子写（根因）
# ============================================================

def test_save_is_atomic_no_partial_file(monkeypatch, tmp_path):
    """写入失败时旧文件必须完好，且不留半截主文件 / 残渣 tmp。

    故障注入：在原子写的 rename 处注入失败（tmp 已写完、替换前中断）。
      - 原子实现：原文件保持旧内容，tmp 被清理 → 本测试通过；
      - 换回裸 `f.write_text(json.dumps(...))`：没有 rename 这一步，
        注入失效、写入直接成功 → 「旧内容未变」断言转红。
    """
    f = _isolate_stock(monkeypatch, tmp_path)
    good = json.dumps(LEGACY_HOLDINGS)
    f.write_text(good, encoding="utf-8")
    before = f.read_bytes()

    def _boom(*a, **k):
        raise OSError("injected: die before rename")

    # persistence.atomic_write_json 用 os.replace 完成最后一步替换；
    # 在这里注入失败 = 模拟"新内容已完整写入 tmp，但替换前进程死了"。
    monkeypatch.setattr(persistence.os, "replace", _boom)

    raised = False
    try:
        sm.save_stock_holdings(
            LEGACY_HOLDINGS + [{"code": "600519", "name": "贵州茅台"}], "default"
        )
    except OSError:
        raised = True

    # 关键性质 1：旧文件内容 / 字节数完好（裸 write_text 会在这里就把旧内容覆盖掉）
    assert f.read_bytes() == before, "写入失败时旧文件被破坏了（说明落盘不是原子的）"
    assert json.loads(f.read_text(encoding="utf-8")) == LEGACY_HOLDINGS
    # 关键性质 2：失败必须向上抛，不能被静默吞成"保存成功"
    assert raised, "注入的落盘失败必须抛出，不能静默吞掉"
    # 目录里没有半截主文件，也没有残留的 .tmp
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != f.name]
    assert leftovers == [], f"不应留下临时/半截文件，实际: {leftovers}"


def test_save_uses_atomic_write_helper(monkeypatch, tmp_path):
    """锁定实现：save 必须走 holdings_store.save_holdings（原子），而不是裸写。

    故障注入：把 `holdings_store.save_holdings` 换成裸 write_text → 本测试转红
    （这正是我们要防的回退）。
    """
    f = _isolate_stock(monkeypatch, tmp_path)
    calls = []

    real = hs.save_holdings

    def _spy(path, holdings):
        calls.append((path, list(holdings)))
        return real(path, holdings)

    monkeypatch.setattr(sm, "save_holdings", _spy)
    sm.save_stock_holdings(LEGACY_HOLDINGS, "default")

    assert len(calls) == 1, "save_stock_holdings 必须委托给原子写 helper"
    assert calls[0][0] == f
    assert json.loads(f.read_text(encoding="utf-8")) == LEGACY_HOLDINGS

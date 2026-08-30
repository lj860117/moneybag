"""
Phase 3 端到端集成测试 (Phase 3 Batch 4)
================================
测试完整的行为监控、待办、月度快照流程
"""

import pytest
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

# 设置测试环境
TEST_USER_ID = "test_user_phase3"
TEST_DATA_DIR = Path(tempfile.mkdtemp())


@pytest.fixture(scope="function", autouse=True)
def setup_test_env(monkeypatch):
    """设置测试环境"""
    monkeypatch.setenv("DATA_DIR", str(TEST_DATA_DIR))
    
    # 创建测试数据目录
    users_dir = TEST_DATA_DIR / "users"
    users_dir.mkdir(parents=True, exist_ok=True)
    
    yield
    
    # 清理


@pytest.fixture
def clean_user():
    """为每个测试创建干净的用户数据

    FIX 2026-08-30: 原实现把干净数据写到 `users_dir / f"{TEST_USER_ID}.json"`，
    但 persistence.py 实际用的是 **SHA256 哈希文件名**（见 `_user_file()`），
    两者根本不是同一个文件 —— 所以这个 fixture 从来没有真正清理过任何数据，
    todos / behavior_events 会跨测试用例不断累积。

    后果：`test_concurrent_todo_operations` 里的 `assert len(todos) >= 5`
    其实是靠前面几个测试累积出来的额度"蒙"过的，并不是真的验证了并发写入。
    这属于"测试给了虚假安全感"，和 todos 无限膨胀能潜伏 106 天是同一类问题。

    修法：直接复用 persistence 自己的路径函数定位文件（不复刻哈希逻辑），
    并走真实的 save_user() 原子写路径。同时清掉 .bak 备份 ——
    否则 load_user() 会在主文件"看起来干净"时从备份里恢复出脏数据。
    """
    from backend.services.persistence import _user_file, save_user

    user_data = {
        "userId": TEST_USER_ID,
        "portfolio": None,
        "ledger": [],
        "profile": {"name": "Test User"},
        "behavior_events": [],
        "todos": [],
        "monthly_snapshots": {},
    }

    user_file = _user_file(TEST_USER_ID)
    user_file.parent.mkdir(parents=True, exist_ok=True)

    # 清掉可能残留的备份，防止 load_user() 从 .bak 恢复出上一个测试的脏数据
    backup = user_file.with_suffix(".json.bak")
    if backup.exists():
        backup.unlink()

    save_user(user_data)  # 走真实哈希路径 + 原子写

    yield TEST_USER_ID
    
    # 清理
    if user_file.exists():
        user_file.unlink()


class TestPhase3EndToEnd:
    """Phase 3 端到端测试套件"""
    
    def test_create_behavior_event_flow(self, clean_user):
        """测试: 创建行为事件流"""
        from backend.services.behavior_recorder import record_behavior_event, get_behavior_events
        
        # Step 1: 记录一条交易事件
        event = record_behavior_event(
            TEST_USER_ID,
            trade_details={
                "code": "000001",
                "direction": "buy",
                "amount": 1000,
                "price": 10.5
            },
            patterns_detected=["chasing_high"],
            market_context={"fgi_score": 75},
        )
        
        assert event is not None
        assert event["trade_details"]["code"] == "000001"
        assert "chasing_high" in event["patterns_detected"]
        
        # Step 2: 验证事件已保存
        events = get_behavior_events(TEST_USER_ID, limit=10)
        assert len(events) > 0
        assert events[0]["trade_details"]["code"] == "000001"
    
    def test_create_todo_flow(self, clean_user):
        """测试: 创建待办流程"""
        from backend.services.todo_manager import create_todo, get_todos, mark_done
        
        # Step 1: 创建待办
        todo = create_todo(
            TEST_USER_ID,
            title="测试任务",
            rule_triggered="allocation_deviation_gt_15",
            due_by_days=7,
        )
        
        assert todo is not None
        assert todo["title"] == "测试任务"
        assert todo["status"] == "open"
        
        # Step 2: 获取待办列表
        todos = get_todos(TEST_USER_ID)
        assert len(todos) > 0
        assert any(t["title"] == "测试任务" for t in todos)
        
        # Step 3: 完成待办
        result = mark_done(TEST_USER_ID, todo["id"])
        assert result is not None
        assert result["status"] == "completed"
    
    def test_monthly_snapshot_flow(self, clean_user):
        """测试: 月度快照流程"""
        from backend.services.monthly_snapshot import (
            save_monthly_snapshot,
            get_monthly_snapshots,
            get_monthly_trend
        )
        
        # Mock get_unified_networth
        with patch("backend.services.portfolio_overview.get_unified_networth") as mock_nw:
            mock_nw.return_value = {
                "netWorth": 1000000,
                "breakdown": {
                    "investment": {"total": 600000},
                    "cash": {"total": 200000},
                    "property": {"total": 200000},
                }
            }
            
            # Step 1: 保存快照
            snapshot = save_monthly_snapshot(TEST_USER_ID)
            assert snapshot is not None
            assert snapshot["net_worth"] == 1000000
            
            # Step 2: 获取快照列表
            snapshots = get_monthly_snapshots(TEST_USER_ID, months=12)
            assert len(snapshots) > 0
            
            # Step 3: 获取趋势数据
            trend = get_monthly_trend(TEST_USER_ID, months=12)
            assert len(trend) > 0
    
    def test_behavior_pattern_detection(self, clean_user):
        """测试: 行为模式检测"""
        from backend.services.behavior_recorder import (
            record_behavior_event,
            get_events_by_pattern,
            get_event_count_today
        )
        
        # 记录多条事件，包含不同的模式
        patterns_list = ["fomo", "chasing_high", "panic_selling"]
        
        for pattern in patterns_list:
            record_behavior_event(
                TEST_USER_ID,
                trade_details={"code": "000001", "direction": "buy", "amount": 1000},
                patterns_detected=[pattern],
            )
        
        # 按模式过滤
        fomo_events = get_events_by_pattern(TEST_USER_ID, "fomo")
        assert len(fomo_events) > 0
        assert all("fomo" in e["patterns_detected"] for e in fomo_events)
        
        # 检查今日统计
        today_count = get_event_count_today(TEST_USER_ID)
        assert today_count >= len(patterns_list)
    
    def test_todo_rule_triggered(self, clean_user):
        """测试: 待办规则触发"""
        from backend.services.todo_manager import create_todo, get_todos
        
        # 创建多条带不同规则的待办
        rules = [
            "allocation_deviation_gt_15",
            "weekly_review",
            "accounting_overdue",
            "behavior_alert_fomo",
        ]
        
        for rule in rules:
            create_todo(
                TEST_USER_ID,
                title=f"测试任务 {rule}",
                rule_triggered=rule,
                due_by_days=7,
            )
        
        # 验证所有待办已创建
        todos = get_todos(TEST_USER_ID)
        assert len(todos) >= len(rules)
        
        # 验证规则已保存
        for todo in todos:
            assert todo["rule_triggered"] in rules
    
    def test_api_integration_todos(self):
        """测试: Todos API 集成"""
        from fastapi.testclient import TestClient
        from backend.main import app
        
        client = TestClient(app)
        
        # GET /api/todos
        response = client.get(f"/api/todos?userId={TEST_USER_ID}")
        assert response.status_code == 200
        data = response.json()
        assert "todos" in data
        assert "open_count" in data
    
    def test_api_integration_behavior(self):
        """测试: Behavior Tracking API 集成"""
        from fastapi.testclient import TestClient
        from backend.main import app
        
        client = TestClient(app)
        
        # GET /api/behavior/events
        response = client.get(f"/api/behavior/events?userId={TEST_USER_ID}")
        assert response.status_code == 200
        data = response.json()
        assert "events" in data
        assert "today_count" in data
        
        # POST /api/behavior/record
        response = client.post(
            f"/api/behavior/record?userId={TEST_USER_ID}",
            json={
                "trade_details": {
                    "code": "000001",
                    "direction": "buy",
                    "amount": 1000,
                    "price": 10.5
                },
                "patterns_detected": ["chasing_high"],
                "market_context": {"fgi_score": 75}
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
    
    def test_api_integration_monthly(self):
        """测试: Monthly Rebalance API 集成"""
        from fastapi.testclient import TestClient
        from backend.main import app
        
        client = TestClient(app)
        
        # GET /api/monthly/snapshots
        response = client.get(f"/api/monthly/snapshots?userId={TEST_USER_ID}")
        assert response.status_code == 200
        data = response.json()
        assert "snapshots" in data
        
        # GET /api/monthly/latest
        response = client.get(f"/api/monthly/latest?userId={TEST_USER_ID}")
        assert response.status_code == 200
        data = response.json()
        # snapshot 可能为空
        assert "snapshot" in data
    
    def test_concurrent_todo_operations(self, clean_user):
        """测试: 并发待办操作（确保原子性 / 不丢更新）

        FIX 2026-08-30: 原实现让 5 个线程用**同一个** rule_triggered="test"，
        把两件完全不同的事混在一起测了：
          - 并发写入不丢更新（本测试的真正意图，见方法名"确保原子性"）
          - 幂等去重（同规则只留 1 条 —— 这是**正确**行为，不是 bug）
        结果是 `assert len(todos) >= 5` 永远说不清该期望什么。

        更糟的是：修好 clean_user fixture 之前，这个断言是靠前面几个测试
        累积下来的脏数据"蒙"过的，而它本该发现的**丢更新**缺陷被完全掩盖 ——
        实测 5 线程即使用 5 个不同规则也只有 1 条落盘，另外 4 条被静默覆盖。

        现在改成 5 个**不同**规则，纯粹验证"并发写入不丢更新"。
        幂等语义由下面的 test_concurrent_same_rule_is_idempotent 单独覆盖。
        """
        from backend.services.todo_manager import create_todo, get_todos
        import threading

        results = []

        def create_todo_thread():
            name = threading.current_thread().name
            try:
                todo = create_todo(
                    TEST_USER_ID,
                    title=f"并发任务 {name}",
                    # 每个线程用不同规则 → 绕开幂等，只测并发写入
                    rule_triggered=f"concurrent_test_{name}",
                )
                results.append(todo)
            except Exception:
                results.append(None)

        # 创建 5 个并发线程
        threads = [
            threading.Thread(target=create_todo_thread, name=f"worker{i}")
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 验证所有待办都创建成功
        assert len([r for r in results if r is not None]) == 5

        # 验证数据库中有所有待办（不丢更新 —— 靠 user_write_lock 保证）
        todos = get_todos(TEST_USER_ID)
        assert len(todos) >= 5, (
            f"并发写入丢更新：期望 >= 5 条，实际 {len(todos)} 条。"
            f"检查 services/persistence.py 的 user_write_lock 是否覆盖了"
            f"整个 load-modify-save 临界区"
        )
        # 5 个规则应该各留一条，互不覆盖
        rules = {t["rule_triggered"] for t in todos}
        assert len(rules) >= 5, f"规则种类应 >= 5，实际 {len(rules)}: {rules}"

    def test_concurrent_same_rule_is_idempotent(self, clean_user):
        """测试: 并发下的幂等去重（同一规则最终恰好 1 条）

        新增 2026-08-30，与 test_concurrent_todo_operations 配对：
        那个测"并发不丢更新"（5 个不同规则 → 5 条都在），
        这个测"并发下幂等仍然成立"（同一规则 → 恰好 1 条，不多不少）。

        "恰好 1 条"同时验证了两件事：
          - 幂等生效（不是 5 条）
          - 幂等检查在锁内（不是 2~4 条 —— 若判重在锁外，
            多个线程会同时判定"不存在"然后各建一条）
        """
        from backend.services.todo_manager import create_todo, get_todos
        import threading

        results = []

        def worker():
            try:
                results.append(create_todo(
                    TEST_USER_ID,
                    title="并发同规则任务",
                    rule_triggered="weekly_review",
                    due_by_days=3,
                ))
            except Exception:
                results.append(None)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 幂等命中也返回 dict（不是 None），调用方 `if todo_obj:` 才不会误判
        assert all(r is not None for r in results), "幂等命中时不应返回 None"

        todos = [
            t for t in get_todos(TEST_USER_ID)
            if t["rule_triggered"] == "weekly_review"
        ]
        assert len(todos) == 1, (
            f"同规则并发应恰好留 1 条，实际 {len(todos)} 条。"
            f"若 > 1，说明幂等检查没有在 user_write_lock 内执行"
        )
        # 所有线程应该拿到同一条
        assert len({r["id"] for r in results}) == 1, "所有线程应拿到同一条待办"
    
    def test_data_persistence_integrity(self, clean_user):
        """测试: 数据持久化完整性"""
        from backend.services.persistence import load_user, save_user
        
        # 加载用户
        user_data = load_user(TEST_USER_ID)
        
        # 修改数据
        user_data["test_field"] = "test_value"
        save_user(user_data)
        
        # 重新加载验证
        reloaded = load_user(TEST_USER_ID)
        assert reloaded.get("test_field") == "test_value"
        
        # 验证 Phase 3 字段仍然存在
        assert "behavior_events" in reloaded
        assert "todos" in reloaded
        assert "monthly_snapshots" in reloaded


class TestPhase3MigrationScript:
    """Phase 3 迁移脚本测试"""
    
    def test_migration_dry_run(self):
        """测试: 迁移 dry-run 模式"""
        from backend.scripts.migrate_phase3 import run_migration
        
        result = run_migration(dry_run=True)
        
        assert result["success"] is True
        assert result["total"] >= 0
        # Dry-run 不应该实际迁移任何内容
        if result["total"] > 0:
            assert result["migrated"] == 0
    
    def test_monthly_close_script(self):
        """测试: 月度关闭脚本"""
        from backend.scripts.monthly_close import run_monthly_close
        
        result = run_monthly_close()
        
        assert result["success"] is True
        assert "stats" in result
        assert "snapshots_saved" in result["stats"]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

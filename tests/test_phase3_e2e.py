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
    """为每个测试创建干净的用户数据"""
    from backend.services.persistence import load_user, save_user
    
    user_data = {
        "userId": TEST_USER_ID,
        "profile": {"name": "Test User"},
        "behavior_events": [],
        "todos": [],
        "monthly_snapshots": {}
    }
    
    users_dir = TEST_DATA_DIR / "users"
    user_file = users_dir / f"{TEST_USER_ID}.json"
    user_file.write_text(json.dumps(user_data), encoding="utf-8")
    
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
        """测试: 并发待办操作（确保原子性）"""
        from backend.services.todo_manager import create_todo, get_todos
        import threading
        
        results = []
        
        def create_todo_thread():
            try:
                todo = create_todo(
                    TEST_USER_ID,
                    title=f"并发任务 {threading.current_thread().name}",
                    rule_triggered="test",
                )
                results.append(todo)
            except Exception as e:
                results.append(None)
        
        # 创建 5 个并发线程
        threads = [threading.Thread(target=create_todo_thread) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # 验证所有待办都创建成功
        assert len([r for r in results if r is not None]) == 5
        
        # 验证数据库中有所有待办
        todos = get_todos(TEST_USER_ID)
        assert len(todos) >= 5
    
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

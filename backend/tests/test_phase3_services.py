"""
Phase 3 Service Unit Tests
==========================
测试 persistence、behavior_recorder、todo_manager、monthly_snapshot
"""
import pytest
import tempfile
import json
from datetime import datetime, timedelta
from pathlib import Path

# Mock config for testing
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.persistence import load_user, save_user, _init_phase3_fields, atomic_write_json
from services.behavior_recorder import (
    record_behavior_event, get_behavior_events, get_recent_event,
    get_events_by_pattern, get_event_count_today,
)
from services.todo_manager import (
    create_todo, update_todo, delete_todo, get_todos, get_todo_by_id,
    mark_done, mark_skipped, get_overdue_todos, get_open_count,
)
from services.monthly_snapshot import (
    save_monthly_snapshot, get_monthly_snapshots, get_snapshot_latest,
)


class TestPersistence:
    """Test persistence module"""
    
    def test_init_phase3_fields(self):
        """Test that Phase 3 fields are properly initialized"""
        data = {"userId": "test_user", "portfolio": None}
        data = _init_phase3_fields(data)
        
        assert "behavior_events" in data
        assert "todos" in data
        assert "monthly_snapshots" in data
        assert data["behavior_events"] == []
        assert data["todos"] == []
        assert data["monthly_snapshots"] == {}
    
    def test_init_phase3_fields_idempotent(self):
        """Test that initialization is idempotent"""
        data = {"userId": "test_user"}
        data = _init_phase3_fields(data)
        data = _init_phase3_fields(data)  # Call twice
        
        assert len(data["behavior_events"]) == 0
        assert len(data["todos"]) == 0


class TestBehaviorRecorder:
    """Test behavior_recorder module"""
    
    def test_record_behavior_event(self):
        """Test recording a behavior event"""
        user_id = "test_user_123"
        trade_details = {
            "code": "000001",
            "direction": "buy",
            "amount": 10000,
            "price": 8.50,
        }
        
        event = record_behavior_event(
            user_id,
            trade_details,
            patterns_detected=["chasing_high"],
            market_context={"fgi": 75},
        )
        
        assert event["event_type"] == "trade_executed"
        assert event["trade_details"]["code"] == "000001"
        assert "chasing_high" in event["patterns_detected"]
        assert "timestamp" in event
    
    def test_get_behavior_events(self):
        """Test retrieving behavior events"""
        user_id = "test_user_events"
        
        # Record 3 events
        for i in range(3):
            record_behavior_event(
                user_id,
                {"code": f"00000{i}", "direction": "buy", "amount": 1000},
            )
        
        events = get_behavior_events(user_id)
        assert len(events) == 3
    
    def test_get_recent_event(self):
        """Test getting the most recent event"""
        user_id = "test_user_recent"
        
        record_behavior_event(user_id, {"code": "000001", "direction": "buy"})
        record_behavior_event(user_id, {"code": "000002", "direction": "sell"})
        
        recent = get_recent_event(user_id)
        assert recent is not None
        assert recent["trade_details"]["code"] == "000002"
    
    def test_get_events_by_pattern(self):
        """Test filtering events by pattern"""
        user_id = "test_user_pattern"
        
        record_behavior_event(
            user_id,
            {"code": "000001", "direction": "buy"},
            patterns_detected=["chasing_high"],
        )
        record_behavior_event(
            user_id,
            {"code": "000002", "direction": "buy"},
            patterns_detected=["fomo"],
        )
        
        chasing_events = get_events_by_pattern(user_id, "chasing_high")
        assert len(chasing_events) == 1
        assert "chasing_high" in chasing_events[0]["patterns_detected"]
    
    def test_event_limit_500(self):
        """Test that only 500 events are retained"""
        user_id = "test_user_limit"
        
        # Record 600 events
        for i in range(600):
            record_behavior_event(
                user_id,
                {"code": f"{i:06d}", "direction": "buy"},
            )
        
        # Should only have 500
        events = get_behavior_events(user_id, limit=600)
        assert len(events) == 500


class TestTodoManager:
    """Test todo_manager module"""
    
    def test_create_todo(self):
        """Test creating a todo"""
        user_id = "test_todo_user"
        
        todo = create_todo(
            user_id,
            "检查配置",
            "allocation_deviation_gt_15",
            due_by_days=7,
        )
        
        assert todo["title"] == "检查配置"
        assert todo["status"] == "open"
        assert todo["rule_triggered"] == "allocation_deviation_gt_15"
        assert "id" in todo
        assert todo["id"].startswith("todo_")
    
    def test_get_todos(self):
        """Test retrieving todos"""
        user_id = "test_get_todos"
        
        create_todo(user_id, "待办1", "rule1")
        create_todo(user_id, "待办2", "rule2")
        create_todo(user_id, "待办3", "rule3")
        
        todos = get_todos(user_id)
        assert len(todos) == 3
    
    def test_mark_done(self):
        """Test marking todo as done"""
        user_id = "test_mark_done"
        
        todo = create_todo(user_id, "待办", "rule1")
        
        updated = mark_done(user_id, todo["id"])
        assert updated["status"] == "completed"
    
    def test_delete_todo(self):
        """Test deleting a todo"""
        user_id = "test_delete"
        
        todo = create_todo(user_id, "待办", "rule1")
        
        deleted = delete_todo(user_id, todo["id"])
        assert deleted is True
        
        # Verify it's gone
        todos = get_todos(user_id)
        assert len(todos) == 0
    
    def test_get_overdue_todos(self):
        """Test getting overdue todos"""
        user_id = "test_overdue"
        
        # Create a todo that expired yesterday
        user_data = load_user(user_id)
        todo = {
            "id": "test_todo",
            "title": "Overdue",
            "rule_triggered": "rule1",
            "created_at": (datetime.now() - timedelta(days=2)).isoformat(),
            "due_by": (datetime.now() - timedelta(days=1)).isoformat(),
            "status": "open",
            "metadata": {},
        }
        if "todos" not in user_data:
            user_data["todos"] = []
        user_data["todos"].append(todo)
        save_user(user_data)
        
        overdue = get_overdue_todos(user_id)
        assert len(overdue) > 0
        assert overdue[0]["id"] == "test_todo"
    
    def test_get_open_count(self):
        """Test counting open todos"""
        user_id = "test_count"
        
        create_todo(user_id, "待办1", "rule1")
        create_todo(user_id, "待办2", "rule2")
        
        count = get_open_count(user_id)
        assert count == 2


class TestMonthlySnapshot:
    """Test monthly_snapshot module"""
    
    def test_get_monthly_snapshots(self):
        """Test retrieving monthly snapshots"""
        user_id = "test_snapshot_user"
        
        # Create test data with snapshots
        user_data = load_user(user_id)
        user_data["monthly_snapshots"] = {
            "2026-04": {"net_worth": 100000, "recorded_at": "2026-04-01T00:00:00"},
            "2026-05": {"net_worth": 105000, "recorded_at": "2026-05-01T00:00:00"},
        }
        save_user(user_data)
        
        snapshots = get_monthly_snapshots(user_id, months=2)
        assert len(snapshots) == 2
        assert snapshots[0]["month"] == "2026-04"
        assert snapshots[1]["month"] == "2026-05"
    
    def test_get_snapshot_latest(self):
        """Test getting the latest snapshot"""
        user_id = "test_latest_snapshot"
        
        user_data = load_user(user_id)
        user_data["monthly_snapshots"] = {
            "2026-04": {"net_worth": 100000},
            "2026-05": {"net_worth": 105000},
        }
        save_user(user_data)
        
        latest = get_snapshot_latest(user_id)
        assert latest is not None
        assert latest["month"] == "2026-05"
        assert latest["net_worth"] == 105000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

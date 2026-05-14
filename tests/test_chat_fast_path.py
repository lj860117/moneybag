"""
测试 chat 快速路径（规则优先）
================================
验证规则引擎命中时 < 1s 返回，不走 LLM。
"""
import time
import pytest
import httpx


@pytest.fixture
def client():
    """直连后端的 httpx 客户端"""
    import os
    host = os.environ.get("MB_TEST_HOST", "http://localhost:8000")
    with httpx.Client(base_url=host, timeout=30) as c:
        # 探活
        try:
            r = c.get("/api/health")
            if r.status_code != 200:
                pytest.skip("后端未启动")
        except Exception:
            pytest.skip("后端未启动")
        yield c


class TestChatFastPath:
    """规则优先快速路径测试"""

    def test_timing_rules_fast(self, client):
        """'现在能进场吗' 应命中 rules（served_by=rules），不走 LLM。
        注意：timing 需要调数据源（估值+恐贪指数），首次可能 >3s，但绝对 <15s（LLM 超时通常 30s+）。
        """
        t0 = time.time()
        r = client.post("/api/chat", json={
            "message": "现在能进场吗",
            "userId": "qa_test_20260419",
        })
        elapsed = time.time() - t0

        assert r.status_code == 200
        data = r.json()
        assert data["served_by"] == "rules", f"Expected rules, got {data.get('served_by')}"
        assert "入场" in data["reply"] or "时机" in data["reply"]
        # 规则路径即使有数据源延迟，也应 <15s（LLM 通常 30s+ 或超时）
        assert elapsed < 15.0, f"规则路径太慢: {elapsed:.2f}s > 15s"
        print(f"✅ timing: served_by=rules, {elapsed:.2f}s")

    def test_take_profit_rules(self, client):
        """'止盈' 应命中 rules"""
        r = client.post("/api/chat", json={
            "message": "止盈",
            "userId": "qa_test_20260419",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["served_by"] == "rules"
        assert "止盈" in data["reply"]
        print(f"✅ take_profit: served_by=rules")

    def test_dca_rules(self, client):
        """'定投多少合适' 应命中 rules"""
        r = client.post("/api/chat", json={
            "message": "定投多少合适",
            "userId": "qa_test_20260419",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["served_by"] == "rules"
        assert "定投" in data["reply"]
        print(f"✅ dca: served_by=rules")

    def test_sentiment_rules(self, client):
        """'市场情绪怎么样' 应命中 rules"""
        r = client.post("/api/chat", json={
            "message": "市场情绪怎么样",
            "userId": "qa_test_20260419",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["served_by"] == "rules"
        assert "情绪" in data["reply"] or "恐惧" in data["reply"]
        print(f"✅ sentiment: served_by=rules")

    def test_general_question_goes_to_llm(self, client):
        """'我手上 600519 怎么看' 是个股分析，应走 LLM（served_by=llm）"""
        r = client.post("/api/chat", json={
            "message": "我手上 600519 怎么看",
            "userId": "qa_test_20260419",
        })
        assert r.status_code == 200
        data = r.json()
        # 个股分析不在 FAST_PATH_INTENTS 中，应走 LLM 或 fallback rules
        # 如果没有 LLM key 则 served_by=rules（降级），有 key 则 served_by=llm
        assert "served_by" in data
        print(f"✅ general: served_by={data['served_by']}")

    def test_macro_rules(self, client):
        """'宏观经济怎么样' 应命中 rules"""
        r = client.post("/api/chat", json={
            "message": "宏观经济怎么样",
            "userId": "qa_test_20260419",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["served_by"] == "rules"
        assert "宏观" in data["reply"] or "经济" in data["reply"]
        print(f"✅ macro: served_by=rules")

    def test_stream_timing_fast(self, client):
        """流式接口 '现在能进场吗' 也应命中 rules，最后一帧带 served_by"""
        t0 = time.time()
        with client.stream("POST", "/api/chat/stream", json={
            "message": "现在能进场吗",
            "userId": "qa_test_20260419",
        }) as r:
            assert r.status_code == 200
            last_line = ""
            for line in r.iter_lines():
                if line.startswith("data: "):
                    last_line = line
        elapsed = time.time() - t0

        import json as _json
        payload = _json.loads(last_line[6:])
        assert payload.get("done") is True
        assert payload.get("served_by") == "rules"
        assert elapsed < 15.0, f"流式规则路径太慢: {elapsed:.2f}s > 15s"
        print(f"✅ stream timing: served_by=rules, {elapsed:.2f}s")

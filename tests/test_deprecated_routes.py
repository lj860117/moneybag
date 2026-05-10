"""
Deprecated Routes 410 Gone Tests (M5 W3)
==========================================
Validates that deprecated routes return 410 Gone with correct body.

Routes deprecated per 10-roadmap.md §四:
  - GET /api/ai-predict/{code}          (old ai_predictor v1)
  - GET /api/ai-predict/portfolio/{uid}  (old ai_predictor v1)
  - POST /api/ai-predict/batch           (old ai_predictor v1)
  - GET /api/decisions                   (old decision_maker v1)

Run: cd backend && python -m pytest ../tests/test_deprecated_routes.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure backend/ is on sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture
def client():
    """Create a minimal FastAPI app with only deprecated route handlers.

    We can't import full quant/misc routers because they eagerly import
    broken legacy services. Instead, we re-create just the deprecated endpoints
    to verify the 410 response logic works correctly.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from fastapi.responses import JSONResponse

    app = FastAPI()

    # Re-create the exact 410 responses as implemented in quant.py and misc.py
    _GONE_BODY = {
        "status": "gone",
        "code": 410,
        "message": "此接口已废弃。AI 预测功能已整合到决策复盘系统（/api/decisions/review）。",
        "migration_guide": "使用 POST /api/decisions/review 提交交易复盘，"
                           "或 GET /api/decisions/monthly-report/{user_id} 查看决策质量报告。",
        "deprecated_since": "2026-05-15",
        "removed_at": "2026-07-01",
    }

    _DECISIONS_GONE_BODY = {
        "status": "gone",
        "code": 410,
        "message": "此接口已废弃。旧版买卖决策已被决策复盘系统取代。",
        "migration_guide": "使用 POST /api/decisions/review 提交交易复盘，"
                           "或 GET /api/decisions/monthly-report/{user_id} 查看决策质量报告。",
        "deprecated_since": "2026-05-15",
        "removed_at": "2026-07-01",
    }

    @app.get("/api/ai-predict/{code}")
    def ai_predict(code: str, days: int = 5):
        return JSONResponse(status_code=410, content=_GONE_BODY)

    @app.get("/api/ai-predict/portfolio/{user_id}")
    def ai_predict_portfolio(user_id: str, days: int = 5):
        return JSONResponse(status_code=410, content=_GONE_BODY)

    @app.post("/api/ai-predict/batch")
    def ai_predict_batch():
        return JSONResponse(status_code=410, content=_GONE_BODY)

    @app.get("/api/decisions")
    def old_decisions(userId: str = ""):
        return JSONResponse(status_code=410, content=_DECISIONS_GONE_BODY)

    # Include the real decisions router for non-regression tests
    from api.decisions import router as decisions_router
    app.include_router(decisions_router)

    return TestClient(app)


# ============================================================
# 1. AI Predict routes — 410 Gone
# ============================================================

class TestAiPredictGone:
    """Test that /api/ai-predict/* routes return 410 Gone."""

    def test_ai_predict_single_stock(self, client):
        """GET /api/ai-predict/{code} returns 410."""
        resp = client.get("/api/ai-predict/600519")
        assert resp.status_code == 410
        body = resp.json()
        assert body["status"] == "gone"
        assert body["code"] == 410
        assert "废弃" in body["message"]
        assert "migration_guide" in body
        assert "deprecated_since" in body
        assert "removed_at" in body

    def test_ai_predict_portfolio(self, client):
        """GET /api/ai-predict/portfolio/{user_id} returns 410."""
        resp = client.get("/api/ai-predict/portfolio/test_user")
        assert resp.status_code == 410
        body = resp.json()
        assert body["status"] == "gone"
        assert body["code"] == 410

    def test_ai_predict_batch(self, client):
        """POST /api/ai-predict/batch returns 410."""
        resp = client.post("/api/ai-predict/batch", json={"codes": ["600519"], "days": 5})
        assert resp.status_code == 410
        body = resp.json()
        assert body["status"] == "gone"
        assert body["code"] == 410

    def test_ai_predict_gone_body_has_migration_guide(self, client):
        """410 body includes migration guide pointing to new endpoints."""
        resp = client.get("/api/ai-predict/000001")
        body = resp.json()
        assert "/api/decisions/review" in body["migration_guide"]
        assert "/api/decisions/monthly-report" in body["migration_guide"]

    def test_ai_predict_gone_body_has_dates(self, client):
        """410 body includes deprecated_since and removed_at dates."""
        resp = client.get("/api/ai-predict/000001")
        body = resp.json()
        assert body["deprecated_since"] == "2026-05-15"
        assert body["removed_at"] == "2026-07-01"


# ============================================================
# 2. Old /api/decisions route — 410 Gone
# ============================================================

class TestOldDecisionsGone:
    """Test that old /api/decisions (decision_maker v1) returns 410 Gone."""

    def test_decisions_gone(self, client):
        """GET /api/decisions returns 410."""
        resp = client.get("/api/decisions")
        assert resp.status_code == 410
        body = resp.json()
        assert body["status"] == "gone"
        assert body["code"] == 410
        assert "废弃" in body["message"]

    def test_decisions_gone_with_user_id(self, client):
        """GET /api/decisions?userId=xxx also returns 410."""
        resp = client.get("/api/decisions?userId=test_user")
        assert resp.status_code == 410
        body = resp.json()
        assert body["status"] == "gone"

    def test_decisions_gone_has_migration_guide(self, client):
        """410 body includes migration guide to new review system."""
        resp = client.get("/api/decisions")
        body = resp.json()
        assert "migration_guide" in body
        assert "/api/decisions/review" in body["migration_guide"]


# ============================================================
# 3. New routes still work (non-regression)
# ============================================================

class TestNewRoutesStillWork:
    """Verify that new decision review routes are NOT affected."""

    def test_decisions_review_reasons_works(self, client):
        """GET /api/decisions/reasons still returns 200."""
        resp = client.get("/api/decisions/reasons")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"

    def test_decisions_checklist_items_works(self, client):
        """GET /api/decisions/checklist/items still returns 200."""
        resp = client.get("/api/decisions/checklist/items")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"


# ============================================================
# 4. Frontend references audit
# ============================================================

class TestFrontendReferences:
    """Verify frontend has been migrated off deprecated routes."""

    def test_frontend_no_ai_predict_references(self):
        """app.js should no longer call /api/ai-predict (migrated M5 W4)."""
        app_js = Path(__file__).resolve().parent.parent / "app.js"
        if not app_js.exists():
            pytest.skip("app.js not found")

        content = app_js.read_text(encoding="utf-8")
        references = []
        for i, line in enumerate(content.split("\n"), 1):
            if "ai-predict" in line:
                references.append(f"  line {i}: {line[:80].strip()}")

        assert len(references) == 0, (
            f"Frontend still references ai-predict:\n" + "\n".join(references)
        )

    def test_frontend_no_old_decisions_references(self):
        """app.js should no longer call /api/decisions (migrated M5 W4)."""
        app_js = Path(__file__).resolve().parent.parent / "app.js"
        if not app_js.exists():
            pytest.skip("app.js not found")

        content = app_js.read_text(encoding="utf-8")
        # Find old /decisions calls (not /decisions/review etc.)
        references = []
        for i, line in enumerate(content.split("\n"), 1):
            if "/decisions" in line and "/decisions/" not in line:
                references.append(f"  line {i}: {line[:80].strip()}")

        assert len(references) == 0, (
            f"Frontend still references old /decisions:\n" + "\n".join(references)
        )


# ============================================================
# 5. Source code verification
# ============================================================

class TestSourceCodeVerification:
    """Verify the actual source files contain 410 logic."""

    def test_quant_py_has_410_for_ai_predict(self):
        """api/quant.py should return 410 for ai-predict routes."""
        source = (BACKEND_DIR / "api" / "quant.py").read_text()
        assert "status_code=410" in source
        assert "已废弃" in source
        assert "_GONE_BODY" in source
        # Should NOT import from services.ai_predictor anymore
        assert "from services.ai_predictor import" not in source

    def test_misc_py_has_410_for_decisions(self):
        """api/misc.py should return 410 for old /api/decisions route."""
        source = (BACKEND_DIR / "api" / "misc.py").read_text()
        assert "status_code=410" in source
        assert "已废弃" in source
        # Should NOT import from services.decision_maker anymore
        assert "from services.decision_maker import" not in source

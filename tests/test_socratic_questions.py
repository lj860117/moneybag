"""
Socratic Questions Tests (M4 W4)
==================================
Validates the Socratic questioning system:
  1. Domain models (QuestionTemplate, SocraticQuestion, SocraticSession)
  2. Protocol satisfaction (QuestionBankProtocol)
  3. Service layer (derive_triggers, select_questions, build_socratic_session)
  4. Infra layer (JsonQuestionBank loads templates)
  5. Use case (generate_socratic_questions end-to-end)
  6. Invariants (domain zero infra import, service pure functions)

Run: cd backend && python -m pytest ../tests/test_socratic_questions.py -v
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

# Ensure backend/ is on sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ============================================================
# 1. Domain Models
# ============================================================

class TestQuestionModels:
    """Test domain model dataclasses."""

    def test_question_template_from_dict(self):
        from domain.models.questions import QuestionTemplate

        data = {
            "id": "q_test",
            "category": "risk_tolerance",
            "text": "如果跌 30% 你怎么办？",
            "applicable_when": ["any_buy", "high_volatility"],
            "weight": 9,
        }
        tmpl = QuestionTemplate.from_dict(data)
        assert tmpl.id == "q_test"
        assert tmpl.category == "risk_tolerance"
        assert tmpl.weight == 9
        assert "any_buy" in tmpl.applicable_when

    def test_question_template_to_dict_roundtrip(self):
        from domain.models.questions import QuestionTemplate

        data = {
            "id": "q_rt",
            "category": "time_horizon",
            "text": "几年后要用？",
            "applicable_when": ["any_buy"],
            "weight": 7,
        }
        tmpl = QuestionTemplate.from_dict(data)
        out = tmpl.to_dict()
        assert out == data

    def test_socratic_question_to_dict(self):
        from domain.models.questions import SocraticQuestion

        q = SocraticQuestion(
            template_id="q_test",
            category="risk_tolerance",
            text="如果跌 30% 你怎么办？",
            relevance_score=25.5,
        )
        d = q.to_dict()
        assert d["template_id"] == "q_test"
        assert d["relevance_score"] == 25.5
        assert d["text"] == "如果跌 30% 你怎么办？"

    def test_socratic_session_to_dict(self):
        from domain.models.questions import SocraticQuestion, SocraticSession

        session = SocraticSession(
            user_id="user_1",
            action="buy",
            questions=[
                SocraticQuestion("q1", "risk", "问题1", 20.0),
                SocraticQuestion("q2", "time", "问题2", 15.0),
            ],
            context_summary="action=buy, triggers=[any_buy]",
        )
        d = session.to_dict()
        assert d["user_id"] == "user_1"
        assert d["question_count"] == 2
        assert len(d["questions"]) == 2

    def test_question_category_enum(self):
        from domain.models.questions import QuestionCategory

        assert QuestionCategory.TIME_HORIZON == "time_horizon"
        assert QuestionCategory.BEHAVIORAL_BIAS == "behavioral_bias"
        assert len(QuestionCategory) >= 15


# ============================================================
# 2. Protocol
# ============================================================

class TestQuestionBankProtocol:
    """Test that JsonQuestionBank satisfies QuestionBankProtocol."""

    def test_protocol_is_runtime_checkable(self):
        from domain.protocols.question_bank import QuestionBankProtocol
        from infra.knowledge.question_bank import JsonQuestionBank

        bank = JsonQuestionBank()
        assert isinstance(bank, QuestionBankProtocol)

    def test_protocol_in_domain_protocols_init(self):
        from domain.protocols import QuestionBankProtocol
        assert QuestionBankProtocol is not None


# ============================================================
# 3. Service Layer (Pure Functions)
# ============================================================

class TestDeriveTriggersService:
    """Test derive_triggers pure function."""

    def test_buy_action_triggers(self):
        from domain.services.advisor_questions_service import derive_triggers

        triggers = derive_triggers("buy", [], {})
        assert "any_buy" in triggers

    def test_sell_action_triggers(self):
        from domain.services.advisor_questions_service import derive_triggers

        triggers = derive_triggers("sell", [], {})
        assert "any_sell" in triggers

    def test_reason_ids_become_triggers(self):
        from domain.services.advisor_questions_service import derive_triggers

        triggers = derive_triggers("buy", ["momentum_chase", "fomo"], {})
        assert "momentum_chase" in triggers
        assert "fomo" in triggers
        assert "any_buy" in triggers

    def test_high_concentration_trigger(self):
        from domain.services.advisor_questions_service import derive_triggers

        triggers = derive_triggers("buy", [], {"concentration_pct": 0.30})
        assert "high_concentration" in triggers

    def test_low_emergency_fund_trigger(self):
        from domain.services.advisor_questions_service import derive_triggers

        triggers = derive_triggers("buy", [], {"emergency_months": 3.0})
        assert "low_emergency_fund" in triggers

    def test_recent_trade_trigger(self):
        from domain.services.advisor_questions_service import derive_triggers

        triggers = derive_triggers("buy", [], {"days_since_last_trade": 10})
        assert "recent_trade" in triggers
        assert "short_cooldown" in triggers
        assert "frequent_trade" in triggers

    def test_large_amount_trigger(self):
        from domain.services.advisor_questions_service import derive_triggers

        triggers = derive_triggers("buy", [], {"amount_pct": 0.25})
        assert "large_amount" in triggers

    def test_debt_trigger(self):
        from domain.services.advisor_questions_service import derive_triggers

        triggers = derive_triggers("buy", [], {"has_debt": True})
        assert "has_debt" in triggers


class TestScoreTemplate:
    """Test score_template pure function."""

    def test_no_matching_triggers_returns_zero(self):
        from domain.models.questions import QuestionTemplate
        from domain.services.advisor_questions_service import score_template

        tmpl = QuestionTemplate("q1", "risk", "text", ["fomo"], 10)
        score = score_template(tmpl, ["any_buy"])
        assert score == 0.0

    def test_one_match_returns_weight_plus_10(self):
        from domain.models.questions import QuestionTemplate
        from domain.services.advisor_questions_service import score_template

        tmpl = QuestionTemplate("q1", "risk", "text", ["any_buy"], 8)
        score = score_template(tmpl, ["any_buy"])
        assert score == 18.0  # weight(8) + 1*10

    def test_two_matches_with_bonus(self):
        from domain.models.questions import QuestionTemplate
        from domain.services.advisor_questions_service import score_template

        tmpl = QuestionTemplate("q1", "risk", "text", ["any_buy", "fomo"], 8)
        score = score_template(tmpl, ["any_buy", "fomo"])
        assert score == 33.0  # weight(8) + 2*10 + 5(bonus)


class TestSelectQuestions:
    """Test select_questions with category diversity."""

    def test_selects_diverse_categories(self):
        from domain.models.questions import QuestionTemplate
        from domain.services.advisor_questions_service import select_questions

        templates = [
            QuestionTemplate("q1", "risk", "t1", ["any_buy"], 10),
            QuestionTemplate("q2", "risk", "t2", ["any_buy"], 9),  # same cat
            QuestionTemplate("q3", "time", "t3", ["any_buy"], 8),
            QuestionTemplate("q4", "safety", "t4", ["any_buy"], 7),
        ]
        result = select_questions(templates, ["any_buy"], max_questions=3)
        categories = [t.category for t, _ in result]
        # Should pick q1(risk), q3(time), q4(safety) — diverse
        assert "risk" in categories
        assert "time" in categories
        assert "safety" in categories

    def test_min_questions_filled(self):
        from domain.models.questions import QuestionTemplate
        from domain.services.advisor_questions_service import select_questions

        templates = [
            QuestionTemplate("q1", "risk", "t1", ["any_buy"], 10),
            QuestionTemplate("q2", "risk", "t2", ["any_buy"], 9),
            QuestionTemplate("q3", "risk", "t3", ["any_buy"], 8),
        ]
        result = select_questions(templates, ["any_buy"], min_questions=3, max_questions=5)
        assert len(result) == 3

    def test_returns_empty_when_no_triggers_match(self):
        from domain.models.questions import QuestionTemplate
        from domain.services.advisor_questions_service import select_questions

        templates = [
            QuestionTemplate("q1", "risk", "t1", ["fomo"], 10),
        ]
        result = select_questions(templates, ["any_buy"], min_questions=3)
        assert len(result) == 0


class TestRenderQuestion:
    """Test render_question placeholder filling."""

    def test_fills_concentration_placeholder(self):
        from domain.models.questions import QuestionTemplate
        from domain.services.advisor_questions_service import render_question

        tmpl = QuestionTemplate(
            "q1", "concentration",
            "集中度会到 {concentration_pct}%",
            ["high_concentration"], 10
        )
        q = render_question(tmpl, {"concentration_pct": 0.35}, score=25.0)
        assert "35.0%" in q.text
        assert q.relevance_score == 25.0

    def test_fills_days_placeholder(self):
        from domain.models.questions import QuestionTemplate
        from domain.services.advisor_questions_service import render_question

        tmpl = QuestionTemplate(
            "q1", "track_record",
            "距今 {days_since_last} 天",
            ["recent_trade"], 8
        )
        q = render_question(tmpl, {"days_since_last_trade": 15})
        assert "15 天" in q.text


class TestBuildSocraticSession:
    """Test build_socratic_session end-to-end service."""

    def test_builds_session_with_questions(self):
        from domain.models.questions import QuestionTemplate
        from domain.services.advisor_questions_service import build_socratic_session

        templates = [
            QuestionTemplate("q1", "risk", "跌30%怎么办", ["any_buy"], 10),
            QuestionTemplate("q2", "time", "几年后用？", ["any_buy"], 9),
            QuestionTemplate("q3", "safety", "应急金够吗？", ["any_buy", "low_emergency_fund"], 8),
            QuestionTemplate("q4", "bias", "是跟风吗？", ["fomo"], 10),
        ]
        session = build_socratic_session(
            templates=templates,
            user_id="u1",
            action="buy",
            reason_ids=["fomo"],
            context={"emergency_months": 3.0},
        )
        assert session.user_id == "u1"
        assert session.action == "buy"
        assert 3 <= len(session.questions) <= 5
        assert "any_buy" in session.context_summary

    def test_session_to_dict_structure(self):
        from domain.models.questions import QuestionTemplate
        from domain.services.advisor_questions_service import build_socratic_session

        templates = [
            QuestionTemplate("q1", "risk", "问题1", ["any_buy"], 10),
            QuestionTemplate("q2", "time", "问题2", ["any_buy"], 9),
            QuestionTemplate("q3", "cost", "问题3", ["any_buy"], 8),
        ]
        session = build_socratic_session(
            templates=templates, user_id="u1",
            action="buy", reason_ids=[], context={},
        )
        d = session.to_dict()
        assert "questions" in d
        assert "question_count" in d
        assert d["question_count"] == len(d["questions"])


# ============================================================
# 4. Infra Layer (JsonQuestionBank)
# ============================================================

class TestJsonQuestionBank:
    """Test the JSON file-backed question bank."""

    def test_loads_templates_from_default_dir(self):
        from infra.knowledge.question_bank import JsonQuestionBank, reset_question_bank

        reset_question_bank()
        bank = JsonQuestionBank()
        templates = bank.load_templates()
        assert len(templates) >= 40  # design doc says 40-60

    def test_get_templates_by_trigger(self):
        from infra.knowledge.question_bank import JsonQuestionBank

        bank = JsonQuestionBank()
        buy_templates = bank.get_templates_by_trigger("any_buy")
        assert len(buy_templates) >= 10  # many questions apply to buy

    def test_get_template_by_id(self):
        from infra.knowledge.question_bank import JsonQuestionBank

        bank = JsonQuestionBank()
        tmpl = bank.get_template_by_id("q_sleep_test")
        assert tmpl is not None
        assert tmpl.id == "q_sleep_test"
        assert "30%" in tmpl.text

    def test_get_template_by_id_not_found(self):
        from infra.knowledge.question_bank import JsonQuestionBank

        bank = JsonQuestionBank()
        tmpl = bank.get_template_by_id("nonexistent_id")
        assert tmpl is None

    def test_singleton_factory(self):
        from infra.knowledge.question_bank import get_question_bank, reset_question_bank

        reset_question_bank()
        b1 = get_question_bank()
        b2 = get_question_bank()
        assert b1 is b2
        reset_question_bank()


# ============================================================
# 5. Use Case (End-to-End)
# ============================================================

class TestGenerateSocraticQuestionsUseCase:
    """Test the full use case flow."""

    def test_generates_questions_for_buy(self):
        from use_cases.generate_socratic_questions import generate_socratic_questions
        from infra.knowledge.question_bank import reset_question_bank

        reset_question_bank()
        session = generate_socratic_questions(
            user_id="test_user",
            action="buy",
            reason_ids=["momentum_chase"],
            context={"concentration_pct": 0.30, "days_since_last_trade": 10},
        )
        assert session.user_id == "test_user"
        assert session.action == "buy"
        assert 3 <= len(session.questions) <= 5
        # Should include FOMO-related questions
        categories = [q.category for q in session.questions]
        assert len(set(categories)) >= 3  # diverse categories

    def test_generates_questions_for_sell(self):
        from use_cases.generate_socratic_questions import generate_socratic_questions
        from infra.knowledge.question_bank import reset_question_bank

        reset_question_bank()
        session = generate_socratic_questions(
            user_id="test_user",
            action="sell",
            reason_ids=[],
            context={},
        )
        assert len(session.questions) >= 3
        # Should have sell-related questions
        template_ids = [q.template_id for q in session.questions]
        # At least one sell-specific question
        sell_ids = {"q_sell_timing", "q_sell_loss_reason", "q_sell_rebalance", "q_reduce_reason"}
        assert any(tid in sell_ids for tid in template_ids)

    def test_max_questions_respected(self):
        from use_cases.generate_socratic_questions import generate_socratic_questions
        from infra.knowledge.question_bank import reset_question_bank

        reset_question_bank()
        session = generate_socratic_questions(
            user_id="u1",
            action="buy",
            reason_ids=["fomo", "momentum_chase", "hot_news"],
            context={"concentration_pct": 0.4, "amount_pct": 0.3},
            max_questions=3,
        )
        assert len(session.questions) <= 3

    def test_placeholders_filled_in_output(self):
        from use_cases.generate_socratic_questions import generate_socratic_questions
        from infra.knowledge.question_bank import reset_question_bank

        reset_question_bank()
        session = generate_socratic_questions(
            user_id="u1",
            action="buy",
            reason_ids=[],
            context={"concentration_pct": 0.35, "days_since_last_trade": 7},
            max_questions=7,
        )
        # Check no unresolved placeholders remain
        for q in session.questions:
            assert "{" not in q.text or "}" not in q.text, f"Unresolved placeholder in: {q.text}"


# ============================================================
# 6. Invariants
# ============================================================

class TestInvariants:
    """Verify architectural invariants."""

    def test_domain_models_questions_no_infra_import(self):
        """domain/models/questions.py must not import from infra/."""
        src = (BACKEND_DIR / "domain" / "models" / "questions.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("infra"), f"Forbidden import: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert not node.module.startswith("infra"), f"Forbidden import: {node.module}"

    def test_domain_services_no_infra_import(self):
        """domain/services/advisor_questions_service.py must not import from infra/."""
        src = (BACKEND_DIR / "domain" / "services" / "advisor_questions_service.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("infra"), f"Forbidden import: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert not node.module.startswith("infra"), f"Forbidden import: {node.module}"

    def test_domain_protocol_no_infra_import(self):
        """domain/protocols/question_bank.py must not import from infra/."""
        src = (BACKEND_DIR / "domain" / "protocols" / "question_bank.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("infra"), f"Forbidden import: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert not node.module.startswith("infra"), f"Forbidden import: {node.module}"

    def test_service_is_pure_no_io(self):
        """advisor_questions_service.py must not perform I/O (no open/read/write/json.load)."""
        src = (BACKEND_DIR / "domain" / "services" / "advisor_questions_service.py").read_text()
        # Check no file I/O imports or calls
        forbidden = ["open(", "json.load", "Path(", "os.path", "import pathlib"]
        for f in forbidden:
            assert f not in src, f"Service should be pure (no I/O): found '{f}'"

    def test_question_template_json_valid(self):
        """All templates in socratic_templates.json are valid."""
        import json
        from domain.models.questions import QuestionTemplate

        json_file = BACKEND_DIR / "infra" / "knowledge" / "content" / "questions" / "socratic_templates.json"
        data = json.loads(json_file.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) >= 40

        for item in data:
            tmpl = QuestionTemplate.from_dict(item)
            assert tmpl.id.startswith("q_")
            assert len(tmpl.text) > 5
            assert len(tmpl.applicable_when) >= 1
            assert 1 <= tmpl.weight <= 10

    def test_question_ids_unique(self):
        """All template IDs are unique."""
        import json

        json_file = BACKEND_DIR / "infra" / "knowledge" / "content" / "questions" / "socratic_templates.json"
        data = json.loads(json_file.read_text(encoding="utf-8"))
        ids = [item["id"] for item in data]
        assert len(ids) == len(set(ids)), f"Duplicate IDs found: {[x for x in ids if ids.count(x) > 1]}"

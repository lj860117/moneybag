"""
Test suite for M6 W2: Weekly Education System
==============================================
Tests for:
- Holding condition evaluation
- Article matching (holdings → articles)
- Fatigue control (weekly cap, monthly event cap, dedup)
- Intro sentence rendering
- End-to-end lesson selection
- API endpoint schema
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Dict, Any, List, Set

import pytest

# Ensure backend/ is on sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from domain.models.education import (
    HoldingArticleMapping,
    HoldingContext,
    LessonPushRecord,
    LessonTrigger,
    PushStatus,
    WeeklyLesson,
    HOLDING_ARTICLE_MAPPINGS,
    MAX_PUSHES_PER_WEEK,
    ARTICLE_REPEAT_COOLDOWN_DAYS,
    DRAWDOWN_THRESHOLD_PCT,
)
from domain.models.knowledge import KnowledgeArticle, SourceGrade
from domain.services.education_service import (
    check_fatigue,
    count_pushes_this_week,
    count_event_pushes_this_month,
    evaluate_holding_conditions,
    get_matching_articles,
    get_recently_sent_article_ids,
    render_intro_sentence,
    select_weekly_lesson,
    build_push_record,
    get_current_week_iso,
)
from use_cases.generate_weekly_lesson import (
    generate_weekly_lesson,
    check_push_allowed,
    get_lesson_history_summary,
    record_lesson_push,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def sample_articles() -> List[KnowledgeArticle]:
    """Sample knowledge articles for testing."""
    return [
        KnowledgeArticle(
            article_id="index-investing",
            title="指数投资的长期优势",
            category="资产配置",
            source="Bogle",
            source_grade=SourceGrade.B,
            tags=["指数基金", "被动投资"],
            review_status="published",
        ),
        KnowledgeArticle(
            article_id="gold-hedge",
            title="黄金在家庭资产中的对冲角色",
            category="单类资产",
            source="World Gold Council",
            source_grade=SourceGrade.B,
            tags=["黄金", "对冲"],
            review_status="published",
        ),
        KnowledgeArticle(
            article_id="loss-aversion",
            title="损失厌恶与处置效应",
            category="行为金融",
            source="Kahneman",
            source_grade=SourceGrade.A,
            tags=["损失厌恶", "行为偏误"],
            review_status="published",
        ),
        KnowledgeArticle(
            article_id="dca-strategy",
            title="定投策略：用时间换空间",
            category="资产配置",
            source="经典理论",
            source_grade=SourceGrade.B,
            tags=["定投", "纪律储蓄"],
            review_status="published",
        ),
        KnowledgeArticle(
            article_id="compound-interest",
            title="复利与72法则",
            category="数学常识",
            source="经典理论",
            source_grade=SourceGrade.B,
            tags=["复利", "长期投资"],
            review_status="published",
        ),
        KnowledgeArticle(
            article_id="stock-bond-rebalance",
            title="股债再平衡",
            category="资产配置",
            source="Bogle",
            source_grade=SourceGrade.B,
            tags=["再平衡", "股债配比"],
            review_status="published",
        ),
        KnowledgeArticle(
            article_id="anchoring-effect",
            title="锚定效应",
            category="行为金融",
            source="Tversky & Kahneman",
            source_grade=SourceGrade.A,
            tags=["锚定效应", "认知偏误"],
            review_status="published",
        ),
        KnowledgeArticle(
            article_id="emergency-fund-6-months",
            title="应急金6个月法则",
            category="家庭财务基础",
            source="经典理论",
            source_grade=SourceGrade.B,
            tags=["应急金", "流动性"],
            review_status="published",
        ),
    ]


@pytest.fixture
def fund_holder_context() -> HoldingContext:
    """User who holds funds."""
    return HoldingContext(
        user_id="test_user",
        asset_classes=["fund"],
        has_fund=True,
        total_positions=3,
    )


@pytest.fixture
def diversified_context() -> HoldingContext:
    """User with diversified holdings."""
    return HoldingContext(
        user_id="test_user",
        asset_classes=["fund", "stock", "gold"],
        has_fund=True,
        has_stock=True,
        has_gold=True,
        total_positions=8,
    )


@pytest.fixture
def drawdown_context() -> HoldingContext:
    """User experiencing a drawdown event."""
    return HoldingContext(
        user_id="test_user",
        asset_classes=["stock"],
        has_stock=True,
        max_drawdown_pct=15.0,
        drawdown_asset_name="贵州茅台",
        total_positions=5,
    )


# ============================================================================
# Tests: Holding Condition Evaluation
# ============================================================================

class TestHoldingConditions:
    """Test holding condition evaluation."""

    def test_fund_holder(self, fund_holder_context: HoldingContext) -> None:
        """Fund holder gets fund + any_holding conditions."""
        conditions = evaluate_holding_conditions(fund_holder_context)
        assert "has_fund" in conditions
        assert "any_holding" in conditions
        assert "has_stock" not in conditions

    def test_diversified(self, diversified_context: HoldingContext) -> None:
        """Diversified holder gets all relevant conditions."""
        conditions = evaluate_holding_conditions(diversified_context)
        assert "has_fund" in conditions
        assert "has_stock" in conditions
        assert "has_gold" in conditions
        assert "any_holding" in conditions

    def test_drawdown_event(self, drawdown_context: HoldingContext) -> None:
        """Drawdown >10% triggers event condition."""
        conditions = evaluate_holding_conditions(drawdown_context)
        assert "drawdown_gt_10" in conditions
        assert "has_stock" in conditions

    def test_no_drawdown_below_threshold(self) -> None:
        """Drawdown <10% does not trigger event condition."""
        context = HoldingContext(
            user_id="test",
            asset_classes=["stock"],
            has_stock=True,
            max_drawdown_pct=8.0,
            total_positions=3,
        )
        conditions = evaluate_holding_conditions(context)
        assert "drawdown_gt_10" not in conditions

    def test_empty_portfolio(self) -> None:
        """Empty portfolio gets no conditions."""
        context = HoldingContext(
            user_id="test",
            asset_classes=[],
            total_positions=0,
        )
        conditions = evaluate_holding_conditions(context)
        assert len(conditions) == 0


# ============================================================================
# Tests: Article Matching
# ============================================================================

class TestArticleMatching:
    """Test holding → article matching."""

    def test_fund_holder_matches(self, fund_holder_context: HoldingContext, sample_articles: List[KnowledgeArticle]) -> None:
        """Fund holder matches index-investing and dca-strategy."""
        available_ids = {a.article_id for a in sample_articles}
        matches = get_matching_articles(fund_holder_context, available_ids, set())
        matched_ids = [m.article_id for m in matches]
        assert "index-investing" in matched_ids
        assert "dca-strategy" in matched_ids

    def test_gold_holder_matches(self, sample_articles: List[KnowledgeArticle]) -> None:
        """Gold holder matches gold-hedge article."""
        context = HoldingContext(
            user_id="test",
            asset_classes=["gold"],
            has_gold=True,
            total_positions=1,
        )
        available_ids = {a.article_id for a in sample_articles}
        matches = get_matching_articles(context, available_ids, set())
        matched_ids = [m.article_id for m in matches]
        assert "gold-hedge" in matched_ids

    def test_excludes_recently_sent(self, fund_holder_context: HoldingContext, sample_articles: List[KnowledgeArticle]) -> None:
        """Recently sent articles are excluded."""
        available_ids = {a.article_id for a in sample_articles}
        excluded = {"index-investing", "dca-strategy"}
        matches = get_matching_articles(fund_holder_context, available_ids, excluded)
        matched_ids = [m.article_id for m in matches]
        assert "index-investing" not in matched_ids
        assert "dca-strategy" not in matched_ids

    def test_priority_ordering(self, drawdown_context: HoldingContext, sample_articles: List[KnowledgeArticle]) -> None:
        """Higher priority articles come first."""
        available_ids = {a.article_id for a in sample_articles}
        matches = get_matching_articles(drawdown_context, available_ids, set())
        # Drawdown articles (priority 9, 8) should be first
        assert matches[0].priority >= matches[-1].priority

    def test_unavailable_articles_excluded(self, fund_holder_context: HoldingContext) -> None:
        """Articles not in knowledge base are excluded."""
        # Only one article available
        available_ids = {"compound-interest"}
        matches = get_matching_articles(fund_holder_context, available_ids, set())
        for m in matches:
            assert m.article_id in available_ids


# ============================================================================
# Tests: Fatigue Control
# ============================================================================

class TestFatigueControl:
    """Test fatigue control rules."""

    def test_allow_first_push(self) -> None:
        """First push of the week is always allowed."""
        allowed, reason = check_fatigue([], "2026-W19", LessonTrigger.WEEKLY_REGULAR, time.time())
        assert allowed is True
        assert reason == ""

    def test_block_after_max_weekly(self) -> None:
        """Block after reaching weekly cap."""
        now = time.time()
        history = [
            LessonPushRecord(
                user_id="test", article_id="a1", week_iso="2026-W19",
                trigger=LessonTrigger.WEEKLY_REGULAR, status=PushStatus.DELIVERED,
                pushed_at=now - 100,
            ),
            LessonPushRecord(
                user_id="test", article_id="a2", week_iso="2026-W19",
                trigger=LessonTrigger.WEEKLY_REGULAR, status=PushStatus.DELIVERED,
                pushed_at=now - 50,
            ),
        ]
        allowed, reason = check_fatigue(history, "2026-W19", LessonTrigger.WEEKLY_REGULAR, now)
        assert allowed is False
        assert "上限" in reason

    def test_different_week_resets(self) -> None:
        """A new week resets the counter."""
        now = time.time()
        history = [
            LessonPushRecord(
                user_id="test", article_id="a1", week_iso="2026-W18",
                trigger=LessonTrigger.WEEKLY_REGULAR, status=PushStatus.DELIVERED,
                pushed_at=now - 700000,
            ),
            LessonPushRecord(
                user_id="test", article_id="a2", week_iso="2026-W18",
                trigger=LessonTrigger.WEEKLY_REGULAR, status=PushStatus.DELIVERED,
                pushed_at=now - 700000,
            ),
        ]
        allowed, reason = check_fatigue(history, "2026-W19", LessonTrigger.WEEKLY_REGULAR, now)
        assert allowed is True

    def test_event_trigger_monthly_cap(self) -> None:
        """Event-triggered pushes capped at 1/month."""
        now = time.time()
        history = [
            LessonPushRecord(
                user_id="test", article_id="a1", week_iso="2026-W17",
                trigger=LessonTrigger.HOLDING_EVENT, status=PushStatus.DELIVERED,
                pushed_at=now - (5 * 86400),  # 5 days ago (within 30 days)
            ),
        ]
        allowed, reason = check_fatigue(history, "2026-W19", LessonTrigger.HOLDING_EVENT, now)
        assert allowed is False
        assert "事件触发" in reason

    def test_skipped_records_dont_count(self) -> None:
        """Skipped records don't count toward limits."""
        now = time.time()
        history = [
            LessonPushRecord(
                user_id="test", article_id="a1", week_iso="2026-W19",
                trigger=LessonTrigger.WEEKLY_REGULAR, status=PushStatus.SKIPPED_FATIGUE,
                pushed_at=now - 100,
            ),
        ]
        allowed, reason = check_fatigue(history, "2026-W19", LessonTrigger.WEEKLY_REGULAR, now)
        assert allowed is True

    def test_dedup_within_cooldown(self) -> None:
        """Articles sent within 90 days are excluded."""
        now = time.time()
        history = [
            LessonPushRecord(
                user_id="test", article_id="gold-hedge", week_iso="2026-W15",
                trigger=LessonTrigger.WEEKLY_REGULAR, status=PushStatus.DELIVERED,
                pushed_at=now - (30 * 86400),  # 30 days ago (within 90-day window)
            ),
        ]
        excluded = get_recently_sent_article_ids(history, now)
        assert "gold-hedge" in excluded

    def test_dedup_outside_cooldown(self) -> None:
        """Articles sent >90 days ago are allowed again."""
        now = time.time()
        history = [
            LessonPushRecord(
                user_id="test", article_id="gold-hedge", week_iso="2026-W05",
                trigger=LessonTrigger.WEEKLY_REGULAR, status=PushStatus.DELIVERED,
                pushed_at=now - (100 * 86400),  # 100 days ago (outside window)
            ),
        ]
        excluded = get_recently_sent_article_ids(history, now)
        assert "gold-hedge" not in excluded


# ============================================================================
# Tests: Intro Sentence Rendering
# ============================================================================

class TestIntroRendering:
    """Test intro sentence template rendering."""

    def test_basic_rendering(self) -> None:
        """Simple template renders correctly."""
        context = HoldingContext(
            user_id="test",
            asset_classes=["fund"],
            has_fund=True,
            total_positions=3,
        )
        text = render_intro_sentence("你持有基金产品，这周来了解一下。", context)
        assert text == "你持有基金产品，这周来了解一下。"

    def test_drawdown_rendering(self) -> None:
        """Drawdown template fills asset_name placeholder."""
        context = HoldingContext(
            user_id="test",
            asset_classes=["stock"],
            has_stock=True,
            max_drawdown_pct=15.0,
            drawdown_asset_name="贵州茅台",
            total_positions=5,
        )
        text = render_intro_sentence(
            "你的{asset_name}浮亏超过10%，这是最该冷静的时刻。",
            context,
        )
        assert "贵州茅台" in text
        assert "{" not in text


# ============================================================================
# Tests: End-to-End Lesson Selection
# ============================================================================

class TestLessonSelection:
    """Test complete lesson selection flow."""

    def test_select_for_fund_holder(self, fund_holder_context: HoldingContext, sample_articles: List[KnowledgeArticle]) -> None:
        """Fund holder gets a relevant lesson."""
        lesson = select_weekly_lesson(
            context=fund_holder_context,
            push_history=[],
            available_articles=sample_articles,
            trigger=LessonTrigger.WEEKLY_REGULAR,
            current_time=time.time(),
        )
        assert lesson is not None
        assert lesson.user_id == "test_user"
        assert lesson.article_id in {"index-investing", "dca-strategy"}
        assert lesson.intro_sentence != ""

    def test_select_for_drawdown(self, drawdown_context: HoldingContext, sample_articles: List[KnowledgeArticle]) -> None:
        """Drawdown event selects behavioral finance content."""
        lesson = select_weekly_lesson(
            context=drawdown_context,
            push_history=[],
            available_articles=sample_articles,
            trigger=LessonTrigger.HOLDING_EVENT,
            current_time=time.time(),
        )
        assert lesson is not None
        # Drawdown articles should be high priority (loss-aversion or anchoring-effect)
        assert lesson.article_id in {"loss-aversion", "anchoring-effect"}
        assert "贵州茅台" in lesson.intro_sentence

    def test_none_when_fatigue_blocked(self, fund_holder_context: HoldingContext, sample_articles: List[KnowledgeArticle]) -> None:
        """Returns None when fatigue control blocks."""
        now = time.time()
        week_iso = get_current_week_iso()
        history = [
            LessonPushRecord(
                user_id="test_user", article_id="a1", week_iso=week_iso,
                trigger=LessonTrigger.WEEKLY_REGULAR, status=PushStatus.DELIVERED,
                pushed_at=now - 100,
            ),
            LessonPushRecord(
                user_id="test_user", article_id="a2", week_iso=week_iso,
                trigger=LessonTrigger.WEEKLY_REGULAR, status=PushStatus.DELIVERED,
                pushed_at=now - 50,
            ),
        ]
        lesson = select_weekly_lesson(
            context=fund_holder_context,
            push_history=history,
            available_articles=sample_articles,
            trigger=LessonTrigger.WEEKLY_REGULAR,
            current_time=now,
        )
        assert lesson is None

    def test_none_when_all_articles_excluded(self, fund_holder_context: HoldingContext, sample_articles: List[KnowledgeArticle]) -> None:
        """Returns None when all matching articles are recently sent."""
        now = time.time()
        # Mark ALL articles as recently sent
        history = [
            LessonPushRecord(
                user_id="test_user", article_id=a.article_id, week_iso="2026-W18",
                trigger=LessonTrigger.WEEKLY_REGULAR, status=PushStatus.DELIVERED,
                pushed_at=now - (10 * 86400),  # 10 days ago (within 90-day window)
            )
            for a in sample_articles
        ]
        lesson = select_weekly_lesson(
            context=fund_holder_context,
            push_history=history,
            available_articles=sample_articles,
            trigger=LessonTrigger.WEEKLY_REGULAR,
            current_time=now,
        )
        assert lesson is None

    def test_lesson_has_all_fields(self, fund_holder_context: HoldingContext, sample_articles: List[KnowledgeArticle]) -> None:
        """Generated lesson has all required fields."""
        lesson = select_weekly_lesson(
            context=fund_holder_context,
            push_history=[],
            available_articles=sample_articles,
            trigger=LessonTrigger.WEEKLY_REGULAR,
            current_time=time.time(),
        )
        assert lesson is not None
        assert lesson.lesson_id != ""
        assert lesson.user_id == "test_user"
        assert lesson.article_title != ""
        assert lesson.article_category != ""
        assert lesson.week_iso != ""
        assert lesson.created_at > 0


# ============================================================================
# Tests: Models
# ============================================================================

class TestModels:
    """Test domain model serialization."""

    def test_weekly_lesson_to_dict(self, fund_holder_context: HoldingContext, sample_articles: List[KnowledgeArticle]) -> None:
        """WeeklyLesson serializes to dict."""
        lesson = select_weekly_lesson(
            context=fund_holder_context,
            push_history=[],
            available_articles=sample_articles,
            current_time=time.time(),
        )
        assert lesson is not None
        d = lesson.to_dict()
        assert d["user_id"] == "test_user"
        assert "trigger" in d
        assert d["trigger"] == "weekly_regular"

    def test_push_record_roundtrip(self) -> None:
        """LessonPushRecord to_dict/from_dict roundtrip."""
        record = LessonPushRecord(
            user_id="test",
            article_id="gold-hedge",
            week_iso="2026-W19",
            trigger=LessonTrigger.WEEKLY_REGULAR,
            status=PushStatus.DELIVERED,
            pushed_at=1234567890.0,
        )
        d = record.to_dict()
        restored = LessonPushRecord.from_dict(d)
        assert restored.user_id == "test"
        assert restored.article_id == "gold-hedge"
        assert restored.trigger == LessonTrigger.WEEKLY_REGULAR
        assert restored.status == PushStatus.DELIVERED

    def test_holding_context_to_dict(self) -> None:
        """HoldingContext serializes to dict."""
        ctx = HoldingContext(
            user_id="test",
            asset_classes=["fund", "gold"],
            has_fund=True,
            has_gold=True,
            total_positions=5,
        )
        d = ctx.to_dict()
        assert d["user_id"] == "test"
        assert d["has_fund"] is True
        assert d["has_gold"] is True

    def test_holding_article_mapping_structure(self) -> None:
        """HOLDING_ARTICLE_MAPPINGS has expected structure."""
        assert len(HOLDING_ARTICLE_MAPPINGS) >= 10
        for mapping in HOLDING_ARTICLE_MAPPINGS:
            assert mapping.condition != ""
            assert mapping.article_id != ""
            assert mapping.intro_template != ""
            assert 1 <= mapping.priority <= 10


# ============================================================================
# Tests: Use Case Functions
# ============================================================================

class TestUseCases:
    """Test use case layer functions."""

    def test_generate_weekly_lesson_delegates(self, fund_holder_context: HoldingContext, sample_articles: List[KnowledgeArticle]) -> None:
        """generate_weekly_lesson delegates to service correctly."""
        lesson = generate_weekly_lesson(
            context=fund_holder_context,
            push_history=[],
            available_articles=sample_articles,
            current_time=time.time(),
        )
        assert lesson is not None
        assert lesson.user_id == "test_user"

    def test_check_push_allowed_empty_history(self) -> None:
        """check_push_allowed with empty history allows push."""
        result = check_push_allowed([], LessonTrigger.WEEKLY_REGULAR, time.time())
        assert result["allowed"] is True
        assert result["pushes_this_week"] == 0

    def test_get_lesson_history_summary_empty(self) -> None:
        """Empty history returns zeros."""
        summary = get_lesson_history_summary([])
        assert summary["total_lessons"] == 0
        assert summary["weeks_active"] == 0
        assert summary["articles_covered"] == 0

    def test_get_lesson_history_summary_with_data(self) -> None:
        """Summary with data returns correct counts."""
        now = time.time()
        history = [
            LessonPushRecord(
                user_id="test", article_id="a1", week_iso="2026-W18",
                trigger=LessonTrigger.WEEKLY_REGULAR, status=PushStatus.DELIVERED,
                pushed_at=now - 700000,
            ),
            LessonPushRecord(
                user_id="test", article_id="a2", week_iso="2026-W19",
                trigger=LessonTrigger.WEEKLY_REGULAR, status=PushStatus.DELIVERED,
                pushed_at=now - 100,
            ),
        ]
        summary = get_lesson_history_summary(history)
        assert summary["total_lessons"] == 2
        assert summary["weeks_active"] == 2
        assert summary["articles_covered"] == 2

    def test_record_lesson_push(self, fund_holder_context: HoldingContext, sample_articles: List[KnowledgeArticle]) -> None:
        """record_lesson_push creates proper record."""
        lesson = generate_weekly_lesson(
            context=fund_holder_context,
            push_history=[],
            available_articles=sample_articles,
            current_time=time.time(),
        )
        assert lesson is not None
        record = record_lesson_push(lesson)
        assert record.user_id == lesson.user_id
        assert record.article_id == lesson.article_id
        assert record.status == PushStatus.DELIVERED


# ============================================================================
# Tests: API Endpoint
# ============================================================================

class TestAPIEndpoint:
    """Test API endpoint importability."""

    def test_weekly_lesson_endpoint_importable(self) -> None:
        """POST /api/decisions/weekly-lesson endpoint exists."""
        from api.decisions import post_weekly_lesson
        assert callable(post_weekly_lesson)

    def test_request_schema(self) -> None:
        """WeeklyLessonRequest validates correctly."""
        from api.decisions import WeeklyLessonRequest
        req = WeeklyLessonRequest(user_id="test_user")
        assert req.user_id == "test_user"
        assert req.trigger == "weekly_regular"

    def test_response_schema(self) -> None:
        """WeeklyLessonResponse serializes correctly."""
        from api.decisions import WeeklyLessonResponse
        resp = WeeklyLessonResponse(
            status="ok",
            lesson=None,
            delivered=False,
            reason="fatigue",
            fatigue_status={"allowed": False},
        )
        assert resp.delivered is False


# ============================================================================
# Tests: Invariants
# ============================================================================

class TestInvariants:
    """Test key invariants from design doc."""

    def test_no_market_prediction_in_intros(self) -> None:
        """Intro templates never contain market predictions."""
        forbidden = ["会涨", "会跌", "目标价", "建议买入", "建议卖出"]
        for mapping in HOLDING_ARTICLE_MAPPINGS:
            for pattern in forbidden:
                assert pattern not in mapping.intro_template, (
                    f"Invariant violation: '{pattern}' found in intro template for {mapping.article_id}"
                )

    def test_all_articles_from_knowledge_base(self) -> None:
        """All mapped article_ids correspond to known knowledge base slugs."""
        known_slugs = {
            # 原有 12 篇
            "index-investing", "gold-hedge", "loss-aversion", "dca-strategy",
            "compound-interest", "stock-bond-rebalance", "anchoring-effect",
            "emergency-fund-6-months", "4pct-rule", "family-pyramid",
            "insurance-priority", "lifecycle-investing",
            # M6 W3-4 新增 16 篇
            "72-rule", "asset-allocation-basics", "bond-basics",
            "convertible-bond-basics", "drawdown-psychology",
            "etf-vs-active-fund", "herd-mentality", "hot-fund-trap",
            "inflation-real-returns", "overconfidence-bias",
            "position-sizing", "rebalancing-math",
            "reit-basics", "sell-high-buy-low-trap",
            "stop-loss-take-profit", "sunk-cost-fallacy",
        }
        for mapping in HOLDING_ARTICLE_MAPPINGS:
            assert mapping.article_id in known_slugs, (
                f"Article '{mapping.article_id}' not in knowledge base"
            )

    def test_fatigue_constants_match_design(self) -> None:
        """Fatigue constants match design doc §3.4."""
        assert MAX_PUSHES_PER_WEEK == 2
        assert ARTICLE_REPEAT_COOLDOWN_DAYS == 90
        assert DRAWDOWN_THRESHOLD_PCT == 10.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

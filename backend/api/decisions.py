"""
Decisions API — Decision review + checklist endpoints
======================================================
Endpoints for submitting post-trade reviews, pre-trade checklist,
querying review history, and retrieving predefined buy reasons.

Routes:
  POST   /api/decisions/review            Submit a post-trade decision review (Mode B)
  POST   /api/decisions/checklist         Run pre-trade 7-point checklist (Mode A)
  GET    /api/decisions/review/{user_id}  Get user's review history
  GET    /api/decisions/review/{user_id}/stats  Get aggregate review statistics
  GET    /api/decisions/reasons           Get predefined buy reasons for UI
  GET    /api/decisions/checklist/items   Get checklist item descriptions for UI

Each endpoint <200 lines. Only routing + validation here; business logic in use_cases/.

Design doc: docs/design/07-decision-guard.md §3-5
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from domain.services.decision_guard_service import get_predefined_reasons
from use_cases.review_decision import (
    get_review_statistics,
    get_user_reviews,
    save_review_to_archive,
    submit_decision_review,
)
from use_cases.run_checklist import (
    get_checklist_items_description,
    run_pre_trade_checklist,
)
from use_cases.generate_monthly_report import (
    generate_monthly_report,
    get_available_months,
    get_monthly_report_summary,
)

router = APIRouter(tags=["决策复盘"])


# ---- Pydantic request/response schemas ----

class DecisionReviewRequest(BaseModel):
    """Request to submit a post-trade decision review."""
    user_id: str = Field(..., min_length=1, description="User ID")
    asset_code: str = Field(..., min_length=1, description="Stock/fund code (e.g. 600519)")
    asset_name: str = Field("", description="Asset display name (e.g. 贵州茅台)")
    action: str = Field("buy", description="buy | sell | add | reduce")
    amount: float = Field(0.0, ge=0, description="Trade amount in CNY")
    reason_ids: List[str] = Field(default_factory=list, description="List of predefined reason IDs")
    custom_reason_text: str = Field("", max_length=200, description="Custom free-text reason (optional)")
    trade_time: str = Field("", description="When the trade was executed (ISO 8601, defaults to now)")
    notes: str = Field("", max_length=500, description="Optional notes")
    # Checklist context (can be auto-computed from balance sheet in future)
    has_emergency_fund: bool = Field(True, description="Checklist: emergency fund >= 6 months?")
    concentration_ok: bool = Field(True, description="Checklist: single asset <= 25%?")
    money_not_needed_3y: bool = Field(True, description="Checklist: money not needed in 3 years?")
    days_since_last_trade: int = Field(60, ge=0, description="Days since last adjustment")


class DecisionReviewResponse(BaseModel):
    """Response after submitting a review."""
    status: str = "ok"
    review: Dict[str, Any] = Field(default_factory=dict, description="The saved review record")
    quality_score: Dict[str, Any] = Field(default_factory=dict, description="Quality score breakdown")
    warnings: List[str] = Field(default_factory=list, description="Red/yellow flag messages")
    further_reading: List[str] = Field(default_factory=list, description="RAG knowledge article titles")


class ReviewListResponse(BaseModel):
    """Response for review list queries."""
    status: str = "ok"
    reviews: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = 0


class ReviewStatsResponse(BaseModel):
    """Response for review statistics."""
    status: str = "ok"
    stats: Dict[str, Any] = Field(default_factory=dict)


class ReasonsResponse(BaseModel):
    """Response with available predefined reasons."""
    status: str = "ok"
    reasons: List[Dict[str, Any]] = Field(default_factory=list)


class ChecklistRequest(BaseModel):
    """Request to run pre-trade 7-point checklist (Mode A)."""
    reason_ids: List[str] = Field(default_factory=list, description="Selected buy reason IDs")
    custom_reason_text: str = Field("", max_length=200, description="Custom reason text")
    emergency_months: float = Field(6.0, ge=0, description="Emergency fund months coverage")
    insurance_count: int = Field(4, ge=0, le=4, description="Insurance types held (0-4)")
    single_asset_pct: float = Field(0.0, ge=0, le=1.0, description="Post-trade single asset concentration (0-1)")
    single_industry_pct: float = Field(0.0, ge=0, le=1.0, description="Post-trade single industry concentration (0-1)")
    money_needed_within_3y: bool = Field(False, description="Will this money be needed within 3 years?")
    trade_pct_of_investable: float = Field(0.0, ge=0, le=1.0, description="Trade amount as fraction of investable assets")
    days_since_last_trade: int = Field(60, ge=0, description="Days since last portfolio adjustment")


class ChecklistResponse(BaseModel):
    """Response for pre-trade checklist evaluation."""
    status: str = "ok"
    checklist: Dict[str, Any] = Field(default_factory=dict, description="Full checklist result")
    total_score: int = Field(0, description="Total score (0-70)")
    max_score: int = Field(70, description="Maximum possible score")
    passed: bool = Field(True, description="Whether the checklist passed (score >= 42)")
    blocked: bool = Field(False, description="Whether the trade should be blocked")
    red_light_count: int = Field(0, description="Number of red-light items")
    warnings: List[str] = Field(default_factory=list, description="Warning messages")
    further_reading: List[str] = Field(default_factory=list, description="RAG knowledge article titles")


class ChecklistItemsResponse(BaseModel):
    """Response with checklist item descriptions for UI."""
    status: str = "ok"
    items: List[Dict[str, str]] = Field(default_factory=list)


class SocraticQuestionsRequest(BaseModel):
    """Request to generate Socratic questions before a trade (09-advisor-features.md §一)."""
    user_id: str = Field(..., min_length=1, description="User ID")
    action: str = Field("buy", description="Trade action: buy | sell | add | reduce")
    reason_ids: List[str] = Field(default_factory=list, description="Selected buy reason IDs")
    # Context fields for question selection
    concentration_pct: float = Field(0.0, ge=0, le=1.0, description="Single asset concentration after trade (0-1)")
    days_since_last_trade: int = Field(60, ge=0, description="Days since last portfolio adjustment")
    emergency_months: float = Field(6.0, ge=0, description="Emergency fund months coverage")
    insurance_count: int = Field(4, ge=0, le=4, description="Insurance types held (0-4)")
    amount_pct: float = Field(0.0, ge=0, le=1.0, description="Trade amount as fraction of investable assets")
    has_debt: bool = Field(False, description="Has outstanding high-interest debt")
    diversification_count: int = Field(5, ge=0, description="Number of uncorrelated asset classes held")
    high_volatility: bool = Field(False, description="Recent high market volatility")
    is_single_stock: bool = Field(False, description="Buying a single stock (vs. fund)")
    max_questions: int = Field(5, ge=3, le=7, description="Maximum questions to return")


class SocraticQuestionsResponse(BaseModel):
    """Response with generated Socratic questions."""
    status: str = "ok"
    questions: List[Dict[str, Any]] = Field(default_factory=list, description="Rendered questions")
    question_count: int = Field(0, description="Number of questions returned")
    context_summary: str = Field("", description="Trigger context summary")


class MultiViewReviewRequest(BaseModel):
    """Request to generate multi-perspective second opinion (09-advisor-features.md §二)."""
    user_id: str = Field(..., min_length=1, description="User ID")
    asset_name: str = Field(..., min_length=1, description="Asset name (e.g., '贵州茅台', 'SPY')")
    asset_class: str = Field(
        ...,
        description="Asset class: stock, fund, bond, real_estate, crypto, gold, commodities",
    )
    transaction_amount: float = Field(..., gt=0, description="Amount being transacted (CNY)")
    current_position_value: float = Field(0.0, ge=0, description="Current position value")
    total_portfolio_value: float = Field(..., gt=0, description="Total portfolio size")
    recent_return_pct: Optional[float] = Field(None, description="Return last 3 months (%)")
    historical_return_pct: Optional[float] = Field(None, description="Long-term avg return (%)")
    loss_pct: Optional[float] = Field(None, description="Current drawdown (%)")
    days_since_rebalance: Optional[int] = Field(None, ge=0, description="Days since last adjustment")


class MultiViewReviewResponse(BaseModel):
    """Response with three-perspective second opinion."""
    status: str = "ok"
    review: Optional[Dict[str, Any]] = Field(None, description="Full review or None if triggers not met")
    triggered: bool = Field(False, description="Whether any trigger conditions were met")
    triggers_met: List[str] = Field(default_factory=list, description="Which trigger conditions fired")
    summary_text: str = Field("", description="Combined 3-perspective summary text")


class MonthlyReportResponse(BaseModel):
    """Response with monthly decision quality report."""
    status: str = "ok"
    report: Dict[str, Any] = Field(default_factory=dict, description="Full monthly report")


class MonthlyReportSummaryResponse(BaseModel):
    """Response with report availability summary."""
    status: str = "ok"
    available_months: List[str] = Field(default_factory=list, description="Months with data")
    latest_month: str = Field("", description="Most recent month with data")
    latest_avg_score: float = Field(0.0, description="Latest month avg quality score")
    total_reviews_all_time: int = Field(0, description="Total reviews all time")


# ---- Endpoints ----

@router.post("/api/decisions/review", response_model=DecisionReviewResponse)
async def post_decision_review(req: DecisionReviewRequest) -> DecisionReviewResponse:
    """Submit a post-trade decision review.

    1. User selects buy reasons (multi-select from predefined list + optional custom text)
    2. System computes decision quality score
    3. Review is saved to decision archive
    4. Returns score + warnings (red/yellow flags)

    This is the MODE B ("post-trade review") core endpoint.
    Design doc: 07-decision-guard.md §4.1
    """
    # Validate action
    valid_actions = {"buy", "sell", "add", "reduce"}
    if req.action not in valid_actions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action '{req.action}'. Must be one of: {valid_actions}",
        )

    # Validate at least 1 reason
    if not req.reason_ids and not req.custom_reason_text.strip():
        raise HTTPException(
            status_code=400,
            detail="至少选择一个买入理由或填写自定义理由",
        )

    # Submit review (business logic in use_case)
    review, warnings = submit_decision_review(
        user_id=req.user_id,
        asset_code=req.asset_code,
        asset_name=req.asset_name,
        action=req.action,
        amount=req.amount,
        reason_ids=req.reason_ids,
        custom_reason_text=req.custom_reason_text,
        trade_time=req.trade_time,
        notes=req.notes,
        has_emergency_fund=req.has_emergency_fund,
        concentration_ok=req.concentration_ok,
        money_not_needed_3y=req.money_not_needed_3y,
        days_since_last_trade=req.days_since_last_trade,
    )

    # Persist to archive
    saved = save_review_to_archive(req.user_id, review)

    # ---- RAG 延伸阅读（M4 W3）----
    further_reading: List[str] = []
    try:
        from infra.knowledge import get_retriever, load_and_index_articles
        from use_cases.interpret_with_rag import build_rag_context

        retriever = get_retriever()
        if retriever.total_chunks() == 0:
            load_and_index_articles(retriever)

        # 根据 reason_ids 和质量评分构建查询摘要
        facts_summary = f"买入操作 {req.asset_name}({req.asset_code})，理由: {', '.join(req.reason_ids) if req.reason_ids else '无'}"
        if req.custom_reason_text:
            facts_summary += f"; {req.custom_reason_text}"
        grade = review.quality_score.grade if hasattr(review.quality_score, "grade") else ""
        if grade:
            facts_summary += f"; 决策质量: {grade}"

        rag_ctx = build_rag_context(
            retriever, facts_summary=facts_summary, category_hint="行为金融", top_k=3
        )
        if rag_ctx["has_rag"]:
            further_reading = rag_ctx["further_reading"]
            for title in further_reading:
                warnings.append(f"📚 延伸阅读：{title}")
    except Exception as e:
        print(f"[DECISIONS/review] RAG failed (non-blocking): {e}")

    return DecisionReviewResponse(
        status="ok",
        review=saved,
        quality_score=review.quality_score.to_dict(),
        warnings=warnings,
        further_reading=further_reading,
    )


@router.get("/api/decisions/review/{user_id}", response_model=ReviewListResponse)
async def get_reviews(user_id: str, limit: int = 20) -> ReviewListResponse:
    """Get a user's recent decision reviews.

    Returns reviews ordered by time (newest first), up to limit.
    """
    if limit < 1 or limit > 100:
        limit = 20

    reviews = get_user_reviews(user_id, limit=limit)
    return ReviewListResponse(
        status="ok",
        reviews=reviews,
        total=len(reviews),
    )


@router.get("/api/decisions/review/{user_id}/stats", response_model=ReviewStatsResponse)
async def get_review_stats(user_id: str) -> ReviewStatsResponse:
    """Get aggregate review statistics for a user.

    Returns: avg_score, total_reviews, grade_distribution, red/yellow flag totals.
    Used for monthly/quarterly review reports.
    """
    stats = get_review_statistics(user_id)
    return ReviewStatsResponse(
        status="ok",
        stats=stats,
    )


@router.get("/api/decisions/reasons", response_model=ReasonsResponse)
async def get_reasons() -> ReasonsResponse:
    """Get all predefined buy reasons for the multi-select UI component.

    Returns the full list of predefined reasons with:
      - id: unique slug
      - label_zh: Chinese label for display
      - category: fundamental/technical/emotional/follow/other
      - signal: green/yellow/red
    """
    reasons = get_predefined_reasons()
    return ReasonsResponse(
        status="ok",
        reasons=reasons,
    )


# ---- Mode A: Pre-trade Checklist ----

@router.post("/api/decisions/checklist", response_model=ChecklistResponse)
async def post_checklist(req: ChecklistRequest) -> ChecklistResponse:
    """Run the 7-point pre-trade decision checklist (Mode A).

    Evaluates 7 dimensions before a trade:
      1. Emergency fund adequacy (>= 6 months)
      2. Insurance coverage (4 types)
      3. Concentration limits (single asset <= 25%, industry <= 40%)
      4. Money time horizon (not needed within 3 years)
      5. Reason rationality (no red/yellow flags from buy reasons)
      6. Position sizing (single trade <= 20% of investable)
      7. Cooldown period (>= 30 days since last trade)

    Each item scores 0-10. Total must be >= 42/70 (60%) to pass.
    If blocked=true, frontend should prevent or strongly discourage the trade.

    Design doc: 07-decision-guard.md §3
    """
    result, warnings = run_pre_trade_checklist(
        reason_ids=req.reason_ids,
        custom_reason_text=req.custom_reason_text,
        emergency_months=req.emergency_months,
        insurance_count=req.insurance_count,
        single_asset_pct=req.single_asset_pct,
        single_industry_pct=req.single_industry_pct,
        money_needed_within_3y=req.money_needed_within_3y,
        trade_pct_of_investable=req.trade_pct_of_investable,
        days_since_last_trade=req.days_since_last_trade,
    )

    # ---- RAG 延伸阅读（M4 W3）----
    further_reading: List[str] = []
    try:
        from infra.knowledge import get_retriever, load_and_index_articles
        from use_cases.interpret_with_rag import build_rag_context

        retriever = get_retriever()
        if retriever.total_chunks() == 0:
            load_and_index_articles(retriever)

        # 用 reason_ids 构建查询摘要
        facts_summary = f"买入理由: {', '.join(req.reason_ids) if req.reason_ids else '无'}"
        if req.custom_reason_text:
            facts_summary += f"; {req.custom_reason_text}"

        rag_ctx = build_rag_context(
            retriever, facts_summary=facts_summary, category_hint="行为金融", top_k=3
        )
        if rag_ctx["has_rag"]:
            further_reading = rag_ctx["further_reading"]
            for title in further_reading:
                warnings.append(f"📚 延伸阅读：{title}")
    except Exception as e:
        print(f"[DECISIONS/checklist] RAG failed (non-blocking): {e}")

    return ChecklistResponse(
        status="ok",
        checklist=result.to_dict(),
        total_score=result.total_score,
        max_score=result.max_score,
        passed=result.passed,
        blocked=result.blocked,
        red_light_count=result.red_light_count,
        warnings=warnings,
        further_reading=further_reading,
    )


@router.get("/api/decisions/checklist/items", response_model=ChecklistItemsResponse)
async def get_checklist_items() -> ChecklistItemsResponse:
    """Get descriptions of all 7 checklist items for UI display.

    Returns item IDs, Chinese labels, and descriptions so the frontend
    can show what will be checked before the user fills in data.
    """
    items = get_checklist_items_description()
    return ChecklistItemsResponse(
        status="ok",
        items=items,
    )


# ---- Mode A+: Socratic Questions (09-advisor-features.md §一) ----

@router.post("/api/decisions/socratic-questions", response_model=SocraticQuestionsResponse)
async def post_socratic_questions(req: SocraticQuestionsRequest) -> SocraticQuestionsResponse:
    """Generate Socratic questions for pre-trade reflection.

    Before executing a trade, present the user with 3-5 thought-provoking
    questions selected from the template bank based on their action,
    reasons, and portfolio context.

    Key invariant: AI only selects + fills blanks, never free-creates.

    Design doc: 09-advisor-features.md §一
    """
    # Validate action
    valid_actions = {"buy", "sell", "add", "reduce"}
    if req.action not in valid_actions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action '{req.action}'. Must be one of: {valid_actions}",
        )

    from use_cases.generate_socratic_questions import generate_socratic_questions

    # Build context dict from request fields
    context: Dict[str, Any] = {
        "concentration_pct": req.concentration_pct,
        "days_since_last_trade": req.days_since_last_trade,
        "emergency_months": req.emergency_months,
        "insurance_count": req.insurance_count,
        "amount_pct": req.amount_pct,
        "has_debt": req.has_debt,
        "diversification_count": req.diversification_count,
        "high_volatility": req.high_volatility,
        "is_single_stock": req.is_single_stock,
    }

    session = generate_socratic_questions(
        user_id=req.user_id,
        action=req.action,
        reason_ids=req.reason_ids,
        context=context,
        max_questions=req.max_questions,
    )

    return SocraticQuestionsResponse(
        status="ok",
        questions=[q.to_dict() for q in session.questions],
        question_count=len(session.questions),
        context_summary=session.context_summary,
    )


# ---- M5 W1-2: Monthly Decision Quality Report ----

@router.get("/api/decisions/monthly-report/{user_id}", response_model=MonthlyReportResponse)
async def get_monthly_report(user_id: str, year_month: str = "") -> MonthlyReportResponse:
    """Generate or retrieve a monthly decision quality report.

    If year_month is empty, uses the latest available month.
    Report includes:
      - Total operations / pass rate
      - Motivation distribution (买入理由分布)
      - Quality score trend (平均/最高/最低)
      - Loss pattern detection (高频亏损理由识别)
      - Recommendations

    Design doc: 07-decision-guard.md §四 — M5 复盘归因
    """
    if not year_month:
        # Use latest available month
        months = get_available_months(user_id)
        if not months:
            return MonthlyReportResponse(
                status="ok",
                report={"error": "no_data", "message": "暂无决策复盘记录"},
            )
        year_month = months[-1]

    # Validate year_month format
    if len(year_month) != 7 or year_month[4] != "-":
        raise HTTPException(
            status_code=400,
            detail=f"Invalid year_month format '{year_month}'. Expected 'YYYY-MM'.",
        )

    report = generate_monthly_report(user_id=user_id, year_month=year_month)
    return MonthlyReportResponse(status="ok", report=report)


@router.get("/api/decisions/monthly-report/{user_id}/summary", response_model=MonthlyReportSummaryResponse)
async def get_monthly_report_summary_endpoint(user_id: str) -> MonthlyReportSummaryResponse:
    """Get a quick summary of available monthly reports.

    Returns available months, latest avg score, total reviews.
    Used by frontend to show report availability before full generation.
    """
    summary = get_monthly_report_summary(user_id)
    return MonthlyReportSummaryResponse(
        status="ok",
        available_months=summary.get("available_months", []),  # type: ignore[arg-type]
        latest_month=str(summary.get("latest_month", "")),
        latest_avg_score=float(summary.get("latest_avg_score", 0.0)),
        total_reviews_all_time=int(summary.get("total_reviews_all_time", 0)),
    )


# ---- M6 W1: Three-Perspective Second Opinion (09-advisor-features.md §二) ----

@router.post("/api/decisions/multi-view", response_model=MultiViewReviewResponse)
async def post_multi_view_review(req: MultiViewReviewRequest) -> MultiViewReviewResponse:
    """Generate three-perspective second opinion for a major portfolio decision.

    Triggers when:
      - Transaction amount > 20% of total portfolio
      - First time buying a major asset class (gold, real estate, crypto)
      - Single asset would exceed 25% concentration after trade

    Returns 3 views (conservative/long-term/behavioral) each 50-80 chars,
    selected from fixed template bank. AI only selects + fills blanks.

    Key invariant: no market predictions, no position recommendations.

    Design doc: 09-advisor-features.md §二
    """
    from domain.models.multi_perspective import MultiViewRequest
    from use_cases.run_multi_view_review import generate_portfolio_review
    from infra.knowledge import get_multi_view_advisor

    advisor = get_multi_view_advisor()

    domain_request = MultiViewRequest(
        user_id=req.user_id,
        asset_name=req.asset_name,
        asset_class=req.asset_class,
        transaction_amount=req.transaction_amount,
        current_position_value=req.current_position_value,
        total_portfolio_value=req.total_portfolio_value,
        recent_return_pct=req.recent_return_pct,
        historical_return_pct=req.historical_return_pct,
        loss_pct=req.loss_pct,
        days_since_rebalance=req.days_since_rebalance,
    )

    review = generate_portfolio_review(domain_request, advisor)

    if review is None:
        return MultiViewReviewResponse(
            status="ok",
            review=None,
            triggered=False,
            triggers_met=[],
            summary_text="",
        )

    return MultiViewReviewResponse(
        status="ok",
        review=review.to_dict(),
        triggered=True,
        triggers_met=review.triggers_met,
        summary_text=review.summary_text,
    )

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.domain import (
    AttributionMetric,
    BuySignal,
    CandidateQualityScores,
    CandidateReadinessHint,
    CandidateTrendDiagnosis,
    MainlineBreadthSnapshot,
    MainlineScore,
    OrderDraft,
    OrderResult,
    Position,
    RiskAlert,
    SellSignal,
)


class MainlineBriefResponse(BaseModel):
    rankings: list[MainlineScore]
    leaders: list[str]
    core_middles: list[str]
    followups: list[str]
    watchwords: list[str]
    risk_prompts: list[str]
    suggested_total_exposure: float


class MainlineSnapshotResponse(BaseModel):
    current_mainlines: list[MainlineScore]
    market_phase: str
    heat_change: str


class MainlineSummaryItem(BaseModel):
    name: str
    group: str | None = None
    total_score: float
    tier: str
    health_label: str | None = None


class MainlineSummaryResponse(BaseModel):
    timestamp: datetime
    market_phase: str
    top_mainline: "TopMainlineResponse"
    top_rankings: list[MainlineSummaryItem]
    cache_hit: bool
    cache_age_seconds: float | None = None
    cache_ttl_seconds: int


class EmergingMainlineItem(BaseModel):
    name: str
    group: str | None = None
    stage: Literal["early_watch", "warming", "confirmed_hot"]
    suggestion: Literal["watch", "probe", "confirm", "avoid_chase"]
    action_plan: Literal["observe", "probe_small", "hold_and_confirm", "avoid_chase"]
    confidence: Literal["low", "medium", "high"]
    position_budget_pct: float
    early_score: float
    current_rank: int
    previous_rank: int | None = None
    rank_change: int | None = None
    current_score: float
    previous_score: float | None = None
    score_change: float | None = None
    avoid_chase: bool
    catalyst_tags: list[str] = Field(default_factory=list)
    reasons: list[str]
    leaders: list[str] = Field(default_factory=list)
    dedupe_key: str


class EmergingMainlinesResponse(BaseModel):
    timestamp: datetime
    lookback_minutes: int
    items: list[EmergingMainlineItem]


class CoreCandidatesResponse(BaseModel):
    mainline: str
    leaders: list[str]
    core_middles: list[str]
    followups: list[str]
    excluded: list[str]


class BuyWatchlistResponse(BaseModel):
    mainline: str
    signal_type: str
    items: list[dict]


class SellWatchlistResponse(BaseModel):
    items: list[dict]


class PortfolioInspectResponse(BaseModel):
    positions: list[dict]
    health_score: float


class ExposureResponse(BaseModel):
    total_exposure: float
    mainline_exposure: float
    non_mainline_exposure: float
    single_name_concentration: float
    risk_level: str


class AlertsResponse(BaseModel):
    items: list[dict]


class PollingWindow(BaseModel):
    name: str
    start: str
    end: str


class PollingPolicyResponse(BaseModel):
    default_interval_minutes: int
    intensive_interval_minutes: int
    intensive_windows: list[PollingWindow]
    quiet_windows: list[PollingWindow]
    push_gates: dict[str, list[str]]


class PollingPolicyHintResponse(BaseModel):
    immediate_push_recommended: bool
    summary_push_recommended: bool
    trigger_reasons: list[str] = Field(default_factory=list)
    dedupe_keys: list[str] = Field(default_factory=list)


class MarketStoryResponse(BaseModel):
    headline: str
    strongest_emerging_name: str | None = None
    strongest_emerging_stage: str | None = None
    strongest_emerging_reason: str | None = None
    caution_notes: list[str] = Field(default_factory=list)


class AdvicePlanRequest(BaseModel):
    ticker: str
    action: Literal["buy", "sell"]
    target_position_pct: float = Field(ge=0, le=1)
    reason: str
    account_id: str


class AdvicePlan(BaseModel):
    plan_id: str
    ticker: str
    action: Literal["buy", "sell"]
    account_id: str
    target_position_pct: float
    suggested_quantity: int = 0
    reference_price: float = 0
    passed_checks: bool
    estimated_exposure: float
    risk_notes: list[str] = Field(default_factory=list)

    @classmethod
    def from_draft(cls, draft: OrderDraft) -> "AdvicePlan":
        return cls(
            plan_id=draft.draft_id,
            ticker=draft.ticker,
            action=draft.action,
            account_id=draft.account_id,
            target_position_pct=draft.target_position_pct,
            suggested_quantity=draft.order_volume,
            reference_price=draft.reference_price,
            passed_checks=draft.passed_checks,
            estimated_exposure=draft.estimated_exposure,
            risk_notes=draft.risk_notes,
        )


class AdvicePlanResponse(BaseModel):
    passed_checks: bool
    plan: AdvicePlan
    requires_confirmation: bool


class AcknowledgeAdviceRequest(BaseModel):
    plan_id: str
    acknowledged: bool


class AdviceResult(BaseModel):
    advice_id: str
    plan_id: str
    status: str

    @classmethod
    def from_result(cls, result: OrderResult) -> "AdviceResult":
        return cls(
            advice_id=result.order_id,
            plan_id=result.draft_id,
            status=result.status,
        )


class AcknowledgeAdviceResponse(BaseModel):
    result: AdviceResult


class AdviceStatusResponse(BaseModel):
    advice_id: str
    plan_id: str
    status: str


class DismissAdviceRequest(BaseModel):
    advice_id: str
    account_id: str


class DailyReviewResponse(BaseModel):
    summary: str
    mainline_changes: list[str]
    candidate_performance: list[str]
    position_changes: list[str]
    discipline_notes: list[str]


class TopMainlineResponse(BaseModel):
    name: str
    score: float
    group: str | None = None


class DiagnosePortfolioHealthResponse(BaseModel):
    health_score: float
    positions: list[Position]


class BackfillHistoryRequest(BaseModel):
    mainline: str | None = None
    limit: int = Field(default=5, ge=1, le=20)
    periods: list[str] = Field(default_factory=lambda: ["1d", "5m", "15m"])


class BackfillHistoryItemResponse(BaseModel):
    ticker: str
    name: str
    missing_before: list[str] = Field(default_factory=list)
    downloaded: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)


class BackfillHistoryResponse(BaseModel):
    mainline: str
    requested_periods: list[str]
    downloaded_periods: dict[str, int]
    items: list[BackfillHistoryItemResponse] = Field(default_factory=list)


class FullDiagnoseResponse(BaseModel):
    timestamp: datetime
    market_phase: str
    top_mainline: TopMainlineResponse
    rankings: list[MainlineScore]
    suggested_exposure: float
    alerts: list[RiskAlert]
    buy_signals: list[BuySignal]
    sell_signals: list[SellSignal]
    portfolio_health: DiagnosePortfolioHealthResponse
    exposure: ExposureResponse
    emerging_mainlines: list[EmergingMainlineItem] = Field(default_factory=list)
    polling_policy_hint: PollingPolicyHintResponse
    market_story: MarketStoryResponse
    trend_diagnosis_version: str = "v1"
    mainline_breadth: MainlineBreadthSnapshot | None = None
    candidate_diagnostics: list[CandidateTrendDiagnosis] = Field(default_factory=list)
    diagnostics_error: str | None = None
    candidate_readiness: list[CandidateReadinessHint] = Field(default_factory=list)
    diagnostic_focus_mainline: str | None = None
    diagnostic_fallback_applied: bool = False
    diagnostic_fallback_reason: str | None = None
    diagnostic_fallback_trace: list[dict[str, Any]] = Field(default_factory=list)

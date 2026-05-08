import os
import re
from datetime import datetime, timedelta
from threading import Lock

from fastapi import APIRouter, HTTPException, Query

from app.core.config import get_settings
from app.models.api import (
    AcknowledgeAdviceRequest,
    AcknowledgeAdviceResponse,
    AdvicePlan,
    AdvicePlanRequest,
    AdvicePlanResponse,
    AdviceResult,
    AdviceStatusResponse,
    AlertsResponse,
    BackfillHistoryRequest,
    BackfillHistoryResponse,
    BuyWatchlistResponse,
    CoreCandidatesResponse,
    DailyReviewResponse,
    DismissAdviceRequest,
    EmergingMainlineItem,
    EmergingMainlinesResponse,
    ExposureResponse,
    FullDiagnoseResponse,
    DiagnosePortfolioHealthResponse,
    MainlineBriefResponse,
    MainlineSnapshotResponse,
    MainlineSummaryItem,
    MainlineSummaryResponse,
    MarketStoryResponse,
    PollingPolicyHintResponse,
    PollingPolicyResponse,
    PortfolioInspectResponse,
    SellWatchlistResponse,
    TopMainlineResponse,
)
from app.models.common import ApiEnvelope
from app.models.domain import MainlineScore
from app.repositories.config_repository import ConfigRepository
from app.repositories.log_repository import JsonlLogRepository
from app.repositories.polling_policy_repository import PollingPolicyRepository
from app.services.core_stock_selector import CoreStockSelector
from app.services.execution_service import ExecutionService
from app.services.emerging_detector import EmergingMainlineDetector
from app.services.mainline_ranker import MainlineRanker
from app.services.market_data_service import MarketDataService
from app.services.order_guard import OrderGuard
from app.services.portfolio_inspector import PortfolioInspector
from app.services.qmt_connector import QmtConnector
from app.services.review_engine import ReviewEngine
from app.services.risk_engine import RiskEngine
from app.services.signal_engine import SignalEngine


settings = get_settings()
router = APIRouter()
config_repository = ConfigRepository(settings.mainline_config_path)
polling_policy_repository = PollingPolicyRepository(settings.polling_policy_path)
qmt_connector = QmtConnector(settings)
market_data_service = MarketDataService(qmt_connector, config_repository)
ranker = MainlineRanker()
selector = CoreStockSelector()
signal_engine = SignalEngine()
risk_engine = RiskEngine()
portfolio_inspector = PortfolioInspector()
order_guard = OrderGuard(settings, qmt_connector)
execution_service = ExecutionService(qmt_connector, settings)
emerging_detector = EmergingMainlineDetector(settings.emerging_snapshot_path)
review_engine = ReviewEngine()
log_repository = JsonlLogRepository(settings.data_dir)

# /diagnose/full 是重型聚合接口：一次调用会触发主线评分、候选诊断、持仓、买卖信号等多组 QMT 请求。
# 前端“循环主线”或 Hermes/cron 短时间重复请求时，多个重型请求并发会把 QMT/uvicorn 线程池拖到 30s+，
# 表现为客户端反复 Request timed out。这里用 single-flight + 短 TTL 缓存：
# - 30 秒内重复请求直接复用结果；
# - 缓存过期但已有请求正在计算时，优先返回 stale 结果，避免再开一个重型 QMT 计算；
# - 无缓存的冷启动请求才实际计算。
_DIAGNOSE_CACHE_TTL = timedelta(seconds=30)
_diagnose_cache_lock = Lock()
_diagnose_compute_lock = Lock()
_diagnose_cached_response: FullDiagnoseResponse | None = None
_diagnose_cached_at: datetime | None = None


def _diagnose_cache_enabled() -> bool:
    """Disable cache under pytest because tests monkeypatch route dependencies per case."""
    return "PYTEST_CURRENT_TEST" not in os.environ


def ok(data):
    return ApiEnvelope(data=data)


def _mask_identifier(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"


def _redact_local_paths(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"[A-Za-z]:\\[^\r\n\"']+", "[local-path]", value)
    return re.sub(r"C:\\Users\\[^\\\s]+", r"C:\\Users\\[user]", value, flags=re.IGNORECASE)


def _build_polling_policy_hint(emerging_items: list[dict]) -> PollingPolicyHintResponse:
    trigger_reasons: list[str] = []
    dedupe_keys: list[str] = []
    immediate_push = False

    for item in emerging_items:
        if item["dedupe_key"] not in dedupe_keys:
            dedupe_keys.append(item["dedupe_key"])
        if item["suggestion"] == "probe" and "emerging suggestion is probe" not in trigger_reasons:
            trigger_reasons.append("emerging suggestion is probe")
            immediate_push = True
        if item.get("previous_rank") is not None and item["current_rank"] <= 5 < item["previous_rank"]:
            if "mainline enters top 5" not in trigger_reasons:
                trigger_reasons.append("mainline enters top 5")
            immediate_push = True
        if item.get("score_change") is not None and item["score_change"] >= 2.0:
            if "mainline score changes by at least 2.0" not in trigger_reasons:
                trigger_reasons.append("mainline score changes by at least 2.0")
            immediate_push = True
        if item["suggestion"] == "avoid_chase" and "confirmed hot mainline avoid_chase" not in trigger_reasons:
            trigger_reasons.append("confirmed hot mainline avoid_chase")
        if item["suggestion"] == "watch" and "emerging suggestion is watch" not in trigger_reasons:
            trigger_reasons.append("emerging suggestion is watch")

    summary_push = (not immediate_push) and any(item["suggestion"] in {"watch", "avoid_chase"} for item in emerging_items)
    return PollingPolicyHintResponse(
        immediate_push_recommended=immediate_push,
        summary_push_recommended=summary_push,
        trigger_reasons=trigger_reasons,
        dedupe_keys=dedupe_keys,
    )


def _build_market_story(emerging_items: list[dict]) -> MarketStoryResponse:
    if not emerging_items:
        return MarketStoryResponse(headline="当前未发现明显的早期升温主线，仍以已确认主线和风险控制为主。")

    strongest = emerging_items[0]
    if strongest["action_plan"] == "probe_small":
        headline = f"{strongest['name']}出现升温信号，可小仓试探，但不宜把确认信号当成早期潜伏信号追高。"
    elif strongest["action_plan"] == "avoid_chase":
        headline = f"{strongest['name']}已进入高分确认区，更适合作为强势确认观察，不宜再按早期启动思路追高。"
    else:
        headline = f"{strongest['name']}进入升温观察区，先跟踪结构强化和扩散情况，不宜贸然追价。"

    caution_notes = [
        f"{item['name']} 已处于确认区过热状态，避免追高。"
        for item in emerging_items
        if item["suggestion"] == "avoid_chase"
    ]
    return MarketStoryResponse(
        headline=headline,
        strongest_emerging_name=strongest["name"],
        strongest_emerging_stage=strongest["stage"],
        strongest_emerging_reason=(strongest["reasons"][0] if strongest.get("reasons") else None),
        caution_notes=caution_notes,
    )


def build_market_context() -> dict:
    scores = ranker.rank_mainlines(market_data_service.get_mainline_scores())
    if not scores:
        raise HTTPException(status_code=503, detail="No mainline data available from xtdata")
    active_groups: list[str] = []
    for item in scores:
        if item.group and item.group not in active_groups:
            active_groups.append(item.group)
        if len(active_groups) >= 2:
            break
    pools_by_branch = {item.name: market_data_service.get_stock_pool(item.name) for item in scores[:8]}
    core_tickers_by_group: dict[str, set[str]] = {}
    for branch_name, stocks in pools_by_branch.items():
        group, _ = market_data_service.split_name(branch_name)
        core_tickers_by_group.setdefault(group, set()).update(item.ticker for item in stocks[:6])
    positions = market_data_service.get_positions()
    alerts = risk_engine.alerts_for_scores(scores=scores, positions=positions)
    exposure = risk_engine.exposure(positions)
    return {
        "scores": scores,
        "active_groups": active_groups,
        "pools_by_branch": pools_by_branch,
        "core_tickers_by_group": core_tickers_by_group,
        "positions": positions,
        "alerts": alerts,
        "exposure": exposure,
    }


@router.get("/mainlines/daily-brief", tags=["mainlines"])
def get_daily_brief() -> ApiEnvelope[MainlineBriefResponse]:
    context = build_market_context()
    top_branch = context["scores"][0].name
    top_group = context["scores"][0].group or market_data_service.split_name(top_branch)[0]
    groups = selector.split_roles(market_data_service.get_stock_pool(top_group))
    response = MainlineBriefResponse(
        rankings=context["scores"],
        leaders=groups["leaders"],
        core_middles=groups["core_middles"],
        followups=groups["followups"],
        watchwords=[
            f"Focus on {top_group} first and watch its leading branch rotation.",
            "Keep non-mainline exposure on a trim-only basis.",
            "Only add names that still sit in the core branch watchlist and show real turnover.",
        ],
        risk_prompts=[item.detail for item in context["alerts"]],
        suggested_total_exposure=risk_engine.suggested_total_exposure(context["scores"]),
    )
    return ok(response)


@router.get("/mainlines/summary", tags=["mainlines"])
def get_mainline_summary(limit: int = Query(default=5, ge=1, le=20)) -> ApiEnvelope[MainlineSummaryResponse]:
    before_meta = market_data_service.get_mainline_scores_cache_meta()
    scores = ranker.rank_mainlines(market_data_service.get_mainline_scores())
    if not scores:
        raise HTTPException(status_code=503, detail="No mainline data available from xtdata")
    after_meta = market_data_service.get_mainline_scores_cache_meta()
    cache_hit = bool(before_meta.get("has_cache") and (before_meta.get("age_seconds") or 0) <= before_meta.get("ttl_seconds", 0))
    top_score = scores[0]
    return ok(
        MainlineSummaryResponse(
            timestamp=datetime.now(),
            market_phase=ranker.market_phase(scores),
            top_mainline=TopMainlineResponse(name=top_score.name, score=top_score.total_score, group=top_score.group),
            top_rankings=[
                MainlineSummaryItem(
                    name=item.name,
                    group=item.group,
                    total_score=item.total_score,
                    tier=item.tier,
                    health_label=item.health_label,
                )
                for item in scores[:limit]
            ],
            cache_hit=cache_hit,
            cache_age_seconds=after_meta.get("age_seconds"),
            cache_ttl_seconds=int(after_meta.get("ttl_seconds") or 0),
        )
    )


@router.get("/mainlines/snapshot", tags=["mainlines"])
def get_mainline_snapshot(realtime: bool = Query(default=False)) -> ApiEnvelope[MainlineSnapshotResponse]:
    scores = ranker.rank_mainlines(market_data_service.get_mainline_scores())
    heat_change = "intraday snapshot refreshed from xtdata" if realtime else "using latest cached market snapshot"
    return ok(
        MainlineSnapshotResponse(
            current_mainlines=scores,
            market_phase=ranker.market_phase(scores),
            heat_change=heat_change,
        )
    )


@router.get("/mainlines/scores", tags=["mainlines"])
def get_mainline_scores() -> ApiEnvelope[list]:
    return ok(ranker.rank_mainlines(market_data_service.get_mainline_scores()))


@router.get("/mainlines/emerging", tags=["mainlines"])
def get_emerging_mainlines(
    lookback_minutes: int = Query(default=30, ge=5, le=240),
    limit: int = Query(default=8, ge=1, le=20),
) -> ApiEnvelope[EmergingMainlinesResponse]:
    scores = ranker.rank_mainlines(market_data_service.get_mainline_scores())
    if not scores:
        raise HTTPException(status_code=503, detail="No mainline data available from xtdata")
    opportunities = emerging_detector.detect(scores, lookback_minutes=lookback_minutes, limit=limit)
    for item in opportunities:
        leaders = market_data_service.get_stock_pool(item["name"])[:5]
        item["leaders"] = [f"{leader.name}({leader.ticker})" for leader in leaders]
    emerging_detector.record_snapshot(scores)
    return ok(
        EmergingMainlinesResponse(
            timestamp=datetime.now(),
            lookback_minutes=lookback_minutes,
            items=[EmergingMainlineItem(**item) for item in opportunities],
        )
    )


@router.get("/candidates/core", tags=["candidates"])
def get_core_candidates(mainline: str = Query(default="AI算力")) -> ApiEnvelope[CoreCandidatesResponse]:
    stocks = market_data_service.get_stock_pool(mainline)
    groups = selector.split_roles(stocks)
    return ok(
        CoreCandidatesResponse(
            mainline=mainline,
            leaders=groups["leaders"],
            core_middles=groups["core_middles"],
            followups=groups["followups"],
            excluded=groups["excluded"],
        )
    )


@router.get("/signals/buy-watchlist", tags=["signals"])
def get_buy_watchlist(
    mainline: str = Query(default="AI算力"),
    signal_type: str = Query(default="all"),
) -> ApiEnvelope[BuyWatchlistResponse]:
    stocks = market_data_service.get_stock_pool(mainline)
    items = signal_engine.build_buy_watchlist(stocks, signal_type)
    return ok(BuyWatchlistResponse(mainline=mainline, signal_type=signal_type, items=[item.model_dump() for item in items]))


@router.get("/signals/sell-watchlist", tags=["signals"])
def get_sell_watchlist() -> ApiEnvelope[SellWatchlistResponse]:
    context = build_market_context()
    items = signal_engine.build_sell_watchlist(
        positions=context["positions"],
        active_groups=context["active_groups"],
        core_tickers_by_group=context["core_tickers_by_group"],
    )
    return ok(SellWatchlistResponse(items=[item.model_dump() for item in items]))


@router.get("/portfolio/inspect", tags=["portfolio"])
def inspect_portfolio() -> ApiEnvelope[PortfolioInspectResponse]:
    context = build_market_context()
    positions, health_score = portfolio_inspector.inspect(
        context["positions"],
        active_groups=context["active_groups"],
        core_tickers_by_group=context["core_tickers_by_group"],
    )
    return ok(PortfolioInspectResponse(positions=[item.model_dump() for item in positions], health_score=health_score))


@router.get("/risk/exposure", tags=["risk"])
def get_exposure() -> ApiEnvelope[ExposureResponse]:
    exposure = risk_engine.exposure(market_data_service.get_positions())
    return ok(ExposureResponse(**exposure))


@router.get("/risk/alerts", tags=["risk"])
def get_alerts() -> ApiEnvelope[AlertsResponse]:
    context = build_market_context()
    return ok(AlertsResponse(items=[item.model_dump() for item in context["alerts"]]))


@router.post("/advice/prepare", tags=["advice"])
def prepare_advice(request: AdvicePlanRequest) -> ApiEnvelope[AdvicePlanResponse]:
    expected_account = settings.qmt_account_id or qmt_connector.get_account_id()
    if request.account_id != expected_account:
        raise HTTPException(status_code=400, detail="Unknown account_id")
    draft = order_guard.prepare(
        ticker=request.ticker,
        action=request.action,
        target_position_pct=request.target_position_pct,
        account_id=request.account_id,
        positions=market_data_service.get_positions(),
    )
    execution_service.remember_draft(draft)
    log_repository.append("advice_plans", AdvicePlan.from_draft(draft).model_dump())
    return ok(
        AdvicePlanResponse(
            passed_checks=draft.passed_checks,
            plan=AdvicePlan.from_draft(draft),
            requires_confirmation=not settings.advisory_only_mode,
        )
    )


@router.post("/advice/acknowledge", tags=["advice"])
def acknowledge_advice(request: AcknowledgeAdviceRequest) -> ApiEnvelope[AcknowledgeAdviceResponse]:
    draft = execution_service.get_draft(request.plan_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Advice plan not found")
    result = execution_service.confirm(draft, request.acknowledged)
    advice_result = AdviceResult.from_result(result)
    log_repository.append("advice_results", advice_result.model_dump())
    return ok(AcknowledgeAdviceResponse(result=advice_result))


@router.get("/advice/status", tags=["advice"])
def get_advice_status(advice_id: str = Query(...)) -> ApiEnvelope[AdviceStatusResponse]:
    result = execution_service.get_status(advice_id)
    return ok(
        AdviceStatusResponse(
            advice_id=result.order_id,
            plan_id=result.draft_id,
            status=result.status,
        )
    )


@router.post("/advice/dismiss", tags=["advice"])
def dismiss_advice(request: DismissAdviceRequest) -> ApiEnvelope[AdviceStatusResponse]:
    expected_account = settings.qmt_account_id or qmt_connector.get_account_id()
    if request.account_id != expected_account:
        raise HTTPException(status_code=400, detail="Unknown account_id")
    result = execution_service.cancel(request.advice_id)
    return ok(
        AdviceStatusResponse(
            advice_id=result.order_id,
            plan_id=result.draft_id,
            status=result.status,
        )
    )


@router.get("/reviews/daily", tags=["reviews"])
def get_daily_review() -> ApiEnvelope[DailyReviewResponse]:
    context = build_market_context()
    top_branch = context["scores"][0].name
    top_candidates = market_data_service.get_stock_pool(top_branch)
    sell_watchlist = signal_engine.build_sell_watchlist(
        positions=context["positions"],
        active_groups=context["active_groups"],
        core_tickers_by_group=context["core_tickers_by_group"],
    )
    review = review_engine.generate_daily_review(
        scores=context["scores"],
        candidates=top_candidates,
        positions=context["positions"],
        sell_watchlist=sell_watchlist,
        alerts=context["alerts"],
    )
    log_repository.append("daily_reviews", review)
    return ok(DailyReviewResponse(**review))


@router.get("/system/health", tags=["system"])
def health_check() -> ApiEnvelope[dict]:
    account_id = None
    userdata_path = None
    discovery_error = ""
    try:
        account_id = settings.qmt_account_id or qmt_connector.get_account_id()
        if qmt_connector.get_userdata_path():
            userdata_path = "[configured]"
    except Exception as exc:
        discovery_error = _redact_local_paths(str(exc))

    return ok(
        {
            "market_connected": qmt_connector.check_market_connection(),
            "account_connected": qmt_connector.check_account_connection(),
            "advisory_only_mode": settings.advisory_only_mode,
            "live_order_submission_enabled": settings.enable_order_submission and not settings.advisory_only_mode,
            "qmt_account_id": _mask_identifier(account_id),
            "qmt_userdata_path": userdata_path,
            "discovery_error": discovery_error,
        }
    )


@router.get("/system/polling-policy", tags=["system"])
def get_polling_policy() -> ApiEnvelope[PollingPolicyResponse]:
    return ok(PollingPolicyResponse(**polling_policy_repository.load()))


DEFAULT_ADJACENT_GROUPS = {
    "AI算力": ["半导体", "消费电子", "信创数据"],
    "半导体": ["AI算力", "消费电子"],
    "消费电子": ["AI算力", "半导体"],
    "信创数据": ["AI算力", "半导体"],
}


def _adjacent_groups(group: str | None) -> list[str]:
    try:
        configured = config_repository.load_mainline_config().get("fallback", {}).get("adjacent_groups", {})
    except Exception:
        configured = {}
    adjacency = configured or DEFAULT_ADJACENT_GROUPS
    return list(adjacency.get(group or "", []))


def _trace_entry(stage: str, mainline: str, selected: bool, reason: str) -> dict:
    return {"stage": stage, "mainline": mainline, "selected": selected, "reason": reason}


def _select_diagnostic_payload(
    top_branch: str,
    scores: list[MainlineScore],
    emerging_items: list[dict],
) -> tuple[str, dict, bool, str | None, list[dict]]:
    trace: list[dict] = []
    diagnostics = market_data_service.get_candidate_diagnostics(top_branch, limit=5)
    candidate_items = diagnostics.get("candidate_diagnostics", [])
    all_tail = bool(candidate_items) and all(item.get("stage") == "tail_rebound" for item in candidate_items)
    if not all_tail:
        trace.append(_trace_entry("top_mainline", top_branch, True, "selected"))
        return top_branch, diagnostics, False, None, trace
    trace.append(_trace_entry("top_mainline", top_branch, False, "all_tail_rebound"))

    for item in emerging_items:
        if item.get("name") == top_branch:
            continue
        if item.get("suggestion") == "avoid_chase":
            trace.append(_trace_entry("emerging", item.get("name", ""), False, "avoid_chase"))
            continue
        fallback_name = item.get("name")
        if not fallback_name:
            continue
        fallback_diagnostics = market_data_service.get_candidate_diagnostics(fallback_name, limit=5)
        fallback_candidates = fallback_diagnostics.get("candidate_diagnostics", [])
        if not fallback_candidates:
            trace.append(_trace_entry("emerging", fallback_name, False, "no_candidates"))
            continue
        if all(diag.get("stage") == "tail_rebound" for diag in fallback_candidates):
            trace.append(_trace_entry("emerging", fallback_name, False, "all_tail_rebound"))
            continue
        trace.append(_trace_entry("emerging", fallback_name, True, "selected"))
        return fallback_name, fallback_diagnostics, True, "top_mainline_all_tail_rebound", trace

    ranked_same_group = [
        score for score in scores
        if score.name != top_branch and score.group == top_branch.split("/", 1)[0] and (score.diffusion_score <= 3.5 or score.persistence_score <= 1.8)
    ]
    ranked_same_group.sort(key=lambda score: (score.diffusion_score + score.persistence_score, -score.capital_strength_score, -score.total_score))
    for score in ranked_same_group[:5]:
        fallback_name = score.name
        fallback_diagnostics = market_data_service.get_candidate_diagnostics(fallback_name, limit=5)
        fallback_candidates = fallback_diagnostics.get("candidate_diagnostics", [])
        if not fallback_candidates:
            trace.append(_trace_entry("same_group", fallback_name, False, "no_candidates"))
            continue
        if all(diag.get("stage") == "tail_rebound" for diag in fallback_candidates):
            trace.append(_trace_entry("same_group", fallback_name, False, "all_tail_rebound"))
            continue
        trace.append(_trace_entry("same_group", fallback_name, True, "selected"))
        return fallback_name, fallback_diagnostics, True, "rankings_same_group_branch_after_all_tail", trace

    top_group = next((score.group for score in scores if score.name == top_branch), top_branch.split("/", 1)[0])
    adjacent_groups = set(_adjacent_groups(top_group))
    adjacent_fallbacks = [
        score for score in scores
        if score.name != top_branch and score.group in adjacent_groups and (score.diffusion_score <= 3.4 or score.persistence_score <= 1.6)
    ]
    adjacent_fallbacks.sort(key=lambda score: (score.diffusion_score + score.persistence_score, -score.capital_strength_score, -score.total_score))
    for score in adjacent_fallbacks[:5]:
        fallback_name = score.name
        fallback_diagnostics = market_data_service.get_candidate_diagnostics(fallback_name, limit=5)
        fallback_candidates = fallback_diagnostics.get("candidate_diagnostics", [])
        if not fallback_candidates:
            trace.append(_trace_entry("adjacent", fallback_name, False, "no_candidates"))
            continue
        if all(diag.get("stage") == "tail_rebound" for diag in fallback_candidates):
            trace.append(_trace_entry("adjacent", fallback_name, False, "all_tail_rebound"))
            continue
        trace.append(_trace_entry("adjacent", fallback_name, True, "selected"))
        return fallback_name, fallback_diagnostics, True, "adjacent_theme_branch_after_all_tail", trace

    ranked_fallbacks = [
        score for score in scores
        if score.name != top_branch and (score.diffusion_score <= 3.2 or score.persistence_score <= 1.5)
    ]
    ranked_fallbacks.sort(key=lambda score: (score.diffusion_score + score.persistence_score, -score.capital_strength_score, -score.total_score))
    for score in ranked_fallbacks[:5]:
        fallback_name = score.name
        fallback_diagnostics = market_data_service.get_candidate_diagnostics(fallback_name, limit=5)
        fallback_candidates = fallback_diagnostics.get("candidate_diagnostics", [])
        if not fallback_candidates:
            trace.append(_trace_entry("market_wide", fallback_name, False, "no_candidates"))
            continue
        if all(diag.get("stage") == "tail_rebound" for diag in fallback_candidates):
            trace.append(_trace_entry("market_wide", fallback_name, False, "all_tail_rebound"))
            continue
        trace.append(_trace_entry("market_wide", fallback_name, True, "selected"))
        return fallback_name, fallback_diagnostics, True, "rankings_cooler_branch_after_all_tail", trace

    return top_branch, diagnostics, False, None, trace


@router.post("/diagnose/backfill-history", tags=["diagnostics"])
def backfill_diagnose_history(request: BackfillHistoryRequest) -> ApiEnvelope[BackfillHistoryResponse]:
    context = build_market_context()
    mainline = request.mainline or context["scores"][0].name
    result = market_data_service.backfill_candidate_history(mainline=mainline, limit=request.limit, periods=request.periods)
    return ok(BackfillHistoryResponse(**result))


def _build_full_diagnose_response() -> FullDiagnoseResponse:
    market_data_service.begin_request()
    try:
        context = build_market_context()
        scores = context["scores"]
        emerging_items = emerging_detector.detect(scores, lookback_minutes=30, limit=5)
        for item in emerging_items:
            leaders = market_data_service.get_stock_pool(item["name"])[:5]
            item["leaders"] = [f"{leader.name}({leader.ticker})" for leader in leaders]
        emerging_detector.record_snapshot(scores)
        polling_policy_hint = _build_polling_policy_hint(emerging_items)
        market_story = _build_market_story(emerging_items)
        market_phase = ranker.market_phase(scores)
        top_branch = scores[0].name
        buy_signals = signal_engine.build_buy_watchlist(
            market_data_service.get_stock_pool(top_branch),
            signal_type="all",
        )
        diagnostics_mainline, diagnostics, fallback_applied, fallback_reason, fallback_trace = _select_diagnostic_payload(top_branch, scores, emerging_items)
        sell_signals = signal_engine.build_sell_watchlist(
            positions=context["positions"],
            active_groups=context["active_groups"],
            core_tickers_by_group=context["core_tickers_by_group"],
        )
        positions_health, health_score = portfolio_inspector.inspect(
            context["positions"],
            active_groups=context["active_groups"],
            core_tickers_by_group=context["core_tickers_by_group"],
        )

        return FullDiagnoseResponse(
            timestamp=datetime.now(),
            market_phase=market_phase,
            top_mainline=TopMainlineResponse(
                name=scores[0].name,
                score=scores[0].total_score,
                group=scores[0].group,
            ),
            rankings=scores,
            suggested_exposure=risk_engine.suggested_total_exposure(scores),
            alerts=context["alerts"],
            buy_signals=buy_signals,
            sell_signals=sell_signals,
            portfolio_health=DiagnosePortfolioHealthResponse(
                health_score=health_score,
                positions=positions_health,
            ),
            exposure=ExposureResponse(**context["exposure"]),
            emerging_mainlines=[EmergingMainlineItem(**item) for item in emerging_items],
            polling_policy_hint=polling_policy_hint,
            market_story=market_story,
            trend_diagnosis_version=diagnostics.get("trend_diagnosis_version", "v1"),
            mainline_breadth=diagnostics.get("mainline_breadth"),
            candidate_diagnostics=diagnostics.get("candidate_diagnostics", []),
            diagnostics_error=diagnostics.get("diagnostics_error"),
            candidate_readiness=diagnostics.get("candidate_readiness", []),
            diagnostic_focus_mainline=diagnostics_mainline,
            diagnostic_fallback_applied=fallback_applied,
            diagnostic_fallback_reason=fallback_reason,
            diagnostic_fallback_trace=fallback_trace,
        )
    finally:
        market_data_service.end_request()


@router.get("/diagnose/full", tags=["diagnostics"])
def diagnose_full() -> ApiEnvelope[FullDiagnoseResponse]:
    """
    返回 Hermes 生成推送消息所需的完整信息。
    避免 Hermes 多次调用不同接口，一次性返回市场状态、信号、告警、持仓健康度。
    重复/并发请求复用短期缓存，防止“循环主线”把 QMT 重型取数打到超时。
    """
    global _diagnose_cached_response, _diagnose_cached_at

    if not _diagnose_cache_enabled():
        return ok(_build_full_diagnose_response())

    now = datetime.now()
    with _diagnose_cache_lock:
        if _diagnose_cached_response is not None and _diagnose_cached_at is not None:
            if now - _diagnose_cached_at <= _DIAGNOSE_CACHE_TTL:
                return ok(_diagnose_cached_response)

    acquired = _diagnose_compute_lock.acquire(blocking=False)
    if not acquired:
        with _diagnose_cache_lock:
            if _diagnose_cached_response is not None:
                return ok(_diagnose_cached_response)
        acquired = _diagnose_compute_lock.acquire(timeout=25)
        if not acquired:
            raise HTTPException(status_code=503, detail="diagnose/full is still warming up; retry shortly")

    try:
        # 等锁期间可能已有其他请求刷新了缓存，避免重复计算。
        now = datetime.now()
        with _diagnose_cache_lock:
            if _diagnose_cached_response is not None and _diagnose_cached_at is not None:
                if now - _diagnose_cached_at <= _DIAGNOSE_CACHE_TTL:
                    return ok(_diagnose_cached_response)

        response = _build_full_diagnose_response()
        with _diagnose_cache_lock:
            _diagnose_cached_response = response
            _diagnose_cached_at = datetime.now()
        return ok(response)
    finally:
        _diagnose_compute_lock.release()

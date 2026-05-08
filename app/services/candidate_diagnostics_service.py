from __future__ import annotations

from statistics import mean
from typing import Any, Callable, Protocol

from app.models.domain import (
    AttributionMetric,
    CandidateQualityScores,
    CandidateReadinessHint,
    CandidateTrendDiagnosis,
    MainlineBreadthSnapshot,
    StockCandidate,
)
from app.services.quality_score_engine import QualityScoreEngine
from app.services.trend_feature_extractor import TrendFeatureExtractor
from app.services.trend_stage_engine import TrendStageEngine


class CandidateDiagnosticsConnector(Protocol):
    def get_daily_bars(self, codes, count: int, fields: list[str]): ...
    def get_minute_bars(self, codes, period: str, count: int, fields: list[str]): ...


class CandidateDiagnosticsService:
    """Build non-Level2 candidate trend diagnostics for one mainline.

    This class owns the diagnosis workflow: breadth snapshot, bar prefetch,
    stage classification, quality scoring, readiness, and attribution. The
    surrounding MarketDataService remains responsible for universe/stock-pool
    construction because that logic depends on mainline configuration and QMT
    sector membership.
    """

    def __init__(
        self,
        connector: CandidateDiagnosticsConnector,
        trend_feature_extractor: TrendFeatureExtractor | None = None,
        trend_stage_engine: TrendStageEngine | None = None,
        quality_score_engine: QualityScoreEngine | None = None,
    ) -> None:
        self.connector = connector
        self.trend_feature_extractor = trend_feature_extractor or TrendFeatureExtractor()
        self.trend_stage_engine = trend_stage_engine or TrendStageEngine()
        self.quality_score_engine = quality_score_engine or QualityScoreEngine()

    def get_candidate_diagnostics(
        self,
        mainline: str,
        limit: int,
        seed_candidates: Callable[[str, int], list[StockCandidate]],
        breadth_pool: Callable[[str], list[StockCandidate]],
        coerce_bars: Callable[[Any, str], list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        coerce = coerce_bars or self.coerce_bars
        candidates = seed_candidates(mainline, limit)
        # 用全主线更宽的样本来计算 breadth，避免只用 top-N 热门样本导致
        # advancers_ratio / ma20_above_ratio 虚高，误将所有候选判为 tail_rebound
        breadth_stats = self.build_mainline_breadth_stats(mainline, breadth_pool(mainline))
        diagnostics: list[CandidateTrendDiagnosis] = []
        readiness: list[CandidateReadinessHint] = []
        diagnostics_error = ""

        all_tickers = [c.ticker for c in candidates]
        bar_fields = ["open", "high", "low", "close", "volume"]
        try:
            all_daily_bars = self.connector.get_daily_bars(
                all_tickers, count=30, fields=bar_fields,
            ) if all_tickers else {}
            all_minute_bars = self.connector.get_minute_bars(
                all_tickers, period="5m", count=12, fields=bar_fields,
            ) if all_tickers else {}
        except Exception as exc:
            diagnostics_error = str(exc)
            all_daily_bars = {}
            all_minute_bars = {}

        for candidate in candidates:
            try:
                daily_bars = coerce(all_daily_bars, candidate.ticker)
                minute_bars = coerce(all_minute_bars, candidate.ticker)
            except Exception as exc:
                diagnostics_error = str(exc)
                readiness.append(
                    CandidateReadinessHint(
                        ticker=candidate.ticker,
                        name=candidate.name,
                        ready=False,
                        missing=["1d", "5m"],
                        reason=str(exc),
                    )
                )
                continue

            missing_periods: list[str] = []
            if not daily_bars:
                missing_periods.append("1d")
            if not minute_bars:
                missing_periods.append("5m")
            if missing_periods:
                if not diagnostics_error:
                    diagnostics_error = f"missing historical bars for {candidate.ticker}"
                readiness.append(
                    CandidateReadinessHint(
                        ticker=candidate.ticker,
                        name=candidate.name,
                        ready=False,
                        missing=missing_periods,
                        reason=diagnostics_error,
                    )
                )
                continue

            readiness.append(
                CandidateReadinessHint(
                    ticker=candidate.ticker,
                    name=candidate.name,
                    ready=True,
                    missing=[],
                    reason="ready",
                )
            )
            trend_features = self.trend_feature_extractor.extract(daily_bars, minute_bars)
            stage_result = self.trend_stage_engine.classify(trend_features, breadth_stats.stats)
            quality_result = self.quality_score_engine.score(
                stage=stage_result["stage"],
                trend_features=trend_features,
                breadth_features=breadth_stats.stats,
            )
            risk_flags = list(stage_result["risk_flags"])
            if quality_result["crowding_risk"] >= 80 and "crowding_risk_high" not in risk_flags:
                risk_flags.append("crowding_risk_high")
            diagnostics.append(
                CandidateTrendDiagnosis(
                    ticker=candidate.ticker,
                    name=candidate.name,
                    role=candidate.role,
                    score=int(round(candidate.score)),
                    stage=stage_result["stage"],
                    stage_reasons=stage_result["reasons"],
                    quality_scores=CandidateQualityScores(**{k: quality_result[k] for k in ["startup_quality", "trend_integrity", "capital_quality", "crowding_risk"]}),
                    risk_flags=risk_flags,
                    system_attitude=self.system_attitude(stage_result["stage"], quality_result["crowding_risk"]),
                    attribution=self.build_attribution(stage_result, trend_features, quality_result),
                )
            )

        diagnostics.sort(
            key=lambda item: (
                self.diagnostic_stage_priority(item.stage),
                item.quality_scores.crowding_risk,
                -item.quality_scores.startup_quality,
                self.diagnostic_role_priority(item.role),
                -item.score,
            )
        )
        result = {
            "trend_diagnosis_version": "v1",
            "mainline_breadth": breadth_stats.model_dump(),
            "candidate_diagnostics": [item.model_dump() for item in diagnostics[:limit]],
            "candidate_readiness": [item.model_dump() for item in readiness],
        }
        if diagnostics_error:
            result["diagnostics_error"] = diagnostics_error
        return result

    @staticmethod
    def diagnostic_stage_priority(stage: str) -> int:
        order = {"startup": 0, "pullback": 1, "markup": 2, "tail_rebound": 3}
        return order.get(stage, 9)

    @staticmethod
    def diagnostic_role_priority(role: str) -> int:
        order = {"followup": 0, "core_middle": 1, "leader": 2}
        return order.get(role, 9)

    @staticmethod
    def build_mainline_breadth_stats(mainline: str, candidates: list[StockCandidate]) -> MainlineBreadthSnapshot:
        # 分母最小 10：防止样本太小时比值虚高（如 5 只全涨 = advancers_ratio 1.0）
        sample_base = max(len(candidates), 10)
        positive_count = sum(1 for candidate in candidates if candidate.pct_change > 0)
        ma5_count = sum(1 for candidate in candidates if candidate.pct_change > 0)
        ma10_count = sum(1 for candidate in candidates if candidate.pct_change >= 1)
        ma20_count = sum(1 for candidate in candidates if candidate.pct_change >= 2)
        limit_up_count = sum(1 for candidate in candidates if candidate.pct_change >= 9.5)
        stats = {
            "advancers_ratio": round(positive_count / sample_base, 4),
            "ma5_above_ratio": round(ma5_count / sample_base, 4),
            "ma10_above_ratio": round(ma10_count / sample_base, 4),
            "ma20_above_ratio": round(ma20_count / sample_base, 4),
            "median_pct_change": round(mean(candidate.pct_change for candidate in candidates), 4) if candidates else 0.0,
            "limit_up_count": limit_up_count,
            "limit_break_count": 0,
            "limit_break_ratio": 0.0,
            "leader_excess_over_median": round((candidates[0].pct_change - mean(candidate.pct_change for candidate in candidates)), 4) if candidates else 0.0,
            "leader_second_gap": round((candidates[0].pct_change - candidates[1].pct_change), 4) if len(candidates) > 1 else 0.0,
            "diffusion_velocity_5m": round(min(0.2, positive_count / sample_base * 0.2), 4),
        }
        return MainlineBreadthSnapshot(mainline=mainline, stats=stats)

    @staticmethod
    def coerce_bars(payload: Any, ticker: str) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        bars = payload.get(ticker, [])
        if isinstance(bars, list):
            return bars
        if hasattr(bars, "to_dict"):
            try:
                if hasattr(bars, "empty") and bool(getattr(bars, "empty")):
                    return []
                records = bars.to_dict(orient="records")
                if isinstance(records, list):
                    return records
            except Exception:
                return []
        return []

    @staticmethod
    def system_attitude(stage: str, crowding_risk: int) -> str:
        if stage == "tail_rebound" or crowding_risk >= 80:
            return "high_risk_tail"
        if stage == "startup" and crowding_risk <= 35:
            return "potential_first"
        if stage == "markup":
            return "trend_follow_only"
        return "monitor_closely"

    @staticmethod
    def build_attribution(stage_result: dict[str, Any], trend_features: dict[str, Any], quality_result: dict[str, Any]) -> list[AttributionMetric]:
        return [
            AttributionMetric(metric="stage", value=stage_result["stage"], conclusion=";".join(stage_result["reasons"])),
            AttributionMetric(
                metric="distance_to_ma20_pct",
                value=trend_features.get("distance_to_ma20_pct"),
                conclusion="偏离不大" if float(trend_features.get("distance_to_ma20_pct") or 0) <= 6 else "偏离扩大",
            ),
            AttributionMetric(
                metric="startup_quality",
                value=quality_result["startup_quality"],
                conclusion="启动质量较高" if quality_result["startup_quality"] >= 70 else "启动质量一般",
            ),
        ]

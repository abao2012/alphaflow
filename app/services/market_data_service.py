import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock
from typing import Any

from app.models.domain import (
    MainlineScore,
    Position,
    StockCandidate,
)
from app.repositories.config_repository import ConfigRepository
from app.services.breadth_feature_extractor import BreadthFeatureExtractor
from app.services.candidate_diagnostics_service import CandidateDiagnosticsService
from app.services.history_backfill_service import HistoryBackfillService
from app.services.qmt_connector import QmtConnector
from app.services.stock_pool_builder import StockPoolBuilder
from app.services.mainline_scoring_service import MainlineScoringService
from app.services.quality_score_engine import QualityScoreEngine
from app.services.trend_feature_extractor import TrendFeatureExtractor
from app.services.trend_stage_engine import TrendStageEngine


@dataclass
class MainlineDefinition:
    name: str
    group: str
    min_sector_hits: int
    exact_sectors: list[str]
    keyword_sectors: list[str]


class MarketDataService:
    def __init__(self, connector: QmtConnector, config_repository: ConfigRepository) -> None:
        self.connector = connector
        self.config_repository = config_repository
        # 请求级缓存：一次 /diagnose/full 调用内的重复 QMT 查询复用结果
        self._req_cache: dict[str, Any] = {}
        self.stock_pool_builder = StockPoolBuilder(connector, self._load_config, self._req_cache)
        self.mainline_scoring_service = MainlineScoringService(connector, self._req_cache)
        self.trend_feature_extractor = TrendFeatureExtractor()
        self.breadth_feature_extractor = BreadthFeatureExtractor()
        self.trend_stage_engine = TrendStageEngine()
        self.quality_score_engine = QualityScoreEngine()
        self.candidate_diagnostics_service = CandidateDiagnosticsService(
            connector,
            trend_feature_extractor=self.trend_feature_extractor,
            trend_stage_engine=self.trend_stage_engine,
            quality_score_engine=self.quality_score_engine,
        )
        self.history_backfill_service = HistoryBackfillService(connector)
        # 主线评分是多个 API 的共同重型入口（scores/snapshot/emerging/daily-brief/diagnose/full）。
        # 前端循环主线时如果每个接口都重新 QMT 取数，会反复触发客户端 timeout。
        # 这里在服务层做 single-flight + 短缓存，覆盖所有主线相关接口。
        self._mainline_scores_cache: list[MainlineScore] | None = None
        self._mainline_scores_cached_at: datetime | None = None
        self._mainline_scores_cache_ttl = timedelta(seconds=int(os.environ.get("ALPHAFLOW_MAINLINE_SCORE_CACHE_TTL", "30")))
        self._mainline_scores_cache_lock = Lock()
        self._mainline_scores_compute_lock = Lock()

    def begin_request(self) -> None:
        """在每次 API 请求开始时清空缓存。"""
        self._req_cache.clear()

    def end_request(self) -> None:
        """在每次 API 请求结束时清空缓存。"""
        self._req_cache.clear()

    def get_mainline_scores_cache_meta(self) -> dict[str, Any]:
        with self._mainline_scores_cache_lock:
            cached_at = self._mainline_scores_cached_at
            has_cache = self._mainline_scores_cache is not None and cached_at is not None
        age_seconds = None
        if cached_at is not None:
            age_seconds = max((datetime.now() - cached_at).total_seconds(), 0.0)
        return {
            "has_cache": has_cache,
            "cached_at": cached_at,
            "age_seconds": age_seconds,
            "ttl_seconds": int(self._mainline_scores_cache_ttl.total_seconds()),
        }

    def prewarm_mainline_scores(self) -> int:
        """启动后预热主线评分缓存，尽量避免前端首次点击命中冷启动。"""
        self.begin_request()
        try:
            scores = self.get_mainline_scores()
            return len(scores)
        finally:
            self.end_request()

    def _load_config(self) -> dict[str, Any]:
        return self.config_repository.load_mainline_config()

    def _load_mainlines(self) -> list[MainlineDefinition]:
        config = self._load_config()
        return [
            MainlineDefinition(
                name=item["name"],
                group=item.get("group", item["name"].split("/")[0]),
                min_sector_hits=int(item.get("min_sector_hits", 1)),
                exact_sectors=item.get("exact_sectors", []),
                keyword_sectors=item.get("keyword_sectors", []),
            )
            for item in config.get("mainlines", [])
        ]

    def _definitions_for_key(self, key: str) -> list[MainlineDefinition]:
        definitions = self._load_mainlines()
        exact = [item for item in definitions if item.name == key]
        if exact:
            return exact
        grouped = [item for item in definitions if item.group == key]
        if grouped:
            return grouped
        raise ValueError(f"Unknown mainline or group: {key}")

    def _filters(self) -> dict[str, Any]:
        return self._load_config().get("filters", {})

    def split_name(self, name: str) -> tuple[str, str]:
        if "/" in name:
            group, branch = name.split("/", 1)
            return group, branch
        return name, name

    def _is_allowed_sector(self, sector_name: str) -> bool:
        filters = self._filters()
        for prefix in filters.get("exclude_sector_prefixes", []):
            if sector_name.startswith(prefix):
                return False
        for keyword in filters.get("exclude_sector_keywords", []):
            if keyword in sector_name:
                return False
        return True

    def _resolve_sector_names(self, definition: MainlineDefinition) -> list[str]:
        available_sectors = self.connector.get_sector_list()
        sector_names: set[str] = set()
        for sector in definition.exact_sectors:
            if sector in available_sectors and self._is_allowed_sector(sector):
                sector_names.add(sector)
        for keyword in definition.keyword_sectors:
            for sector in available_sectors:
                if keyword in sector and self._is_allowed_sector(sector):
                    sector_names.add(sector)
        return sorted(sector_names)

    def _resolve_codes_for_mainline(self, definition: MainlineDefinition) -> dict[str, int]:
        cache_key = f"codes:{definition.name}"
        if cache_key in self._req_cache:
            return self._req_cache[cache_key]
        hit_counter: dict[str, int] = {}
        for sector in self._resolve_sector_names(definition):
            for code in self.connector.get_stock_list_in_sector(sector):
                hit_counter[code] = hit_counter.get(code, 0) + 1
        self._req_cache[cache_key] = hit_counter
        return hit_counter

    def _is_allowed_stock(self, code: str, detail: dict[str, Any], tick: dict[str, Any]) -> bool:
        return self.stock_pool_builder.is_allowed_stock(code, detail, tick)

    def _build_stock_metrics(self, hit_counter: dict[str, int], pre_fetched_tick: dict | None = None) -> list[dict[str, Any]]:
        return self.stock_pool_builder.build_stock_metrics(hit_counter, pre_fetched_tick)

    @staticmethod
    def _normalize(value: float, lower: float, upper: float) -> float:
        return MainlineScoringService._normalize(value, lower, upper)

    def _compute_multiday_features(self, codes: list[str], daily_bars_map: dict[str, list[dict]]) -> dict[str, dict[str, float]]:
        return self.mainline_scoring_service._compute_multiday_features(codes, daily_bars_map)

    def _compute_stock_multiday_metrics(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        return self.mainline_scoring_service._compute_stock_multiday_metrics(codes)

    def _aggregate_multiday(self, stock_metrics: dict[str, dict[str, Any]]) -> dict[str, float]:
        return self.mainline_scoring_service._aggregate_multiday(stock_metrics)

    def _compute_9dim_scores(
        self, agg: dict[str, float], intraday_positive: float, intraday_strong: float, intraday_limit_up: int, n_stocks: int
    ) -> dict[str, float]:
        return self.mainline_scoring_service._compute_9dim_scores(
            agg,
            intraday_positive=intraday_positive,
            intraday_strong=intraday_strong,
            intraday_limit_up=intraday_limit_up,
            n_stocks=n_stocks,
        )

    def _mainline_scores_cache_enabled(self) -> bool:
        return "PYTEST_CURRENT_TEST" not in os.environ

    def _compute_mainline_scores_uncached(self) -> list[MainlineScore]:
        return self.mainline_scoring_service.get_mainline_scores(
            load_mainlines=self._load_mainlines,
            resolve_codes_for_mainline=self._resolve_codes_for_mainline,
            build_stock_metrics=self._build_stock_metrics,
        )

    def get_mainline_scores(self) -> list[MainlineScore]:
        if not self._mainline_scores_cache_enabled():
            return self._compute_mainline_scores_uncached()

        now = datetime.now()
        with self._mainline_scores_cache_lock:
            if self._mainline_scores_cache is not None and self._mainline_scores_cached_at is not None:
                if now - self._mainline_scores_cached_at <= self._mainline_scores_cache_ttl:
                    return list(self._mainline_scores_cache)

        acquired = self._mainline_scores_compute_lock.acquire(blocking=False)
        if not acquired:
            with self._mainline_scores_cache_lock:
                if self._mainline_scores_cache is not None:
                    return list(self._mainline_scores_cache)
            acquired = self._mainline_scores_compute_lock.acquire(timeout=20)
            if not acquired:
                with self._mainline_scores_cache_lock:
                    if self._mainline_scores_cache is not None:
                        return list(self._mainline_scores_cache)
                raise TimeoutError("mainline score computation is still warming up")

        try:
            now = datetime.now()
            with self._mainline_scores_cache_lock:
                if self._mainline_scores_cache is not None and self._mainline_scores_cached_at is not None:
                    if now - self._mainline_scores_cached_at <= self._mainline_scores_cache_ttl:
                        return list(self._mainline_scores_cache)

            scores = self._compute_mainline_scores_uncached()
            with self._mainline_scores_cache_lock:
                self._mainline_scores_cache = list(scores)
                self._mainline_scores_cached_at = datetime.now()
            return scores
        finally:
            self._mainline_scores_compute_lock.release()

    def _candidate_rows_for_mainline(self, mainline: str) -> list[dict[str, Any]]:
        return self.stock_pool_builder.candidate_rows_for_mainline(
            mainline,
            definitions_for_key=self._definitions_for_key,
            resolve_codes_for_mainline=self._resolve_codes_for_mainline,
        )

    def get_stock_pool(self, mainline: str) -> list[StockCandidate]:
        return self.stock_pool_builder.get_stock_pool(mainline, self._candidate_rows_for_mainline)

    def _diagnostic_seed_candidates(self, mainline: str, limit: int) -> list[StockCandidate]:
        return self.stock_pool_builder.diagnostic_seed_candidates(
            mainline,
            limit,
            get_stock_pool=self.get_stock_pool,
            candidate_rows_for_mainline=self._candidate_rows_for_mainline,
        )

    def _wider_breadth_pool(self, mainline: str) -> list[StockCandidate]:
        return self.stock_pool_builder.wider_breadth_pool(
            mainline,
            get_stock_pool=self.get_stock_pool,
            candidate_rows_for_mainline=self._candidate_rows_for_mainline,
        )

    @staticmethod
    def _diagnostic_stage_priority(stage: str) -> int:
        return CandidateDiagnosticsService.diagnostic_stage_priority(stage)

    @staticmethod
    def _diagnostic_role_priority(role: str) -> int:
        return CandidateDiagnosticsService.diagnostic_role_priority(role)

    def get_candidate_diagnostics(self, mainline: str, limit: int = 5) -> dict[str, Any]:
        return self.candidate_diagnostics_service.get_candidate_diagnostics(
            mainline=mainline,
            limit=limit,
            seed_candidates=self._diagnostic_seed_candidates,
            breadth_pool=self._wider_breadth_pool,
            coerce_bars=self._coerce_bars,
        )

    @staticmethod
    def _history_count_for_period(period: str) -> int:
        return HistoryBackfillService.history_count_for_period(period)

    def _has_history_for_period(self, ticker: str, period: str) -> bool:
        return self.history_backfill_service.has_history_for_period(ticker, period, self._coerce_bars)

    def backfill_candidate_history(self, mainline: str, limit: int = 5, periods: list[str] | None = None) -> dict[str, Any]:
        return self.history_backfill_service.backfill_candidate_history(
            mainline=mainline,
            limit=limit,
            periods=periods,
            candidate_pool=self.get_stock_pool,
            coerce_bars=self._coerce_bars,
        )

    def _build_mainline_breadth_stats(self, mainline: str, candidates: list[StockCandidate]):
        return CandidateDiagnosticsService.build_mainline_breadth_stats(mainline, candidates)

    @staticmethod
    def _coerce_bars(payload: Any, ticker: str) -> list[dict[str, Any]]:
        return CandidateDiagnosticsService.coerce_bars(payload, ticker)

    @staticmethod
    def _system_attitude(stage: str, crowding_risk: int) -> str:
        return CandidateDiagnosticsService.system_attitude(stage, crowding_risk)

    @staticmethod
    def _build_attribution(stage_result: dict[str, Any], trend_features: dict[str, Any], quality_result: dict[str, Any]):
        return CandidateDiagnosticsService.build_attribution(stage_result, trend_features, quality_result)

    def _build_membership_cache(self) -> dict[str, tuple[str, str, int]]:
        membership_cache: dict[str, tuple[str, str, int]] = {}
        for definition in self._load_mainlines():
            for code, hits in self._resolve_codes_for_mainline(definition).items():
                current = membership_cache.get(code)
                candidate = (definition.group, definition.name, hits)
                if current is None or hits > current[2]:
                    membership_cache[code] = candidate
        return membership_cache

    def get_positions(self) -> list[Position]:
        cache_key = "positions"
        if cache_key in self._req_cache:
            return self._req_cache[cache_key]
        asset = self.connector.query_stock_asset()
        total_asset = float(getattr(asset, "total_asset", 0) or 0)
        membership_cache = self._build_membership_cache()
        holdings = list(self.connector.query_stock_positions())
        tick_map = self.connector.get_full_tick([getattr(item, "stock_code") for item in holdings])
        positions: list[Position] = []
        for holding in holdings:
            code = getattr(holding, "stock_code")
            detail = self.connector.get_instrument_detail(code) or {}
            tick = tick_map.get(code, {})
            market_value = float(getattr(holding, "market_value", 0) or 0)
            last_price = float(tick.get("lastPrice") or 0)
            cost_price = float(getattr(holding, "open_price", 0) or 0)
            pnl_pct = round((last_price - cost_price) / cost_price * 100, 2) if cost_price > 0 and last_price > 0 else 0
            mapped = membership_cache.get(code)
            mapped_group = mapped[0] if mapped else None
            mapped_mainline = mapped[1] if mapped else None
            positions.append(
                Position(
                    ticker=code,
                    name=detail.get("InstrumentName", code),
                    quantity=int(getattr(holding, "volume", 0) or 0),
                    cost_price=cost_price,
                    last_price=last_price,
                    pnl_pct=pnl_pct,
                    market_value=market_value,
                    position_pct=round((market_value / total_asset), 4) if total_asset else 0,
                    mapped_group=mapped_group,
                    mapped_mainline=mapped_mainline,
                    is_core=mapped_mainline is not None,
                )
            )
        self._req_cache[cache_key] = positions
        return positions

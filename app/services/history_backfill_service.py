from __future__ import annotations

from typing import Any, Callable, Protocol

from app.models.domain import StockCandidate


class HistoryBackfillConnector(Protocol):
    def get_daily_bars(self, codes, count: int, fields: list[str]): ...
    def get_minute_bars(self, codes, period: str, count: int, fields: list[str]): ...
    def download_history_data(self, codes, period: str, count: int) -> dict[str, Any]: ...


class HistoryBackfillService:
    """Backfill missing 1d/5m/15m history for candidate stocks.

    This service keeps QMT history-readiness probing and per-period download
    error handling out of MarketDataService. MarketDataService still supplies
    the candidate pool because stock-pool construction is mainline/config aware.
    """

    def __init__(self, connector: HistoryBackfillConnector) -> None:
        self.connector = connector

    def backfill_candidate_history(
        self,
        mainline: str,
        limit: int,
        periods: list[str] | None,
        candidate_pool: Callable[[str], list[StockCandidate]],
        coerce_bars: Callable[[Any, str], list[dict[str, Any]]],
    ) -> dict[str, Any]:
        requested_periods = list(dict.fromkeys(periods or ["1d", "5m", "15m"]))
        candidates = candidate_pool(mainline)[:limit]
        downloaded_periods = {period: 0 for period in requested_periods}
        items: list[dict[str, Any]] = []

        for candidate in candidates:
            missing_before: list[str] = []
            downloaded: list[str] = []
            skipped: list[str] = []
            errors: dict[str, str] = {}
            for period in requested_periods:
                if self.has_history_for_period(candidate.ticker, period, coerce_bars):
                    skipped.append(period)
                    continue
                missing_before.append(period)
                try:
                    self.connector.download_history_data([candidate.ticker], period=period, count=self.history_count_for_period(period))
                except Exception as exc:
                    errors[period] = str(exc)
                    continue
                downloaded.append(period)
                downloaded_periods[period] += 1
            items.append(
                {
                    "ticker": candidate.ticker,
                    "name": candidate.name,
                    "missing_before": missing_before,
                    "downloaded": downloaded,
                    "skipped": skipped,
                    "errors": errors,
                }
            )

        return {
            "mainline": mainline,
            "requested_periods": requested_periods,
            "downloaded_periods": downloaded_periods,
            "items": items,
        }

    @staticmethod
    def history_count_for_period(period: str) -> int:
        if period == "1d":
            return 60
        if period == "5m":
            return 96
        if period == "15m":
            return 64
        raise ValueError(f"Unsupported period: {period}")

    def has_history_for_period(
        self,
        ticker: str,
        period: str,
        coerce_bars: Callable[[Any, str], list[dict[str, Any]]],
    ) -> bool:
        count = self.history_count_for_period(period)
        if period == "1d":
            payload = self.connector.get_daily_bars([ticker], count=count, fields=["open", "high", "low", "close", "volume"])
        else:
            payload = self.connector.get_minute_bars([ticker], period=period, count=count, fields=["open", "high", "low", "close", "volume"])
        return bool(coerce_bars(payload, ticker))

from __future__ import annotations

from math import log10
from typing import Any, Callable, Protocol

from app.models.domain import StockCandidate


class StockPoolConnector(Protocol):
    def get_full_tick(self, codes): ...
    def get_instrument_detail(self, code: str): ...


class StockPoolBuilder:
    """Build ranked stock pools and wider diagnostic seeds for a mainline."""

    def __init__(self, connector: StockPoolConnector, config_loader: Callable[[], dict[str, Any]], request_cache: dict[str, Any]) -> None:
        self.connector = connector
        self.config_loader = config_loader
        self.request_cache = request_cache

    def filters(self) -> dict[str, Any]:
        return self.config_loader().get("filters", {})

    def is_allowed_stock(self, code: str, detail: dict[str, Any], tick: dict[str, Any]) -> bool:
        filters = self.filters()
        allowed_markets = set(filters.get("allowed_markets", ["SH", "SZ"]))
        if "." in code and code.split(".")[-1] not in allowed_markets:
            return False
        name = str(detail.get("InstrumentName", code))
        if any(name.startswith(prefix) for prefix in filters.get("exclude_name_prefixes", [])):
            return False
        instrument_status = detail.get("InstrumentStatus")
        if instrument_status not in (None, 0):
            return False
        last_price = float(tick.get("lastPrice") or 0)
        amount = float(tick.get("amount") or 0)
        volume = float(tick.get("volume") or 0)
        if last_price < float(filters.get("min_price", 0)):
            return False
        min_snapshot_amount = float(filters.get("min_snapshot_amount", 0))
        preopen_snapshot = amount <= 0 and volume <= 0 and float(tick.get("lastClose") or 0) > 0
        if amount < min_snapshot_amount and not preopen_snapshot:
            return False
        return True

    def build_stock_metrics(self, hit_counter: dict[str, int], pre_fetched_tick: dict | None = None) -> list[dict[str, Any]]:
        codes = list(hit_counter.keys())
        if pre_fetched_tick is not None:
            tick_map = pre_fetched_tick
        else:
            cache_key = "tick_map_global"
            tick_map = self.request_cache.get(cache_key)
            if tick_map is None:
                tick_map = self.connector.get_full_tick(codes)
                self.request_cache[cache_key] = tick_map
            else:
                missing = [c for c in codes if c not in tick_map]
                if missing:
                    tick_map.update(self.connector.get_full_tick(missing))
                    self.request_cache[cache_key] = tick_map
        rows: list[dict[str, Any]] = []
        for code in codes:
            tick = tick_map.get(code)
            if not tick:
                continue
            detail = self.connector.get_instrument_detail(code) or {}
            if not self.is_allowed_stock(code, detail, tick):
                continue
            last_price = float(tick.get("lastPrice") or 0)
            last_close = float(tick.get("lastClose") or 0)
            if last_price <= 0 or last_close <= 0:
                continue
            pct_change = (last_price - last_close) / last_close * 100
            amount = float(tick.get("amount") or 0)
            rank_score = hit_counter.get(code, 1) * 18 + min(log10(max(amount, 1)) * 4, 42) + pct_change * 1.5
            rows.append(
                {
                    "ticker": code,
                    "name": detail.get("InstrumentName", code),
                    "last_price": round(last_price, 3),
                    "pct_change": round(pct_change, 2),
                    "amount": round(amount, 2),
                    "sector_hits": hit_counter.get(code, 1),
                    "rank_score": round(rank_score, 2),
                }
            )
        return rows

    def candidate_rows_for_mainline(
        self,
        mainline: str,
        definitions_for_key: Callable[[str], list[Any]],
        resolve_codes_for_mainline: Callable[[Any], dict[str, int]],
    ) -> list[dict[str, Any]]:
        cache_key = f"rows:{mainline}"
        if cache_key in self.request_cache:
            return self.request_cache[cache_key]
        definitions = definitions_for_key(mainline)
        aggregate_hits: dict[str, int] = {}
        min_sector_hits = min(item.min_sector_hits for item in definitions)
        for definition in definitions:
            for code, hits in resolve_codes_for_mainline(definition).items():
                aggregate_hits[code] = max(aggregate_hits.get(code, 0), hits)
        rows = self.build_stock_metrics(aggregate_hits)
        rows = [row for row in rows if row["sector_hits"] >= min_sector_hits]
        result = sorted(rows, key=lambda item: (item["sector_hits"], item["rank_score"], item["amount"], item["pct_change"]), reverse=True)
        self.request_cache[cache_key] = result
        return result

    def get_stock_pool(self, mainline: str, candidate_rows_for_mainline: Callable[[str], list[dict[str, Any]]]) -> list[StockCandidate]:
        cache_key = f"pool:{mainline}"
        if cache_key in self.request_cache:
            return self.request_cache[cache_key]
        ranked = candidate_rows_for_mainline(mainline)
        candidates: list[StockCandidate] = []
        for index, row in enumerate(ranked[:12]):
            if index < 2 and row["pct_change"] >= 1:
                role = "leader"
            elif index < 6:
                role = "core_middle"
            else:
                role = "followup"
            signal_tag = self.signal_tag_for_pct_change(row["pct_change"])
            candidates.append(self.stock_candidate_from_row(row, role, [signal_tag]))
        self.request_cache[cache_key] = candidates
        return candidates

    def diagnostic_seed_candidates(
        self,
        mainline: str,
        limit: int,
        get_stock_pool: Callable[[str], list[StockCandidate]],
        candidate_rows_for_mainline: Callable[[str], list[dict[str, Any]]],
    ) -> list[StockCandidate]:
        hot_pool = get_stock_pool(mainline)
        role_by_ticker = {item.ticker: item.role for item in hot_pool}
        try:
            ranked_rows = candidate_rows_for_mainline(mainline)
        except Exception:
            return hot_pool

        seed_size = max(limit * 8, 36)
        candidates: list[StockCandidate] = []
        for row in ranked_rows[:seed_size]:
            pct_change = row["pct_change"]
            if row["ticker"] in role_by_ticker:
                role = role_by_ticker[row["ticker"]]
            elif pct_change >= 4:
                role = "leader"
            elif pct_change >= 1.5:
                role = "core_middle"
            else:
                role = "followup"
            candidates.append(self.stock_candidate_from_row(row, role, [self.signal_tag_for_pct_change(pct_change)]))
        return candidates

    def wider_breadth_pool(
        self,
        mainline: str,
        get_stock_pool: Callable[[str], list[StockCandidate]],
        candidate_rows_for_mainline: Callable[[str], list[dict[str, Any]]],
    ) -> list[StockCandidate]:
        hot_pool = get_stock_pool(mainline)
        role_by_ticker = {item.ticker: item.role for item in hot_pool}
        try:
            ranked_rows = candidate_rows_for_mainline(mainline)
        except Exception:
            return hot_pool

        candidates: list[StockCandidate] = []
        for row in ranked_rows[:60]:
            pct_change = row["pct_change"]
            role = role_by_ticker.get(
                row["ticker"],
                "leader" if pct_change >= 4 else "core_middle" if pct_change >= 1.5 else "followup",
            )
            candidates.append(self.stock_candidate_from_row(row, role, []))
        return candidates

    @staticmethod
    def signal_tag_for_pct_change(pct_change: float) -> str:
        if pct_change >= 6:
            return "breakout"
        if pct_change >= 2:
            return "reclaim"
        return "pullback"

    @staticmethod
    def stock_candidate_from_row(row: dict[str, Any], role: str, signal_tags: list[str]) -> StockCandidate:
        score = min(99.0, max(55.0, row["rank_score"]))
        return StockCandidate(
            ticker=row["ticker"],
            name=row["name"],
            role=role,
            score=round(score, 2),
            last_price=row["last_price"],
            pct_change=row["pct_change"],
            amount=row["amount"],
            sector_hits=row["sector_hits"],
            signal_tags=signal_tags,
        )

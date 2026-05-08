from app.models.domain import Position


class PortfolioInspector:
    def inspect(
        self,
        positions: list[Position],
        active_groups: list[str] | None = None,
        core_tickers_by_group: dict[str, set[str]] | None = None,
    ) -> tuple[list[Position], float]:
        scored: list[Position] = []
        health = 100.0
        active_groups = active_groups or []
        core_tickers_by_group = core_tickers_by_group or {}

        for item in positions:
            if item.mapped_group is None:
                item.suggestion = "trim" if item.position_pct >= 0.08 else "exit"
                health -= 12 if item.position_pct >= 0.08 else 18
            elif item.mapped_group not in active_groups:
                item.suggestion = "trim"
                health -= 10
            elif item.ticker not in core_tickers_by_group.get(item.mapped_group, set()) and item.position_pct >= 0.08:
                item.suggestion = "trim"
                health -= 8
            elif item.pnl_pct <= -7:
                item.suggestion = "exit"
                health -= 15
            else:
                item.suggestion = "hold"
            scored.append(item)
        return scored, max(0.0, health)

from app.models.domain import MainlineScore, Position, RiskAlert, SellSignal, StockCandidate


class ReviewEngine:
    def generate_daily_review(
        self,
        scores: list[MainlineScore],
        candidates: list[StockCandidate],
        positions: list[Position],
        sell_watchlist: list[SellSignal],
        alerts: list[RiskAlert],
    ) -> dict[str, list[str] | str]:
        ordered_scores = sorted(scores, key=lambda item: item.total_score, reverse=True)
        top_names = [item.name for item in ordered_scores[:2]]
        top_summary = " / ".join(top_names) if top_names else "No clear mainline"
        non_mainline_positions = [item for item in positions if not item.is_core]
        summary = (
            f"Today's strongest tracked themes are {top_summary}. "
            f"Current holdings contain {len(non_mainline_positions)} position(s) outside the active mainline framework."
        )

        mainline_changes = [
            f"{item.name}: total {item.total_score:.2f}, leader {item.leader_score:.2f}, diffusion {item.diffusion_score:.2f}"
            for item in ordered_scores[:4]
        ]
        candidate_performance = [
            f"{item.name} ({item.ticker}) shows {item.signal_tags[0]} structure with {item.pct_change:.2f}% change and {item.sector_hits} sector hit(s)."
            for item in candidates[:5]
        ]
        if sell_watchlist:
            position_changes = [
                f"{item.ticker}: {item.action} because {item.reason}"
                for item in sell_watchlist[:5]
            ]
        else:
            position_changes = ["No forced trim or exit signal was generated for the current holdings."]

        discipline_notes = [f"{alert.title}: {alert.detail}" for alert in alerts[:4]]
        if not discipline_notes:
            discipline_notes = ["No material discipline warning was triggered."]

        return {
            "summary": summary,
            "mainline_changes": mainline_changes,
            "candidate_performance": candidate_performance,
            "position_changes": position_changes,
            "discipline_notes": discipline_notes,
        }

from app.models.domain import MainlineScore, Position, RiskAlert


class RiskEngine:
    def suggested_total_exposure(self, scores: list[MainlineScore] | None = None) -> float:
        if not scores:
            return 0.4
        top_score = max(item.total_score for item in scores)
        if top_score >= 26:
            return 0.75
        if top_score >= 23:
            return 0.65
        if top_score >= 20:
            return 0.5
        return 0.35

    def alerts_for_scores(
        self,
        scores: list[MainlineScore] | None = None,
        positions: list[Position] | None = None,
    ) -> list[RiskAlert]:
        alerts: list[RiskAlert] = []
        if scores:
            ordered = sorted(scores, key=lambda item: item.total_score, reverse=True)
            top = ordered[0]
            if top.total_score < 23:
                alerts.append(
                    RiskAlert(
                        level="warn",
                        title="Top mainline is not decisive",
                        detail="The strongest tracked theme is not yet in a clear expansion phase. Keep gross exposure moderate.",
                    )
                )
            if len(ordered) > 1 and abs(ordered[0].total_score - ordered[1].total_score) < 1.0:
                alerts.append(
                    RiskAlert(
                        level="info",
                        title="Rotation risk is elevated",
                        detail="The score gap between the first and second mainline is narrow. Avoid overcommitting to late followers.",
                    )
                )
        if positions:
            non_mainline = sum(item.position_pct for item in positions if not item.is_core)
            max_position = max((item.position_pct for item in positions), default=0)
            if non_mainline >= 0.3:
                alerts.append(
                    RiskAlert(
                        level="warn",
                        title="Non-mainline exposure is still heavy",
                        detail="A meaningful share of capital is sitting outside the active themes. Keep trimming passive holdings.",
                    )
                )
            if max_position >= 0.2:
                alerts.append(
                    RiskAlert(
                        level="warn",
                        title="Single-name concentration is high",
                        detail="At least one position is above 20% of total assets. Avoid adding unless the name remains in the core list.",
                    )
                )
        if not alerts:
            alerts.append(
                RiskAlert(
                    level="info",
                    title="Risk state is stable",
                    detail="Mainline leadership and current exposure are within the configured bounds.",
                )
            )
        return alerts

    def exposure(self, positions: list[Position]) -> dict[str, float | str]:
        total = round(sum(item.position_pct for item in positions), 2)
        mainline = round(sum(item.position_pct for item in positions if item.is_core), 2)
        non_mainline = round(total - mainline, 2)
        concentration = round(max((item.position_pct for item in positions), default=0), 2)
        if total <= 0.45 and concentration <= 0.12:
            level = "low"
        elif total <= 0.75 and concentration <= 0.18:
            level = "medium"
        else:
            level = "high"
        return {
            "total_exposure": total,
            "mainline_exposure": mainline,
            "non_mainline_exposure": non_mainline,
            "single_name_concentration": concentration,
            "risk_level": level,
        }

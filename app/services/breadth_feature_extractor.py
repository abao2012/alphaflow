from __future__ import annotations

from statistics import median
from typing import Any


class BreadthFeatureExtractor:
    def extract(
        self,
        members: list[dict[str, Any]],
        minute_breadth: list[dict[str, Any]] | None = None,
    ) -> dict[str, float | int]:
        if not members:
            return {
                "advancers_ratio": 0.0,
                "ma5_above_ratio": 0.0,
                "ma10_above_ratio": 0.0,
                "ma20_above_ratio": 0.0,
                "median_pct_change": 0.0,
                "limit_up_count": 0,
                "limit_break_count": 0,
                "limit_break_ratio": 0.0,
                "leader_excess_over_median": 0.0,
                "leader_second_gap": 0.0,
                "diffusion_velocity_5m": 0.0,
            }

        pct_changes = sorted(float(item.get("pct_change", 0) or 0) for item in members)
        top_pct = pct_changes[-1]
        second_pct = pct_changes[-2] if len(pct_changes) > 1 else pct_changes[-1]
        median_pct = float(median(pct_changes))
        total = len(members)
        advancers = sum(1 for item in members if float(item.get("pct_change", 0) or 0) > 0)
        ma5_above = sum(1 for item in members if bool(item.get("above_ma5", False)))
        ma10_above = sum(1 for item in members if bool(item.get("above_ma10", False)))
        ma20_above = sum(1 for item in members if bool(item.get("above_ma20", False)))
        limit_up_count = sum(1 for item in members if bool(item.get("hit_limit_up", False)))
        limit_break_count = sum(1 for item in members if bool(item.get("broken_limit_up", False)))

        velocity = 0.0
        normalized_breadth = [float(point.get("advancers_ratio", 0) or 0) for point in (minute_breadth or [])]
        if len(normalized_breadth) >= 2:
            velocity = normalized_breadth[-1] - normalized_breadth[0]

        return {
            "advancers_ratio": round(advancers / total, 4),
            "ma5_above_ratio": round(ma5_above / total, 4),
            "ma10_above_ratio": round(ma10_above / total, 4),
            "ma20_above_ratio": round(ma20_above / total, 4),
            "median_pct_change": round(median_pct, 4),
            "limit_up_count": limit_up_count,
            "limit_break_count": limit_break_count,
            "limit_break_ratio": round(limit_break_count / total, 4),
            "leader_excess_over_median": round(top_pct - median_pct, 4),
            "leader_second_gap": round(top_pct - second_pct, 4),
            "diffusion_velocity_5m": round(velocity, 4),
        }

from __future__ import annotations

from typing import Any


class QualityScoreEngine:
    def score(self, stage: str, trend_features: dict[str, Any], breadth_features: dict[str, Any]) -> dict[str, Any]:
        startup_quality = self._startup_quality(trend_features, breadth_features)
        trend_integrity = self._trend_integrity(trend_features, breadth_features)
        capital_quality = self._capital_quality(trend_features, breadth_features)
        crowding_risk = self._crowding_risk(trend_features, breadth_features)

        if stage == "tail_rebound":
            startup_quality = min(startup_quality, 35)
            capital_quality = min(capital_quality, 50)
        elif stage == "startup":
            startup_quality = max(startup_quality, 80)
            crowding_risk = min(crowding_risk, 35)

        return {
            "startup_quality": startup_quality,
            "trend_integrity": trend_integrity,
            "capital_quality": capital_quality,
            "crowding_risk": crowding_risk,
            "contributors": {
                "startup_quality": self._startup_contributors(trend_features),
                "trend_integrity": self._trend_integrity_contributors(trend_features, breadth_features),
                "capital_quality": self._capital_quality_contributors(trend_features, breadth_features),
                "crowding_risk": self._crowding_risk_contributors(trend_features, breadth_features),
            },
        }

    def _startup_quality(self, trend_features: dict[str, Any], breadth_features: dict[str, Any]) -> int:
        score = 20
        if trend_features.get("above_ma20"):
            score += 15
        if trend_features.get("platform_breakout_10d"):
            score += 18
        if trend_features.get("platform_breakout_20d"):
            score += 18
        if trend_features.get("contraction_then_breakout"):
            score += 14
        if self._num(trend_features.get("ma20_slope_5d")) > 0:
            score += 8
        if self._num(breadth_features.get("diffusion_velocity_5m")) > 0.08:
            score += 7
        if self._num(trend_features.get("intraday_pullback_from_high_pct")) <= 1.0:
            score += 5
        return self._clamp(score)

    def _trend_integrity(self, trend_features: dict[str, Any], breadth_features: dict[str, Any]) -> int:
        score = 25
        if trend_features.get("above_ma20"):
            score += 18
        if self._num(trend_features.get("ma20_slope_5d")) >= 1:
            score += 15
        if self._num(trend_features.get("return_10d")) > 0:
            score += 14
        if self._num(breadth_features.get("ma20_above_ratio")) >= 0.4:
            score += 12
        if self._num(trend_features.get("intraday_pullback_from_high_pct")) <= 1.5:
            score += 8
        if self._num(breadth_features.get("leader_second_gap")) <= 1.5:
            score += 8
        return self._clamp(score)

    def _capital_quality(self, trend_features: dict[str, Any], breadth_features: dict[str, Any]) -> int:
        score = 20
        if trend_features.get("close_near_intraday_high"):
            score += 15
        if self._num(trend_features.get("intraday_pullback_from_high_pct")) <= 1.0:
            score += 12
        if self._num(breadth_features.get("diffusion_velocity_5m")) > 0.08:
            score += 14
        if self._num(breadth_features.get("leader_excess_over_median")) >= 2.5:
            score += 10
        if self._num(breadth_features.get("median_pct_change")) >= 1.5:
            score += 8
        if trend_features.get("contraction_then_breakout"):
            score += 10
        if self._num(breadth_features.get("limit_break_ratio")) >= 0.12:
            score -= 15
        if self._num(trend_features.get("intraday_pullback_from_high_pct")) >= 4:
            score -= 18
        return self._clamp(score)

    def _crowding_risk(self, trend_features: dict[str, Any], breadth_features: dict[str, Any]) -> int:
        score = 5
        distance = self._num(trend_features.get("distance_to_ma20_pct"))
        return_5d = self._num(trend_features.get("return_5d"))
        return_10d = self._num(trend_features.get("return_10d"))
        advancers_ratio = self._num(breadth_features.get("advancers_ratio"))
        ma20_above_ratio = self._num(breadth_features.get("ma20_above_ratio"))
        limit_break_ratio = self._num(breadth_features.get("limit_break_ratio"))
        intraday_pullback = self._num(trend_features.get("intraday_pullback_from_high_pct"))

        if distance >= 15:
            score += 30
        elif distance >= 10:
            score += 20
        elif distance >= 6:
            score += 10

        if return_5d >= 18:
            score += 20
        elif return_5d >= 10:
            score += 10

        if return_10d >= 30:
            score += 15
        elif return_10d >= 18:
            score += 8

        if advancers_ratio >= 0.85:
            score += 10
        elif advancers_ratio >= 0.7:
            score += 5

        if ma20_above_ratio >= 0.75:
            score += 10
        elif ma20_above_ratio >= 0.55:
            score += 5

        if limit_break_ratio >= 0.15:
            score += 10
        if intraday_pullback >= 4:
            score += 8

        return self._clamp(score)

    @staticmethod
    def _startup_contributors(trend_features: dict[str, Any]) -> list[str]:
        contributors: list[str] = []
        for field in ["above_ma20", "platform_breakout_10d", "platform_breakout_20d", "contraction_then_breakout", "ma20_slope_5d"]:
            value = trend_features.get(field)
            if value not in (None, False, 0, 0.0):
                contributors.append(field)
        return contributors

    @staticmethod
    def _trend_integrity_contributors(trend_features: dict[str, Any], breadth_features: dict[str, Any]) -> list[str]:
        contributors: list[str] = []
        for field in ["above_ma20", "ma20_slope_5d", "return_10d", "intraday_pullback_from_high_pct"]:
            value = trend_features.get(field)
            if value not in (None, False, 0, 0.0):
                contributors.append(field)
        for field in ["ma20_above_ratio", "leader_second_gap"]:
            value = breadth_features.get(field)
            if value not in (None, False, 0, 0.0):
                contributors.append(field)
        return contributors

    @staticmethod
    def _capital_quality_contributors(trend_features: dict[str, Any], breadth_features: dict[str, Any]) -> list[str]:
        contributors: list[str] = []
        for field in ["close_near_intraday_high", "intraday_pullback_from_high_pct", "contraction_then_breakout"]:
            value = trend_features.get(field)
            if value not in (None, False, 0, 0.0):
                contributors.append(field)
        for field in ["diffusion_velocity_5m", "leader_excess_over_median", "median_pct_change"]:
            value = breadth_features.get(field)
            if value not in (None, False, 0, 0.0):
                contributors.append(field)
        return contributors

    @staticmethod
    def _crowding_risk_contributors(trend_features: dict[str, Any], breadth_features: dict[str, Any]) -> list[str]:
        contributors: list[str] = []
        for field in ["distance_to_ma20_pct", "return_5d", "return_10d", "intraday_pullback_from_high_pct"]:
            value = trend_features.get(field)
            if value not in (None, False, 0, 0.0):
                contributors.append(field)
        for field in ["advancers_ratio", "ma20_above_ratio", "limit_break_ratio"]:
            value = breadth_features.get(field)
            if value not in (None, False, 0, 0.0):
                contributors.append(field)
        return contributors

    @staticmethod
    def _clamp(value: float) -> int:
        return max(0, min(100, int(round(value))))

    @staticmethod
    def _num(value: Any) -> float:
        return float(value or 0.0)

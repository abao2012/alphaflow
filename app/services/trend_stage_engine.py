from __future__ import annotations

from typing import Any


class TrendStageEngine:
    def classify(self, trend_features: dict[str, Any], breadth_features: dict[str, Any]) -> dict[str, Any]:
        distance = self._num(trend_features.get("distance_to_ma20_pct"))
        ma20_slope = self._num(trend_features.get("ma20_slope_5d"))
        return_3d = self._num(trend_features.get("return_3d"))
        return_5d = self._num(trend_features.get("return_5d"))
        return_10d = self._num(trend_features.get("return_10d"))
        intraday_pullback = self._num(trend_features.get("intraday_pullback_from_high_pct"))

        advancers_ratio = self._num(breadth_features.get("advancers_ratio"))
        ma20_above_ratio = self._num(breadth_features.get("ma20_above_ratio"))
        median_pct_change = self._num(breadth_features.get("median_pct_change"))
        limit_break_ratio = self._num(breadth_features.get("limit_break_ratio"))
        leader_excess = self._num(breadth_features.get("leader_excess_over_median"))
        leader_second_gap = self._num(breadth_features.get("leader_second_gap"))
        diffusion_velocity = self._num(breadth_features.get("diffusion_velocity_5m"))

        reasons: list[str] = []
        risk_flags: list[str] = []

        tail_condition = (
            distance >= 15
            or return_5d >= 18
            or return_10d >= 30
            or advancers_ratio >= 0.88
            or ma20_above_ratio >= 0.78
            or limit_break_ratio >= 0.15
            or intraday_pullback >= 4
        )
        if tail_condition:
            reasons.append("价格偏离过大或板块过热")
            if limit_break_ratio >= 0.15 or advancers_ratio >= 0.88 or distance >= 15:
                risk_flags.append("crowding_risk_high")
            return {"stage": "tail_rebound", "reasons": reasons, "risk_flags": risk_flags}

        startup_condition = (
            bool(trend_features.get("above_ma20"))
            and (bool(trend_features.get("platform_breakout_10d")) or bool(trend_features.get("platform_breakout_20d")))
            and ma20_slope > 0
            and distance <= 6
            and diffusion_velocity > 0.08
            and advancers_ratio <= 0.7
            and ma20_above_ratio <= 0.5
        )
        if startup_condition:
            reasons.extend(["站上MA20或平台突破", "板块扩散刚开始但未过热"])
            return {"stage": "startup", "reasons": reasons, "risk_flags": risk_flags}

        markup_condition = (
            bool(trend_features.get("above_ma20"))
            and ma20_slope >= 1.5
            and 4 <= distance <= 12
            and advancers_ratio >= 0.65
            and ma20_above_ratio >= 0.55
            and diffusion_velocity >= 0.08
            and leader_excess >= 2.0
            and leader_second_gap <= 1.2
        )
        if markup_condition:
            reasons.append("均线趋势和扩散共振")
            return {"stage": "markup", "reasons": reasons, "risk_flags": risk_flags}

        pullback_condition = (
            bool(trend_features.get("above_ma20"))
            and ma20_slope >= 0
            and distance <= 3.5
            and return_10d > 0
            and return_3d <= 1
            and advancers_ratio < 0.6
            and diffusion_velocity <= 0.02
            and median_pct_change <= 1.0
        )
        if pullback_condition:
            reasons.append("结构未破但热度回落")
            return {"stage": "pullback", "reasons": reasons, "risk_flags": risk_flags}

        if bool(trend_features.get("above_ma20")):
            reasons.append("均线仍在上方但确认不足，暂按主升处理")
            return {"stage": "markup", "reasons": reasons, "risk_flags": risk_flags}

        reasons.append("结构尚未修复，暂按调整处理")
        return {"stage": "pullback", "reasons": reasons, "risk_flags": risk_flags}

    @staticmethod
    def _num(value: Any) -> float:
        return float(value or 0.0)

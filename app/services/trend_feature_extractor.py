from __future__ import annotations

from statistics import mean
from typing import Any


class TrendFeatureExtractor:
    def extract(
        self,
        daily_bars: list[dict[str, Any]],
        minute_bars: list[dict[str, Any]] | None = None,
    ) -> dict[str, float | bool | None]:
        normalized_daily = [self._normalize_bar(bar) for bar in daily_bars if bar]
        if not normalized_daily:
            return {
                "above_ma20": False,
                "ma20_value": None,
                "ma20_slope_5d": None,
                "distance_to_ma20_pct": None,
                "platform_breakout_10d": False,
                "platform_breakout_20d": False,
                "contraction_then_breakout": False,
                "return_3d": None,
                "return_5d": None,
                "return_10d": None,
                "close_near_intraday_high": False,
                "intraday_pullback_from_high_pct": None,
            }

        closes = [bar["close"] for bar in normalized_daily]
        volumes = [bar["volume"] for bar in normalized_daily]
        latest_close = closes[-1]
        ma20 = self._mean_tail(closes, 20)
        prev_ma20 = self._mean_window(closes, 20, offset=5)
        ma20_slope = None
        if ma20 is not None and prev_ma20 not in (None, 0):
            ma20_slope = round((ma20 - prev_ma20) / prev_ma20 * 100, 4)

        distance_to_ma20 = None
        if ma20 not in (None, 0):
            distance_to_ma20 = round((latest_close - ma20) / ma20 * 100, 4)

        features: dict[str, float | bool | None] = {
            "above_ma20": bool(ma20 is not None and latest_close >= ma20),
            "ma20_value": round(ma20, 4) if ma20 is not None else None,
            "ma20_slope_5d": ma20_slope,
            "distance_to_ma20_pct": distance_to_ma20,
            "platform_breakout_10d": self._is_platform_breakout(normalized_daily, lookback=10),
            "platform_breakout_20d": self._is_platform_breakout(normalized_daily, lookback=20),
            "contraction_then_breakout": self._is_contraction_then_breakout(normalized_daily, volumes),
            "return_3d": self._return_over(closes, 3),
            "return_5d": self._return_over(closes, 5),
            "return_10d": self._return_over(closes, 10),
            "close_near_intraday_high": False,
            "intraday_pullback_from_high_pct": None,
        }

        normalized_minute = [self._normalize_bar(bar) for bar in minute_bars or [] if bar]
        if normalized_minute:
            session_high = max(bar["high"] for bar in normalized_minute)
            session_close = normalized_minute[-1]["close"]
            pullback = 0.0 if session_high <= 0 else round((session_high - session_close) / session_high * 100, 4)
            features["intraday_pullback_from_high_pct"] = pullback
            features["close_near_intraday_high"] = pullback <= 1.0

        return features

    @staticmethod
    def _normalize_bar(bar: dict[str, Any]) -> dict[str, float]:
        return {
            "open": float(bar.get("open", bar.get("close", 0)) or 0),
            "high": float(bar.get("high", bar.get("close", 0)) or 0),
            "low": float(bar.get("low", bar.get("close", 0)) or 0),
            "close": float(bar.get("close", 0) or 0),
            "volume": float(bar.get("volume", bar.get("amount", 0)) or 0),
        }

    @staticmethod
    def _mean_tail(values: list[float], window: int) -> float | None:
        if not values:
            return None
        sample = values[-window:] if len(values) >= window else values
        return mean(sample)

    @staticmethod
    def _mean_window(values: list[float], window: int, offset: int) -> float | None:
        if len(values) <= offset:
            return None
        end = len(values) - offset
        start = max(0, end - window)
        sample = values[start:end]
        if not sample:
            return None
        return mean(sample)

    @staticmethod
    def _return_over(closes: list[float], periods: int) -> float | None:
        if len(closes) <= periods:
            return None
        baseline = closes[-periods - 1]
        if baseline == 0:
            return None
        return round((closes[-1] - baseline) / baseline * 100, 4)

    @staticmethod
    def _is_platform_breakout(daily_bars: list[dict[str, float]], lookback: int) -> bool:
        if len(daily_bars) <= 1:
            return False
        history = daily_bars[-(lookback + 1):-1]
        if not history:
            return False
        history_high = max(bar["high"] for bar in history)
        return daily_bars[-1]["close"] > history_high

    @staticmethod
    def _is_contraction_then_breakout(daily_bars: list[dict[str, float]], volumes: list[float]) -> bool:
        if len(daily_bars) < 6:
            return False
        recent_volumes = volumes[-6:-1]
        if len(recent_volumes) < 3:
            return False
        prev_volumes = volumes[-11:-6] if len(volumes) >= 11 else volumes[:-6]
        avg_recent = mean(recent_volumes)
        avg_prev = mean(prev_volumes) if prev_volumes else avg_recent
        latest_volume = volumes[-1]
        return avg_recent <= avg_prev and latest_volume > avg_recent and TrendFeatureExtractor._is_platform_breakout(daily_bars, lookback=10)

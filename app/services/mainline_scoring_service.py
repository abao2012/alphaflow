from __future__ import annotations

from math import log10
from typing import Any

from app.models.domain import MainlineScore


class MainlineScoringService:
    """Compute multi-day 9-dimension mainline scores."""

    def __init__(self, connector, request_cache: dict[str, Any]) -> None:
        self.connector = connector
        self._req_cache = request_cache

    @staticmethod
    def _normalize(value: float, lower: float, upper: float) -> float:
        if upper <= lower:
            return 0.0
        clipped = min(max(value, lower), upper)
        return round((clipped - lower) / (upper - lower) * 5, 2)

    # ------------------------------------------------------------------
    # 多日数据采集：为每个成分股计算 5d/10d/20d 指标
    # ------------------------------------------------------------------
    def _compute_multiday_features(
        self, 
        codes: list[str], 
        daily_bars_map: dict[str, list[dict]]
    ) -> dict[str, dict[str, float]]:
        """为一批股票计算多日K线特征，返回 {code: metrics_dict}。
        
        这个方法是为了满足新的评分逻辑需求而添加的，与 _compute_stock_multiday_metrics 类似但返回更多指标。
        """
        if not codes:
            return {}

        cache_key = "multiday_features_global"
        cached = self._req_cache.get(cache_key)
        if cached is not None:
            return cached

        # 拉取换手率用于资本属性鉴别
        turnover_data = self.connector.get_turnover_snapshot(codes, count=10)

        result: dict[str, dict[str, float]] = {}
        for code in codes:
            bars = daily_bars_map.get(code, [])
            if not bars or len(bars) < 5:
                continue

            closes = [float(b.get("close", 0)) for b in bars]
            volumes = [float(b.get("volume", 0)) for b in bars]
            # QMT 不一定返回 amount 字段，用 volume * close 估算成交额
            amounts = [float(b.get("amount", 0)) if b.get("amount") else float(b.get("volume", 0)) * float(b.get("close", 0)) for b in bars]
            highs = [float(b.get("high", 0)) for b in bars]
            n = len(closes)

            # === 5d/10d/3d 累计涨幅 ===
            return_5d = ((closes[-1] / closes[-6]) - 1) * 100 if n >= 6 else 0.0
            return_10d = ((closes[-1] / closes[-11]) - 1) * 100 if n >= 11 else return_5d
            return_3d = ((closes[-1] / closes[-4]) - 1) * 100 if n >= 4 else 0.0

            # === MA 对齐度 ===
            def _ma(data: list[float], period: int) -> float | None:
                if len(data) < period:
                    return None
                return sum(data[-period:]) / period

            ma5 = _ma(closes, 5)
            ma10 = _ma(closes, 10)
            ma20 = _ma(closes, 20)
            
            # 计算MA5和MA10上方比例（最后5天）
            ma5_above_ratio = sum(1 for c in closes[-5:] if ma5 and c >= ma5) / 5 if ma5 else 0
            ma10_above_ratio = sum(1 for c in closes[-5:] if ma10 and c >= ma10) / 5 if ma10 else 0
            ma20_above = 1.0 if (ma20 and closes[-1] >= ma20) else 0.0
            
            # 计算连续站上MA20天数（从最后一天往前数）
            days_above_ma20 = 0
            if ma20:
                for i in range(1, min(21, n)):  # 最多20天
                    if closes[-i] >= ma20:
                        days_above_ma20 += 1
                    else:
                        break
            
            # MA20斜率（5天）
            ma20_slope = 0.0
            if n >= 10:
                ma20_prev = _ma(closes[:-5], 20)
                ma20_curr = ma20
                if ma20_prev and ma20_curr and ma20_prev > 0:
                    ma20_slope = ((ma20_curr / ma20_prev) - 1) * 100

            # === 趋势加速度 ===
            acceleration = return_3d - (return_10d / 10 * 3) if return_10d != 0 else 0.0

            # === 成交额趋势 ===
            vol_5d_avg = sum(amounts[-5:]) / 5 if n >= 5 else 0
            vol_10d_avg = sum(amounts[-10:]) / 10 if n >= 10 else vol_5d_avg
            volume_trend = vol_5d_avg / vol_10d_avg if vol_10d_avg > 0 else 1.0

            # === 波动率（5日） ===
            returns = [((closes[i] / closes[i-1]) - 1) * 100 for i in range(1, n)]
            volatility_5d = 0.0
            if len(returns) >= 5:
                recent_returns = returns[-5:]
                mean_return = sum(recent_returns) / len(recent_returns)
                volatility_5d = (sum((r - mean_return)**2 for r in recent_returns) / len(recent_returns))**0.5

            # === 换手率数据 ===
            turnover_list = []
            turn_data = turnover_data.get(code, [])
            if turn_data:
                turnover_list = [float(t.get("turnoverratio", 0)) for t in turn_data if t.get("turnoverratio")]
            
            # 5日平均换手率
            turnover_5d_avg = sum(turnover_list[-5:]) / 5 if len(turnover_list) >= 5 else (sum(turnover_list) / len(turnover_list) if turnover_list else 0)
            
            # 5日换手率变异系数
            turnover_5d_cv = 0.0
            if len(turnover_list) >= 3:
                turnover_mean = sum(turnover_list) / len(turnover_list)
                turnover_std = (sum((t - turnover_mean)**2 for t in turnover_list) / len(turnover_list))**0.5
                turnover_5d_cv = turnover_std / turnover_mean if turnover_mean > 0 else 0.0

            # === 距离近期高点（20日） ===
            high_20d = max(highs[-20:]) if n >= 20 else max(highs)
            distance_to_high_20d = ((closes[-1] / high_20d) - 1) * 100 if high_20d > 0 else 0.0

            # === 涨停计数（5日内）===
            limit_up_count_5d = 0
            for i in range(1, min(6, n)):
                prev_close = closes[-(i + 1)]
                if prev_close > 0 and ((closes[-i] / prev_close) - 1) * 100 >= 9.5:
                    limit_up_count_5d += 1

            # === 市值对数（估算） ===
            # 获取instrument detail来计算市值
            detail = self.connector.get_instrument_detail(code) or {}
            market_cap = float(detail.get("MarketValue", 0) or 0)
            if market_cap <= 0 and vol_5d_avg > 0:
                # 如果没有市值数据，用成交额估算
                market_cap = vol_5d_avg * 100  # 粗略估算
            market_cap_log = log10(market_cap) if market_cap > 0 else 8.0  # 默认8（1亿）

            result[code] = {
                "return_5d": round(return_5d, 2),
                "return_10d": round(return_10d, 2),
                "return_3d": round(return_3d, 2),
                "acceleration": round(acceleration, 2),
                "ma5_above_ratio": round(ma5_above_ratio, 3),
                "ma10_above_ratio": round(ma10_above_ratio, 3),
                "ma20_above": ma20_above,
                "days_above_ma20": float(days_above_ma20),
                "ma20_slope": round(ma20_slope, 2),
                "volume_trend": round(volume_trend, 3),
                "vol_5d_avg": round(vol_5d_avg, 2),
                "volatility_5d": round(volatility_5d, 2),
                "turnover_5d_avg": round(turnover_5d_avg, 4),
                "turnover_5d_cv": round(turnover_5d_cv, 3),
                "distance_to_high_20d": round(distance_to_high_20d, 2),
                "limit_up_count_5d": float(limit_up_count_5d),
                "market_cap_log": round(market_cap_log, 2),
            }

        self._req_cache[cache_key] = result
        return result

    def _compute_stock_multiday_metrics(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        """为一批股票计算多日K线指标，返回 {code: metrics_dict}。
        
        这是旧方法的保留版本，为了向后兼容。
        """
        if not codes:
            return {}

        cache_key = "multiday_metrics_global"
        cached = self._req_cache.get(cache_key)
        if cached is not None:
            return cached

        # 拉取20日日K线（足够计算5d/10d/20d指标）
        daily_bars = self.connector.get_daily_bars(codes, count=25, fields=["open", "high", "low", "close", "volume", "amount"])
        
        # 使用新的 _compute_multiday_features 方法计算特征
        features = self._compute_multiday_features(codes, daily_bars)
        
        # 转换为旧格式以保持向后兼容
        result: dict[str, dict[str, Any]] = {}
        for code, feat in features.items():
            result[code] = {
                "return_5d": feat["return_5d"],
                "return_10d": feat["return_10d"],
                "return_3d": feat["return_3d"],
                "trend_accel": feat["acceleration"],
                "above_ma5_ratio": feat["ma5_above_ratio"],
                "above_ma10_ratio": feat["ma10_above_ratio"],
                "above_ma20": bool(feat["ma20_above"]),
                "ma20_slope": feat["ma20_slope"],
                "vol_5d_avg": feat["vol_5d_avg"],
                "vol_trend": feat["volume_trend"],
                "volatility": feat["volatility_5d"],
                "turnover_cv": feat["turnover_5d_cv"],
                "turnover_5d_avg": feat["turnover_5d_avg"],  # 新增
                "dist_from_high": feat["distance_to_high_20d"],
                "limit_up_5d": int(feat["limit_up_count_5d"]),
            }

        self._req_cache[cache_key] = result
        return result

    # ------------------------------------------------------------------
    # 板块级多日指标聚合
    # ------------------------------------------------------------------
    def _aggregate_multiday(self, stock_metrics: dict[str, dict[str, Any]]) -> dict[str, float]:
        """将成分股级多日指标聚合为板块级指标。"""
        if not stock_metrics:
            return {}

        values = list(stock_metrics.values())
        n = len(values)

        avg_5d_return = sum(v["return_5d"] for v in values) / n
        avg_10d_return = sum(v["return_10d"] for v in values) / n
        avg_3d_return = sum(v["return_3d"] for v in values) / n
        avg_accel = sum(v["trend_accel"] for v in values) / n

        ma5_above_avg = sum(v["above_ma5_ratio"] for v in values) / n
        ma10_above_avg = sum(v["above_ma10_ratio"] for v in values) / n
        ma20_above_ratio = sum(1 for v in values if v["above_ma20"]) / n
        avg_ma20_slope = sum(v["ma20_slope"] for v in values) / n

        avg_vol_trend = sum(v["vol_trend"] for v in values) / n
        avg_vol_5d = sum(v["vol_5d_avg"] for v in values) / n

        avg_volatility = sum(v["volatility"] for v in values) / n
        avg_turnover_cv = sum(v["turnover_cv"] for v in values) / n
        # 计算平均换手率
        avg_turnover_5d = sum(v["turnover_5d_avg"] for v in values) / n
        
        positive_ratio = sum(1 for v in values if v["return_5d"] > 0) / n
        strong_ratio = sum(1 for v in values if v["return_5d"] >= 3) / n

        avg_dist_from_high = sum(v["dist_from_high"] for v in values) / n
        total_limit_up = sum(v["limit_up_5d"] for v in values)

        # === 站上MA20的平均天数估算（用当前ma20_above + ma20斜率推断）===
        days_above_ma20_est = ma20_above_ratio * 10  # 粗估：比例越高，站上天数越长

        # === 龙头-跟风差距 ===
        sorted_by_ret = sorted(values, key=lambda x: x["return_5d"], reverse=True)
        top3_avg = sum(v["return_5d"] for v in sorted_by_ret[:min(3, n)]) / min(3, n) if n >= 3 else avg_5d_return
        rest_avg = sum(v["return_5d"] for v in sorted_by_ret[3:min(8, n)]) / max(1, min(5, n - 3)) if n > 3 else avg_5d_return
        leader_gap = top3_avg - rest_avg

        return {
            "avg_5d_return": round(avg_5d_return, 2),
            "avg_10d_return": round(avg_10d_return, 2),
            "avg_3d_return": round(avg_3d_return, 2),
            "avg_accel": round(avg_accel, 2),
            "ma5_above_avg": round(ma5_above_avg, 2),
            "ma10_above_avg": round(ma10_above_avg, 2),
            "ma20_above_ratio": round(ma20_above_ratio, 2),
            "avg_ma20_slope": round(avg_ma20_slope, 2),
            "avg_vol_trend": round(avg_vol_trend, 3),
            "avg_vol_5d": round(avg_vol_5d, 2),
            "avg_volatility": round(avg_volatility, 2),
            "avg_turnover_cv": round(avg_turnover_cv, 3),
            "avg_turnover_5d": round(avg_turnover_5d, 4),  # 新增
            "positive_5d_ratio": round(positive_ratio, 2),
            "strong_5d_ratio": round(strong_ratio, 2),
            "avg_dist_from_high": round(avg_dist_from_high, 2),
            "total_limit_up_5d": total_limit_up,
            "days_above_ma20_est": round(days_above_ma20_est, 1),
            "leader_gap": round(leader_gap, 2),
            "top3_avg_5d": round(top3_avg, 2),
            "rest_avg_5d": round(rest_avg, 2),
        }

    # ------------------------------------------------------------------
    # 9维度评分计算
    # ------------------------------------------------------------------
    def _compute_9dim_scores(
        self, agg: dict[str, float], intraday_positive: float, intraday_strong: float, intraday_limit_up: int, n_stocks: int
    ) -> dict[str, float]:
        """基于聚合后的多日指标 + 日内数据，计算9个维度的分数（各0-5.0）。"""

        # 1. 趋势持续性 trend_persistence
        #    Data: 5d_return, 10d_return, ma5_above_ratio, ma10_above_ratio
        s1 = self._normalize(agg.get("avg_5d_return", 0), -5, 20)
        s2 = self._normalize(agg.get("avg_10d_return", 0), -8, 30)
        mean_ma5_above = agg.get("ma5_above_avg", 0) * 100  # Convert to percentage
        s3 = self._normalize(mean_ma5_above, 20, 80)
        trend_persistence = s1 * 0.4 + s2 * 0.3 + s3 * 0.3

        # 2. 资金流向 capital_flow
        #    Data: volume_trend (5d/10d ratio), avg_vol_5d (per-stock average 5-day amount)
        #    avg_vol_5d range is ~5e6 to 5e8 for individual stocks
        s1 = self._normalize(agg.get("avg_vol_trend", 1.0), 0.5, 2.0)
        s2 = self._normalize(agg.get("avg_vol_5d", 0), 5e6, 3e8)
        capital_flow = s1 * 0.6 + s2 * 0.4

        # 3. 扩散广度 breadth_expansion
        #    Data: ma5_above_ratio (5d avg), ma10_above_ratio (5d avg)
        s1 = self._normalize(agg.get("ma5_above_avg", 0) * 100, 20, 80)
        s2 = self._normalize(agg.get("ma10_above_avg", 0) * 100, 15, 70)
        breadth_expansion = s1 * 0.55 + s2 * 0.45

        # 4. 龙头质量 leader_quality
        #    Data: 5d returns, sorted
        #    leader_5d = mean(top3 5d returns)
        #    follower_5d = mean(ranked 4-8 5d returns)
        #    consistency = % of stocks with 5d return > 0
        #    gap = leader_5d - follower_5d
        leader_5d = agg.get("top3_avg_5d", 0)
        follower_5d = agg.get("rest_avg_5d", 0)
        consistency = agg.get("positive_5d_ratio", 0.5) * 100  # Convert to percentage
        gap = leader_5d - follower_5d
        
        s1 = self._normalize(leader_5d, 0, 25)
        s2 = self._normalize(consistency, 30, 80)
        s3 = self._normalize(gap, 0, 15)
        leader_quality = s1 * 0.35 + s2 * 0.35 + s3 * 0.3

        # 5. 资本属性鉴别 capital_style
        #    Data: turnover_5d_avg, turnover_5d_cv, limit_up_count_5d, volatility_5d
        #    Institutional characteristics: low CV, moderate turnover, few limit-ups, low volatility
        #    Hot money characteristics: high CV, high turnover, many limit-ups, high volatility
        turnover_cv = agg.get("avg_turnover_cv", 0)
        turnover_avg = agg.get("avg_turnover_5d", 0)
        limit_up_count = agg.get("total_limit_up_5d", 0)
        has_turnover_data = turnover_avg > 0.001  # 换手率>0.1%才算有效数据
        
        if not has_turnover_data:
            # 缺失换手数据时给中性分3.0（不偏不倚），不假装判别
            capital_style = 3.0
        else:
            institutional_signal = 0.0
            # Low turnover CV = institutional (CV < 0.5 is good)
            s1 = self._normalize(1.0 - min(turnover_cv, 1.0), 0, 1.0)
            institutional_signal += s1 * 0.3  # max 1.5
            
            # Moderate turnover (2-8% is institutional zone; extremes = hot money)
            # Score peaks at turnover ~5%, drops off at extremes
            turnover_fit = max(0, 1.0 - abs(turnover_avg - 0.05) / 0.15)
            s2 = turnover_fit * 5
            institutional_signal += s2 * 0.4  # max 2.0
            
            # Few limit ups = institutional, many = hot money
            s3 = self._normalize(4 - min(limit_up_count, 8), 0, 8)
            institutional_signal += s3 * 0.3  # max 1.5
            
            capital_style = min(institutional_signal, 5.0)

        # 6. 趋势阶段 trend_maturity
        #    Data: days_above_ma20, ma20_slope, acceleration
        #    Sweet spot: 3-10 days above MA20, accelerating, MA20 sloping up
        days_above_ma20 = agg.get("days_above_ma20_est", 5)
        
        s1 = self._normalize(days_above_ma20, 0, 20)  # penalizes >15
        if days_above_ma20 > 15:
            s1 *= 0.6  # overheat penalty
        
        s2 = self._normalize(agg.get("avg_accel", 0), -3, 8)
        s3 = self._normalize(agg.get("avg_ma20_slope", 0), -2, 4)
        trend_maturity = s1 * 0.35 + s2 * 0.35 + s3 * 0.3

        # 7. 行业逻辑 industry_logic（不变）
        #    industry_logic = _normalize(len(rows), 6, 45)
        industry_logic = self._normalize(n_stocks, 6, 45)

        # 8. 日内动量 intraday_momentum（仅作辅助确认信号，权重较低）
        #    放宽归一化区间，避免日内普涨时全部满分
        s1 = self._normalize(intraday_positive * 100, 40, 90)
        s2 = self._normalize(intraday_strong * 100, 10, 45)
        intraday_momentum = s1 * 0.5 + s2 * 0.5

        # 9. 趋势加速度 trend_acceleration
        #    Data: acceleration, return_3d
        s1 = self._normalize(agg.get("avg_accel", 0), -5, 10)
        s2 = self._normalize(agg.get("avg_3d_return", 0), -5, 15)
        trend_acceleration = s1 * 0.6 + s2 * 0.4

        return {
            "trend_persistence": round(trend_persistence, 2),
            "capital_flow": round(capital_flow, 2),
            "breadth_expansion": round(breadth_expansion, 2),
            "leader_quality": round(leader_quality, 2),
            "capital_style": round(capital_style, 2),
            "trend_maturity": round(trend_maturity, 2),
            "industry_logic": round(industry_logic, 2),
            "intraday_momentum": round(intraday_momentum, 2),
            "trend_acceleration": round(trend_acceleration, 2),
        }

    # ------------------------------------------------------------------
    # 多日评分主入口（替换旧版纯日内评分）
    # ------------------------------------------------------------------
    def get_mainline_scores(
        self,
        load_mainlines,
        resolve_codes_for_mainline,
        build_stock_metrics,
    ) -> list[MainlineScore]:
        """基于多日K线数据的9维度评分，日内数据仅作确认信号。"""
        # ------------------------------------------------------------------
        # 第 1 步：遍历所有主线，收集全部唯一股票代码
        # ------------------------------------------------------------------
        definitions = load_mainlines()
        all_hit_counters: list[tuple[object, dict[str, int]]] = []
        unique_codes: set[str] = set()
        for definition in definitions:
            hit_counter = resolve_codes_for_mainline(definition)
            if hit_counter:
                all_hit_counters.append((definition, hit_counter))
                unique_codes.update(hit_counter.keys())

        # ------------------------------------------------------------------
        # 第 2 步：批量预取 tick 和 instrument_detail（1 次调用代替 N 次）
        # ------------------------------------------------------------------
        if unique_codes:
            codes_list = list(unique_codes)
            tick_map_cache = self.connector.get_full_tick(codes_list)
            # 存入请求级缓存，后续 _candidate_rows_for_mainline 等调用可直接复用
            self._req_cache["tick_map_global"] = tick_map_cache
            # 并行预热 instrument_detail 缓存（4 线程并行，2000 只股票 ~500 批次）
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="detail-prefetch") as pool:
                list(pool.map(self.connector.get_instrument_detail, codes_list))
        else:
            tick_map_cache = {}

        # ------------------------------------------------------------------
        # 第 2.5 步：批量预取多日K线数据
        # ------------------------------------------------------------------
        codes_list = list(unique_codes) if unique_codes else []
        multiday_metrics: dict[str, dict[str, Any]] = {}
        if codes_list:
            multiday_metrics = self._compute_stock_multiday_metrics(codes_list)

        # ------------------------------------------------------------------
        # 第 3 步：用 tick + 多日数据计算各主线评分
        # ------------------------------------------------------------------
        scores: list[MainlineScore] = []
        for definition, hit_counter in all_hit_counters:
            # 日内数据（用于 intraday_momentum + 过滤）
            rows = build_stock_metrics(hit_counter, pre_fetched_tick=tick_map_cache)
            rows = [row for row in rows if row["sector_hits"] >= definition.min_sector_hits]
            if not rows:
                continue

            # --- 日内指标（仅作确认信号） ---
            intraday_positive = sum(1 for row in rows if row["pct_change"] > 0) / len(rows)
            intraday_strong = sum(1 for row in rows if row["pct_change"] >= 3) / len(rows)
            intraday_limit_up = sum(1 for row in rows if row["pct_change"] >= 9.5)

            # --- 多日指标（核心评分来源） ---
            stock_codes = [row["ticker"] for row in rows]
            sector_multiday = {
                code: multiday_metrics[code]
                for code in stock_codes
                if code in multiday_metrics
            }
            agg = self._aggregate_multiday(sector_multiday) if sector_multiday else {}

            if not agg:
                # 多日数据不足时，降级使用日内数据（兼容性回退）
                agg = {
                    "avg_5d_return": 0, "avg_10d_return": 0, "avg_3d_return": 0, "avg_accel": 0,
                    "ma5_above_avg": 0.5, "ma10_above_avg": 0.4, "ma20_above_ratio": 0.5,
                    "avg_ma20_slope": 0, "avg_vol_trend": 1.0, "avg_vol_5d": 0,
                    "avg_volatility": 3.0, "avg_turnover_cv": 0.3, "avg_turnover_5d": 0.05,
                    "positive_5d_ratio": intraday_positive, "strong_5d_ratio": intraday_strong,
                    "avg_dist_from_high": -5, "total_limit_up_5d": intraday_limit_up,
                    "days_above_ma20_est": 5, "leader_gap": 3,
                    "top3_avg_5d": 0, "rest_avg_5d": 0,
                }

            # --- 9维度评分 ---
            dim = self._compute_9dim_scores(
                agg,
                intraday_positive=intraday_positive,
                intraday_strong=intraday_strong,
                intraday_limit_up=intraday_limit_up,
                n_stocks=len(rows),
            )

            # 映射到 MainlineScore（保持字段名向后兼容）
            # 根据用户要求进行映射：
            # - industry_logic_score → industry_logic (unchanged)
            # - capital_strength_score → capital_flow
            # - leader_score → leader_quality
            # - core_score → trend_acceleration (was core_strength, now trend acceleration)
            # - diffusion_score → breadth_expansion (was diffusion)
            # - persistence_score → trend_persistence
            # - market_status_score → intraday_momentum (辅助确认，仅反映日内强弱)
            industry_logic = dim["industry_logic"]
            capital_strength = dim["capital_flow"]          # 资金流向
            leader_strength = dim["leader_quality"]         # 龙头质量
            trend_acceleration = dim["trend_acceleration"]  # 趋势加速度
            diffusion = dim["breadth_expansion"]            # 扩散广度
            persistence = dim["trend_persistence"]          # 趋势持续性
            intraday_momentum = dim["intraday_momentum"]    # 日内动量（仅辅助确认）
            capital_style = dim["capital_style"]            # 资本属性（机构 vs 游资）
            trend_maturity = dim["trend_maturity"]          # 趋势阶段（站上MA20天数/MA20斜率）

            # 总分 = 8个维度 + 日内动量确认（满分40+5=45）
            # 注意：日内动量仅作辅助确认，不重复计入
            total = round(
                industry_logic
                + capital_strength
                + leader_strength
                + trend_acceleration
                + diffusion
                + persistence
                + trend_maturity
                + capital_style
                + intraday_momentum,
                2,
            )

            # ------------------------------------------------------------------
            # 拥挤度惩罚（新公式）：扩散不足 + 龙头独涨 = 假高潮
            # ------------------------------------------------------------------
            breadth_health = diffusion + persistence
            crowding_penalty = 0.0
            if leader_strength >= 3.5 and breadth_health < 2.0:
                crowding_penalty = min(leader_strength * 0.4, 2.5)
                total = max(total - crowding_penalty, 0)
                total = round(total, 2)

            # ------------------------------------------------------------------
            # Tier 判定（新阈值，max total 45）
            # ------------------------------------------------------------------
            if breadth_health >= 3.0:
                tier = "core" if total >= 22 else "secondary" if total >= 16 else "rotation"
            elif breadth_health >= 1.5:
                tier = "secondary" if total >= 16 else "rotation"
            else:
                tier = "rotation"

            # ------------------------------------------------------------------
            # 健康标签（修正版）：先判晚期，再判健康，避免晚期被吃掉
            # ------------------------------------------------------------------
            if trend_maturity >= 3.5 and leader_strength >= 3.5 and persistence >= 3.0:
                health_label = "late_stage"           # 晚期/鱼尾：趋势已老+龙头独涨+涨幅已大
            elif breadth_health >= 3.0 and leader_strength >= 2.5:
                health_label = "healthy_rally"        # 真启动：有宽度有力度
            elif trend_maturity >= 1.0 and trend_maturity <= 3.0 and breadth_health >= 1.5:
                health_label = "early_stage"          # 早期升温：趋势阶段早，扩散在起来
            elif breadth_health < 1.0 and leader_strength < 2.0:
                health_label = "dormant"              # 沉寂
            else:
                health_label = "mixed"

            scores.append(
                MainlineScore(
                    name=definition.name,
                    group=definition.group,
                    industry_logic_score=industry_logic,
                    capital_strength_score=capital_strength,
                    leader_score=leader_strength,
                    core_score=trend_acceleration,
                    diffusion_score=diffusion,
                    persistence_score=persistence,
                    market_status_score=intraday_momentum,
                    total_score=total,
                    tier=tier,
                    health_label=health_label,
                    crowding_penalty=crowding_penalty,
                    capital_style_score=capital_style,
                    trend_accel_score=trend_acceleration,
                    trend_maturity_score=trend_maturity,
                )
            )
        return scores



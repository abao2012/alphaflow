from app.models.domain import BuySignal, Position, SellSignal, StockCandidate


class SignalEngine:
    def build_buy_watchlist(self, stocks: list[StockCandidate], signal_type: str) -> list[BuySignal]:
        signals: list[BuySignal] = []
        for item in stocks:
            current_type = item.signal_tags[0] if item.signal_tags else "pullback"
            if signal_type != "all" and current_type != signal_type:
                continue
            if item.score < 72 or item.amount < 8e7:
                continue
            if current_type == "breakout":
                stop_loss = "Break below the breakout bar low or lose the 5-day trend."
                risk_level = "low" if item.role == "leader" and item.sector_hits >= 2 else "medium"
            elif current_type == "reclaim":
                stop_loss = "Fall back below the reclaim level or lose the intraday VWAP."
                risk_level = "medium"
            else:
                stop_loss = "Lose the pullback support area or close below the recent swing low."
                risk_level = "medium" if item.role != "followup" else "high"

            suggested_position_pct = 0.12 if item.role == "leader" else 0.08 if item.role == "core_middle" else 0.05
            signals.append(
                BuySignal(
                    ticker=item.ticker,
                    signal_type=current_type,
                    entry_price=item.last_price,
                    suggested_position_pct=suggested_position_pct,
                    stop_loss=stop_loss,
                    risk_level=risk_level,
                )
            )
        return signals

    def build_sell_watchlist(
        self,
        positions: list[Position],
        active_groups: list[str],
        core_tickers_by_group: dict[str, set[str]],
    ) -> list[SellSignal]:
        items: list[SellSignal] = []
        active_set = set(active_groups)
        for position in positions:
            if position.mapped_group is None:
                items.append(
                    SellSignal(
                        ticker=position.ticker,
                        reason="Holding is outside the current tracked mainline groups.",
                        action="exit" if position.position_pct <= 0.08 or position.pnl_pct < 0 else "trim",
                        trend_status=f"PnL {position.pnl_pct:.2f}%",
                    )
                )
                continue

            if position.mapped_group not in active_set:
                items.append(
                    SellSignal(
                        ticker=position.ticker,
                        reason=f"{position.mapped_group} is no longer in the active mainline groups.",
                        action="trim" if position.position_pct >= 0.08 else "exit",
                        trend_status=f"PnL {position.pnl_pct:.2f}%",
                    )
                )
                continue

            if position.ticker not in core_tickers_by_group.get(position.mapped_group, set()) and position.position_pct >= 0.08:
                items.append(
                    SellSignal(
                        ticker=position.ticker,
                        reason=f"{position.mapped_group} remains active, but this name is no longer in the current core branch watchlist.",
                        action="trim",
                        trend_status=f"PnL {position.pnl_pct:.2f}%",
                    )
                )
                continue

            if position.pnl_pct <= -7:
                items.append(
                    SellSignal(
                        ticker=position.ticker,
                        reason="Unrealized drawdown breached the discipline threshold.",
                        action="exit",
                        trend_status=f"PnL {position.pnl_pct:.2f}%",
                    )
                )
        return items

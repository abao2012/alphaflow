from uuid import uuid4

from app.core.config import Settings
from app.models.domain import OrderDraft, Position
from app.services.qmt_connector import QmtConnector


class OrderGuard:
    def __init__(self, settings: Settings, connector: QmtConnector) -> None:
        self.settings = settings
        self.connector = connector

    def prepare(
        self,
        ticker: str,
        action: str,
        target_position_pct: float,
        account_id: str,
        positions: list[Position],
    ) -> OrderDraft:
        total_exposure = sum(item.position_pct for item in positions)
        risk_notes: list[str] = []
        passed = True

        asset = self.connector.query_stock_asset()
        total_asset = float(getattr(asset, "total_asset", 0) or 0)
        latest_tick = self.connector.get_full_tick([ticker]).get(ticker, {})
        reference_price = float(latest_tick.get("lastPrice") or 0)
        target_value = total_asset * target_position_pct
        current_value = next((item.market_value for item in positions if item.ticker == ticker), 0.0)
        current_position_pct = round((current_value / total_asset), 4) if total_asset > 0 else 0.0
        delta_value = target_value - current_value if action == "buy" else current_value - target_value
        order_volume = int(max(delta_value, 0) / reference_price / 100) * 100 if reference_price > 0 else 0
        delta_position_pct = max(target_position_pct - current_position_pct, 0.0) if action == "buy" else max(current_position_pct - target_position_pct, 0.0)
        estimated_exposure = total_exposure + delta_position_pct if action == "buy" else max(0.0, total_exposure - delta_position_pct)

        if target_position_pct > self.settings.max_single_position:
            passed = False
            risk_notes.append("Single-stock target exposure exceeds the configured limit.")
        if estimated_exposure > self.settings.max_total_exposure:
            passed = False
            risk_notes.append("Total portfolio exposure would exceed the configured limit.")
        if reference_price <= 0:
            passed = False
            risk_notes.append("Failed to read the latest market price for this ticker.")
        if order_volume <= 0:
            passed = False
            risk_notes.append("Computed order volume is below one trading lot or target position is unchanged.")
        if not risk_notes:
            risk_notes.append("Passed the basic exposure and pricing checks.")

        return OrderDraft(
            draft_id=f"draft_{uuid4().hex[:12]}",
            ticker=ticker,
            action=action,
            account_id=account_id,
            target_position_pct=target_position_pct,
            order_volume=order_volume,
            reference_price=round(reference_price, 3),
            passed_checks=passed,
            estimated_exposure=round(estimated_exposure, 2),
            risk_notes=risk_notes,
        )

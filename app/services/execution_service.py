import json

from pydantic import ValidationError

from app.core.config import Settings
from app.models.domain import OrderDraft, OrderResult
from app.services.qmt_connector import QmtConnector


class ExecutionService:
    def __init__(self, connector: QmtConnector, settings: Settings) -> None:
        self.connector = connector
        self.settings = settings
        self._draft_cache: dict[str, OrderDraft] = {}
        self._order_to_draft: dict[str, str] = {}
        self._state_path = settings.execution_state_path
        self._load_state()

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            drafts = payload.get("drafts", {})
            self._draft_cache = {draft_id: OrderDraft.model_validate(item) for draft_id, item in drafts.items()}
            self._order_to_draft = {
                str(order_id): str(draft_id)
                for order_id, draft_id in payload.get("order_to_draft", {}).items()
            }
        except (OSError, json.JSONDecodeError, ValidationError):
            self._draft_cache = {}
            self._order_to_draft = {}

    def _persist_state(self) -> None:
        payload = {
            "drafts": {draft_id: draft.model_dump() for draft_id, draft in self._draft_cache.items()},
            "order_to_draft": self._order_to_draft,
        }
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _remember_order_mapping(self, order_id: str, draft_id: str) -> None:
        self._order_to_draft[str(order_id)] = draft_id
        self._persist_state()

    @staticmethod
    def _advisory_order_id(draft_id: str) -> str:
        return f"advisory:{draft_id}"

    def remember_draft(self, draft: OrderDraft) -> None:
        self._draft_cache[draft.draft_id] = draft
        self._persist_state()

    def get_draft(self, draft_id: str) -> OrderDraft | None:
        return self._draft_cache.get(draft_id)

    def _calc_order_price(self, ticker: str, fallback: float = 0.0) -> float:
        tick = self.connector.get_full_tick([ticker]).get(ticker, {})
        return float(tick.get("lastPrice") or fallback or 0.0)

    def confirm(self, draft: OrderDraft, user_confirmed: bool) -> OrderResult:
        if not user_confirmed:
            self._remember_order_mapping("rejected", draft.draft_id)
            return OrderResult(order_id="rejected", draft_id=draft.draft_id, status="cancelled", filled_quantity=0)
        if not draft.passed_checks:
            self._remember_order_mapping("rejected", draft.draft_id)
            return OrderResult(order_id="rejected", draft_id=draft.draft_id, status="rejected", filled_quantity=0)
        if self.settings.advisory_only_mode:
            order_id = self._advisory_order_id(draft.draft_id)
            self._remember_order_mapping(order_id, draft.draft_id)
            return OrderResult(order_id=order_id, draft_id=draft.draft_id, status="advisory_only", filled_quantity=0)
        if not self.settings.enable_order_submission:
            self._remember_order_mapping("dry-run", draft.draft_id)
            return OrderResult(order_id="dry-run", draft_id=draft.draft_id, status="blocked", filled_quantity=0)

        order_type = self.connector.xtconstant().STOCK_BUY if draft.action == "buy" else self.connector.xtconstant().STOCK_SELL
        price_type = self.connector.xtconstant().FIX_PRICE
        price = self._calc_order_price(draft.ticker, draft.reference_price)
        order_id = self.connector.place_order(
            stock_code=draft.ticker,
            order_type=order_type,
            order_volume=draft.order_volume,
            price_type=price_type,
            price=price,
            order_remark=draft.draft_id,
        )
        self._remember_order_mapping(str(order_id), draft.draft_id)
        return OrderResult(order_id=str(order_id), draft_id=draft.draft_id, status="submitted", filled_quantity=0)

    def get_status(self, order_id: str) -> OrderResult:
        if order_id.startswith("advisory:"):
            return OrderResult(
                order_id=order_id,
                draft_id=self._order_to_draft.get(order_id, "unknown"),
                status="advisory_only",
                filled_quantity=0,
            )
        if order_id in {"dry-run", "rejected"}:
            return OrderResult(order_id=order_id, draft_id=self._order_to_draft.get(order_id, "unknown"), status=order_id, filled_quantity=0)
        order = self.connector.query_stock_order(order_id)
        if order is None:
            return OrderResult(order_id=str(order_id), draft_id=self._order_to_draft.get(str(order_id), "unknown"), status="unknown", filled_quantity=0)
        return OrderResult(
            order_id=str(getattr(order, "order_id", order_id)),
            draft_id=self._order_to_draft.get(str(order_id), "unknown"),
            status=str(getattr(order, "status_msg", getattr(order, "order_status", "submitted"))),
            filled_quantity=int(getattr(order, "traded_volume", 0) or 0),
        )

    def cancel(self, order_id: str) -> OrderResult:
        if order_id.startswith("advisory:"):
            return OrderResult(
                order_id=order_id,
                draft_id=self._order_to_draft.get(order_id, "unknown"),
                status="cancelled",
                filled_quantity=0,
            )
        if order_id in {"dry-run", "rejected"}:
            return OrderResult(order_id=order_id, draft_id=self._order_to_draft.get(order_id, "unknown"), status="cancelled", filled_quantity=0)
        cancel_result = self.connector.cancel_order(order_id)
        status = "cancelled" if cancel_result == 0 else f"cancel_failed_{cancel_result}"
        return OrderResult(
            order_id=str(order_id),
            draft_id=self._order_to_draft.get(str(order_id), "unknown"),
            status=status,
            filled_quantity=0,
        )

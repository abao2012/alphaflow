from app.models.domain import StockCandidate


class CoreStockSelector:
    def split_roles(self, stocks: list[StockCandidate]) -> dict[str, list[str]]:
        groups = {"leaders": [], "core_middles": [], "followups": [], "excluded": []}
        for item in stocks:
            if item.score < 75:
                groups["excluded"].append(item.name)
            elif item.role == "leader":
                groups["leaders"].append(item.name)
            elif item.role == "core_middle":
                groups["core_middles"].append(item.name)
            else:
                groups["followups"].append(item.name)
        return groups

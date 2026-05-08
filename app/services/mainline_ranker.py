from app.models.domain import MainlineScore


class MainlineRanker:
    def rank_mainlines(self, scores: list[MainlineScore]) -> list[MainlineScore]:
        return sorted(scores, key=lambda item: item.total_score, reverse=True)

    def market_phase(self, scores: list[MainlineScore]) -> str:
        top_score = max((item.total_score for item in scores), default=0)
        if top_score >= 26:
            return "主升"
        if top_score >= 22:
            return "轮动"
        return "退潮"

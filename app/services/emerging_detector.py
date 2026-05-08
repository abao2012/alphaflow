import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.models.domain import MainlineScore


class EmergingMainlineDetector:
    def __init__(self, snapshot_path: Path, retention_hours: int = 8) -> None:
        self.snapshot_path = snapshot_path
        self.retention_hours = retention_hours

    def record_snapshot(self, scores: list[MainlineScore], now: datetime | None = None) -> None:
        now = now or datetime.now()
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        ordered = sorted(scores, key=lambda item: item.total_score, reverse=True)
        payload = {
            "ts": now.isoformat(),
            "scores": [
                {
                    "name": item.name,
                    "group": item.group,
                    "rank": index,
                    "total_score": item.total_score,
                    "capital_strength_score": item.capital_strength_score,
                    "leader_score": item.leader_score,
                    "diffusion_score": item.diffusion_score,
                    "persistence_score": item.persistence_score,
                    "market_status_score": item.market_status_score,
                    "trend_maturity_score": item.trend_maturity_score,
                    "capital_style_score": item.capital_style_score,
                    "trend_accel_score": item.trend_accel_score,
                }
                for index, item in enumerate(ordered, start=1)
            ],
        }
        with self.snapshot_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._trim_snapshots(now)

    def detect(
        self,
        scores: list[MainlineScore],
        lookback_minutes: int = 30,
        limit: int = 8,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        now = now or datetime.now()
        ordered = sorted(scores, key=lambda item: item.total_score, reverse=True)
        baseline = self._find_baseline(now=now, lookback_minutes=lookback_minutes)
        baseline_scores = {item["name"]: item for item in baseline.get("scores", [])} if baseline else {}
        items: list[dict[str, Any]] = []

        for current_rank, score in enumerate(ordered, start=1):
            previous = baseline_scores.get(score.name)
            previous_rank = int(previous["rank"]) if previous else None
            previous_score = float(previous["total_score"]) if previous else None
            rank_change = previous_rank - current_rank if previous_rank is not None else None
            score_change = round(score.total_score - previous_score, 2) if previous_score is not None else None
            avoid_chase = self._is_hot_confirmed(score, current_rank)
            early_score = self._early_score(score, current_rank, rank_change, score_change, avoid_chase)
            stage = self._stage(score, rank_change, score_change, avoid_chase)
            suggestion = self._suggestion(stage, early_score, avoid_chase)
            action_plan = self._action_plan(stage, suggestion, avoid_chase)
            confidence = self._confidence(stage, early_score, score_change)
            position_budget_pct = self._position_budget_pct(action_plan, confidence)
            catalyst_tags = self._catalyst_tags(score, rank_change, score_change, avoid_chase)
            reasons = self._reasons(score, rank_change, score_change, previous is None, avoid_chase)
            dedupe_key = self._dedupe_key(now.date(), score.name, suggestion)

            if stage != "confirmed_hot" and early_score < 55:
                continue
            if current_rank > 20 and (rank_change is None or rank_change < 5):
                continue

            items.append(
                {
                    "name": score.name,
                    "group": score.group,
                    "stage": stage,
                    "suggestion": suggestion,
                    "action_plan": action_plan,
                    "confidence": confidence,
                    "position_budget_pct": position_budget_pct,
                    "early_score": round(early_score, 2),
                    "current_rank": current_rank,
                    "previous_rank": previous_rank,
                    "rank_change": rank_change,
                    "current_score": score.total_score,
                    "previous_score": previous_score,
                    "score_change": score_change,
                    "avoid_chase": avoid_chase,
                    "catalyst_tags": catalyst_tags,
                    "reasons": reasons,
                    "leaders": [],
                    "dedupe_key": dedupe_key,
                }
            )

        return sorted(
            items,
            key=lambda item: (item["avoid_chase"], item["early_score"]),
            reverse=True,
        )[:limit]

    def _load_snapshots(self) -> list[dict[str, Any]]:
        if not self.snapshot_path.exists():
            return []
        snapshots: list[dict[str, Any]] = []
        for line in self.snapshot_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                snapshots.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return snapshots

    def _find_baseline(self, now: datetime, lookback_minutes: int) -> dict[str, Any] | None:
        lower_bound = now - timedelta(minutes=lookback_minutes)
        min_age = now - timedelta(minutes=3)
        candidates: list[tuple[datetime, dict[str, Any]]] = []
        for snapshot in self._load_snapshots():
            try:
                ts = datetime.fromisoformat(snapshot["ts"])
            except (KeyError, TypeError, ValueError):
                continue
            if lower_bound <= ts <= min_age:
                candidates.append((ts, snapshot))
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    def _trim_snapshots(self, now: datetime) -> None:
        cutoff = now - timedelta(hours=self.retention_hours)
        kept: list[str] = []
        for snapshot in self._load_snapshots():
            try:
                ts = datetime.fromisoformat(snapshot["ts"])
            except (KeyError, TypeError, ValueError):
                continue
            if ts >= cutoff:
                kept.append(json.dumps(snapshot, ensure_ascii=False))
        self.snapshot_path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")

    @staticmethod
    def _is_hot_confirmed(score: MainlineScore, current_rank: int) -> bool:
        """多日版：趋势已老(trend_maturity高) + 龙头独强 = 过热确认区，不宜追高。"""
        return (
            current_rank <= 5
            and score.total_score >= 25          # 总分高
            and score.leader_score >= 3.5        # 龙头质量强
            and score.trend_maturity_score >= 3.5  # 趋势阶段已晚（站上MA20>15天）
            and score.persistence_score >= 2.5    # 多日涨幅已大
        )

    @staticmethod
    def _early_score(
        score: MainlineScore,
        current_rank: int,
        rank_change: int | None,
        score_change: float | None,
        avoid_chase: bool,
    ) -> float:
        """多日版早期信号评分：侧重趋势持续性、资本属性、加速度。"""
        current_structure = score.total_score / 35 * 30
        # 多日资金先行（持续流入比单日爆发更重要）
        capital_signal = score.capital_strength_score * 4
        # 扩散广度（MA对齐度，不是日内红盘率）
        breadth_signal = score.diffusion_score * 3
        # 趋势加速度（加速=好信号）
        accel_signal = score.trend_accel_score * 3
        # 资本属性（机构抱团加分，游资炒作扣分）
        style_signal = (score.capital_style_score - 2.5) * 2  # [-5, +5]
        # 持续性适中加分（刚开始涨最好，涨太久不加分）
        persistence_val = score.persistence_score
        persistence_bonus = max(0.0, min(persistence_val, 3.0)) * 1.5
        # 排名跃升信号
        rank_signal = max(0, rank_change or 0) * 2.5
        score_signal = max(0.0, score_change or 0.0) * 4
        rank_position_bonus = max(0.0, 10 - current_rank) * 1.0
        chase_penalty = 25 if avoid_chase else 0
        return max(
            0.0,
            min(
                100.0,
                current_structure
                + capital_signal
                + breadth_signal
                + accel_signal
                + style_signal
                + persistence_bonus
                + rank_signal
                + score_signal
                + rank_position_bonus
                - chase_penalty,
            ),
        )

    @staticmethod
    def _stage(
        score: MainlineScore,
        rank_change: int | None,
        score_change: float | None,
        avoid_chase: bool,
    ) -> str:
        """多日版阶段判定：用趋势阶段和加速度判断。"""
        if avoid_chase:
            return "confirmed_hot"
        # 早期信号：排名跃升 + 趋势阶段早 + 加速
        is_early = (rank_change is not None and rank_change >= 4) or \
                   (score_change is not None and score_change >= 2.0) or \
                   (score.trend_maturity_score <= 2.0 and score.trend_accel_score >= 2.0)
        if is_early:
            return "early_watch"
        # 升温中：资金有流入 + 扩散开始，但还没明显加速
        if score.capital_strength_score >= 2.5 and score.diffusion_score >= 2.0:
            return "warming"
        return "warming"

    @staticmethod
    def _suggestion(stage: str, early_score: float, avoid_chase: bool) -> str:
        if avoid_chase:
            return "avoid_chase"
        if stage == "early_watch" and early_score >= 70:
            return "probe"
        if stage == "warming" and early_score >= 60:
            return "probe"
        return "watch"

    @staticmethod
    def _action_plan(stage: str, suggestion: str, avoid_chase: bool) -> str:
        if avoid_chase:
            return "avoid_chase"
        if suggestion == "probe":
            return "probe_small"
        if stage == "confirmed_hot":
            return "hold_and_confirm"
        return "observe"

    @staticmethod
    def _confidence(stage: str, early_score: float, score_change: float | None) -> str:
        if stage == "confirmed_hot":
            return "high"
        if early_score >= 75 or (score_change is not None and score_change >= 3.0):
            return "high"
        if early_score >= 60:
            return "medium"
        return "low"

    @staticmethod
    def _position_budget_pct(action_plan: str, confidence: str) -> float:
        if action_plan == "avoid_chase":
            return 0.0
        if action_plan == "probe_small":
            if confidence == "high":
                return 0.05
            if confidence == "medium":
                return 0.03
        return 0.0

    @staticmethod
    def _catalyst_tags(
        score: MainlineScore,
        rank_change: int | None,
        score_change: float | None,
        avoid_chase: bool,
    ) -> list[str]:
        """多日版催化因素标签。"""
        tags: list[str] = []
        if score.capital_strength_score >= 3.0:
            tags.append("多日资金持续流入")
        if score.diffusion_score >= 2.5:
            tags.append("MA对齐扩散增强")
        if score.trend_accel_score >= 2.5:
            tags.append("趋势加速")
        if score.capital_style_score >= 3.5:
            tags.append("机构抱团型")
        elif score.capital_style_score <= 1.5:
            tags.append("游资炒作型")
        if score.trend_maturity_score <= 2.0:
            tags.append("趋势早期阶段")
        elif score.trend_maturity_score >= 3.5:
            tags.append("趋势晚期")
        if score.persistence_score >= 3.0:
            tags.append("多日涨幅较大")
        if score.leader_score >= 3.5:
            tags.append("龙头质量突出")
        if rank_change is not None and rank_change > 0:
            tags.append("排名加速")
        if score_change is not None and score_change >= 2.0:
            tags.append("评分抬升")
        if avoid_chase:
            tags.append("确认区过热")
        return tags

    @staticmethod
    def _dedupe_key(trade_date: date, name: str, suggestion: str) -> str:
        return f"{trade_date.isoformat()}:{name}:{suggestion}"

    @staticmethod
    def _reasons(
        score: MainlineScore,
        rank_change: int | None,
        score_change: float | None,
        no_history: bool,
        avoid_chase: bool,
    ) -> list[str]:
        """多日版打分原因：每个板块给出各维度的具体解释。"""
        reasons: list[str] = []
        if no_history:
            reasons.append("尚无足够历史快照，先按当前结构预警。")
        if rank_change is not None and rank_change > 0:
            reasons.append(f"短周期排名提升 {rank_change} 位。")
        if score_change is not None and score_change > 0:
            reasons.append(f"短周期评分提升 {score_change:.2f} 分。")

        # 资金维度
        if score.capital_strength_score >= 3.5:
            reasons.append("近5日/10日成交额持续放大，资金有明显先行迹象。")
        elif score.capital_strength_score >= 2.5:
            reasons.append("近5日成交额温和放大，资金关注度提升。")

        # 扩散维度
        if score.diffusion_score >= 3.0:
            reasons.append("成分股站上MA5/MA10的比例高，扩散广度充足。")
        elif score.diffusion_score >= 2.0:
            reasons.append("扩散广度开始增强，半数以上成分股站上短期均线。")

        # 趋势阶段
        if score.trend_maturity_score <= 2.0:
            reasons.append("趋势处于早期阶段，刚站上MA20不久，空间充足。")
        elif score.trend_maturity_score >= 3.5:
            reasons.append("趋势已持续较久，需警惕后期衰竭风险。")

        # 加速度
        if score.trend_accel_score >= 3.0:
            reasons.append("近3日涨幅明显快于5日均速，趋势正在加速。")
        elif score.trend_accel_score <= 1.0 and score.persistence_score >= 3.0:
            reasons.append("近3日涨幅放缓，动力可能开始衰竭。")

        # 资本属性
        if score.capital_style_score >= 3.5:
            reasons.append("低波动+换手稳定，符合机构抱团特征，趋势持续性较强。")
        elif score.capital_style_score <= 1.5:
            reasons.append("高波动+频繁涨停，游资炒作特征明显，警惕昙花一现。")

        # 龙头质量
        if score.leader_score >= 3.5:
            reasons.append("龙头股多日涨幅突出且跟风股有参与，结构健康。")
        elif score.leader_score >= 2.5:
            reasons.append("龙头表现稳健，跟风参与度一般。")

        # 持续性
        if score.persistence_score >= 3.0:
            reasons.append("5日/10日累计涨幅较大，趋势已验证。")
        elif score.persistence_score <= 1.5:
            reasons.append("多日涨幅有限，趋势尚待验证。")

        if avoid_chase:
            reasons.append("分支已处在过热确认区（趋势晚期+龙头独涨），避免把确认信号当成早期信号追高。")
        return reasons or ["当前结构进入升温观察区。"]

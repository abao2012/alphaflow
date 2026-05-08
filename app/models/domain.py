from typing import Any, Literal

from pydantic import BaseModel, Field


Tier = Literal["core", "secondary", "rotation", "noise"]
SignalType = Literal["breakout", "reclaim", "pullback"]
ActionType = Literal["hold", "trim", "exit", "buy"]
TrendStage = Literal["startup", "markup", "pullback", "tail_rebound"]
SystemAttitude = Literal["potential_first", "monitor_closely", "trend_follow_only", "high_risk_tail"]


class MainlineScore(BaseModel):
    name: str
    group: str | None = None
    # --- 核心评分维度（多日为主，日内为辅） ---
    industry_logic_score: float       # 行业逻辑：成分股数量
    capital_strength_score: float     # 资金流向：5d/10d成交额趋势
    leader_score: float               # 龙头质量：多日持续涨幅 + 跟风差距
    core_score: float                 # 趋势加速度：加速(高) vs 减速(低)
    diffusion_score: float            # 扩散广度：5d内个股站上MA5/MA10的比例
    persistence_score: float          # 趋势持续性：5d/10d累计涨幅
    market_status_score: float        # 日内动量：仅作辅助确认信号
    total_score: float
    tier: Tier
    health_label: str = "unknown"
    crowding_penalty: float = 0.0
    # --- 新增维度 ---
    capital_style_score: float = 0.0  # 资本属性鉴别：机构抱团(高) vs 游资炒作(低)
    trend_accel_score: float = 0.0    # 趋势加速度（冗余字段，保持向后兼容）
    trend_maturity_score: float = 0.0  # 趋势阶段：站上MA20天数/MA20斜率


class StockCandidate(BaseModel):
    ticker: str
    name: str
    role: Literal["leader", "core_middle", "followup"]
    score: float
    last_price: float
    pct_change: float = 0
    amount: float = 0
    sector_hits: int = 0
    signal_tags: list[str] = Field(default_factory=list)


class AttributionMetric(BaseModel):
    metric: str
    value: Any
    conclusion: str


class CandidateQualityScores(BaseModel):
    startup_quality: int
    trend_integrity: int
    capital_quality: int
    crowding_risk: int


class CandidateTrendDiagnosis(BaseModel):
    ticker: str
    name: str
    role: Literal["leader", "core_middle", "followup"]
    score: float
    stage: TrendStage
    stage_reasons: list[str] = Field(default_factory=list)
    quality_scores: CandidateQualityScores
    risk_flags: list[str] = Field(default_factory=list)
    system_attitude: SystemAttitude
    attribution: list[AttributionMetric] = Field(default_factory=list)


class MainlineBreadthSnapshot(BaseModel):
    mainline: str
    stats: dict[str, float | int]


class CandidateReadinessHint(BaseModel):
    ticker: str
    name: str
    ready: bool
    missing: list[str] = Field(default_factory=list)
    reason: str = ""


class BuySignal(BaseModel):
    ticker: str
    signal_type: SignalType
    entry_price: float
    suggested_position_pct: float
    stop_loss: str
    risk_level: Literal["low", "medium", "high"]


class SellSignal(BaseModel):
    ticker: str
    reason: str
    action: Literal["trim", "exit"]
    trend_status: str


class Position(BaseModel):
    ticker: str
    name: str
    quantity: int
    cost_price: float
    last_price: float = 0
    pnl_pct: float = 0
    market_value: float
    position_pct: float
    mapped_group: str | None = None
    mapped_mainline: str | None = None
    is_core: bool = False
    suggestion: ActionType = "hold"


class RiskAlert(BaseModel):
    level: Literal["info", "warn", "critical"]
    title: str
    detail: str


class OrderDraft(BaseModel):
    draft_id: str
    ticker: str
    action: Literal["buy", "sell"]
    account_id: str
    target_position_pct: float
    order_volume: int = 0
    reference_price: float = 0
    passed_checks: bool
    estimated_exposure: float
    risk_notes: list[str] = Field(default_factory=list)


class OrderResult(BaseModel):
    order_id: str
    draft_id: str
    status: str
    filled_quantity: int = 0

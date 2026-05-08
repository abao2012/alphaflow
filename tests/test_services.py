from datetime import datetime, timedelta
from types import SimpleNamespace

from app.core.config import Settings
from app.models.domain import BuySignal, MainlineScore, OrderDraft, OrderResult, Position, RiskAlert, SellSignal, StockCandidate
from app.services.breadth_feature_extractor import BreadthFeatureExtractor
from app.services.candidate_diagnostics_service import CandidateDiagnosticsService
from app.services.emerging_detector import EmergingMainlineDetector
from app.services.execution_service import ExecutionService
from app.services.history_backfill_service import HistoryBackfillService
from app.services.market_data_service import MarketDataService
from app.services.order_guard import OrderGuard
from app.services.qmt_connector import QmtConnector
from app.services.stock_pool_builder import StockPoolBuilder
from app.services.mainline_scoring_service import MainlineScoringService
from app.services.quality_score_engine import QualityScoreEngine
from app.services.trend_feature_extractor import TrendFeatureExtractor
from app.services.trend_stage_engine import TrendStageEngine


class FakeConnector:
    def __init__(self, total_asset: float, price: float) -> None:
        self.total_asset = total_asset
        self.price = price

    def query_stock_asset(self):
        return SimpleNamespace(total_asset=self.total_asset)

    def get_full_tick(self, codes):
        return {code: {"lastPrice": self.price} for code in codes}


def make_score(
    name: str,
    total_score: float,
    capital_strength_score: float = 4.0,
    leader_score: float = 3.5,
    diffusion_score: float = 3.0,
    persistence_score: float = 1.0,
    market_status_score: float = 3.0,
    group: str = "AI算力",
    capital_style_score: float = 2.5,
    trend_accel_score: float = 2.0,
    trend_maturity_score: float = 2.0,
) -> MainlineScore:
    return MainlineScore(
        name=name,
        group=group,
        industry_logic_score=4.0,
        capital_strength_score=capital_strength_score,
        leader_score=leader_score,
        core_score=3.0,
        diffusion_score=diffusion_score,
        persistence_score=persistence_score,
        market_status_score=market_status_score,
        total_score=total_score,
        tier="secondary" if total_score < 24 else "core",
        capital_style_score=capital_style_score,
        trend_accel_score=trend_accel_score,
        trend_maturity_score=trend_maturity_score,
    )


def test_emerging_detector_flags_rank_acceleration(tmp_path):
    now = datetime(2026, 4, 20, 10, 30)
    detector = EmergingMainlineDetector(tmp_path / "snapshots.jsonl")
    detector.record_snapshot(
        [
            make_score("旧主线", 26.0, group="军工"),
            make_score("AI算力/CPO共封装", 18.5),
            make_score("其他", 17.0, group="其他"),
        ],
        now=now - timedelta(minutes=20),
    )

    items = detector.detect(
        [
            make_score("AI算力/CPO共封装", 23.5, capital_strength_score=4.6, diffusion_score=3.4, persistence_score=0.8),
            make_score("旧主线", 22.0, group="军工"),
            make_score("其他", 17.5, group="其他"),
        ],
        now=now,
    )

    cpo = next(item for item in items if item["name"] == "AI算力/CPO共封装")
    assert cpo["stage"] == "early_watch"
    assert cpo["suggestion"] in {"probe", "watch"}
    assert cpo["action_plan"] == "probe_small"
    assert cpo["confidence"] == "high"
    assert cpo["position_budget_pct"] == 0.05
    assert cpo["rank_change"] == 1
    assert cpo["score_change"] == 5.0
    assert cpo["avoid_chase"] is False
    assert cpo["catalyst_tags"] == ["多日资金持续流入", "MA对齐扩散增强", "龙头质量突出", "排名加速", "评分抬升"]
    assert cpo["dedupe_key"] == "2026-04-20:AI算力/CPO共封装:probe"


def test_emerging_detector_warns_when_theme_is_already_hot(tmp_path):
    detector = EmergingMainlineDetector(tmp_path / "snapshots.jsonl")
    items = detector.detect(
        [
            make_score(
                "AI算力/CPO共封装",
                31.0,
                capital_strength_score=5.0,
                leader_score=5.0,
                diffusion_score=4.0,
                persistence_score=2.8,
                trend_maturity_score=4.0,
            )
        ],
        now=datetime(2026, 4, 20, 10, 30),
    )

    assert items[0]["stage"] == "confirmed_hot"
    assert items[0]["suggestion"] == "avoid_chase"
    assert items[0]["action_plan"] == "avoid_chase"
    assert items[0]["confidence"] == "high"
    assert items[0]["position_budget_pct"] == 0.0
    assert items[0]["avoid_chase"] is True
    assert any("避免" in reason for reason in items[0]["reasons"])
    assert "确认区过热" in items[0]["catalyst_tags"]
    assert items[0]["dedupe_key"] == "2026-04-20:AI算力/CPO共封装:avoid_chase"


def test_emerging_detector_can_warn_without_history(tmp_path):
    detector = EmergingMainlineDetector(tmp_path / "snapshots.jsonl")
    items = detector.detect(
        [
            make_score(
                "低空经济/eVTOL",
                22.5,
                capital_strength_score=4.2,
                diffusion_score=3.2,
                persistence_score=0.6,
                group="低空经济",
            )
        ],
        now=datetime(2026, 4, 20, 10, 30),
    )

    assert items[0]["stage"] == "warming"
    assert items[0]["suggestion"] in {"watch", "probe"}
    assert items[0]["action_plan"] == "probe_small"
    assert items[0]["confidence"] == "medium"
    assert items[0]["position_budget_pct"] == 0.03
    assert items[0]["previous_rank"] is None
    assert any("尚无足够历史快照" in reason for reason in items[0]["reasons"])
    assert items[0]["catalyst_tags"] == ["多日资金持续流入", "MA对齐扩散增强", "龙头质量突出"]
    assert items[0]["dedupe_key"] == "2026-04-20:低空经济/eVTOL:probe"


def test_order_guard_buy_uses_incremental_exposure():
    settings = Settings(max_total_exposure=0.8, max_single_position=0.3)
    guard = OrderGuard(settings, FakeConnector(total_asset=100000.0, price=10.0))
    positions = [
        Position(
            ticker="300308.SZ",
            name="中际旭创",
            quantity=1000,
            cost_price=9.0,
            last_price=10.0,
            pnl_pct=11.11,
            market_value=10000.0,
            position_pct=0.1,
            mapped_group="AI算力",
            mapped_mainline="AI算力/CPO共封装",
            is_core=True,
        ),
        Position(
            ticker="600000.SH",
            name="浦发银行",
            quantity=2000,
            cost_price=10.0,
            last_price=10.0,
            pnl_pct=0.0,
            market_value=50000.0,
            position_pct=0.5,
        ),
    ]

    draft = guard.prepare(
        ticker="300308.SZ",
        action="buy",
        target_position_pct=0.2,
        account_id="acct-001",
        positions=positions,
    )

    assert draft.order_volume == 1000
    assert draft.estimated_exposure == 0.7
    assert draft.passed_checks is True


def test_order_guard_sell_uses_current_position_reduction():
    settings = Settings(max_total_exposure=0.8, max_single_position=0.3)
    guard = OrderGuard(settings, FakeConnector(total_asset=100000.0, price=10.0))
    positions = [
        Position(
            ticker="300308.SZ",
            name="中际旭创",
            quantity=1500,
            cost_price=9.0,
            last_price=10.0,
            pnl_pct=11.11,
            market_value=15000.0,
            position_pct=0.15,
            mapped_group="AI算力",
            mapped_mainline="AI算力/CPO共封装",
            is_core=True,
        ),
        Position(
            ticker="600000.SH",
            name="浦发银行",
            quantity=2000,
            cost_price=10.0,
            last_price=10.0,
            pnl_pct=0.0,
            market_value=45000.0,
            position_pct=0.45,
        ),
    ]

    draft = guard.prepare(
        ticker="300308.SZ",
        action="sell",
        target_position_pct=0.05,
        account_id="acct-001",
        positions=positions,
    )

    assert draft.order_volume == 1000
    assert draft.estimated_exposure == 0.5
    assert draft.passed_checks is True


def test_qmt_connector_uses_discovery_cache_without_scanning(tmp_path, monkeypatch):
    site_packages = tmp_path / "QMT" / "bin.x64" / "Lib" / "site-packages"
    userdata = tmp_path / "QMT" / "userdata_mini"
    users = userdata / "users" / "test_account_01"
    site_packages.mkdir(parents=True)
    userdata.mkdir(parents=True)
    users.mkdir(parents=True)

    settings = Settings(
        data_dir=tmp_path / "runtime-data",
        qmt_discovery_cache_path=tmp_path / "runtime-data" / "qmt_discovery_cache.json",
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.qmt_discovery_cache_path.parent.mkdir(parents=True, exist_ok=True)
    settings.qmt_discovery_cache_path.write_text(
        (
            "{\n"
            f'  "site_packages": "{site_packages.as_posix()}",\n'
            f'  "userdata_path": "{userdata.as_posix()}",\n'
            '  "account_id": "test_account_01"\n'
            "}\n"
        ),
        encoding="utf-8",
    )

    connector = QmtConnector(settings)
    monkeypatch.setattr(connector, "_scan_files", lambda filename: (_ for _ in ()).throw(AssertionError("scan should not run")))

    assert connector.get_userdata_path() == userdata.resolve()
    assert connector.get_account_id() == "test_account_01"
    assert connector._discover_site_packages() == site_packages.resolve()


def test_qmt_connector_get_market_bars_passes_expected_xtdata_parameters(tmp_path):
    settings = Settings(data_dir=tmp_path / "runtime-data")
    connector = QmtConnector(settings)

    class FakeXtData:
        def __init__(self) -> None:
            self.calls = []

        def get_market_data_ex(self, field_list, stock_list, period, start_time, end_time, count, dividend_type, fill_data):
            self.calls.append(
                {
                    "field_list": field_list,
                    "stock_list": stock_list,
                    "period": period,
                    "start_time": start_time,
                    "end_time": end_time,
                    "count": count,
                    "dividend_type": dividend_type,
                    "fill_data": fill_data,
                }
            )
            return {"000001.SZ": [{"close": 10.0, "volume": 1000}]}

    fake_xtdata = FakeXtData()
    connector._xtdata = fake_xtdata
    connector._modules_loaded = True

    result = connector.get_market_bars(
        ["000001.SZ"],
        period="1d",
        count=20,
        fields=["close", "volume"],
        dividend_type="front",
        fill_data=False,
    )

    assert result == {"000001.SZ": [{"close": 10.0, "volume": 1000}]}
    assert fake_xtdata.calls == [
        {
            "field_list": ["close", "volume"],
            "stock_list": ["000001.SZ"],
            "period": "1d",
            "start_time": "",
            "end_time": "",
            "count": 20,
            "dividend_type": "front",
            "fill_data": False,
        }
    ]



def test_qmt_connector_download_history_data_uses_single_ticker_supply_history_api(tmp_path):
    settings = Settings(data_dir=tmp_path / "runtime-data")
    connector = QmtConnector(settings)

    class FakeXtData:
        def __init__(self) -> None:
            self.calls = []

        def download_data(self, stock_list, period):
            if isinstance(stock_list, list):
                raise TypeError("list input not supported")
            self.calls.append((stock_list, period, "", ""))
            return True

    fake_xtdata = FakeXtData()
    connector._xtdata = fake_xtdata
    connector._modules_loaded = True

    result = connector.download_history_data(["300308.SZ", "002281.SZ"], period="5m", count=96)

    assert result["requested"] == 2
    assert result["period"] == "5m"
    assert fake_xtdata.calls == [
        ("300308.SZ", "5m", "", ""),
        ("002281.SZ", "5m", "", ""),
    ]



def test_qmt_connector_convenience_bar_methods_return_empty_for_empty_code_list(tmp_path):
    settings = Settings(data_dir=tmp_path / "runtime-data")
    connector = QmtConnector(settings)

    assert connector.get_market_bars([], period="1d") == {}
    assert connector.get_daily_bars([], count=30) == {}
    assert connector.get_minute_bars([], period="5m", count=12) == {}



def test_trend_feature_extractor_identifies_startup_style_structure():
    daily_bars = [
        {"close": 10.00, "high": 10.10, "low": 9.90, "volume": 1000},
        {"close": 9.95, "high": 10.05, "low": 9.85, "volume": 980},
        {"close": 10.05, "high": 10.15, "low": 9.95, "volume": 970},
        {"close": 10.00, "high": 10.10, "low": 9.90, "volume": 960},
        {"close": 10.10, "high": 10.20, "low": 9.98, "volume": 950},
        {"close": 10.15, "high": 10.25, "low": 10.05, "volume": 940},
        {"close": 10.20, "high": 10.28, "low": 10.10, "volume": 930},
        {"close": 10.18, "high": 10.26, "low": 10.08, "volume": 920},
        {"close": 10.22, "high": 10.30, "low": 10.12, "volume": 915},
        {"close": 10.25, "high": 10.33, "low": 10.14, "volume": 910},
        {"close": 10.28, "high": 10.35, "low": 10.17, "volume": 905},
        {"close": 10.30, "high": 10.36, "low": 10.18, "volume": 900},
        {"close": 10.32, "high": 10.38, "low": 10.20, "volume": 895},
        {"close": 10.35, "high": 10.41, "low": 10.22, "volume": 890},
        {"close": 10.38, "high": 10.45, "low": 10.25, "volume": 885},
        {"close": 10.40, "high": 10.48, "low": 10.27, "volume": 880},
        {"close": 10.42, "high": 10.49, "low": 10.29, "volume": 875},
        {"close": 10.45, "high": 10.52, "low": 10.31, "volume": 870},
        {"close": 10.50, "high": 10.56, "low": 10.36, "volume": 860},
        {"close": 10.88, "high": 10.92, "low": 10.47, "volume": 1400},
    ]
    minute_bars = [
        {"open": 10.55, "high": 10.70, "low": 10.50, "close": 10.68, "volume": 200},
        {"open": 10.68, "high": 10.82, "low": 10.64, "close": 10.80, "volume": 260},
        {"open": 10.80, "high": 10.92, "low": 10.78, "close": 10.88, "volume": 320},
    ]

    features = TrendFeatureExtractor().extract(daily_bars, minute_bars)

    assert features["above_ma20"] is True
    assert features["ma20_slope_5d"] > 0
    assert features["distance_to_ma20_pct"] > 0
    assert features["platform_breakout_10d"] is True
    assert features["platform_breakout_20d"] is True
    assert features["contraction_then_breakout"] is True
    assert features["return_3d"] > features["return_10d"] / 3
    assert features["close_near_intraday_high"] is True
    assert features["intraday_pullback_from_high_pct"] < 1.0



def test_breadth_feature_extractor_measures_diffusion_and_crowding_without_level2():
    members = [
        {"ticker": "A", "pct_change": 6.2, "above_ma5": True, "above_ma10": True, "above_ma20": True, "hit_limit_up": False, "broken_limit_up": False},
        {"ticker": "B", "pct_change": 4.8, "above_ma5": True, "above_ma10": True, "above_ma20": True, "hit_limit_up": False, "broken_limit_up": False},
        {"ticker": "C", "pct_change": 2.1, "above_ma5": True, "above_ma10": True, "above_ma20": False, "hit_limit_up": False, "broken_limit_up": False},
        {"ticker": "D", "pct_change": 1.5, "above_ma5": True, "above_ma10": False, "above_ma20": False, "hit_limit_up": False, "broken_limit_up": True},
        {"ticker": "E", "pct_change": -0.6, "above_ma5": False, "above_ma10": False, "above_ma20": False, "hit_limit_up": False, "broken_limit_up": False},
    ]
    minute_breadth = [
        {"ts": "10:00", "advancers_ratio": 0.32},
        {"ts": "10:05", "advancers_ratio": 0.45},
        {"ts": "10:10", "advancers_ratio": 0.58},
    ]

    features = BreadthFeatureExtractor().extract(members, minute_breadth)

    assert features["advancers_ratio"] == 0.8
    assert features["ma5_above_ratio"] == 0.8
    assert features["ma10_above_ratio"] == 0.6
    assert features["ma20_above_ratio"] == 0.4
    assert features["median_pct_change"] == 2.1
    assert features["limit_up_count"] == 0
    assert features["limit_break_count"] == 1
    assert features["limit_break_ratio"] == 0.2
    assert features["leader_excess_over_median"] == 4.1
    assert features["leader_second_gap"] == 1.4
    assert features["diffusion_velocity_5m"] == 0.26



def test_trend_stage_engine_classifies_startup_from_repair_and_early_diffusion():
    trend_features = {
        "above_ma20": True,
        "ma20_slope_5d": 1.2,
        "distance_to_ma20_pct": 3.5,
        "platform_breakout_10d": True,
        "platform_breakout_20d": True,
        "contraction_then_breakout": True,
        "return_3d": 5.5,
        "return_5d": 7.8,
        "return_10d": 9.2,
        "close_near_intraday_high": True,
        "intraday_pullback_from_high_pct": 0.4,
    }
    breadth_features = {
        "advancers_ratio": 0.58,
        "ma5_above_ratio": 0.52,
        "ma10_above_ratio": 0.45,
        "ma20_above_ratio": 0.38,
        "median_pct_change": 2.0,
        "limit_up_count": 1,
        "limit_break_count": 0,
        "limit_break_ratio": 0.0,
        "leader_excess_over_median": 3.8,
        "leader_second_gap": 1.2,
        "diffusion_velocity_5m": 0.18,
    }

    result = TrendStageEngine().classify(trend_features, breadth_features)

    assert result["stage"] == "startup"
    assert "站上MA20或平台突破" in result["reasons"]
    assert "板块扩散刚开始但未过热" in result["reasons"]
    assert result["risk_flags"] == []



def test_trend_stage_engine_classifies_markup_when_trend_and_diffusion_are_confirmed():
    trend_features = {
        "above_ma20": True,
        "ma20_slope_5d": 2.8,
        "distance_to_ma20_pct": 7.5,
        "platform_breakout_10d": True,
        "platform_breakout_20d": True,
        "contraction_then_breakout": False,
        "return_3d": 6.0,
        "return_5d": 12.0,
        "return_10d": 18.0,
        "close_near_intraday_high": True,
        "intraday_pullback_from_high_pct": 0.6,
    }
    breadth_features = {
        "advancers_ratio": 0.76,
        "ma5_above_ratio": 0.74,
        "ma10_above_ratio": 0.68,
        "ma20_above_ratio": 0.61,
        "median_pct_change": 3.6,
        "limit_up_count": 3,
        "limit_break_count": 0,
        "limit_break_ratio": 0.0,
        "leader_excess_over_median": 2.8,
        "leader_second_gap": 0.8,
        "diffusion_velocity_5m": 0.14,
    }

    result = TrendStageEngine().classify(trend_features, breadth_features)

    assert result["stage"] == "markup"
    assert "均线趋势和扩散共振" in result["reasons"]
    assert result["risk_flags"] == []



def test_trend_stage_engine_classifies_pullback_when_structure_holds_but_heat_cools():
    trend_features = {
        "above_ma20": True,
        "ma20_slope_5d": 1.0,
        "distance_to_ma20_pct": 1.6,
        "platform_breakout_10d": False,
        "platform_breakout_20d": False,
        "contraction_then_breakout": False,
        "return_3d": -1.2,
        "return_5d": 1.5,
        "return_10d": 8.0,
        "close_near_intraday_high": False,
        "intraday_pullback_from_high_pct": 1.8,
    }
    breadth_features = {
        "advancers_ratio": 0.46,
        "ma5_above_ratio": 0.48,
        "ma10_above_ratio": 0.52,
        "ma20_above_ratio": 0.56,
        "median_pct_change": 0.4,
        "limit_up_count": 0,
        "limit_break_count": 0,
        "limit_break_ratio": 0.0,
        "leader_excess_over_median": 2.0,
        "leader_second_gap": 0.9,
        "diffusion_velocity_5m": -0.04,
    }

    result = TrendStageEngine().classify(trend_features, breadth_features)

    assert result["stage"] == "pullback"
    assert "结构未破但热度回落" in result["reasons"]



def test_trend_stage_engine_classifies_tail_rebound_when_stretched_and_crowded():
    trend_features = {
        "above_ma20": True,
        "ma20_slope_5d": 3.2,
        "distance_to_ma20_pct": 18.5,
        "platform_breakout_10d": True,
        "platform_breakout_20d": True,
        "contraction_then_breakout": False,
        "return_3d": 14.0,
        "return_5d": 24.0,
        "return_10d": 38.0,
        "close_near_intraday_high": False,
        "intraday_pullback_from_high_pct": 5.2,
    }
    breadth_features = {
        "advancers_ratio": 0.92,
        "ma5_above_ratio": 0.90,
        "ma10_above_ratio": 0.86,
        "ma20_above_ratio": 0.82,
        "median_pct_change": 6.5,
        "limit_up_count": 6,
        "limit_break_count": 2,
        "limit_break_ratio": 0.22,
        "leader_excess_over_median": 0.9,
        "leader_second_gap": 0.2,
        "diffusion_velocity_5m": 0.03,
    }

    result = TrendStageEngine().classify(trend_features, breadth_features)

    assert result["stage"] == "tail_rebound"
    assert "价格偏离过大或板块过热" in result["reasons"]
    assert "crowding_risk_high" in result["risk_flags"]



def test_quality_score_engine_scores_startup_high_and_crowding_low_for_early_setup():
    trend_features = {
        "above_ma20": True,
        "ma20_slope_5d": 1.1,
        "distance_to_ma20_pct": 3.2,
        "platform_breakout_10d": True,
        "platform_breakout_20d": True,
        "contraction_then_breakout": True,
        "return_3d": 4.2,
        "return_5d": 6.1,
        "return_10d": 8.8,
        "close_near_intraday_high": True,
        "intraday_pullback_from_high_pct": 0.5,
    }
    breadth_features = {
        "advancers_ratio": 0.55,
        "ma5_above_ratio": 0.54,
        "ma10_above_ratio": 0.49,
        "ma20_above_ratio": 0.41,
        "median_pct_change": 1.8,
        "limit_up_count": 1,
        "limit_break_count": 0,
        "limit_break_ratio": 0.0,
        "leader_excess_over_median": 3.4,
        "leader_second_gap": 1.1,
        "diffusion_velocity_5m": 0.16,
    }

    result = QualityScoreEngine().score(stage="startup", trend_features=trend_features, breadth_features=breadth_features)

    assert result["startup_quality"] >= 80
    assert result["trend_integrity"] >= 70
    assert result["capital_quality"] >= 65
    assert result["crowding_risk"] <= 35
    assert "platform_breakout_20d" in result["contributors"]["startup_quality"]



def test_quality_score_engine_scores_crowding_high_for_tail_rebound():
    trend_features = {
        "above_ma20": True,
        "ma20_slope_5d": 3.0,
        "distance_to_ma20_pct": 16.0,
        "platform_breakout_10d": True,
        "platform_breakout_20d": True,
        "contraction_then_breakout": False,
        "return_3d": 12.0,
        "return_5d": 20.0,
        "return_10d": 33.0,
        "close_near_intraday_high": False,
        "intraday_pullback_from_high_pct": 4.8,
    }
    breadth_features = {
        "advancers_ratio": 0.90,
        "ma5_above_ratio": 0.88,
        "ma10_above_ratio": 0.83,
        "ma20_above_ratio": 0.79,
        "median_pct_change": 5.8,
        "limit_up_count": 5,
        "limit_break_count": 2,
        "limit_break_ratio": 0.18,
        "leader_excess_over_median": 0.7,
        "leader_second_gap": 0.1,
        "diffusion_velocity_5m": 0.02,
    }

    result = QualityScoreEngine().score(stage="tail_rebound", trend_features=trend_features, breadth_features=breadth_features)

    assert result["crowding_risk"] >= 80
    assert result["startup_quality"] <= 35
    assert result["capital_quality"] <= 50
    assert "distance_to_ma20_pct" in result["contributors"]["crowding_risk"]



def test_market_data_service_delegates_candidate_diagnostics_to_extracted_service():
    class FakeConfigRepository:
        def load_mainline_config(self):
            return {"mainlines": []}

    class FakeConnector:
        pass

    service = MarketDataService(FakeConnector(), FakeConfigRepository())

    assert isinstance(service.candidate_diagnostics_service, CandidateDiagnosticsService)



def test_market_data_service_builds_candidate_diagnostics_without_level2():
    class FakeConfigRepository:
        def load_mainline_config(self):
            return {"mainlines": []}

    class FakeConnector:
        def get_daily_bars(self, codes, count=60, fields=None, start_time="", end_time="", dividend_type="none", fill_data=True):
            return {
                codes[0]: [
                    {"close": 10.00, "high": 10.10, "low": 9.90, "volume": 1000},
                    {"close": 9.95, "high": 10.05, "low": 9.85, "volume": 980},
                    {"close": 10.05, "high": 10.15, "low": 9.95, "volume": 970},
                    {"close": 10.00, "high": 10.10, "low": 9.90, "volume": 960},
                    {"close": 10.10, "high": 10.20, "low": 9.98, "volume": 950},
                    {"close": 10.15, "high": 10.25, "low": 10.05, "volume": 940},
                    {"close": 10.20, "high": 10.28, "low": 10.10, "volume": 930},
                    {"close": 10.18, "high": 10.26, "low": 10.08, "volume": 920},
                    {"close": 10.22, "high": 10.30, "low": 10.12, "volume": 915},
                    {"close": 10.25, "high": 10.33, "low": 10.14, "volume": 910},
                    {"close": 10.28, "high": 10.35, "low": 10.17, "volume": 905},
                    {"close": 10.30, "high": 10.36, "low": 10.18, "volume": 900},
                    {"close": 10.32, "high": 10.38, "low": 10.20, "volume": 895},
                    {"close": 10.35, "high": 10.41, "low": 10.22, "volume": 890},
                    {"close": 10.38, "high": 10.45, "low": 10.25, "volume": 885},
                    {"close": 10.40, "high": 10.48, "low": 10.27, "volume": 880},
                    {"close": 10.42, "high": 10.49, "low": 10.29, "volume": 875},
                    {"close": 10.45, "high": 10.52, "low": 10.31, "volume": 870},
                    {"close": 10.50, "high": 10.56, "low": 10.36, "volume": 860},
                    {"close": 10.88, "high": 10.92, "low": 10.47, "volume": 1400},
                ]
            }

        def get_minute_bars(self, codes, period="5m", count=48, fields=None, start_time="", end_time="", dividend_type="none", fill_data=True):
            return {
                codes[0]: [
                    {"open": 10.55, "high": 10.70, "low": 10.50, "close": 10.68, "volume": 200},
                    {"open": 10.68, "high": 10.82, "low": 10.64, "close": 10.80, "volume": 260},
                    {"open": 10.80, "high": 10.92, "low": 10.78, "close": 10.88, "volume": 320},
                ]
            }

    service = MarketDataService(FakeConnector(), FakeConfigRepository())
    service.get_stock_pool = lambda mainline: [
        StockCandidate(ticker="300308.SZ", name="中际旭创", role="leader", score=90, last_price=110.0, pct_change=6.2, amount=2.1e8, sector_hits=3, signal_tags=["breakout"]),
        StockCandidate(ticker="300394.SZ", name="天孚通信", role="core_middle", score=82, last_price=98.0, pct_change=3.8, amount=1.6e8, sector_hits=2, signal_tags=["reclaim"]),
        StockCandidate(ticker="002463.SZ", name="沪电股份", role="followup", score=75, last_price=32.0, pct_change=1.6, amount=1.1e8, sector_hits=2, signal_tags=["pullback"]),
    ]

    diagnostics = service.get_candidate_diagnostics("AI算力/CPO共封装")

    assert diagnostics["trend_diagnosis_version"] == "v1"
    assert diagnostics["mainline_breadth"]["mainline"] == "AI算力/CPO共封装"
    # denominator floor=10, 3 stocks all positive -> 3/10 = 0.3
    assert diagnostics["mainline_breadth"]["stats"]["advancers_ratio"] == 0.3
    assert diagnostics["candidate_diagnostics"][0]["stage"] in {"startup", "markup"}
    assert "300308.SZ" in {item["ticker"] for item in diagnostics["candidate_diagnostics"]}
    assert "startup_quality" in diagnostics["candidate_diagnostics"][0]["quality_scores"]
    assert diagnostics["candidate_diagnostics"][0]["system_attitude"] in {"potential_first", "monitor_closely", "trend_follow_only"}
    assert diagnostics["candidate_diagnostics"][0]["attribution"][0]["metric"] == "stage"



def test_market_data_service_prefers_potential_candidates_over_hottest_top5_when_building_diagnostics():
    class FakeConfigRepository:
        def load_mainline_config(self):
            return {"mainlines": []}

    class FakeConnector:
        def get_daily_bars(self, codes, count=60, fields=None, start_time="", end_time="", dividend_type="none", fill_data=True):
            return {codes[0]: [{"close": 10, "high": 10, "low": 10, "volume": 100}]}

        def get_minute_bars(self, codes, period="5m", count=48, fields=None, start_time="", end_time="", dividend_type="none", fill_data=True):
            return {codes[0]: [{"open": 10, "high": 10, "low": 10, "close": 10, "volume": 100}]}

    service = MarketDataService(FakeConnector(), FakeConfigRepository())
    service.get_stock_pool = lambda mainline: [
        StockCandidate(ticker="000001.SZ", name="热票1", role="leader", score=95, last_price=10.0, pct_change=9.0, amount=3e8, sector_hits=3, signal_tags=["breakout"]),
        StockCandidate(ticker="000002.SZ", name="热票2", role="leader", score=94, last_price=10.0, pct_change=8.0, amount=2.8e8, sector_hits=3, signal_tags=["breakout"]),
        StockCandidate(ticker="000003.SZ", name="热票3", role="core_middle", score=93, last_price=10.0, pct_change=7.0, amount=2.6e8, sector_hits=3, signal_tags=["breakout"]),
        StockCandidate(ticker="000004.SZ", name="热票4", role="core_middle", score=92, last_price=10.0, pct_change=6.0, amount=2.4e8, sector_hits=3, signal_tags=["breakout"]),
        StockCandidate(ticker="000005.SZ", name="热票5", role="followup", score=91, last_price=10.0, pct_change=5.0, amount=2.2e8, sector_hits=3, signal_tags=["breakout"]),
        StockCandidate(ticker="000006.SZ", name="启动票", role="followup", score=76, last_price=10.0, pct_change=2.0, amount=1.2e8, sector_hits=2, signal_tags=["pullback"]),
    ]

    stage_by_ticker = {
        "000001.SZ": "tail_rebound",
        "000002.SZ": "tail_rebound",
        "000003.SZ": "tail_rebound",
        "000004.SZ": "tail_rebound",
        "000005.SZ": "tail_rebound",
        "000006.SZ": "startup",
    }
    current = {"ticker": None}

    def fake_coerce(payload, ticker):
        current["ticker"] = ticker
        return [{"close": 10, "high": 10, "low": 10, "volume": 100}]

    service._coerce_bars = fake_coerce
    service.trend_feature_extractor.extract = lambda daily, minute: {"above_ma20": True, "distance_to_ma20_pct": 3.0}
    service.trend_stage_engine.classify = lambda trend_features, breadth_stats: {
        "stage": stage_by_ticker[current["ticker"]],
        "reasons": [stage_by_ticker[current["ticker"]]],
        "risk_flags": ["crowding_risk_high"] if stage_by_ticker[current["ticker"]] == "tail_rebound" else [],
    }
    service.quality_score_engine.score = lambda stage, trend_features, breadth_features: {
        "startup_quality": 85 if stage == "startup" else 35,
        "trend_integrity": 75,
        "capital_quality": 70 if stage == "startup" else 50,
        "crowding_risk": 20 if stage == "startup" else 90,
    }

    diagnostics = service.get_candidate_diagnostics("AI算力/CPO共封装", limit=5)

    tickers = [item["ticker"] for item in diagnostics["candidate_diagnostics"]]
    assert diagnostics["candidate_diagnostics"][0]["ticker"] == "000006.SZ"
    assert diagnostics["candidate_diagnostics"][0]["stage"] == "startup"
    assert "000006.SZ" in tickers
    assert len(diagnostics["candidate_diagnostics"]) == 5



def test_market_data_service_breadth_uses_full_diagnostic_seed_not_only_top5():
    class FakeConfigRepository:
        def load_mainline_config(self):
            return {"mainlines": []}

    class FakeConnector:
        def get_daily_bars(self, codes, count=60, fields=None, start_time="", end_time="", dividend_type="none", fill_data=True):
            return {codes[0]: [{"close": 10, "high": 10, "low": 10, "volume": 100}]}

        def get_minute_bars(self, codes, period="5m", count=48, fields=None, start_time="", end_time="", dividend_type="none", fill_data=True):
            return {codes[0]: [{"open": 10, "high": 10, "low": 10, "close": 10, "volume": 100}]}

    service = MarketDataService(FakeConnector(), FakeConfigRepository())
    service._diagnostic_seed_candidates = lambda mainline, limit: [
        StockCandidate(ticker="000001.SZ", name="A", role="leader", score=90, last_price=10, pct_change=10, amount=1e8, sector_hits=3, signal_tags=["breakout"]),
        StockCandidate(ticker="000002.SZ", name="B", role="leader", score=90, last_price=10, pct_change=10, amount=1e8, sector_hits=3, signal_tags=["breakout"]),
        StockCandidate(ticker="000003.SZ", name="C", role="leader", score=90, last_price=10, pct_change=10, amount=1e8, sector_hits=3, signal_tags=["breakout"]),
        StockCandidate(ticker="000004.SZ", name="D", role="leader", score=90, last_price=10, pct_change=10, amount=1e8, sector_hits=3, signal_tags=["breakout"]),
        StockCandidate(ticker="000005.SZ", name="E", role="leader", score=90, last_price=10, pct_change=10, amount=1e8, sector_hits=3, signal_tags=["breakout"]),
        StockCandidate(ticker="000006.SZ", name="F", role="followup", score=70, last_price=10, pct_change=-5, amount=1e8, sector_hits=2, signal_tags=["pullback"]),
    ]
    # breadth 也用同样的宽样本，确保测试覆盖 _wider_breadth_pool 路径
    service._wider_breadth_pool = lambda mainline: service._diagnostic_seed_candidates(mainline, 5)
    service._coerce_bars = lambda payload, ticker: [{"close": 10, "high": 10, "low": 10, "volume": 100}]
    service.trend_feature_extractor.extract = lambda daily, minute: {"above_ma20": True, "distance_to_ma20_pct": 3.0}
    service.trend_stage_engine.classify = lambda trend_features, breadth_stats: {"stage": "markup", "reasons": [], "risk_flags": []}
    service.quality_score_engine.score = lambda stage, trend_features, breadth_features: {"startup_quality": 70, "trend_integrity": 70, "capital_quality": 70, "crowding_risk": 30}

    diagnostics = service.get_candidate_diagnostics("AI算力/CPO共封装", limit=5)

    # 6 stocks, denominator floor=10, 5 positive -> 5/10 = 0.5
    assert diagnostics["mainline_breadth"]["stats"]["advancers_ratio"] == 0.5
    assert diagnostics["mainline_breadth"]["stats"]["median_pct_change"] == 7.5



def test_market_data_service_degrades_gracefully_when_bar_fetch_fails():
    class FakeConfigRepository:
        def load_mainline_config(self):
            return {"mainlines": []}

    class FailingConnector:
        def get_daily_bars(self, *args, **kwargs):
            raise ImportError("numpy.core._multiarray_umath missing")

        def get_minute_bars(self, *args, **kwargs):
            raise ImportError("numpy.core._multiarray_umath missing")

    service = MarketDataService(FailingConnector(), FakeConfigRepository())
    service.get_stock_pool = lambda mainline: [
        StockCandidate(ticker="300308.SZ", name="中际旭创", role="leader", score=90, last_price=110.0, pct_change=6.2, amount=2.1e8, sector_hits=3, signal_tags=["breakout"]),
    ]

    diagnostics = service.get_candidate_diagnostics("AI算力/CPO共封装")

    assert diagnostics["trend_diagnosis_version"] == "v1"
    assert diagnostics["mainline_breadth"]["mainline"] == "AI算力/CPO共封装"
    assert diagnostics["candidate_diagnostics"] == []
    assert diagnostics["diagnostics_error"] == "numpy.core._multiarray_umath missing"



def test_qmt_connector_preloads_runtime_numpy_before_importing_xtquant(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / "runtime-data")
    connector = QmtConnector(settings)

    import_calls = []

    monkeypatch.setattr(connector, "_discover_site_packages", lambda: tmp_path / "qmt-site-packages")

    original_import_module = __import__("importlib").import_module

    def fake_import_module(name):
        import_calls.append(name)
        if name in {"numpy", "pytz", "pandas"}:
            return object()
        if name == "xtquant.xtdata":
            return object()
        if name == "xtquant.xtconstant":
            return object()
        if name == "xtquant.xttrader":
            return SimpleNamespace(XtQuantTrader=object())
        if name == "xtquant.xttype":
            return SimpleNamespace(StockAccount=object())
        return original_import_module(name)

    monkeypatch.setattr(__import__("importlib"), "import_module", fake_import_module)

    connector._ensure_modules_loaded()

    assert import_calls[:7] == [
        "numpy",
        "pytz",
        "pandas",
        "xtquant.xtdata",
        "xtquant.xtconstant",
        "xtquant.xttrader",
        "xtquant.xttype",
    ]



def test_market_data_service_coerces_dataframe_like_bar_payloads():
    class FakeFrame:
        def __init__(self, rows):
            self._rows = rows

        @property
        def empty(self):
            return not self._rows

        def to_dict(self, orient="records"):
            assert orient == "records"
            return list(self._rows)

    payload = {
        "300308.SZ": FakeFrame([
            {"open": 10.1, "high": 10.5, "low": 10.0, "close": 10.4, "volume": 1200},
            {"open": 10.4, "high": 10.8, "low": 10.3, "close": 10.7, "volume": 1500},
        ])
    }

    bars = MarketDataService._coerce_bars(payload, "300308.SZ")

    assert bars == [
        {"open": 10.1, "high": 10.5, "low": 10.0, "close": 10.4, "volume": 1200},
        {"open": 10.4, "high": 10.8, "low": 10.3, "close": 10.7, "volume": 1500},
    ]



def test_market_data_service_skips_candidates_when_history_is_empty():
    class FakeConfigRepository:
        def load_mainline_config(self):
            return {"mainlines": []}

    class EmptyBarsConnector:
        def get_daily_bars(self, *args, **kwargs):
            return {"300308.SZ": []}

        def get_minute_bars(self, *args, **kwargs):
            return {"300308.SZ": []}

    service = MarketDataService(EmptyBarsConnector(), FakeConfigRepository())
    service.get_stock_pool = lambda mainline: [
        StockCandidate(ticker="300308.SZ", name="中际旭创", role="leader", score=90, last_price=110.0, pct_change=6.2, amount=2.1e8, sector_hits=3, signal_tags=["breakout"]),
    ]

    diagnostics = service.get_candidate_diagnostics("AI算力/CPO共封装")

    assert diagnostics["candidate_diagnostics"] == []
    assert diagnostics["diagnostics_error"] == "missing historical bars for 300308.SZ"



def test_market_data_service_reports_candidate_readiness_when_history_missing():
    class FakeConfigRepository:
        def load_mainline_config(self):
            return {"mainlines": []}

    class EmptyBarsConnector:
        def get_daily_bars(self, *args, **kwargs):
            return {"300308.SZ": []}

        def get_minute_bars(self, *args, **kwargs):
            return {"300308.SZ": []}

    service = MarketDataService(EmptyBarsConnector(), FakeConfigRepository())
    service.get_stock_pool = lambda mainline: [
        StockCandidate(ticker="300308.SZ", name="中际旭创", role="leader", score=90, last_price=110.0, pct_change=6.2, amount=2.1e8, sector_hits=3, signal_tags=["breakout"]),
    ]

    diagnostics = service.get_candidate_diagnostics("AI算力/CPO共封装")

    assert diagnostics["candidate_diagnostics"] == []
    assert diagnostics["diagnostics_error"] == "missing historical bars for 300308.SZ"
    assert diagnostics["candidate_readiness"][0] == {
        "ticker": "300308.SZ",
        "name": "中际旭创",
        "ready": False,
        "missing": ["1d", "5m"],
        "reason": "missing historical bars for 300308.SZ",
    }



def test_market_data_service_delegates_mainline_scoring_to_extracted_service():
    class FakeConfigRepository:
        def load_mainline_config(self):
            return {"mainlines": []}

    class FakeConnector:
        pass

    service = MarketDataService(FakeConnector(), FakeConfigRepository())

    assert isinstance(service.mainline_scoring_service, MainlineScoringService)



def test_market_data_service_prewarm_populates_mainline_score_cache(monkeypatch):
    class FakeConfigRepository:
        def load_mainline_config(self):
            return {"mainlines": []}

    class FakeConnector:
        pass

    service = MarketDataService(FakeConnector(), FakeConfigRepository())
    monkeypatch.setattr(service, "_mainline_scores_cache_enabled", lambda: True)
    sample_scores = [make_score("AI算力/算力芯片", 23.6)]
    calls = {"count": 0}

    def fake_compute():
        calls["count"] += 1
        return sample_scores

    monkeypatch.setattr(service, "_compute_mainline_scores_uncached", fake_compute)

    warmed = service.prewarm_mainline_scores()
    meta = service.get_mainline_scores_cache_meta()
    cached_scores = service.get_mainline_scores()

    assert warmed == 1
    assert calls["count"] == 1
    assert meta["has_cache"] is True
    assert meta["ttl_seconds"] == 30
    assert cached_scores[0].name == "AI算力/算力芯片"



def test_market_data_service_delegates_stock_pool_to_extracted_builder():
    class FakeConfigRepository:
        def load_mainline_config(self):
            return {"mainlines": []}

    class FakeConnector:
        pass

    service = MarketDataService(FakeConnector(), FakeConfigRepository())

    assert isinstance(service.stock_pool_builder, StockPoolBuilder)



def test_market_data_service_allows_zero_amount_preopen_snapshots_when_price_is_valid():
    class FakeConfigRepository:
        def load_mainline_config(self):
            return {
                "filters": {
                    "allowed_markets": ["SH", "SZ"],
                    "exclude_name_prefixes": [],
                    "exclude_sector_prefixes": [],
                    "exclude_sector_keywords": [],
                    "min_snapshot_amount": 30000000,
                    "min_price": 2.5,
                },
                "mainlines": [],
            }

    class FakeConnector:
        def get_full_tick(self, codes):
            return {
                "600183.SH": {
                    "lastPrice": 72.54,
                    "lastClose": 72.55,
                    "amount": 0,
                    "volume": 0,
                }
            }

        def get_instrument_detail(self, stock_code):
            return {"InstrumentName": "生益科技", "InstrumentStatus": 0}

    service = MarketDataService(FakeConnector(), FakeConfigRepository())
    rows = service._build_stock_metrics({"600183.SH": 2})

    assert len(rows) == 1
    assert rows[0]["ticker"] == "600183.SH"
    assert rows[0]["amount"] == 0.0



def test_market_data_service_delegates_backfill_history_to_extracted_service():
    class FakeConfigRepository:
        def load_mainline_config(self):
            return {"mainlines": []}

    class FakeConnector:
        pass

    service = MarketDataService(FakeConnector(), FakeConfigRepository())

    assert isinstance(service.history_backfill_service, HistoryBackfillService)



def test_market_data_service_backfills_candidate_history_for_requested_periods():
    class FakeConfigRepository:
        def load_mainline_config(self):
            return {"mainlines": []}

    class FakeConnector:
        def __init__(self):
            self.download_calls = []

        def get_daily_bars(self, codes, count=60, **kwargs):
            ticker = codes[0]
            return {ticker: []}

        def get_minute_bars(self, codes, period="5m", count=48, **kwargs):
            ticker = codes[0]
            if period == "15m":
                return {ticker: [{"open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]}
            return {ticker: []}

        def download_history_data(self, codes, period, count):
            self.download_calls.append((tuple(codes), period, count))
            return {"requested": len(codes), "period": period, "count": count}

    service = MarketDataService(FakeConnector(), FakeConfigRepository())
    service.get_stock_pool = lambda mainline: [
        StockCandidate(ticker="300308.SZ", name="中际旭创", role="leader", score=90, last_price=110.0, pct_change=6.2, amount=2.1e8, sector_hits=3, signal_tags=["breakout"]),
        StockCandidate(ticker="002281.SZ", name="光迅科技", role="core_middle", score=82, last_price=50.0, pct_change=3.1, amount=1.3e8, sector_hits=2, signal_tags=["reclaim"]),
    ]

    result = service.backfill_candidate_history("AI算力/CPO共封装", limit=2, periods=["1d", "5m", "15m"])

    assert result["mainline"] == "AI算力/CPO共封装"
    assert result["requested_periods"] == ["1d", "5m", "15m"]
    assert result["downloaded_periods"] == {"1d": 2, "5m": 2, "15m": 0}
    assert result["items"][0]["missing_before"] == ["1d", "5m"]
    assert result["items"][0]["downloaded"] == ["1d", "5m"]
    assert result["items"][0]["skipped"] == ["15m"]
    assert result["items"][0]["errors"] == {}
    assert service.connector.download_calls == [
        (("300308.SZ",), "1d", 60),
        (("300308.SZ",), "5m", 96),
        (("002281.SZ",), "1d", 60),
        (("002281.SZ",), "5m", 96),
    ]



def test_market_data_service_backfill_history_reports_download_errors_without_raising():
    class FakeConfigRepository:
        def load_mainline_config(self):
            return {"mainlines": []}

    class FakeConnector:
        def get_daily_bars(self, codes, count=60, **kwargs):
            return {codes[0]: []}

        def get_minute_bars(self, codes, period="5m", count=48, **kwargs):
            return {codes[0]: []}

        def download_history_data(self, codes, period, count):
            raise RuntimeError(f"download failed for {period}")

    service = MarketDataService(FakeConnector(), FakeConfigRepository())
    service.get_stock_pool = lambda mainline: [
        StockCandidate(ticker="300308.SZ", name="中际旭创", role="leader", score=90, last_price=110.0, pct_change=6.2, amount=2.1e8, sector_hits=3, signal_tags=["breakout"]),
    ]

    result = service.backfill_candidate_history("AI算力/CPO共封装", periods=["1d", "5m"])

    assert result["downloaded_periods"] == {"1d": 0, "5m": 0}
    assert result["items"][0]["errors"] == {"1d": "download failed for 1d", "5m": "download failed for 5m"}
    assert result["items"][0]["downloaded"] == []



def test_execution_service_persists_drafts_and_result_mapping(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "runtime-data",
        execution_state_path=tmp_path / "runtime-data" / "execution_state.json",
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.execution_state_path.parent.mkdir(parents=True, exist_ok=True)
    connector = SimpleNamespace()
    draft = OrderDraft(
        draft_id="draft-persist-001",
        ticker="300308.SZ",
        action="buy",
        account_id="acct-001",
        target_position_pct=0.2,
        order_volume=100,
        reference_price=110.0,
        passed_checks=True,
        estimated_exposure=0.2,
        risk_notes=["Passed the basic exposure and pricing checks."],
    )

    service = ExecutionService(connector, settings)
    service.remember_draft(draft)
    result = service.confirm(draft, user_confirmed=True)

    reloaded = ExecutionService(connector, settings)

    assert result.status == "advisory_only"
    assert reloaded.get_draft("draft-persist-001") is not None
    assert reloaded.get_status("advisory:draft-persist-001").draft_id == "draft-persist-001"

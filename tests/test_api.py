from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api import routes
from app.main import app
from app.models.domain import BuySignal, MainlineScore, OrderDraft, OrderResult, Position, RiskAlert, SellSignal, StockCandidate


client = TestClient(app)


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_app_uses_lifespan_instead_of_deprecated_on_event():
    assert app.router.on_startup == []


def test_diagnose_full_contract(monkeypatch):
    scores = [
        MainlineScore(
            name="AI算力/CPO共封装",
            group="AI算力",
            industry_logic_score=4.2,
            capital_strength_score=4.1,
            leader_score=4.8,
            core_score=4.0,
            diffusion_score=3.9,
            persistence_score=3.6,
            market_status_score=4.4,
            total_score=29.0,
            tier="core",
        )
    ]
    positions = [
        Position(
            ticker="300308.SZ",
            name="中际旭创",
            quantity=100,
            cost_price=100.0,
            last_price=110.0,
            pnl_pct=10.0,
            market_value=11000.0,
            position_pct=0.11,
            mapped_group="AI算力",
            mapped_mainline="AI算力/CPO共封装",
            is_core=True,
        )
    ]
    alerts = [RiskAlert(level="info", title="Risk state is stable", detail="Mainline leadership remains intact.")]
    context = {
        "scores": scores,
        "active_groups": ["AI算力"],
        "pools_by_branch": {"AI算力/CPO共封装": []},
        "core_tickers_by_group": {"AI算力": {"300308.SZ"}},
        "positions": positions,
        "alerts": alerts,
        "exposure": {
            "total_exposure": 0.11,
            "mainline_exposure": 0.11,
            "non_mainline_exposure": 0.0,
            "single_name_concentration": 0.11,
            "risk_level": "low",
        },
    }
    buy_signals = [
        BuySignal(
            ticker="300308.SZ",
            signal_type="breakout",
            entry_price=110.0,
            suggested_position_pct=0.12,
            stop_loss="Lose the breakout low.",
            risk_level="low",
        )
    ]
    sell_signals = [SellSignal(ticker="300308.SZ", reason="None", action="trim", trend_status="PnL 10.00%")]
    emerging_items = [
        {
            "name": "AI算力/CPO共封装",
            "group": "AI算力",
            "stage": "early_watch",
            "suggestion": "probe",
            "action_plan": "probe_small",
            "confidence": "high",
            "position_budget_pct": 0.05,
            "early_score": 78.0,
            "current_rank": 1,
            "previous_rank": 7,
            "rank_change": 6,
            "current_score": 29.0,
            "previous_score": 26.5,
            "score_change": 2.5,
            "avoid_chase": False,
            "catalyst_tags": ["资金先行", "扩散增强"],
            "reasons": ["短周期评分提升 2.50 分。"],
            "leaders": [],
            "dedupe_key": "2026-04-20:AI算力/CPO共封装:probe",
        }
    ]

    monkeypatch.setattr(routes, "build_market_context", lambda: context)
    monkeypatch.setattr(routes.ranker, "market_phase", lambda current_scores: "主升")
    monkeypatch.setattr(
        routes.market_data_service,
        "get_stock_pool",
        lambda mainline: [StockCandidate(ticker="300308.SZ", name="中际旭创", role="leader", score=90, last_price=110.0)],
    )
    monkeypatch.setattr(routes.signal_engine, "build_buy_watchlist", lambda stocks, signal_type: buy_signals)
    monkeypatch.setattr(routes.signal_engine, "build_sell_watchlist", lambda positions, active_groups, core_tickers_by_group: sell_signals)
    monkeypatch.setattr(routes.market_data_service, "get_candidate_diagnostics", lambda mainline, limit=5: {
        "trend_diagnosis_version": "v1",
        "mainline_breadth": {
            "mainline": mainline,
            "stats": {
                "advancers_ratio": 0.8,
                "ma5_above_ratio": 0.7,
                "ma10_above_ratio": 0.6,
                "ma20_above_ratio": 0.5,
                "median_pct_change": 2.6,
                "limit_up_count": 1,
                "limit_break_count": 0,
                "limit_break_ratio": 0.0,
                "leader_excess_over_median": 3.1,
                "leader_second_gap": 1.0,
                "diffusion_velocity_5m": 0.15,
            },
        },
        "candidate_diagnostics": [
            {
                "ticker": "300308.SZ",
                "name": "中际旭创",
                "role": "leader",
                "score": 90.0,
                "stage": "startup",
                "stage_reasons": ["站上MA20或平台突破", "板块扩散刚开始但未过热"],
                "quality_scores": {
                    "startup_quality": 86,
                    "trend_integrity": 79,
                    "capital_quality": 74,
                    "crowding_risk": 22,
                },
                "risk_flags": [],
                "system_attitude": "potential_first",
                "attribution": [
                    {"metric": "stage", "value": "startup", "conclusion": "结构修复+扩散初升"},
                    {"metric": "distance_to_ma20_pct", "value": 3.2, "conclusion": "偏离不大"},
                ],
            }
        ],
        "diagnostics_error": "numpy.core._multiarray_umath missing",
        "candidate_readiness": [
            {
                "ticker": "300308.SZ",
                "name": "中际旭创",
                "ready": False,
                "missing": ["1d", "5m"],
                "reason": "missing historical bars for 300308.SZ",
            }
        ],
    })
    monkeypatch.setattr(routes.portfolio_inspector, "inspect", lambda positions, active_groups, core_tickers_by_group: (positions, 88.0))
    monkeypatch.setattr(routes.emerging_detector, "detect", lambda scores, lookback_minutes=30, limit=5: emerging_items)
    monkeypatch.setattr(routes.emerging_detector, "record_snapshot", lambda scores: None)

    response = client.get("/api/v1/diagnose/full")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["data"]["top_mainline"] == {"name": "AI算力/CPO共封装", "score": 29.0, "group": "AI算力"}
    assert body["data"]["rankings"][0]["tier"] == "core"
    assert body["data"]["buy_signals"][0]["signal_type"] == "breakout"
    assert body["data"]["portfolio_health"]["health_score"] == 88.0
    assert body["data"]["emerging_mainlines"][0]["action_plan"] == "probe_small"
    assert body["data"]["emerging_mainlines"][0]["dedupe_key"] == "2026-04-20:AI算力/CPO共封装:probe"
    assert body["data"]["emerging_mainlines"][0]["leaders"] == ["中际旭创(300308.SZ)"]
    assert body["data"]["polling_policy_hint"] == {
        "immediate_push_recommended": True,
        "summary_push_recommended": False,
        "trigger_reasons": [
            "emerging suggestion is probe",
            "mainline enters top 5",
            "mainline score changes by at least 2.0",
        ],
        "dedupe_keys": ["2026-04-20:AI算力/CPO共封装:probe"],
    }
    assert body["data"]["market_story"] == {
        "headline": "AI算力/CPO共封装出现升温信号，可小仓试探，但不宜把确认信号当成早期潜伏信号追高。",
        "strongest_emerging_name": "AI算力/CPO共封装",
        "strongest_emerging_stage": "early_watch",
        "strongest_emerging_reason": "短周期评分提升 2.50 分。",
        "caution_notes": [],
    }
    assert body["data"]["trend_diagnosis_version"] == "v1"
    assert body["data"]["mainline_breadth"]["mainline"] == "AI算力/CPO共封装"
    assert body["data"]["candidate_diagnostics"][0]["stage"] == "startup"
    assert body["data"]["candidate_diagnostics"][0]["quality_scores"]["startup_quality"] == 86
    assert body["data"]["candidate_diagnostics"][0]["system_attitude"] == "potential_first"
    assert body["data"]["diagnostics_error"] == "numpy.core._multiarray_umath missing"
    assert body["data"]["candidate_readiness"][0]["ready"] is False
    assert body["data"]["candidate_readiness"][0]["missing"] == ["1d", "5m"]


def test_diagnose_full_returns_503_when_no_mainline_data(monkeypatch):
    monkeypatch.setattr(routes.market_data_service, "get_mainline_scores", lambda: [])

    response = client.get("/api/v1/diagnose/full")

    assert response.status_code == 503
    assert response.json()["detail"] == "No mainline data available from xtdata"


def test_diagnose_backfill_history(monkeypatch):
    monkeypatch.setattr(routes, "build_market_context", lambda: {
        "scores": [MainlineScore(name="AI算力/CPO共封装", group="AI算力", industry_logic_score=4, capital_strength_score=4, leader_score=4, core_score=4, diffusion_score=4, persistence_score=4, market_status_score=4, total_score=28.0, tier="core")],
        "active_groups": ["AI算力"],
        "pools_by_branch": {},
        "core_tickers_by_group": {},
        "positions": [],
        "alerts": [],
        "exposure": {"total_exposure_pct": 0.2, "cash_pct": 0.8, "mainline_exposure_pct": 0.2, "non_mainline_exposure_pct": 0.0},
    })
    monkeypatch.setattr(routes.market_data_service, "backfill_candidate_history", lambda mainline, limit=5, periods=None: {
        "mainline": mainline,
        "requested_periods": periods or ["1d", "5m", "15m"],
        "downloaded_periods": {"1d": 2, "5m": 2, "15m": 1},
        "items": [{"ticker": "300308.SZ", "name": "中际旭创", "missing_before": ["1d", "5m"], "downloaded": ["1d", "5m"], "skipped": ["15m"]}],
    })

    response = client.post("/api/v1/diagnose/backfill-history", json={"mainline": "AI算力/CPO共封装", "limit": 5, "periods": ["1d", "5m", "15m"]})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["data"]["mainline"] == "AI算力/CPO共封装"
    assert body["data"]["downloaded_periods"] == {"1d": 2, "5m": 2, "15m": 1}
    assert body["data"]["items"][0]["downloaded"] == ["1d", "5m"]



def test_diagnose_full_falls_back_to_emerging_branch_when_top_mainline_is_all_tail(monkeypatch):
    context = {
        "scores": [
            MainlineScore(
                name="AI算力/CPO共封装",
                group="AI算力",
                industry_logic_score=4.0,
                capital_strength_score=4.2,
                leader_score=4.9,
                core_score=4.0,
                diffusion_score=4.8,
                persistence_score=4.6,
                market_status_score=4.7,
                total_score=31.2,
                tier="core",
            ),
            MainlineScore(
                name="AI算力/液冷服务器",
                group="AI算力",
                industry_logic_score=3.7,
                capital_strength_score=3.9,
                leader_score=3.8,
                core_score=3.6,
                diffusion_score=2.4,
                persistence_score=1.8,
                market_status_score=2.8,
                total_score=25.0,
                tier="secondary",
            ),
        ],
        "active_groups": ["AI算力"],
        "pools_by_branch": {},
        "core_tickers_by_group": {},
        "positions": [],
        "alerts": [],
        "exposure": {"total_exposure": 0.2, "mainline_exposure": 0.2, "non_mainline_exposure": 0.0, "single_name_concentration": 0.0, "risk_level": "low"},
    }
    emerging_items = [
        {
            "name": "AI算力/液冷服务器",
            "group": "AI算力",
            "stage": "early_watch",
            "suggestion": "probe",
            "action_plan": "probe_small",
            "confidence": "high",
            "position_budget_pct": 0.12,
            "early_score": 76.0,
            "current_rank": 6,
            "previous_rank": 12,
            "rank_change": 6,
            "current_score": 25.0,
            "previous_score": 21.5,
            "score_change": 3.5,
            "avoid_chase": False,
            "catalyst_tags": ["资金先行"],
            "reasons": ["液冷服务器升温明显"],
            "leaders": [],
            "dedupe_key": "2026-04-22:AI算力/液冷服务器:probe",
        }
    ]
    all_tail = {
        "trend_diagnosis_version": "v1",
        "mainline_breadth": {"mainline": "AI算力/CPO共封装", "stats": {"advancers_ratio": 1.0}},
        "candidate_diagnostics": [
            {
                "ticker": "300857.SZ",
                "name": "协创数据",
                "role": "leader",
                "score": 95.0,
                "stage": "tail_rebound",
                "stage_reasons": ["价格偏离过大或板块过热"],
                "quality_scores": {"startup_quality": 35, "trend_integrity": 100, "capital_quality": 50, "crowding_risk": 90},
                "risk_flags": ["crowding_risk_high"],
                "system_attitude": "high_risk_tail",
                "attribution": [{"metric": "stage", "value": "tail_rebound", "conclusion": "过热"}],
            }
        ],
        "candidate_readiness": [],
    }
    fallback_diag = {
        "trend_diagnosis_version": "v1",
        "mainline_breadth": {"mainline": "AI算力/液冷服务器", "stats": {"advancers_ratio": 0.55}},
        "candidate_diagnostics": [
            {
                "ticker": "000777.SZ",
                "name": "液冷样本",
                "role": "followup",
                "score": 76.0,
                "stage": "startup",
                "stage_reasons": ["站上MA20或平台突破"],
                "quality_scores": {"startup_quality": 84, "trend_integrity": 72, "capital_quality": 69, "crowding_risk": 22},
                "risk_flags": [],
                "system_attitude": "potential_first",
                "attribution": [{"metric": "stage", "value": "startup", "conclusion": "结构修复"}],
            }
        ],
        "candidate_readiness": [],
    }

    monkeypatch.setattr(routes, "build_market_context", lambda: context)
    monkeypatch.setattr(routes.ranker, "market_phase", lambda current_scores: "主升")
    monkeypatch.setattr(routes.signal_engine, "build_buy_watchlist", lambda stocks, signal_type: [])
    monkeypatch.setattr(routes.signal_engine, "build_sell_watchlist", lambda positions, active_groups, core_tickers_by_group: [])
    monkeypatch.setattr(routes.portfolio_inspector, "inspect", lambda positions, active_groups, core_tickers_by_group: (positions, 90.0))
    monkeypatch.setattr(routes.emerging_detector, "detect", lambda scores, lookback_minutes=30, limit=5: emerging_items)
    monkeypatch.setattr(routes.emerging_detector, "record_snapshot", lambda scores: None)
    monkeypatch.setattr(routes.market_data_service, "get_stock_pool", lambda mainline: [StockCandidate(ticker="000001.SZ", name="样本", role="leader", score=90, last_price=10.0)])
    monkeypatch.setattr(
        routes.market_data_service,
        "get_candidate_diagnostics",
        lambda mainline, limit=5: all_tail if mainline == "AI算力/CPO共封装" else fallback_diag,
    )

    response = client.get("/api/v1/diagnose/full")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["diagnostic_fallback_applied"] is True
    assert body["diagnostic_focus_mainline"] == "AI算力/液冷服务器"
    assert body["diagnostic_fallback_reason"] == "top_mainline_all_tail_rebound"
    assert body["candidate_diagnostics"][0]["ticker"] == "000777.SZ"
    assert body["candidate_diagnostics"][0]["stage"] == "startup"



def test_diagnose_full_falls_back_to_cooler_ranked_branch_when_emerging_is_all_hot(monkeypatch):
    context = {
        "scores": [
            MainlineScore(name="AI算力/CPO共封装", group="AI算力", industry_logic_score=4.0, capital_strength_score=4.2, leader_score=4.9, core_score=4.0, diffusion_score=4.8, persistence_score=4.6, market_status_score=4.7, total_score=31.2, tier="core"),
            MainlineScore(name="AI算力/服务器液冷", group="AI算力", industry_logic_score=3.6, capital_strength_score=3.8, leader_score=3.4, core_score=3.4, diffusion_score=3.0, persistence_score=1.2, market_status_score=4.0, total_score=28.5, tier="secondary"),
        ],
        "active_groups": ["AI算力"],
        "pools_by_branch": {},
        "core_tickers_by_group": {},
        "positions": [],
        "alerts": [],
        "exposure": {"total_exposure": 0.2, "mainline_exposure": 0.2, "non_mainline_exposure": 0.0, "single_name_concentration": 0.0, "risk_level": "low"},
    }
    emerging_items = [
        {
            "name": "AI算力/CPO共封装",
            "group": "AI算力",
            "stage": "confirmed_hot",
            "suggestion": "avoid_chase",
            "action_plan": "avoid_chase",
            "confidence": "high",
            "position_budget_pct": 0.0,
            "early_score": 72.0,
            "current_rank": 1,
            "previous_rank": 1,
            "rank_change": 0,
            "current_score": 31.2,
            "previous_score": 30.0,
            "score_change": 1.2,
            "avoid_chase": True,
            "catalyst_tags": ["过热"],
            "reasons": ["已确认过热"],
            "leaders": [],
            "dedupe_key": "2026-04-22:AI算力/CPO共封装:avoid_chase",
        }
    ]
    all_tail = {
        "trend_diagnosis_version": "v1",
        "mainline_breadth": {"mainline": "AI算力/CPO共封装", "stats": {"advancers_ratio": 1.0}},
        "candidate_diagnostics": [{"ticker": "300857.SZ", "name": "协创数据", "role": "leader", "score": 95.0, "stage": "tail_rebound", "stage_reasons": ["价格偏离过大或板块过热"], "quality_scores": {"startup_quality": 35, "trend_integrity": 100, "capital_quality": 50, "crowding_risk": 90}, "risk_flags": ["crowding_risk_high"], "system_attitude": "high_risk_tail", "attribution": [{"metric": "stage", "value": "tail_rebound", "conclusion": "过热"}]}],
        "candidate_readiness": [],
    }
    fallback_diag = {
        "trend_diagnosis_version": "v1",
        "mainline_breadth": {"mainline": "AI算力/服务器液冷", "stats": {"advancers_ratio": 0.55}},
        "candidate_diagnostics": [{"ticker": "000888.SZ", "name": "液冷候选", "role": "followup", "score": 75.0, "stage": "startup", "stage_reasons": ["站上MA20或平台突破"], "quality_scores": {"startup_quality": 82, "trend_integrity": 70, "capital_quality": 66, "crowding_risk": 25}, "risk_flags": [], "system_attitude": "potential_first", "attribution": [{"metric": "stage", "value": "startup", "conclusion": "结构修复"}]}],
        "candidate_readiness": [],
    }

    monkeypatch.setattr(routes, "build_market_context", lambda: context)
    monkeypatch.setattr(routes.ranker, "market_phase", lambda current_scores: "主升")
    monkeypatch.setattr(routes.signal_engine, "build_buy_watchlist", lambda stocks, signal_type: [])
    monkeypatch.setattr(routes.signal_engine, "build_sell_watchlist", lambda positions, active_groups, core_tickers_by_group: [])
    monkeypatch.setattr(routes.portfolio_inspector, "inspect", lambda positions, active_groups, core_tickers_by_group: (positions, 90.0))
    monkeypatch.setattr(routes.emerging_detector, "detect", lambda scores, lookback_minutes=30, limit=5: emerging_items)
    monkeypatch.setattr(routes.emerging_detector, "record_snapshot", lambda scores: None)
    monkeypatch.setattr(routes.market_data_service, "get_stock_pool", lambda mainline: [StockCandidate(ticker="000001.SZ", name="样本", role="leader", score=90, last_price=10.0)])
    monkeypatch.setattr(routes.market_data_service, "get_candidate_diagnostics", lambda mainline, limit=5: all_tail if mainline == "AI算力/CPO共封装" else fallback_diag)

    response = client.get("/api/v1/diagnose/full")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["diagnostic_fallback_applied"] is True
    assert body["diagnostic_focus_mainline"] == "AI算力/服务器液冷"
    assert body["diagnostic_fallback_reason"] == "rankings_same_group_branch_after_all_tail"
    assert body["candidate_diagnostics"][0]["ticker"] == "000888.SZ"



def test_diagnose_full_prefers_same_group_branch_before_unrelated_cooler_branch(monkeypatch):
    context = {
        "scores": [
            MainlineScore(name="AI算力/CPO共封装", group="AI算力", industry_logic_score=4.0, capital_strength_score=4.2, leader_score=4.9, core_score=4.0, diffusion_score=4.8, persistence_score=4.6, market_status_score=4.7, total_score=31.2, tier="core"),
            MainlineScore(name="金融/保险银行", group="金融", industry_logic_score=3.5, capital_strength_score=3.2, leader_score=2.8, core_score=2.9, diffusion_score=1.6, persistence_score=0.8, market_status_score=2.0, total_score=26.0, tier="secondary"),
            MainlineScore(name="AI算力/服务器液冷", group="AI算力", industry_logic_score=3.6, capital_strength_score=3.8, leader_score=3.4, core_score=3.4, diffusion_score=3.0, persistence_score=1.2, market_status_score=4.0, total_score=28.5, tier="secondary"),
        ],
        "active_groups": ["AI算力"],
        "pools_by_branch": {},
        "core_tickers_by_group": {},
        "positions": [],
        "alerts": [],
        "exposure": {"total_exposure": 0.2, "mainline_exposure": 0.2, "non_mainline_exposure": 0.0, "single_name_concentration": 0.0, "risk_level": "low"},
    }
    all_tail = {
        "trend_diagnosis_version": "v1",
        "mainline_breadth": {"mainline": "AI算力/CPO共封装", "stats": {"advancers_ratio": 1.0}},
        "candidate_diagnostics": [{"ticker": "300857.SZ", "name": "协创数据", "role": "leader", "score": 95.0, "stage": "tail_rebound", "stage_reasons": ["价格偏离过大或板块过热"], "quality_scores": {"startup_quality": 35, "trend_integrity": 100, "capital_quality": 50, "crowding_risk": 90}, "risk_flags": ["crowding_risk_high"], "system_attitude": "high_risk_tail", "attribution": [{"metric": "stage", "value": "tail_rebound", "conclusion": "过热"}]}],
        "candidate_readiness": [],
    }
    ai_fallback = {
        "trend_diagnosis_version": "v1",
        "mainline_breadth": {"mainline": "AI算力/服务器液冷", "stats": {"advancers_ratio": 0.55}},
        "candidate_diagnostics": [{"ticker": "000888.SZ", "name": "液冷候选", "role": "followup", "score": 75.0, "stage": "startup", "stage_reasons": ["站上MA20或平台突破"], "quality_scores": {"startup_quality": 82, "trend_integrity": 70, "capital_quality": 66, "crowding_risk": 25}, "risk_flags": [], "system_attitude": "potential_first", "attribution": [{"metric": "stage", "value": "startup", "conclusion": "结构修复"}]}],
        "candidate_readiness": [],
    }
    finance_fallback = {
        "trend_diagnosis_version": "v1",
        "mainline_breadth": {"mainline": "金融/保险银行", "stats": {"advancers_ratio": 0.4}},
        "candidate_diagnostics": [{"ticker": "000001.SZ", "name": "平安银行", "role": "followup", "score": 70.0, "stage": "markup", "stage_reasons": ["均线趋势和扩散共振"], "quality_scores": {"startup_quality": 48, "trend_integrity": 73, "capital_quality": 47, "crowding_risk": 5}, "risk_flags": [], "system_attitude": "trend_follow_only", "attribution": [{"metric": "stage", "value": "markup", "conclusion": "温和趋势"}]}],
        "candidate_readiness": [],
    }

    monkeypatch.setattr(routes, "build_market_context", lambda: context)
    monkeypatch.setattr(routes.ranker, "market_phase", lambda current_scores: "主升")
    monkeypatch.setattr(routes.signal_engine, "build_buy_watchlist", lambda stocks, signal_type: [])
    monkeypatch.setattr(routes.signal_engine, "build_sell_watchlist", lambda positions, active_groups, core_tickers_by_group: [])
    monkeypatch.setattr(routes.portfolio_inspector, "inspect", lambda positions, active_groups, core_tickers_by_group: (positions, 90.0))
    monkeypatch.setattr(routes.emerging_detector, "detect", lambda scores, lookback_minutes=30, limit=5: [])
    monkeypatch.setattr(routes.emerging_detector, "record_snapshot", lambda scores: None)
    monkeypatch.setattr(routes.market_data_service, "get_stock_pool", lambda mainline: [StockCandidate(ticker="000001.SZ", name="样本", role="leader", score=90, last_price=10.0)])
    monkeypatch.setattr(
        routes.market_data_service,
        "get_candidate_diagnostics",
        lambda mainline, limit=5: all_tail if mainline == "AI算力/CPO共封装" else (ai_fallback if mainline == "AI算力/服务器液冷" else finance_fallback),
    )

    response = client.get("/api/v1/diagnose/full")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["diagnostic_fallback_applied"] is True
    assert body["diagnostic_focus_mainline"] == "AI算力/服务器液冷"
    assert body["diagnostic_fallback_reason"] == "rankings_same_group_branch_after_all_tail"
    assert body["candidate_diagnostics"][0]["ticker"] == "000888.SZ"



def test_adjacent_groups_can_be_overridden_from_mainline_config(monkeypatch):
    monkeypatch.setattr(
        routes.config_repository,
        "load_mainline_config",
        lambda: {"fallback": {"adjacent_groups": {"AI算力": ["机器人"]}}},
    )

    assert routes._adjacent_groups("AI算力") == ["机器人"]



def test_diagnose_full_prefers_adjacent_theme_before_marketwide_branch(monkeypatch):
    context = {
        "scores": [
            MainlineScore(name="AI算力/CPO共封装", group="AI算力", industry_logic_score=4.0, capital_strength_score=4.2, leader_score=4.9, core_score=4.0, diffusion_score=4.8, persistence_score=4.6, market_status_score=4.7, total_score=31.2, tier="core"),
            MainlineScore(name="金融/保险银行", group="金融", industry_logic_score=3.5, capital_strength_score=3.2, leader_score=2.8, core_score=2.9, diffusion_score=1.6, persistence_score=0.8, market_status_score=2.0, total_score=26.0, tier="secondary"),
            MainlineScore(name="半导体/先进封装", group="半导体", industry_logic_score=3.8, capital_strength_score=3.7, leader_score=3.4, core_score=3.5, diffusion_score=3.1, persistence_score=1.4, market_status_score=4.2, total_score=29.0, tier="secondary"),
            MainlineScore(name="AI算力/服务器液冷", group="AI算力", industry_logic_score=3.6, capital_strength_score=3.8, leader_score=3.4, core_score=3.4, diffusion_score=3.9, persistence_score=2.6, market_status_score=4.0, total_score=28.5, tier="secondary"),
        ],
        "active_groups": ["AI算力"],
        "pools_by_branch": {},
        "core_tickers_by_group": {},
        "positions": [],
        "alerts": [],
        "exposure": {"total_exposure": 0.2, "mainline_exposure": 0.2, "non_mainline_exposure": 0.0, "single_name_concentration": 0.0, "risk_level": "low"},
    }
    all_tail = {
        "trend_diagnosis_version": "v1",
        "mainline_breadth": {"mainline": "AI算力/CPO共封装", "stats": {"advancers_ratio": 1.0}},
        "candidate_diagnostics": [{"ticker": "300857.SZ", "name": "协创数据", "role": "leader", "score": 95.0, "stage": "tail_rebound", "stage_reasons": ["价格偏离过大或板块过热"], "quality_scores": {"startup_quality": 35, "trend_integrity": 100, "capital_quality": 50, "crowding_risk": 90}, "risk_flags": ["crowding_risk_high"], "system_attitude": "high_risk_tail", "attribution": [{"metric": "stage", "value": "tail_rebound", "conclusion": "过热"}]}],
        "candidate_readiness": [],
    }
    semi_fallback = {
        "trend_diagnosis_version": "v1",
        "mainline_breadth": {"mainline": "半导体/先进封装", "stats": {"advancers_ratio": 0.52}},
        "candidate_diagnostics": [{"ticker": "688000.SH", "name": "封装候选", "role": "followup", "score": 74.0, "stage": "startup", "stage_reasons": ["站上MA20或平台突破"], "quality_scores": {"startup_quality": 83, "trend_integrity": 71, "capital_quality": 65, "crowding_risk": 24}, "risk_flags": [], "system_attitude": "potential_first", "attribution": [{"metric": "stage", "value": "startup", "conclusion": "结构修复"}]}],
        "candidate_readiness": [],
    }
    finance_fallback = {
        "trend_diagnosis_version": "v1",
        "mainline_breadth": {"mainline": "金融/保险银行", "stats": {"advancers_ratio": 0.4}},
        "candidate_diagnostics": [{"ticker": "000001.SZ", "name": "平安银行", "role": "followup", "score": 70.0, "stage": "markup", "stage_reasons": ["均线趋势和扩散共振"], "quality_scores": {"startup_quality": 48, "trend_integrity": 73, "capital_quality": 47, "crowding_risk": 5}, "risk_flags": [], "system_attitude": "trend_follow_only", "attribution": [{"metric": "stage", "value": "markup", "conclusion": "温和趋势"}]}],
        "candidate_readiness": [],
    }

    monkeypatch.setattr(routes, "build_market_context", lambda: context)
    monkeypatch.setattr(routes.ranker, "market_phase", lambda current_scores: "主升")
    monkeypatch.setattr(routes.signal_engine, "build_buy_watchlist", lambda stocks, signal_type: [])
    monkeypatch.setattr(routes.signal_engine, "build_sell_watchlist", lambda positions, active_groups, core_tickers_by_group: [])
    monkeypatch.setattr(routes.portfolio_inspector, "inspect", lambda positions, active_groups, core_tickers_by_group: (positions, 90.0))
    monkeypatch.setattr(routes.emerging_detector, "detect", lambda scores, lookback_minutes=30, limit=5: [])
    monkeypatch.setattr(routes.emerging_detector, "record_snapshot", lambda scores: None)
    monkeypatch.setattr(routes.market_data_service, "get_stock_pool", lambda mainline: [StockCandidate(ticker="000001.SZ", name="样本", role="leader", score=90, last_price=10.0)])
    monkeypatch.setattr(
        routes.market_data_service,
        "get_candidate_diagnostics",
        lambda mainline, limit=5: all_tail if mainline == "AI算力/CPO共封装" else (semi_fallback if mainline == "半导体/先进封装" else finance_fallback),
    )

    response = client.get("/api/v1/diagnose/full")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["diagnostic_focus_mainline"] == "半导体/先进封装"
    assert body["diagnostic_fallback_reason"] == "adjacent_theme_branch_after_all_tail"
    assert body["candidate_diagnostics"][0]["ticker"] == "688000.SH"



def test_diagnose_full_includes_fallback_trace(monkeypatch):
    context = {
        "scores": [
            MainlineScore(name="AI算力/CPO共封装", group="AI算力", industry_logic_score=4.0, capital_strength_score=4.2, leader_score=4.9, core_score=4.0, diffusion_score=4.8, persistence_score=4.6, market_status_score=4.7, total_score=31.2, tier="core"),
            MainlineScore(name="AI算力/服务器液冷", group="AI算力", industry_logic_score=3.6, capital_strength_score=3.8, leader_score=3.4, core_score=3.4, diffusion_score=3.0, persistence_score=1.2, market_status_score=4.0, total_score=28.5, tier="secondary"),
            MainlineScore(name="半导体/先进封装", group="半导体", industry_logic_score=3.8, capital_strength_score=3.7, leader_score=3.4, core_score=3.5, diffusion_score=3.1, persistence_score=1.4, market_status_score=4.2, total_score=29.0, tier="secondary"),
            MainlineScore(name="金融/保险银行", group="金融", industry_logic_score=3.5, capital_strength_score=3.2, leader_score=2.8, core_score=2.9, diffusion_score=1.6, persistence_score=0.8, market_status_score=2.0, total_score=26.0, tier="secondary"),
        ],
        "active_groups": ["AI算力"],
        "pools_by_branch": {},
        "core_tickers_by_group": {},
        "positions": [],
        "alerts": [],
        "exposure": {"total_exposure": 0.2, "mainline_exposure": 0.2, "non_mainline_exposure": 0.0, "single_name_concentration": 0.0, "risk_level": "low"},
    }
    all_tail = {
        "trend_diagnosis_version": "v1",
        "mainline_breadth": {"mainline": "AI算力/CPO共封装", "stats": {"advancers_ratio": 1.0}},
        "candidate_diagnostics": [{"ticker": "300857.SZ", "name": "协创数据", "role": "leader", "score": 95.0, "stage": "tail_rebound", "stage_reasons": ["价格偏离过大或板块过热"], "quality_scores": {"startup_quality": 35, "trend_integrity": 100, "capital_quality": 50, "crowding_risk": 90}, "risk_flags": ["crowding_risk_high"], "system_attitude": "high_risk_tail", "attribution": [{"metric": "stage", "value": "tail_rebound", "conclusion": "过热"}]}],
        "candidate_readiness": [],
    }
    ai_tail = {
        "trend_diagnosis_version": "v1",
        "mainline_breadth": {"mainline": "AI算力/服务器液冷", "stats": {"advancers_ratio": 0.7}},
        "candidate_diagnostics": [{"ticker": "000666.SZ", "name": "液冷热票", "role": "leader", "score": 90.0, "stage": "tail_rebound", "stage_reasons": ["过热"], "quality_scores": {"startup_quality": 35, "trend_integrity": 88, "capital_quality": 50, "crowding_risk": 85}, "risk_flags": ["crowding_risk_high"], "system_attitude": "high_risk_tail", "attribution": [{"metric": "stage", "value": "tail_rebound", "conclusion": "过热"}]}],
        "candidate_readiness": [],
    }
    semi_startup = {
        "trend_diagnosis_version": "v1",
        "mainline_breadth": {"mainline": "半导体/先进封装", "stats": {"advancers_ratio": 0.52}},
        "candidate_diagnostics": [{"ticker": "688000.SH", "name": "封装候选", "role": "followup", "score": 74.0, "stage": "startup", "stage_reasons": ["站上MA20或平台突破"], "quality_scores": {"startup_quality": 83, "trend_integrity": 71, "capital_quality": 65, "crowding_risk": 24}, "risk_flags": [], "system_attitude": "potential_first", "attribution": [{"metric": "stage", "value": "startup", "conclusion": "结构修复"}]}],
        "candidate_readiness": [],
    }
    finance_markup = {
        "trend_diagnosis_version": "v1",
        "mainline_breadth": {"mainline": "金融/保险银行", "stats": {"advancers_ratio": 0.4}},
        "candidate_diagnostics": [{"ticker": "000001.SZ", "name": "平安银行", "role": "followup", "score": 70.0, "stage": "markup", "stage_reasons": ["均线趋势和扩散共振"], "quality_scores": {"startup_quality": 48, "trend_integrity": 73, "capital_quality": 47, "crowding_risk": 5}, "risk_flags": [], "system_attitude": "trend_follow_only", "attribution": [{"metric": "stage", "value": "markup", "conclusion": "温和趋势"}]}],
        "candidate_readiness": [],
    }

    monkeypatch.setattr(routes, "build_market_context", lambda: context)
    monkeypatch.setattr(routes.ranker, "market_phase", lambda current_scores: "主升")
    monkeypatch.setattr(routes.signal_engine, "build_buy_watchlist", lambda stocks, signal_type: [])
    monkeypatch.setattr(routes.signal_engine, "build_sell_watchlist", lambda positions, active_groups, core_tickers_by_group: [])
    monkeypatch.setattr(routes.portfolio_inspector, "inspect", lambda positions, active_groups, core_tickers_by_group: (positions, 90.0))
    monkeypatch.setattr(routes.emerging_detector, "detect", lambda scores, lookback_minutes=30, limit=5: [])
    monkeypatch.setattr(routes.emerging_detector, "record_snapshot", lambda scores: None)
    monkeypatch.setattr(routes.market_data_service, "get_stock_pool", lambda mainline: [StockCandidate(ticker="000001.SZ", name="样本", role="leader", score=90, last_price=10.0)])
    monkeypatch.setattr(
        routes.market_data_service,
        "get_candidate_diagnostics",
        lambda mainline, limit=5: all_tail if mainline == "AI算力/CPO共封装" else (ai_tail if mainline == "AI算力/服务器液冷" else (semi_startup if mainline == "半导体/先进封装" else finance_markup)),
    )

    response = client.get("/api/v1/diagnose/full")

    assert response.status_code == 200
    body = response.json()["data"]
    trace = body["diagnostic_fallback_trace"]
    assert trace[0]["stage"] == "top_mainline"
    assert trace[0]["mainline"] == "AI算力/CPO共封装"
    assert trace[0]["selected"] is False
    assert trace[0]["reason"] == "all_tail_rebound"
    assert trace[1]["stage"] == "same_group"
    assert trace[1]["mainline"] == "AI算力/服务器液冷"
    assert trace[1]["selected"] is False
    assert trace[1]["reason"] == "all_tail_rebound"
    assert trace[2]["stage"] == "adjacent"
    assert trace[2]["mainline"] == "半导体/先进封装"
    assert trace[2]["selected"] is True
    assert trace[2]["reason"] == "selected"



def test_prepare_and_acknowledge_advice(monkeypatch):
    routes.execution_service._draft_cache.clear()
    monkeypatch.setattr(routes.qmt_connector, "get_account_id", lambda: "acct-001")
    monkeypatch.setattr(
        routes.market_data_service,
        "get_positions",
        lambda: [
            Position(
                ticker="300308.SZ",
                name="中际旭创",
                quantity=100,
                cost_price=100.0,
                last_price=110.0,
                pnl_pct=10.0,
                market_value=11000.0,
                position_pct=0.11,
                mapped_group="AI算力",
                mapped_mainline="AI算力/CPO共封装",
                is_core=True,
            )
        ],
    )
    monkeypatch.setattr(
        routes.order_guard,
        "prepare",
        lambda **kwargs: OrderDraft(
            draft_id="draft-test-001",
            ticker=kwargs["ticker"],
            action=kwargs["action"],
            account_id=kwargs["account_id"],
            target_position_pct=kwargs["target_position_pct"],
            order_volume=100,
            reference_price=110.0,
            passed_checks=True,
            estimated_exposure=0.2,
            risk_notes=["Passed the basic exposure and pricing checks."],
        ),
    )
    monkeypatch.setattr(
        routes.execution_service,
        "confirm",
        lambda draft, user_confirmed: OrderResult(
            order_id=f"advisory:{draft.draft_id}",
            draft_id=draft.draft_id,
            status="advisory_only",
            filled_quantity=0,
        ),
    )

    prepare = client.post(
        "/api/v1/advice/prepare",
        json={
            "ticker": "300308.SZ",
            "action": "buy",
            "target_position_pct": 0.2,
            "reason": "mainline_core_reclaim",
            "account_id": "acct-001",
        },
    )
    assert prepare.status_code == 200
    plan_id = prepare.json()["data"]["plan"]["plan_id"]
    assert prepare.json()["data"]["plan"]["suggested_quantity"] == 100
    assert prepare.json()["data"]["requires_confirmation"] is False

    acknowledge = client.post(
        "/api/v1/advice/acknowledge",
        json={"plan_id": plan_id, "acknowledged": True},
    )
    assert acknowledge.status_code == 200
    assert acknowledge.json()["data"]["result"]["advice_id"] == "advisory:draft-test-001"
    assert acknowledge.json()["data"]["result"]["status"] == "advisory_only"


def test_legacy_order_routes_are_not_exposed():
    response = client.post(
        "/api/v1/orders/prepare",
        json={
            "ticker": "300308.SZ",
            "action": "buy",
            "target_position_pct": 0.2,
            "reason": "mainline_core_reclaim",
            "account_id": "acct-001",
        },
    )

    assert response.status_code == 404


def test_health_check_degrades_gracefully_when_qmt_metadata_is_unavailable(monkeypatch):
    monkeypatch.setattr(routes.qmt_connector, "check_market_connection", lambda: False)
    monkeypatch.setattr(routes.qmt_connector, "check_account_connection", lambda: False)
    monkeypatch.setattr(routes.qmt_connector, "get_account_id", lambda: (_ for _ in ()).throw(RuntimeError("account lookup failed")))
    monkeypatch.setattr(routes.qmt_connector, "get_userdata_path", lambda: (_ for _ in ()).throw(RuntimeError("userdata lookup failed")))

    response = client.get("/api/v1/system/health")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["market_connected"] is False
    assert body["data"]["account_connected"] is False
    assert body["data"]["advisory_only_mode"] is True
    assert body["data"]["live_order_submission_enabled"] is False
    assert body["data"]["qmt_account_id"] is None
    assert body["data"]["qmt_userdata_path"] is None
    assert body["data"]["discovery_error"] == "account lookup failed"


def test_mainline_summary_returns_lightweight_cached_payload(monkeypatch):
    scores = [
        MainlineScore(
            name="AI算力/算力芯片",
            group="AI算力",
            industry_logic_score=2.18,
            capital_strength_score=1.2,
            leader_score=3.65,
            core_score=1.96,
            diffusion_score=3.26,
            persistence_score=2.42,
            market_status_score=3.5,
            total_score=23.62,
            tier="core",
            health_label="healthy_rally",
        ),
        MainlineScore(
            name="半导体/AI芯片",
            group="半导体",
            industry_logic_score=2.18,
            capital_strength_score=1.2,
            leader_score=3.65,
            core_score=1.96,
            diffusion_score=3.26,
            persistence_score=2.42,
            market_status_score=3.0,
            total_score=22.49,
            tier="core",
            health_label="healthy_rally",
        ),
    ]
    monkeypatch.setattr(routes.market_data_service, "get_mainline_scores_cache_meta", lambda: {"has_cache": True, "age_seconds": 0.8, "ttl_seconds": 30})
    monkeypatch.setattr(routes.market_data_service, "get_mainline_scores", lambda: scores)
    monkeypatch.setattr(routes.ranker, "rank_mainlines", lambda items: items)
    monkeypatch.setattr(routes.ranker, "market_phase", lambda items: "轮动")

    response = client.get("/api/v1/mainlines/summary?limit=1")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["market_phase"] == "轮动"
    assert data["top_mainline"]["name"] == "AI算力/算力芯片"
    assert data["cache_hit"] is True
    assert data["cache_ttl_seconds"] == 30
    assert len(data["top_rankings"]) == 1
    assert set(data["top_rankings"][0].keys()) == {"name", "group", "total_score", "tier", "health_label"}



def test_polling_policy_defaults_to_15_minutes():
    response = client.get("/api/v1/system/polling-policy")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["default_interval_minutes"] == 15
    assert data["intensive_interval_minutes"] == 5
    assert "emerging suggestion is probe" in data["push_gates"]["immediate"]

"""Task 1 behavioral tests: contracts (domain.py) and configuration (config.py).

Each test maps to a Task 1 acceptance item in PLAN.md section 8:
advisory default, empty live allowlist, startup live lock, rejection of NaN,
naive timestamps, crossed quotes and invalid stop geometry.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from xauusd.config import (
    Config,
    ConfigError,
    LiveApproval,
    RiskLimits,
    load_config,
    resolve_broker_symbol,
    startup_live_lock,
)
from xauusd.domain import (
    AccountMode,
    Action,
    Bar,
    Candidate,
    Decision,
    EntryType,
    Forecast,
    MarketEvent,
    MarketSnapshot,
    OrderEvent,
    OrderIntent,
    OrderStatus,
    Quote,
    Side,
    SourceHealth,
    SymbolSpec,
)

UTC = timezone.utc


def dt(y: int, m: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, s, tzinfo=UTC)


def make_quote(**over) -> Quote:
    base = dict(
        symbol="XAUUSD",
        bid=2000.10,
        ask=2000.30,
        time=dt(2026, 11, 1, 10, 0, 0),
        received_at=dt(2026, 11, 1, 10, 0, 0),
    )
    base.update(over)
    return Quote(**base)


def make_spec(**over) -> SymbolSpec:
    base = dict(
        symbol="XAUUSD",
        description="Gold vs US Dollar",
        currency_profit="USD",
        digits=2,
        point=Decimal("0.01"),
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        volume_step=Decimal("0.01"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("100"),
        stops_level_points=0,
        freeze_level_points=0,
        trade_allowed=True,
        discovered_at=dt(2026, 11, 1, 9, 0, 0),
    )
    base.update(over)
    return SymbolSpec(**base)


def make_account(**over) -> "object":
    from xauusd.domain import AccountSnapshot

    base = dict(
        login=123456,
        server="DemoServer",
        company="Broker",
        currency="USD",
        mode=AccountMode.DEMO,
        balance=10000.0,
        equity=10000.0,
        margin=0.0,
        margin_free=10000.0,
        margin_level=None,
        leverage=100,
        open_positions=0,
        pending_orders=0,
        server_time_utc=dt(2026, 11, 1, 10, 0, 0),
        snapshot_at=dt(2026, 11, 1, 10, 0, 0),
    )
    base.update(over)
    return AccountSnapshot(**base)


def make_bar(**over) -> Bar:
    base = dict(
        symbol="XAUUSD",
        timeframe="M15",
        time=dt(2026, 11, 1, 9, 45),
        open=2000.00,
        high=2000.80,
        low=1999.90,
        close=2000.50,
        tick_volume=120,
        closed=True,
    )
    base.update(over)
    return Bar(**base)


def make_candidate(**over) -> Candidate:
    base = dict(
        symbol="XAUUSD",
        side=Side.BUY,
        entry_type=EntryType.MARKET,
        entry=2000.30,
        reference_bid=2000.10,
        reference_ask=2000.30,
        stop=1999.00,
        target=2002.90,
        atr=1.30,
        expiry=dt(2026, 11, 1, 10, 15),
        created_at=dt(2026, 11, 1, 10, 0, 0),
        strategy_version="ema-pullback-r1",
        trigger_note="M15 low touched EMA20; close above EMA20 and prior high",
    )
    base.update(over)
    return Candidate(**base)


def make_buy_decision(**over) -> Decision:
    base = dict(
        created_at=dt(2026, 11, 1, 10, 0, 0),
        symbol="XAUUSD",
        action=Action.BUY,
        mode=AccountMode.DEMO,
        candidate=make_candidate(),
        lots=Decimal("0.25"),
        cash_risk=25.0,
        risk_pct_equity=0.0025,
        reasons=["H1 uptrend regime", "M15 pullback trigger"],
        blockers=[],
        strategy_version="ema-pullback-r1",
        dataset_version="ds-test-1",
        config_version="0.1.0",
        quote_bid=2000.10,
        quote_ask=2000.30,
        quote_time=dt(2026, 11, 1, 10, 0, 0),
    )
    base.update(over)
    return Decision(**base)


def make_intent(**over) -> OrderIntent:
    base = dict(
        decision_id="dec-1",
        symbol="XAUUSD",
        side=Side.BUY,
        order_type=EntryType.MARKET,
        volume=Decimal("0.25"),
        price=None,
        reference_price=Decimal("2000.30"),
        stop_loss=Decimal("1999.00"),
        take_profit=Decimal("2002.90"),
        created_at=dt(2026, 11, 1, 10, 0, 0),
        expires_at=dt(2026, 11, 1, 10, 15),
        mode=AccountMode.DEMO,
        strategy_version="ema-pullback-r1",
        magic=600001,
    )
    base.update(over)
    return OrderIntent(**base)


def make_order_event(**over) -> OrderEvent:
    base = dict(
        intent_id="intent-1",
        ticket=777001,
        retcode=10009,
        retcode_name="TRADE_RETCODE_DONE",
        status=OrderStatus.FILLED,
        requested_volume=Decimal("0.25"),
        filled_volume=Decimal("0.25"),
        avg_fill_price=Decimal("2000.31"),
        event_time=dt(2026, 11, 1, 10, 0, 1),
        raw={"redacted": True},
    )
    base.update(over)
    return OrderEvent(**base)


def make_snapshot(**over) -> MarketSnapshot:
    base = dict(
        symbol="XAUUSD",
        quote=make_quote(),
        bars={
            "M15": [make_bar(), make_bar(time=dt(2026, 11, 1, 10, 0), open=2000.5, high=2001.0, low=2000.2, close=2000.9)],
            "H1": [make_bar(timeframe="H1", time=dt(2026, 11, 1, 9, 0))],
        },
        symbol_spec=make_spec(),
        account=make_account(),
        source_health=[SourceHealth(name="mt5", status="ok", last_success_utc=dt(2026, 11, 1, 10, 0), detail="")],
        events=[
            MarketEvent(
                event_id="cpi-2026-11",
                title="US CPI",
                currency="USD",
                importance="high",
                scheduled_utc=dt(2026, 11, 3, 13, 30),
                source="calendar",
            )
        ],
        dataset_version="ds-test-1",
        snapshot_at=dt(2026, 11, 1, 10, 0, 0),
    )
    base.update(over)
    return MarketSnapshot(**base)


# ---------------------------------------------------------------- config


def test_advisory_is_default_mode():
    cfg = Config()
    assert cfg.mode is AccountMode.ADVISORY


def test_default_yaml_loads_advisory_with_no_execution_path():
    cfg = load_config(Path("config/default.yaml"))
    assert cfg.mode is AccountMode.ADVISORY
    assert cfg.live_approval is None
    assert resolve_broker_symbol(cfg, "XAUUSD") == "XAUUSD"


def test_live_allowlist_empty_by_default():
    cfg = Config(mode=AccountMode.LIVE)
    with pytest.raises(ConfigError):
        startup_live_lock(cfg, enable_live=True, login=111)


def test_startup_live_requires_explicit_enable_flag():
    cfg = Config(mode=AccountMode.LIVE, live_approval=_approval([111]))
    with pytest.raises(ConfigError, match="silently"):
        startup_live_lock(cfg, enable_live=False, login=111)
    startup_live_lock(cfg, enable_live=True, login=111)  # does not raise


def _approval(accounts: list[int]) -> LiveApproval:
    return LiveApproval(
        approved_accounts=accounts,
        strategy_version="ema-pullback-r1",
        max_cash_risk_per_trade=25.0,
        max_aggregate_cash_risk=50.0,
        approved_at=dt(2026, 10, 1),
        note="unit-test approval fixture",
    )


def test_startup_live_rejects_account_not_in_allowlist():
    cfg = Config(mode=AccountMode.LIVE, live_approval=_approval([111]))
    with pytest.raises(ConfigError):
        startup_live_lock(cfg, enable_live=True, login=999)


def test_startup_live_requires_account_identity():
    cfg = Config(mode=AccountMode.LIVE, live_approval=_approval([111]))
    with pytest.raises(ConfigError):
        startup_live_lock(cfg, enable_live=True, login=None)


def test_startup_live_rejects_strategy_version_mismatch():
    bad = _approval([111])
    bad = bad.model_copy(update={"strategy_version": "other-strategy"})
    cfg = Config(mode=AccountMode.LIVE, live_approval=bad, strategy_version="ema-pullback-r1")
    with pytest.raises(ConfigError, match="strategy"):
        startup_live_lock(cfg, enable_live=True, login=111)


def test_startup_live_rejects_missing_cash_limits():
    bad = _approval([111]).model_copy(update={"max_cash_risk_per_trade": None})
    cfg = Config(mode=AccountMode.LIVE, live_approval=bad)
    with pytest.raises(ConfigError):
        startup_live_lock(cfg, enable_live=True, login=111)


def test_startup_live_ignores_non_live_modes():
    startup_live_lock(Config(), enable_live=False, login=None)  # advisory: no lock triggered


def test_resolve_broker_symbol_requires_explicit_mapping():
    cfg = Config(symbol_map={"XAUUSD": "XAUUSD.x"})
    assert resolve_broker_symbol(cfg, "XAUUSD") == "XAUUSD.x"
    with pytest.raises(ConfigError, match="symbol_map"):
        resolve_broker_symbol(cfg, "GOLD")


def test_config_rejects_unknown_keys():
    with pytest.raises((ValidationError, ConfigError)):
        load_config_from_dict({"mode": "advisory", "rogue_key": 1})


def load_config_from_dict(data: dict) -> Config:
    from xauusd.config import config_from_dict

    return config_from_dict(data)


def test_risk_defaults_match_plan_section_6():
    r = RiskLimits()
    assert r.planned_risk_per_trade == pytest.approx(0.0025)
    assert r.aggregate_open_risk == pytest.approx(0.005)
    assert r.broker_day_loss_halt == pytest.approx(0.01)
    assert r.equity_drawdown_halt == pytest.approx(0.05)
    assert r.free_margin_reserve == pytest.approx(0.5)
    assert r.max_fills_per_broker_day == 3
    assert r.pause_after_consecutive_losses == 2


# ---------------------------------------------------------------- numeric hygiene


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_quote_rejects_nonfinite_bid(bad):
    with pytest.raises(ValidationError):
        make_quote(bid=bad)


@pytest.mark.parametrize("field", ["stop", "target", "entry", "atr"])
def test_candidate_rejects_nonfinite(field):
    with pytest.raises(ValidationError):
        make_candidate(**{field: float("nan")})


def test_account_rejects_nonfinite_equity():
    with pytest.raises(ValidationError):
        make_account(equity=float("nan"))


def test_decision_rejects_nonfinite_cash_risk():
    with pytest.raises(ValidationError):
        make_buy_decision(cash_risk=float("inf"))


def test_quote_rejects_crossed_quotes():
    with pytest.raises(ValidationError, match="crossed"):
        make_quote(bid=2000.30, ask=2000.10)


def test_quote_allows_equal_bid_ask():
    q = make_quote(bid=2000.10, ask=2000.10)
    assert q.bid == q.ask


def test_quote_rejects_naive_timestamps():
    naive = datetime(2026, 11, 1, 10, 0, 0)
    with pytest.raises(ValidationError):
        make_quote(time=naive)
    with pytest.raises(ValidationError):
        make_quote(received_at=naive)


def test_quote_rejects_nonpositive_prices():
    with pytest.raises(ValidationError):
        make_quote(bid=0.0)
    with pytest.raises(ValidationError):
        make_quote(ask=-1.0)


def test_timestamps_are_normalized_to_utc():
    q = make_quote(time=datetime(2026, 11, 1, 12, 0, tzinfo=timezone(timedelta(hours=2))))
    assert q.time.tzinfo is UTC
    assert q.time.hour == 10


def test_models_are_frozen():
    q = make_quote()
    with pytest.raises(ValidationError):
        q.bid = 1.0


def test_models_reject_extra_fields():
    with pytest.raises(ValidationError):
        make_quote(rogue_field=1)


# ---------------------------------------------------------------- bar hygiene


def test_bar_rejects_inconsistent_candle():
    with pytest.raises(ValidationError):
        make_bar(high=1999.0)  # high < open/close
    with pytest.raises(ValidationError):
        make_bar(low=2001.0)  # low > open/close
    with pytest.raises(ValidationError):
        make_bar(high=1998.0, low=1999.0)  # high < low


def test_bar_rejects_unknown_timeframe():
    with pytest.raises(ValidationError):
        make_bar(timeframe="H7")


def test_bar_must_be_marked_closed_or_open():
    assert make_bar(closed=True).closed is True
    assert make_bar(closed=False).closed is False


# ---------------------------------------------------------------- candidate geometry


def test_candidate_buy_geometry_valid():
    c = make_candidate()
    assert c.stop < c.entry < c.target


def test_candidate_buy_geometry_invalid():
    with pytest.raises(ValidationError, match="geometry"):
        make_candidate(stop=2001.00)  # stop above entry
    with pytest.raises(ValidationError, match="geometry"):
        make_candidate(target=1999.50)  # target below entry
    with pytest.raises(ValidationError, match="geometry"):
        make_candidate(stop=2002.90, target=1999.00)  # fully inverted


def test_candidate_sell_geometry_mirrored():
    c = make_candidate(
        side=Side.SELL,
        entry=2000.10,
        reference_bid=2000.10,
        reference_ask=2000.30,
        stop=2001.40,
        target=1997.50,
    )
    assert c.target < c.entry < c.stop
    with pytest.raises(ValidationError, match="geometry"):
        make_candidate(side=Side.SELL, stop=1999.00, target=2001.00)


def test_candidate_expiry_must_be_after_created_at():
    with pytest.raises(ValidationError):
        make_candidate(expiry=dt(2026, 11, 1, 9, 0))


# ---------------------------------------------------------------- decision invariants


def test_decision_no_trade_has_zero_executable_volume():
    d = make_buy_decision(action=Action.NO_TRADE, candidate=None, lots=None, cash_risk=None, risk_pct_equity=None, blockers=["GATE_EVENT_BLOCK"])
    assert d.lots is None and d.cash_risk is None
    with pytest.raises(ValidationError):
        make_buy_decision(action=Action.NO_TRADE, candidate=None, lots=Decimal("0.25"))
    with pytest.raises(ValidationError):
        make_buy_decision(action=Action.NO_TRADE, candidate=None, cash_risk=10.0)


def test_decision_wait_carries_no_volume():
    with pytest.raises(ValidationError):
        make_buy_decision(action=Action.WAIT, candidate=None, lots=Decimal("0.10"), cash_risk=10.0)


def test_decision_buy_requires_candidate_and_risk():
    with pytest.raises(ValidationError):
        make_buy_decision(candidate=None)
    with pytest.raises(ValidationError):
        make_buy_decision(lots=None)
    with pytest.raises(ValidationError):
        make_buy_decision(cash_risk=None)


def test_decision_exit_requires_position_ticket():
    with pytest.raises(ValidationError):
        make_buy_decision(
            action=Action.EXIT,
            candidate=None,
            lots=None,
            cash_risk=None,
            risk_pct_equity=None,
        )
    d = make_buy_decision(
        action=Action.EXIT,
        candidate=None,
        lots=None,
        cash_risk=None,
        risk_pct_equity=None,
        position_ticket=777001,
        reasons=["time exit after 16 M15 bars"],
    )
    assert d.position_ticket == 777001


# ---------------------------------------------------------------- forecast gating


def test_unvalidated_forecast_cannot_show_probabilities():
    with pytest.raises(ValidationError, match="unvalidated"):
        Forecast(
            horizon_bars=16,
            outcome="target reached before stop within 16 M15 bars",
            probability_target_first=0.62,
            model_version="constant-baseline",
            dataset_version="ds-test-1",
            validated=False,
            limitations=["no calibration evidence yet"],
            created_at=dt(2026, 11, 1, 10, 0),
        )


def test_validated_forecast_can_show_probabilities():
    f = Forecast(
        horizon_bars=16,
        outcome="target reached before stop within 16 M15 bars",
        probability_target_first=0.62,
        model_version="logistic-r1",
        dataset_version="ds-test-1",
        validated=True,
        calibration_note="calibrated on 2024H2-2025H1 folds",
        limitations=[],
        created_at=dt(2026, 11, 1, 10, 0),
    )
    assert f.probability_target_first == pytest.approx(0.62)


def test_forecast_probability_bounds():
    with pytest.raises(ValidationError):
        Forecast(
            horizon_bars=16,
            outcome="x",
            probability_target_first=1.5,
            model_version="m",
            dataset_version="d",
            validated=True,
            limitations=[],
            created_at=dt(2026, 11, 1, 10, 0),
        )


# ---------------------------------------------------------------- market event / snapshot


def test_market_event_actual_value_requires_actual_timestamp():
    with pytest.raises(ValidationError):
        MarketEvent(
            event_id="cpi-x",
            title="US CPI",
            importance="high",
            scheduled_utc=dt(2026, 11, 3, 13, 30),
            actual_value=3.1,
            source="calendar",
        )


def test_market_snapshot_assembles_all_required_inputs():
    s = make_snapshot()
    assert s.quote.symbol == "XAUUSD"
    assert set(s.bars) <= {"M1", "M5", "M15", "H1", "H4"}
    assert s.dataset_version == "ds-test-1"


def test_market_snapshot_rejects_unknown_bar_key():
    with pytest.raises(ValidationError):
        make_snapshot(bars={"H3": [make_bar()]})


# ---------------------------------------------------------------- order intents / events


def test_order_intent_rejected_in_advisory_mode():
    with pytest.raises(ValidationError, match="advisory"):
        make_intent(mode=AccountMode.ADVISORY)


def test_order_intent_stop_order_requires_price():
    with pytest.raises(ValidationError):
        make_intent(order_type=EntryType.STOP, price=None)
    intent = make_intent(order_type=EntryType.STOP, price=Decimal("2000.50"))
    assert intent.price == Decimal("2000.50")


def test_order_intent_market_order_has_no_price():
    with pytest.raises(ValidationError):
        make_intent(order_type=EntryType.MARKET, price=Decimal("2000.50"))


def test_order_intent_volume_must_be_positive_decimal():
    with pytest.raises(ValidationError):
        make_intent(volume=Decimal("0"))
    intent = make_intent(volume=Decimal("0.01"))
    assert isinstance(intent.volume, Decimal)


def test_order_intent_stop_geometry():
    with pytest.raises(ValidationError, match="geometry"):
        make_intent(side=Side.BUY, stop_loss=Decimal("2001.00"))  # stop above entry reference
    with pytest.raises(ValidationError, match="geometry"):
        make_intent(side=Side.BUY, take_profit=Decimal("1998.00"))


def test_order_event_fill_consistency():
    with pytest.raises(ValidationError):
        make_order_event(status=OrderStatus.FILLED, filled_volume=Decimal("0.20"))
    with pytest.raises(ValidationError):
        make_order_event(status=OrderStatus.PARTIAL, filled_volume=Decimal("0"))
    with pytest.raises(ValidationError):
        make_order_event(status=OrderStatus.PARTIAL, filled_volume=Decimal("0.25"))
    partial = make_order_event(status=OrderStatus.PARTIAL, filled_volume=Decimal("0.10"))
    assert Decimal("0") < partial.filled_volume < partial.requested_volume
    for st in (OrderStatus.REJECTED, OrderStatus.CANCELLED, OrderStatus.TIMEOUT, OrderStatus.UNKNOWN, OrderStatus.SUBMITTED):
        with pytest.raises(ValidationError):
            make_order_event(status=st, filled_volume=Decimal("0.10"))

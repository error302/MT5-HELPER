"""Task 2 integration tests: read-only MT5 feed (data/mt5_feed.py).

All broker interactions go through a FakeMt5 module. Behavioral coverage per
PLAN.md Task 2: disconnect, stale quote, wrong account, demo/live mismatch,
missing symbol, unavailable calculation values - plus the read-only guarantee
(order functions are never called by the feed).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from xauusd.data.mt5_feed import (
    AccountMismatchError,
    FeedError,
    Mt5Feed,
    StaleQuoteError,
    SymbolNotFoundError,
    TerminalDisconnectedError,
    mask_login,
)
from xauusd.domain import AccountMode

UTC = timezone.utc
NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)
LOGIN = 555123

ORDER_CALLS = {"ORDER_SEND", "ORDER_CHECK", "ORDER_CALC_PROFIT", "ORDER_CALC_MARGIN", "BUY", "SELL"}


class FakeTick:
    def __init__(self, bid: float, ask: float, time: int, time_msc: int) -> None:
        self.bid = bid
        self.ask = ask
        self.time = time
        self.time_msc = time_msc


class FakeSymbolInfo:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = "Gold vs US Dollar"
        self.currency_profit = "USD"
        self.path = "Forex\\Metals\\XAUUSD"
        self.digits = 2
        self.point = 0.01
        self.trade_tick_size = 0.01
        self.trade_tick_value = 0.01
        self.volume_min = 0.01
        self.volume_max = 50.0
        self.volume_step = 0.01
        self.trade_stops_level = 0
        self.trade_freeze_level = 0
        self.trade_mode = 4  # SYMBOL_TRADE_MODE_FULL
        self.trade_calc_mode = 0
        self.trade_exec = 0
        self.filling_mode = 1
        self.swap_long = -7.5
        self.swap_short = 2.1
        self.session_deals = np.zeros(0)
        self.session_quotes = np.zeros(0)


class FakeAccountInfo:
    def __init__(self, trade_mode: int = 0, login: int = LOGIN) -> None:
        self.login = login
        self.server = "Demo-Server"
        self.company = "TestBroker Ltd"
        self.currency = "USD"
        self.balance = 10000.0
        self.equity = 10000.0
        self.margin = 0.0
        self.margin_free = 10000.0
        self.margin_level = 0.0
        self.leverage = 100
        self.trade_mode = trade_mode
        self.time = int(NOW.timestamp())


class FakeTerminalInfo:
    def __init__(self, connected: bool = True) -> None:
        self.name = "MetaTrader 5"
        self.connected = connected
        self.build = 5000


class FakeMt5:
    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 15
    TIMEFRAME_H1 = 16385
    TIMEFRAME_H4 = 16388
    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_CONTEST = 1
    ACCOUNT_TRADE_MODE_REAL = 2
    SYMBOL_TRADE_MODE_FULL = 4

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.initialize_result = True
        self.last_error_result = (0, "no error")
        self.terminal_connected = True
        self.account = FakeAccountInfo()
        self.terminal = FakeTerminalInfo(True)
        self.symbols: dict[str, FakeSymbolInfo] = {}
        self.ticks: dict[str, FakeTick] = {}
        self.rates: dict[tuple[str, int], np.ndarray] = {}
        self.positions_total_result = 0
        self.orders_total_result = 0
        self.version_result = ("5.00.50", 5000, "2026.09.15")

    # lifecycle
    def initialize(self, path: str | None = None, **kwargs) -> bool:
        self.calls.append(("initialize", path, tuple(sorted(kwargs))))
        self._init_kwargs = kwargs
        return self.initialize_result

    def shutdown(self) -> None:
        self.calls.append(("shutdown",))

    def terminal_info(self) -> FakeTerminalInfo | None:
        self.calls.append(("terminal_info",))
        return self.terminal

    def last_error(self) -> tuple[int, str]:
        return self.last_error_result

    def version(self) -> tuple[str, int, str]:
        return self.version_result

    # market data
    def symbol_info(self, symbol: str) -> FakeSymbolInfo | None:
        self.calls.append(("symbol_info", symbol))
        return self.symbols.get(symbol)

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        self.calls.append(("symbol_select", symbol, enable))
        return symbol in self.symbols

    def symbols_get(self, group: str | None = None) -> list[FakeSymbolInfo]:
        self.calls.append(("symbols_get", group))
        return list(self.symbols.values())

    def symbol_info_tick(self, symbol: str) -> FakeTick | None:
        self.calls.append(("symbol_info_tick", symbol))
        return self.ticks.get(symbol)

    def copy_rates_from_pos(self, symbol: str, timeframe: int, start: int, count: int):
        self.calls.append(("copy_rates_from_pos", symbol, timeframe, start, count))
        return self.rates.get((symbol, timeframe))

    # account
    def account_info(self) -> FakeAccountInfo:
        self.calls.append(("account_info",))
        return self.account

    def positions_total(self) -> int:
        self.calls.append(("positions_total",))
        return self.positions_total_result

    def orders_total(self) -> int:
        self.calls.append(("orders_total",))
        return self.orders_total_result

    # order functions - the feed must NEVER call these
    def order_send(self, request) -> None:
        self.calls.append(("ORDER_SEND", request))

    def order_check(self, request) -> None:
        self.calls.append(("ORDER_CHECK", request))

    def order_calc_profit(self, *args) -> None:
        self.calls.append(("ORDER_CALC_PROFIT", args))

    def order_calc_margin(self, *args) -> None:
        self.calls.append(("ORDER_CALC_MARGIN", args))

    def buy(self, *args, **kwargs) -> None:
        self.calls.append(("BUY", args))

    def sell(self, *args, **kwargs) -> None:
        self.calls.append(("SELL", args))


def make_rates(symbol: str, timeframe: int, n: int, start: datetime) -> np.ndarray:
    times = np.array([int((start + timedelta(minutes=15 * i)).timestamp()) for i in range(n)], dtype="<i8")
    return np.array(
        list(
            zip(
                times,
                np.full(n, 2000.0),
                np.full(n, 2001.0),
                np.full(n, 1999.5),
                np.full(n, 2000.5),
                np.full(n, 100, dtype="<i8"),
                np.zeros(n, dtype="<i4"),
                np.zeros(n, dtype="<i8"),
            )
        ),
        dtype=[
            ("time", "<i8"),
            ("open", "<f8"),
            ("high", "<f8"),
            ("low", "<f8"),
            ("close", "<f8"),
            ("tick_volume", "<i8"),
            ("spread", "<i4"),
            ("real_volume", "<i8"),
        ],
    )


@pytest.fixture()
def fake() -> FakeMt5:
    f = FakeMt5()
    f.symbols["XAUUSD.x"] = FakeSymbolInfo("XAUUSD.x")
    f.ticks["XAUUSD.x"] = FakeTick(
        bid=2000.10,
        ask=2000.30,
        time=int((NOW - timedelta(seconds=1)).timestamp()),
        time_msc=int((NOW - timedelta(seconds=1)).timestamp() * 1000),
    )
    f.rates[("XAUUSD.x", FakeMt5.TIMEFRAME_M15)] = make_rates(
        "XAUUSD.x", FakeMt5.TIMEFRAME_M15, 11, NOW - timedelta(minutes=15 * 11)
    )
    return f


@pytest.fixture()
def feed(fake: FakeMt5) -> Mt5Feed:
    fd = Mt5Feed(expected_login=LOGIN, expected_mode=AccountMode.DEMO, mt5_module=fake, now_fn=lambda: NOW)
    fd.connect()
    return fd


# ---------------------------------------------------------------- lifecycle


def test_connect_fails_when_terminal_unavailable(fake: FakeMt5):
    fake.initialize_result = False
    fd = Mt5Feed(mt5_module=fake)
    with pytest.raises(TerminalDisconnectedError):
        fd.connect()


def test_connect_fails_when_terminal_not_connected_to_broker(fake: FakeMt5):
    fake.terminal = FakeTerminalInfo(connected=False)
    fd = Mt5Feed(mt5_module=fake)
    with pytest.raises(TerminalDisconnectedError):
        fd.connect()


def test_connect_attaches_to_running_terminal_without_credentials(fake: FakeMt5):
    fd = Mt5Feed(mt5_module=fake)
    fd.connect()
    init_calls = [c for c in fake.calls if c[0] == "initialize"]
    assert init_calls, "initialize must be called"
    _, path, kwargs = init_calls[0]
    assert kwargs == (), "no credentials may be passed to initialize"
    assert path is None
    fd.disconnect()


def test_reads_before_connect_raise(fake: FakeMt5):
    fd = Mt5Feed(mt5_module=fake)
    with pytest.raises(TerminalDisconnectedError):
        fd.get_quote("XAUUSD.x")
    with pytest.raises(TerminalDisconnectedError):
        fd.get_account()


def test_disconnect_is_idempotent(feed: Mt5Feed):
    feed.disconnect()
    feed.disconnect()
    assert feed.is_connected() is False


def test_terminal_vanishing_mid_session_raises(fake: FakeMt5):
    fd = Mt5Feed(mt5_module=fake, now_fn=lambda: NOW)
    fd.connect()
    fake.terminal = None
    with pytest.raises(TerminalDisconnectedError):
        fd.get_quote("XAUUSD.x")


# ---------------------------------------------------------------- symbol mapping


def test_resolve_exact_configured_symbol(feed: Mt5Feed):
    assert feed.resolve_symbol("XAUUSD", exact="XAUUSD.x") == "XAUUSD.x"


def test_resolve_configured_symbol_missing_raises(fake: FakeMt5):
    fd = Mt5Feed(mt5_module=fake, now_fn=lambda: NOW)
    fd.connect()
    with pytest.raises(SymbolNotFoundError):
        fd.resolve_symbol("XAUUSD", exact="XAUUSD.missing")


def test_resolve_suffix_discovery_unique(fake: FakeMt5):
    fd = Mt5Feed(mt5_module=fake, now_fn=lambda: NOW)
    fd.connect()
    assert fd.resolve_symbol("XAUUSD") == "XAUUSD.x"


def test_resolve_ambiguous_candidates_require_explicit_mapping(fake: FakeMt5):
    fake.symbols["XAUUSD.y"] = FakeSymbolInfo("XAUUSD.y")
    fd = Mt5Feed(mt5_module=fake, now_fn=lambda: NOW)
    fd.connect()
    with pytest.raises(FeedError, match="symbol_map"):
        fd.resolve_symbol("XAUUSD")


def test_resolve_unknown_symbol_raises(fake: FakeMt5):
    fd = Mt5Feed(mt5_module=fake, now_fn=lambda: NOW)
    fd.connect()
    with pytest.raises(SymbolNotFoundError):
        fd.resolve_symbol("GOLD")


# ---------------------------------------------------------------- quotes


def test_get_quote_fresh(feed: Mt5Feed):
    q = feed.get_quote("XAUUSD.x")
    assert q.symbol == "XAUUSD.x"
    assert q.bid == pytest.approx(2000.10)
    assert q.ask == pytest.approx(2000.30)
    assert q.received_at == NOW


def test_get_quote_stale_raises(fake: FakeMt5):
    fake.ticks["XAUUSD.x"] = FakeTick(
        bid=2000.10,
        ask=2000.30,
        time=int((NOW - timedelta(seconds=120)).timestamp()),
        time_msc=int((NOW - timedelta(seconds=120)).timestamp() * 1000),
    )
    fd = Mt5Feed(mt5_module=fake, now_fn=lambda: NOW)
    fd.connect()
    with pytest.raises(StaleQuoteError):
        fd.get_quote("XAUUSD.x")


def test_get_quote_missing_tick_raises(fake: FakeMt5):
    fake.ticks.clear()
    fd = Mt5Feed(mt5_module=fake, now_fn=lambda: NOW)
    fd.connect()
    with pytest.raises(FeedError):
        fd.get_quote("XAUUSD.x")


def test_get_quote_zero_prices_raise(fake: FakeMt5):
    fake.ticks["XAUUSD.x"] = FakeTick(bid=0.0, ask=0.0, time=int(NOW.timestamp()), time_msc=int(NOW.timestamp() * 1000))
    fd = Mt5Feed(mt5_module=fake, now_fn=lambda: NOW)
    fd.connect()
    with pytest.raises(FeedError):
        fd.get_quote("XAUUSD.x")


# ---------------------------------------------------------------- bars


def test_get_bars_drop_unfinished_bar(feed: Mt5Feed):
    bars = feed.get_bars("XAUUSD.x", "M15", 10, closed_only=True)
    assert len(bars) == 10
    raw = feed._mt5.rates[("XAUUSD.x", 15)]
    assert bars[0].time.timestamp() == raw[1]["time"], "position 0 (forming bar) must be dropped"
    assert all(b.closed for b in bars)


def test_get_bars_include_forming_bar_when_requested(feed: Mt5Feed):
    bars = feed.get_bars("XAUUSD.x", "M15", 11, closed_only=False)
    assert len(bars) == 11


def test_get_bars_unknown_timeframe_raises(feed: Mt5Feed):
    with pytest.raises(FeedError):
        feed.get_bars("XAUUSD.x", "H3", 10)


def test_get_bars_read_failure_raises(fake: FakeMt5):
    fd = Mt5Feed(mt5_module=fake, now_fn=lambda: NOW)
    fd.connect()
    with pytest.raises(FeedError):
        fd.get_bars("XAUUSD.x", "H1", 10)


# ---------------------------------------------------------------- symbol economics


def test_symbol_spec_built_from_metadata(feed: Mt5Feed):
    spec = feed.get_symbol_spec("XAUUSD.x")
    assert spec.symbol == "XAUUSD.x"
    assert spec.digits == 2
    assert spec.volume_step.as_tuple().exponent >= -2
    assert spec.trade_allowed is True


def test_symbol_missing_raises(fake: FakeMt5):
    fd = Mt5Feed(mt5_module=fake, now_fn=lambda: NOW)
    fd.connect()
    with pytest.raises(SymbolNotFoundError):
        fd.get_symbol_spec("NOPE.x")


@pytest.mark.parametrize("field", ["trade_tick_value", "trade_tick_size", "point", "volume_step"])
def test_unavailable_calculation_values_block(fake: FakeMt5, field: str):
    info = FakeSymbolInfo("XAUUSD.x")
    setattr(info, field, 0.0)
    fake.symbols = {"XAUUSD.x": info}
    fd = Mt5Feed(mt5_module=fake, now_fn=lambda: NOW)
    fd.connect()
    with pytest.raises(FeedError, match="unavailable"):
        fd.get_symbol_spec("XAUUSD.x")


def test_trade_disabled_symbol_marks_not_allowed(fake: FakeMt5):
    info = FakeSymbolInfo("XAUUSD.x")
    info.trade_mode = 0  # SYMBOL_TRADE_MODE_DISABLED
    fake.symbols = {"XAUUSD.x": info}
    fd = Mt5Feed(mt5_module=fake, now_fn=lambda: NOW)
    fd.connect()
    assert fd.get_symbol_spec("XAUUSD.x").trade_allowed is False


# ---------------------------------------------------------------- account / identity


def test_wrong_account_rejected(fake: FakeMt5):
    fd = Mt5Feed(expected_login=999, mt5_module=fake, now_fn=lambda: NOW)
    fd.connect()
    with pytest.raises(AccountMismatchError, match="allowlist|wrong account"):
        fd.get_account()


def test_demo_live_mismatch_rejected(fake: FakeMt5):
    fake.account = FakeAccountInfo(trade_mode=2)  # REAL
    fd = Mt5Feed(mt5_module=fake, now_fn=lambda: NOW)
    fd.connect()
    with pytest.raises(AccountMismatchError, match="demo|live"):
        fd.get_account(expected_mode=AccountMode.DEMO)


def test_live_expected_but_demo_present_rejected(fake: FakeMt5):
    fake.account = FakeAccountInfo(trade_mode=0)  # DEMO
    fd = Mt5Feed(mt5_module=fake, now_fn=lambda: NOW)
    fd.connect()
    with pytest.raises(AccountMismatchError):
        fd.get_account(expected_mode=AccountMode.LIVE)


def test_matching_demo_account_passes(feed: Mt5Feed):
    snap = feed.get_account(expected_mode=AccountMode.DEMO)
    assert snap.mode is AccountMode.DEMO
    assert snap.login == LOGIN
    assert snap.open_positions == 0


def test_account_unavailable_raises(fake: FakeMt5):
    fd = Mt5Feed(mt5_module=fake, now_fn=lambda: NOW)
    fd.connect()

    class _NoneAccount:
        pass

    fake.account_info = lambda: None  # type: ignore[method-assign]
    with pytest.raises(TerminalDisconnectedError):
        fd.get_account()


# ---------------------------------------------------------------- capability report


def test_capability_report_is_redacted(feed: Mt5Feed):
    report = feed.capability_report("XAUUSD", exact="XAUUSD.x")
    blob = json.dumps(report, default=str)
    assert str(LOGIN) not in blob, "full login must not appear in the report"
    assert report["login_masked"] == mask_login(LOGIN)
    assert report["account_mode"] == "demo"
    assert report["symbol_economics"]["digits"] == 2
    assert report["read_only"] is True


# ---------------------------------------------------------------- read-only guarantee


def test_full_read_cycle_never_calls_order_functions(feed: Mt5Feed):
    feed.resolve_symbol("XAUUSD", exact="XAUUSD.x")
    feed.get_symbol_spec("XAUUSD.x")
    feed.get_quote("XAUUSD.x")
    feed.get_bars("XAUUSD.x", "M15", 10)
    feed.get_account()
    feed.capability_report("XAUUSD", exact="XAUUSD.x")
    feed.disconnect()
    order_calls = [c for c in feed._mt5.calls if c[0] in ORDER_CALLS]
    assert order_calls == []

"""Read-only MT5 terminal integration (PLAN.md Task 2).

This feed NEVER places, modifies or cancels orders. It only reads:
- terminal lifecycle (attach to an already-logged-in terminal, no credentials)
- exact-symbol resolution with explicit suffix mapping
- quotes with staleness detection
- closed bars (the forming bar is dropped unless explicitly requested)
- account snapshots with demo/live and allowlist identity checks
- a redacted broker capability report

Order functions of the MetaTrader5 module are never imported into call paths
here; tests assert this on a recording fake.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from xauusd.domain import AccountMode, Bar, Quote, SymbolSpec, utc_now

UTC = timezone.utc

# MetaTrader5 module constants are mirrored here so this module can be tested
# without the native package installed. The real module is imported lazily.
_TF_MAP_KEYS = ("M1", "M5", "M15", "H1", "H4")

_ACCOUNT_MODE_MAP = {0: AccountMode.DEMO, 1: None, 2: AccountMode.LIVE}  # contest unsupported
SYMBOL_TRADE_MODE_FULL = 4


class FeedError(Exception):
    """Base class for feed failures that must block decisions."""


class TerminalDisconnectedError(FeedError):
    pass


class StaleQuoteError(FeedError):
    pass


class SymbolNotFoundError(FeedError):
    pass


class AccountMismatchError(FeedError):
    pass


def mask_login(login: int | None) -> str:
    """Mask an account login for logs and reports."""
    if login is None:
        return "***"
    s = str(login)
    return "***" + s[-3:] if len(s) > 3 else "***"


class _Mt5Like(Protocol):  # pragma: no cover - structural typing only
    def initialize(self, *args: Any, **kwargs: Any) -> bool: ...
    def shutdown(self) -> None: ...
    def terminal_info(self) -> Any: ...
    def last_error(self) -> tuple[int, str]: ...
    def version(self) -> tuple[str, int, str] | None: ...
    def symbol_info(self, symbol: str) -> Any: ...
    def symbol_select(self, symbol: str, enable: bool) -> bool: ...
    def symbols_get(self, group: str | None = None) -> Any: ...
    def symbol_info_tick(self, symbol: str) -> Any: ...
    def copy_rates_from_pos(self, symbol: str, timeframe: int, start: int, count: int) -> Any: ...
    def account_info(self) -> Any: ...
    def positions_total(self) -> int: ...
    def orders_total(self) -> int: ...
    TIMEFRAME_M1: int
    TIMEFRAME_M5: int
    TIMEFRAME_M15: int
    TIMEFRAME_H1: int
    TIMEFRAME_H4: int


class Mt5Feed:
    """Read-only access to a running, logged-in MT5 terminal."""

    def __init__(
        self,
        *,
        expected_login: int | None = None,
        expected_mode: AccountMode | None = None,
        max_quote_age_seconds: float = 5.0,
        terminal_path: str | None = None,
        mt5_module: Any | None = None,
        now_fn: Callable[[], datetime] = utc_now,
    ) -> None:
        self._mt5_module = mt5_module
        self._mt5: Any | None = None
        self._connected = False
        self._expected_login = expected_login
        self._expected_mode = expected_mode
        self._max_quote_age = max_quote_age_seconds
        self._terminal_path = terminal_path
        self._now_fn = now_fn

    # ------------------------------------------------------------ lifecycle

    def _module(self) -> Any:
        if self._mt5 is None:
            if self._mt5_module is not None:
                self._mt5 = self._mt5_module
            else:
                try:
                    import MetaTrader5 as real_mt5  # deferred: keeps unit environments clean
                except Exception as exc:  # pragma: no cover - depends on host
                    raise FeedError(f"MetaTrader5 package unavailable on this host: {exc}") from exc
                self._mt5 = real_mt5
        return self._mt5

    def connect(self) -> None:
        mt5 = self._module()
        kwargs: dict[str, Any] = {}
        if self._terminal_path:
            kwargs["path"] = self._terminal_path
        # Credentials are intentionally never accepted here: the terminal is
        # already logged in by the owner (PLAN.md section 1).
        if not mt5.initialize(**kwargs):
            raise TerminalDisconnectedError(f"terminal initialize failed: {mt5.last_error()}")
        ti = mt5.terminal_info()
        if ti is None or not getattr(ti, "connected", False):
            mt5.shutdown()
            raise TerminalDisconnectedError("terminal is running but not connected to the broker")
        self._connected = True

    def disconnect(self) -> None:
        if self._connected and self._mt5 is not None:
            self._mt5.shutdown()
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def _ensure_connected(self) -> Any:
        mt5 = self._module()
        if not self._connected or mt5.terminal_info() is None:
            raise TerminalDisconnectedError("feed is not connected; call connect() first")
        return mt5

    def _timeframe_const(self, timeframe: str) -> int:
        mt5 = self._module()
        const = f"TIMEFRAME_{timeframe}"
        value = getattr(mt5, const, None)
        if value is None:
            raise FeedError(f"unknown timeframe {timeframe!r}")
        return int(value)

    # ------------------------------------------------------------ symbols

    def resolve_symbol(self, canonical: str, exact: str | None = None) -> str:
        """Resolve the canonical instrument to the exact broker symbol.

        An explicitly configured symbol is verified verbatim. Without one,
        discovery is allowed only when it is unambiguous; otherwise the exact
        suffix must be mapped in config (PLAN.md section 2).
        """
        mt5 = self._ensure_connected()
        if exact:
            info = mt5.symbol_info(exact)
            if info is None:
                raise SymbolNotFoundError(f"configured broker symbol {exact!r} not found in terminal")
            if not mt5.symbol_select(exact, True):
                raise FeedError(f"failed to select broker symbol {exact!r} in Market Watch")
            return exact
        info = mt5.symbol_info(canonical)
        if info is not None:
            if not mt5.symbol_select(canonical, True):
                raise FeedError(f"failed to select broker symbol {canonical!r} in Market Watch")
            return canonical
        candidates = sorted(
            s.name for s in (mt5.symbols_get() or []) if canonical in getattr(s, "name", "")
        )
        if not candidates:
            raise SymbolNotFoundError(f"no symbol matching {canonical!r} in terminal")
        if len(candidates) == 1:
            if not mt5.symbol_select(candidates[0], True):
                raise FeedError(f"failed to select broker symbol {candidates[0]!r} in Market Watch")
            return candidates[0]
        raise FeedError(
            f"ambiguous symbol candidates for {canonical!r}: {candidates}; "
            "map the exact broker symbol in config symbol_map"
        )

    def get_symbol_spec(self, broker_symbol: str) -> SymbolSpec:
        mt5 = self._ensure_connected()
        info = mt5.symbol_info(broker_symbol)
        if info is None:
            raise SymbolNotFoundError(f"symbol {broker_symbol!r} not found")
        point = float(getattr(info, "point", 0.0) or 0.0)
        tick_size_raw = getattr(info, "trade_tick_size", None)
        tick_size = float(tick_size_raw) if tick_size_raw is not None else 0.0
        tick_value = float(getattr(info, "trade_tick_value", 0.0) or 0.0)
        volume_step = float(getattr(info, "volume_step", 0.0) or 0.0)
        volume_min = float(getattr(info, "volume_min", 0.0) or 0.0)
        volume_max = float(getattr(info, "volume_max", 0.0) or 0.0)
        unavailable = [
            name
            for name, value in (
                ("point", point),
                ("tick_size", tick_size),
                ("tick_value", tick_value),
                ("volume_step", volume_step),
                ("volume_min", volume_min),
                ("volume_max", volume_max),
            )
            if value <= 0 or not math.isfinite(value)
        ]
        if unavailable:
            raise FeedError(
                f"unavailable calculation values for {broker_symbol!r}: {', '.join(unavailable)}; "
                "contract economics must be verified before trading"
            )
        symbol_trade_mode = int(getattr(info, "trade_mode", 0) or 0)
        return SymbolSpec(
            symbol=str(info.name),
            description=str(getattr(info, "description", "") or ""),
            currency_profit=str(getattr(info, "currency_profit", "") or ""),
            digits=int(getattr(info, "digits", 0) or 0),
            point=repr_float_as_decimal(point),
            tick_size=repr_float_as_decimal(tick_size),
            tick_value=repr_float_as_decimal(tick_value),
            volume_step=repr_float_as_decimal(volume_step),
            volume_min=repr_float_as_decimal(volume_min),
            volume_max=repr_float_as_decimal(volume_max),
            stops_level_points=int(getattr(info, "trade_stops_level", 0) or 0),
            freeze_level_points=int(getattr(info, "trade_freeze_level", 0) or 0),
            trade_allowed=symbol_trade_mode == SYMBOL_TRADE_MODE_FULL,
            discovered_at=self._now_fn(),
        )

    # ------------------------------------------------------------ quotes / bars

    def get_quote(self, broker_symbol: str, *, now: datetime | None = None) -> Quote:
        mt5 = self._ensure_connected()
        tick = mt5.symbol_info_tick(broker_symbol)
        if tick is None:
            raise FeedError(f"no tick available for {broker_symbol!r}")
        now = now or self._now_fn()
        time_msc = int(getattr(tick, "time_msc", 0) or 0)
        if time_msc > 0:
            tick_dt = datetime.fromtimestamp(time_msc / 1000.0, tz=UTC)
        else:
            tick_dt = datetime.fromtimestamp(int(getattr(tick, "time", 0) or 0), tz=UTC)
        if tick_dt <= datetime(1971, 1, 1, tzinfo=UTC):
            raise FeedError(f"tick for {broker_symbol!r} has no usable timestamp")
        bid = float(getattr(tick, "bid", 0.0) or 0.0)
        ask = float(getattr(tick, "ask", 0.0) or 0.0)
        if bid <= 0 or ask <= 0:
            raise FeedError(f"nonpositive quote for {broker_symbol!r} (market closed or bad data)")
        age = (now - tick_dt).total_seconds()
        if age > self._max_quote_age:
            raise StaleQuoteError(
                f"quote for {broker_symbol!r} is {age:.1f}s old (maximum allowed {self._max_quote_age:.1f}s)"
            )
        return Quote(symbol=broker_symbol, bid=bid, ask=ask, time=tick_dt, received_at=now)

    def get_bars(self, broker_symbol: str, timeframe: str, count: int, *, closed_only: bool = True) -> list[Bar]:
        if timeframe not in _TF_MAP_KEYS:
            raise FeedError(f"unknown timeframe {timeframe!r}; allowed: {_TF_MAP_KEYS}")
        mt5 = self._ensure_connected()
        tf = self._timeframe_const(timeframe)
        request_count = count + 1 if closed_only else count
        rates = mt5.copy_rates_from_pos(broker_symbol, tf, 0, request_count)
        if rates is None:
            raise FeedError(f"bar read failed for {broker_symbol!r} {timeframe}: {mt5.last_error()}")
        rows = rates[1:] if closed_only else rates
        bars: list[Bar] = []
        for r in rows:
            bars.append(
                Bar(
                    symbol=broker_symbol,
                    timeframe=timeframe,
                    time=datetime.fromtimestamp(int(r["time"]), tz=UTC),
                    open=float(r["open"]),
                    high=float(r["high"]),
                    low=float(r["low"]),
                    close=float(r["close"]),
                    tick_volume=int(r["tick_volume"]),
                    closed=True,  # forming bar is excluded above
                )
            )
        return bars

    # ------------------------------------------------------------ account

    def get_account(self, expected_mode: AccountMode | None = None) -> "Any":
        from xauusd.domain import AccountSnapshot

        mt5 = self._ensure_connected()
        info = mt5.account_info()
        if info is None:
            raise TerminalDisconnectedError("account_info unavailable; terminal not logged in?")
        trade_mode = int(getattr(info, "trade_mode", -1))
        mode = _ACCOUNT_MODE_MAP.get(trade_mode)
        if mode is None:
            raise FeedError(f"unsupported account trade_mode {trade_mode} (contest?)")
        want_mode = expected_mode if expected_mode is not None else self._expected_mode
        if want_mode is not None and mode is not want_mode:
            raise AccountMismatchError(
                f"terminal account is {mode.value} but {want_mode.value} was expected; "
                "demo/live mismatch blocks execution"
            )
        if self._expected_login is not None and int(info.login) != int(self._expected_login):
            raise AccountMismatchError(
                f"wrong account: login {mask_login(int(info.login))} does not match expected "
                f"{mask_login(self._expected_login)}"
            )
        margin_level = getattr(info, "margin_level", 0.0) or 0.0
        positions = mt5.positions_total()
        orders = mt5.orders_total()
        return AccountSnapshot(
            login=int(info.login),
            server=str(getattr(info, "server", "") or ""),
            company=str(getattr(info, "company", "") or ""),
            currency=str(getattr(info, "currency", "") or ""),
            mode=mode,
            balance=float(info.balance),
            equity=float(info.equity),
            margin=float(getattr(info, "margin", 0.0) or 0.0),
            margin_free=float(getattr(info, "margin_free", 0.0) or 0.0),
            margin_level=float(margin_level) if math.isfinite(float(margin_level)) else None,
            leverage=int(getattr(info, "leverage", 0) or 0),
            open_positions=int(positions or 0),
            pending_orders=int(orders or 0),
            server_time_utc=datetime.fromtimestamp(int(getattr(info, "time", 0) or 0), tz=UTC),
            snapshot_at=self._now_fn(),
        )

    # ------------------------------------------------------------ reporting

    def capability_report(self, canonical: str, *, exact: str | None = None) -> dict[str, Any]:
        """Redacted broker capability report (account mode, economics, sessions)."""
        mt5 = self._ensure_connected()
        broker_symbol = self.resolve_symbol(canonical, exact=exact)
        spec = self.get_symbol_spec(broker_symbol)
        account = self.get_account(expected_mode=self._expected_mode)
        ti = mt5.terminal_info()
        version = mt5.version()
        info = mt5.symbol_info(broker_symbol)
        now = self._now_fn()
        skew = (account.server_time_utc - now).total_seconds()
        session_deals = getattr(info, "session_deals", None)
        session_quotes = getattr(info, "session_quotes", None)
        return {
            "generated_at_utc": now.isoformat(),
            "read_only": True,
            "canonical": canonical,
            "broker_symbol": broker_symbol,
            "account_mode": account.mode.value,
            "login_masked": mask_login(account.login),
            "server": account.server,
            "company": account.company,
            "currency": account.currency,
            "leverage": account.leverage,
            "terminal": {
                "name": getattr(ti, "name", "") if ti else "",
                "connected": bool(getattr(ti, "connected", False)) if ti else False,
                "version": str(version[0]) if version else "",
                "build": int(version[1]) if version else 0,
            },
            "clock": {
                "server_time_utc": account.server_time_utc.isoformat(),
                "approx_server_skew_seconds": round(skew, 1),
            },
            "symbol_economics": {
                "description": spec.description,
                "digits": spec.digits,
                "point": str(spec.point),
                "tick_size": str(spec.tick_size),
                "tick_value": str(spec.tick_value),
                "volume_min": str(spec.volume_min),
                "volume_step": str(spec.volume_step),
                "volume_max": str(spec.volume_max),
                "stops_level_points": spec.stops_level_points,
                "freeze_level_points": spec.freeze_level_points,
                "trade_allowed": spec.trade_allowed,
                "trade_calc_mode": int(getattr(info, "trade_calc_mode", 0) or 0),
                "trade_exec": int(getattr(info, "trade_exec", 0) or 0),
                "filling_mode_mask": int(getattr(info, "filling_mode", 0) or 0),
                "swap_long": float(getattr(info, "swap_long", 0.0) or 0.0),
                "swap_short": float(getattr(info, "swap_short", 0.0) or 0.0),
                "session_deals_count": int(len(session_deals)) if session_deals is not None else 0,
                "session_quotes_count": int(len(session_quotes)) if session_quotes is not None else 0,
                "path": str(getattr(info, "path", "") or ""),
            },
        }


def repr_float_as_decimal(value: float) -> Any:
    """Convert a broker float to Decimal via its shortest repr (avoids binary noise)."""
    from decimal import Decimal

    return Decimal(repr(float(value)))

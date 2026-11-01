"""Validated domain contracts for xauusd-assistant (PLAN.md section 4).

Rules enforced here:
- strict models: extra fields forbidden, models frozen (immutable decisions)
- all timestamps timezone-aware and normalized to UTC (naive datetimes rejected)
- all floats finite (NaN/inf rejected)
- broker-boundary prices and volumes use Decimal
- invariants: quote geometry, candidate stop geometry, decision volume rules,
  order intent mode gating, order event fill consistency, unvalidated forecasts
  cannot present numerical confidence.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

UTC = timezone.utc

TIMEFRAMES = ("M1", "M5", "M15", "H1", "H4")


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


def ensure_utc(value: datetime | None, field_name: str) -> datetime | None:
    """Normalize to UTC; reject naive datetimes."""
    if value is None:
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{field_name} must be timezone-aware (naive datetime rejected)")
    return value.astimezone(UTC)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def _utc_validator(*names: str) -> Callable:
    def _inner(v: datetime | None, info: ValidationInfo) -> datetime | None:
        return ensure_utc(v, info.field_name or "<datetime>")

    return field_validator(*names)(_inner)


def _finite_field(**kwargs: Any) -> Any:
    kwargs.setdefault("allow_inf_nan", False)
    return Field(**kwargs)


# ------------------------------------------------------------------ enums


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"
    NO_TRADE = "NO_TRADE"
    EXIT = "EXIT"


class AccountMode(str, Enum):
    ADVISORY = "advisory"
    PAPER = "paper"
    DEMO = "demo"
    LIVE = "live"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class EntryType(str, Enum):
    MARKET = "MARKET"
    STOP = "STOP"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    SUBMITTED = "SUBMITTED"  # accepted by broker layer, fill not yet confirmed
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"  # outcome unknown; never a permission to resubmit
    UNKNOWN = "UNKNOWN"


class SourceStatus(str, Enum):
    OK = "ok"
    STALE = "stale"
    MISSING = "missing"
    ERROR = "error"


class Importance(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ------------------------------------------------------------------ market data


class Quote(StrictModel):
    symbol: str = Field(min_length=1)
    bid: float = _finite_field(gt=0)
    ask: float = _finite_field(gt=0)
    time: datetime
    received_at: datetime

    _utc = _utc_validator("time", "received_at")

    @model_validator(mode="after")
    def _no_crossed_quotes(self) -> "Quote":
        if self.bid > self.ask:
            raise ValueError(f"crossed quotes rejected: bid {self.bid} > ask {self.ask}")
        return self


class Bar(StrictModel):
    symbol: str = Field(min_length=1)
    timeframe: str
    time: datetime  # bar open time, UTC
    open: float = _finite_field(gt=0)
    high: float = _finite_field(gt=0)
    low: float = _finite_field(gt=0)
    close: float = _finite_field(gt=0)
    tick_volume: int = Field(ge=0)
    closed: bool = True

    _utc = _utc_validator("time")

    @field_validator("timeframe")
    @classmethod
    def _known_timeframe(cls, v: str) -> str:
        if v not in TIMEFRAMES:
            raise ValueError(f"unknown timeframe {v!r}; allowed: {TIMEFRAMES}")
        return v

    @model_validator(mode="after")
    def _candle_consistency(self) -> "Bar":
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("inconsistent candle: low/high must bound open and close")
        if self.low > self.high:
            raise ValueError("inconsistent candle: low > high")
        return self


class SymbolSpec(StrictModel):
    """Broker-verified contract economics. Discovered at connection (Task 2)."""

    symbol: str = Field(min_length=1)
    description: str = ""
    currency_profit: str = Field(min_length=3)
    digits: int = Field(ge=0, le=10)
    point: Decimal = Field(gt=0)
    tick_size: Decimal = Field(gt=0)
    tick_value: Decimal = Field(gt=0)
    volume_step: Decimal = Field(gt=0)
    volume_min: Decimal = Field(gt=0)
    volume_max: Decimal = Field(gt=0)
    stops_level_points: int = Field(ge=0)
    freeze_level_points: int = Field(ge=0)
    trade_allowed: bool = False  # read-only until verified
    discovered_at: datetime

    _utc = _utc_validator("discovered_at")

    @model_validator(mode="after")
    def _volume_bounds(self) -> "SymbolSpec":
        if self.volume_min > self.volume_max:
            raise ValueError("volume_min exceeds volume_max")
        return self


class SourceHealth(StrictModel):
    name: str = Field(min_length=1)
    status: SourceStatus
    last_success_utc: datetime | None = None
    detail: str = ""

    _utc = _utc_validator("last_success_utc")


class MarketEvent(StrictModel):
    """Scheduled market event with point-in-time availability of actuals."""

    event_id: str = Field(min_length=1)
    title: str
    currency: str = "USD"
    importance: Importance
    scheduled_utc: datetime
    actual_utc: datetime | None = None  # when the actual value became available
    actual_value: float | None = _finite_field(default=None)
    consensus: float | None = _finite_field(default=None)
    source: str = ""

    _utc = _utc_validator("scheduled_utc", "actual_utc")

    @model_validator(mode="after")
    def _actual_requires_timestamp(self) -> "MarketEvent":
        if self.actual_value is not None and self.actual_utc is None:
            raise ValueError("actual_value without actual_utc would leak future knowledge")
        return self


# ------------------------------------------------------------------ account


class AccountSnapshot(StrictModel):
    login: int = Field(gt=0)
    server: str = Field(min_length=1)
    company: str = ""
    currency: str = Field(min_length=3, max_length=8)
    mode: AccountMode
    balance: float = _finite_field()
    equity: float = _finite_field()
    margin: float = _finite_field(ge=0)
    margin_free: float = _finite_field()
    margin_level: float | None = _finite_field(default=None)
    leverage: int = Field(ge=0)
    open_positions: int = Field(ge=0)
    pending_orders: int = Field(ge=0)
    server_time_utc: datetime | None = None  # None when the terminal does not report it (e.g. time==0)
    snapshot_at: datetime

    _utc = _utc_validator("server_time_utc", "snapshot_at")


# ------------------------------------------------------------------ strategy / risk


class Candidate(StrictModel):
    """A setup proposed by the strategy. Not an order."""

    candidate_id: str = Field(default_factory=new_id)
    symbol: str = Field(min_length=1)
    side: Side
    entry_type: EntryType = EntryType.MARKET
    entry: float = _finite_field(gt=0)  # reference executable price (ask for BUY, bid for SELL)
    reference_bid: float = _finite_field(gt=0)
    reference_ask: float = _finite_field(gt=0)
    stop: float = _finite_field(gt=0)
    target: float = _finite_field(gt=0)
    atr: float = _finite_field(gt=0)
    expiry: datetime
    created_at: datetime
    strategy_version: str = Field(min_length=1)
    trigger_note: str = ""

    _utc = _utc_validator("expiry", "created_at")

    @model_validator(mode="after")
    def _expiry_after_created(self) -> "Candidate":
        if self.expiry <= self.created_at:
            raise ValueError(f"candidate expiry {self.expiry} must be after created_at {self.created_at}")
        return self

    @model_validator(mode="after")
    def _stop_geometry(self) -> "Candidate":
        if self.side is Side.BUY and not (self.stop < self.entry < self.target):
            raise ValueError(
                f"invalid BUY stop geometry: require stop {self.stop} < entry {self.entry} < target {self.target}"
            )
        if self.side is Side.SELL and not (self.target < self.entry < self.stop):
            raise ValueError(
                f"invalid SELL stop geometry: require target {self.target} < entry {self.entry} < stop {self.stop}"
            )
        return self


class Forecast(StrictModel):
    """Optional prediction attached to a candidate. Numerical confidence only
    when validated (PLAN.md sections 2 and 7)."""

    horizon_bars: int = Field(gt=0)
    outcome: str = Field(min_length=1)  # explicit outcome definition
    probability_target_first: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    timeout_probability: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    quantiles: dict[str, float] | None = None
    calibration_note: str = ""
    model_version: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    validated: bool = False
    limitations: list[str] = Field(default_factory=list)
    created_at: datetime

    _utc = _utc_validator("created_at")

    @model_validator(mode="after")
    def _no_unvalidated_confidence(self) -> "Forecast":
        if not self.validated:
            shown = [
                name
                for name in ("probability_target_first", "timeout_probability", "quantiles")
                if getattr(self, name) is not None
            ]
            if shown:
                raise ValueError(f"unvalidated forecast cannot present confidence values: {', '.join(shown)}")
        return self


class Decision(StrictModel):
    """The single orchestrator output. Immutable once created."""

    decision_id: str = Field(default_factory=new_id)
    created_at: datetime
    symbol: str = Field(min_length=1)
    action: Action
    mode: AccountMode
    candidate: Candidate | None = None
    lots: Decimal | None = Field(default=None, gt=0)
    cash_risk: float | None = _finite_field(default=None, gt=0)  # planned loss at stop, deposit currency
    risk_pct_equity: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    reasons: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)  # machine gate codes
    strategy_version: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    config_version: str = Field(min_length=1)
    quote_bid: float | None = _finite_field(default=None, gt=0)
    quote_ask: float | None = _finite_field(default=None, gt=0)
    quote_time: datetime | None = None
    position_ticket: int | None = None  # required for EXIT

    _utc = _utc_validator("created_at", "quote_time")

    @model_validator(mode="after")
    def _action_volume_consistency(self) -> "Decision":
        if self.action in (Action.BUY, Action.SELL):
            missing = [
                name
                for name, value in (
                    ("candidate", self.candidate),
                    ("lots", self.lots),
                    ("cash_risk", self.cash_risk),
                )
                if value is None
            ]
            if missing:
                raise ValueError(f"{self.action.value} decision requires: {', '.join(missing)}")
        elif self.action in (Action.WAIT, Action.NO_TRADE):
            if self.lots is not None or self.cash_risk is not None or self.risk_pct_equity is not None:
                raise ValueError(f"{self.action.value} must carry zero executable volume (lots/cash_risk/risk_pct must be None)")
        elif self.action is Action.EXIT:
            if self.position_ticket is None:
                raise ValueError("EXIT decision requires position_ticket of the reconciled position")
            if self.lots is not None or self.cash_risk is not None:
                raise ValueError("EXIT must not introduce new planned risk: lots/cash_risk must be None")
        return self


# ------------------------------------------------------------------ orders


class OrderIntent(StrictModel):
    """An order derived from an accepted Decision. Advisory mode never produces one."""

    intent_id: str = Field(default_factory=new_id)
    decision_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: Side
    order_type: EntryType
    volume: Decimal = Field(gt=0)
    price: Decimal | None = Field(default=None, gt=0)  # required for STOP/LIMIT, None for MARKET
    reference_price: Decimal | None = Field(default=None, gt=0)  # decision-time executable reference
    stop_loss: Decimal = Field(gt=0)
    take_profit: Decimal = Field(gt=0)
    created_at: datetime
    expires_at: datetime | None = None
    mode: AccountMode
    strategy_version: str = Field(min_length=1)
    magic: int = Field(ge=0, default=0)

    _utc = _utc_validator("created_at", "expires_at")

    @model_validator(mode="after")
    def _intent_rules(self) -> "OrderIntent":
        if self.mode is AccountMode.ADVISORY:
            raise ValueError("advisory mode never produces order intents")
        if self.order_type is EntryType.MARKET and self.price is not None:
            raise ValueError("MARKET intent must not carry a price")
        if self.order_type in (EntryType.STOP, EntryType.LIMIT) and self.price is None:
            raise ValueError(f"{self.order_type.value} intent requires a price")
        if self.reference_price is not None:
            ref = float(self.reference_price)
            sl, tp = float(self.stop_loss), float(self.take_profit)
            if self.side is Side.BUY and not (sl < ref < tp):
                raise ValueError(f"invalid BUY order geometry: require stop {sl} < reference {ref} < target {tp}")
            if self.side is Side.SELL and not (tp < ref < sl):
                raise ValueError(f"invalid SELL order geometry: require target {tp} < reference {ref} < stop {sl}")
        return self


class OrderEvent(StrictModel):
    """Traceable broker-side outcome of an intent."""

    event_id: str = Field(default_factory=new_id)
    intent_id: str = Field(min_length=1)
    ticket: int | None = None
    retcode: int
    retcode_name: str = ""
    status: OrderStatus
    requested_volume: Decimal = Field(gt=0)
    filled_volume: Decimal = Field(default=Decimal("0"), ge=0)
    avg_fill_price: Decimal | None = Field(default=None, gt=0)
    event_time: datetime
    raw: dict[str, Any] | None = None  # redacted upstream before storage

    _utc = _utc_validator("event_time")

    @model_validator(mode="after")
    def _fill_consistency(self) -> "OrderEvent":
        if self.status is OrderStatus.FILLED:
            if self.filled_volume != self.requested_volume or self.avg_fill_price is None:
                raise ValueError("FILLED requires filled_volume == requested_volume and an average fill price")
        elif self.status is OrderStatus.PARTIAL:
            if not (Decimal("0") < self.filled_volume < self.requested_volume) or self.avg_fill_price is None:
                raise ValueError("PARTIAL requires 0 < filled < requested and an average fill price")
        elif self.status in (OrderStatus.REJECTED, OrderStatus.CANCELLED, OrderStatus.TIMEOUT, OrderStatus.UNKNOWN, OrderStatus.SUBMITTED):
            if self.filled_volume != 0:
                raise ValueError(f"{self.status.value} must carry zero filled volume")
        return self


# ------------------------------------------------------------------ aggregate snapshot


class MarketSnapshot(StrictModel):
    """Everything the deterministic pipeline reads. No network calls downstream."""

    symbol: str = Field(min_length=1)
    quote: Quote
    bars: dict[str, list[Bar]]
    symbol_spec: SymbolSpec
    account: AccountSnapshot
    source_health: list[SourceHealth] = Field(default_factory=list)
    events: list[MarketEvent] = Field(default_factory=list)
    dataset_version: str = Field(min_length=1)
    snapshot_at: datetime

    _utc = _utc_validator("snapshot_at")

    @field_validator("bars")
    @classmethod
    def _known_bar_keys(cls, v: dict[str, list[Bar]]) -> dict[str, list[Bar]]:
        bad = [k for k in v if k not in TIMEFRAMES]
        if bad:
            raise ValueError(f"unknown bar timeframe keys: {bad}; allowed: {TIMEFRAMES}")
        return v

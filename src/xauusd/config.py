"""Configuration, mode gating and account allowlists (PLAN.md Task 1).

Defaults are advisory. Live mode is doubly locked:
1. the YAML/config must contain a complete ``live_approval`` block, and
2. the process must be started with an explicit ``enable_live=True`` flag,
   so a config file alone can never silently enable live trading.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from xauusd.domain import AccountMode, ensure_utc


class ConfigError(Exception):
    """Raised for invalid configuration or unauthorized startup modes."""


class RiskLimits(BaseModel):
    """Research defaults from PLAN.md section 6 - require owner review."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    planned_risk_per_trade: float = Field(default=0.0025, gt=0, lt=1, allow_inf_nan=False)
    aggregate_open_risk: float = Field(default=0.005, gt=0, lt=1, allow_inf_nan=False)
    broker_day_loss_halt: float = Field(default=0.01, gt=0, lt=1, allow_inf_nan=False)
    equity_drawdown_halt: float = Field(default=0.05, gt=0, lt=1, allow_inf_nan=False)
    free_margin_reserve: float = Field(default=0.50, gt=0, le=1, allow_inf_nan=False)
    max_fills_per_broker_day: int = Field(default=3, ge=1)
    pause_after_consecutive_losses: int = Field(default=2, ge=1)


class EventGates(BaseModel):
    """Entry block windows in minutes (PLAN.md section 5, rules 8-9)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    high_impact_block_minutes_before: int = Field(default=30, ge=0)
    high_impact_block_minutes_after: int = Field(default=30, ge=0)
    fomc_block_minutes_before: int = Field(default=30, ge=0)
    fomc_conference_buffer_minutes: int = Field(default=30, ge=0)
    daily_rollover_block_minutes: int = Field(default=30, ge=0)
    weekly_close_block_minutes: int = Field(default=60, ge=0)
    exit_before_rollover_minutes: int = Field(default=15, ge=0)
    exit_before_weekly_close_minutes: int = Field(default=15, ge=0)


class PathsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data_dir: Path = Path("var/data")
    journal_dir: Path = Path("var/journal")
    reports_dir: Path = Path("var/reports")


class LiveApproval(BaseModel):
    """Owner-authorized live trading packet. Absent/empty = live locked."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    approved_accounts: list[int] = Field(default_factory=list)
    strategy_version: str = ""
    max_cash_risk_per_trade: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    max_aggregate_cash_risk: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    approved_at: datetime | None = None
    note: str = ""

    @field_validator("approved_at")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        return ensure_utc(v, "approved_at")


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: AccountMode = AccountMode.ADVISORY
    config_version: str = "0.1.0"
    strategy_version: str = "ema-pullback-r1"
    dataset_version: str = "unversioned"
    symbol_map: dict[str, str] = Field(default_factory=dict)
    risk: RiskLimits = Field(default_factory=RiskLimits)
    events: EventGates = Field(default_factory=EventGates)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    live_approval: LiveApproval | None = None

    @model_validator(mode="after")
    def _symbol_map_values(self) -> "Config":
        for canonical, broker in self.symbol_map.items():
            if not broker or not broker.strip():
                raise ValueError(f"symbol_map entry for {canonical!r} is empty; exact broker symbol required")
        return self


def config_from_dict(data: dict[str, Any]) -> Config:
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration: {exc}") from exc


def load_config(path: Path | str | None = None) -> Config:
    """Load and validate a YAML config file. Defaults to config/default.yaml."""
    p = Path(path) if path is not None else Path("config/default.yaml")
    if not p.exists():
        raise ConfigError(f"config file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"config file is not valid YAML: {p}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"config file must contain a YAML mapping: {p}")
    return config_from_dict(raw)


def startup_live_lock(
    config: Config,
    *,
    enable_live: bool = False,
    login: int | None = None,
) -> None:
    """Refuse to start unless live mode is explicitly and validly authorized.

    Called at process startup. Non-live modes return immediately; a config
    that merely *says* live is not enough - the explicit flag, the account
    allowlist, matching strategy version and cash risk limits are all
    required. This is what prevents silent re-enabling of live mode.
    """
    if config.mode is not AccountMode.LIVE:
        return
    if not enable_live:
        raise ConfigError(
            "config requests LIVE mode but the explicit startup enable flag is not set; "
            "refusing to enable live trading silently"
        )
    approval = config.live_approval
    if approval is None or not approval.approved_accounts:
        raise ConfigError(
            "live mode requires a live_approval block with a non-empty account allowlist; "
            "the live allowlist is empty"
        )
    if login is None:
        raise ConfigError("live mode requires the actual account login to be verified against the allowlist")
    if login not in approval.approved_accounts:
        raise ConfigError(f"account {login} is not on the live allowlist")
    if approval.strategy_version != config.strategy_version:
        raise ConfigError(
            f"live approval strategy version {approval.strategy_version!r} does not match configured "
            f"strategy version {config.strategy_version!r}"
        )
    if approval.max_cash_risk_per_trade is None or approval.max_aggregate_cash_risk is None:
        raise ConfigError("live approval must state explicit cash risk limits")
    if approval.approved_at is None:
        raise ConfigError("live approval must carry an approval timestamp")


def resolve_broker_symbol(config: Config, canonical: str) -> str:
    """Map a canonical instrument to the exact broker symbol (suffix included)."""
    mapped = config.symbol_map.get(canonical)
    if not mapped:
        raise ConfigError(
            f"symbol {canonical!r} is not mapped to an exact broker symbol; "
            "add it to config symbol_map after broker metadata discovery"
        )
    return mapped

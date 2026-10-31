"""External provider adapters: calendar / macro / news (PLAN.md Task 3).

No licensed provider is connected yet (purchase approval is an owner gate).
This module defines the adapter contract, a deterministic in-memory provider
for tests and offline development, calendar normalization, heartbeat-based
coverage gates and point-in-time event views.

Rules:
- Required sources (the economic calendar) must have a heartbeat within
  ``max_heartbeat_minutes`` or new entries are blocked.
- Optional sources are labelled when missing/stale; they never block.
- Calendar actuals become visible only at/after their release timestamp.
- News text is untrusted input and is never treated as complete coverage.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Protocol

from pydantic import Field, ValidationInfo, field_validator, model_validator

from xauusd.domain import Importance, StrictModel, SourceStatus, ensure_utc, utc_now


class ProviderError(Exception):
    """Raised when a provider payload cannot be normalized safely."""


class CalendarRecord(StrictModel):
    event_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    currency: str = "USD"
    importance: Importance
    scheduled_utc: datetime
    actual_utc: datetime | None = None
    actual_value: float | None = Field(default=None, allow_inf_nan=False)
    consensus: float | None = Field(default=None, allow_inf_nan=False)
    source: str = ""
    ingested_at: datetime

    @field_validator("scheduled_utc", "actual_utc", "ingested_at")
    @classmethod
    def _utc_fields(cls, v: datetime | None, info: ValidationInfo) -> datetime | None:
        return ensure_utc(v, info.field_name or "<datetime>")

    @model_validator(mode="after")
    def _actual_requires_timestamp(self) -> "CalendarRecord":
        if self.actual_value is not None and self.actual_utc is None:
            raise ValueError("actual_value without actual_utc would leak future knowledge")
        return self


class ProviderStatus(StrictModel):
    name: str
    kind: str  # "required" | "optional"
    status: SourceStatus
    last_success_utc: datetime | None = None
    detail: str = ""

    @field_validator("last_success_utc")
    @classmethod
    def _utc_fields(cls, v: datetime | None, info: ValidationInfo) -> datetime | None:
        return ensure_utc(v, info.field_name or "<datetime>")


class GateReport(StrictModel):
    blockers: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)


class CalendarProvider(Protocol):
    def fetch(self, now: datetime) -> list[CalendarRecord]: ...


class StaticProvider:
    """Deterministic provider returning fixed records (tests / offline dev)."""

    def __init__(self, records: list[CalendarRecord]) -> None:
        self._records = list(records)

    def fetch(self, now: datetime) -> list[CalendarRecord]:
        return list(self._records)


class ProviderService:
    """Wraps a provider with heartbeat tracking and coverage status."""

    def __init__(
        self,
        *,
        name: str,
        kind: str,
        provider: CalendarProvider,
        max_heartbeat_minutes: float = 5.0,
        now_fn: Callable[[], datetime] = utc_now,
    ) -> None:
        if kind not in ("required", "optional"):
            raise ValueError(f"provider kind must be 'required' or 'optional', got {kind!r}")
        self.name = name
        self.kind = kind
        self._provider = provider
        self._max_heartbeat = max_heartbeat_minutes
        self._now_fn = now_fn
        self._last_success: datetime | None = None
        self._last_error: str = ""

    def refresh(self, now: datetime | None = None) -> list[CalendarRecord]:
        now = now or self._now_fn()
        try:
            records = self._provider.fetch(now)
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            return []
        self._last_success = now
        self._last_error = ""
        return records

    def status(self, now: datetime | None = None) -> ProviderStatus:
        now = now or self._now_fn()
        if self._last_success is None:
            if self._last_error:
                status, detail = SourceStatus.ERROR, self._last_error
            else:
                status, detail = SourceStatus.MISSING, "never fetched"
        elif (now - self._last_success).total_seconds() <= self._max_heartbeat * 60:
            status, detail = SourceStatus.OK, ""
        else:
            status, detail = SourceStatus.STALE, f"last success {self._last_success.isoformat()}"
        return ProviderStatus(
            name=self.name,
            kind=self.kind,
            status=status,
            last_success_utc=self._last_success,
            detail=detail,
        )


class CalendarService(ProviderService):
    """Required economic-calendar service with point-in-time event views."""

    def __init__(
        self,
        provider: CalendarProvider,
        *,
        max_heartbeat_minutes: float = 5.0,
        now_fn: Callable[[], datetime] = utc_now,
    ) -> None:
        super().__init__(
            name="calendar",
            kind="required",
            provider=provider,
            max_heartbeat_minutes=max_heartbeat_minutes,
            now_fn=now_fn,
        )
        self._events: list[CalendarRecord] = []

    def refresh(self, now: datetime | None = None) -> list[CalendarRecord]:
        self._events = list(super().refresh(now))
        return self._events

    @property
    def events(self) -> list[CalendarRecord]:
        return list(self._events)

    def events_asof(self, as_of: datetime) -> list[CalendarRecord]:
        return strip_future_actuals(self._events, as_of)


def strip_future_actuals(events: list[CalendarRecord], as_of: datetime) -> list[CalendarRecord]:
    """Hide actuals that were not yet published at ``as_of``."""
    out: list[CalendarRecord] = []
    for event in events:
        if event.actual_utc is not None and event.actual_utc <= as_of:
            out.append(event)
        else:
            out.append(event.model_copy(update={"actual_utc": None, "actual_value": None}))
    return out


def gate_report(statuses: list[ProviderStatus]) -> GateReport:
    """Required-source problems become blockers; optional problems become labels."""
    blockers: list[str] = []
    labels: list[str] = []
    for status in statuses:
        if status.status is SourceStatus.OK:
            continue
        code = f"GATE_EVENT_COVERAGE:{status.name}({status.status.value})"
        if status.kind == "required":
            blockers.append(code)
        else:
            labels.append(f"OPTIONAL_{status.status.value.upper()}:{status.name}")
    return GateReport(blockers=blockers, labels=labels)


def normalize_calendar_row(
    row: dict[str, object],
    *,
    source: str,
    ingested_at: datetime,
) -> CalendarRecord:
    """Normalize a raw calendar payload row into a CalendarRecord.

    Timestamps must be ISO-8601 WITH an offset; naive timestamps are rejected
    because a silent timezone assumption would corrupt the event gate.
    """
    def parse_ts(key: str) -> datetime | None:
        raw = row.get(key)
        if raw is None or raw == "":
            return None
        if isinstance(raw, datetime):
            parsed = raw
        else:
            try:
                parsed = datetime.fromisoformat(str(raw))
            except ValueError as exc:
                raise ProviderError(f"calendar row field {key!r} is not ISO-8601: {raw!r}") from exc
        if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
            raise ProviderError(f"calendar row field {key!r} has a naive timestamp: {raw!r}")
        return parsed.astimezone(utc_now().tzinfo)

    importance_raw = str(row.get("importance", "")).strip().lower()
    try:
        importance = Importance(importance_raw)
    except ValueError as exc:
        raise ProviderError(f"calendar row has unknown importance {importance_raw!r}") from exc

    event_id = str(row.get("event_id", "")).strip()
    title = str(row.get("title", "")).strip()
    scheduled = parse_ts("scheduled_utc")
    if not event_id or not title or scheduled is None:
        raise ProviderError("calendar row requires event_id, title and scheduled_utc")

    return CalendarRecord(
        event_id=event_id,
        title=title,
        currency=str(row.get("currency", "USD")),
        importance=importance,
        scheduled_utc=scheduled,
        actual_utc=parse_ts("actual_utc"),
        actual_value=float(row["actual_value"]) if row.get("actual_value") is not None else None,  # type: ignore[arg-type]
        consensus=float(row["consensus"]) if row.get("consensus") is not None else None,  # type: ignore[arg-type]
        source=source,
        ingested_at=ingested_at,
    )

"""Provenant, versioned dataset store (PLAN.md Task 3).

Every observation carries: series name, value, units, frequency,
valid_from (event time), available_at (publication time), ingested_at,
revision, source and a sha256 of the immutable raw payload. Storage is
append-only Parquet (one file per series) plus a manifest.json whose content
hash yields the dataset version. Duplicate ingestion is a no-op (idempotent).

All reads used by decisions go through as-of joins by availability time, so a
revision published later can never leak into an earlier decision.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Callable

import pandas as pd
from pydantic import Field, ValidationInfo, field_validator, model_validator

from xauusd.domain import StrictModel, ensure_utc, utc_now

UTC = timezone.utc

_IDENTITY_COLUMNS = ("valid_from", "available_at", "revision", "raw_sha256")
_PROVENANCE_COLUMNS = (
    "value",
    "units",
    "frequency",
    "valid_from",
    "available_at",
    "ingested_at",
    "revision",
    "source",
    "raw_sha256",
    "raw_json",
)


def sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SeriesObservation(StrictModel):
    """One versioned value of a named external series."""

    series: str = Field(min_length=1)
    value: float | None = Field(default=None, allow_inf_nan=False)
    units: str = ""
    frequency: str = ""
    valid_from: datetime
    available_at: datetime
    ingested_at: datetime
    revision: int = Field(default=0, ge=0)
    source: str = ""
    raw_json: str = ""
    raw_sha256: str = ""

    @field_validator("valid_from", "available_at", "ingested_at")
    @classmethod
    def _utc_fields(cls, v: datetime | None, info: ValidationInfo) -> datetime | None:
        return ensure_utc(v, info.field_name or "<datetime>")

    @model_validator(mode="after")
    def _raw_hash_integrity(self) -> "SeriesObservation":
        if self.raw_json and self.raw_sha256 != sha256_hex(self.raw_json):
            raise ValueError("raw_sha256 does not match raw_json; immutable raw payload required")
        return self


class IngestReport(StrictModel):
    series: str
    added: int = Field(ge=0)
    skipped: int = Field(ge=0)


# ---------------------------------------------------------------- gap classification


class GapKind(str, Enum):
    WEEKEND_CLOSURE = "weekend_closure"
    WEEKLY_CLOSURE = "weekly_closure"
    SESSION_BREAK = "session_break"
    DST_TRANSITION = "dst_transition"
    DATA_OUTAGE = "data_outage"


class GapReport(StrictModel):
    start: datetime
    end: datetime
    duration_minutes: float = Field(gt=0)
    kind: GapKind

    @field_validator("start", "end")
    @classmethod
    def _utc_fields(cls, v: datetime | None, info: ValidationInfo) -> datetime | None:
        return ensure_utc(v, info.field_name or "<datetime>")


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def us_dst_transitions(year: int) -> list[datetime]:
    """US DST transitions as UTC instants: spring-forward 07:00 UTC (2nd Sun
    of March), fall-back 06:00 UTC (1st Sun of November)."""
    spring = _nth_weekday(year, 3, weekday=6, n=2)
    fall = _nth_weekday(year, 11, weekday=6, n=1)
    return [
        datetime(spring.year, spring.month, spring.day, 7, 0, tzinfo=UTC),
        datetime(fall.year, fall.month, fall.day, 6, 0, tzinfo=UTC),
    ]


def _classify_gap(start: datetime, end: datetime, duration_minutes: float) -> GapKind:
    for year in {start.year, end.year}:
        for transition in us_dst_transitions(year):
            if start <= transition + timedelta(hours=26) and end >= transition - timedelta(hours=26):
                return GapKind.DST_TRANSITION
    if start.weekday() == 4 and end.weekday() >= 5:  # Friday evening -> into the weekend
        return GapKind.WEEKLY_CLOSURE
    if start.weekday() >= 5 and end.weekday() >= 5:
        return GapKind.WEEKEND_CLOSURE
    if duration_minutes <= 180:
        return GapKind.SESSION_BREAK
    return GapKind.DATA_OUTAGE


def classify_gaps(bars: list[datetime], *, expected_interval_minutes: float) -> list[GapReport]:
    """Classify gaps in a bar timestamp series (weekend / DST / outage / break)."""
    if len(bars) < 2:
        return []
    ordered = sorted(set(bars))
    reports: list[GapReport] = []
    for prev, nxt in zip(ordered, ordered[1:]):
        gap_minutes = (nxt - prev).total_seconds() / 60.0
        if gap_minutes <= expected_interval_minutes * 1.5:
            continue
        reports.append(
            GapReport(
                start=prev,
                end=nxt,
                duration_minutes=round(gap_minutes, 1),
                kind=_classify_gap(prev, nxt, gap_minutes),
            )
        )
    return reports


# ---------------------------------------------------------------- store


def _record_to_row(record: SeriesObservation) -> dict[str, object]:
    return {
        "value": float(record.value) if record.value is not None else float("nan"),
        "units": record.units,
        "frequency": record.frequency,
        "valid_from": record.valid_from,
        "available_at": record.available_at,
        "ingested_at": record.ingested_at,
        "revision": record.revision,
        "source": record.source,
        "raw_sha256": record.raw_sha256,
        "raw_json": record.raw_json,
    }


def _row_to_record(series: str, row: pd.Series) -> SeriesObservation:
    value = row["value"]
    value_f = None if pd.isna(value) else float(value)
    return SeriesObservation(
        series=series,
        value=value_f,
        units=str(row["units"]),
        frequency=str(row["frequency"]),
        valid_from=row["valid_from"].to_pydatetime(),
        available_at=row["available_at"].to_pydatetime(),
        ingested_at=row["ingested_at"].to_pydatetime(),
        revision=int(row["revision"]),
        source=str(row["source"]),
        raw_json=str(row["raw_json"]),
        raw_sha256=str(row["raw_sha256"]),
    )


class DatasetStore:
    """Append-only per-series Parquet store with manifest and dataset version."""

    def __init__(self, root: Path, now_fn: Callable[[], datetime] = utc_now) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._now_fn = now_fn
        self._manifest: dict[str, object] = {}
        manifest_path = self._root / "manifest.json"
        if manifest_path.exists():
            self._manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    def _series_path(self, series: str) -> Path:
        return self._root / f"{series}.parquet"

    def _read(self, series: str) -> pd.DataFrame | None:
        path = self._series_path(series)
        if not path.exists():
            return None
        df = pd.read_parquet(path)
        for col in ("valid_from", "available_at", "ingested_at"):
            df[col] = pd.to_datetime(df[col], utc=True)
        return df

    def ingest_series(self, series: str, records: list[SeriesObservation]) -> IngestReport:
        if not records:
            return IngestReport(series=series, added=0, skipped=0)
        df_existing = self._read(series)
        existing_keys: set[tuple] = set()
        if df_existing is not None and not df_existing.empty:
            existing_keys = {
                (row["valid_from"], row["available_at"], int(row["revision"]), row["raw_sha256"])
                for _, row in df_existing.iterrows()
            }
        new_rows: list[dict[str, object]] = []
        skipped = 0
        for record in records:
            key = (record.valid_from, record.available_at, record.revision, record.raw_sha256)
            if key in existing_keys:
                skipped += 1
                continue
            existing_keys.add(key)
            new_rows.append(_record_to_row(record))
        added = len(new_rows)
        if added:
            df_new = pd.DataFrame(new_rows)
            df = pd.concat([df_existing, df_new], ignore_index=True) if df_existing is not None else df_new
            df = df.sort_values(["valid_from", "available_at", "revision"]).reset_index(drop=True)
            path = self._series_path(series)
            df.to_parquet(path, index=False)
            self._update_manifest(series, df)
        return IngestReport(series=series, added=added, skipped=skipped)

    def _update_manifest(self, series: str, df: pd.DataFrame) -> None:
        path = self._series_path(series)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        last_ingested = df["ingested_at"].max().to_pydatetime()
        series_info: dict[str, object] = {
            "rows": int(df.shape[0]),
            "sha256": sha,
            "last_ingested_at": last_ingested.isoformat(),
        }
        series_map = dict(self._manifest.get("series", {}))  # type: ignore[arg-type]
        series_map[series] = series_info
        self._manifest = {
            "series": series_map,
            "dataset_version": self._compute_version(series_map),
            "generated_at_utc": self._now_fn().isoformat(),
        }
        (self._root / "manifest.json").write_text(json.dumps(self._manifest, indent=2), encoding="utf-8")

    @staticmethod
    def _compute_version(series_map: dict[str, object]) -> str:
        h = hashlib.sha256()
        for name in sorted(series_map):
            info = series_map[name]
            h.update(f"{name}|{info['rows']}|{info['sha256']}\n".encode("utf-8"))
        return "ds-" + h.hexdigest()[:12]

    def read_series(self, series: str) -> pd.DataFrame:
        df = self._read(series)
        return df if df is not None else pd.DataFrame(columns=_PROVENANCE_COLUMNS)

    def series_asof(self, series: str, as_of: datetime) -> SeriesObservation | None:
        """Latest value that was *available* at ``as_of`` (point-in-time safe)."""
        df = self._read(series)
        if df is None or df.empty:
            return None
        df = df[df["available_at"] <= pd.Timestamp(as_of)]
        if df.empty:
            return None
        latest_valid = df["valid_from"].max()
        df = df[df["valid_from"] == latest_valid]
        df = df.sort_values(["available_at", "revision"])
        return _row_to_record(series, df.iloc[-1])

    def manifest(self) -> dict[str, object]:
        return dict(self._manifest)

    def dataset_version(self) -> str:
        version = self._manifest.get("dataset_version")
        return str(version) if version else "unversioned"

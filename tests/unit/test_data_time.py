"""Task 3 tests: point-in-time data, provenance store, provider gates.

Behavioral coverage per PLAN.md Task 3:
- as-of joins by availability time (revised inflation cannot leak into earlier
  decisions; calendar actuals appear only after release)
- provenance: source/availability/ingestion timestamps, revisions, units,
  frequencies, immutable raw hashes
- idempotent duplicate ingestion, reproducible manifest
- DST gaps classified; required-calendar outage blocks entries; optional
  missing data is labelled, never blocking
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from xauusd.data.providers import (
    CalendarRecord,
    CalendarService,
    GateReport,
    ProviderError,
    ProviderService,
    ProviderStatus,
    StaticProvider,
    gate_report,
    normalize_calendar_row,
    strip_future_actuals,
)
from xauusd.data.store import DatasetStore, GapKind, SeriesObservation, classify_gaps
from xauusd.domain import MarketEvent, SourceStatus

UTC = timezone.utc


def dt(y: int, m: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def obs(
    series: str = "us_cpi_yoy",
    value: float = 3.2,
    valid_from: datetime | None = None,
    available_at: datetime | None = None,
    revision: int = 0,
    units: str = "percent_yoy",
    frequency: str = "monthly",
    source: str = "test",
) -> SeriesObservation:
    raw = json.dumps({"series": series, "value": value, "revision": revision}, sort_keys=True)
    return SeriesObservation(
        series=series,
        value=value,
        units=units,
        frequency=frequency,
        valid_from=valid_from or dt(2026, 3, 1),
        available_at=available_at or dt(2026, 4, 10, 13, 30),
        ingested_at=available_at or dt(2026, 4, 10, 13, 30),
        revision=revision,
        source=source,
        raw_json=raw,
        raw_sha256=hashlib.sha256(raw.encode()).hexdigest(),
    )


# ---------------------------------------------------------------- as-of joins


def test_revised_inflation_cannot_leak_into_earlier_decisions(tmp_path: Path):
    store = DatasetStore(tmp_path)
    store.ingest_series(
        "us_cpi_yoy",
        [
            obs(value=3.2, valid_from=dt(2026, 3, 1), available_at=dt(2026, 4, 10, 13, 30), revision=0),
            obs(value=3.4, valid_from=dt(2026, 3, 1), available_at=dt(2026, 4, 25, 13, 30), revision=1),
        ],
    )
    at_release = store.series_asof("us_cpi_yoy", dt(2026, 4, 12))
    at_revision = store.series_asof("us_cpi_yoy", dt(2026, 4, 26))
    assert at_release is not None and at_release.value == pytest.approx(3.2)
    assert at_release.revision == 0
    assert at_revision is not None and at_revision.value == pytest.approx(3.4)
    assert at_revision.revision == 1


def test_asof_returns_latest_published_month_not_latest_revision_only(tmp_path: Path):
    store = DatasetStore(tmp_path)
    store.ingest_series(
        "us_cpi_yoy",
        [
            obs(value=3.5, valid_from=dt(2026, 2, 1), available_at=dt(2026, 3, 10), revision=0),
            obs(value=3.2, valid_from=dt(2026, 3, 1), available_at=dt(2026, 4, 10), revision=0),
        ],
    )
    got = store.series_asof("us_cpi_yoy", dt(2026, 4, 12))
    assert got is not None and got.value == pytest.approx(3.2), "most recent valid_from wins"


def test_asof_tie_prefers_higher_revision(tmp_path: Path):
    store = DatasetStore(tmp_path)
    store.ingest_series(
        "us_cpi_yoy",
        [
            obs(value=3.2, available_at=dt(2026, 4, 10), revision=0),
            obs(value=3.3, available_at=dt(2026, 4, 10), revision=1),
        ],
    )
    got = store.series_asof("us_cpi_yoy", dt(2026, 4, 11))
    assert got is not None and got.value == pytest.approx(3.3)


def test_asof_returns_none_before_anything_available(tmp_path: Path):
    store = DatasetStore(tmp_path)
    store.ingest_series("us_cpi_yoy", [obs(available_at=dt(2026, 4, 10))])
    assert store.series_asof("us_cpi_yoy", dt(2026, 4, 1)) is None


def test_calendar_actuals_appear_only_after_release():
    event = CalendarRecord(
        event_id="cpi-mar-2026",
        title="US CPI m/m",
        currency="USD",
        importance="high",
        scheduled_utc=dt(2026, 4, 10, 13, 30),
        actual_utc=dt(2026, 4, 10, 13, 32),
        actual_value=0.3,
        consensus=0.2,
        source="test-calendar",
        ingested_at=dt(2026, 4, 10, 13, 32),
    )
    before = strip_future_actuals([event], dt(2026, 4, 10, 13, 31))[0]
    after = strip_future_actuals([event], dt(2026, 4, 10, 13, 33))[0]
    assert before.actual_value is None and before.actual_utc is None
    assert after.actual_value == pytest.approx(0.3)
    # both must satisfy the domain contract (no future knowledge either way)
    MarketEvent(
        event_id=before.event_id,
        title=before.title,
        currency=before.currency,
        importance=before.importance,
        scheduled_utc=before.scheduled_utc,
        actual_utc=before.actual_utc,
        actual_value=before.actual_value,
        source=before.source,
    )


# ---------------------------------------------------------------- provenance


def test_store_persists_provenance_columns_and_raw_hash(tmp_path: Path):
    store = DatasetStore(tmp_path)
    record = obs()
    store.ingest_series("us_cpi_yoy", [record])
    df = store.read_series("us_cpi_yoy")
    for col in ("value", "units", "frequency", "valid_from", "available_at", "ingested_at", "revision", "source", "raw_sha256"):
        assert col in df.columns
    row = df.iloc[0]
    assert row["raw_sha256"] == hashlib.sha256(record.raw_json.encode()).hexdigest()
    assert row["revision"] == 0


def test_duplicate_ingestion_is_idempotent(tmp_path: Path):
    store = DatasetStore(tmp_path)
    records = [
        obs(value=3.2, available_at=dt(2026, 4, 10), revision=0),
        obs(value=3.4, available_at=dt(2026, 4, 25), revision=1),
    ]
    store.ingest_series("us_cpi_yoy", records)
    manifest1 = store.manifest()
    report = store.ingest_series("us_cpi_yoy", records)
    manifest2 = store.manifest()
    assert report.added == 0 and report.skipped == 2
    assert manifest1 == manifest2
    assert store.read_series("us_cpi_yoy").shape[0] == 2


def test_manifest_and_dataset_version_are_reproducible(tmp_path: Path):
    records = [obs(value=3.2, available_at=dt(2026, 4, 10), revision=0)]
    s1 = DatasetStore(tmp_path / "a")
    s1.ingest_series("us_cpi_yoy", records)
    s2 = DatasetStore(tmp_path / "b")
    s2.ingest_series("us_cpi_yoy", records)
    assert s1.dataset_version() == s2.dataset_version()
    assert s1.manifest()["series"]["us_cpi_yoy"]["rows"] == 1
    s1.ingest_series("us_cpi_yoy", [obs(value=3.4, available_at=dt(2026, 4, 25), revision=1)])
    assert s1.dataset_version() != s2.dataset_version()


# ---------------------------------------------------------------- gap classification


def hourly(*hours: tuple[int, int]) -> list[datetime]:
    return [dt(2026, m, d, h, mi) for (m, d, h, mi) in hours]


def test_dst_transition_gap_is_classified():
    # 2026-11-01 is the first Sunday of November (US fall-back, 06:00 UTC)
    bars = hourly((11, 1, 4, 0), (11, 1, 7, 0))
    gaps = classify_gaps(bars, expected_interval_minutes=60)
    kinds = [g.kind for g in gaps]
    assert GapKind.DST_TRANSITION in kinds


def test_weekend_closure_gap_is_classified():
    bars = [dt(2026, 10, 23, 21, 0), dt(2026, 10, 25, 22, 0)]
    gaps = classify_gaps(bars, expected_interval_minutes=60)
    assert gaps and gaps[0].kind is GapKind.WEEKLY_CLOSURE
    assert gaps[0].duration_minutes > 1000


def test_daily_session_break_is_classified():
    bars = [dt(2026, 10, 27, 23, 0), dt(2026, 10, 28, 0, 0)]
    gaps = classify_gaps(bars, expected_interval_minutes=15)
    assert gaps and gaps[0].kind is GapKind.SESSION_BREAK


def test_data_outage_gap_is_classified():
    bars = [dt(2026, 10, 27, 10, 0), dt(2026, 10, 27, 14, 0)]
    gaps = classify_gaps(bars, expected_interval_minutes=60)
    assert gaps and gaps[0].kind is GapKind.DATA_OUTAGE


def test_no_gaps_reported_when_series_is_contiguous():
    bars = [dt(2026, 10, 27, 10, 0), dt(2026, 10, 27, 11, 0), dt(2026, 10, 27, 12, 0)]
    assert classify_gaps(bars, expected_interval_minutes=60) == []


# ---------------------------------------------------------------- provider gates


def test_required_calendar_outage_blocks_entries():
    events = [
        CalendarRecord(
            event_id="nfp",
            title="Nonfarm payrolls",
            importance="high",
            scheduled_utc=dt(2026, 11, 6, 13, 30),
            source="test-calendar",
            ingested_at=dt(2026, 11, 6, 13, 30),
        )
    ]
    service = CalendarService(StaticProvider(events), max_heartbeat_minutes=5)
    now = dt(2026, 11, 6, 12, 0)
    service.refresh(now)
    report = gate_report([service.status(now)])
    assert report.blockers == []

    later = now + timedelta(minutes=10)  # heartbeat stale: no refresh since
    stale_report = gate_report([service.status(later)])
    assert any(b.startswith("GATE_EVENT_COVERAGE:calendar") for b in stale_report.blockers)


def test_required_calendar_fetch_error_blocks_entries():
    class Exploding:
        def fetch(self, now: datetime) -> list[CalendarRecord]:
            raise ProviderError("calendar upstream 503")

    service = CalendarService(Exploding(), max_heartbeat_minutes=5)
    now = dt(2026, 11, 6, 12, 0)
    service.refresh(now)
    report = gate_report([service.status(now)])
    assert report.blockers, "failed required fetch must block entries"
    assert service.status(now).status is SourceStatus.ERROR


def test_missing_optional_data_is_labelled_but_not_blocking():
    news = ProviderService(name="news", kind="optional", provider=StaticProvider([]), max_heartbeat_minutes=5)
    now = dt(2026, 11, 6, 12, 0)
    report = gate_report([news.status(now)])
    assert report.blockers == []
    assert "OPTIONAL_MISSING:news" in report.labels


def test_gate_report_model_is_strict():
    report = GateReport(blockers=["GATE_EVENT_COVERAGE:calendar(stale)"], labels=[])
    assert report.blockers and not report.labels


def test_normalize_calendar_row_requires_aware_timestamps():
    with pytest.raises(ProviderError):
        normalize_calendar_row(
            {
                "event_id": "x",
                "title": "CPI",
                "importance": "high",
                "scheduled_utc": "2026-04-10T13:30:00",  # naive: rejected
            },
            source="test",
            ingested_at=dt(2026, 4, 10, 13, 32),
        )
    record = normalize_calendar_row(
        {
            "event_id": "x",
            "title": "CPI",
            "importance": "high",
            "scheduled_utc": "2026-04-10T13:30:00+00:00",
            "actual_utc": "2026-04-10T13:32:00+00:00",
            "actual_value": 0.3,
        },
        source="test",
        ingested_at=dt(2026, 4, 10, 13, 32),
    )
    assert record.scheduled_utc.tzinfo is UTC
    assert record.actual_value == pytest.approx(0.3)


def test_provider_status_values_are_complete():
    st = ProviderStatus(name="calendar", kind="required", status="ok", last_success_utc=None, detail="")
    assert st.status is SourceStatus.OK

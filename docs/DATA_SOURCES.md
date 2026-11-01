# Data sources

Point-in-time discipline, licensing and coverage. Status values:
`in use` | `planned - approval required` | `planned - optional`. **No paid
provider has been purchased or connected.** Purchases require explicit owner
approval (PLAN.md section 3).

## Principles

1. Every stored observation keeps: source, availability/publication time,
   ingestion time, revision, units, frequency and a raw-content hash.
2. Decisions join data as-of availability time. A revised value can never
   leak into a decision made before the revision was published.
3. Optional sources that are missing are labelled visibly. A model that
   requires a missing source stops instead of fabricating substitutes.
4. Daily macro data is not intraday data: frequency and staleness travel
   with the value everywhere it is used.

## In use

| Source | Provides | Access | Notes / known quirks |
|---|---|---|---|
| MetaTrader 5 terminal (owner's broker) | bid/ask ticks, M1/M15/H1/H4 bars, account/positions/orders, contract economics, sessions, swap | local terminal, read-only Python API (`MetaTrader5` 5.0.6231) | Bar/tick timestamps arrive as epoch seconds rendered in the **server clock**; the capability report records `approx_server_skew_seconds` so consumers can audit drift. `tick_volume` is broker-local, **not** global traded volume. Quote freshness gate: age <= 5 s during trading. Forming bar is dropped by default. |

Verification evidence (2026-11-01, MetaQuotes-Demo demo account `***698`,
read-only INVESTOR session — least privilege):
`var/reports/capability_report_*.json`. Broker symbol verified as exactly
`XAUUSD` (no suffix). Contract economics: digits=2, tick 0.01, tick value
0.1 USD/lot, volume 0.01..100 step 0.01, trade_allowed=True, 100 M15 closed
bars readable. Known quirk: `account_info().time` is unset (0) on this
server, so `server_time_utc` is reported as `null` rather than a silent 1970
value; clock-skew monitoring must use tick timestamps while the market is
open. Login credentials live only in gitignored `var/secrets/`; nothing is
committed.

## Planned - required for entry gating (approval required)

| Source | Provides | Why required | Acquisition status |
|---|---|---|---|
| Economic calendar (high-impact US: CPI, payrolls, PCE, FOMC decision + conference) | scheduled/actual/consensus with publication timestamps | Event gate (PLAN.md section 5 rule 8). Without heartbeat coverage <= 5 min, entries must block. | not connected; provider selection + purchase approval pending owner decision |

Candidates to evaluate (documented before purchase): paid feeds with
licensable redistribution and explicit release timestamps (e.g. commercial
calendar APIs), or free official schedules (BEA/BLS/Fed) that require an
internal scraper with documented heartbeat and outage behavior. Selection
criteria: point-in-time release history, latency, licensing/redistribution,
retention, outage behavior, price.

## Planned - optional context (never fabricated)

| Source | Provides | Caveats | Status |
|---|---|---|---|
| Dollar index or documented proxy; nominal/real US yields | opportunity-cost context | daily frequency is not intraday; publication lag applies | not connected; optional |
| Inflation surprise / policy expectation series | repricing context | must be point-in-time (first-published values only) | not connected; optional |
| Verified headlines (geopolitical/financial) | shock alerts, explanation text | untrusted input; never a completeness claim | not connected; optional |
| ETF flows, central-bank demand, CFTC positioning | slow demand context | publication lags and revisions | not connected; optional |
| Exchange futures volume/open interest | participation context | contract rolls and basis; broker tick volume is not a substitute | not connected; optional |

## Gate behavior when coverage fails

| Situation | Behavior |
|---|---|
| Required calendar heartbeat stale / outage | new entries BLOCKED (`GATE_EVENT_COVERAGE`) |
| Optional source missing | labelled in decision evidence; model requiring it stops |
| Broker metadata unknown / calculation values zero | entries BLOCKED (`GATE_SYMBOL_ECONOMICS`) |
| Quote older than 5 s during trading | entries BLOCKED (`GATE_STALE_QUOTE`) |

## Historical data plan (Task 6)

- Seek >= 3 years of broker-compatible XAUUSD history (M15/H1/H4 + ticks if
  available) from the connected broker first; disclose shorter coverage.
- Store as Parquet under `var/data/` with dataset manifests (source, range,
  row counts, sha256) so replay runs are reproducible.
- Newest 20% reserved as untouched holdout; expanding-window folds before it,
  with 16-bar label-horizon purging (PLAN.md sections 7 and 9).

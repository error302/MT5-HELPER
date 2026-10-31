# XAUUSD MT5 Trading Assistant Implementation Plan

> **For agentic workers:** Use the executing-plans skill to implement this plan task by task. Track the checkboxes and provide evidence at each gate. This document authorizes development planning, not real-money trading or paid service purchases.

**Goal:** Build a gold trading assistant that explains BUY, SELL, WAIT, EXIT and NO TRADE decisions, calculates entries, stops, targets and lot sizes, and supports MT5 execution after validation and explicit live authorization.

**Architecture:** A Windows Python service reads the MT5 terminal and licensed external feeds. A deterministic strategy and risk engine produce structured decisions. A local dashboard explains decisions. Replay, paper, demo and live adapters share the same strategy logic but have distinct execution permissions.

**Tech stack:** Python 3.12 subject to official MetaTrader5 package compatibility verification; FastAPI, Pydantic, pandas, NumPy, scikit-learn, pytest, SQLite for operational state and Parquet for historical data. Pin verified dependency versions in a lockfile.

**Spec:** This document contains the product specification and implementation sequence. All proposed source paths belong to a NEW standalone project called `xauusd-assistant`. Do not integrate trading functionality into EDITORS-PRO. This Markdown file is a handoff artifact only.

## 1. Non-negotiable requirements

- No promise of accurate predictions, guaranteed profits, complete market knowledge or a predetermined win rate. Build measurable forecasts and evaluate them honestly.
- Default to read-only advisory mode. Separate advisory, paper, demo and live modes. Live mode requires explicit owner approval of account, strategy version and cash risk limits; startup must not silently re-enable it.
- Never request passwords in chat. Use terminal login or OS credential facilities; redact logs.
- Missing mandatory inputs, stale quotes, unknown symbol economics, unresolved order state or unavailable required event coverage blocks new entries.
- No martingale, loss-recovery doubling, unlimited averaging down or widening stops after entry.
- Stops and daily limits constrain planned risk; gaps and execution failures can produce larger losses.
- Synthetic, historical, prospective demo and actual live evidence must remain clearly distinguished.
- If no strategy passes validation, deliver a useful research/advisory tool with an explicit "no demonstrated tradable edge" label. Do not manufacture a profitable result.

## 2. User experience

The assistant must answer: Should I act now? At what entry? Where is the stop? Where do I exit? How much could I lose? Why does this setup qualify? What would invalidate it?

Every trade card includes:

| Field | Required content |
|---|---|
| Action | BUY, SELL, WAIT, NO TRADE or EXIT |
| Identity | Decision ID, exact broker symbol, account mode, strategy/model versions |
| Price context | Broker bid/ask, UTC timestamp, user's local timestamp, data health |
| Entry | Market/limit/stop type, allowed range, trigger, expiry |
| Protection | Stop price, structural reason, target, time exit, invalidation |
| Risk | Lots, planned cash loss and equity percentage, costs, net reward/risk |
| Evidence | Supporting and opposing factors, regime, upcoming events, missing feeds |
| Forecast | Defined horizon/outcome and validated probability, or "unvalidated research estimate" without numerical confidence |

WAIT means a setup is incomplete. NO TRADE means a data, event, market or risk gate failed. EXIT concerns a reconciled existing position. A signal is not an order; an accepted order is not a confirmed fill. Clearly show all three states separately.

Charts overlay the exact entry/stop/target associated with an immutable decision. Explain why the stop sits beyond invalidation and why a trade was rejected. Never hide NO TRADE to make the product appear active. Confirmation revalidates the current quote, expiry and risk; changed terms require a fresh decision.

Initial scope: intraday broker XAUUSD spot/CFD, M15 entries, H1 trend and H4 context. Broker suffixes must be explicitly mapped. Futures and CFDs are different instruments; do not silently substitute one for the other.

## 3. Market information and coverage

"Factor everything" becomes a documented input inventory, not a claim of omniscience. Unexpected news, private order flow and future policy decisions cannot be completely known.

| Input | Use | Requirements |
|---|---|---|
| Broker bid/ask ticks, closed M1/M15/H1/H4 bars | Executable prices, structure, volatility | Quote age <=5 seconds during trading; detect gaps and unfinished bars |
| Equity, positions, pending orders, margin | Exposure and sizing | Refresh immediately before submission and after every execution event |
| Contract, tick size/value, volume steps, stops/freeze levels, sessions, swap | Broker-correct execution | Discover at connection and refresh before order checks |
| CPI, payrolls, PCE, FOMC decisions/conferences and relevant Fed speeches | Scheduled event risk | Licensed calendar heartbeat <=5 minutes and explicit coverage checks |
| Dollar index or documented proxy, nominal/real US yields | Opportunity cost and dollar context | Preserve frequency, publication time and staleness; daily data is not intraday data |
| Inflation surprises and policy expectations | Repricing context | Actual/consensus values must have point-in-time release history |
| Verified geopolitical and financial headlines | Shock alerts and explanations | Publisher and ingestion timestamps; no claim of complete breaking-news coverage |
| ETF flows, central-bank demand, CFTC positioning | Slow demand/positioning context | Apply publication lags and revisions; never backfill future knowledge |
| Futures volume/open interest if licensed | Optional participation context | Identify contract rolls and basis; broker tick volume is not global traded volume |
| Spread, slippage, rollover, holidays and sessions | Liquidity and cost | Broker-specific measurements; UTC storage and named time zones for DST |

Gold relationships vary by regime. Do not hard-code "inflation up means buy" or "dollar down always means buy." Treat macro factors as candidate explanatory variables or risk gates and retain only empirically useful features.

Select providers before modeling. Document price, licensing, redistribution rights, latency, historical point-in-time coverage and outage behavior. Obtain approval before purchasing feeds. Optional missing sources must be visibly excluded; an active model requiring them must stop rather than fabricate substitutes.

## 4. Standalone project structure

```text
xauusd-assistant/
  README.md
  pyproject.toml
  config/default.yaml
  src/xauusd/domain.py             # validated contracts
  src/xauusd/config.py             # mode, limits, account allowlists
  src/xauusd/data/mt5_feed.py      # broker reads and metadata
  src/xauusd/data/providers.py     # calendar/news/macro adapters
  src/xauusd/data/store.py         # provenance and versioned datasets
  src/xauusd/features.py           # closed-bar indicators and regimes
  src/xauusd/strategy.py           # candidate generation
  src/xauusd/risk.py               # sizing and vetoes
  src/xauusd/prediction.py         # optional calibrated forecasts
  src/xauusd/decision.py           # orchestration
  src/xauusd/execution/paper.py    # realistic simulated execution
  src/xauusd/execution/mt5.py      # preflight, orders, reconciliation
  src/xauusd/research.py           # chronological evaluation
  src/xauusd/journal.py            # persistent audit events
  src/xauusd/service.py            # scheduler/watchdog/API
  src/xauusd/ui/                   # local dashboard
  tests/unit/
  tests/integration/
  tests/replay/
  docs/DATA_SOURCES.md
  docs/STRATEGY.md
  docs/MODEL_CARD.md
  docs/VALIDATION_REPORT.md
  docs/OPERATIONS.md
  docs/USER_GUIDE.md
```

Contracts in `domain.py`: Quote; SymbolSpec; AccountSnapshot; MarketSnapshot; Candidate; Forecast; Decision; OrderIntent; OrderEvent. Use strict validated models, finite values, timezone-aware UTC timestamps, explicit units/currencies and version IDs. Quantize prices and volumes with decimal arithmetic at broker boundaries.

Required interfaces:

```python
build_features(snapshot: MarketSnapshot) -> dict
generate_candidate(snapshot: MarketSnapshot, features: dict) -> Candidate | None
evaluate(snapshot: MarketSnapshot, candidate: Candidate | None) -> Decision
forecast(snapshot: MarketSnapshot, candidate: Candidate) -> Forecast | None
submit(intent: OrderIntent) -> OrderEvent
reconcile(account: AccountSnapshot) -> list[OrderEvent]
```

MarketSnapshot includes quotes, completed bars, source health, events, symbol specifications, account state and dataset version. Candidate includes side, entry, stop, target, expiry and strategy version. Decision includes action, candidate, lots, cash risk, reasons, blockers and immutable versions. Forecast includes horizon, outcome definition, probability/quantiles, calibration dates and limitations. Order events include intent ID, broker ticket, retcode, actual filled volume/price and event time.

Keep broker/network calls outside pure feature, strategy and risk functions. The UI and language model cannot bypass risk evaluation.

## 5. First strategy: a testable hypothesis

These rules are a research starting point, not a proven profitable strategy. Freeze them before evaluation. Begin with one setup so performance and errors are attributable.

1. Compute EMA20, EMA50 and Wilder ATR14 from completed bars with documented warmup. H1 BUY regime: EMA20 > EMA50, close > EMA50 and EMA50 > its value three closed H1 bars earlier. SELL is the inverse. Display H4 context initially without silently optimizing it into a filter.
2. BUY trigger: completed M15 low touches/crosses EMA20, close finishes above EMA20 and above the previous bar high. SELL: high touches/crosses EMA20, close below EMA20 and below the previous bar low.
3. Use next executable ask for BUY and bid for SELL, not the signal-bar close. Candidate expires after one M15 bar. Cancel if price moves >0.25 M15 ATR from the reference quote, regime changes or any gate fails.
4. BUY stop: lowest low of the last five closed M15 bars minus 0.2 ATR. SELL stop: highest high plus 0.2 ATR. Reject stop distances outside 0.8-3.0 ATR. Never squeeze a structural stop to fit a larger lot size.
5. Target: 2 times entry-to-stop distance. Reject if an already-known prior-day high/low or confirmed H1 pivot lies between entry and target. Pivots require two bars on each side and become available only after the second right-side bar closes.
6. Time exit after 16 M15 bars at the next executable quote. Baseline has no partial exits or trailing stop. Evaluate those later as separately versioned experiments.
7. One open position, at most three new fills per broker day, no re-entry on the same signal. Two consecutive losses pause entries until the next configured broker day. Persist counters across restart.
8. Block entries 30 minutes before through 30 minutes after high-impact US releases. For FOMC, block from 30 minutes before the decision until conference completion plus 30 minutes. Uncertain event timing keeps the gate blocked; document a conservative scheduled fallback window.
9. Block entries within 30 minutes of daily rollover or 60 minutes of weekly close. Exit baseline positions 15 minutes before these boundaries using verified broker sessions. Protect existing positions during event blocks; do not remove their stops.

Fictional educational example: BUY at 2500, stop 2490, target 2520 has a gross 2:1 reward/risk ratio. These are not current prices or a recommendation. Lot size still depends on actual broker contract economics and account currency. SELL geometry is reversed.

## 6. Risk engine

Research defaults requiring owner review: 0.25% equity planned risk per trade; 0.50% aggregate open planned risk; 1% broker-day loss halt; 5% cash-flow-adjusted equity high-water drawdown halt. These do not guarantee realized loss ceilings.

Use MT5 `order_calc_profit` to calculate loss at stop in deposit currency for the actual symbol/volume. Add commission and adverse-slippage allowance. Search valid broker volume steps downward; never round lots upward. Recalculate after tick-size rounding. Reject if minimum lot exceeds budget. Use `order_calc_margin` and `order_check`, with a starting free-margin reserve of 50%.

```python
budget = min(equity * 0.0025, remaining_daily_budget, remaining_open_risk_budget)
# Choose the largest permitted volume v satisfying:
# abs(order_calc_profit(side, symbol, v, entry, stop))
# + commission(v) + adverse_slippage_cost(v) <= budget
# and required_margin(v) <= free_margin * 0.50.
# If no valid volume fits, return NO_TRADE.
```

Validate bid/ask geometry, stops/freeze levels, supported filling mode, symbol trading permissions, conversion values and account mode. Missing/nonfinite broker calculations block entries. Initial spread cap: both <=0.10 M15 ATR and <=historical broker/session 95th percentile calculated only from prior observations. Collect spread history before enabling this gate for trading.

Daily loss includes realized loss, commissions, fees, swap and floating loss across the account; deposits do not reset it. Include manual positions and pending orders in exposure. Unknown or unprotected external exposure blocks entries. Do not automatically close unrelated manual positions.

At a loss threshold, cancel bot entry orders and block entries while preserving protective exits. Drawdown halt requires documented manual review to resume. Market gaps can still exceed all planned budgets.

## 7. Forecasting and explanations

Primary prediction: probability the candidate reaches target before stop within 16 M15 bars. Model timeout as a separate outcome. Optional secondary prediction: four-hour return quantiles with measured empirical coverage. If both levels occur within one historical bar, resolve using ticks or pessimistically count stop first.

Start with a constant base-rate predictor and logistic regression. Test gradient boosting only if it improves held-out outcomes. Features can include lagged returns, ATR, slopes, known-level distances, spreads, sessions and correctly lagged macro inputs. No random shuffling of time series. Use expanding-window training, time-ordered calibration, and purging of overlapping label horizons. Fit preprocessing on training rows only.

Report Brier score, log loss, reliability bands, sample counts, interval coverage, actionable-signal coverage, net expectancy, drawdown and turnover. Suppress probabilities in unsupported regimes or insufficient samples. A rule score is not a probability. High directional accuracy can lose money after costs.

Expected monetary value must include target, stop AND timeout outcomes after costs. Select thresholds on validation data, then freeze before final holdout. Keep model/dataset/config versions immutable. No silent online self-modification or unreviewed retraining.

An optional language model may explain structured evidence. It cannot choose orders, calculate authoritative risk, override blockers or execute trades. Treat news text as untrusted input. Show opposing evidence, feed limitations and forecast uncertainty.

## 8. Implementation tasks and verification

For each task: write the named behavioral tests first, run them to confirm failure, implement the named files, rerun to pass, review the diff and commit only task files when repository policy permits. Record commands/results and evidence paths. Do not change unrelated work.

### Task 1: Project, settings and contracts

Files: `pyproject.toml`, `config/default.yaml`, `domain.py`, `config.py`, `tests/unit/test_contracts.py`.

- [ ] Create standalone project and locked environment; verify Windows/MT5/Python compatibility.
- [ ] Implement contracts and mode/account allowlists.
- [ ] Test advisory default, empty live allowlist, startup live lock, rejection of NaN, naive timestamps, crossed quotes and invalid stop geometry.
- [ ] Run `python -m pytest tests/unit/test_contracts.py -v`.

Acceptance: clean install loads valid configuration with no active execution path.

### Task 2: Read-only MT5 integration

Files: `data/mt5_feed.py`, `tests/integration/test_mt5_readonly.py`, `docs/DATA_SOURCES.md`.

- [ ] Implement terminal lifecycle, exact-symbol mapping, quote/bar/account reads and metadata discovery.
- [ ] Test disconnect, stale quote, wrong account, demo/live mismatch, missing symbol and unavailable calculation values using mocks.
- [ ] Run `python -m pytest tests/integration/test_mt5_readonly.py -v`; perform a separate read-only demo smoke test without order calls.

Acceptance: redacted broker capability report with account mode, contract economics and sessions verified.

### Task 3: Point-in-time data and providers

Files: `data/providers.py`, `data/store.py`, `tests/unit/test_data_time.py`.

- [ ] Inventory providers and obtain purchase approval where required.
- [ ] Persist source/availability/ingestion timestamps, revisions, units, frequencies and immutable raw hashes.
- [ ] Implement as-of joins by availability time. Test revised inflation cannot leak into earlier decisions, calendar actuals appear only after release, duplicates are idempotent and DST gaps are classified.
- [ ] Test required-calendar outage blocks entries; missing optional data is labelled.
- [ ] Run `python -m pytest tests/unit/test_data_time.py -v`.

Acceptance: reproducible dataset manifest and honest missingness/coverage report.

### Task 4: Features and strategy

Files: `features.py`, `strategy.py`, `tests/unit/test_strategy.py`, `docs/STRATEGY.md`.

- [ ] Implement section 5 exactly.
- [ ] Test synthetic BUY and mirrored SELL, unfinished bars ignored, insufficient warmup rejected, delayed pivot confirmation and expiry.
- [ ] Run `python -m pytest tests/unit/test_strategy.py -v`.

Acceptance: identical snapshots yield identical candidates; strategy performs no network calls.

### Task 5: Risk and decisions

Files: `risk.py`, `decision.py`, `tests/unit/test_risk.py`.

- [ ] Implement every gate in section 6 with machine codes and plain-language reasons.
- [ ] Test minimum lot exceeds budget, volume rounds down, non-USD account conversion, rejected stop distance, event block, manual exposure and floating-loss halt surviving restart.
- [ ] Fixture: loss of 100 deposit-currency units per lot and 25-unit budget permits at most 0.25 lots before fees; fees cannot cause final risk to exceed budget.
- [ ] Run `python -m pytest tests/unit/test_risk.py -v`.

Acceptance: no actionable decision bypasses validated cash risk; NO TRADE always has zero executable volume.

### Task 6: Realistic replay and baseline research

Files: `execution/paper.py`, `research.py`, `tests/replay/test_fills.py`, `docs/VALIDATION_REPORT.md`.

- [ ] Implement next-quote bid/ask fills, gaps, fees, swap, expiry, market hours and chronological state.
- [ ] Test BUY enters ask/exits bid, SELL enters bid/exits ask, ambiguous bars are pessimistic, gaps exceed planned stop loss and no fill precedes decision availability.
- [ ] Run `python -m pytest tests/replay/test_fills.py -v`.
- [ ] Seek at least three years of usable broker-compatible history; disclose shorter coverage if unavailable. Reserve newest 20% as untouched holdout; use earlier expanding-window folds and horizon purging.
- [ ] Publish trades, equity, costs, regime/session results, all parameter trials and limitations.

Acceptance: independently reproducible results; failed strategy remains research-only.

### Task 7: Optional prediction model

Files: `prediction.py`, `tests/unit/test_prediction.py`, `docs/MODEL_CARD.md`.

- [ ] Implement section 7 and compare to constant base-rate and rules-only baselines.
- [ ] Test training transforms cannot see holdout, horizon purge covers 16 bars, feature schemas are versioned, future inputs reject and unvalidated models cannot show confidence percentages.
- [ ] Run `python -m pytest tests/unit/test_prediction.py -v`.

Acceptance: enable only with demonstrated calibration and incremental held-out economic value; otherwise keep rules-only guidance.

### Task 8: Dashboard and education

Files: `service.py`, `ui/`, `tests/integration/test_advisory_api.py`, `docs/USER_GUIDE.md`.

- [ ] Implement section 2, local-only binding, authenticated state changes and origin/CSRF protection where applicable. No secrets in browser storage.
- [ ] Test expired decisions cannot submit, stale quotes are visible, mode is prominent and NO TRADE cannot execute.
- [ ] Run `python -m pytest tests/integration/test_advisory_api.py -v`; review keyboard access and all five action states with labelled fixtures.

Acceptance: user can explain the trigger, stop, invalidation and cash risk from the card alone. Notifications require separately authorized provider setup/sending.

### Task 9: Demo execution and journal

Files: `execution/mt5.py`, `journal.py`, `tests/integration/test_execution.py`.

- [ ] Persist intent IDs; enforce one execution writer; refresh quote/account and rerun risk before `order_check` and `order_send`.
- [ ] Handle retcodes, partial fills, slippage, freeze levels and rejected stops. Require broker-side protective stops on entry; reject unprotected entry methods in v1.
- [ ] Reconcile deals/orders/positions before retries and on restart. Timeout means unknown state, not permission to resubmit. Use broker tickets and strategy magic mapping; comments alone are insufficient deduplication.
- [ ] If actual fill exceeds budget, perform and verify a documented risk-reducing action. Never blindly retry a close.
- [ ] Test timeout then late fill creates one position, restart discovers prior orders, partial fills update exposure, wrong account rejects and kill switch preserves protective exits.
- [ ] Run `python -m pytest tests/integration/test_execution.py -v`; save an authorized demo lifecycle with redacted evidence.

Acceptance: every intent has a traceable terminal state or explicit unresolved halt. Do not claim exactly-once broker delivery.

### Task 10: Prospective demo and recovery

Files: `tests/integration/test_recovery.py`, `docs/OPERATIONS.md`, `docs/VALIDATION_REPORT.md`.

- [ ] Freeze versions and observe at least eight weeks AND 100 filled demo trades; do not increase frequency to meet the sample target.
- [ ] Drill service/terminal crash, clock drift, calendar/feed outage, spread spike, duplicate process, stale recommendation and disk-write failure. Journal failure blocks entries.
- [ ] Run `python -m pytest -v`; save restart reconciliation and separately verified alert evidence.

Acceptance: no unresolved duplicate/unprotected trades, complete journal and explained replay/demo differences. Strategy changes restart prospective evaluation for that version.

### Task 11: Owner review and live deployment

Files: `docs/OPERATIONS.md`, `docs/VALIDATION_REPORT.md`, `config/default.yaml`.

- [ ] Prepare exact versions, account mode, broker economics, data coverage, research/demo results, failure drills, proposed cash limits and rollback procedure for review.
- [ ] Obtain explicit live authorization; completing this plan cannot itself enable real-money orders.
- [ ] Deploy on dedicated Windows host/VPS with synchronized clock, single executor, backups, watchdog and credential protection. Verify broker automation terms.
- [ ] Begin with shadow comparisons; if approved, use smallest valid volume within the approved cash budget. If minimum volume is too large, do not trade.
- [ ] Monitor actual slippage, stops, costs and drift daily; pause on breached budgets or unresolved state. Require review before resuming.

Acceptance: account-specific authorization and operational evidence. Demo success alone is insufficient.

## 9. Predeclared promotion criteria

Ratify thresholds before inspecting final holdout. Proposed screening gates: at least 200 out-of-sample trades; positive net expectancy with positive lower 95% block-bootstrap bound; profit factor >=1.15 after base costs; positive expectancy with doubled spread/slippage assumptions; observed equity drawdown <=5% at stated sizing; no single trade contributes >20% of net profit. Report serial dependence, sensitivity and all tested variants. These thresholds and counts are screening conventions, not guarantees or universal statistical sufficiency.

Forecasts must improve held-out Brier score over the base-rate predictor without systematic overconfidence. Report uncertainty and sparse probability bands. Check sessions, volatility regimes and event proximity. If final holdout fails, changes require fresh future data; repeatedly tuning against the same holdout invalidates its status.

Never substitute passing unit tests, an attractive chart or a high win rate for financial evidence. If gates fail, deliver the research findings and leave trading disabled.

## 10. Operations and ongoing maintenance

Provide separate controls for pause entries, cancel bot pending entries and explicitly confirmed close-bot-positions. Explain that stopping the app does not close broker positions. Preserve broker protection during outages and do not touch unrelated manual positions.

Journal decisions, source hashes, versions, risk checks, redacted order requests/results, fills, stop changes and owner actions. Persist day counters and high-water equity with cash-flow adjustment. Back up and test restoration on demo. Reports separate P&L, costs, transfers, missing data and execution errors.

Monitor quote age, event coverage, clock skew, terminal health, unresolved intents, unprotected exposure, feature drift, probability calibration and actual trading costs. Retrain offline with versioning and the same gates. Respect provider retention and redistribution terms.

## 11. Final deliverables

- [ ] Standalone repository, locked installation and redacted sample configuration.
- [ ] Provider inventory and point-in-time coverage evidence.
- [ ] Frozen strategy, reproducible datasets/models, replay instructions and test results.
- [ ] Validation report separating synthetic, historical, demo and live evidence.
- [ ] User guide with annotated BUY/SELL/WAIT/NO TRADE/EXIT examples.
- [ ] Recovery runbook, evidence, known limitations and live approval packet.
- [ ] Progress log containing completed tasks, commands, results, artifacts and unresolved blockers.

Order: foundation/data -> deterministic advisory engine -> replay -> optional model -> dashboard -> demo execution -> prospective validation -> owner live review. No credible profitability or completion-date promise is justified before broker/data discovery.

## 12. Primary references

Consult current official documentation while implementing; API details and terms can change.

- [Official MT5 Python integration](https://www.mql5.com/en/docs/python_metatrader5): terminal/account/data access and trade functions.
- [MT5 order_check](https://www.mql5.com/en/docs/python_metatrader5/mt5ordercheck_py): preflight does not guarantee execution.
- [MT5 symbol properties](https://www.mql5.com/en/docs/constants/environment_state/marketinfoconstants): broker-specific trading economics and constraints.
- [MT5 Strategy Tester](https://www.metatrader5.com/en/terminal/help/algotrading/testing): native Expert Advisor testing. The Python application needs its own replay engine; a native MQL5 port would require separate parity verification.
- [World Gold Council attribution framework](https://www.gold.org/goldhub/research/gold-market-commentary-july-2025): economic expansion, risk/uncertainty, opportunity cost and momentum are context, not automatic trading signals.
- [Chicago Fed research on gold prices](https://www.chicagofed.org/-/media/publications/chicago-fed-letter/2021/cfl464-pdf.pdf): real-rate relationships and historical limitations, not a short-term prediction guarantee.

## Assignment for the next agent

Implement this plan in a new standalone xauusd-assistant project. Begin with Tasks 1-3, document evidence and proceed through each gate. Preserve EDITORS-PRO. Default to advisory mode; verify account identity before demo execution. Do not purchase services, send notifications, enable live mode or place real-money orders without explicit owner authorization. If no tradable edge is demonstrated, deliver that result honestly and keep trading disabled.

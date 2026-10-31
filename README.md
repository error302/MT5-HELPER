# xauusd-assistant

Local, read-only-first XAUUSD (gold) trading assistant for MetaTrader 5.

It explains BUY / SELL / WAIT / NO TRADE / EXIT decisions, calculates entry,
stop, target and lot size from broker-verified contract economics, and keeps
advisory, paper, demo and live modes strictly separated. **Live mode is locked
at startup and requires explicit owner authorization** (see `docs/OPERATIONS.md`).

This is a research and advisory tool. It makes no promise of predictions,
profits or a win rate. If no tradable edge is demonstrated by the validation
gates in `PLAN.md` section 9, trading stays disabled and the result is reported
honestly.

## Status

Implementation follows `PLAN.md` task by task. See `PROGRESS.md` for the
evidence log (commands, results, artifacts, unresolved blockers).

| Task | Scope | State |
|---|---|---|
| 1 | Project, settings, contracts | in progress |
| 2 | Read-only MT5 integration | pending |
| 3 | Point-in-time data and providers | pending |
| 4-11 | Strategy, risk, replay, model, UI, demo, prospective, live review | pending |

## Quickstart (development)

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-lock.txt
.venv\Scripts\python -m pytest -v
```

Default configuration (`config/default.yaml`) runs in **advisory** mode with an
**empty live allowlist**: no execution path is active.

## Layout

- `src/xauusd/domain.py` - validated contracts (strict Pydantic, UTC, Decimal at broker boundaries)
- `src/xauusd/config.py` - mode gating, account allowlists, risk limits
- `src/xauusd/data/` - MT5 feed, external providers, provenant store
- `src/xauusd/` - features, strategy, risk, decision, prediction, execution, research, journal, service, ui
- `tests/` - unit / integration / replay
- `docs/` - DATA_SOURCES, STRATEGY, MODEL_CARD, VALIDATION_REPORT, OPERATIONS, USER_GUIDE

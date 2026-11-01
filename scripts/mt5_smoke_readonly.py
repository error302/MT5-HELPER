"""Read-only MT5 smoke test - attaches to the running terminal, never trades.

Usage:
    .venv\\Scripts\\python scripts\\mt5_smoke_readonly.py

Produces a redacted capability report under var/reports/ and prints a summary.
It contains no order calls by construction (the Mt5Feed is read-only).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xauusd.config import load_config  # noqa: E402
from xauusd.data.mt5_feed import FeedError, Mt5Feed, StaleQuoteError  # noqa: E402

CANONICAL = "XAUUSD"


def main() -> int:
    cfg = load_config()
    configured = cfg.symbol_map.get(CANONICAL)
    exact = configured if configured and configured != CANONICAL else None

    feed = Mt5Feed(max_quote_age_seconds=5.0)
    try:
        feed.connect()
        broker_symbol = feed.resolve_symbol(CANONICAL, exact=exact)
        report = feed.capability_report(CANONICAL, exact=broker_symbol)

        # prove bar reads and quote reads work; still read-only.
        # Outside trading hours the freshness gate legitimately blocks: that
        # is reported as market-closed evidence, not a failure.
        bars = feed.get_bars(broker_symbol, "M15", 100, closed_only=True)
        report["smoke"] = {
            "m15_closed_bars_read": len(bars),
            "oldest_closed_bar_utc": bars[-1].time.isoformat(),
            "newest_closed_bar_utc": bars[0].time.isoformat(),
        }
        try:
            quote = feed.get_quote(broker_symbol)
            report["smoke"].update(
                {
                    "market_likely_closed": False,
                    "quote_utc": quote.time.isoformat(),
                    "quote_bid": quote.bid,
                    "quote_ask": quote.ask,
                }
            )
        except StaleQuoteError as exc:
            report["smoke"].update(
                {
                    "market_likely_closed": True,
                    "quote_gate_detail": str(exc),
                }
            )
    except FeedError as exc:
        print(f"SMOKE TEST FAILED (read-only): {exc}")
        return 1
    finally:
        feed.disconnect()

    out_dir = ROOT / "var" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = out_dir / f"capability_report_{stamp}.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print("SMOKE TEST OK (read-only)")
    print(f"  broker symbol : {report['broker_symbol']}")
    print(f"  account mode  : {report['account_mode']} (login {report['login_masked']})")
    skew = report["clock"]["approx_server_skew_seconds"]
    print(f"  server skew   : {skew if skew is not None else 'unreported by server'}")
    econ = report["symbol_economics"]
    print(f"  economics     : digits={econ['digits']} tick={econ['tick_size']}/{econ['tick_value']} "
          f"vol {econ['volume_min']}..{econ['volume_max']} step {econ['volume_step']} "
          f"trade_allowed={econ['trade_allowed']}")
    print(f"  M15 bars read : {report['smoke']['m15_closed_bars_read']}")
    if report["smoke"].get("market_likely_closed"):
        print("  quote         : MARKET CLOSED - freshness gate blocked as designed")
    else:
        print(f"  quote         : {report['smoke']['quote_utc']} bid={report['smoke']['quote_bid']} ask={report['smoke']['quote_ask']}")
    print(f"  report saved  : {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

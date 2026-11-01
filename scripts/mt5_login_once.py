"""One-time terminal login using local, gitignored credentials.

Usage:
    .venv\\Scripts\\python scripts\\mt5_login_once.py [path-to-env-file]

Defaults to var/secrets/demo_account.env (gitignored under var/).

Security contract:
- credentials are never hardcoded here and never printed (masked only)
- the INVESTOR password is preferred: it grants read-only terminal access,
  which matches this project's read-only-first design (PLAN.md section 1)
- no order calls exist in this script by construction
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import dotenv_values  # noqa: E402

TRADE_MODES = {0: "demo", 1: "contest", 2: "real"}


def mask(value: str) -> str:
    return ("***" + value[-3:]) if len(value) > 3 else "***"


def main() -> int:
    env_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "var" / "secrets" / "demo_account.env"
    if not env_path.exists():
        print(f"credentials file not found: {env_path}")
        return 1
    cfg = dotenv_values(env_path)
    login_raw = str(cfg.get("MT5_LOGIN", "") or "")
    password = str(cfg.get("MT5_INVESTOR_PASSWORD") or cfg.get("MT5_PASSWORD") or "")
    server = str(cfg.get("MT5_SERVER", "") or "")
    investor = bool(cfg.get("MT5_INVESTOR_PASSWORD")) and password == cfg.get("MT5_INVESTOR_PASSWORD")
    if not login_raw.isdigit() or not password or not server:
        print("env file must contain MT5_LOGIN, MT5_SERVER and MT5_PASSWORD or MT5_INVESTOR_PASSWORD")
        return 1

    import MetaTrader5 as mt5

    if not mt5.initialize(login=int(login_raw), password=password, server=server):
        code, msg = mt5.last_error()
        print(f"LOGIN FAILED: ({code}, {msg}) login={mask(login_raw)} server={server}")
        mt5.shutdown()
        return 1

    acct = mt5.account_info()
    if acct is None:
        print("LOGIN FAILED: initialized but account_info unavailable")
        mt5.shutdown()
        return 1

    print("LOGIN OK" + (" (investor read-only session)" if investor else ""))
    print(f"  login (masked)  : {mask(str(acct.login))}")
    print(f"  server          : {acct.server}")
    print(f"  account mode    : {TRADE_MODES.get(acct.trade_mode, f'trade_mode={acct.trade_mode}')}")
    print(f"  currency        : {acct.currency}  balance: {acct.balance}")
    candidates = sorted(s.name for s in (mt5.symbols_get("*XAUUSD*") or []))
    print(f"  XAUUSD candidates: {candidates}")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

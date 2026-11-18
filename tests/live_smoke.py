"""Read-only smoke test against the real portal (synthetic output only).

Credentials come from environment variables so nothing is stored in the repo:
    KYNY_USER=... KYNY_PASS=... python tests/live_smoke.py
Prints only counts and redacted values.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main() -> int:
    user = os.environ.get("KYNY_USER", "")
    password = os.environ.get("KYNY_PASS", "")
    if not user or not password:
        print("SKIP: KYNY_USER / KYNY_PASS not set")
        return 0

    from custom_components.kuangyuan_gas.api import KuangyuanAPI, create_session

    session = create_session()
    api = KuangyuanAPI(session, user, password)
    failed = 0
    try:
        accounts = await api.async_get_accounts()
        print(f"accounts: {len(accounts)}")
        for account in accounts:
            print(f"  uno={account.uno} category={account.category} "
                  f"owner={account.owner} addr_len={len(account.address)}")
            info = await api.async_query_balance(account.uno)
            print(f"    balance={info.balance} usage={info.usage} status={info.status}")
            if info.balance is None:
                failed += 1
            records = await api.async_get_recharges(account.uno, 1)
            print(f"    recharges={len(records)}")
    except Exception as exc:  # noqa: BLE001
        print("LIVE FAILED:", type(exc).__name__, exc)
        failed += 1
    finally:
        await api.async_close()
    print("LIVE OK" if not failed else f"LIVE ISSUES: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""Keeps the eBay side current (see deckledger/ebay.py): re-reads the listings followed for card
prices and every connected account's own listings and sales, each when it is due. The container
starts this every ten minutes; runs do not overlap.

    python ebay_sync.py
"""

import fcntl
import json
import os
import sqlite3
import sys

from deckledger.config import DB_PATH
from deckledger.ebay import run_due

LOCK_PATH = os.path.join(os.path.dirname(DB_PATH), "ebay-sync.lock")


def main() -> int:
    with open(LOCK_PATH, "a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 0    # the previous run is still at it
        connection = sqlite3.connect(DB_PATH)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            summary = run_due(connection)
        finally:
            connection.close()
    if summary.get("tracked") or summary.get("accounts") or summary.get("errors"):
        print("eBay-Abgleich:", json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

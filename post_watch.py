"""Reads the comments of the posts users linked to their sheets, and the new posts of the
communities they have searched for their cards (see deckledger/watcher.py).

One run asks for at most one feed -- the source allows about one anonymous request a minute --
so the container starts this once a minute. Runs do not overlap.

    python post_watch.py
"""

import fcntl
import json
import os
import sqlite3
import sys

from deckledger.config import DB_PATH
from deckledger.watcher import run_due

LOCK_PATH = os.path.join(os.path.dirname(DB_PATH), "post-watch.lock")


def main() -> int:
    with open(LOCK_PATH, "a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 0    # the previous run is still at it
        connection = sqlite3.connect(DB_PATH)
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            summary = run_due(connection)
        finally:
            connection.close()
    # Quiet unless something happened: this runs 1440 times a day.
    if summary.get("new") or summary.get("error") or summary.get("retired"):
        print("Post-Watcher:", json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

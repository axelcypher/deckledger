"""Serves the app for the browser tests (tests/frontend/*.test.mjs): the working tree, a throwaway
database and the same small catalogue the Python tests use.

    python tests/frontend/serve.py <port>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import conftest  # noqa: E402  -- points DATABASE_PATH at a temp file before the app is imported

conftest.catalog_sync.write_database(conftest.sample_catalog(), {"vcard", "lorcana", "one-piece"})
conftest.deckledger.app.run(host="127.0.0.1", port=int(sys.argv[1]), debug=False, threaded=True)

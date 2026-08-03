"""Subprocess entry point for Tier-2 custom-code catalog providers.

Invoked as: python catalog_provider_harness.py <code-file> <output-file>

Execs the admin-supplied Python source and calls the `fetch_catalog() -> dict`
function it must define, then writes the result as JSON to <output-file> --
never to stdout, so any print()/debug output the admin's code produces along
the way is never mistaken for the result. Trusted-admin model: no sandboxing
beyond what the caller enforces via a process timeout (catalog_provider_runner.py).

The entrypoint is named `fetch_catalog`, not `fetch`, so provider code can
still `from catalog_provider_contract import fetch` (the HTTP helper) without
shadowing it -- almost every provider needs that fetch far more often than it
needs to reference its own entrypoint by name.
"""

from __future__ import annotations

import json
import sys
import traceback


def main() -> None:
    code_path, output_path = sys.argv[1], sys.argv[2]
    with open(code_path, "r", encoding="utf-8") as handle:
        code = handle.read()
    try:
        namespace: dict = {"__name__": "__custom_provider__"}
        exec(compile(code, "<custom-provider>", "exec"), namespace)
        fetch_catalog = namespace.get("fetch_catalog")
        if not callable(fetch_catalog):
            raise RuntimeError("Der Code muss eine Funktion `def fetch_catalog() -> dict:` definieren.")
        catalog = fetch_catalog()
        if not isinstance(catalog, dict):
            raise RuntimeError("fetch_catalog() muss ein dict zurückgeben.")
        result = {"ok": True, "catalog": catalog}
    except Exception:
        result = {"ok": False, "error": traceback.format_exc()}
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False)


if __name__ == "__main__":
    main()

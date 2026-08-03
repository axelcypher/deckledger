"""Runs a Tier-2 custom-code catalog provider in an isolated subprocess.

Trusted-admin model (confirmed design decision): no network or filesystem
sandboxing, since this is a self-hosted tool with a small number of trusted
admins. The only enforced protection is a wall-clock timeout, same spirit as
the existing subprocess pattern in /api/prices/sync and /api/admin/providers/
<id>/run. Kept dependency-light (stdlib only) so it can be imported from
catalog_provider_registry.py without pulling in Flask/app.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HARNESS_PATH = os.path.join(os.path.dirname(__file__), "catalog_provider_harness.py")


def run_custom_code(code: str, timeout_seconds: int) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        code_path = os.path.join(tmp, "provider_code.py")
        output_path = os.path.join(tmp, "result.json")
        with open(code_path, "w", encoding="utf-8") as handle:
            handle.write(code)
        try:
            completed = subprocess.run(
                [sys.executable, HARNESS_PATH, code_path, output_path],
                timeout=timeout_seconds, capture_output=True, text=True,
                cwd=os.path.dirname(__file__),
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Custom-Code-Provider hat das Zeitlimit von {timeout_seconds}s überschritten")
        if not os.path.exists(output_path):
            tail = (completed.stderr or completed.stdout or "")[-2000:]
            raise RuntimeError(f"Custom-Code-Provider hat kein Ergebnis geschrieben (exit {completed.returncode}): {tail}")
        with open(output_path, "r", encoding="utf-8") as handle:
            result = json.load(handle)
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or "Custom-Code-Provider ist fehlgeschlagen")
        return result["catalog"]

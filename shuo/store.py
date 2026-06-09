"""
store.py — Centralised data-directory management.

All persistent artefacts (traces, reports, logs) are written under a single
root controlled by the DATA_DIR environment variable.  Defaults to
``./data`` relative to the current working directory so it *just works* in
development without any configuration, but survives process restarts (unlike
/tmp on most systems).

Set DATA_DIR to an absolute path in production (e.g. a mounted volume) so
data persists across deployments.

Usage::

    from .store import get_data_dir, get_call_data_dir

    path = get_call_data_dir("acme") / "MZ8a3b1f.json"
"""

import os
from pathlib import Path


def get_data_dir() -> Path:
    """Return the root data directory, creating it if necessary."""
    raw = os.getenv("DATA_DIR", "")
    root = Path(raw) if raw else Path.cwd() / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_call_data_dir(tenant_id: str = "default") -> Path:
    """Return the per-tenant call-data directory, creating it if necessary."""
    d = get_data_dir() / "calls" / tenant_id
    d.mkdir(parents=True, exist_ok=True)
    return d

"""Reproducibility manifest for persisted analysis runs and release diagnostics."""

from __future__ import annotations

import hashlib
import importlib.metadata
import platform
import sqlite3
import sys
from pathlib import Path

from . import __version__

_NUMERICAL_PACKAGES = (
    "numpy",
    "pandas",
    "scipy",
    "scikit-learn",
    "duckdb",
    "pyarrow",
    "psutil",
)


def runtime_environment_manifest() -> dict[str, object]:
    """Return stable, JSON-shaped runtime facts needed to reproduce a run."""
    return {
        "autoanalyst_version": __version__,
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.system().lower(),
        "machine": platform.machine().lower(),
        "sqlite_version": sqlite3.sqlite_version,
        "packages": {name: _distribution_version(name) for name in _NUMERICAL_PACKAGES},
        "uv_lock_sha256": _uv_lock_sha256(),
    }


def _distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _uv_lock_sha256() -> str | None:
    candidates: list[Path] = []
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        candidates.append(Path(frozen_root) / "uv.lock")
    candidates.extend(
        (
            Path.cwd() / "uv.lock",
            Path(__file__).resolve().parents[2] / "uv.lock",
        )
    )
    for path in candidates:
        try:
            payload = path.read_bytes()
        except OSError:
            continue
        return hashlib.sha256(payload).hexdigest()
    return None

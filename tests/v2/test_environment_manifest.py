from __future__ import annotations

import hashlib
from pathlib import Path

from autoanalyst import __version__
from autoanalyst.environment import runtime_environment_manifest


def test_runtime_manifest_records_versions_and_lock_hash() -> None:
    manifest = runtime_environment_manifest()

    assert manifest["autoanalyst_version"] == __version__
    assert isinstance(manifest["python_version"], str)
    assert manifest["sqlite_version"]
    assert manifest["packages"]["numpy"]
    assert manifest["packages"]["scikit-learn"]

    lock = Path(__file__).parents[2] / "uv.lock"
    expected = hashlib.sha256(lock.read_bytes()).hexdigest()
    assert manifest["uv_lock_sha256"] == expected

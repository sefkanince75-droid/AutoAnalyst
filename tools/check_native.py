"""Verify the packaged native window with fresh and historical workspaces."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from autoanalyst.application.projects import ProjectService
from autoanalyst.storage.sqlite import SQLiteCatalog


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    args = parser.parse_args()
    executable = args.executable.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="autoanalyst-native-") as directory:
        root = Path(directory)
        migration_root = Path(__file__).resolve().parents[1] / "src/autoanalyst/storage/migrations"
        legacy = root / "legacy-migrations"
        legacy.mkdir()
        paths = sorted(migration_root.glob("*.sql"))
        for number, source in enumerate([*paths[:5], paths[6], paths[7]], 1):
            shutil.copy2(source, legacy / f"{number:04d}_{source.name.split('_', 1)[1]}")
        old_workspace = root / "historical"
        old_catalog = SQLiteCatalog(old_workspace, migrations_dir=legacy)
        project = ProjectService(old_catalog).create("Preserved native startup example")
        for workspace in (root / "fresh", old_workspace):
            environment = {**os.environ, "AUTOANALYST_WORKSPACE": str(workspace)}
            result = subprocess.run(
                [str(executable), "--native-check", "--port", "0"],
                env=environment,
                capture_output=True,
                text=True,
                timeout=240,
                check=True,
            )
            print(result.stdout.strip(), flush=True)
            catalog = SQLiteCatalog(workspace)
            assert len(catalog.migration_records()) == 8
        assert SQLiteCatalog(old_workspace).get_project(project.project_id).name == project.name
        print(
            json.dumps(
                {"fresh_native_ui": True, "historical_native_ui": True, "project_preserved": True}
            )
        )


if __name__ == "__main__":
    main()

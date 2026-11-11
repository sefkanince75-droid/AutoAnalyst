"""Build the official V2 Windows portable executable used locally and in CI."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from autoanalyst import __version__

ROOT = Path(__file__).resolve().parents[1]


def build_identity() -> dict[str, str]:
    built_at = datetime.now(UTC)
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
        dirty = bool(
            subprocess.check_output(
                [
                    "git",
                    "status",
                    "--porcelain",
                    "--",
                    "src/autoanalyst",
                    "tools/build_release.py",
                    "pyproject.toml",
                    "uv.lock",
                ],
                cwd=ROOT,
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        )
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = "", False
    return {
        "version": __version__,
        "build_id": f"{commit[:8] or 'local'}-{built_at:%Y%m%dT%H%M%SZ}"
        + ("-modified" if dirty else ""),
        "built_at": built_at.isoformat(timespec="seconds"),
        "commit": commit,
        "source": "github-actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "local",
    }


def windows_version_info() -> str:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[a-zA-Z0-9.]+)?", __version__)
    if match is None:
        raise ValueError("Unsupported Windows release version")
    numbers = tuple(int(part) for part in match.groups()) + (0,)
    return f"""VSVersionInfo(
    ffi=FixedFileInfo(filevers={numbers!r}, prodvers={numbers!r},
                     mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
    kids=[StringFileInfo([StringTable('040904B0', [
        StringStruct('CompanyName', 'AutoAnalyst'),
        StringStruct('FileDescription', 'AutoAnalyst V2'),
        StringStruct('FileVersion', {__version__!r}),
        StringStruct('ProductVersion', {__version__!r}),
        StringStruct('ProductName', 'AutoAnalyst'),
        StringStruct('OriginalFilename', 'AutoAnalyst.exe')
    ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"""


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("The portable executable must be built on Windows.")
    build_root = ROOT / "build" / "v2-release"
    build_root.mkdir(parents=True, exist_ok=True)
    metadata = build_root / "windows_version_info.txt"
    metadata.write_text(windows_version_info(), encoding="utf-8")
    identity = build_root / "autoanalyst-build.json"
    identity.write_text(json.dumps(build_identity(), indent=2), encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        "AutoAnalyst",
        "--specpath",
        str(build_root),
        "--workpath",
        str(build_root / "work"),
        "--distpath",
        str(ROOT / "dist"),
        "--version-file",
        str(metadata),
        "--add-data",
        f"{identity};.",
        "--add-data",
        f"{ROOT / 'src/autoanalyst/ui/entrypoint.py'};autoanalyst/ui",
        "--add-data",
        f"{ROOT / 'uv.lock'};.",
        "--add-data",
        f"{ROOT / 'src/autoanalyst/storage/migrations'};autoanalyst/storage/migrations",
        "--collect-all",
        "streamlit",
        "--collect-all",
        "webview",
        "--hidden-import",
        "webview.platforms.winforms",
        "--hidden-import",
        "webview.platforms.edgechromium",
        "--collect-data",
        "autoanalyst",
        "--collect-submodules",
        "autoanalyst.ui",
        "--hidden-import",
        "autoanalyst.execution.worker",
        "--hidden-import",
        "autoanalyst.execution.data_worker",
    ]
    icon = ROOT / "assets" / "autoanalyst.ico"
    if icon.is_file():
        command += ["--icon", str(icon)]
    command.append(str(ROOT / "src/autoanalyst/desktop.py"))
    subprocess.run(command, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()

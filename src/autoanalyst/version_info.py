"""Offline identity of the running application and its packaged build."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import __version__


@dataclass(frozen=True)
class VersionInfo:
    version: str
    prerelease: bool
    packaged: bool
    build_id: str | None = None
    built_at: str | None = None
    commit: str | None = None
    source: str | None = None
    executable: str | None = None


@lru_cache(maxsize=1)
def version_info() -> VersionInfo:
    packaged = bool(getattr(sys, "frozen", False))
    metadata = {}
    if packaged:
        try:
            path = (
                Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
                / "autoanalyst-build.json"
            )
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("version") == __version__:
                metadata = value
        except (OSError, ValueError):
            pass  # Older packages remain usable without build metadata.

    def field(key):
        value = metadata.get(key)
        return value if isinstance(value, str) and value.strip() else None

    return VersionInfo(
        version=__version__,
        prerelease=bool(re.search(r"(?:a|b|rc|dev)\d", __version__)),
        packaged=packaged,
        build_id=field("build_id"),
        built_at=field("built_at"),
        commit=field("commit"),
        source=field("source"),
        executable=str(Path(sys.executable).resolve()) if packaged else None,
    )

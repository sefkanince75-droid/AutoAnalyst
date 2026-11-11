"""Emit the deterministic environment manifest shipped with V2 release artifacts."""

from __future__ import annotations

import json

from autoanalyst.environment import runtime_environment_manifest


def main() -> None:
    print(json.dumps(runtime_environment_manifest(), ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()

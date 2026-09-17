from __future__ import annotations

from pathlib import Path
import tomllib

from autoanalyst import __version__


ROOT = Path(__file__).parents[2]


def test_release_candidate_version_is_consistent() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert pyproject["project"]["version"] == __version__
    assert __version__ in readme
    assert __version__.startswith("2.0.0")


def test_workspace_and_build_outputs_are_gitignored() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for required in ("/workspace/", "/staging/", "/build/", "/dist/"):
        assert required in ignore

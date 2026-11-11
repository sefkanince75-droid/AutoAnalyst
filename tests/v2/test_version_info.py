import json
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from autoanalyst import __version__
from autoanalyst.version_info import version_info
from tools.build_release import build_identity


@pytest.fixture(autouse=True)
def clear_identity_cache():
    version_info.cache_clear()
    yield
    version_info.cache_clear()


def test_packaged_identity_is_read_from_the_bundle_not_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    (tmp_path / "autoanalyst-build.json").write_text(
        json.dumps(
            {
                "version": __version__,
                "build_id": "abc12345-20261111T120000Z",
                "commit": "a" * 40,
                "built_at": "2026-11-11T12:00:00+00:00",
                "source": "github-actions",
            }
        ),
        encoding="utf-8",
    )
    info = version_info()
    assert info.version == __version__ and info.prerelease and info.packaged
    assert info.build_id == "abc12345-20261111T120000Z"
    assert info.source == "github-actions"
    assert info.executable == str(Path(sys.executable).resolve())


@pytest.mark.parametrize(
    "payload", [None, "broken json", "[]", '{"version":"0.0.1","build_id":"wrong"}']
)
def test_old_or_invalid_metadata_never_breaks_startup_or_claims_another_build(
    tmp_path, monkeypatch, payload
):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    if payload is not None:
        (tmp_path / "autoanalyst-build.json").write_text(payload, encoding="utf-8")
    info = version_info()
    assert info.version == __version__ and info.packaged
    assert info.build_id is None and info.commit is None


def test_build_metadata_distinguishes_local_changes_and_ci(monkeypatch):
    import tools.build_release as module

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(
        module.subprocess,
        "check_output",
        lambda args, **kw: "a" * 40 if args[1] == "rev-parse" else " M src/autoanalyst/ui/app.py\n",
    )
    value = build_identity()
    assert value["version"] == __version__
    assert value["commit"] == "a" * 40
    assert value["build_id"].startswith("aaaaaaaa-") and value["build_id"].endswith("-modified")
    assert value["source"] == "github-actions"


@pytest.mark.parametrize(
    "language, title", [("tr", "Uygulama hakkında"), ("en", "About AutoAnalyst")]
)
def test_version_area_exists_without_a_project(tmp_path, monkeypatch, language, title):
    monkeypatch.setenv("AUTOANALYST_WORKSPACE", str(tmp_path / "workspace"))
    app = AppTest.from_file(str(Path(__file__).parents[2] / "app.py"))
    app.session_state["language"] = language
    app.run(timeout=30)
    assert not app.exception
    assert any(expander.label == title for expander in app.sidebar.expander)
    assert any(__version__ in caption.value for caption in app.sidebar.caption)
    assert any(__version__ in text.value for text in app.text)

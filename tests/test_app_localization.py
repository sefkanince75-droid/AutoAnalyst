from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_language_selector_switches_initial_ui_to_german() -> None:
    app = AppTest.from_file(Path(__file__).parents[1] / "legacy_app.py").run(timeout=20)
    app.selectbox[0].set_value("Deutsch")
    app.run(timeout=20)
    assert not app.exception
    assert app.selectbox[0].label == "Sprache"
    assert any("lokal" in message.value for message in app.info)
    assert app.file_uploader[0].label == "Eine oder mehrere CSV- oder Excel-Dateien (.xlsx) hochladen"

from pathlib import Path


def test_streamlit_upload_ceiling_is_256_megabytes() -> None:
    config = (Path(__file__).parents[1] / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert "maxUploadSize = 256" in config
    assert "gatherUsageStats = false" in config

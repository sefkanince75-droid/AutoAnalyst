from pathlib import Path


def test_streamlit_upload_limit_is_one_gigabyte_per_file() -> None:
    config = (Path(__file__).parents[1] / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert "maxUploadSize = 1024" in config

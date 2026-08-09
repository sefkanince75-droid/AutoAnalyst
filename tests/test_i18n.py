import pytest

from src.i18n import TRANSLATIONS, translate


def test_translation_catalogs_have_matching_keys() -> None:
    expected = set(TRANSLATIONS["en"])
    assert set(TRANSLATIONS) == {"en", "tr", "de", "fr", "es"}
    assert all(set(catalog) == expected for catalog in TRANSLATIONS.values())


def test_language_switching_returns_natural_localized_text() -> None:
    assert translate("en", "training") == "Training baseline models..."
    assert translate("tr", "training") == "Temel modeller eğitiliyor..."
    assert translate("en", "training") != translate("tr", "training")
    assert translate("tr", "recommended_text", model="M", precision=0.8, recall=0.9, pr_auc=0.7, threshold=0.4, minimum=0.85).startswith("Recall")


def test_unknown_language_or_key_fails_loudly() -> None:
    with pytest.raises(ValueError):
        translate("it", "title")
    with pytest.raises(KeyError):
        translate("en", "missing-key")


@pytest.mark.parametrize(
    ("language", "expected_upload", "expected_complete"),
    [
        ("de", "CSV- oder Excel-Datei", "Analyse abgeschlossen"),
        ("fr", "fichier CSV ou Excel", "Analyse terminée"),
        ("es", "archivo CSV o Excel", "Análisis completo"),
    ],
)
def test_v11_languages_have_natural_core_translations(
    language: str,
    expected_upload: str,
    expected_complete: str,
) -> None:
    assert expected_upload in translate(language, "upload_v11")
    assert translate(language, "analysis_complete") == expected_complete
    assert translate(language, "privacy_message") != translate("en", "privacy_message")

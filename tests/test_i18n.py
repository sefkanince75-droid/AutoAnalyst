import pytest

from src.i18n import TRANSLATIONS, translate


def test_translation_catalogs_have_matching_keys() -> None:
    assert set(TRANSLATIONS["en"]) == set(TRANSLATIONS["tr"])


def test_language_switching_returns_natural_localized_text() -> None:
    assert translate("en", "training") == "Training baseline models..."
    assert translate("tr", "training") == "Temel modeller eğitiliyor..."
    assert translate("en", "training") != translate("tr", "training")
    assert translate("tr", "recommended_text", model="M", precision=0.8, recall=0.9, pr_auc=0.7, threshold=0.4, minimum=0.85).startswith("Recall")


def test_unknown_language_or_key_fails_loudly() -> None:
    with pytest.raises(ValueError):
        translate("de", "title")
    with pytest.raises(KeyError):
        translate("en", "missing-key")

from io import StringIO

import pytest

from src.data_loader import CSVLoadError, load_csv


def test_load_csv_accepts_tabular_data() -> None:
    dataframe = load_csv(StringIO("feature,target\n1,no\n2,yes\n"))
    assert dataframe.shape == (2, 2)


def test_load_csv_rejects_featureless_dataset() -> None:
    with pytest.raises(CSVLoadError, match="at least one feature"):
        load_csv(StringIO("target\nyes\nno\n"))

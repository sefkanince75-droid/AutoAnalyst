from __future__ import annotations

import json
from datetime import date, time
from io import BytesIO
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from autoanalyst.analyses.profiling import ProfilingModule
from autoanalyst.application import DatasetService, PreparationService, ProjectService
from autoanalyst.bootstrap import build_runtime
from autoanalyst.data import CSVIngestor, TableAccess
from autoanalyst.domain.plans import AnalysisModuleId
from autoanalyst.reporting import ReportService
from autoanalyst.storage import ArtifactStore, SQLiteCatalog
from autoanalyst.ui.app import _spec

ROOT = Path(__file__).parents[2]


def test_v2_streamlit_reopens_persisted_project_without_backend_exception(
    tmp_path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("AUTOANALYST_WORKSPACE", str(workspace))

    catalog = SQLiteCatalog(workspace)
    store = ArtifactStore(catalog.paths.root)
    project = ProjectService(catalog).create("Journey project", default_language="en")
    dataset = DatasetService(catalog).create(project.project_id, "Journey data")
    CSVIngestor(catalog, store).import_csv(
        project_id=project.project_id,
        dataset_id=dataset.dataset_id,
        source=b"value,group\n1,a\n2,a\n3,b\n4,b\n",
        original_name="journey.csv",
    )

    app = AppTest.from_file(str(ROOT / "app.py"))
    app.run(timeout=30)

    assert len(app.exception) == 0
    assert any("AutoAnalyst V2" in title.value for title in app.title)
    assert any("Journey project" in str(item.value) for item in app.selectbox)

    # A second independent Streamlit session must reopen the same persisted workspace.
    reopened = AppTest.from_file(str(ROOT / "app.py"))
    reopened.run(timeout=30)
    assert len(reopened.exception) == 0
    assert any("Journey project" in str(item.value) for item in reopened.selectbox)


@pytest.fixture
def ui_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOANALYST_WORKSPACE", str(tmp_path / "workspace"))
    runtime = build_runtime()
    projects = ProjectService(runtime.catalog)
    datasets = DatasetService(runtime.catalog)
    project = projects.create("A project")
    other_project = projects.create("B project")
    ingestor = CSVIngestor(runtime.catalog, runtime.artifact_store)

    def add_dataset(project_id, name, content=b"value,group\n1,a\n2,b\n3,a\n"):
        dataset = datasets.create(project_id, name)
        imported = ingestor.import_csv(
            project_id=project_id,
            dataset_id=dataset.dataset_id,
            source=content,
            original_name="input.csv",
        )
        return dataset, imported

    first, first_import = add_dataset(project.project_id, "A data")
    second, second_import = add_dataset(project.project_id, "B data")
    other, other_import = add_dataset(other_project.project_id, "Other data")
    return SimpleNamespace(**locals())


def _app():
    app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
    assert not app.exception
    # Project timestamps can tie on Windows. Select the intended fixture
    # project explicitly instead of relying on random UUID ordering.
    project = next((item for item in app.selectbox if item.label == "Project"), None)
    if project is not None and "A project" in project.options:
        if getattr(project.value, "name", None) != "A project":
            project.select_index(project.options.index("A project")).run(timeout=30)
            assert not app.exception
    return app


def _select(app, label, option):
    selector = next((item for item in app.selectbox if item.label == label), None)
    if selector is not None:
        selector.select_index(selector.options.index(option)).run(timeout=30)
    else:
        next(item for item in app.radio if item.label == label).set_value(option).run(timeout=30)
    assert not app.exception


def _preview_rename(app):
    app.selectbox(key="prep_operation").select("rename_column").run(timeout=30)
    next(item for item in app.text_input if item.label == "New name").input("renamed").run(
        timeout=30
    )
    app.button(key="preview_preparation").click().run(timeout=30)
    assert not app.exception
    assert any(button.key == "apply_preparation" for button in app.button)


@pytest.mark.parametrize("change", ["dataset", "project", "parameter", "operation", "version"])
def test_preparation_preview_is_invalidated_when_context_changes(ui_workspace, change):
    ws = ui_workspace
    app = _app()
    _preview_rename(app)
    if change == "dataset":
        _select(app, "Dataset", "B data")
    elif change == "project":
        _select(app, "Project", "B project")
    elif change == "parameter":
        next(item for item in app.text_input if item.label == "New name").input("different").run(
            timeout=30
        )
    elif change == "operation":
        app.selectbox(key="prep_operation").select("drop_columns").run(timeout=30)
    else:
        ws.ingestor.import_csv(
            project_id=ws.project.project_id,
            dataset_id=ws.first.dataset_id,
            source=b"value,group\n4,c\n5,d\n",
            original_name="new.csv",
        )
        app.run(timeout=30)
    assert not app.exception
    assert not any(button.key == "apply_preparation" for button in app.button)
    assert "preview_id" not in app.session_state
    assert ws.datasets.get(ws.first.dataset_id).head_revision == (2 if change == "version" else 1)


def test_valid_preparation_preview_applies_once_and_reopens(ui_workspace):
    ws = ui_workspace
    app = _app()
    _preview_rename(app)
    app.button(key="apply_preparation").click().run(timeout=30)
    assert not app.exception
    assert "preview_id" not in app.session_state
    assert ws.datasets.get(ws.first.dataset_id).head_revision == 2
    version = ws.datasets.current_version(ws.first.dataset_id)
    assert any(
        c["display_name"] == "renamed" for c in ws.runtime.catalog.list_columns(version.version_id)
    )
    assert not _app().exception


def _profile(ws, project_id, version_id, request_key):
    spec = _spec(project_id, version_id, AnalysisModuleId.PROFILING, "profile", {}, ())
    run = ws.runtime.coordinator.create_run(spec, request_key=request_key)
    completed = ws.runtime.coordinator.execute_inline(
        run.run_id,
        ProfilingModule(ws.runtime.catalog, ws.runtime.artifact_store),
        cancellation_event=Event(),
    )
    assert completed.status.value == "completed", completed.error_code
    return ws.runtime.run_store.get_result(completed.result_id)


@pytest.mark.parametrize("change", ["project", "result", "format", "language"])
def test_report_download_is_invalidated_when_context_changes(ui_workspace, change):
    ws = ui_workspace
    _profile(ws, ws.project.project_id, ws.first_import.version.version_id, "first")
    _profile(ws, ws.project.project_id, ws.second_import.version.version_id, "second")
    _profile(ws, ws.other_project.project_id, ws.other_import.version.version_id, "other")
    app = _app()
    app.button(key="build_report").click().run(timeout=30)
    assert not app.exception
    assert len(app.get("download_button")) == 1

    if change == "project":
        _select(app, "Project", "B project")
    elif change == "result":
        selector = next(item for item in app.selectbox if item.label == "Run")
        selector.select_index(1 - selector.index).run(timeout=30)
    elif change == "format":
        _select(app, "Format", "json")
    else:
        _select(app, "Report language", "tr")
    assert not app.exception
    assert "report_id" not in app.session_state
    assert not app.get("download_button")
    app.button(key="build_report").click().run(timeout=30)
    assert not app.exception
    assert len(app.get("download_button")) == 1


@pytest.mark.parametrize("failure", ["empty_csv", "broken_xlsx", "disk", "limit", "unexpected"])
@pytest.mark.parametrize("language", ["en", "tr"])
def test_import_error_keeps_ui_and_previous_data_available(
    ui_workspace, monkeypatch, failure, language
):
    ws = ui_workspace
    name = "broken.xlsx" if failure == "broken_xlsx" else "input.csv"
    data = b"broken" if failure == "broken_xlsx" else b"value\n10\n"
    if failure == "empty_csv":
        data = b""
    monkeypatch.setattr(
        st,
        "file_uploader",
        lambda *args, **kwargs: SimpleNamespace(name=name, getvalue=lambda: data),
    )
    if failure in {"disk", "unexpected"}:

        def fail(*args, **kwargs):
            error = OSError if failure == "disk" else RuntimeError
            raise error("private-data-must-not-be-logged")

        monkeypatch.setattr(CSVIngestor, "import_csv", fail)
    elif failure == "limit":
        import autoanalyst.data.limits as limits

        monkeypatch.setattr(limits, "CSV_MAX_BYTES", 1)
    app = _app()
    if language == "tr":
        _select(app, "Language / Dil", "tr")
    if failure != "broken_xlsx":
        app.button(key="preview_import").click().run(timeout=30)
        if failure in {"disk", "unexpected"}:
            app.button(key="import_csv").click().run(timeout=30)
    assert not app.exception
    assert app.error
    message = app.error[-1].value
    assert ("Teşhis numarası" if language == "tr" else "Diagnostic reference") in message
    assert "private-data" not in message
    assert ws.datasets.current_version(ws.first.dataset_id) == ws.first_import.version
    diagnostic = (ws.runtime.catalog.paths.logs / "import-errors.jsonl").read_text(encoding="utf-8")
    assert "private-data" not in diagnostic
    assert json.loads(diagnostic.splitlines()[-1])["reference"] in message


def test_report_id_is_checked_even_when_cached_context_matches(ui_workspace):
    ws = ui_workspace
    selected = _profile(ws, ws.project.project_id, ws.first_import.version.version_id, "selected")
    other = _profile(ws, ws.other_project.project_id, ws.other_import.version.version_id, "other")
    reports = ReportService(ws.runtime.catalog, ws.runtime.artifact_store)
    other_report = reports.render(other.result_id, format="html", language="en")
    app = _app()
    app.button(key="build_report").click().run(timeout=30)
    report_id = app.session_state["report_id"]
    assert reports.get(report_id).root_result_id == selected.result_id
    app.run(timeout=30)
    assert app.session_state["report_id"] == report_id
    app.session_state["report_id"] = other_report.report_id
    app.run(timeout=30)
    assert not app.exception
    assert "report_id" not in app.session_state
    assert not app.get("download_button")


def test_preview_id_is_checked_even_when_cached_context_matches(ui_workspace):
    ws = ui_workspace
    app = _app()
    _preview_rename(app)
    preview_id = app.session_state["preview_id"]
    from autoanalyst.application import PreparationService

    preparation = PreparationService(ws.runtime.catalog, ws.runtime.artifact_store)
    column = next(
        c
        for c in ws.runtime.catalog.list_columns(ws.second_import.version.version_id)
        if not c["is_system"]
    )
    recipe = preparation.create_recipe(
        project_id=ws.project.project_id,
        base_version_id=ws.second_import.version.version_id,
        steps=(
            {
                "operation": "rename_column",
                "parameters": {"column_id": column["column_id"], "new_name": "other"},
            },
        ),
    )
    other_preview = preparation.preview_recipe(recipe.recipe_id)
    assert other_preview.preview_id != preview_id
    app.session_state["preview_id"] = other_preview.preview_id
    app.run(timeout=30)
    assert not app.exception
    assert "preview_id" not in app.session_state
    assert not any(button.key == "apply_preparation" for button in app.button)
    assert ws.datasets.get(ws.first.dataset_id).head_revision == 1
    assert ws.datasets.get(ws.second.dataset_id).head_revision == 1


def test_csv_import_requires_a_matching_preview_and_uses_selected_options(
    ui_workspace, monkeypatch
):
    ws = ui_workspace
    upload = SimpleNamespace(
        name="turkish.csv", getvalue=lambda: "şehir;adet\nİzmir;10\nIğdır;20\n".encode("cp1254")
    )
    monkeypatch.setattr(st, "file_uploader", lambda *args, **kwargs: upload)
    app = _app()
    assert app.button(key="import_csv").disabled
    next(item for item in app.selectbox if item.label == "CSV delimiter").select(";").run(
        timeout=30
    )
    _select(app, "CSV encoding", "cp1254")
    app.button(key="preview_import").click().run(timeout=30)
    assert not app.exception
    assert not app.button(key="import_csv").disabled
    assert app.session_state["import_sample"]["sample"].frame["şehir"].tolist() == [
        "İzmir",
        "Iğdır",
    ]
    assert ws.datasets.get(ws.first.dataset_id).head_revision == 1
    _select(app, "CSV encoding", "utf-8")
    assert "import_sample" not in app.session_state
    assert app.button(key="import_csv").disabled
    _select(app, "CSV encoding", "cp1254")
    app.button(key="preview_import").click().run(timeout=30)
    app.button(key="import_csv").click().run(timeout=30)
    assert not app.exception
    version = ws.datasets.current_version(ws.first.dataset_id)
    assert version.row_count == 2
    assert version.column_count == 2
    assert json.loads(version.parse_contract)["delimiter"] == ";"
    assert json.loads(version.parse_contract)["encoding"] == "cp1254"


def test_import_sample_is_invalidated_when_uploaded_content_changes(ui_workspace, monkeypatch):
    upload = {"content": b"value\n10\n"}
    monkeypatch.setattr(
        st,
        "file_uploader",
        lambda *args, **kwargs: SimpleNamespace(
            name="same.csv", getvalue=lambda: upload["content"]
        ),
    )
    app = _app()
    app.button(key="preview_import").click().run(timeout=30)
    assert not app.button(key="import_csv").disabled
    upload["content"] = b"different\n20\n"
    app.run(timeout=30)
    assert not app.exception
    assert "import_sample" not in app.session_state
    assert app.button(key="import_csv").disabled


def test_xlsx_sheet_change_invalidates_sample_and_imports_only_selected_sheet(
    ui_workspace, monkeypatch
):
    ws = ui_workspace
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"first": [1]}).to_excel(writer, sheet_name="First", index=False)
        pd.DataFrame({"selected": [10, 20]}).to_excel(writer, sheet_name="Selected", index=False)
    monkeypatch.setattr(
        st,
        "file_uploader",
        lambda *args, **kwargs: SimpleNamespace(name="sample.xlsx", getvalue=buffer.getvalue),
    )
    app = _app()
    app.button(key="preview_import").click().run(timeout=30)
    assert not app.button(key="import_xlsx").disabled
    _select(app, "Worksheet", "Selected")
    assert "import_sample" not in app.session_state
    assert app.button(key="import_xlsx").disabled
    app.button(key="preview_import").click().run(timeout=30)
    assert app.session_state["import_sample"]["sample"].frame.columns.tolist() == ["selected"]
    app.button(key="import_xlsx").click().run(timeout=30)
    assert not app.exception
    version = ws.datasets.current_version(ws.first.dataset_id)
    assert version.row_count == 2
    assert json.loads(version.parse_contract)["sheet_name"] == "Selected"


def test_data_browser_pages_without_loading_all_rows_and_resets_on_dataset_change(
    ui_workspace, monkeypatch
):
    ws = ui_workspace
    ws.ingestor.import_csv(
        project_id=ws.project.project_id,
        dataset_id=ws.first.dataset_id,
        source=("value,group\n" + "".join(f"{i},a\n" for i in range(205))).encode(),
    )

    def forbidden_full_read(*args, **kwargs):
        raise AssertionError("Browsing must use a bounded page query")

    monkeypatch.setattr(TableAccess, "selected_columns", forbidden_full_read)
    app = _app()

    def displayed_values():
        frame = next(
            item.value for item in app.dataframe if list(item.value.columns) == ["value", "group"]
        )
        return frame["value"].tolist()

    assert displayed_values() == list(range(100))
    next(item for item in app.number_input if item.label == "Page").set_value(3).run(timeout=30)
    assert not app.exception
    assert displayed_values() == list(range(200, 205))
    _select(app, "Rows per page", "25")
    assert displayed_values() == list(range(25))
    next(item for item in app.number_input if item.label == "Page").set_value(9).run(timeout=30)
    assert displayed_values() == list(range(200, 205))
    _select(app, "Dataset", "B data")
    assert not app.exception
    assert displayed_values() == [1, 2, 3]
    assert next(item for item in app.number_input if item.label == "Page").value == 1


def _typed_source(ws, kind, missing=False):
    content = {
        "integer": b"value,anchor\n1,a\n2,b\n3,c\n",
        "numeric": b"value,anchor\n1.5,a\n2.5,b\n3.5,c\n",
        "boolean": b"value,anchor\ntrue,a\nfalse,b\ntrue,c\n",
        "datetime": b"value,anchor\n2024-01-01T12:00,a\n2024-01-02T12:00,b\n2024-01-03T12:00,c\n",
    }[kind]
    if missing:
        content = b"\n".join(content.split(b"\n")[:2] + [b",b"] + content.split(b"\n")[3:])
    imported = ws.ingestor.import_csv(
        project_id=ws.project.project_id, dataset_id=ws.first.dataset_id, source=content
    )
    if kind in {"datetime", "boolean"}:
        column = next(c for c in imported.columns if c.display_name == "value" and not c.is_system)
        preparation = PreparationService(ws.runtime.catalog, ws.runtime.artifact_store)
        recipe = preparation.create_recipe(
            project_id=ws.project.project_id,
            base_version_id=imported.version.version_id,
            steps=(
                {
                    "operation": "cast_column",
                    "parameters": {
                        "column_id": column.column_id,
                        "target_type": kind,
                        "failure_policy": "strict",
                    },
                },
            ),
        )
        preparation.apply_preview(preparation.preview_recipe(recipe.recipe_id).preview_id)


@pytest.mark.parametrize("kind", ["integer", "numeric", "boolean", "datetime"])
def test_preparation_filter_preserves_scalar_type_and_applies_correct_rows(ui_workspace, kind):
    ws = ui_workspace
    _typed_source(ws, kind)
    app = _app()
    operator = next(
        item for item in app.selectbox if item.key and item.key.startswith("prep_operator_")
    )
    if kind == "boolean":
        next(
            item for item in app.selectbox if item.key and item.key.startswith("prep_filter_value_")
        ).select(False).run(timeout=30)
        expected = False
    else:
        operator.select("gte").run(timeout=30)
        if kind == "datetime":
            app.date_input[0].set_value(date(2024, 1, 2)).run(timeout=30)
            app.time_input[0].set_value(time(12, 0)).run(timeout=30)
            expected = "2024-01-02T12:00:00"
        else:
            next(
                item for item in app.number_input if item.key.startswith("prep_filter_value_")
            ).set_value(2 if kind == "integer" else 2.0).run(timeout=30)
            expected = 2 if kind == "integer" else 2.0
    app.button(key="preview_preparation").click().run(timeout=30)
    assert not app.exception
    preview = PreparationService(ws.runtime.catalog, ws.runtime.artifact_store).get_preview(
        app.session_state["preview_id"]
    )
    recipe = ws.runtime.catalog.get_preparation_recipe(preview.recipe_id)
    value = recipe.ordered_steps[0].parameters["value"]
    assert value == expected and type(value) is type(expected)
    app.button(key="apply_preparation").click().run(timeout=30)
    assert not app.exception
    assert ws.datasets.current_version(ws.first.dataset_id).row_count == (
        1 if kind == "boolean" else 2
    )


@pytest.mark.parametrize("kind", ["numeric", "boolean", "datetime"])
def test_constant_fill_uses_column_type_instead_of_string(ui_workspace, kind):
    ws = ui_workspace
    _typed_source(ws, kind, missing=True)
    app = _app()
    app.selectbox(key="prep_operation").select("fill_missing").run(timeout=30)
    app.multiselect(key="prep_fill_cols").set_value(["value"]).run(timeout=30)
    app.selectbox(key="prep_fill_strategy").select("constant").run(timeout=30)
    if kind == "numeric":
        next(item for item in app.number_input if item.key.startswith("prep_constant_")).set_value(
            7.5
        ).run(timeout=30)
    elif kind == "boolean":
        next(
            item for item in app.selectbox if item.key and item.key.startswith("prep_constant_")
        ).select(False).run(timeout=30)
    else:
        app.date_input[0].set_value(date(2020, 2, 3)).run(timeout=30)
    app.button(key="preview_preparation").click().run(timeout=30)
    assert not app.exception
    assert "preview_id" in app.session_state, [item.value for item in app.error]
    app.button(key="apply_preparation").click().run(timeout=30)
    assert not app.exception
    version = ws.datasets.current_version(ws.first.dataset_id)
    column = next(
        c
        for c in ws.runtime.catalog.list_columns(version.version_id)
        if c["display_name"] == "value"
    )
    values = (
        TableAccess(ws.runtime.catalog, ws.runtime.artifact_store)
        .page(version.version_id, (column["column_id"],))
        .column(0)
        .to_pylist()
    )
    assert all(value is not None for value in values)
    if kind == "numeric":
        assert values[1] == 7.5
    if kind == "boolean":
        assert values[1] is False


def test_language_change_preserves_typed_value_and_preview(ui_workspace):
    ws = ui_workspace
    _typed_source(ws, "numeric")
    app = _app()
    field = next(item for item in app.number_input if item.key.startswith("prep_filter_value_"))
    field.set_value(2.5).run(timeout=30)
    app.button(key="preview_preparation").click().run(timeout=30)
    preview_id = app.session_state["preview_id"]
    app.selectbox(key="language").select("tr").run(timeout=30)
    assert not app.exception
    assert app.selectbox(key="prep_operation").value == "filter_rows"
    assert (
        next(item for item in app.number_input if item.key.startswith("prep_filter_value_")).value
        == 2.5
    )
    assert app.session_state["preview_id"] == preview_id
    assert app.button(key="apply_preparation").label == "Önizlemeyi uygula"


def test_project_language_default_and_explicit_override(ui_workspace):
    ws = ui_workspace
    project = ws.projects.create("TR project", default_language="tr")
    app = _app()
    app.selectbox(key="project_selector").select(project).run(timeout=30)
    assert not app.exception
    assert app.selectbox(key="language").value == "tr"
    assert any(item.label == "Proje" for item in app.selectbox)
    app.selectbox(key="language").select("en").run(timeout=30)
    app.selectbox(key="project_selector").select(ws.project).run(timeout=30)
    app.selectbox(key="project_selector").select(project).run(timeout=30)
    assert not app.exception
    assert app.selectbox(key="language").value == "en"


def test_welch_requires_explicit_independence_acknowledgement(ui_workspace, monkeypatch):
    import autoanalyst.ui.app as ui

    ws = ui_workspace
    ws.ingestor.import_csv(
        project_id=ws.project.project_id,
        dataset_id=ws.first.dataset_id,
        source=b"value,group\n1,a\n2,a\n3,a\n4,b\n5,b\n6,b\n",
    )
    captured = []
    monkeypatch.setattr(ui, "_launch", lambda runtime, spec: captured.append(spec))
    app = _app()
    app.selectbox(key="analysis_task").select("Welch two groups").run(timeout=30)
    app.selectbox(key="welch_group").select("group").run(timeout=30)
    assert app.button(key="run_welch").disabled
    next(item for item in app.checkbox if item.key.startswith("welch_independent_")).check().run(
        timeout=30
    )
    app.button(key="run_welch").click().run(timeout=30)
    assert not app.exception
    assert len(captured) == 1
    assert captured[0].parameters["independence_confirmed"] is True
    assert not ws.runtime.registry.get(AnalysisModuleId.COMPARISON).validate(captured[0])


def test_large_integer_filter_does_not_round_at_browser_precision_limit(ui_workspace):
    ws = ui_workspace
    large = 2**53 + 1
    ws.ingestor.import_csv(
        project_id=ws.project.project_id,
        dataset_id=ws.first.dataset_id,
        source=f"value\n{large - 1}\n{large}\n{large + 1}\n".encode(),
    )
    app = _app()
    next(item for item in app.checkbox if item.key.startswith("prep_filter_value_")).check().run(
        timeout=30
    )
    field = next(
        item for item in app.text_input if item.key and item.key.startswith("prep_filter_value_")
    )
    field.input(str(large)).run(timeout=30)
    app.button(key="preview_preparation").click().run(timeout=30)
    assert not app.exception
    preparation = PreparationService(ws.runtime.catalog, ws.runtime.artifact_store)
    preview = preparation.get_preview(app.session_state["preview_id"])
    recipe = preparation.get_recipe(preview.recipe_id)
    assert recipe.ordered_steps[0].parameters["value"] == large
    assert preview.after_row_count == 1
    next(
        item for item in app.text_input if item.key and item.key.startswith("prep_filter_value_")
    ).input("1.5").run(timeout=30)
    assert app.button(key="preview_preparation").disabled
    assert "preview_id" not in app.session_state


def test_mixed_type_constant_fill_is_blocked_before_preview(ui_workspace):
    app = _app()
    app.selectbox(key="prep_operation").select("fill_missing").run(timeout=30)
    app.multiselect(key="prep_fill_cols").set_value(["value", "group"]).run(timeout=30)
    app.selectbox(key="prep_fill_strategy").select("constant").run(timeout=30)
    assert not app.exception
    assert app.button(key="preview_preparation").disabled
    assert any("same type" in item.value for item in app.info)


def test_turkish_project_language_is_used_on_first_open(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOANALYST_WORKSPACE", str(tmp_path))
    project = ProjectService(SQLiteCatalog(tmp_path)).create("Türkçe proje", default_language="tr")
    app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
    assert not app.exception
    assert app.selectbox(key="language").value == "tr"
    assert app.selectbox(key="project_selector").value.project_id == project.project_id
    assert app.button(key="create_dataset").label == "Veri seti oluştur"


def test_report_template_revision_invalidates_cache_without_overwriting_old_report(
    ui_workspace, monkeypatch
):
    import autoanalyst.reporting.render as rendering

    ws = ui_workspace
    result = _profile(ws, ws.project.project_id, ws.first_import.version.version_id, "report-cache")
    reports = ReportService(ws.runtime.catalog, ws.runtime.artifact_store)
    current = rendering.TEMPLATE_VERSION
    monkeypatch.setattr(rendering, "TEMPLATE_VERSION", "legacy-report-template")
    old = reports.render(result.result_id, format="html", language="tr")
    old_bytes = reports.artifact_bytes(old)
    monkeypatch.setattr(rendering, "TEMPLATE_VERSION", current)
    new = reports.render(result.result_id, format="html", language="tr")
    assert old.report_id != new.report_id
    assert reports.artifact_bytes(old) == old_bytes
    assert reports.render(result.result_id, format="html", language="tr").report_id == new.report_id


def test_datetime_mode_fill_records_serializable_resolved_value(ui_workspace):
    ws = ui_workspace
    _typed_source(ws, "datetime", missing=True)
    app = _app()
    app.selectbox(key="prep_operation").select("fill_missing").run(timeout=30)
    app.multiselect(key="prep_fill_cols").set_value(["value"]).run(timeout=30)
    app.selectbox(key="prep_fill_strategy").select("mode").run(timeout=30)
    app.button(key="preview_preparation").click().run(timeout=30)
    assert not app.exception
    assert "preview_id" in app.session_state, [item.value for item in app.error]
    app.button(key="apply_preparation").click().run(timeout=30)
    assert not app.exception


def test_results_and_html_reopen_the_same_saved_chart_after_dataset_changes(
    ui_workspace, monkeypatch
):
    from autoanalyst.reporting.charts import chart_views

    ws = ui_workspace
    result = _profile(ws, ws.project.project_id, ws.first_import.version.version_id, "chart-ui")
    # The current dataset can change independently of this immutable result.
    ws.ingestor.import_csv(
        project_id=ws.project.project_id,
        dataset_id=ws.first.dataset_id,
        source=b"renamed\n900\n1000\n",
    )
    expected = chart_views(result, "en")[0].svg
    captured = []
    original = st.image

    def capture(image, **kwargs):
        captured.append(image)
        return original(image, **kwargs)

    monkeypatch.setattr(st, "image", capture)
    app = _app()
    assert not app.exception
    assert captured[-1] == expected
    assert {item.label: item.value for item in app.metric}["Row count"] == "3"
    assert any(item.value == "Distribution: value" for item in app.caption)
    app.button(key="build_report").click().run(timeout=30)
    reports = ReportService(ws.runtime.catalog, ws.runtime.artifact_store)
    report = reports.get(app.session_state["report_id"])
    assert expected.encode() in reports.artifact_bytes(report)
    captured.clear()
    reopened = _app()
    assert not reopened.exception and captured[-1] == expected
    reopened.selectbox(key="language").select("tr").run(timeout=30)
    assert not reopened.exception
    assert captured[-1] == chart_views(result, "tr")[0].svg
    assert any(item.value == "Dağılım: value" for item in reopened.caption)


def test_new_session_discovers_pending_run_and_can_cancel_it(ui_workspace):
    ws = ui_workspace
    run = ws.runtime.coordinator.create_run(
        _spec(
            ws.project.project_id,
            ws.first_import.version.version_id,
            AnalysisModuleId.PROFILING,
            "profile",
            {},
            (),
        ),
        request_key="monitor-pending",
    )
    app = _app()
    assert app.session_state["active_run_id"] == run.run_id
    assert app.button(key="cancel_active_run")
    assert any("automatically every second" in item.value for item in app.caption)
    reopened = _app()
    assert reopened.session_state["active_run_id"] == run.run_id
    reopened.button(key="cancel_active_run").click().run(timeout=30)
    assert not reopened.exception
    assert ws.runtime.run_store.get_run(run.run_id).status.value == "cancelled"
    assert "active_run_id" not in reopened.session_state
    assert not reopened.get("progress")


def test_monitor_restores_progress_and_completion_from_persisted_state(ui_workspace):
    import os

    from autoanalyst.domain.runs import RunStatus
    from autoanalyst.execution.lease import activate_worker, release_worker, reserve_worker
    from autoanalyst.execution.worker_protocol import write_progress_manifest

    ws = ui_workspace
    spec = _spec(
        ws.project.project_id,
        ws.first_import.version.version_id,
        AnalysisModuleId.PROFILING,
        "profile",
        {},
        (),
    )
    run = ws.runtime.coordinator.create_run(spec, request_key="monitor-progress")
    ws.runtime.run_store.transition(run.run_id, status=RunStatus.RUNNING)
    reserve_worker(ws.runtime.catalog.paths.root, run.run_id)
    activate_worker(ws.runtime.catalog.paths.root, run.run_id, os.getpid())
    try:
        write_progress_manifest(
            ws.runtime.catalog.paths.root,
            run.run_id,
            stage="profile.columns",
            fraction=0.5,
            payload={"secret": "private-data"},
        )
        app = _app()
        assert not app.exception
        assert app.get("progress")[0].proto.value == 50
        assert "private-data" not in str(app)
        # Complete the same registered run without recomputing or replacing its input.
        from uuid import uuid4

        from autoanalyst.domain.results import AnalysisResult, ResultOutcome

        result = AnalysisResult(
            str(uuid4()), run.run_id, AnalysisModuleId.PROFILING, ResultOutcome.SUCCEEDED
        )
        ws.runtime.run_store.publish_result(run.run_id, result)
        app.run(timeout=30)
        assert not app.exception
        assert app.session_state["last_result_id"] == result.result_id
        assert not app.get("progress")
        assert app.button(key="dismiss_run_notice")
    finally:
        release_worker(ws.runtime.catalog.paths.root, run.run_id)


def test_monitor_identifies_other_project_and_blocks_duplicate_launch(ui_workspace, monkeypatch):
    ws = ui_workspace
    run = ws.runtime.coordinator.create_run(
        _spec(
            ws.other_project.project_id,
            ws.other_import.version.version_id,
            AnalysisModuleId.PROFILING,
            "profile",
            {},
            (),
        ),
        request_key="monitor-other-project",
    )
    app = _app()
    assert app.session_state["active_run_id"] == run.run_id
    assert any("another project" in item.value for item in app.info)
    before = ws.runtime.run_store.list_runs(ws.project.project_id)
    app.button(key="run_profile").click().run(timeout=30)
    assert not app.exception
    assert ws.runtime.run_store.list_runs(ws.project.project_id) == before


def test_project_rename_keeps_selected_project_data_and_reports(ui_workspace):
    ws = ui_workspace
    result = _profile(
        ws, ws.project.project_id, ws.first_import.version.version_id, "rename-project"
    )
    reports = ReportService(ws.runtime.catalog, ws.runtime.artifact_store)
    report = reports.render(result.result_id, format="html")
    content = reports.artifact_bytes(report)
    app = _app()
    key = f"project_name_{ws.project.project_id}_0"
    app.text_input(key=key).input("Renamed project").run(timeout=30)
    app.button(key="rename_project").click().run(timeout=30)
    assert not app.exception
    assert app.selectbox(key="project_selector").value.name == "Renamed project"
    assert app.selectbox(key="project_selector").value.project_id == ws.project.project_id
    assert ws.projects.get(ws.project.project_id).revision == 1
    assert ws.datasets.current_version(ws.first.dataset_id) == ws.first_import.version
    assert reports.artifact_bytes(report) == content


def test_archive_and_restore_project_preserve_existing_results(ui_workspace):
    ws = ui_workspace
    result = _profile(
        ws, ws.project.project_id, ws.first_import.version.version_id, "archive-project"
    )
    app = _app()
    app.button(key="archive_project").click().run(timeout=30)
    assert not app.exception
    assert ws.projects.get(ws.project.project_id).archived_at is not None
    assert "A project" not in app.selectbox(key="project_selector").options
    assert ws.runtime.run_store.get_result(result.result_id) == result
    app.selectbox(key="archived_project_selector").select(ws.project.project_id).run(timeout=30)
    app.button(key="restore_project").click().run(timeout=30)
    assert not app.exception
    assert app.selectbox(key="project_selector").value.project_id == ws.project.project_id
    assert ws.projects.get(ws.project.project_id).archived_at is None
    assert ws.projects.get(ws.project.project_id).revision == 2
    assert ws.runtime.run_store.get_result(result.result_id) == result


def test_stale_rename_callback_cannot_overwrite_another_session(ui_workspace):
    ws = ui_workspace
    old = _app()
    old.text_input(key=f"project_name_{ws.project.project_id}_0").input("Stale rename").run(
        timeout=30
    )
    fresh = _app()
    fresh.text_input(key=f"project_name_{ws.project.project_id}_0").input("New name").run(
        timeout=30
    )
    fresh.button(key="rename_project").click().run(timeout=30)
    old.button(key="rename_project").click().run(timeout=30)
    assert not old.exception
    assert ws.projects.get(ws.project.project_id).name == "New name"
    assert any("another session" in item.value for item in old.error)


def test_project_archive_is_blocked_when_an_analysis_is_pending(ui_workspace):
    ws = ui_workspace
    ws.runtime.coordinator.create_run(
        _spec(
            ws.project.project_id,
            ws.first_import.version.version_id,
            AnalysisModuleId.PROFILING,
            "profile",
            {},
            (),
        ),
        request_key="archive-pending",
    )
    app = _app()
    assert app.button(key="archive_project").disabled
    assert ws.projects.get(ws.project.project_id).archived_at is None


def test_last_archived_project_can_be_restored_without_creating_a_new_project(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AUTOANALYST_WORKSPACE", str(tmp_path))
    projects = ProjectService(SQLiteCatalog(tmp_path))
    project = projects.create("Only project")
    app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
    app.button(key="archive_project").click().run(timeout=30)
    assert not app.exception
    assert projects.list() == ()
    app.button(key="restore_project").click().run(timeout=30)
    assert not app.exception
    assert app.selectbox(key="project_selector").value.project_id == project.project_id


def _add_ui_version(ws, source=b"value,group\n10,c\n20,d\n"):
    return ws.ingestor.import_csv(
        project_id=ws.project.project_id, dataset_id=ws.first.dataset_id, source=source
    ).version


def _review_restore(app, ws, target):
    head = ws.datasets.get(ws.first.dataset_id)
    key = f"restore_target_{ws.first.dataset_id}_{head.head_revision}"
    app.selectbox(key=key).select(target).run(timeout=30)
    app.button(key="preview_version_restore").click().run(timeout=30)
    assert not app.exception
    assert app.button(key="apply_version_restore")


def test_ui_version_restore_and_redo_preserve_history_and_clear_preparation(ui_workspace):
    ws = ui_workspace
    second = _add_ui_version(ws)
    history = ws.datasets.version_history(ws.first.dataset_id)
    app = _app()
    _preview_rename(app)
    _review_restore(app, ws, ws.first_import.version.version_id)
    assert list(app.session_state["version_restore_review"]["frame"]["value"]) == [1, 2, 3]
    app.button(key="apply_version_restore").click().run(timeout=30)
    assert not app.exception
    assert (
        ws.datasets.get(ws.first.dataset_id).head_version_id == ws.first_import.version.version_id
    )
    assert ws.datasets.get(ws.first.dataset_id).head_revision == 3
    assert "preview_id" not in app.session_state
    assert "version_restore_review" not in app.session_state
    _review_restore(app, ws, second.version_id)
    app.button(key="apply_version_restore").click().run(timeout=30)
    assert not app.exception
    assert ws.datasets.get(ws.first.dataset_id).head_version_id == second.version_id
    assert ws.datasets.get(ws.first.dataset_id).head_revision == 4
    assert ws.datasets.version_history(ws.first.dataset_id) == history
    assert (
        ws.runtime.catalog.list_head_events(ws.first.dataset_id)[-1]["reason"] == "restore_version"
    )


@pytest.mark.parametrize("change", ["project", "dataset", "target", "head"])
def test_version_restore_review_is_invalidated_by_context_changes(ui_workspace, change):
    ws = ui_workspace
    second = _add_ui_version(ws)
    third = _add_ui_version(ws, b"value,group\n30,e\n")
    app = _app()
    _review_restore(app, ws, ws.first_import.version.version_id)
    if change == "project":
        _select(app, "Project", "B project")
    elif change == "dataset":
        _select(app, "Dataset", "B data")
    elif change == "target":
        app.selectbox(key=f"restore_target_{ws.first.dataset_id}_3").select(second.version_id).run(
            timeout=30
        )
    else:
        _add_ui_version(ws, b"value,group\n40,f\n")
        app.run(timeout=30)
    assert not app.exception
    assert "version_restore_review" not in app.session_state
    assert not any(button.key == "apply_version_restore" for button in app.button)
    assert ws.datasets.get(ws.first.dataset_id).head_version_id == (
        third.version_id
        if change != "head"
        else ws.datasets.current_version(ws.first.dataset_id).version_id
    )


def test_restore_review_is_bounded_and_does_not_create_versions_or_artifacts(ui_workspace):
    ws = ui_workspace
    second = _add_ui_version(ws, b"value,group\n" + b"1,a\n" * 1000)
    _add_ui_version(ws)
    before = ws.datasets.version_history(ws.first.dataset_id)
    with ws.runtime.catalog.connection() as connection:
        artifacts_before = connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
    app = _app()
    _review_restore(app, ws, second.version_id)
    assert len(app.session_state["version_restore_review"]["frame"]) == 25
    assert ws.datasets.version_history(ws.first.dataset_id) == before
    with ws.runtime.catalog.connection() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == artifacts_before
        )


def test_duplicate_project_names_are_distinguishable_when_managing_projects(ui_workspace):
    ws = ui_workspace
    duplicate = ws.projects.create(ws.project.name)
    app = _app()
    options = app.selectbox(key="project_selector").options
    assert f"A project · {ws.project.project_id[:8]}" in options
    assert f"A project · {duplicate.project_id[:8]}" in options
    app.selectbox(key="project_selector").select(duplicate).run(timeout=30)
    app.text_input(key=f"project_name_{duplicate.project_id}_0").input("Distinct name").run(
        timeout=30
    )
    app.button(key="rename_project").click().run(timeout=30)
    assert not app.exception
    assert ws.projects.get(duplicate.project_id).name == "Distinct name"
    assert ws.projects.get(ws.project.project_id).name == "A project"


def _add_recipe_step(app, operation):
    app.selectbox(key="prep_operation").select(operation).run(timeout=30)


def _save_recipe_step(app):
    app.button(key="save_recipe_step").click().run(timeout=30)
    assert not app.exception


def test_multi_step_order_changes_result_and_invalidates_preview(ui_workspace):
    ws = ui_workspace
    app = _app()
    app.checkbox(key="prep_multi").check().run(timeout=30)
    _add_recipe_step(app, "replace_values")
    app.selectbox(key="prep_replace_col").select("value").run(timeout=30)
    next(item for item in app.number_input if item.key.startswith("replace_from_0_")).set_value(
        1
    ).run(timeout=30)
    next(item for item in app.number_input if item.key.startswith("replace_to_0_")).set_value(
        9
    ).run(timeout=30)
    _save_recipe_step(app)
    _add_recipe_step(app, "filter_rows")
    next(item for item in app.selectbox if item.key.startswith("prep_operator_")).select("gt").run(
        timeout=30
    )
    next(item for item in app.number_input if item.key.startswith("prep_filter_value_")).set_value(
        2
    ).run(timeout=30)
    _save_recipe_step(app)
    app.button(key="preview_preparation").click().run(timeout=30)
    assert not app.exception
    preview = PreparationService(ws.runtime.catalog, ws.runtime.artifact_store).get_preview(
        app.session_state["preview_id"]
    )
    assert preview.after_row_count == 2
    uid = app.session_state["prep_draft"][1]["uid"]
    app.button(key="step_up_" + uid).click().run(timeout=30)
    assert "preview_id" not in app.session_state
    app.button(key="preview_preparation").click().run(timeout=30)
    assert not app.exception
    preview = PreparationService(ws.runtime.catalog, ws.runtime.artifact_store).get_preview(
        app.session_state["preview_id"]
    )
    assert preview.after_row_count == 1
    app.button(key="apply_preparation").click().run(timeout=30)
    assert not app.exception
    assert ws.datasets.current_version(ws.first.dataset_id).row_count == 1
    assert app.session_state["prep_draft"] == []


def test_recipe_edit_uses_saved_values_and_requires_new_preview(ui_workspace):
    app = _app()
    app.checkbox(key="prep_multi").check().run(timeout=30)
    next(item for item in app.selectbox if item.key.startswith("prep_operator_")).select("gte").run(
        timeout=30
    )
    next(item for item in app.number_input if item.key.startswith("prep_filter_value_")).set_value(
        2
    ).run(timeout=30)
    _save_recipe_step(app)
    app.button(key="preview_preparation").click().run(timeout=30)
    uid = app.session_state["prep_draft"][0]["uid"]
    app.button(key="step_edit_" + uid).click().run(timeout=30)
    assert not app.exception
    field = next(item for item in app.number_input if item.key.startswith("prep_filter_value_"))
    assert field.value == 2
    assert app.button(key="preview_preparation").disabled
    field.set_value(3).run(timeout=30)
    _save_recipe_step(app)
    assert app.session_state["prep_draft"][0]["parameters"]["value"] == 3
    assert "preview_id" not in app.session_state
    app.button(key="preview_preparation").click().run(timeout=30)
    assert not app.exception


def test_recipe_schema_tracks_rename_and_cast_for_later_steps(ui_workspace):
    app = _app()
    app.checkbox(key="prep_multi").check().run(timeout=30)
    _add_recipe_step(app, "rename_column")
    app.selectbox(key="prep_rename_col").select("value").run(timeout=30)
    app.text_input(key="prep_new_name").input("amount").run(timeout=30)
    _save_recipe_step(app)
    _add_recipe_step(app, "cast_column")
    app.selectbox(key="prep_cast_col").select("amount").run(timeout=30)
    app.selectbox(key="prep_cast_type").select("numeric").run(timeout=30)
    _save_recipe_step(app)
    _add_recipe_step(app, "filter_rows")
    assert "amount" in app.selectbox(key="prep_filter_col").options
    app.selectbox(key="prep_filter_col").select("amount").run(timeout=30)
    field = next(item for item in app.number_input if item.key.startswith("prep_filter_value_"))
    assert field.key.endswith("numeric")
    field.set_value(2.0).run(timeout=30)
    _save_recipe_step(app)
    app.button(key="preview_preparation").click().run(timeout=30)
    assert not app.exception
    assert "amount" in app.session_state["prep_sample_cache"][2].columns


@pytest.mark.parametrize("change", ["project", "dataset", "head", "aba"])
def test_recipe_draft_is_cleared_when_its_base_context_changes(ui_workspace, change):
    ws = ui_workspace
    app = _app()
    app.checkbox(key="prep_multi").check().run(timeout=30)
    _save_recipe_step(app)
    if change == "project":
        _select(app, "Project", "B project")
    elif change == "dataset":
        _select(app, "Dataset", "B data")
    elif change == "head":
        _add_ui_version(ws)
        app.run(timeout=30)
    else:
        second = _add_ui_version(ws)
        ws.datasets.move_head(ws.first.dataset_id, ws.first_import.version.version_id)
        assert second.version_id != ws.first_import.version.version_id
        app.run(timeout=30)
    assert not app.exception
    assert app.session_state["prep_draft"] == []
    assert "preview_id" not in app.session_state


def test_recipe_remove_clear_and_language_change(ui_workspace):
    app = _app()
    app.checkbox(key="prep_multi").check().run(timeout=30)
    _save_recipe_step(app)
    uid = app.session_state["prep_draft"][0]["uid"]
    app.button(key="preview_preparation").click().run(timeout=30)
    preview_id = app.session_state["preview_id"]
    app.selectbox(key="language").select("tr").run(timeout=30)
    assert not app.exception
    assert app.session_state["preview_id"] == preview_id
    app.button(key="step_remove_" + uid).click().run(timeout=30)
    assert app.session_state["prep_draft"] == []
    assert app.button(key="preview_preparation").disabled
    _save_recipe_step(app)
    app.button(key="clear_recipe").click().run(timeout=30)
    assert not app.exception and app.session_state["prep_draft"] == []


def _ui_append_branch(ws, operator, value):
    column = next(
        c
        for c in ws.runtime.catalog.list_columns(ws.first_import.version.version_id)
        if c["display_name"] == "value" and not c["is_system"]
    )
    service = PreparationService(ws.runtime.catalog, ws.runtime.artifact_store)
    recipe = service.create_recipe(
        project_id=ws.project.project_id,
        base_version_id=ws.first_import.version.version_id,
        steps=(
            {
                "operation": "filter_rows",
                "parameters": {
                    "column_id": column["column_id"],
                    "operator": operator,
                    "value": value,
                },
            },
        ),
    )
    branch = service.apply_preview(service.preview_recipe(recipe.recipe_id).preview_id)
    ws.datasets.move_head(ws.first.dataset_id, ws.first_import.version.version_id)
    return branch


def test_append_ui_preserves_input_order_row_identity_and_lineage(ui_workspace):
    ws = ui_workspace
    first = _ui_append_branch(ws, "gte", 2)
    second = _ui_append_branch(ws, "lte", 2)
    app = _app()
    _add_recipe_step(app, "append_rows")
    app.multiselect(key="prep_append_inputs").set_value([second.version_id, first.version_id]).run(
        timeout=30
    )
    app.button(key="preview_preparation").click().run(timeout=30)
    assert not app.exception
    assert app.session_state["prep_sample_cache"][2]["value"].tolist() == [1, 2, 3, 1, 2, 2, 3]
    app.button(key="apply_preparation").click().run(timeout=30)
    assert not app.exception
    version = ws.datasets.current_version(ws.first.dataset_id)
    lineage = ws.runtime.catalog.get_version_lineage(version.version_id)
    assert [row["input_version_id"] for row in lineage] == [
        ws.first_import.version.version_id,
        second.version_id,
        first.version_id,
    ]
    row_id = next(
        c
        for c in ws.runtime.catalog.list_columns(version.version_id)
        if c["physical_name"] == "__aa_internal_row_id__"
    )
    ids = (
        TableAccess(ws.runtime.catalog, ws.runtime.artifact_store)
        .page(version.version_id, (row_id["column_id"],))
        .column(0)
        .to_pylist()
    )
    assert len(ids) == len(set(ids)) == 7


def test_append_ui_explains_incompatible_independent_imports(ui_workspace):
    app = _app()
    _add_recipe_step(app, "append_rows")
    assert not app.multiselect(key="prep_append_inputs").options
    assert app.button(key="preview_preparation").disabled
    assert any("No compatible" in item.value for item in app.info)


def test_replace_ui_preserves_types_and_does_not_cascade(ui_workspace):
    app = _app()
    _add_recipe_step(app, "replace_values")
    app.selectbox(key="prep_replace_col").select("value").run(timeout=30)
    app.number_input(key="prep_replace_count").set_value(2).run(timeout=30)
    for index, (old, new) in enumerate(((1, 2), (2, 3))):
        next(
            item for item in app.number_input if item.key.startswith(f"replace_from_{index}_")
        ).set_value(old).run(timeout=30)
        next(
            item for item in app.number_input if item.key.startswith(f"replace_to_{index}_")
        ).set_value(new).run(timeout=30)
    app.button(key="preview_preparation").click().run(timeout=30)
    assert not app.exception
    assert app.session_state["prep_sample_cache"][2]["value"].tolist() == [2, 3, 3]
    app.button(key="apply_preparation").click().run(timeout=30)
    assert not app.exception


def test_duplicate_replace_sources_show_an_explanation_without_applying(ui_workspace):
    ws = ui_workspace
    app = _app()
    _add_recipe_step(app, "replace_values")
    app.number_input(key="prep_replace_count").set_value(2).run(timeout=30)
    app.button(key="preview_preparation").click().run(timeout=30)
    assert not app.exception
    assert "preview_id" not in app.session_state
    assert any("appears more than once" in item.value for item in app.error)
    assert ws.datasets.get(ws.first.dataset_id).head_revision == 1


def test_recipe_failure_identifies_step_and_preserves_original_data(ui_workspace):
    ws = ui_workspace
    app = _app()
    app.checkbox(key="prep_multi").check().run(timeout=30)
    _add_recipe_step(app, "rename_column")
    app.selectbox(key="prep_rename_col").select("group").run(timeout=30)
    app.text_input(key="prep_new_name").input("category").run(timeout=30)
    _save_recipe_step(app)
    _add_recipe_step(app, "cast_column")
    app.selectbox(key="prep_cast_col").select("category").run(timeout=30)
    app.selectbox(key="prep_cast_type").select("numeric").run(timeout=30)
    _save_recipe_step(app)
    app.button(key="preview_preparation").click().run(timeout=30)
    assert not app.exception
    assert any("Step 2:" in item.value for item in app.error)
    assert "preview_id" not in app.session_state
    assert ws.datasets.current_version(ws.first.dataset_id) == ws.first_import.version


def _snapshot_upload(ws, tmp_path, monkeypatch):
    from autoanalyst.application.snapshots import WorkspaceSnapshotService

    path = WorkspaceSnapshotService(ws.runtime.catalog, ws.runtime.artifact_store).export_snapshot(
        tmp_path / "source.aasnapshot"
    )
    raw = {"value": path.read_bytes()}
    upload = SimpleNamespace(name="source.aasnapshot", getvalue=lambda: raw["value"])
    monkeypatch.setattr(
        st,
        "file_uploader",
        lambda *args, **kwargs: upload if kwargs.get("key") == "snapshot_upload" else None,
    )
    return raw


def test_backup_button_exports_entire_workspace_and_offers_download(ui_workspace):
    from autoanalyst.application.snapshots import WorkspaceSnapshotService

    ws = ui_workspace
    app = _app()
    path = ws.tmp_path / "export.aasnapshot"
    app.text_input(key="snapshot_export_destination").input(str(path)).run(timeout=30)
    app.button(key="create_snapshot").click().run(timeout=30)
    assert not app.exception and path.is_file()
    assert app.get("download_button")
    summary = WorkspaceSnapshotService.inspect_snapshot(path)
    assert summary.projects == 2 and summary.datasets == 3
    assert ws.datasets.current_version(ws.first.dataset_id) == ws.first_import.version


def test_snapshot_restore_needs_review_and_trust_then_can_switch_and_return(
    ui_workspace, monkeypatch
):
    import os

    ws = ui_workspace
    original_env = os.environ["AUTOANALYST_WORKSPACE"]
    _snapshot_upload(ws, ws.tmp_path, monkeypatch)
    target = ws.tmp_path / "restored"
    app = _app()
    app.text_input(key="snapshot_restore_destination").input(str(target)).run(timeout=30)
    app.button(key="inspect_snapshot").click().run(timeout=30)
    assert not app.exception
    assert app.button(key="restore_snapshot").disabled
    next(item for item in app.checkbox if item.key.startswith("snapshot_trust_")).check().run(
        timeout=30
    )
    app.button(key="restore_snapshot").click().run(timeout=30)
    assert not app.exception and (target / "catalog.sqlite").is_file()
    assert app.session_state.get("active_workspace") is None
    app.button(key="open_restored_workspace").click().run(timeout=30)
    assert not app.exception
    assert Path(app.session_state["active_workspace"]) == target.resolve()
    assert os.environ["AUTOANALYST_WORKSPACE"] == original_env
    assert app.selectbox(key="project_selector").value.project_id in {
        ws.project.project_id,
        ws.other_project.project_id,
    }
    app.button(key="return_workspace").click().run(timeout=30)
    assert not app.exception
    assert Path(app.session_state["active_workspace"]) == ws.runtime.catalog.paths.root


@pytest.mark.parametrize("change", ["upload", "destination"])
def test_snapshot_review_is_bound_to_uploaded_bytes_and_destination(
    ui_workspace, monkeypatch, change
):
    ws = ui_workspace
    raw = _snapshot_upload(ws, ws.tmp_path, monkeypatch)
    app = _app()
    app.button(key="inspect_snapshot").click().run(timeout=30)
    assert "snapshot_inspection" in app.session_state
    if change == "upload":
        raw["value"] = b"changed archive"
        app.run(timeout=30)
    else:
        app.text_input(key="snapshot_restore_destination").input(
            str(ws.tmp_path / "other-restore")
        ).run(timeout=30)
    assert not app.exception
    assert "snapshot_inspection" not in app.session_state
    assert not any(item.key == "restore_snapshot" for item in app.button)


@pytest.mark.parametrize("kind", ["current", "nonempty"])
def test_snapshot_ui_rejects_current_or_nonempty_restore_folder(ui_workspace, monkeypatch, kind):
    ws = ui_workspace
    _snapshot_upload(ws, ws.tmp_path, monkeypatch)
    target = ws.runtime.catalog.paths.root if kind == "current" else ws.tmp_path / "occupied"
    target.mkdir(exist_ok=True)
    marker = target / "keep.txt"
    marker.write_text("existing data")
    app = _app()
    app.text_input(key="snapshot_restore_destination").input(str(target)).run(timeout=30)
    app.button(key="inspect_snapshot").click().run(timeout=30)
    assert not app.exception and app.error
    assert "snapshot_inspection" not in app.session_state
    assert marker.read_text() == "existing data"


def test_backup_and_workspace_switch_are_disabled_during_active_work(ui_workspace):
    ws = ui_workspace
    ws.runtime.coordinator.create_run(
        _spec(
            ws.project.project_id,
            ws.first_import.version.version_id,
            AnalysisModuleId.PROFILING,
            "profile",
            {},
            (),
        ),
        request_key="backup-active",
    )
    app = _app()
    assert app.button(key="create_snapshot").disabled
    assert app.button(key="open_workspace").disabled


def test_empty_workspace_still_has_backup_controls(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOANALYST_WORKSPACE", str(tmp_path / "empty-workspace"))
    app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
    assert not app.exception
    assert app.button(key="create_snapshot")
    assert app.text_input(key="snapshot_restore_destination")


def test_storage_ui_review_quarantine_and_restore(ui_workspace):
    import os
    import time as clock_time
    from uuid import uuid4

    from autoanalyst.application.storage_maintenance import MIN_AGE_SECONDS

    ws = ui_workspace
    staged = ws.runtime.artifact_store.stage_bytes(
        b"orphan",
        project_id=ws.project.project_id,
        owner_run_id=str(uuid4()),
        kind="test",
        media_type="application/octet-stream",
    )
    old = clock_time.time() - MIN_AGE_SECONDS - 60
    os.utime(staged.staging_path, (old, old))
    app = _app()
    assert "storage_review" not in app.session_state
    app.button(key="storage_scan").click().run(timeout=30)
    assert not app.exception
    assert app.button(key="maintenance_quarantine").disabled
    relative = staged.staging_path.relative_to(ws.runtime.catalog.paths.root).as_posix()
    app.multiselect(key="storage_selection").select(relative).run(timeout=30)
    app.button(key="maintenance_quarantine").click().run(timeout=30)
    assert not app.exception and not app.error
    assert not staged.staging_path.exists()
    assert "storage_review" not in app.session_state
    app.button(key="maintenance_restore").click().run(timeout=30)
    assert not app.exception and not app.error
    assert staged.staging_path.read_bytes() == b"orphan"


def test_empty_workspace_storage_scan_is_available(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOANALYST_WORKSPACE", str(tmp_path / "empty"))
    app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
    app.button(key="storage_scan").click().run(timeout=30)
    assert not app.exception
    assert not app.session_state["storage_review"].candidates


@pytest.mark.parametrize("fmt", ["csv", "parquet"])
def test_data_export_ui_creates_complete_version_file(ui_workspace, fmt):
    ws = ui_workspace
    app = _app()
    app.selectbox(key="data_export_format").select(fmt).run(timeout=30)
    target = ws.tmp_path / f"clean-data.{fmt}"
    next(field for field in app.text_input if field.key.startswith("data_export_path_")).input(
        str(target)
    ).run(timeout=30)
    assert not app.get("download_button")
    app.button(key="data_export_create").click().run(timeout=30)
    assert not app.exception and not app.error
    receipt = app.session_state["data_export_ready"]
    assert receipt.version_id == ws.first_import.version.version_id
    assert receipt.rows == 3 and receipt.columns == ("value", "group")
    assert target.exists() and app.get("download_button")
    app.button(key="data_export_create").click().run(timeout=30)
    assert not app.exception and app.error
    assert target.exists() and "data_export_ready" not in app.session_state


@pytest.mark.parametrize("change", ["format", "destination", "dataset", "version"])
def test_data_export_download_is_bound_to_reviewed_context(ui_workspace, change):
    ws = ui_workspace
    app = _app()
    target = ws.tmp_path / "clean-data.csv"
    next(field for field in app.text_input if field.key.startswith("data_export_path_")).input(
        str(target)
    ).run(timeout=30)
    app.button(key="data_export_create").click().run(timeout=30)
    assert app.get("download_button")
    if change == "format":
        app.selectbox(key="data_export_format").select("parquet").run(timeout=30)
    elif change == "destination":
        next(field for field in app.text_input if field.key.startswith("data_export_path_")).input(
            str(ws.tmp_path / "other.csv")
        ).run(timeout=30)
    elif change == "dataset":
        _select(app, "Dataset", "B data")
    else:
        ws.ingestor.import_csv(
            project_id=ws.project.project_id,
            dataset_id=ws.first.dataset_id,
            source=b"value,group\n99,a\n",
        )
        app.run(timeout=30)
    assert not app.exception and "data_export_ready" not in app.session_state
    assert not app.get("download_button")
    assert target.exists()


def test_saved_summary_and_report_use_original_version_after_import(ui_workspace, monkeypatch):
    from autoanalyst.reporting.summary import result_summary

    ws = ui_workspace
    result = _profile(
        ws, ws.project.project_id, ws.first_import.version.version_id, "summary-original"
    )
    expected = result_summary(result, "en")
    ws.ingestor.import_csv(
        project_id=ws.project.project_id,
        dataset_id=ws.first.dataset_id,
        source=b"value,group\n99,a\n",
    )

    def no_reanalysis(*args, **kwargs):
        raise AssertionError("summary must not run analysis")

    monkeypatch.setattr(ProfilingModule, "run", no_reanalysis)
    app = _app()
    for line in expected.lines:
        assert any(item.value == line for item in app.text)
    app.button(key="build_report").click().run(timeout=30)
    assert not app.exception and not app.error
    reports = ReportService(ws.runtime.catalog, ws.runtime.artifact_store)
    report = reports.get(app.session_state["report_id"])
    assert ws.first_import.version.version_id.encode() in reports.artifact_bytes(report)


def test_isolated_import_ui_reconnects_and_updates_dataset(ui_workspace, monkeypatch):
    import time as clock

    ws = ui_workspace
    upload = SimpleNamespace(name="isolated.csv", getvalue=lambda: b"value,group\n40,a\n50,b\n")
    monkeypatch.setattr(
        st,
        "file_uploader",
        lambda *a, **k: upload if k.get("key", "").startswith("upload_") else None,
    )
    app = _app()
    app.button(key="preview_import").click().run(timeout=30)
    next(item for item in app.checkbox if item.key.startswith("isolate_import_")).check().run(
        timeout=30
    )
    app.button(key="import_csv").click().run(timeout=30)
    deadline = clock.monotonic() + 30
    while clock.monotonic() < deadline and "data_task_delivery" not in app.session_state:
        clock.sleep(0.05)
        app.run(timeout=30)
    app.run(timeout=30)
    assert not app.exception
    assert ws.datasets.current_version(ws.first.dataset_id).row_count == 2
    assert app.session_state["data_task_delivery"]["status"] == "completed"


def test_command_ui_preview_apply_and_stale_input(ui_workspace):
    ws = ui_workspace
    app = _app()
    app.text_input(key="command_text").set_value("filter value >= 2").run(timeout=30)
    app.button(key="make_command_plan").click().run(timeout=30)
    assert "command_plan" in app.session_state
    app.button(key="preview_command").click().run(timeout=30)
    assert not app.exception and "command_preview" in app.session_state
    assert ws.datasets.current_version(ws.first.dataset_id).row_count == 3
    app.text_input(key="command_text").set_value("filter value >= 3").run(timeout=30)
    assert "command_preview" not in app.session_state
    app.button(key="make_command_plan").click().run(timeout=30)
    app.button(key="preview_command").click().run(timeout=30)
    app.button(key="apply_command").click().run(timeout=30)
    assert not app.exception and ws.datasets.current_version(ws.first.dataset_id).row_count == 1


@pytest.mark.parametrize(
    "task",
    [
        "Model audit",
        "Confidence interval",
        "Calibration report",
        "Data drift",
        "Regression",
        "Multiclass classification",
        "Correlation",
    ],
)
def test_new_analysis_forms_render_without_errors(ui_workspace, task):
    app = _app()
    app.selectbox(key="analysis_task").set_value(task).run(timeout=30)
    assert not app.exception and not app.error


@pytest.mark.parametrize("fmt", ["pdf", "xlsx"])
def test_binary_report_ui_download_is_bound_to_result(ui_workspace, fmt):
    ws = ui_workspace
    _profile(ws, ws.project.project_id, ws.first_import.version.version_id, "binary-format-ui")
    app = _app()
    _select(app, "Format", fmt)
    app.button(key="build_report").click().run(timeout=30)
    assert not app.exception and not app.error
    service = ReportService(ws.runtime.catalog, ws.runtime.artifact_store)
    report = service.get(app.session_state["report_id"])
    assert report.format == fmt and service.artifact_bytes(report)


def test_isolated_preparation_ui_keeps_apply_explicit_and_invalidates_stale_context(ui_workspace):
    import time as clock

    ws = ui_workspace
    app = _app()
    next(item for item in app.checkbox if item.key.startswith("isolate_preparation_")).check().run(
        timeout=30
    )
    app.button(key="preview_preparation").click().run(timeout=30)
    deadline = clock.monotonic() + 30
    while clock.monotonic() < deadline and "preview_id" not in app.session_state:
        clock.sleep(0.05)
        app.run(timeout=30)
    assert not app.exception and "preview_id" in app.session_state
    assert ws.datasets.current_version(ws.first.dataset_id) == ws.first_import.version
    assert app.button(key="apply_preparation")
    next(item for item in app.number_input if item.key.startswith("prep_filter_value_")).set_value(
        2
    ).run(timeout=30)
    assert not app.exception and "preview_id" not in app.session_state


def test_empty_project_and_dataset_names_cannot_be_submitted(ui_workspace):
    app = _app()
    assert app.button(key="create_project").disabled
    assert app.button(key="create_dataset").disabled


def test_prepared_training_is_blocked_before_worker_launch(ui_workspace):
    ws = ui_workspace
    column = next(
        row
        for row in ws.runtime.catalog.list_columns(ws.first_import.version.version_id)
        if row["display_name"] == "value"
    )
    service = PreparationService(ws.runtime.catalog, ws.runtime.artifact_store)
    recipe = service.create_recipe(
        project_id=ws.project.project_id,
        base_version_id=ws.first_import.version.version_id,
        steps=[
            {
                "operation": "fill_missing",
                "parameters": {"column_ids": [column["column_id"]], "strategy": "median"},
            }
        ],
    )
    preview = service.preview_recipe(recipe.recipe_id)
    service.apply_preview(preview.preview_id)
    app = _app()
    app.selectbox(key="analysis_task").select("Binary classification").run(timeout=30)
    app.selectbox(key="binary_target").select("group").run(timeout=30)
    app.multiselect(key="binary_features").select("value").run(timeout=30)
    assert not app.exception
    assert app.button(key="run_binary").disabled
    assert any("preparation learned from all rows" in item.value for item in app.warning)
    assert not ws.runtime.run_store.list_runs(ws.project.project_id)


def test_selected_tab_survives_rerender_and_completion_can_navigate(ui_workspace):
    app = _app()
    app.session_state["workspace_tab"] = "Analysis"
    app.run(timeout=30)
    assert not app.exception
    assert app.session_state["workspace_tab"] == "Analysis"
    app.session_state["pending_workspace_tab"] = "results"
    app.run(timeout=30)
    assert not app.exception
    assert app.session_state["workspace_tab"] == "Results"

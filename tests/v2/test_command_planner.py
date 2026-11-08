import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from autoanalyst.application.commands import Clarification, CommandProposal, CommandService
from autoanalyst.application.planner import HTTPPlanner, parse_plan, propose_with_fallback
from autoanalyst.domain.errors import SchemaError


def service(ws):
    return CommandService(ws.catalog, ws.store)


def interpret(ws, text):
    return service(ws).interpret(
        text,
        project_id=ws.project.project_id,
        dataset_id=ws.dataset.dataset_id,
        version_id=ws.imported.version.version_id,
    )


def test_command_proposal_preview_apply_and_stale_revision(phase3_workspace):
    ws = phase3_workspace
    plan = interpret(ws, 'filter "num" >= 2')
    assert isinstance(plan, CommandProposal)
    assert ws.datasets.current_version(ws.dataset.dataset_id) == ws.imported.version
    preview = service(ws).preview(plan)
    assert preview.after_row_count == 3
    result = service(ws).apply(plan, preview.preview_id)
    assert result.row_count == 3 and result.version_id != ws.imported.version.version_id
    with pytest.raises(SchemaError):
        service(ws).preview(plan)
    with pytest.raises(SchemaError):
        service(ws).apply(replace(plan, workspace="other"), preview.preview_id)


@pytest.mark.parametrize(
    "command",
    [
        '__import__("os")',
        "filter num > eval(1)",
        "rename missing other",
        "filter num >",
        "filter num SQL 10",
        "replace num 1 NaN",
    ],
)
def test_ambiguous_or_code_commands_do_not_mutate(phase3_workspace, command):
    ws = phase3_workspace
    assert isinstance(interpret(ws, command), Clarification)
    assert ws.datasets.current_version(ws.dataset.dataset_id) == ws.imported.version


def test_provider_is_never_called_without_consent(phase3_workspace):
    ws = phase3_workspace

    def forbidden(*a, **k):
        pytest.fail("provider called without consent")

    value, fallback = propose_with_fallback(
        service(ws),
        "drop_missing num",
        project_id=ws.project.project_id,
        dataset_id=ws.dataset.dataset_id,
        version_id=ws.imported.version.version_id,
        provider=SimpleNamespace(generate=forbidden),
        consent=False,
    )
    assert isinstance(value, CommandProposal) and fallback


@pytest.mark.parametrize(
    "payload",
    [
        '{"steps":[],"steps":[]}',
        '{"steps":[{"operation":"python","parameters":{"code":"print(1)"}}]}',
        '{"steps":[{"operation":"filter_rows","parameters":{"column_id":"bad","operator":"eq","value":1}}]}',
        '{"steps":NaN}',
        "not-json",
    ],
)
def test_untrusted_model_response_is_rejected_and_falls_back(phase3_workspace, payload):
    ws = phase3_workspace
    provider = SimpleNamespace(generate=lambda *a, **k: parse_plan(payload))
    result, fallback = propose_with_fallback(
        service(ws),
        "drop_missing num",
        project_id=ws.project.project_id,
        dataset_id=ws.dataset.dataset_id,
        version_id=ws.imported.version.version_id,
        provider=provider,
        consent=True,
    )
    assert isinstance(result, CommandProposal) and fallback


def test_http_request_is_metadata_only_and_bounded(phase3_workspace, monkeypatch):
    import autoanalyst.application.planner as module

    ws = phase3_workspace
    column = ws.column("num")["column_id"]
    response = {
        "message": {
            "content": json.dumps(
                {
                    "steps": [
                        {"operation": "drop_missing_rows", "parameters": {"column_ids": [column]}}
                    ]
                }
            )
        }
    }
    captured = {}

    class Connection:
        sock = None

        def __init__(self, *a, **k):
            pass

        def request(self, method, path, body, headers):
            captured.update(json.loads(body))

        def getresponse(self):
            raw = iter([json.dumps(response).encode(), b""])
            return SimpleNamespace(status=200, read1=lambda n: next(raw))

        def close(self):
            pass

    monkeypatch.setattr(module.http.client, "HTTPConnection", Connection)
    planner = HTTPPlanner("http://127.0.0.1:11434/api/chat", "test-model")
    steps = planner.generate(
        "remove missing", ws.catalog.list_columns(ws.imported.version.version_id), consent=True
    )
    assert steps[0]["operation"] == "drop_missing_rows"
    user = json.loads(captured["messages"][1]["content"])
    assert set(user) == {"request", "columns", "allowed_operations"}
    assert all(set(c) == {"column_id", "name", "type"} for c in user["columns"])
    assert captured["options"]["num_predict"] == 512 and captured["stream"] is False
    with pytest.raises(SchemaError):
        planner.generate("x", [], consent=False)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.com/api/chat",
        "https://user:password@example.com/api/chat",
        "file:///data",
        "http://127.0.0.1/api/chat#fragment",
    ],
)
def test_unsafe_provider_configuration_is_rejected(endpoint):
    with pytest.raises(SchemaError):
        HTTPPlanner(endpoint, "model")

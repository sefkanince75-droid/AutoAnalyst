"""Opt-in metadata-only Ollama-compatible planner with strict local validation."""

from __future__ import annotations

import http.client
import json
import time
from urllib.parse import urlsplit

from ..domain.codec import to_primitive
from ..domain.errors import ResourceError, SchemaError
from ..preparation.operations import OPERATION_REGISTRY


class HTTPPlanner:
    def __init__(self, endpoint: str, model: str, *, max_tokens: int = 512, timeout: float = 15):
        url = urlsplit(endpoint)
        try:
            _ = url.port
        except ValueError as exc:
            raise SchemaError({"reason": "planner.configuration"}) from exc
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.fragment
            or (url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"})
            or not model.strip()
            or len(model) > 200
            or type(max_tokens) is not int
            or not 32 <= max_tokens <= 2048
            or not 0 < timeout <= 30
        ):
            raise SchemaError({"reason": "planner.configuration"})
        self.url, self.model, self.max_tokens, self.timeout = url, model, max_tokens, timeout

    def generate(self, prompt, columns, *, consent=False):
        if consent is not True:
            raise SchemaError({"reason": "planner.consent"})
        if not isinstance(prompt, str) or not 1 <= len(prompt) <= 1000:
            raise SchemaError({"reason": "planner.response"})
        metadata = [
            {
                "column_id": str(c["column_id"]),
                "name": str(c["display_name"]),
                "type": str(c["physical_type"]),
            }
            for c in columns
            if not c["is_system"]
        ]
        allowed = {
            name: to_primitive(definition.parameter_schema)
            for name, definition in OPERATION_REGISTRY.items()
            if name not in {"join_rows", "append_rows"}
        }
        system = 'Return only JSON: {"steps":[{"operation":string,"parameters":object}]} or {"clarification":string}. Use supplied column IDs and allowed closed operations. Never return code, SQL, paths or execution instructions. All output is validated and manually previewed before any apply.'
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"request": prompt, "columns": metadata, "allowed_operations": allowed},
                        ensure_ascii=False,
                    ),
                },
            ],
            "options": {"num_predict": self.max_tokens, "temperature": 0},
        }
        body = json.dumps(to_primitive(payload), ensure_ascii=False).encode("utf-8")
        if len(body) > 32768:
            raise ResourceError({"reason": "planner.budget"})
        connection_type = (
            http.client.HTTPSConnection
            if self.url.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_type(str(self.url.hostname), self.url.port, timeout=self.timeout)
        started = time.monotonic()
        try:
            path = self.url.path or "/api/chat"
            if self.url.query:
                path += "?" + self.url.query
            connection.request(
                "POST", path, body=body, headers={"Content-Type": "application/json"}
            )
            response = connection.getresponse()
            if response.status != 200:
                raise SchemaError({"reason": "planner.unavailable"})
            data = bytearray()
            while True:
                remaining = self.timeout - (time.monotonic() - started)
                if remaining <= 0 or len(data) > 65536:
                    raise ResourceError({"reason": "planner.budget"})
                if connection.sock:
                    connection.sock.settimeout(remaining)
                chunk = response.read1(min(4096, 65537 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) > 65536:
                raise ResourceError({"reason": "planner.budget"})
            envelope = json.loads(bytes(data))
            content = envelope["message"]["content"]
            if not isinstance(content, str) or len(content) > 32768:
                raise SchemaError({"reason": "planner.response"})
            return parse_plan(content)
        except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException) as exc:
            raise SchemaError({"reason": "planner.unavailable"}) from exc
        finally:
            connection.close()


def parse_plan(content):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(
            content,
            object_pairs_hook=unique,
            parse_constant=lambda v: (_ for _ in ()).throw(ValueError("nonfinite")),
        )
        if (
            not isinstance(value, dict)
            or set(value) != {"steps"}
            or not isinstance(value["steps"], list)
            or not 1 <= len(value["steps"]) <= 20
        ):
            raise ValueError("invalid plan shape")
        return value["steps"]
    except (ValueError, TypeError, RecursionError) as exc:
        raise SchemaError({"reason": "planner.response"}) from exc


def propose_with_fallback(
    commands, text, *, project_id, dataset_id, version_id, provider=None, consent=False
):
    if provider is not None and consent is True:
        try:
            columns = commands.catalog.list_columns(version_id)
            steps = provider.generate(text, columns, consent=consent)
            return commands.proposal(
                project_id=project_id, dataset_id=dataset_id, version_id=version_id, steps=steps
            ), False
        except Exception:
            pass
    return commands.interpret(
        text, project_id=project_id, dataset_id=dataset_id, version_id=version_id
    ), True

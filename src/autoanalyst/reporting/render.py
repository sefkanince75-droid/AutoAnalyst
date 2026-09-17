"""Versioned offline report rendering from immutable AnalysisResult objects."""

from __future__ import annotations

import csv
from html import escape
import io
import json
from uuid import uuid4
import zipfile

from ..domain.codec import canonical_json, fingerprint, to_primitive, utc_now
from ..domain.errors import DataError, SchemaError
from ..domain.results import AnalysisResult, Artifact, Report
from ..storage.artifacts import ArtifactStore
from ..storage.runs import RunStore
from ..storage.sqlite import SQLiteCatalog


TEMPLATE_VERSION = "2.0"
SUPPORTED_FORMATS = {"html", "markdown", "json", "csv"}


class ReportService:
    def __init__(self, catalog: SQLiteCatalog, artifact_store: ArtifactStore) -> None:
        self.catalog = catalog
        self.artifact_store = artifact_store
        self.run_store = RunStore(catalog)

    def render(self, result_id: str, *, format: str, language: str = "en", options: dict[str, object] | None = None) -> Report:
        fmt = format.lower()
        if fmt not in SUPPORTED_FORMATS:
            raise SchemaError({"reason": "unsupported_report_format", "format": fmt})
        if language not in {"en", "tr"}:
            raise SchemaError({"reason": "unsupported_report_language", "language": language})
        result = self.run_store.get_result(result_id)
        options_hash = fingerprint(options or {})
        existing = self._find_existing(result_id, fmt, language, options_hash)
        if existing is not None:
            return existing
        content, media_type = _render_bytes(result, fmt, language)
        run = self.run_store.get_run(result.run_id)
        staged = self.artifact_store.stage_bytes(content, project_id=run.project_id, owner_run_id=result.run_id, kind=f"report_{fmt}", media_type=media_type, format_version=TEMPLATE_VERSION)
        artifact = self.artifact_store.finalize(staged)
        report = Report(str(uuid4()), result.result_id, fmt, language, TEMPLATE_VERSION, options_hash, artifact.artifact_id, utc_now())
        with self.catalog.transaction() as connection:
            connection.execute(
                """INSERT INTO artifacts
                   (artifact_id, project_id, owner_run_id, kind, relative_path, media_type,
                    byte_size, sha256, format_version, created_at, schema_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (artifact.artifact_id, artifact.project_id, artifact.owner_run_id, artifact.kind, artifact.relative_path, artifact.media_type, artifact.byte_size, artifact.sha256, artifact.format_version, artifact.created_at.isoformat().replace("+00:00", "Z"), artifact.schema_version),
            )
            connection.execute(
                """INSERT INTO reports
                   (report_id, root_result_id, format, language, template_version,
                    render_options_hash, artifact_id, created_at, schema_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (report.report_id, report.root_result_id, report.format, report.language, report.template_version, report.render_options_hash, report.artifact_id, report.created_at.isoformat().replace("+00:00", "Z"), report.schema_version),
            )
        return report

    def get(self, report_id: str) -> Report:
        with self.catalog.connection() as connection:
            row = connection.execute("SELECT * FROM reports WHERE report_id = ?", (report_id,)).fetchone()
        if row is None:
            raise DataError({"reason": "report_not_found", "report_id": report_id})
        return _report(row)

    def artifact_bytes(self, report: Report) -> bytes:
        artifact = self.catalog.get_artifact(report.artifact_id)
        if not self.artifact_store.verify(artifact):
            raise DataError({"reason": "artifact_verification_failed", "artifact_id": artifact.artifact_id})
        return self.artifact_store.resolve_relative_path(artifact.relative_path).read_bytes()

    def _find_existing(self, result_id: str, fmt: str, language: str, options_hash: str) -> Report | None:
        with self.catalog.connection() as connection:
            row = connection.execute(
                """SELECT * FROM reports WHERE root_result_id = ? AND format = ? AND language = ?
                   AND template_version = ? AND render_options_hash = ?""",
                (result_id, fmt, language, TEMPLATE_VERSION, options_hash),
            ).fetchone()
        return _report(row) if row is not None else None


def _render_bytes(result: AnalysisResult, fmt: str, language: str) -> tuple[bytes, str]:
    if fmt == "html":
        return _html(result, language).encode("utf-8"), "text/html; charset=utf-8"
    if fmt == "markdown":
        return _markdown(result, language).encode("utf-8"), "text/markdown; charset=utf-8"
    if fmt == "json":
        return canonical_json(result).encode("utf-8"), "application/json"
    return _csv_zip(result), "application/zip"


def _html(result: AnalysisResult, language: str) -> str:
    title = "AutoAnalyst Analysis Report" if language == "en" else "AutoAnalyst Analiz Raporu"
    metric_rows = "".join(f"<tr><td>{escape(m.name)}</td><td>{escape(_metric_text(m))}</td><td>{escape(str(dict(m.dimensions)))}</td></tr>" for m in result.metrics)
    findings = "".join(f"<li><strong>{escape(f.code)}</strong> — {escape(f.severity.value)} {escape(str(dict(f.parameters)))}</li>" for f in result.findings)
    tables = "".join(_html_table(table) for table in result.tables)
    return f"""<!doctype html><html><head><meta charset=\"utf-8\"><title>{escape(title)}</title><style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem}}table{{border-collapse:collapse;width:100%;margin:1rem 0}}th,td{{border:1px solid #bbb;padding:.4rem;text-align:left}}code{{white-space:pre-wrap}}@media print{{body{{margin:0}}}}</style></head><body><h1>{escape(title)}</h1><p><b>Module:</b> {escape(result.module_id.value)} · <b>Outcome:</b> {escape(result.outcome.value)}</p><h2>Metrics</h2><table><thead><tr><th>Name</th><th>Value</th><th>Dimensions</th></tr></thead><tbody>{metric_rows}</tbody></table><h2>Findings</h2><ul>{findings}</ul><h2>Tables</h2>{tables}<h2>Methodology</h2><code>{escape(json.dumps(to_primitive(result.methodology), ensure_ascii=False, indent=2))}</code><h2>Provenance</h2><code>{escape(json.dumps(to_primitive(result.provenance), ensure_ascii=False, indent=2))}</code></body></html>"""


def _html_table(table) -> str:
    head = "".join(f"<th>{escape(str(c))}</th>" for c in table.columns)
    body = "".join("<tr>" + "".join(f"<td>{escape(str(cell))}</td>" for cell in row) + "</tr>" for row in table.rows[:500])
    note = "<p><em>Showing first 500 rows.</em></p>" if len(table.rows) > 500 else ""
    return f"<h3>{escape(table.table_id)}</h3><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>{note}"


def _markdown(result: AnalysisResult, language: str) -> str:
    title = "AutoAnalyst Analysis Report" if language == "en" else "AutoAnalyst Analiz Raporu"
    lines = [f"# {title}", "", f"- Module: `{result.module_id.value}`", f"- Outcome: `{result.outcome.value}`", "", "## Metrics", "", "| Metric | Value | Dimensions |", "|---|---:|---|"]
    lines.extend(f"| {m.name} | {_metric_text(m)} | `{dict(m.dimensions)}` |" for m in result.metrics)
    lines += ["", "## Findings", ""]
    lines.extend(f"- **{f.code}** ({f.severity.value}): `{dict(f.parameters)}`" for f in result.findings)
    for table in result.tables:
        lines += ["", f"## Table `{table.table_id}`", "", "| " + " | ".join(table.columns) + " |", "| " + " | ".join("---" for _ in table.columns) + " |"]
        for row in table.rows[:100]:
            lines.append("| " + " | ".join(str(cell).replace("|", "\\|") for cell in row) + " |")
        if len(table.rows) > 100:
            lines.append("\n_Showing first 100 rows._")
    lines += ["", "## Methodology", "", "```json", json.dumps(to_primitive(result.methodology), ensure_ascii=False, indent=2), "```", "", "## Provenance", "", "```json", json.dumps(to_primitive(result.provenance), ensure_ascii=False, indent=2), "```"]
    return "\n".join(lines) + "\n"


def _csv_zip(result: AnalysisResult) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        metrics = io.StringIO(newline="")
        writer = csv.writer(metrics)
        writer.writerow(["metric_id", "name", "value_state", "value", "unit", "sample_size", "dimensions"])
        for metric in result.metrics:
            writer.writerow([metric.metric_id, _csv_safe(metric.name), metric.value_state.value, metric.value, metric.unit or "", metric.sample_size if metric.sample_size is not None else "", canonical_json(dict(metric.dimensions))])
        archive.writestr("metrics.csv", metrics.getvalue().encode("utf-8-sig"))
        for index, table in enumerate(result.tables, 1):
            data = io.StringIO(newline="")
            writer = csv.writer(data)
            writer.writerow(table.columns)
            for row in table.rows:
                writer.writerow([_csv_safe(cell) for cell in row])
            archive.writestr(f"table_{index}.csv", data.getvalue().encode("utf-8-sig"))
    return output.getvalue()


def _csv_safe(value):
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _metric_text(metric) -> str:
    if metric.value_state.value == "finite":
        return str(metric.value)
    return metric.value_state.value


def _report(row) -> Report:
    from datetime import datetime
    return Report(
        report_id=row["report_id"], root_result_id=row["root_result_id"], format=row["format"], language=row["language"],
        template_version=row["template_version"], render_options_hash=row["render_options_hash"], artifact_id=row["artifact_id"],
        created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")), schema_version=row["schema_version"],
    )

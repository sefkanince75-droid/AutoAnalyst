"""Versioned offline report rendering from immutable AnalysisResult objects."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from html import escape
from uuid import uuid4

from .. import __version__
from ..domain.codec import canonical_json, fingerprint, to_primitive, utc_now
from ..domain.errors import DataError, SchemaError
from ..domain.results import AnalysisResult, Report
from ..localization import I18N, cell_text, label, result_finding_text
from ..storage.activity import artifact_activity
from ..storage.artifacts import ArtifactStore
from ..storage.runs import RunStore
from ..storage.sqlite import SQLiteCatalog
from .charts import chart_views, summary_metrics, table_title
from .summary import result_summary

TEMPLATE_VERSION = f"{__version__}/presentation-6"
SUPPORTED_FORMATS = {"html", "markdown", "json", "csv", "pdf", "xlsx"}


class ReportService:
    def __init__(self, catalog: SQLiteCatalog, artifact_store: ArtifactStore) -> None:
        self.catalog = catalog
        self.artifact_store = artifact_store
        self.run_store = RunStore(catalog)

    @artifact_activity
    def render(
        self,
        result_id: str,
        *,
        format: str,
        language: str = "en",
        options: dict[str, object] | None = None,
    ) -> Report:
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
        staged = self.artifact_store.stage_bytes(
            content,
            project_id=run.project_id,
            owner_run_id=result.run_id,
            kind=f"report_{fmt}",
            media_type=media_type,
            format_version=TEMPLATE_VERSION,
        )
        artifact = self.artifact_store.finalize(staged)
        report = Report(
            str(uuid4()),
            result.result_id,
            fmt,
            language,
            TEMPLATE_VERSION,
            options_hash,
            artifact.artifact_id,
            utc_now(),
        )
        with self.catalog.transaction() as connection:
            connection.execute(
                """INSERT INTO artifacts
                   (artifact_id, project_id, owner_run_id, kind, relative_path, media_type,
                    byte_size, sha256, format_version, created_at, schema_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    artifact.artifact_id,
                    artifact.project_id,
                    artifact.owner_run_id,
                    artifact.kind,
                    artifact.relative_path,
                    artifact.media_type,
                    artifact.byte_size,
                    artifact.sha256,
                    artifact.format_version,
                    artifact.created_at.isoformat().replace("+00:00", "Z"),
                    artifact.schema_version,
                ),
            )
            connection.execute(
                """INSERT INTO reports
                   (report_id, root_result_id, format, language, template_version,
                    render_options_hash, artifact_id, created_at, schema_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    report.report_id,
                    report.root_result_id,
                    report.format,
                    report.language,
                    report.template_version,
                    report.render_options_hash,
                    report.artifact_id,
                    report.created_at.isoformat().replace("+00:00", "Z"),
                    report.schema_version,
                ),
            )
        return report

    def get(self, report_id: str) -> Report:
        with self.catalog.connection() as connection:
            row = connection.execute(
                "SELECT * FROM reports WHERE report_id = ?", (report_id,)
            ).fetchone()
        if row is None:
            raise DataError({"reason": "report_not_found", "report_id": report_id})
        return _report(row)

    def artifact_bytes(self, report: Report) -> bytes:
        artifact = self.catalog.get_artifact(report.artifact_id)
        if not self.artifact_store.verify(artifact):
            raise DataError(
                {"reason": "artifact_verification_failed", "artifact_id": artifact.artifact_id}
            )
        return self.artifact_store.resolve_relative_path(artifact.relative_path).read_bytes()

    def _find_existing(
        self,
        result_id: str,
        fmt: str,
        language: str,
        options_hash: str,
    ) -> Report | None:
        with self.catalog.connection() as connection:
            row = connection.execute(
                """SELECT * FROM reports WHERE root_result_id = ? AND format = ? AND language = ?
                   AND template_version = ? AND render_options_hash = ?""",
                (result_id, fmt, language, TEMPLATE_VERSION, options_hash),
            ).fetchone()
        return _report(row) if row is not None else None


def _render_bytes(result: AnalysisResult, fmt: str, language: str) -> tuple[bytes, str]:
    if fmt in {"pdf", "xlsx"}:
        from .binary_formats import render_binary

        return render_binary(result, fmt, language)
    if fmt == "html":
        return _html(result, language).encode("utf-8"), "text/html; charset=utf-8"
    if fmt == "markdown":
        return _markdown(result, language).encode("utf-8"), "text/markdown; charset=utf-8"
    if fmt == "json":
        payload = {
            "autoanalyst_version": __version__,
            "result": to_primitive(result),
            "summary": to_primitive(result_summary(result, language)),
        }
        return canonical_json(payload).encode("utf-8"), "application/json"
    return _csv_zip(result, language), "application/zip"


def _html(result: AnalysisResult, language: str) -> str:
    t = I18N[language]
    metric_rows = "".join(
        f"<tr><td>{escape(label(m.name, language))}</td><td>{escape(_metric_text(m, language))}</td>"
        f"<td>{escape(str({label(k, language): label(v, language) for k, v in m.dimensions.items()}))}</td></tr>"
        for m in result.metrics
    )
    findings = "".join(
        f"<li>{escape(result_finding_text(result, f, language))}</li>" for f in result.findings
    )
    tables = "".join(
        _html_table(table, language, index) for index, table in enumerate(result.tables, 1)
    )
    summary = "".join(
        f'<div class="card"><b>{escape(label(m.name, language))}</b><br>'
        f"<strong>{escape(_metric_text(m, language))}</strong></div>"
        for m in summary_metrics(result)
    )
    summary = (
        f'<h2>{escape(t["summary"])}</h2><div class="summary">{summary}</div>' if summary else ""
    )
    charts = _html_charts(result, language)
    overview = result_summary(result, language)
    executive = (
        f"<h2>{escape(t['report_overview'])}</h2><ul>"
        + "".join(f"<li>{escape(line)}</li>" for line in overview.lines)
        + f"</ul><p>{escape(overview.scope)}</p>"
    )
    details = "".join(
        f"<h2>{escape(t[key])}</h2><pre>{escape(json.dumps(to_primitive(value), ensure_ascii=False, indent=2))}</pre>"
        for key, value in (("methodology", result.methodology), ("provenance", result.provenance))
    )
    return f"""<!doctype html><html lang="{language}"><head><meta charset="utf-8"><title>{escape(t["report_title"])}</title><style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem}}table{{border-collapse:collapse;width:100%;margin:1rem 0}}th,td{{border:1px solid #bbb;padding:.4rem;text-align:left}}pre{{white-space:pre-wrap}}.summary{{display:flex;gap:1rem;flex-wrap:wrap}}.card{{border:1px solid #cbd5e1;border-radius:8px;padding:1rem;min-width:140px}}.card strong{{font-size:1.5rem}}figure{{margin:1rem 0;break-inside:avoid}}svg{{width:100%;height:auto}}figcaption{{font-size:.85rem}}@media print{{body{{margin:0}}}}</style></head><body><h1>{escape(t["report_title"])}</h1><p><b>AutoAnalyst:</b> {escape(__version__)} · <b>{escape(t["module"])}:</b> {escape(label(result.module_id.value, language))} · <b>{escape(t["outcome"])}:</b> {escape(label(result.outcome.value, language))}</p>{executive}{summary}{charts}<h2>{escape(t["metrics"])}</h2><table><thead><tr><th>{escape(t["metric"])}</th><th>{escape(t["value"])}</th><th>{escape(t["dimensions"])}</th></tr></thead><tbody>{metric_rows}</tbody></table><h2>{escape(t["findings"])}</h2><ul>{findings}</ul><h2>{escape(t["tables"])}</h2>{tables}{details}</body></html>"""


def _html_charts(result: AnalysisResult, language: str) -> str:
    t = I18N[language]
    if not result.charts:
        return ""
    sections = [f"<h2>{escape(t['charts'])}</h2>"]
    indexes = {table.table_id: index for index, table in enumerate(result.tables, 1)}
    for chart in chart_views(result, language):
        if chart.svg:
            source = t["chart_source"].format(index=indexes[chart.table_id])
            sections.append(
                f'<figure>{chart.svg}<figcaption><a href="#table-{chart.table_id}">'
                f"{escape(source)}</a></figcaption></figure>"
            )
        else:
            sections.append(
                f'<p role="status"><b>{escape(chart.title)}</b>: {escape(chart.notice or "")}</p>'
            )
    return "".join(sections)


def _html_table(table, language: str = "en", index: int = 1) -> str:
    t = I18N[language]
    head = "".join(f"<th>{escape(label(c, language))}</th>" for c in table.columns)
    body = "".join(
        "<tr>"
        + "".join(
            f"<td>{escape(str(cell_text(cell, column, language)))}</td>"
            for column, cell in zip(table.columns, row, strict=True)
        )
        + "</tr>"
        for row in table.rows[:500]
    )
    note = (
        "<p><em>" + t["first_rows"].format(limit=500) + "</em></p>" if len(table.rows) > 500 else ""
    )
    return f'<h3 id="table-{table.table_id}">{escape(table_title(table, index, language))}</h3><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>{note}'


def _md_cell(value) -> str:
    return escape(str(value), quote=False).replace("|", "\\|").replace("\n", "<br>")


def _markdown(result: AnalysisResult, language: str) -> str:
    t = I18N[language]
    overview = result_summary(result, language)
    lines = [
        f"# {t['report_title']}",
        "",
        f"- AutoAnalyst: `{__version__}`",
        f"- {t['module']}: {label(result.module_id.value, language)}",
        f"- {t['outcome']}: {label(result.outcome.value, language)}",
        "",
        f"## {t['report_overview']}",
        "",
        *("- " + _md_cell(line) for line in overview.lines),
        "",
        overview.scope,
        "",
        f"## {t['metrics']}",
        "",
        f"| {t['metric']} | {t['value']} | {t['dimensions']} |",
        "|---|---:|---|",
    ]
    for metric in result.metrics:
        dims = {label(k, language): label(v, language) for k, v in metric.dimensions.items()}
        lines.append(
            f"| {_md_cell(label(metric.name, language))} | {_md_cell(_metric_text(metric, language))} | {_md_cell(dims)} |"
        )
    lines += ["", f"## {t['findings']}", ""]
    lines.extend("- " + result_finding_text(result, f, language) for f in result.findings)
    for table in result.tables:
        lines += [
            "",
            f"## {t['table']} `{table.table_id}`",
            "",
            "| " + " | ".join(_md_cell(label(c, language)) for c in table.columns) + " |",
            "| " + " | ".join("---" for _ in table.columns) + " |",
        ]
        for row in table.rows[:100]:
            lines.append(
                "| "
                + " | ".join(
                    _md_cell(cell_text(value, column, language))
                    for column, value in zip(table.columns, row, strict=True)
                )
                + " |"
            )
        if len(table.rows) > 100:
            lines += ["", "_" + t["first_rows"].format(limit=100) + "_"]
    for key, value in (("methodology", result.methodology), ("provenance", result.provenance)):
        lines += [
            "",
            f"## {t[key]}",
            "",
            "```json",
            json.dumps(to_primitive(value), ensure_ascii=False, indent=2),
            "```",
        ]
    return "\n".join(lines) + "\n"


def _csv_zip(result: AnalysisResult, language: str = "en") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        overview = result_summary(result, language)
        archive.writestr(
            "summary.txt", ("\n".join((*overview.lines, "", overview.scope)) + "\n").encode("utf-8")
        )
        metadata = io.StringIO(newline="")
        writer = csv.writer(metadata)
        writer.writerow(["key", "value"])
        writer.writerow(["autoanalyst_version", __version__])
        writer.writerow(["result_id", result.result_id])
        writer.writerow(["module", result.module_id.value])
        writer.writerow(["outcome", result.outcome.value])
        archive.writestr("metadata.csv", metadata.getvalue().encode("utf-8-sig"))

        metrics = io.StringIO(newline="")
        writer = csv.writer(metrics)
        writer.writerow(
            ["metric_id", "name", "value_state", "value", "unit", "sample_size", "dimensions"]
        )
        for metric in result.metrics:
            writer.writerow(
                [
                    metric.metric_id,
                    _csv_safe(metric.name),
                    metric.value_state.value,
                    metric.value,
                    metric.unit or "",
                    metric.sample_size if metric.sample_size is not None else "",
                    canonical_json(dict(metric.dimensions)),
                ]
            )
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


def _metric_text(metric, language: str = "en") -> str:
    if metric.value_state.value == "finite":
        value = (
            format(metric.value, ".6g") if isinstance(metric.value, float) else str(metric.value)
        )
        if metric.interval is not None:
            value += f" · {I18N[language]['confidence_interval']}: [{metric.interval[0]}, {metric.interval[1]}]"
        return value
    return label(metric.value_state.value, language)


def _report(row) -> Report:
    from datetime import datetime

    return Report(
        report_id=row["report_id"],
        root_result_id=row["root_result_id"],
        format=row["format"],
        language=row["language"],
        template_version=row["template_version"],
        render_options_hash=row["render_options_hash"],
        artifact_id=row["artifact_id"],
        created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")),
        schema_version=row["schema_version"],
    )

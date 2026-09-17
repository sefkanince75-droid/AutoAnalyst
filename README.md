# AutoAnalyst V2.0 Release Candidate

AutoAnalyst is a **local-first, deterministic analytics workspace for tabular data**. V2 keeps raw data and computation on the local machine, stores projects and immutable dataset versions in a persistent workspace, and separates numerical results from presentation so the same result can be reopened and exported reproducibly.

> Status: `2.0.0rc1`. V2 is in release hardening. The published V1.1 implementation is preserved in `legacy_app.py` while the V2 release candidate is the default `app.py` entry point.

## V2 workflow

`Project -> Data -> Analysis -> Results -> History`

V2 supports four closed analysis areas:

- **Data profile** without requiring a target: dimensions, missingness, duplicates, numerical summaries, categorical frequencies and deterministic chart data.
- **Data preparation** through a closed recipe system with full-data preview, immutable apply, stale-preview protection, version history and lineage.
- **Comparison / descriptive analysis** including group summaries, distributions, crosstabs, Pearson/Spearman correlation, Welch two-group comparison and Fisher 2x2 exact test.
- **Binary classification** with an explicit positive class, leakage-safe train-only preprocessing, stratified/group/time split policies, Dummy baseline, Logistic Regression and Random Forest, validation-only threshold selection, persistent final-holdout locking and scoring on new compatible data.

CSV and explicitly selected XLSX worksheets can be imported. Canonical dataset versions are stored as Parquet while raw source artifacts are retained unchanged with SHA-256 checksums. Row and column identities survive supported preparation operations.

## Reproducibility and safety boundaries

AutoAnalyst V2 intentionally uses a closed operation set. It does **not** execute user Python, `eval`, arbitrary SQL, generated code or dynamic analysis plugins. DuckDB is used as an ephemeral local table engine over catalog-registered Parquet artifacts.

The modeling path keeps learned preprocessing inside the scikit-learn training pipeline. Dataset-wide learned preparation such as mean/median/mode filling is recorded in lineage and can be rejected for modeling when it would create leakage risk. Final-test access is persistent: once final evaluation starts, the selected training run/model/threshold cannot silently change.

Analysis runs and results are persisted in SQLite. Heavy analysis runs execute through the worker path with explicit progress/cancellation state; completed results can be reopened after restarting the application.

## Reports

A persisted `AnalysisResult` is the source of truth for presentation. Reports do not recompute analysis values. V2 can render:

- HTML (offline/self-contained)
- Markdown
- CSV
- JSON

Report rendering is deterministic for a result/template/options/language combination and protects spreadsheet exports from formula injection.

## Requirements

The V2 release baseline is **Python 3.12 x64**.

Recommended setup with `uv`:

```powershell
uv sync --frozen --group dev
uv run streamlit run app.py
```

The persistent workspace defaults to:

```text
%LOCALAPPDATA%/AutoAnalyst/workspace
```

It can be overridden for testing or controlled deployments with `AUTOANALYST_WORKSPACE`. User datasets and active SQLite state are not written into the repository by default.

## Development and verification

Run the full suite:

```powershell
uv run pytest -q
```

Build the wheel:

```powershell
uv run python -m build
```

The repository CI verifies the frozen dependency graph, compilation, tests and package build. Release hardening additionally covers persistence/reopen behavior, stale-plan protection, leakage invariants, final-holdout discipline, reporting, cancellation and reproducibility.

## V2 architecture

The main V2 package lives under `src/autoanalyst/`:

- `domain/` — immutable domain models, typed values, canonical serialization and fingerprints.
- `application/` — project, dataset and preparation orchestration.
- `storage/` — SQLite catalog, migrations, immutable artifacts and run/holdout persistence.
- `data/` — CSV/XLSX ingestion, schema identity and controlled DuckDB table access.
- `preparation/` — closed preparation operations and preview execution.
- `analyses/` — shared analysis contract, profiling, comparisons and binary classification.
- `execution/` — resource checks, run lifecycle, worker protocol and cancellation.
- `reporting/` — result-only HTML/Markdown/CSV/JSON rendering.
- `ui/` — thin Streamlit presentation layer.

`legacy_app.py` and the legacy `src/*.py` modules remain only to preserve the V1.1 reference path during migration; V2 code does not depend on the legacy UI lifecycle.

## V2.0 scope limits

V2.0 does not claim support for regression, multiclass classification, time-series forecasting, NLP/images, clustering, arbitrary joins, arbitrary SQL, a dashboard builder, cloud/multi-user collaboration, real-time model serving or large AutoML searches. Group+time combined binary splitting is intentionally blocked rather than silently falling back to a random split.

Statistical comparisons are descriptive/inferential aids, not causal evidence. Model metrics depend on data quality and representativeness; a model that does not improve on the Dummy baseline is a valid outcome and is not automatically recommended.

## Legacy V1.1

The previous localized seven-stage V1.1 application is preserved in `legacy_app.py` for migration/reference testing. It is not the V2 product path.

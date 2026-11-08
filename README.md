# AutoAnalyst V2.0 Release Candidate

AutoAnalyst is a **local-first, deterministic analytics workspace for tabular data**. V2 keeps raw data and computation on the local machine, stores projects and immutable dataset versions in a persistent workspace, and separates numerical results from presentation so the same result can be reopened and exported reproducibly.

> Status: `2.0.0rc1`. V2 is in release hardening. The published V1.1 implementation is preserved in `legacy_app.py` while the V2 release candidate is the default `app.py` entry point.

## V2 workflow

`Project -> Data -> Analysis -> Results -> History`

V2 supports six closed analysis areas:

- **Data profile** without requiring a target: dimensions, missingness, duplicates, numerical summaries, categorical frequencies and deterministic chart data.
- **Data preparation** through a closed recipe system with full-data preview, immutable apply, stale-preview protection, version history and lineage.
- **Comparison / descriptive analysis** including group summaries, distributions, crosstabs, Pearson/Spearman correlation, Welch two-group comparison and Fisher 2x2 exact test.
- **Binary classification** with an explicit positive class, leakage-safe train-only preprocessing, stratified/group/time split policies, Dummy baseline, Logistic Regression and Random Forest, validation-only threshold selection, persistent final-holdout locking and scoring on new compatible data.

CSV and explicitly selected XLSX worksheets can be imported. Canonical dataset versions are stored as Parquet while raw source artifacts are retained unchanged with SHA-256 checksums. Row and column identities survive supported preparation operations.

Before importing, choose the CSV delimiter/encoding or XLSX worksheet and inspect an unsaved sample of up to 100 rows. Changing the file or parsing options requires a fresh preview. The full file is validated when imported; sample-inferred types can differ from the final schema. UTF-8/BOM, Turkish Windows encoding, UTF-16, comma, semicolon, tab and pipe options are available.

The Data tab provides stable, numbered pages of 25–500 rows and a selectable projection of up to 50 columns. Browsing retrieves only the selected page into the display table and keeps the stored dataset unchanged. Page state is scoped to the immutable dataset version.

The interface and human-readable reports share a Turkish/English text catalog. Projects supply the initial language; an explicit language choice persists while switching projects. Preparation filters and constant fills use typed numeric, boolean and date/time inputs, including an exact integer option for values beyond JavaScript's safe integer range. Welch comparison requires an explicit independence confirmation before running.

Projects can be renamed, archived and restored from the sidebar. Archiving hides a project without deleting its data or results and is blocked while that project has active work. Projects with identical names are distinguished by their short IDs. The History tab lets you review a saved version's sample before making it current. Restoring moves the dataset head and records a new revision; existing versions, lineage, reports and model locks remain unchanged, and you can return to a newer saved version afterward.

Preparation exposes all ten closed operations, including exact typed replacements and appending compatible saved versions. Enable the multi-step recipe editor to add, edit, reorder or remove up to 20 steps, then preview and apply the listed sequence as one new version. Step effects and bounded before/after samples are shown before applying. Append retains source order and records lineage; its existing contract requires matching column identities, order and compatible types, so independently imported files with matching headers are not automatically combined.

The sidebar also offers an explicit storage scan and reversible quarantine. All catalog-registered files are protected. Only unregistered generated files unchanged for at least 24 hours can be selected; active imports, previews, reports and analysis jobs block maintenance. Files are checked again before moving and can be restored without overwriting existing files. Quarantine does not free disk space and performs no permanent deletion.

The Data tab exports the selected immutable version as CSV or Parquet. Export includes every row and user column in source order, independent of the browser page or visible columns, with display names and without internal row IDs. Files are written outside the workspace without overwriting existing files. CSV uses UTF-8 BOM and comma separation; CSV reimport infers types again. Parquet preserves source Arrow field types and nulls. Downloads up to 256 MiB are offered after checksum verification; larger files remain at the displayed disk path. Parquet is a table export; the current import UI accepts CSV/XLSX, and workspace backups preserve project history.

The Results tab and HTML, Markdown, JSON and CSV-ZIP reports share a deterministic overview built from the saved result. It names the result/run/input version, recorded sample counts, up to four overall finite metrics and three warnings/errors, with explicit scope notes. It does not rerun analysis or infer statistical significance. Existing reports retain their original bytes; the updated report template creates a new artifact.

Large data imports and preparation previews can run in a separate, cancellable process with persisted status and sampled elapsed time, RSS and net workspace file growth. CSV uploads of at least 4 MiB, XLSX uploads of at least 1 MiB and preparation inputs of at least 100,000 rows or 1,000,000 cells use this mode automatically; smaller operations can opt in. Worker policy: 1,536 MiB sampled RSS, 120 seconds, 1,024 MiB net file growth and 128 MiB free disk reserve. Sampling is periodic and permits short overshoots; these are enforcement policies rather than hardware-independent capacity guarantees. Inline small operations retain shape/disk checks, without a disposable-process watchdog.

Import/preparation limits are 1,000,000 rows, 500 user columns and 5,000,000 cells, in addition to source byte limits. XLSX expanded content is bounded before opening the workbook. Content fingerprints stream rows while preserving existing hashes, and preparation releases previous intermediate tables with a 512 MiB DuckDB pool and 256 MiB private spill limit. Committed results survive cancellation races; incomplete tasks preserve the previous dataset head. Measurements and the reproducible synthetic benchmark command are documented in `docs/DEVELOPMENT.md` and `docs/benchmarks/c04-local.json`.

## Reproducibility and safety boundaries

AutoAnalyst V2 intentionally uses a closed operation set. It does **not** execute user Python, `eval`, arbitrary SQL, generated code or dynamic analysis plugins. DuckDB is used as an ephemeral local table engine over catalog-registered Parquet artifacts.

The modeling path keeps learned preprocessing inside the scikit-learn training pipeline. Dataset-wide learned preparation such as mean/median/mode filling is recorded in lineage and can be rejected for modeling when it would create leakage risk. Final-test access is persistent: once final evaluation starts, the selected training run/model/threshold cannot silently change.

Analysis runs and results are persisted in SQLite. Heavy analysis runs execute through the worker path with explicit progress/cancellation state; completed results can be reopened after restarting the application.

While an analysis is active, its status panel refreshes every second and shows the owning project, elapsed time, stage progress and update age. A new browser session discovers active work from the workspace rather than relying on the session that launched it. Stage percentages are not overall completion or remaining-time estimates. The History tab exposes the latest 100 recorded lifecycle and observed progress events.

The sidebar's Workspace and backups panel exports the entire workspace and inspects backups before restoring them to a separate new or empty folder. Restore validates archive paths, checksums, the known catalog schema and stored records before publishing the folder. It requires explicit source trust because model files may execute code when later loaded; inspection never loads models. A restored workspace can be opened in the current browser session and you can return to the original workspace. File hashes detect corruption and do not authenticate the backup's author.

Browser upload/download is limited to 256 MiB. Larger exports remain on disk; the application service supports archives up to 2 GiB, at most 8 GiB unpacked and 100,000 entries. Backups are refused while the catalog contains pending or running work. Existing backup files and nonempty restore targets are preserved.

## Reports

A persisted `AnalysisResult` is the source of truth for presentation. Reports do not recompute analysis values. V2 can render:

- HTML (offline/self-contained)
- Markdown
- CSV tables in a ZIP archive
- JSON
- PDF with saved charts and paginated records
- XLSX with complete saved tables and embedded charts

Reports are cached by result/template/options/language; binary document container timestamps are not a byte-reproducibility guarantee. Rendering uses only persisted results and protects spreadsheet exports from formula injection.

HTML and Markdown localize headings, known metrics and findings without recomputing results. User data stays unchanged; JSON/CSV keys and technical methodology/provenance remain stable for downstream processing. Presentation template revisions create new report artifacts while preserving earlier reports.

The Results tab and offline HTML reports display saved profiling histograms through the same SVG renderer. Bin boundaries and counts come directly from the persisted result tables, even after the dataset changes. Summary cards show up to four recorded overall metrics; detailed metrics and source tables remain available. Unsupported or malformed saved charts show a localized explanation instead of interrupting the report.

## Extended modeling and assistance

Regression uses Dummy, Ridge and RandomForest baselines with MAE/RMSE/R². Multiclass classification supports 3–20 typed classes with accuracy, macro/weighted F1, per-class metrics and a validation confusion matrix. Both persist training-only preprocessing, validation selection and a locked final evaluation before scoring new mapped data.

Binary model audit provides 2–5 training-only CV folds that respect the original group/time policy, a model card and validation permutation importance. Calibration compares sigmoid CV and the saved uncalibrated model on validation data; it does not deploy or replace a model. Drift compares new mapped features against training data using explicit heuristic thresholds, without claiming performance or concept-drift detection.

Confidence intervals cover independent-row means, exact binomial proportions, Welch mean differences and optionally Pearson correlation when independence is acknowledged. Spearman intervals are not claimed. Each requested comparison is unadjusted for multiplicity and is not causal evidence.

The preparation panel supports bounded inner/left joins with explicit key types, cardinality and expansion limits. The Data tab accepts closed commands such as `filter value >= 2` and `rename "old name" "new name"`. Plan, Preview and Apply are separate actions; existing version history provides rollback.

An optional Ollama-compatible JSON planner is disabled by default. Each request needs consent to send the command and column names/types/IDs to the chosen endpoint; rows are never sent. Local schema validation, token/output/time bounds and a rule-based fallback remain in force. There is no arbitrary code execution, automatic Apply or paid-service account integration.

PDF shows up to 100 saved rows per table and 500 characters per cell, with an explicit truncation note and a 250-page budget. XLSX keeps complete saved tables within a 500,000-cell/50-chart report budget and Excel cell/shape limits; oversize values are rejected rather than silently truncated. Spreadsheet-like text remains literal, including strings beginning with `=`.

Offline help and four synthetic example projects are available in the sidebar. See `docs/KULLANICI_REHBERI.md` and `docs/PILOT_GOREVLERI.md`. Real participant pilot sessions and acceptance on a separate Python-free Windows machine remain external release gates.

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

See [V2 development and distribution guide](docs/DEVELOPMENT.md) for the official build path, installed-wheel verification, legacy boundaries and import diagnostics.

The installed package can be started with `uv run --frozen autoanalyst`. The official portable Windows build uses the same command locally and in CI:

```powershell
uv run --frozen --group desktop python tools/build_release.py
.\dist\AutoAnalyst.exe --self-test
```

The self-test imports a sample CSV in a temporary workspace, executes a real analysis worker, renders a report and verifies persistence after reopening. It does not use an existing user workspace.

Run the full suite:

```powershell
uv run --frozen playwright install chromium
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
- `analyses/` — shared analysis contract, profiling, comparisons, binary classification, regression and multiclass classification.
- `execution/` — resource checks, run lifecycle, worker protocol and cancellation.
- `reporting/` — result-only HTML/Markdown/CSV/JSON/PDF/XLSX rendering.
- `ui/` — thin Streamlit presentation layer.

`legacy_app.py` and the legacy `src/*.py` modules remain only to preserve the V1.1 reference path during migration; V2 code does not depend on the legacy UI lifecycle.

## V2.0 scope limits

V2.0 does not claim support for time-series forecasting, NLP/images, clustering, arbitrary joins beyond the bounded inner/left key join, arbitrary SQL, a dashboard builder, cloud/multi-user collaboration, real-time model serving or large AutoML searches. Group+time combined binary splitting is intentionally blocked rather than silently falling back to a random split.

Statistical comparisons are descriptive/inferential aids, not causal evidence. Model metrics depend on data quality and representativeness; a model that does not improve on the Dummy baseline is a valid outcome and is not automatically recommended.

## Legacy V1.1

The previous localized seven-stage V1.1 application is preserved in `legacy_app.py` for migration/reference testing. It is not the V2 product path.


## Windows UI startup validation

The V2 launcher explicitly loads production Streamlit configuration before starting the server. It binds to 127.0.0.1 and uses the same resolved port for the server and browser URL. Port 8501 is preferred; an occupied port falls back to a free loopback port. `AutoAnalyst.exe --port 0` selects a free port, and `--no-browser` starts only the local server.

On Windows, double-clicking opens a standalone WebView2 desktop window, without a Chrome tab or address bar. The WebView2 runtime is required. `--browser` explicitly selects the external browser for diagnostics; `--native-check --port 0` verifies the actual rendered native window and exits. Closing the window stops its own UI server; analysis workers keep their independent lifecycle.

Known historical workspaces with migrations 6=artifact quarantine and 7=holdout source locks are upgraded transactionally to the canonical 6=join, 7=quarantine, 8=source locks layout. Exact trusted checksums and schema objects must match; unknown histories remain rejected. Existing data and security records are preserved. Back up existing workspaces before upgrading.

`--self-test` verifies backend workflows; it is not proof that a browser can load the UI. The development-only `tools/check_startup.py --executable dist/AutoAnalyst.exe` additionally verifies real HTTP HTML/JS/CSS, the UI websocket and a rendered synthetic example project using an isolated temporary workspace. CI runs both checks. Playwright is a development dependency and is not required by users of the Windows executable.

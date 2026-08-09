# AutoAnalyst

AutoAnalyst is a local-first Streamlit application for explainable, deterministic tabular binary-classification workflows. Python performs all numerical and statistical work; a future optional AI layer may explain results and orchestrate workflows, but will never calculate metrics or transform data.

## Complete V1 workflow

- Upload and validate CSV datasets locally.
- Inspect dimensions, schema, sample rows, missing values, and duplicates.
- Select and validate a binary target, including minimum class-size checks.
- Restrict target choices to columns with exactly two distinct non-null values and automatically select a sole candidate.
- Show class counts and percentages.
- Diagnose feature types, class imbalance, missingness, constants, high-cardinality categoricals, robust numerical outliers, and likely scaling needs.
- Create deterministic stratified 70% train, 15% validation, and 15% final-test partitions.
- Fit numerical imputation/scaling and categorical imputation/one-hot encoding on training data only.
- Train imbalance-aware Logistic Regression and Random Forest baseline pipelines.
- Compare validation ROC-AUC, PR-AUC, precision, recall, F1, confusion matrices, and diagnostic curves at threshold 0.5.
- Optimize validation decision thresholds for a user-defined minimum Recall and recommend the feasible model with highest Precision.
- Switch the complete interface between English and Turkish using a centralized translation catalog.
- Evaluate the validation-locked model and threshold once on the untouched final test set.
- Export a localized Markdown report, combined metrics CSV, and reusable joblib model package without raw data.
- Guide users through a progressively disclosed, localized seven-stage workflow with safe new-analysis reset.

The V1 workflow is:

`CSV upload -> diagnostics -> stratified split -> leakage-safe preprocessing -> baseline models -> validation comparison -> threshold optimization -> model recommendation -> untouched final test -> exportable report`

Outliers are flagged for human review and are never automatically removed or modified. The final-test result cannot revise the validation-selected model or threshold.

## Installation and use

Python 3.11 or newer is recommended.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py
```

Run tests with:

```powershell
python -m pytest
```

## Architecture

- `app.py`: presentation and user interaction only.
- `src/data_loader.py`: defensive CSV ingestion.
- `src/diagnostics.py`: deterministic target and dataset diagnostics.
- `src/splitting.py`: stratified 70/15/15 partitioning.
- `src/preprocessing.py`: training-only scikit-learn preprocessing.
- `src/models.py`: reproducible, imbalance-aware baseline pipelines.
- `src/evaluation.py`: validation-only metrics and curve generation.
- `src/thresholding.py`: efficient validation-only threshold optimization and recommendation.
- `src/i18n.py`: centralized English and Turkish interface strings.
- `src/final_evaluation.py`: locked, one-shot final-test metrics.
- `src/reporting.py`: localized Markdown, CSV metric assembly, and safe model-package serialization.
- `tests/`: focused unit tests for critical logic.

Each fitted model owns a scikit-learn preprocessing pipeline trained only on the training partition. Baseline comparison and threshold selection use validation data. The selected model and threshold are then locked before the final test partition is evaluated once.

## Methodology and model selection

V1 compares imbalance-aware Logistic Regression and Random Forest pipelines. Selection maximizes validation Precision while satisfying the user's minimum Recall constraint, with PR-AUC used as supporting evidence and a tie-breaker. ROC-AUC, false positives, and false negatives provide additional context. Accuracy does not drive selection when severe imbalance makes it misleading.

## V1 limitations

CSV input, tabular data, binary classification, and manual target selection only. Cross-validation and hyperparameter tuning are not implemented. Feature relationships are not causal evidence, and measured performance depends on data quality, representativeness, and sample size. Regression, multiclass classification, time series, NLP, images, clustering, and Excel are out of scope.

## Roadmap

Multiclass classification, regression, cross-validation, broader hyperparameter optimization, SHAP explanations, LLM-generated narrative reports, Excel support, and time-series workflows.

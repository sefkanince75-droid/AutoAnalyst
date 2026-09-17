from __future__ import annotations

import ast
from pathlib import Path


PACKAGE = Path(__file__).parents[2] / "src" / "autoanalyst"


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.append(node.module)
    return tuple(modules)


def _python_files(root: Path) -> tuple[Path, ...]:
    return tuple(sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts))


def test_domain_is_standard_library_only() -> None:
    forbidden = {
        "pandas",
        "numpy",
        "streamlit",
        "sklearn",
        "scipy",
        "duckdb",
        "pyarrow",
        "joblib",
        "psutil",
        "sqlite3",
    }
    violations: list[str] = []
    for path in _python_files(PACKAGE / "domain"):
        for module in _imports(path):
            if module.split(".", 1)[0] in forbidden:
                violations.append(f"{path.relative_to(PACKAGE)} -> {module}")
    assert not violations, "Domain dependency violations:\n" + "\n".join(violations)


def test_backend_layers_do_not_depend_on_streamlit_or_ui() -> None:
    roots = (
        PACKAGE / "domain",
        PACKAGE / "application",
        PACKAGE / "storage",
        PACKAGE / "data",
        PACKAGE / "preparation",
        PACKAGE / "analyses",
        PACKAGE / "execution",
        PACKAGE / "reporting",
    )
    violations: list[str] = []
    for root in roots:
        for path in _python_files(root):
            for module in _imports(path):
                if module == "streamlit" or module.startswith("streamlit.") or module.startswith("autoanalyst.ui"):
                    violations.append(f"{path.relative_to(PACKAGE)} -> {module}")
    assert not violations, "Backend/UI dependency violations:\n" + "\n".join(violations)


def test_reporting_does_not_import_analysis_implementations() -> None:
    violations: list[str] = []
    for path in _python_files(PACKAGE / "reporting"):
        for module in _imports(path):
            if module.startswith("autoanalyst.analyses") or module.startswith("..analyses"):
                violations.append(f"{path.relative_to(PACKAGE)} -> {module}")
    assert not violations, "Reporting must render persisted results only:\n" + "\n".join(violations)


def test_v2_package_does_not_import_legacy_root_modules() -> None:
    legacy = {
        "training",
        "reporting",
        "data_cleaning",
        "data_inspection",
        "dataset_workspace",
        "workspace_commands",
        "workspace_command_executor",
        "workspace_command_parser",
        "i18n",
        "locales_v11",
    }
    violations: list[str] = []
    for path in _python_files(PACKAGE):
        for module in _imports(path):
            if module.split(".", 1)[0] in legacy:
                violations.append(f"{path.relative_to(PACKAGE)} -> {module}")
    assert not violations, "V2/legacy dependency violations:\n" + "\n".join(violations)

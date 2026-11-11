"""AutoAnalyst V2 Streamlit entry point."""

from __future__ import annotations

from pathlib import Path
import sys


SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autoanalyst.ui.app import main


if __name__ == "__main__":
    main()

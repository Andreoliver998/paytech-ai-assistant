"""
Smoke check for local imports.

Run:
  python scripts/smoke_imports.py
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from backend.app import app  # noqa: F401
    from backend.main import app as app2  # noqa: F401
    from backend.services import downloads_service, llm_planner, memory_store, rag_service, tool_runner  # noqa: F401

    assert app2 is not None
    print("OK: imports")


if __name__ == "__main__":
    main()

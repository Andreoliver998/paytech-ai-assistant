"""
Compatibility entrypoint.

Some setups run the backend with:
  uvicorn backend.main:app --reload

Some setups run from inside `backend/` with:
  uvicorn main:app --reload

The actual FastAPI instance lives in `app.py`.
"""

if __package__ in (None, ""):
    # Running as a top-level module (e.g. `uvicorn main:app` inside backend/).
    # Ensure repo root is importable so `backend.*` works.
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from backend.app import app  # noqa: F401
else:
    from .app import app  # noqa: F401

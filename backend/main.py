"""
Compatibility entrypoint.

Some setups run the backend with:
  uvicorn main:app --reload

The actual FastAPI instance lives in `app.py`.
"""

from app import app  # noqa: F401


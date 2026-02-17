from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from ..settings import settings

BASE_DIR = Path(__file__).resolve().parents[1]  # backend/
DATA_DIR = Path(settings.PAYTECH_DATA_DIR) if settings.PAYTECH_DATA_DIR else (BASE_DIR / "data")
UPLOADS_DIR = DATA_DIR / "uploads"
KB_FILE = DATA_DIR / "kb_store.json"
STORAGE_DIR = BASE_DIR / "storage"
DOWNLOADS_DIR = STORAGE_DIR / "downloads"

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)


def load_kb() -> Dict[str, Any]:
    if not KB_FILE.exists():
        return {"chunks": []}
    try:
        return json.loads(KB_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"chunks": []}


def save_kb(kb: Dict[str, Any]) -> None:
    KB_FILE.write_text(json.dumps(kb, ensure_ascii=False, indent=2), encoding="utf-8")

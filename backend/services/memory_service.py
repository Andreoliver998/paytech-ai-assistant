from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict

from sqlalchemy.orm import Session

from models import UserMemoryDB


def _safe_json_loads(s: str) -> Dict[str, Any]:
    try:
        v = json.loads(s or "{}")
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def get_preferences(db: Session, user_id: str) -> Dict[str, Any]:
    uid = (user_id or "").strip()
    if not uid:
        return {}
    row = db.get(UserMemoryDB, uid)
    if not row:
        return {}
    return _safe_json_loads(row.preferences_json or "{}")


def upsert_preferences(db: Session, user_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    uid = (user_id or "").strip()
    if not uid:
        return {}

    patch = patch if isinstance(patch, dict) else {}

    row = db.get(UserMemoryDB, uid)
    now = datetime.now()
    if not row:
        prefs: Dict[str, Any] = {}
        for k, v in patch.items():
            if v is not None:
                prefs[str(k)] = v
        row = UserMemoryDB(
            user_id=uid,
            preferences_json=json.dumps(prefs, ensure_ascii=False),
            createdAt=now,
            updatedAt=now,
        )
        db.add(row)
        db.commit()
        return prefs

    prefs = _safe_json_loads(row.preferences_json or "{}")
    for k, v in patch.items():
        if v is None:
            continue
        prefs[str(k)] = v
    row.preferences_json = json.dumps(prefs, ensure_ascii=False)
    row.updatedAt = now
    db.commit()
    return prefs


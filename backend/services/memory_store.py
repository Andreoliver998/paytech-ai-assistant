from __future__ import annotations

import concurrent.futures
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from models import ProductPrefDB, ThreadMetaDB, UserPrefDB
from services.openai_service import cosine_similarity, embed_texts
from settings import settings


EMBED_TIMEOUT_S = 1.2


def _safe_json_loads(s: str, default: Any):
    try:
        return json.loads(s)
    except Exception:
        return default


def _normalize_key(key: str) -> str:
    k = (key or "").strip().lower()
    k = re.sub(r"[^a-z0-9_.-]+", "_", k)
    return k[:80] or "unknown"


def _pref_text(key: str, value: str) -> str:
    return f"{key}: {value}".strip()


def _try_embed(texts: List[str]) -> List[List[float]]:
    """
    Embeddings são úteis para recall, mas nunca devem bloquear o streaming.
    Usa timeout curto; em caso de falha/timeout, retorna vazio e o sistema
    faz fallback por keyword.
    """
    if not texts:
        return []
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(embed_texts, texts, settings.OPENAI_EMBED_MODEL)
            return fut.result(timeout=EMBED_TIMEOUT_S) or []
    except Exception:
        return []


def upsert_user_pref(db: Session, user_id: str, key: str, value: str) -> None:
    uid = (user_id or "").strip()
    if not uid:
        return
    k = _normalize_key(key)
    v = (value or "").strip()

    row = (
        db.query(UserPrefDB)
        .filter(UserPrefDB.user_id == uid, UserPrefDB.key == k)
        .order_by(UserPrefDB.updatedAt.desc())
        .first()
    )

    emb = _try_embed([_pref_text(k, v)])
    emb_json = json.dumps(emb[0] if emb else [])

    now = datetime.now()
    if not row:
        db.add(UserPrefDB(user_id=uid, key=k, value=v, embedding_json=emb_json, updatedAt=now))
    else:
        row.value = v
        row.embedding_json = emb_json
        row.updatedAt = now
    db.commit()


def get_user_prefs(db: Session, user_id: str) -> Dict[str, str]:
    uid = (user_id or "").strip()
    if not uid:
        return {}
    rows = db.query(UserPrefDB).filter(UserPrefDB.user_id == uid).all()
    out: Dict[str, str] = {}
    for r in rows:
        out[str(r.key)] = str(r.value or "")
    return out


def recall_user_prefs(db: Session, user_id: str, query: str, top_k: int = 4) -> List[Tuple[str, str, float]]:
    """
    Retorna prefs relevantes para injeção seletiva.
    - Se embeddings indisponíveis, faz fallback por keyword simples.
    """
    uid = (user_id or "").strip()
    q = (query or "").strip()
    if not uid or not q:
        return []

    rows = db.query(UserPrefDB).filter(UserPrefDB.user_id == uid).all()
    if not rows:
        return []

    q_embs = _try_embed([q])
    q_emb = q_embs[0] if q_embs else None

    scored: List[Tuple[str, str, float]] = []
    if q_emb is not None:
        for r in rows:
            emb = _safe_json_loads(r.embedding_json or "[]", [])
            score = cosine_similarity(q_emb, emb)
            scored.append((str(r.key), str(r.value or ""), float(score)))
    else:
        q_low = q.lower()
        for r in rows:
            txt = _pref_text(str(r.key), str(r.value or "")).lower()
            score = 1.0 if any(t in txt for t in re.findall(r"[a-z0-9À-ÿ]{3,}", q_low)) else 0.0
            if score > 0:
                scored.append((str(r.key), str(r.value or ""), float(score)))

    scored.sort(key=lambda x: x[2], reverse=True)
    return scored[: max(0, int(top_k or 4))]


def upsert_thread_meta(db: Session, thread_id: str, title: str) -> None:
    tid = (thread_id or "").strip()
    if not tid:
        return
    t = (title or "").strip() or "Conversa"
    now = datetime.now()
    row = db.get(ThreadMetaDB, tid)
    if not row:
        db.add(ThreadMetaDB(thread_id=tid, title=t, createdAt=now, updatedAt=now))
    else:
        row.title = t
        row.updatedAt = now
    db.commit()


def get_thread_title(db: Session, thread_id: str) -> Optional[str]:
    tid = (thread_id or "").strip()
    if not tid:
        return None
    row = db.get(ThreadMetaDB, tid)
    return (row.title or "").strip() or None if row else None


def set_product_pref(db: Session, key: str, value: str) -> None:
    k = _normalize_key(key)
    v = (value or "").strip()
    now = datetime.now()
    row = db.get(ProductPrefDB, k)
    if not row:
        db.add(ProductPrefDB(key=k, value=v, updatedAt=now))
    else:
        row.value = v
        row.updatedAt = now
    db.commit()


def get_product_prefs(db: Session) -> Dict[str, str]:
    rows = db.query(ProductPrefDB).all()
    return {str(r.key): str(r.value or "") for r in rows}

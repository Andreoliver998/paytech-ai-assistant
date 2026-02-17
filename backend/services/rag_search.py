from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..models import DownloadChunkDB, DownloadChunkMetaDB
from .downloads_service import search_downloads


def _parse_meta_from_text(text: str) -> Dict[str, Any]:
    """
    Best-effort extraction from stored text markers:
    - PDF: [Página N]
    - XLSX: [Aba: Nome]
    """
    s = (text or "")
    meta: Dict[str, Any] = {}

    m = re.search(r"\[Página\s+(\d+)\]", s, flags=re.IGNORECASE)
    if m:
        try:
            meta["page"] = int(m.group(1))
        except Exception:
            pass

    m2 = re.search(r"\[Aba:\s*([^\]]+)\]", s, flags=re.IGNORECASE)
    if m2:
        meta["sheet"] = (m2.group(1) or "").strip()

    return meta


def _load_chunk_meta(db: Session, chunk_id: int) -> Dict[str, Any]:
    row = db.get(DownloadChunkMetaDB, int(chunk_id))
    if not row:
        return {}
    try:
        v = json.loads(row.meta_json or "{}")
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def rag_search_downloads(db: Session, query: str, top_k: int = 6) -> List[Dict[str, Any]]:
    """
    Retorna itens auditáveis (docId + snippet + metadados).
    Usa search_downloads (embeddings/keyword) e enriquece com metadados persistidos.
    """
    items = search_downloads(db, query, top_k)
    out: List[Dict[str, Any]] = []

    # search_downloads returns "text" which is the chunk text stored.
    for it in items:
        doc_id = it.get("doc_id") or it.get("id")
        filename = it.get("filename")
        snippet = it.get("snippet") or ""
        text = it.get("text") or ""

        # Map back to a chunk row to find meta, best-effort by (file_id,text)
        chunk_id: Optional[int] = None
        try:
            row = (
                db.query(DownloadChunkDB)
                .filter(DownloadChunkDB.file_id == doc_id, DownloadChunkDB.text == text)
                .order_by(DownloadChunkDB.id.desc())
                .first()
            )
            if row:
                chunk_id = int(row.id)
        except Exception:
            chunk_id = None

        meta = _load_chunk_meta(db, chunk_id) if chunk_id else {}
        if not meta:
            meta = _parse_meta_from_text(text)

        out.append(
            {
                "docId": doc_id,
                "filename": filename,
                "snippet": snippet,
                "page": meta.get("page"),
                "sheet": meta.get("sheet"),
                "rowRange": meta.get("rowRange"),
                # extra evidence fields (safe to ignore in UI)
                "chunkId": chunk_id,
                "score": it.get("score"),
            }
        )
    return out

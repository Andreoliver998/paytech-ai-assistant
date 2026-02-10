from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from sqlalchemy.orm import Session

from models import DownloadChunkDB, DownloadChunkMetaDB, DownloadFileDB
from services.openai_service import cosine_similarity, embed_texts
from services.rag_service import extract_csv_text, extract_pdf_text, extract_xlsx_text, split_text_tokens
from settings import settings


ALLOWED_EXTS = {"pdf", "xlsx", "csv", "txt"}


def safe_filename(name: str) -> str:
    n = (name or "arquivo").strip()
    n = re.sub(r"[^\w.\- ()]+", "_", n, flags=re.UNICODE)
    n = re.sub(r"\s+", " ", n).strip()
    return n or "arquivo"


def extract_text_by_ext(path: Path, ext: str) -> str:
    e = (ext or "").lower().strip(".")
    if e == "pdf":
        return extract_pdf_text(path)
    if e == "csv":
        return extract_csv_text(path)
    if e == "xlsx":
        return extract_xlsx_text(path)
    if e == "txt":
        return path.read_text(encoding="utf-8", errors="replace")
    raise ValueError("Formato não suportado.")


def _try_embed_texts(texts: List[str]) -> List[List[float]]:
    """
    Embeddings são o caminho ideal. Se OpenAI não estiver configurado/der erro,
    seguimos com indexação/busca por palavra-chave.
    """
    try:
        return embed_texts(texts, model=settings.OPENAI_EMBED_MODEL)
    except Exception:
        return []


def _tokenize_query(query: str) -> List[str]:
    q = (query or "").lower()
    terms = re.findall(r"[\wÀ-ÿ]{2,}", q, flags=re.UNICODE)
    seen = set()
    out: List[str] = []
    for t in terms:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _keyword_score(text: str, filename: str, terms: List[str]) -> float:
    if not terms:
        return 0.0
    t = (text or "").lower()
    fn = (filename or "").lower()
    score = 0.0
    for term in terms:
        if not term:
            continue
        score += min(10.0, float(t.count(term))) * 1.0
        if term in fn:
            score += 2.0
    denom = max(200.0, float(len(t)))
    return float(score * (800.0 / denom))


def _make_snippet(text: str, terms: List[str], max_len: int = 360) -> str:
    s = (text or "").replace("\r\n", "\n").strip()
    if not s:
        return ""
    if not terms:
        return s[:max_len]
    low = s.lower()
    idx = -1
    hit = ""
    for term in terms:
        i = low.find(term)
        if i != -1 and (idx == -1 or i < idx):
            idx = i
            hit = term
    if idx == -1:
        return s[:max_len]
    half = max(80, int(max_len / 2))
    start = max(0, idx - half)
    end = min(len(s), idx + len(hit) + half)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(s) else ""
    return f"{prefix}{s[start:end].strip()}{suffix}"[: max_len + 2]


def index_download_file(
    *,
    db: Session,
    file_id: str,
    filename: str,
    ext: str,
    stored_path: Path,
    full_text: str,
) -> int:
    chunks = split_text_tokens(
        full_text,
        settings.DOWNLOADS_CHUNK_TOKENS,
        settings.DOWNLOADS_CHUNK_OVERLAP_TOKENS,
    )
    if not chunks:
        return 0

    embeddings = _try_embed_texts(chunks)

    f = db.get(DownloadFileDB, file_id)
    if not f:
        f = DownloadFileDB(
            id=file_id,
            filename=filename,
            ext=ext,
            stored_path=str(stored_path),
            size=stored_path.stat().st_size if stored_path.exists() else 0,
        )
        db.add(f)
        db.commit()

    added = 0
    if embeddings and len(embeddings) == len(chunks):
        pairs = zip(chunks, embeddings)
    else:
        pairs = ((t, []) for t in chunks)

    for text, emb in pairs:
        row = DownloadChunkDB(
            file_id=file_id,
            filename=filename,
            ext=ext,
            text=text,
            embedding_json=json.dumps(emb),
        )
        db.add(row)
        db.flush()  # get row.id without committing

        # Persist audit meta (best-effort) extracted from text markers.
        meta: Dict[str, Any] = {}
        try:
            m = re.search(r"\\[Página\\s+(\\d+)\\]", text or "", flags=re.IGNORECASE)
            if m:
                meta["page"] = int(m.group(1))
        except Exception:
            pass
        try:
            m2 = re.search(r"\\[Aba:\\s*([^\\]]+)\\]", text or "", flags=re.IGNORECASE)
            if m2:
                meta["sheet"] = (m2.group(1) or "").strip()
        except Exception:
            pass

        if meta:
            db.add(DownloadChunkMetaDB(chunk_id=int(row.id), meta_json=json.dumps(meta, ensure_ascii=False)))
        added += 1
    db.commit()
    return added


def list_downloads(db: Session) -> List[Dict[str, Any]]:
    rows = db.query(DownloadFileDB).order_by(DownloadFileDB.createdAt.desc()).all()
    out: List[Dict[str, Any]] = []
    for r in rows:
        mime = {
            "pdf": "application/pdf",
            "csv": "text/csv",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "txt": "text/plain",
        }.get((r.ext or "").lower(), "application/octet-stream")
        out.append(
            {
                "id": r.id,
                "filename": r.filename,
                "mime": mime,
                "ext": r.ext,
                "size": r.size,
                "created_at": r.createdAt.isoformat(timespec="seconds") if r.createdAt else None,
                "createdAt": r.createdAt.isoformat(timespec="seconds") if r.createdAt else None,  # compat frontend antigo
            }
        )
    return out


def delete_download(db: Session, file_id: str) -> DownloadFileDB | None:
    f = db.get(DownloadFileDB, file_id)
    if not f:
        return None
    db.query(DownloadChunkDB).filter(DownloadChunkDB.file_id == file_id).delete()
    db.delete(f)
    db.commit()
    return f


def search_downloads(db: Session, query: str, top_k: int) -> List[Dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return []

    terms = _tokenize_query(q)
    q_embs = _try_embed_texts([q])
    q_emb = q_embs[0] if q_embs else None

    rows = db.query(DownloadChunkDB).all()
    scored: List[Tuple[float, DownloadChunkDB]] = []
    for r in rows:
        kw = _keyword_score(r.text or "", r.filename or "", terms)
        if q_emb is not None:
            try:
                emb = json.loads(r.embedding_json or "[]")
            except Exception:
                emb = []
            emb_score = cosine_similarity(q_emb, emb)
            score = (0.65 * float(emb_score)) + (0.35 * float(kw))
        else:
            score = float(kw)
        if q_emb is None and score <= 0:
            continue
        scored.append((float(score), r))

    scored.sort(key=lambda x: x[0], reverse=True)
    out: List[Dict[str, Any]] = []
    for score, r in scored[: max(0, int(top_k or 6))]:
        out.append(
            {
                "score": float(score),
                "doc_id": r.file_id,
                "id": r.file_id,  # compat (frontend/handlers antigos)
                "filename": r.filename,
                "snippet": _make_snippet(r.text or "", terms, max_len=360),
                "text": r.text,  # usado internamente no RAG do chat
                "meta": {"ext": r.ext},
            }
        )
    return out

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from pypdf import PdfReader
from sqlalchemy.orm import Session

from ..models import FileDB, KBChunkDB
from .openai_service import embed_texts, cosine_similarity
from ..settings import settings
from ..utils.files import load_kb, save_kb

FULLTEXTS_DIR = (Path(settings.PAYTECH_DATA_DIR) if settings.PAYTECH_DATA_DIR else (Path(__file__).resolve().parents[1] / "data")) / "fulltexts"
FULLTEXTS_DIR.mkdir(parents=True, exist_ok=True)


def _fulltext_path(tenant_id: str, file_id: str) -> Path:
    tid = (tenant_id or "unknown").strip() or "unknown"
    fid = (file_id or "unknown").strip() or "unknown"
    p = FULLTEXTS_DIR / tid
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{fid}.txt"


def save_full_text(*, tenant_id: str, file_id: str, text: str) -> str:
    p = _fulltext_path(tenant_id, file_id)
    p.write_text(text or "", encoding="utf-8", errors="replace")
    return str(p)


def split_text(text: str, chunk_size: int, overlap: int) -> List[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    chunks: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        chunks.append(text[start:end].strip())
        if end == n:
            break
        start = max(0, end - overlap)
    return [c for c in chunks if c]


def split_text_tokens(text: str, chunk_tokens: int, overlap_tokens: int) -> List[str]:
    """
    Chunker baseado em tokens (preferível para RAG).
    - Usa tiktoken se disponível; caso contrário faz fallback por caractere.
    """
    s = (text or "").strip()
    if not s:
        return []

    try:
        import tiktoken  # type: ignore

        enc = tiktoken.get_encoding("cl100k_base")
        toks = enc.encode(s)
        if not toks:
            return []

        chunks: List[str] = []
        start = 0
        n = len(toks)
        ct = max(50, int(chunk_tokens or 700))
        ov = max(0, int(overlap_tokens or 0))

        while start < n:
            end = min(start + ct, n)
            chunk = enc.decode(toks[start:end]).strip()
            if chunk:
                chunks.append(chunk)
            if end == n:
                break
            start = max(0, end - ov)
        return chunks
    except Exception:
        # fallback: aproxima 4 chars ~ 1 token
        ct_chars = max(300, int(chunk_tokens or 700) * 4)
        ov_chars = max(0, int(overlap_tokens or 0) * 4)
        return split_text(s, ct_chars, ov_chars)


def extract_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    parts: List[str] = []
    for i, page in enumerate(reader.pages):
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        if t.strip():
            parts.append(f"[Página {i+1}]\n{t}")
    return "\n\n".join(parts).strip()


def dataframe_to_text(df: pd.DataFrame, source_name: str) -> str:
    lines: List[str] = []
    lines.append(f"FONTE: {source_name}")
    lines.append(f"LINHAS: {len(df)} | COLUNAS: {len(df.columns)}")
    lines.append("COLUNAS: " + ", ".join([str(c) for c in df.columns]))
    lines.append("")
    lines.append("AMOSTRA (primeiras 20 linhas):")
    try:
        lines.append(df.head(20).to_string(index=False))
    except Exception:
        lines.append("(falha ao renderizar amostra)")
    lines.append("")

    # Deterministic-friendly row-wise dump (bounded).
    # This enables exact queries (counts/list/extractions) without relying only on chunk samples.
    max_rows = 2000
    try:
        total = int(df.shape[0])
    except Exception:
        total = len(df)
    if total > 0:
        lines.append(f"DADOS (CSV linha-a-linha, até {max_rows} linhas):")
        try:
            # Prefer CSV format for stability across Pandas versions.
            csv_text = df.head(max_rows).to_csv(index=False)
            lines.append(csv_text.strip())
            if total > max_rows:
                lines.append(f"(truncado: {total} linhas no total)")
        except Exception:
            lines.append("(falha ao serializar CSV completo)")
        lines.append("")

    lines.append("DESCRIBE (numérico/geral):")
    try:
        lines.append(df.describe(include="all").to_string())
    except Exception:
        lines.append("(sem describe)")
    return "\n".join(lines)


def extract_csv_text(path: Path) -> str:
    df = pd.read_csv(path)
    return dataframe_to_text(df, source_name=path.name)


def extract_xlsx_text(path: Path) -> str:
    xls = pd.ExcelFile(path)
    parts: List[str] = []
    for sheet in xls.sheet_names:
        df = xls.parse(sheet)
        parts.append(f"[Aba: {sheet}]\n{dataframe_to_text(df, source_name=f'{path.name}::{sheet}')}")
    return "\n\n".join(parts).strip()


def index_file_and_save(
    *,
    db: Session,
    tenant_id: str,
    file_id: str,
    filename: str,
    ext: str,
    stored_path: Path,
    full_text: str,
) -> int:
    """
    Mantém sua lógica:
    - split_text -> embeddings -> salva em DB (kb_chunks)
    - atualiza kb_store.json por compatibilidade
    - registra arquivo em tabela files
    """
    chunks = split_text(full_text, settings.RAG_CHUNK_SIZE, settings.RAG_CHUNK_OVERLAP)
    if not chunks:
        return 0

    embeddings = embed_texts(chunks, model=settings.OPENAI_EMBED_MODEL)

    # upsert arquivo
    f = db.query(FileDB).filter(FileDB.file_id == file_id, FileDB.tenant_id == tenant_id).first()
    if not f:
        f = FileDB(
            file_id=file_id,
            tenant_id=tenant_id,
            filename=filename,
            ext=ext,
            stored_path=str(stored_path),
            size=stored_path.stat().st_size if stored_path.exists() else 0,
        )
        db.add(f)
        db.flush()

    # Persist full text to disk (best-effort) for deterministic ops.
    try:
        f.full_text_path = save_full_text(tenant_id=tenant_id, file_id=file_id, text=full_text)
    except Exception:
        pass

    # Persist basic metadata for deterministic queries (best-effort).
    try:
        f.text_chars = int(len(full_text or ""))
    except Exception:
        pass
    try:
        ext_low = (ext or "").lower().strip(".")
        if ext_low in ("csv", "xlsx"):
            p = Path(str(stored_path))
            if ext_low == "xlsx":
                df = pd.read_excel(p)
            else:
                df = pd.read_csv(p)
            f.rows = int(df.shape[0])
            f.cols = int(df.shape[1])
            f.columns_json = json.dumps([str(c) for c in list(df.columns)], ensure_ascii=False)
    except Exception:
        pass
    db.commit()

    # salva chunks no DB
    added = 0
    for text, emb in zip(chunks, embeddings):
        row = KBChunkDB(
            tenant_id=tenant_id,
            file_id=file_id,
            filename=filename,
            ext=ext,
            text=text,
            embedding_json=json.dumps(emb),
        )
        db.add(row)
        added += 1
    db.commit()

    # compatibilidade kb_store.json
    kb = load_kb()
    kb_chunks = kb.get("chunks") or []
    for text, emb in zip(chunks, embeddings):
        kb_chunks.append(
            {
                "file_id": file_id,
                "filename": filename,
                "ext": ext,
                "text": text,
                "embedding": emb,
            }
        )
    kb["chunks"] = kb_chunks
    save_kb(kb)

    return added


def retrieve_context(db: Session, tenant_id: str, query: str, top_k: int) -> List[Dict[str, Any]]:
    """
    Recupera chunks por similaridade de cosseno (embeddings em DB).
    """
    q = (query or "").strip()
    if not q:
        return []

    q_emb = embed_texts([q], model=settings.OPENAI_EMBED_MODEL)
    if not q_emb:
        return []
    q_emb = q_emb[0]

    rows = db.query(KBChunkDB).filter(KBChunkDB.tenant_id == tenant_id).all()
    scored: List[Tuple[float, KBChunkDB]] = []

    for r in rows:
        try:
            emb = json.loads(r.embedding_json or "[]")
        except Exception:
            emb = []
        score = cosine_similarity(q_emb, emb)
        scored.append((score, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    out: List[Dict[str, Any]] = []
    for score, r in scored[: max(0, top_k)]:
        out.append(
            {
                "score": float(score),
                "chunk_id": int(r.id),
                "file_id": r.file_id,
                "filename": r.filename,
                "ext": r.ext,
                "text": r.text,
            }
        )
    return out


def retrieve_context_lexical(db: Session, tenant_id: str, query: str, top_k: int) -> List[Dict[str, Any]]:
    """
    Simple lexical retrieval (case-insensitive) to complement embeddings retrieval.
    Scoring is intentionally lightweight: term frequency + density bonus.
    """
    q = (query or "").strip()
    if not q:
        return []

    terms = re.findall(r"[\wÀ-ÿ]{2,}", q.lower(), flags=re.UNICODE)
    # Keep only a handful of distinct terms (avoid over-penalizing long questions).
    seen = set()
    uniq: List[str] = []
    for t in terms:
        if t in seen:
            continue
        seen.add(t)
        uniq.append(t)
        if len(uniq) >= 10:
            break
    if not uniq:
        return []

    rows = (
        db.query(KBChunkDB)
        .filter(KBChunkDB.tenant_id == tenant_id)
        .order_by(KBChunkDB.id.asc())
        .all()
    )

    scored: List[Tuple[float, KBChunkDB]] = []
    for r in rows:
        text = (r.text or "")
        low = text.lower()
        tf = 0
        hits = 0
        first_pos = None
        last_pos = None
        for term in uniq:
            c = low.count(term)
            if c <= 0:
                continue
            hits += 1
            tf += c
            p = low.find(term)
            if p >= 0:
                first_pos = p if first_pos is None else min(first_pos, p)
                last_pos = p + len(term) if last_pos is None else max(last_pos, p + len(term))

        if tf <= 0:
            continue

        # Base: term frequency, bonus for covering multiple distinct terms.
        score = float(tf) + (2.0 * float(hits - 1))
        # Density bonus: if hits occur close together, prefer this chunk.
        if first_pos is not None and last_pos is not None and last_pos > first_pos:
            span = max(1, last_pos - first_pos)
            score += 6.0 * (1.0 / float(span)) * 100.0
        # Mild normalization by length.
        score *= 800.0 / max(200.0, float(len(low)))
        scored.append((float(score), r))

    scored.sort(key=lambda x: x[0], reverse=True)
    out: List[Dict[str, Any]] = []
    for score, r in scored[: max(0, int(top_k or 6))]:
        out.append(
            {
                "score": float(score),
                "chunk_id": int(r.id),
                "file_id": r.file_id,
                "filename": r.filename,
                "ext": r.ext,
                "text": r.text,
            }
        )
    return out


def build_rag_system_prompt(chunks: List[Dict[str, Any]]) -> str:
    """
    Prompt RAG de alta precisao: o documento e a unica fonte de verdade.
    """
    if not chunks:
        return (
            "Você é um analisador técnico de documentos.\n"
            "Sua única fonte de verdade é o CONTEXTO fornecido.\n"
            "Se a informação não estiver explicitamente no CONTEXTO, responda apenas:\n"
            "'Essa informação não consta no documento analisado.'"
        )

    parts: List[str] = []
    parts.append(
        "Você é um analisador técnico de documentos.\n"
        "Sua única fonte de verdade é o CONTEXTO fornecido.\n"
        "\nREGRAS (OBRIGATÓRIAS):\n"
        "- NÃO assumir, inferir ou sugerir informações externas.\n"
        "- NÃO responder com conselhos genéricos.\n"
        "- NUNCA sugerir consultar aplicativo, banco ou outra fonte externa.\n"
        "- PROIBIDO usar frases como:\n"
        "  - 'Você pode verificar...'\n"
        "  - 'Recomendo consultar...'\n"
        "  - 'Caso contrário...'\n"
        "  - 'Se precisar...'\n"
        "- Se a informação existir no CONTEXTO, responda com precisão absoluta.\n"
        "- Se a informação NÃO estiver explicitamente no CONTEXTO, responda apenas:\n"
        "  'Essa informação não consta no documento analisado.'\n"
        "\nPRECISÃO:\n"
        "- Para perguntas como 'qual o valor', 'qual a data', 'quantas parcelas', 'qual o número':\n"
        "  localize explicitamente no CONTEXTO e devolva exatamente como está no documento.\n"
        "\nCONTEXTO:\n"
    )
    for i, c in enumerate(chunks, start=1):
        parts.append(
            f"\n[Trecho {i}] (Fonte: {c.get('filename')})\n{c.get('text')}\n"
        )
    return "".join(parts)

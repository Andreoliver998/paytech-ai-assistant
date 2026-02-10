from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from pypdf import PdfReader
from sqlalchemy.orm import Session

from models import FileDB, KBChunkDB
from services.openai_service import embed_texts, cosine_similarity
from settings import settings
from utils.files import load_kb, save_kb


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
    f = db.get(FileDB, file_id)
    if not f:
        f = FileDB(
            file_id=file_id,
            filename=filename,
            ext=ext,
            stored_path=str(stored_path),
            size=stored_path.stat().st_size if stored_path.exists() else 0,
        )
        db.add(f)
        db.commit()

    # salva chunks no DB
    added = 0
    for text, emb in zip(chunks, embeddings):
        row = KBChunkDB(
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


def retrieve_context(db: Session, query: str, top_k: int) -> List[Dict[str, Any]]:
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

    rows = db.query(KBChunkDB).all()
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
                "file_id": r.file_id,
                "filename": r.filename,
                "ext": r.ext,
                "text": r.text,
            }
        )
    return out


def build_rag_system_prompt(chunks: List[Dict[str, Any]]) -> str:
    """
    Prompt RAG “ChatGPT-like”: use o contexto quando relevante, cite o arquivo e não invente.
    """
    if not chunks:
        return (
            "Você tem acesso a documentos enviados pelo usuário. "
            "Se o usuário pedir algo sobre documentos, peça para enviar/selecionar o arquivo."
        )

    parts: List[str] = []
    parts.append(
        "Você tem acesso ao CONTEXTO extraído de documentos do usuário.\n"
        "Regras:\n"
        "- Use o contexto apenas quando for relevante.\n"
        "- Se o contexto não contiver a resposta, diga que não encontrou no documento.\n"
        "- Ao usar o contexto, cite a fonte no texto (ex.: 'Fonte: arquivo.pdf').\n"
        "- Não invente números, nomes ou conclusões não presentes.\n"
        "\nCONTEXTO:\n"
    )
    for i, c in enumerate(chunks, start=1):
        parts.append(
            f"\n[Trecho {i}] (Fonte: {c.get('filename')} | score={c.get('score'):.3f})\n{c.get('text')}\n"
        )
    return "".join(parts)

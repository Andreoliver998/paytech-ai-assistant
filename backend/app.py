from __future__ import annotations

import re
import uuid
import json
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import concurrent.futures

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from db import Base, engine, get_db
from models import DownloadChunkDB, DownloadFileDB, FileDB, KBChunkDB, MessageDB, SessionDB
from services.openai_service import gerar_resposta, gerar_resposta_stream
from services.downloads_service import list_downloads, search_downloads
from services.memory_service import get_preferences, upsert_preferences
from services.title_service import generate_conversation_title
from services.llm_planner import plan_next_action
from services.tool_runner import run_tools
from services.verifier import verify_and_fix
from services.memory_store import get_user_prefs, recall_user_prefs, upsert_thread_meta, upsert_user_pref
from services.rag_service import (
    build_rag_system_prompt,
    extract_csv_text,
    extract_pdf_text,
    extract_xlsx_text,
    index_file_and_save,
    retrieve_context,
)
from settings import settings
from utils.files import DATA_DIR, UPLOADS_DIR, KB_FILE
from utils.text import sanitize_and_trim_messages
from routers.downloads_router import router as downloads_router
from routers.export_router import router as export_router

# Exports (artifacts)
EXPORTS_DIR = Path(__file__).resolve().parent / "storage" / "exports"
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

# NOTE: We intentionally keep these executors module-scoped.
# Using `with ThreadPoolExecutor(...) as ex:` defeats `future.result(timeout=...)`
# because the context manager calls `shutdown(wait=True)` and blocks until the
# worker finishes anyway (causing SSE to get stuck after the initial "thinking").
_PLANNER_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="planner")
_TOOLS_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="tools")


# =========================================================
# App
# =========================================================
app = FastAPI(title="PayTechAI (Senior)")

# Frontend asset (for favicon fallback when opening backend base URL)
FRONTEND_FAVICON = (Path(__file__).resolve().parent.parent / "frontend" / "assets" / "pay.png")


# =========================================================
# CORS (sem abrir "*" em prod)
# =========================================================
origins = settings.cors_list()
app.add_middleware(
    CORSMiddleware,
    # Em DEV, é comum variar a porta do frontend (Live Server, Vite, etc.).
    # Se a origem não estiver explicitamente listada, usamos um regex seguro
    # para localhost/127.0.0.1 em qualquer porta.
    #
    # Nota: ao abrir o frontend via `file://` o browser usa `Origin: null`.
    # Em DEV, liberamos explicitamente `null` para evitar “chat não responde”
    # por bloqueio de CORS. (Em PROD isso fica desabilitado.)
    allow_origins=origins if origins else [],
    allow_origin_regex=(
        r"^(null|https?://(localhost|127\\.0\\.0\\.1)(:\\d+)?)$"
        if settings.ENV.lower() == "dev"
        else None
    ),
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


# =========================================================
# DB init (compatível com sua lógica; em prod use migrations)
# =========================================================
if settings.ENV.lower() == "dev":
    Base.metadata.create_all(bind=engine)

# Routers
app.include_router(downloads_router)
app.include_router(export_router)


# =========================================================
# Schemas
# =========================================================
class ChatRequest(BaseModel):
    messages: List[Dict[str, Any]]
    session_id: Optional[str] = None  # se vier, salva user/assistant no DB
    title: Optional[str] = None        # opcional: atualizar título na primeira msg
    user_id: Optional[str] = None
    thread_id: Optional[str] = None
    response_mode: Optional[str] = None
    use_downloads: bool = False
    downloads_top_k: int = 6


class SummarizeRequest(BaseModel):
    mode: str = "all"  # "all" | "file"
    file_id: Optional[str] = None
    style: str = "completo"
    max_sources: int = 12


class SessionCreateRequest(BaseModel):
    title: str = "Nova conversa"


class SessionModel(BaseModel):
    id: str
    title: str = "Conversa"
    createdAt: str
    updatedAt: str
    messages: List[Dict[str, Any]] = []

class TitleGenerateRequest(BaseModel):
    user_id: Optional[str] = None
    conversation_id: Optional[str] = None
    first_user: str
    first_assistant: str


# =========================================================
# Helpers (DB sessions)
# =========================================================
def _ensure_session(db: Session, session_id: str, title: Optional[str] = None) -> SessionDB:
    s = db.get(SessionDB, session_id)
    now = datetime.now()
    if not s:
        s = SessionDB(
            id=session_id,
            title=(title or "Conversa").strip() or "Conversa",
            createdAt=now,
            updatedAt=now,
        )
        db.add(s)
        db.commit()
        return s

    if title:
        t = (title or "").strip()
        if t:
            s.title = t
    s.updatedAt = now
    db.commit()
    return s


def _persist_message(db: Session, session_id: str, role: str, content: str) -> None:
    role = (role or "").strip()
    content = (content or "").strip()
    if role not in ("system", "user", "assistant") or not content:
        return
    db.add(MessageDB(session_id=session_id, role=role, content=content))
    s = db.get(SessionDB, session_id)
    if s:
        s.updatedAt = datetime.now()
    db.commit()


def _should_use_kb(last_user: Optional[str]) -> bool:
    if not last_user:
        return False
    t = last_user.lower()
    return any(x in t for x in ["pdf", "csv", "xlsx", "arquivo", "documento", "planilha", "comprovante", "/kb", "/rag"])


def _should_use_downloads(last_user: Optional[str]) -> bool:
    if not last_user:
        return False
    t = last_user.lower()
    return any(x in t for x in ["pdf", "csv", "xlsx", "txt", "arquivo", "documento", "planilha", "comprovante", "downloads", "download"])

def _is_list_documents_request(last_user: Optional[str]) -> bool:
    if not last_user:
        return False
    t = last_user.lower()
    triggers = [
        "quais documentos",
        "quais arquivos",
        "listar documentos",
        "liste os documentos",
        "meus documentos",
        "documentos armazen",
        "arquivos armazen",
        "documentos que você tem",
        "documentos disponíveis",
    ]
    return any(x in t for x in triggers)

def _format_downloads_list_markdown(items: List[Dict[str, Any]]) -> str:
    if not items:
        return (
            "Não encontrei documentos na biblioteca.\n\n"
            "Envie um PDF/CSV/XLSX/TXT em **Download → Adicionar documentos** (ou anexe no chat) e tente novamente."
        )
    lines = ["Documentos disponíveis na biblioteca (Downloads):", ""]
    for i, f in enumerate(items[:50], start=1):
        name = str(f.get("filename") or "arquivo").strip() or "arquivo"
        fid = str(f.get("id") or "").strip()
        lines.append(f"{i}. {name}" + (f" (`{fid}`)" if fid else ""))
    if len(items) > 50:
        lines.append("")
        lines.append(f"... e mais {len(items) - 50} arquivo(s).")
    lines.append("")
    lines.append("Dica: abra **Download** no menu esquerdo para ver/gerenciar e usar nas respostas.")
    return "\n".join(lines)


def _temperature(use_kb: bool) -> float:
    return float(settings.TEMPERATURE_RAG if use_kb else settings.TEMPERATURE_GENERAL)

def _normalize_response_mode(mode: Optional[str]) -> str:
    m = (mode or "").strip().lower()
    if m in ("tecnico", "resumido", "didatico", "estrategico"):
        return m
    return "tecnico"


def _mode_system_prompt(mode: str) -> str:
    m = _normalize_response_mode(mode)
    if m == "resumido":
        return "Modo de resposta: RESUMIDO. Seja direto, sem floreios, com no máximo 6 bullets quando possível."
    if m == "didatico":
        return "Modo de resposta: DIDÁTICO. Explique com clareza, exemplos curtos e passos numerados quando fizer sentido."
    if m == "estrategico":
        return "Modo de resposta: ESTRATÉGICO. Traga opções, trade-offs, recomendações e próximos passos."
    return "Modo de resposta: TÉCNICO. Seja preciso, objetivo e consistente com detalhes relevantes."


def _memory_system_prompt(prefs: Dict[str, Any]) -> Optional[str]:
    if not prefs:
        return None
    # Keep this short: it's a UX lever, not a long-term profile store.
    parts: List[str] = []
    mode = _normalize_response_mode(str(prefs.get("response_mode") or ""))
    if mode:
        parts.append(f"- Modo preferido: {mode}")
    if prefs.get("use_downloads") in (True, False):
        parts.append(f"- Usar documentos: {'sim' if prefs.get('use_downloads') else 'não'}")
    if not parts:
        return None
    return "Preferências persistentes do usuário:\n" + "\n".join(parts)


# =========================================================
# Rotas básicas
# =========================================================
@app.get("/health")
def health():
    return {"status": "ok", "env": settings.ENV}

@app.get("/")
def root():
    return {
        "service": "paytechai",
        "env": settings.ENV,
        "health": "/health",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


@app.get("/favicon.ico")
def favicon():
    if FRONTEND_FAVICON.exists() and FRONTEND_FAVICON.is_file():
        return FileResponse(path=str(FRONTEND_FAVICON), media_type="image/png", filename="favicon.png")
    return Response(status_code=204)


# Compat: alguns frontends/proxies testam "/api/health".
@app.get("/api/health")
def api_health():
    return health()


@app.get("/config-check")
def config_check():
    return {
        "api_key_loaded": bool(settings.OPENAI_API_KEY),
        "model": settings.OPENAI_MODEL,
        "embed_model": settings.OPENAI_EMBED_MODEL,
        "data_dir": str(DATA_DIR),
        "uploads_dir": str(UPLOADS_DIR),
        "kb_file": str(KB_FILE),
        "db_url": settings.PAYTECH_DB_URL or "sqlite (default)",
        "cors_origins": settings.cors_list(),
    }


@app.get("/meta")
def meta():
    return {
        "service": "paytechai",
        "env": settings.ENV,
        "version": "1.0.0",
        "endpoints": {
            "health": "/health",
            "chat": "/chat",
            "chat_stream": "/chat/stream",
            "upload": "/upload",
            "kb_stats": "/kb/stats",
            "kb_preview": "/kb/file/{file_id}/preview",
            "kb_summarize": "/kb/summarize",
            "kb_summarize_api": "/api/kb/summarize",
            "documents_summarize": "/documents/{id}/summarize",
            "documents_summarize_api": "/api/documents/{id}/summarize",
        },
    }


@app.get("/debug/paths")
def debug_paths():
    return {
        "DATA_DIR": str(DATA_DIR),
        "UPLOADS_DIR": str(UPLOADS_DIR),
        "KB_FILE": str(KB_FILE),
    }


# =========================================================
# Sessões (DB) — usado pelo frontend
# =========================================================
@app.get("/sessions")
def sessions_list(db: Session = Depends(get_db)):
    rows = db.query(SessionDB).order_by(SessionDB.updatedAt.desc()).all()
    out = []
    for s in rows:
        out.append(
            {
                "id": s.id,
                "title": s.title,
                "createdAt": s.createdAt.isoformat(timespec="seconds"),
                "updatedAt": s.updatedAt.isoformat(timespec="seconds"),
            }
        )
    return {"sessions": out}


@app.post("/sessions")
def sessions_create(payload: SessionCreateRequest, db: Session = Depends(get_db)):
    sid = str(uuid.uuid4())
    now = datetime.now()

    s = SessionDB(
        id=sid,
        title=(payload.title or "Nova conversa").strip() or "Nova conversa",
        createdAt=now,
        updatedAt=now,
    )
    db.add(s)
    db.commit()

    return {
        "session": {
            "id": s.id,  # ✅ corrigido (tinha "s.. id" quebrado)
            "title": s.title,
            "createdAt": s.createdAt.isoformat(timespec="seconds"),
            "updatedAt": s.updatedAt.isoformat(timespec="seconds"),
            "messages": [],
        }
    }


@app.get("/sessions/{session_id}")
def sessions_get(session_id: str, db: Session = Depends(get_db)):
    s = db.get(SessionDB, session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")

    msgs = db.query(MessageDB).filter(MessageDB.session_id == session_id).order_by(MessageDB.id.asc()).all()
    messages = [{"role": m.role, "content": m.content} for m in msgs]

    return {
        "session": {
            "id": s.id,
            "title": s.title,
            "createdAt": s.createdAt.isoformat(timespec="seconds"),
            "updatedAt": s.updatedAt.isoformat(timespec="seconds"),
            "messages": messages,
        }
    }


@app.put("/sessions/{session_id}")
def sessions_put(session_id: str, payload: SessionModel, db: Session = Depends(get_db)):
    if payload.id != session_id:
        raise HTTPException(status_code=400, detail="ID da URL diferente do payload.")

    s = db.get(SessionDB, session_id)
    now = datetime.now()

    if not s:
        s = SessionDB(
            id=session_id,
            title=(payload.title or "Conversa").strip() or "Conversa",
            createdAt=datetime.fromisoformat(payload.createdAt) if payload.createdAt else now,
            updatedAt=now,
        )
        db.add(s)
        db.commit()
    else:
        s.title = (payload.title or s.title).strip() or s.title
        s.updatedAt = now
        db.commit()

    db.query(MessageDB).filter(MessageDB.session_id == session_id).delete()
    for m in payload.messages or []:
        role = (m.get("role") or "").strip()
        content = (m.get("content") or "").strip()
        if role in ("system", "user", "assistant") and content:
            db.add(MessageDB(session_id=session_id, role=role, content=content))
    db.commit()

    return sessions_get(session_id, db)


@app.delete("/sessions/{session_id}")
def sessions_delete(session_id: str, db: Session = Depends(get_db)):
    s = db.get(SessionDB, session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    db.delete(s)
    db.commit()
    return {"ok": True}


@app.get("/exports/{name}")
def exports_get(name: str):
    safe = (name or "").strip()
    if not safe or "/" in safe or "\\" in safe or ".." in safe:
        raise HTTPException(status_code=400, detail="Nome inválido.")
    path = EXPORTS_DIR / safe
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Artefato não encontrado.")
    media_type = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path=str(path), media_type=media_type, filename=path.name)


# =========================================================
# User Memory (preferences)
# =========================================================
class MemoryPatchRequest(BaseModel):
    preferences: Dict[str, Any] = {}


@app.get("/memory/{user_id}")
def memory_get(user_id: str, db: Session = Depends(get_db)):
    prefs = get_preferences(db, user_id)
    return {"user_id": (user_id or "").strip(), "preferences": prefs}


@app.put("/memory/{user_id}")
def memory_put(user_id: str, payload: MemoryPatchRequest, db: Session = Depends(get_db)):
    prefs = upsert_preferences(db, user_id, payload.preferences or {})
    return {"user_id": (user_id or "").strip(), "preferences": prefs}


# =========================================================
# Auto Title
# =========================================================
@app.post("/titles/generate")
def titles_generate(payload: TitleGenerateRequest, db: Session = Depends(get_db)):
    title = generate_conversation_title(
        first_user=payload.first_user,
        first_assistant=payload.first_assistant,
        model=settings.OPENAI_MODEL,
    )
    # persist in memory (best-effort) so the platform "remembers" preferred style signals
    if payload.user_id:
        upsert_preferences(db, payload.user_id, {"last_title": title})
    return {"title": title}


# =========================================================
# Upload (salva em disco + DB + kb_store.json)
# =========================================================
@app.post("/upload")
async def upload_file(file: UploadFile = File(...), db: Session = Depends(get_db)):
    filename = file.filename or "arquivo"
    ext = (Path(filename).suffix or "").lower().strip(".")
    if ext not in ("pdf", "csv", "xlsx"):
        raise HTTPException(status_code=400, detail="Formato não suportado. Use PDF, CSV ou XLSX.")

    safe_name = re.sub(r"[^a-zA-Z0-9._ -]", "_", filename)
    file_id = f"{int(datetime.now().timestamp())}_{safe_name}"
    save_path = UPLOADS_DIR / file_id

    content = await file.read()
    save_path.write_bytes(content)

    try:
        if ext == "pdf":
            text = extract_pdf_text(save_path)
        elif ext == "csv":
            text = extract_csv_text(save_path)
        else:
            text = extract_xlsx_text(save_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha ao ler arquivo: {e}")

    if not text.strip():
        raise HTTPException(status_code=400, detail="Não consegui extrair conteúdo do arquivo.")

    try:
        added = index_file_and_save(
            db=db,
            file_id=file_id,
            filename=filename,
            ext=ext,
            stored_path=save_path,
            full_text=text,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha ao indexar arquivo: {e}")

    return {"ok": True, "file_id": file_id, "filename": filename, "chunks_added": added}


@app.get("/files")
def list_files():
    files = []
    for p in UPLOADS_DIR.glob("*"):
        if p.is_file():
            files.append({"name": p.name, "size": p.stat().st_size})
    return {"files": files}


# =========================================================
# Files (DB): lista + download do arquivo original
# =========================================================
@app.get("/files/db")
def list_files_db(db: Session = Depends(get_db)):
    rows = db.query(FileDB).order_by(FileDB.createdAt.desc()).all()
    return {
        "files": [
            {
                "file_id": r.file_id,
                "filename": r.filename,
                "ext": r.ext,
                "size": r.size,
                "createdAt": r.createdAt.isoformat(timespec="seconds") if r.createdAt else None,
            }
            for r in rows
        ]
    }


@app.get("/files/db/{file_id}/download")
def download_file_db(file_id: str, db: Session = Depends(get_db)):
    r = db.get(FileDB, file_id)
    if not r:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado no banco.")

    stored_path = Path(r.stored_path or "")
    if not stored_path.exists() or not stored_path.is_file():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado no disco.")

    filename = (r.filename or stored_path.name or "arquivo").strip() or "arquivo"
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(path=str(stored_path), media_type=media_type, filename=filename)


# =========================================================
# KB endpoints (para o painel “Documentos” do frontend)
# =========================================================
@app.get("/kb/stats")
def kb_stats(db: Session = Depends(get_db)):
    files = db.query(FileDB).order_by(FileDB.createdAt.desc()).all()

    # agrega chunks por arquivo
    chunk_counts = dict(
        db.query(KBChunkDB.file_id, func.count(KBChunkDB.id))
        .group_by(KBChunkDB.file_id)
        .all()
    )

    # agrega chars por arquivo (soma do tamanho do texto)
    # (para SQLite, length(text) funciona; para outros DBs também costuma funcionar)
    chars_counts = dict(
        db.query(KBChunkDB.file_id, func.coalesce(func.sum(func.length(KBChunkDB.text)), 0))
        .group_by(KBChunkDB.file_id)
        .all()
    )

    chunks_total = db.query(KBChunkDB).count()

    return {
        "files": [
            {
                "file_id": f.file_id,
                "filename": f.filename,
                "ext": f.ext,
                "size": f.size,
                "createdAt": f.createdAt.isoformat(timespec="seconds"),
                "chunks": int(chunk_counts.get(f.file_id, 0)),
                "chars": int(chars_counts.get(f.file_id, 0)),
            }
            for f in files
        ],
        "chunks": chunks_total,
        "kb_file": str(KB_FILE),
    }


@app.get("/kb/file/{file_id}/preview")
def kb_preview(file_id: str, limit: int = settings.KB_PREVIEW_LIMIT, db: Session = Depends(get_db)):
    rows = (
        db.query(KBChunkDB)
        .filter(KBChunkDB.file_id == file_id)
        .order_by(KBChunkDB.id.asc())
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado na base de conhecimento.")

    filename = rows[0].filename if getattr(rows[0], "filename", None) else "arquivo"
    ext = getattr(rows[0], "ext", None) or ""

    full_text = "\n\n".join([r.text for r in rows])
    full_chars = len(full_text)
    preview = full_text[: max(0, int(limit))]

    return {
        "file_id": file_id,
        "filename": filename,
        "ext": ext,
        "chunks": len(rows),
        "chars": full_chars,
        "preview": preview,
    }


# =========================================================
# KB / Summarização
# =========================================================
def _kb_summarize_impl(*, mode: str, file_id: Optional[str], style: str, max_sources: int, db: Session) -> Dict[str, Any]:
    mode = (mode or "all").strip().lower()
    style = (style or "completo").strip().lower()
    max_sources = max(1, int(max_sources or 12))

    if mode == "file":
        if not file_id:
            raise HTTPException(status_code=400, detail="file_id é obrigatório quando mode='file'.")
        rows = (
            db.query(KBChunkDB)
            .filter(KBChunkDB.file_id == file_id)
            .order_by(KBChunkDB.id.asc())
            .limit(max_sources)
            .all()
        )
    else:
        rows = db.query(KBChunkDB).order_by(KBChunkDB.id.desc()).limit(max_sources).all()

    chunks = [{"filename": r.filename, "text": r.text, "score": 1.0} for r in rows]

    system_prompt = build_rag_system_prompt(chunks)
    prompt_user = f"""Faça um resumo dos documentos.
Estilo: {style}
Cite as fontes por nome do arquivo."""

    reply = gerar_resposta(
        mensagens=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt_user},
        ],
        modelo=settings.OPENAI_MODEL,
        temperatura=0.2,
    )

    # ✅ frontend espera [{ref, filename}]
    sources = [{"ref": f"Fonte {i+1}", "filename": c["filename"]} for i, c in enumerate(chunks)]

    return {"ok": True, "summary": reply, "sources": sources}


@app.post("/kb/summarize")
def kb_summarize(payload: SummarizeRequest, db: Session = Depends(get_db)):
    return _kb_summarize_impl(
        mode=payload.mode or "all",
        file_id=payload.file_id,
        style=payload.style or "completo",
        max_sources=payload.max_sources or 12,
        db=db,
    )


# ✅ COMPAT: evita 405 quando alguém faz GET /kb/summarize
@app.get("/kb/summarize")
def kb_summarize_get(
    mode: str = Query("all"),
    file_id: Optional[str] = Query(None),
    style: str = Query("completo"),
    max_sources: int = Query(12),
    db: Session = Depends(get_db),
):
    # mantém a lógica (mesmo impl), só muda o "contrato" para GET via querystring
    return _kb_summarize_impl(mode=mode, file_id=file_id, style=style, max_sources=max_sources, db=db)


# ---- Aliases de compatibilidade para evitar 404 por mudança de rota/prefixo ----
@app.post("/api/kb/summarize")
def kb_summarize_api(payload: SummarizeRequest, db: Session = Depends(get_db)):
    return kb_summarize(payload, db)


@app.get("/api/kb/summarize")
def kb_summarize_api_get(
    mode: str = Query("all"),
    file_id: Optional[str] = Query(None),
    style: str = Query("completo"),
    max_sources: int = Query(12),
    db: Session = Depends(get_db),
):
    return kb_summarize_get(mode=mode, file_id=file_id, style=style, max_sources=max_sources, db=db)


class DocumentSummarizeRequest(BaseModel):
    style: str = "completo"
    max_sources: int = 12


@app.post("/documents/{doc_id}/summarize")
def documents_summarize(doc_id: str, payload: DocumentSummarizeRequest, db: Session = Depends(get_db)):
    return _kb_summarize_impl(
        mode="file",
        file_id=doc_id,
        style=payload.style or "completo",
        max_sources=payload.max_sources or 12,
        db=db,
    )


@app.post("/api/documents/{doc_id}/summarize")
def documents_summarize_api(doc_id: str, payload: DocumentSummarizeRequest, db: Session = Depends(get_db)):
    return documents_summarize(doc_id, payload, db)


# =========================================================
# Chat (NORMAL)
# =========================================================
@app.post("/chat")
def chat(payload: ChatRequest, db: Session = Depends(get_db)):
    try:
        trimmed = sanitize_and_trim_messages(payload.messages)

        last_user: Optional[str] = None
        for m in reversed(trimmed):
            if m["role"] == "user":
                last_user = m["content"]
                break

        # Fast path: list documents without an LLM roundtrip (reduz latência e evita respostas inúteis).
        if _is_list_documents_request(last_user):
            files = list_downloads(db)
            return {
                "reply": _format_downloads_list_markdown(files),
                "sources": [],
                "debug": {
                    "received_messages": len(payload.messages),
                    "sent_to_model": 0,
                    "context_used": 0,
                    "model": settings.OPENAI_MODEL,
                    "use_kb": False,
                    "use_downloads": True,
                    "response_mode": _normalize_response_mode(payload.response_mode),
                    "saved_to_db": bool(payload.session_id),
                    "fast_path": "list_downloads",
                    "downloads_count": len(files),
                },
            }

        prefs = get_preferences(db, payload.user_id or "") if payload.user_id else {}
        mode = _normalize_response_mode(payload.response_mode or (prefs.get("response_mode") if isinstance(prefs, dict) else None))

        use_downloads_pref = bool(prefs.get("use_downloads")) if isinstance(prefs, dict) else False
        use_downloads = bool(payload.use_downloads) or use_downloads_pref or _should_use_downloads(last_user)
        use_kb = _should_use_kb(last_user)

        if payload.user_id:
            upsert_preferences(
                db,
                payload.user_id,
                {
                    "response_mode": mode,
                    "use_downloads": bool(payload.use_downloads) or use_downloads_pref,
                },
            )

        mem_prompt = _memory_system_prompt(prefs if isinstance(prefs, dict) else {})
        sys_prefix: List[Dict[str, Any]] = []
        if mem_prompt:
            sys_prefix.append({"role": "system", "content": mem_prompt})
        sys_prefix.append({"role": "system", "content": _mode_system_prompt(mode)})

        if use_downloads:
            context_chunks = search_downloads(db, last_user or "", top_k=int(payload.downloads_top_k or settings.RAG_TOP_K))
            system_prompt = build_rag_system_prompt(context_chunks)
            final_messages = [{"role": "system", "content": system_prompt}] + sys_prefix + trimmed
        elif use_kb:
            context_chunks = retrieve_context(db, last_user or "", top_k=settings.RAG_TOP_K)
            system_prompt = build_rag_system_prompt(context_chunks)
            final_messages = [{"role": "system", "content": system_prompt}] + sys_prefix + trimmed
        else:
            context_chunks = []
            final_messages = sys_prefix + trimmed

        reply = gerar_resposta(
            mensagens=final_messages,
            modelo=settings.OPENAI_MODEL,
            temperatura=_temperature(use_kb),
        )

        if payload.session_id:
            _ensure_session(db, payload.session_id, title=payload.title)
            if last_user:
                _persist_message(db, payload.session_id, "user", last_user)
            _persist_message(db, payload.session_id, "assistant", reply)

        sources = []
        if use_downloads or use_kb:
            sources = [{"ref": f"Fonte {i}", "filename": ch.get("filename")} for i, ch in enumerate(context_chunks, start=1)]

        return {
            "reply": reply,
            "sources": sources,
            "debug": {
                "received_messages": len(payload.messages),
                "sent_to_model": len(final_messages),
                "context_used": len(sources),
                "model": settings.OPENAI_MODEL,
                "use_kb": use_kb,
                "use_downloads": use_downloads,
                "response_mode": mode,
                "saved_to_db": bool(payload.session_id),
            },
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================
# Chat (STREAM) — SSE (POST) com eventos meta/delta/citations/done/error
# =========================================================
@app.post("/chat/stream")
def chat_stream(payload: ChatRequest, db: Session = Depends(get_db)):
    def _sse(event: str, data: Any) -> str:
        payload_str = json.dumps(data, ensure_ascii=False) if isinstance(data, (dict, list)) else str(data)
        return f"event: {event}\ndata: {payload_str}\n\n"

    def _planner_mode_to_prompt(mode: str) -> str:
        m = (mode or "normal").strip().lower()
        # Frontend supports: tecnico|resumido|didatico|estrategico
        # Planner supports: normal|didatico|executivo|tecnico
        # Map for compatibility so UI modes behave consistently in streaming.
        if m == "resumido":
            m = "executivo"
        if m == "estrategico":
            m = "executivo"
        if m == "didatico":
            return "Modo de resposta: DIDÁTICO. Explique com clareza, com passos e exemplos curtos."
        if m == "executivo":
            return "Modo de resposta: EXECUTIVO. Resuma, destaque decisões e próximos passos."
        if m == "tecnico":
            return "Modo de resposta: TÉCNICO. Seja preciso e detalhado no que importa."
        return "Modo de resposta: NORMAL. Seja claro e direto."

    def token_generator():
        message_id = str(uuid.uuid4())
        started_at = datetime.now().isoformat(timespec="seconds")

        try:
            yield _sse("status", {"phase": "thinking", "ts": started_at})

            trimmed = sanitize_and_trim_messages(payload.messages)
            last_user: Optional[str] = None
            for m in reversed(trimmed):
                if m["role"] == "user":
                    last_user = m["content"]
                    break

            # Fast path: list documents (stream-friendly; avoids planner/tools overhead).
            if _is_list_documents_request(last_user):
                yield _sse("status", {"phase": "answer"})
                files = list_downloads(db)
                yield _sse("delta", {"text": _format_downloads_list_markdown(files)})
                yield _sse(
                    "status",
                    {
                        "phase": "done",
                        "message_id": message_id,
                        "fast_path": "list_downloads",
                        "downloads_count": len(files),
                    },
                )
                return

            # structured memory + thread meta
            user_prefs = get_user_prefs(db, payload.user_id or "") if payload.user_id else {}
            if payload.thread_id and payload.title:
                upsert_thread_meta(db, payload.thread_id, payload.title)

            # quick thread context summary (last 6)
            ctx = []
            for m in (trimmed[-6:] if len(trimmed) > 6 else trimmed):
                role = m.get("role")
                content = (m.get("content") or "").strip()
                if role in ("user", "assistant") and content:
                    ctx.append(f"{role}: {content[:220]}")
            thread_context = "\n".join(ctx)

            # Plan with a hard timeout to avoid "stuck on thinking" if the planner stalls.
            def _call_plan():
                return plan_next_action(
                    user_message=last_user or "",
                    thread_context=thread_context,
                    user_prefs=user_prefs,
                    model=settings.OPENAI_MODEL,
                )

            fut = _PLANNER_EXECUTOR.submit(_call_plan)
            try:
                plan = fut.result(timeout=5.0)
            except Exception:
                # IMPORTANT: do not block waiting for the planner thread.
                try:
                    fut.cancel()
                except Exception:
                    pass
                m = str(payload.response_mode or "").strip().lower()
                if m in ("resumido", "estrategico"):
                    m = "executivo"
                if m not in ("normal", "didatico", "executivo", "tecnico"):
                    m = "normal"
                plan = {
                    "needs_rag": bool(payload.use_downloads) or _should_use_downloads(last_user),
                    "needs_export": "none",
                    "query": (last_user or "").strip(),
                    "response_mode": m,
                    "must_cite_sources": False,
                }

            # persist high-signal prefs
            if payload.user_id:
                try:
                    upsert_user_pref(db, payload.user_id, "response_mode", plan.get("response_mode") or "")
                except Exception:
                    pass

            # tools
            tool_phase = bool(plan.get("needs_rag")) or str(plan.get("needs_export") or "none") != "none"
            if tool_phase:
                yield _sse("status", {"phase": "tool"})
            # Tools can involve embeddings/export; protect with a timeout so we can still answer.
            def _call_tools():
                return run_tools(
                    db=db,
                    plan=plan,
                    exports_dir=EXPORTS_DIR,
                    conversation={
                        "id": payload.thread_id or payload.session_id or message_id,
                        "title": payload.title or "Conversa",
                        "messages": trimmed,
                    },
                )

            tool_res = None
            if tool_phase:
                fut = _TOOLS_EXECUTOR.submit(_call_tools)
                try:
                    tool_res = fut.result(timeout=8.0)
                except Exception:
                    # IMPORTANT: do not block waiting for tools; just degrade gracefully.
                    try:
                        fut.cancel()
                    except Exception:
                        pass
                    tool_res = None
            else:
                tool_res = _call_tools()

            sources = (tool_res.sources if tool_res else []) or []
            artifacts = (tool_res.artifacts if tool_res else []) or []

            # Build prompt (memory recall + RAG evidence)
            recall = recall_user_prefs(db, payload.user_id or "", last_user or "", top_k=4) if payload.user_id else []
            recall_lines = [f"- {k}: {v}" for (k, v, _score) in recall if k and v]
            recall_block = "Memória relevante:\n" + "\n".join(recall_lines) if recall_lines else ""

            evidence_block = ""
            if sources:
                lines = ["Evidências (documentos do usuário):"]
                for i, s in enumerate(sources, start=1):
                    meta = []
                    if s.get("page"):
                        meta.append(f"p.{s.get('page')}")
                    if s.get("sheet"):
                        meta.append(f"aba {s.get('sheet')}")
                    if s.get("rowRange"):
                        meta.append(f"linhas {s.get('rowRange')}")
                    meta_txt = (" (" + ", ".join(meta) + ")") if meta else ""
                    lines.append(f"[{i}] {s.get('filename')}{meta_txt}\n{(s.get('snippet') or '').strip()}")
                evidence_block = "\n\n".join(lines)

            sys_msgs: List[Dict[str, Any]] = []
            if recall_block:
                sys_msgs.append({"role": "system", "content": recall_block})
            sys_msgs.append({"role": "system", "content": _planner_mode_to_prompt(str(plan.get('response_mode') or 'normal'))})
            if evidence_block:
                sys_msgs.append({"role": "system", "content": evidence_block})
                if plan.get("must_cite_sources"):
                    sys_msgs.append({"role": "system", "content": "Se usar evidências, inclua uma seção final 'Fontes' com os itens citados."})

            final_messages = sys_msgs + trimmed

            yield _sse("status", {"phase": "answer"})

            collected: List[str] = []
            for token in gerar_resposta_stream(
                mensagens=final_messages,
                modelo=settings.OPENAI_MODEL,
                temperatura=_temperature(bool(plan.get("needs_rag"))),
            ):
                collected.append(token)
                yield _sse("delta", {"text": token})

            full = "".join(collected).strip()
            verified, _warnings = verify_and_fix(plan=plan, answer_text=full, sources=sources)
            if verified != full:
                # append only (no rewind)
                suffix = verified[len(full) :] if verified.startswith(full) else ("\n\n" + verified)
                if suffix.strip():
                    yield _sse("delta", {"text": suffix})
                    full = (full + suffix).strip()

            if payload.session_id:
                _ensure_session(db, payload.session_id, title=payload.title)
                if last_user:
                    _persist_message(db, payload.session_id, "user", last_user)
                if full:
                    _persist_message(db, payload.session_id, "assistant", full)

            if sources:
                yield _sse("sources", {"items": sources})
            for a in artifacts:
                yield _sse("artifact", a)

            yield _sse("status", {"phase": "done", "message_id": message_id})

        except Exception as e:
            yield _sse("status", {"phase": "error", "message": str(e), "message_id": message_id})

    return StreamingResponse(
        token_generator(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

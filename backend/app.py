from __future__ import annotations

import re
import uuid
import json
import mimetypes
import logging
import time
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import concurrent.futures
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from .db import get_db, SessionLocal, bootstrap_database
from .models import DownloadChunkDB, DownloadFileDB, FileDB, KBChunkDB, MembershipDB, MessageDB, SessionDB, TenantDB, UserDB
from .services.openai_service import gerar_resposta, gerar_resposta_stream, get_client
from .services.auth_service import AuthContext, authenticate_user, create_access_token, get_current_user, maybe_seed_demo, register_user
from .services.downloads_service import list_downloads, search_downloads
from .services.memory_service import get_preferences, upsert_preferences
from .services.title_service import generate_conversation_title
from .services.llm_planner import plan_next_action
from .services.tool_runner import run_tools
from .services.verifier import verify_and_fix
from .services.memory_store import get_user_prefs, recall_user_prefs, upsert_thread_meta, upsert_user_pref
from .services.rag_service import (
    build_rag_system_prompt,
    extract_csv_text,
    extract_pdf_text,
    extract_xlsx_text,
    index_file_and_save,
    retrieve_context,
    retrieve_context_lexical,
)
from .services.precision_service import (
    compute_csv_filter,
    compute_on_text,
    compute_table_stats,
    load_full_text_for_download,
    load_full_text_for_kb_file,
    find_file_by_hint,
)
from .settings import settings, validate_openai_settings, openai_settings_hint, ENV_FILE_USED
from .utils.files import DATA_DIR, UPLOADS_DIR, KB_FILE
from .utils.text import sanitize_and_trim_messages
from .routers.downloads_router import router as downloads_router
from .routers.export_router import router as export_router

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
_startup_logger = logging.getLogger("paytechai.startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.log_config_summary(_startup_logger)
    try:
        bootstrap_database()
        with SessionLocal() as db:
            maybe_seed_demo(db)
    except Exception as e:
        _startup_logger.warning("Falha ao bootstrap de banco/auth: %s", str(e))
    hint = openai_settings_hint(settings)
    if hint:
        if (settings.ENV or "").strip().lower() == "dev":
            _startup_logger.warning(
                "OpenAI não configurado; endpoints /chat e /chat/stream podem falhar. Configure OPENAI_API_KEY no .env. env_file_used=%s",
                str(ENV_FILE_USED),
            )
        else:
            validate_openai_settings(settings)
    yield


app = FastAPI(title="PayTechAI (Senior)", lifespan=lifespan)

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
    # We don't use cookies/auth headers for this app; keeping credentials disabled
    # simplifies CORS semantics and avoids edge cases with streaming.
    allow_credentials=False,
)


# Routers
app.include_router(downloads_router)
app.include_router(export_router, dependencies=[Depends(get_current_user)])


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
    use_downloads: Optional[bool] = None
    downloads_top_k: Optional[int] = None
    show_sources: Optional[bool] = False
    precision: Optional[bool] = True
    file_id: Optional[str] = None


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


class AuthLoginRequest(BaseModel):
    email: str
    password: str
    tenant: Optional[str] = None


class AuthRegisterRequest(BaseModel):
    tenant_name: str
    email: str
    password: str


# =========================================================
# Helpers (DB sessions)
# =========================================================
def _ensure_session(
    db: Session,
    tenant_id: str,
    user_id: str,
    session_id: str,
    title: Optional[str] = None,
) -> SessionDB:
    s = db.query(SessionDB).filter(SessionDB.id == session_id, SessionDB.tenant_id == tenant_id).first()
    now = datetime.now()
    if not s:
        s = SessionDB(
            id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
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


def _persist_message(db: Session, tenant_id: str, session_id: str, role: str, content: str) -> None:
    role = (role or "").strip()
    content = (content or "").strip()
    if role not in ("system", "user", "assistant") or not content:
        return
    db.add(MessageDB(session_id=session_id, tenant_id=tenant_id, role=role, content=content))
    s = db.query(SessionDB).filter(SessionDB.id == session_id, SessionDB.tenant_id == tenant_id).first()
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


def _did_user_request_sources(user_text: Optional[str]) -> bool:
    t = re.sub(r"\s+", " ", (user_text or "").strip().lower())
    if not t:
        return False

    explicit_markers = (
        "fonte",
        "fontes",
        "de onde veio",
        "qual documento",
        "quais documentos",
        "referência",
        "referencias",
        "referências",
        "cite",
        "mostrar fontes",
        "listar fontes",
    )
    return any(m in t for m in explicit_markers)


def _temperature(use_kb: bool) -> float:
    return float(settings.TEMPERATURE_RAG if use_kb else settings.TEMPERATURE_GENERAL)


def _normalize_downloads_top_k(value: Optional[int]) -> int:
    fallback = max(1, int(settings.RAG_TOP_K or 6))
    try:
        k = int(value if value is not None else fallback)
    except Exception:
        return fallback
    return min(20, max(1, k))


def _resolve_use_downloads(
    requested: Optional[bool],
    pref_value: bool,
    last_user: Optional[str],
) -> bool:
    # Explicit user choice always wins.
    if requested is True:
        return True
    if requested is False:
        return False
    # Otherwise, rely on persistent preference, then heuristic trigger.
    return bool(pref_value) or _should_use_downloads(last_user)


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

@app.options("/chat/stream")
def chat_stream_options():
    # Explicit preflight handler (defensive): if a request reaches routing (e.g. missing
    # preflight headers), we still respond cleanly. CORSMiddleware will add CORS headers.
    return Response(status_code=204)

@app.options("/chat")
def chat_options():
    return Response(status_code=204)


@app.get("/health/openai")
def health_openai():
    try:
        client = get_client()
        models = client.models.list()
        data = getattr(models, "data", None) or []
        first = (data[0].id if data else None)
        return {
            "status": "ok",
            "openai": "ok",
            "model_count": len(data),
            "first_model": first,
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"OpenAI healthcheck failed: {e}")

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


@app.post("/auth/login")
def auth_login(payload: AuthLoginRequest, db: Session = Depends(get_db)):
    email = (payload.email or "").strip().lower()
    password = payload.password or ""
    tenant_hint = (payload.tenant or "").strip()
    if not email or not password:
        raise HTTPException(status_code=400, detail="email e password são obrigatórios.")

    auth = authenticate_user(db, email, password, tenant_hint or None)
    if not auth:
        raise HTTPException(status_code=401, detail="Credenciais inválidas.")
    user, tenant, membership = auth

    try:
        token = create_access_token(
            user_id=user.id,
            tenant_id=tenant.id,
            role=(membership.role or "MEMBER").upper(),
            email=user.email,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "ok": True,
        "token": token,
        "access_token": token,
        "token_type": "bearer",
        "user": {"id": user.id, "email": user.email, "role": (membership.role or "MEMBER").upper()},
        "tenant": {"id": tenant.id, "name": tenant.name},
    }


@app.post("/auth/register")
def auth_register(payload: AuthRegisterRequest, db: Session = Depends(get_db)):
    try:
        tenant, user, membership = register_user(
            db=db,
            tenant_name=payload.tenant_name,
            email=payload.email,
            password=payload.password,
        )
        token = create_access_token(
            user_id=user.id,
            tenant_id=tenant.id,
            role=(membership.role or "OWNER").upper(),
            email=user.email,
        )
        return {
            "ok": True,
            "token": token,
            "access_token": token,
            "token_type": "bearer",
            "user": {"id": user.id, "email": user.email, "role": (membership.role or "OWNER").upper()},
            "tenant": {"id": tenant.id, "name": tenant.name},
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/config-check")
def config_check():
    return {
        "api_key_loaded": settings.openai_api_key_loaded(),
        "api_key_fingerprint": settings.openai_api_key_fingerprint() if settings.openai_api_key_loaded() else "",
        "env_file_used": str(ENV_FILE_USED),
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
# Debug: deterministic SSE stream (for frontend validation)
# =========================================================
@app.get("/debug/stream")
@app.post("/debug/stream")
def debug_stream():
    """
    Deterministic SSE stream used to validate the frontend SSE parser + incremental rendering.

    Enable explicitly (never on by default):
      - Set DEBUG_STREAM=true in .env
    """
    if not bool(getattr(settings, "DEBUG_STREAM", False)):
        raise HTTPException(status_code=404, detail="Not found")

    def _sse(event: str, data: Any) -> str:
        payload_str = json.dumps(data, ensure_ascii=False) if isinstance(data, (dict, list)) else str(data)
        return f"event: {event}\ndata: {payload_str}\n\n"

    def token_generator():
        try:
            yield _sse("status", {"phase": "thinking", "ts": datetime.now().isoformat(timespec="seconds"), "debug": True})
            time.sleep(0.15)
            yield _sse("status", {"phase": "answer", "debug": True})
            time.sleep(0.10)
            yield _sse("delta", {"text": "A"})
            time.sleep(0.10)
            yield _sse("delta", {"text": "B"})
            time.sleep(0.10)
            yield _sse("delta", {"text": "C"})
            time.sleep(0.10)
            yield _sse("status", {"phase": "done", "debug": True})
        except (GeneratorExit, asyncio.CancelledError):
            return
        except Exception as e:
            yield _sse("status", {"phase": "error", "message": str(e), "debug": True})

    return StreamingResponse(
        token_generator(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# =========================================================
# Sessões (DB) — usado pelo frontend
# =========================================================
@app.get("/sessions")
def sessions_list(
    db: Session = Depends(get_db),
    current: AuthContext = Depends(get_current_user),
):
    rows = (
        db.query(SessionDB)
        .filter(SessionDB.tenant_id == current.tenant_id)
        .order_by(SessionDB.updatedAt.desc())
        .all()
    )
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
def sessions_create(
    payload: SessionCreateRequest,
    db: Session = Depends(get_db),
    current: AuthContext = Depends(get_current_user),
):
    sid = str(uuid.uuid4())
    now = datetime.now()

    s = SessionDB(
        id=sid,
        tenant_id=current.tenant_id,
        user_id=current.user_id,
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
def sessions_get(
    session_id: str,
    db: Session = Depends(get_db),
    current: AuthContext = Depends(get_current_user),
):
    s = db.query(SessionDB).filter(SessionDB.id == session_id, SessionDB.tenant_id == current.tenant_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")

    msgs = (
        db.query(MessageDB)
        .filter(MessageDB.session_id == session_id, MessageDB.tenant_id == current.tenant_id)
        .order_by(MessageDB.id.asc())
        .all()
    )
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
def sessions_put(
    session_id: str,
    payload: SessionModel,
    db: Session = Depends(get_db),
    current: AuthContext = Depends(get_current_user),
):
    if payload.id != session_id:
        raise HTTPException(status_code=400, detail="ID da URL diferente do payload.")

    s = db.query(SessionDB).filter(SessionDB.id == session_id, SessionDB.tenant_id == current.tenant_id).first()
    now = datetime.now()

    if not s:
        s = SessionDB(
            id=session_id,
            tenant_id=current.tenant_id,
            user_id=current.user_id,
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

    db.query(MessageDB).filter(MessageDB.session_id == session_id, MessageDB.tenant_id == current.tenant_id).delete()
    for m in payload.messages or []:
        role = (m.get("role") or "").strip()
        content = (m.get("content") or "").strip()
        if role in ("system", "user", "assistant") and content:
            db.add(MessageDB(session_id=session_id, tenant_id=current.tenant_id, role=role, content=content))
    db.commit()

    return sessions_get(session_id, db, current)


@app.delete("/sessions/{session_id}")
def sessions_delete(
    session_id: str,
    db: Session = Depends(get_db),
    current: AuthContext = Depends(get_current_user),
):
    s = db.query(SessionDB).filter(SessionDB.id == session_id, SessionDB.tenant_id == current.tenant_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    db.delete(s)
    db.commit()
    return {"ok": True}


@app.get("/exports/{name}")
def exports_get(
    name: str,
    current: AuthContext = Depends(get_current_user),
):
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
def memory_get(
    user_id: str,
    db: Session = Depends(get_db),
    current: AuthContext = Depends(get_current_user),
):
    prefs = get_preferences(db, user_id)
    return {"user_id": (user_id or "").strip(), "preferences": prefs}


@app.put("/memory/{user_id}")
def memory_put(
    user_id: str,
    payload: MemoryPatchRequest,
    db: Session = Depends(get_db),
    current: AuthContext = Depends(get_current_user),
):
    prefs = upsert_preferences(db, user_id, payload.preferences or {})
    return {"user_id": (user_id or "").strip(), "preferences": prefs}


# =========================================================
# Auto Title
# =========================================================
@app.post("/titles/generate")
def titles_generate(
    payload: TitleGenerateRequest,
    db: Session = Depends(get_db),
    current: AuthContext = Depends(get_current_user),
):
    title = generate_conversation_title(
        first_user=payload.first_user,
        first_assistant=payload.first_assistant,
        model=settings.OPENAI_MODEL,
    )
    # persist in memory (best-effort) so the platform "remembers" preferred style signals
    upsert_preferences(db, current.user_id, {"last_title": title})
    return {"title": title}


# =========================================================
# Upload (salva em disco + DB + kb_store.json)
# =========================================================
@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current: AuthContext = Depends(get_current_user),
):
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
            tenant_id=current.tenant_id,
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
def list_files(
    db: Session = Depends(get_db),
    current: AuthContext = Depends(get_current_user),
):
    rows = db.query(FileDB).filter(FileDB.tenant_id == current.tenant_id).order_by(FileDB.createdAt.desc()).all()
    return {"files": [{"name": r.filename, "size": int(r.size or 0), "file_id": r.file_id} for r in rows]}


# =========================================================
# Files (DB): lista + download do arquivo original
# =========================================================
@app.get("/files/db")
def list_files_db(
    db: Session = Depends(get_db),
    current: AuthContext = Depends(get_current_user),
):
    rows = (
        db.query(FileDB)
        .filter(FileDB.tenant_id == current.tenant_id)
        .order_by(FileDB.createdAt.desc())
        .all()
    )
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
def download_file_db(
    file_id: str,
    db: Session = Depends(get_db),
    current: AuthContext = Depends(get_current_user),
):
    r = db.query(FileDB).filter(FileDB.file_id == file_id, FileDB.tenant_id == current.tenant_id).first()
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
def kb_stats(
    db: Session = Depends(get_db),
    current: AuthContext = Depends(get_current_user),
):
    files = (
        db.query(FileDB)
        .filter(FileDB.tenant_id == current.tenant_id)
        .order_by(FileDB.createdAt.desc())
        .all()
    )

    # agrega chunks por arquivo
    chunk_counts = dict(
        db.query(KBChunkDB.file_id, func.count(KBChunkDB.id))
        .filter(KBChunkDB.tenant_id == current.tenant_id)
        .group_by(KBChunkDB.file_id)
        .all()
    )

    # agrega chars por arquivo (soma do tamanho do texto)
    # (para SQLite, length(text) funciona; para outros DBs também costuma funcionar)
    chars_counts = dict(
        db.query(KBChunkDB.file_id, func.coalesce(func.sum(func.length(KBChunkDB.text)), 0))
        .filter(KBChunkDB.tenant_id == current.tenant_id)
        .group_by(KBChunkDB.file_id)
        .all()
    )

    chunks_total = db.query(KBChunkDB).filter(KBChunkDB.tenant_id == current.tenant_id).count()

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
def kb_preview(
    file_id: str,
    limit: int = settings.KB_PREVIEW_LIMIT,
    db: Session = Depends(get_db),
    current: AuthContext = Depends(get_current_user),
):
    rows = (
        db.query(KBChunkDB)
        .filter(KBChunkDB.file_id == file_id, KBChunkDB.tenant_id == current.tenant_id)
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
def _kb_summarize_impl(
    *,
    tenant_id: str,
    mode: str,
    file_id: Optional[str],
    style: str,
    max_sources: int,
    db: Session,
) -> Dict[str, Any]:
    mode = (mode or "all").strip().lower()
    style = (style or "completo").strip().lower()
    max_sources = max(1, int(max_sources or 12))

    if mode == "file":
        if not file_id:
            raise HTTPException(status_code=400, detail="file_id é obrigatório quando mode='file'.")
        rows = (
            db.query(KBChunkDB)
            .filter(KBChunkDB.file_id == file_id, KBChunkDB.tenant_id == tenant_id)
            .order_by(KBChunkDB.id.asc())
            .limit(max_sources)
            .all()
        )
    else:
        rows = (
            db.query(KBChunkDB)
            .filter(KBChunkDB.tenant_id == tenant_id)
            .order_by(KBChunkDB.id.desc())
            .limit(max_sources)
            .all()
        )

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
def kb_summarize(
    payload: SummarizeRequest,
    db: Session = Depends(get_db),
    current: AuthContext = Depends(get_current_user),
):
    return _kb_summarize_impl(
        tenant_id=current.tenant_id,
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
    current: AuthContext = Depends(get_current_user),
):
    # mantém a lógica (mesmo impl), só muda o "contrato" para GET via querystring
    return _kb_summarize_impl(
        tenant_id=current.tenant_id,
        mode=mode,
        file_id=file_id,
        style=style,
        max_sources=max_sources,
        db=db,
    )


# ---- Aliases de compatibilidade para evitar 404 por mudança de rota/prefixo ----
@app.post("/api/kb/summarize")
def kb_summarize_api(
    payload: SummarizeRequest,
    db: Session = Depends(get_db),
    current: AuthContext = Depends(get_current_user),
):
    return kb_summarize(payload, db, current)


@app.get("/api/kb/summarize")
def kb_summarize_api_get(
    mode: str = Query("all"),
    file_id: Optional[str] = Query(None),
    style: str = Query("completo"),
    max_sources: int = Query(12),
    db: Session = Depends(get_db),
    current: AuthContext = Depends(get_current_user),
):
    return kb_summarize_get(
        mode=mode,
        file_id=file_id,
        style=style,
        max_sources=max_sources,
        db=db,
        current=current,
    )


class DocumentSummarizeRequest(BaseModel):
    style: str = "completo"
    max_sources: int = 12


@app.post("/documents/{doc_id}/summarize")
def documents_summarize(
    doc_id: str,
    payload: DocumentSummarizeRequest,
    db: Session = Depends(get_db),
    current: AuthContext = Depends(get_current_user),
):
    return _kb_summarize_impl(
        tenant_id=current.tenant_id,
        mode="file",
        file_id=doc_id,
        style=payload.style or "completo",
        max_sources=payload.max_sources or 12,
        db=db,
    )


@app.post("/api/documents/{doc_id}/summarize")
def documents_summarize_api(
    doc_id: str,
    payload: DocumentSummarizeRequest,
    db: Session = Depends(get_db),
    current: AuthContext = Depends(get_current_user),
):
    return documents_summarize(doc_id, payload, db, current)


# =========================================================
# Chat (NORMAL)
# =========================================================
@app.post("/chat")
def chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    current: AuthContext = Depends(get_current_user),
):
    hint = openai_settings_hint(settings)
    if hint:
        raise HTTPException(status_code=503, detail=hint)

    try:
        trimmed = sanitize_and_trim_messages(payload.messages)

        last_user: Optional[str] = None
        for m in reversed(trimmed):
            if m["role"] == "user":
                last_user = m["content"]
                break

        precision_on = payload.precision is not False

        if precision_on and last_user:
            show_sources = bool(payload.show_sources) or _did_user_request_sources(last_user)
            intent = detect_deterministic_intent(last_user)
            if intent:
                det = _deterministic_reply_from_intent(
                    db=db,
                    current=current,
                    intent=intent,
                    user_text=last_user,
                    explicit_file_id=(payload.file_id or "").strip(),
                )
                if det:
                    reply_text, det_debug, det_sources = det
                    return {
                        "reply": reply_text,
                        "sources": det_sources if show_sources else [],
                        "debug": {**det_debug},
                    }

        # Fast path: list documents without an LLM roundtrip (reduz latência e evita respostas inúteis).
        if _is_list_documents_request(last_user):
            files = list_downloads(db, current.tenant_id)
            return {
                "reply": _format_downloads_list_markdown(files),
                "sources": [],
                "debug": {
                    "received_messages": len(payload.messages),
                    "sent_to_model": 0,
                    "context_used": 0,
                    "model": settings.OPENAI_MODEL,
                    "use_kb": False,
                    "use_downloads": False,
                    "response_mode": _normalize_response_mode(payload.response_mode),
                    "saved_to_db": bool(payload.session_id),
                    "fast_path": "list_downloads",
                    "downloads_count": len(files),
                },
            }

        effective_user_id = current.user_id
        prefs = get_preferences(db, effective_user_id) if effective_user_id else {}
        mode = _normalize_response_mode(payload.response_mode or (prefs.get("response_mode") if isinstance(prefs, dict) else None))

        use_downloads_pref = bool(prefs.get("use_downloads")) if isinstance(prefs, dict) and prefs.get("use_downloads") in (True, False) else False
        use_downloads = _resolve_use_downloads(payload.use_downloads, use_downloads_pref, last_user)
        downloads_top_k = _normalize_downloads_top_k(payload.downloads_top_k)
        use_kb = _should_use_kb(last_user)
        show_sources = bool(payload.show_sources) or _did_user_request_sources(last_user)

        merged_prefs = dict(prefs) if isinstance(prefs, dict) else {}
        merged_prefs["response_mode"] = mode
        if payload.use_downloads in (True, False):
            merged_prefs["use_downloads"] = bool(payload.use_downloads)

        if effective_user_id:
            patch: Dict[str, Any] = {"response_mode": mode}
            if payload.use_downloads in (True, False):
                patch["use_downloads"] = bool(payload.use_downloads)
            upsert_preferences(
                db,
                effective_user_id,
                patch,
            )

        mem_prompt = _memory_system_prompt(merged_prefs)
        sys_prefix: List[Dict[str, Any]] = []
        if mem_prompt:
            sys_prefix.append({"role": "system", "content": mem_prompt})
        sys_prefix.append({"role": "system", "content": _mode_system_prompt(mode)})

        rag_timeout = False
        context_chunks: List[Dict[str, Any]] = []
        final_messages = sys_prefix + trimmed

        def _rag_with_timeout(fn):
            nonlocal rag_timeout
            fut = _TOOLS_EXECUTOR.submit(fn)
            try:
                return fut.result(timeout=8.0)
            except Exception:
                rag_timeout = True
                try:
                    fut.cancel()
                except Exception:
                    pass
                return []

        if use_kb:
            # Document questions: expand context coverage (lexical + semantic).
            tdoc = (last_user or "").lower()
            doc_focus = any(x in tdoc for x in ["fatura", "venc", "cart", "cpf", "valor", "linha", "coluna", "data", "parcel"])
            sem_k = max(12, int(settings.RAG_TOP_K or 6))
            lex_k = 8
            if doc_focus:
                sem_k = max(16, sem_k)
                lex_k = max(12, lex_k)

            sem = _rag_with_timeout(lambda: retrieve_context(db, current.tenant_id, last_user or "", top_k=sem_k))
            lex = _rag_with_timeout(lambda: retrieve_context_lexical(db, current.tenant_id, last_user or "", top_k=lex_k))

            seen_ids = set()
            merged: List[Dict[str, Any]] = []
            for it in (lex or []) + (sem or []):
                cid = it.get("chunk_id")
                key = f"c:{cid}" if cid is not None else f"t:{it.get('file_id')}:{hash(it.get('text') or '')}"
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                merged.append(it)
            context_chunks = merged[: max(12, int(sem_k))]
        elif use_downloads:
            context_chunks = _rag_with_timeout(
                lambda: search_downloads(
                    db,
                    current.tenant_id,
                    last_user or "",
                    top_k=downloads_top_k,
                )
            )

        if context_chunks:
            system_prompt = build_rag_system_prompt(context_chunks)
            final_messages = [{"role": "system", "content": system_prompt}] + sys_prefix + trimmed

        reply = gerar_resposta(
            mensagens=final_messages,
            modelo=settings.OPENAI_MODEL,
            temperatura=_temperature(use_kb),
        )

        if payload.session_id:
            _ensure_session(
                db,
                current.tenant_id,
                current.user_id,
                payload.session_id,
                title=payload.title,
            )
            if last_user:
                _persist_message(db, current.tenant_id, payload.session_id, "user", last_user)
            _persist_message(db, current.tenant_id, payload.session_id, "assistant", reply)

        sources = (
            [{"ref": f"Fonte {i+1}", "filename": c.get("filename")} for i, c in enumerate(context_chunks)]
            if show_sources
            else []
        )

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
                "downloads_top_k": downloads_top_k,
                "response_mode": mode,
                "saved_to_db": bool(payload.session_id),
                "rag_timeout": rag_timeout,
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
def chat_stream(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    current: AuthContext = Depends(get_current_user),
):
    hint = openai_settings_hint(settings)
    if hint:
        raise HTTPException(status_code=503, detail=hint)

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
            show_sources = bool(payload.show_sources) or _did_user_request_sources(last_user)
            precision_on = payload.precision is not False

            if precision_on and last_user:
                intent = detect_deterministic_intent(last_user)
                if intent:
                    det = _deterministic_reply_from_intent(
                        db=db,
                        current=current,
                        intent=intent,
                        user_text=last_user,
                        explicit_file_id=(payload.file_id or "").strip(),
                    )
                    if det:
                        reply_text, _det_debug, det_sources = det
                        yield _sse("status", {"phase": "answer"})
                        yield _sse("delta", {"text": reply_text})
                        if show_sources and det_sources:
                            yield _sse("sources", det_sources)
                        yield _sse("status", {"phase": "done", "message_id": message_id, "deterministic": True})
                        return

            # Fast path: list documents (stream-friendly; avoids planner/tools overhead).
            if _is_list_documents_request(last_user):
                yield _sse("status", {"phase": "answer"})
                files = list_downloads(db, current.tenant_id)
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
            effective_user_id = current.user_id
            user_prefs = get_user_prefs(db, effective_user_id) if effective_user_id else {}
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
                    "needs_rag": _resolve_use_downloads(payload.use_downloads, False, last_user),
                    "needs_export": "none",
                    "query": (last_user or "").strip(),
                    "response_mode": m,
                    "must_cite_sources": False,
                }

            explicit_downloads = payload.use_downloads if payload.use_downloads in (True, False) else None
            pref_downloads_raw = str(user_prefs.get("use_downloads") or "").strip().lower() if isinstance(user_prefs, dict) else ""
            pref_downloads: Optional[bool] = None
            if pref_downloads_raw in ("true", "1", "yes", "sim"):
                pref_downloads = True
            elif pref_downloads_raw in ("false", "0", "no", "nao", "não"):
                pref_downloads = False

            if explicit_downloads is not None:
                plan["needs_rag"] = bool(explicit_downloads)
            elif pref_downloads is not None:
                plan["needs_rag"] = bool(pref_downloads)

            # persist high-signal prefs
            if effective_user_id:
                try:
                    upsert_user_pref(db, effective_user_id, "response_mode", plan.get("response_mode") or "")
                    if explicit_downloads is not None:
                        upsert_user_pref(db, effective_user_id, "use_downloads", "true" if explicit_downloads else "false")
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
                    tenant_id=current.tenant_id,
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
            recall = recall_user_prefs(db, effective_user_id, last_user or "", top_k=4) if effective_user_id else []
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
                sys_msgs.append(
                    {
                        "role": "system",
                        "content": (
                            "Você é um analisador técnico de documentos. Sua única fonte de verdade é o conteúdo em 'Evidências'.\n"
                            "Regras:\n"
                            "- Responda somente com base nas evidências.\n"
                            "- Se não estiver explicitamente nas evidências, responda apenas: 'Essa informação não consta no documento analisado.'\n"
                            "- Proibido sugerir verificar app/banco/outra fonte.\n"
                            "- Proibido usar linguagem genérica (ex.: 'Recomendo consultar...').\n"
                            "- Para valores/datas/números/parcelas: devolva exatamente como está no documento."
                        ),
                    }
                )
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
                _ensure_session(
                    db,
                    current.tenant_id,
                    current.user_id,
                    payload.session_id,
                    title=payload.title,
                )
                if last_user:
                    _persist_message(db, current.tenant_id, payload.session_id, "user", last_user)
                if full:
                    _persist_message(db, current.tenant_id, payload.session_id, "assistant", full)

            if show_sources and sources:
                yield _sse("sources", sources)
            for a in artifacts:
                yield _sse("artifact", a)

            yield _sse("status", {"phase": "done", "message_id": message_id})

        except (GeneratorExit, asyncio.CancelledError):
            # Client disconnected / request cancelled; stop quietly (no further yields).
            return
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

class KBComputeRequest(BaseModel):
    file_id: str
    op: str
    arg: Optional[str] = ""
    flags: Dict[str, Any] = {}


def user_requested_sources(text: Optional[str]) -> bool:
    return _did_user_request_sources(text)


def detect_deterministic_intent(text: str) -> Dict[str, Any] | None:
    """
    Returns:
      { action: count|stats|extract|list, target: ..., file_hint?: str }
    """
    t = (text or "").strip()
    low = t.lower()
    if not low:
        return None

    # file hints
    file_hint = ""
    mfile = re.search(r"([\w\-\.\s]+?\.(pdf|csv|xlsx|txt))", t, flags=re.IGNORECASE)
    if mfile:
        file_hint = (mfile.group(1) or "").strip()
    mid = re.search(r"\b([a-f0-9]{32})\b", low)
    if mid and not file_hint:
        file_hint = mid.group(1)

    def out(action: str, target: str) -> Dict[str, Any]:
        d: Dict[str, Any] = {"action": action, "target": target}
        if file_hint:
            d["file_hint"] = file_hint
        return d

    if re.search(r"\b(quantos?|quantas?|contar|contagem|total\b|total de|qual o total|n[uú]mero de|quantidade de)\b", low):
        if "interroga" in low or "?" in low:
            return out("count", "punctuation")
        if "exclama" in low or "!" in low:
            return out("count", "punctuation")
        if "caracter" in low or "chars" in low:
            return out("count", "chars")
        if "palavr" in low:
            return out("count", "words")
        if "linhas" in low or "linha" in low:
            return out("stats", "lines")
        if "colunas" in low or "coluna" in low:
            return out("stats", "columns")
        if "alunos" in low or "aluno" in low or "pessoas" in low or "pessoa" in low:
            return out("count", "records")
        return out("count", "all")

    if re.search(r"\b(linhas e colunas|quantas linhas|quantas colunas|colunas tem|linhas tem)\b", low):
        return out("stats", "table")

    if re.search(r"\b(valor exato|cpf exato|data exata|nome exato)\b", low):
        return out("extract", "field")

    if re.search(r"\b(liste todos|liste todas|listar todos|listar todas)\b", low):
        return out("list", "all")

    return None


def _map_deterministic_to_compute(text: str) -> tuple[str, str, Dict[str, Any]] | None:
    t = (text or "").strip().lower()
    if not t:
        return None
    if "interroga" in t and ("quantos" in t or "conte" in t):
        return "count_char", "?", {}
    if "exclama" in t and ("quantos" in t or "conte" in t):
        return "count_char", "!", {}

    m = re.search(r"quantos?\s+['\"]([^'\"]+)['\"]", t)
    if m:
        return "count_regex", m.group(1), {"case_insensitive": True}

    # csv filter heuristic: "STATUS=Pago"
    m2 = re.search(r"\b([a-zA-Z0-9_À-ÿ]+)\s*=\s*([a-zA-Z0-9_À-ÿ -]+)", text or "")
    if m2 and ("csv" in t or "xlsx" in t or "planilha" in t or "coluna" in t or "filtr" in t):
        col = str(m2.group(1)).strip()
        val = str(m2.group(2)).strip()
        return "csv_filter", json.dumps({"column": col, "value": val}, ensure_ascii=False), {"case_insensitive": True}

    # generic regex count: "ocorrências de X"
    m3 = re.search(r"ocorr[eê]ncias?\s+de\s+(.+)$", t)
    if m3:
        arg = (m3.group(1) or "").strip().strip(".")
        if arg:
            return "count_regex", arg, {"case_insensitive": True}
    return None


def _pick_best_file_id(*, lex: List[Dict[str, Any]], sem: List[Dict[str, Any]]) -> str:
    """
    Choose the most likely file_id from retrieval results without relying on raw score scales.
    Uses rank-based aggregation to avoid lexical vs semantic score mismatch.
    """
    points: Dict[str, float] = {}

    def add_ranked(lst: List[Dict[str, Any]], weight: float):
        n = len(lst)
        for idx, it in enumerate(lst):
            fid = str(it.get("file_id") or "").strip()
            if not fid:
                continue
            points[fid] = points.get(fid, 0.0) + (float(max(0, n - idx)) * float(weight))

    add_ranked(lex or [], 1.0)
    add_ranked(sem or [], 0.8)
    if not points:
        return ""
    return sorted(points.items(), key=lambda kv: kv[1], reverse=True)[0][0]


def _deterministic_reply_from_intent(
    *,
    db: Session,
    current: AuthContext,
    intent: Dict[str, Any],
    user_text: str,
    explicit_file_id: str,
) -> tuple[str, Dict[str, Any], List[Dict[str, Any]]] | None:
    action = str(intent.get("action") or "")
    target = str(intent.get("target") or "")
    file_hint = str(intent.get("file_hint") or "").strip()

    chosen_id = (explicit_file_id or "").strip()
    if not chosen_id and file_hint:
        hit = find_file_by_hint(db, current.tenant_id, file_hint)
        if hit:
            chosen_id = hit[1]
    if not chosen_id:
        # pick best candidate via hybrid retrieval
        sem = retrieve_context(db, current.tenant_id, user_text, top_k=10)
        lex = retrieve_context_lexical(db, current.tenant_id, user_text, top_k=10)
        chosen_id = str(_pick_best_file_id(lex=lex, sem=sem) or "").strip()
    if not chosen_id:
        return None

    kb_file, full_text = load_full_text_for_kb_file(db, current.tenant_id, chosen_id)
    dl_file = None
    if not kb_file:
        dl_file, full_text = load_full_text_for_download(db, current.tenant_id, chosen_id)
    fobj = kb_file or dl_file
    if not fobj:
        return None

    filename = str(getattr(fobj, "filename", "") or "arquivo")
    ext = str(getattr(fobj, "ext", "") or "").lower()

    debug = {"deterministic": True, "action": action, "target": target, "file_id": chosen_id, "filename": filename}
    sources = [{"ref": "Fonte 1", "filename": filename, "file_id": chosen_id}]

    # stats for tables
    if action == "stats" or target in ("lines", "columns", "table"):
        if ext in ("csv", "xlsx"):
            rows = int(getattr(fobj, "rows", 0) or 0)
            cols = int(getattr(fobj, "cols", 0) or 0)
            try:
                cols_list = json.loads(getattr(fobj, "columns_json", "") or "[]")
                if not isinstance(cols_list, list):
                    cols_list = []
            except Exception:
                cols_list = []
            if not rows or not cols:
                stats = compute_table_stats(stored_path=getattr(fobj, "stored_path", ""), ext=ext)
                rows = int(stats.get("rows") or 0)
                cols = int(stats.get("cols") or 0)
                cols_list = list(stats.get("column_names") or [])
                try:
                    setattr(fobj, "rows", rows)
                    setattr(fobj, "cols", cols)
                    setattr(fobj, "columns_json", json.dumps([str(c) for c in cols_list], ensure_ascii=False))
                    db.commit()
                except Exception:
                    pass
            return (f"Linhas: {rows}\nColunas: {cols}\nColunas: {', '.join([str(c) for c in cols_list])}", debug, sources)
        # non-table docs: lines only
        lines = (full_text or "").splitlines()
        return (f"Linhas: {len(lines)}", debug, sources)

    if action == "count":
        s = full_text or ""
        if target == "punctuation":
            q = s.count("?")
            e = s.count("!")
            d = s.count(".")
            c = s.count(",")
            # if asked specifically "?"
            if "?" in user_text or "interroga" in user_text.lower():
                return (str(q), debug, sources)
            if "!" in user_text or "exclama" in user_text.lower():
                return (str(e), debug, sources)
            return (f"?: {q}\n!: {e}\n.: {d}\n,: {c}", debug, sources)
        if target == "chars":
            return (str(len(s)), debug, sources)
        if target == "words":
            words = re.findall(r"[\wÀ-ÿ]+", s, flags=re.UNICODE)
            return (str(len(words)), debug, sources)
        if target == "records":
            if ext in ("csv", "xlsx"):
                rows = int(getattr(fobj, "rows", 0) or 0)
                if not rows:
                    stats = compute_table_stats(stored_path=getattr(fobj, "stored_path", ""), ext=ext)
                    rows = int(stats.get("rows") or 0)
                    try:
                        setattr(fobj, "rows", rows)
                        db.commit()
                    except Exception:
                        pass
                return (str(rows), debug, sources)
            low = s.lower()
            patterns = [
                r"\baluno\s*:",
                r"\bnome\s*:",
                r"^\s*\d+\s*[-.)]\s+",
            ]
            counts = []
            for p in patterns:
                try:
                    counts.append(len(re.findall(p, low, flags=re.IGNORECASE | re.MULTILINE)))
                except Exception:
                    pass
            best = max(counts) if counts else 0
            if best > 0:
                return (str(best), debug, sources)
            return (
                "Não há um marcador consistente para contar automaticamente; posso contar por um padrão (ex.: 'Aluno:' ou 'Nome:') se você confirmar.",
                debug,
                sources,
            )
        # fallback: count occurrences of a quoted token if present
        m = re.search(r"['\"]([^'\"]+)['\"]", user_text or "")
        if m:
            token = m.group(1)
            r = compute_on_text(text=s, op="count_regex", arg=re.escape(token), flags={"case_insensitive": True})
            return (str(r.result), debug, sources)
        return (str(len(s)), debug, sources)

    if action == "extract":
        lowq = (user_text or "").lower()
        s = full_text or ""
        def _line_for_match(span_start: int, span_end: int) -> str:
            if span_start < 0 or span_end < 0:
                return ""
            a = s.rfind("\n", 0, span_start)
            b = s.find("\n", span_end)
            if a < 0:
                a = 0
            else:
                a += 1
            if b < 0:
                b = len(s)
            return (s[a:b] or "").strip()
        if "cpf" in lowq:
            m = re.search(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b|\b\d{11}\b", s)
            if m:
                line = _line_for_match(m.start(), m.end())
                return (line or m.group(0), debug, sources)
        if "data" in lowq or "venc" in lowq:
            m = re.search(r"\b\d{2}/\d{2}/\d{4}\b|\b\d{4}-\d{2}-\d{2}\b", s)
            if m:
                line = _line_for_match(m.start(), m.end())
                return (line or m.group(0), debug, sources)
        if "valor" in lowq or "r$" in lowq:
            m = re.search(r"R\$\s*\d{1,3}(?:\.\d{3})*,\d{2}", s)
            if m:
                line = _line_for_match(m.start(), m.end())
                return (line or m.group(0), debug, sources)
        return ("Essa informação não consta no documento analisado.", debug, sources)

    if action == "list":
        # Minimal: list lines that contain a term after "liste todos os ..."
        m = re.search(r"liste (?:todos|todas) os?\s+(.+)$", (user_text or "").strip(), flags=re.IGNORECASE)
        term = (m.group(1) if m else "").strip()
        if term:
            r = compute_on_text(text=full_text or "", op="extract_lines", arg=term, flags={"case_insensitive": True, "max_lines": 200})
            lines = r.result if isinstance(r.result, list) else []
            if lines:
                return ("\n".join([str(x) for x in lines]), debug, sources)
        return ("Essa informação não consta no documento analisado.", debug, sources)

    return None


@app.post("/kb/compute")
def kb_compute(
    payload: KBComputeRequest,
    db: Session = Depends(get_db),
    current: AuthContext = Depends(get_current_user),
):
    fid = (payload.file_id or "").strip()
    op = (payload.op or "").strip()
    arg = payload.arg or ""
    flags = payload.flags or {}

    # Try KB file first, then Downloads library (ids can overlap in theory; we prefer KB).
    kb_file, text = load_full_text_for_kb_file(db, current.tenant_id, fid)
    if kb_file:
        if op == "csv_filter":
            r = compute_csv_filter(stored_path=kb_file.stored_path or "", ext=kb_file.ext or "", arg=arg, flags=flags)
        else:
            r = compute_on_text(text=text, op=op, arg=arg, flags=flags)
        if not r.ok:
            raise HTTPException(status_code=400, detail=r.meta.get("error") or "compute falhou")
        return {
            "ok": True,
            "file": {"id": kb_file.file_id, "filename": kb_file.filename, "ext": kb_file.ext},
            "op": op,
            "result": r.result,
            "meta": r.meta,
        }

    dl_file, dl_text = load_full_text_for_download(db, current.tenant_id, fid)
    if dl_file:
        if op == "csv_filter":
            r = compute_csv_filter(stored_path=dl_file.stored_path or "", ext=dl_file.ext or "", arg=arg, flags=flags)
        else:
            r = compute_on_text(text=dl_text, op=op, arg=arg, flags=flags)
        if not r.ok:
            raise HTTPException(status_code=400, detail=r.meta.get("error") or "compute falhou")
        return {
            "ok": True,
            "file": {"id": dl_file.id, "filename": dl_file.filename, "ext": dl_file.ext},
            "op": op,
            "result": r.result,
            "meta": r.meta,
        }

    raise HTTPException(status_code=404, detail="Arquivo não encontrado.")


@app.post("/api/kb/compute")
def kb_compute_api(
    payload: KBComputeRequest,
    db: Session = Depends(get_db),
    current: AuthContext = Depends(get_current_user),
):
    return kb_compute(payload, db, current)

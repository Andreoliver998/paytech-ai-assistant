from __future__ import annotations

import io
import re
from datetime import datetime
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import MessageDB, SessionDB
from ..services.export_service import render_conversation_docx_bytes, render_conversation_pdf_bytes


router = APIRouter(prefix="/export", tags=["export"])


class ConversationMessage(BaseModel):
    role: str
    content: str = ""
    ts: Optional[str] = None


class ConversationModel(BaseModel):
    id: str
    title: str = "Conversa"
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None
    messages: List[ConversationMessage] = []


class ConversationExportRequest(BaseModel):
    conversationId: Optional[str] = None
    conversation: Optional[ConversationModel] = None


def _load_conversation_from_db(db: Session, conversation_id: str) -> ConversationModel:
    sid = (conversation_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="conversationId inválido.")

    s = db.get(SessionDB, sid)
    if not s:
        raise HTTPException(status_code=404, detail="Conversa não encontrada.")

    msgs = (
        db.query(MessageDB)
        .filter(MessageDB.session_id == sid)
        .order_by(MessageDB.createdAt.asc(), MessageDB.id.asc())
        .all()
    )
    return ConversationModel(
        id=s.id,
        title=s.title or "Conversa",
        createdAt=s.createdAt.isoformat(timespec="seconds") if s.createdAt else None,
        updatedAt=s.updatedAt.isoformat(timespec="seconds") if s.updatedAt else None,
        messages=[
            ConversationMessage(
                role=m.role,
                content=m.content or "",
                ts=m.createdAt.isoformat(timespec="seconds") if m.createdAt else None,
            )
            for m in msgs
        ],
    )


def _slugify(s: str) -> str:
    t = (s or "").strip().lower()
    t = re.sub(r"[^\w\s-]+", "", t, flags=re.UNICODE)
    t = re.sub(r"[\s_-]+", "-", t).strip("-")
    return t or "conversa"


def _export_filename(conv: ConversationModel, ext: str) -> str:
    slug = _slugify(conv.title or "conversa")
    ts = datetime.now().strftime("%Y%m%d-%H%M")
    return f"conversa-{slug}-{ts}.{ext}"


@router.post("/conversation/docx")
def export_conversation_docx(payload: ConversationExportRequest, db: Session = Depends(get_db)):
    conv = payload.conversation or (_load_conversation_from_db(db, payload.conversationId or "") if payload.conversationId else None)
    if conv is None:
        raise HTTPException(status_code=400, detail="Envie `conversationId` ou `conversation`.")
    try:
        data = render_conversation_docx_bytes(conv.model_dump())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha ao exportar DOCX: {e}")

    filename = _export_filename(conv, "docx")
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _split_fenced_code(text: str) -> List[Tuple[str, str]]:
    """
    Returns list of (kind, content) where kind is 'text' or 'code'.
    Preserves code content exactly between ``` fences.
    """
    s = text or ""
    parts: List[Tuple[str, str]] = []
    i = 0
    while True:
        start = s.find("```", i)
        if start == -1:
            tail = s[i:]
            if tail:
                parts.append(("text", tail))
            break
        if start > i:
            parts.append(("text", s[i:start]))
        end = s.find("```", start + 3)
        if end == -1:
            # no closing fence -> treat rest as text (rule: don't invent)
            parts.append(("text", s[start:]))
            break
        code_block = s[start + 3 : end]
        # drop optional language first line, keep rest
        if "\n" in code_block:
            first, rest = code_block.split("\n", 1)
            if re.match(r"^[a-zA-Z0-9_+-]{1,20}$", first.strip()):
                code_block = rest
        parts.append(("code", code_block))
        i = end + 3
    return parts


@router.post("/conversation/pdf")
def export_conversation_pdf(payload: ConversationExportRequest, db: Session = Depends(get_db)):
    conv = payload.conversation or (_load_conversation_from_db(db, payload.conversationId or "") if payload.conversationId else None)
    if conv is None:
        raise HTTPException(status_code=400, detail="Envie `conversationId` ou `conversation`.")
    try:
        data = render_conversation_pdf_bytes(conv.model_dump())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha ao exportar PDF: {e}")

    filename = _export_filename(conv, "pdf")
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

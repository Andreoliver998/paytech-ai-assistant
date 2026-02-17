from __future__ import annotations

import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import DownloadFileDB
from ..services.downloads_service import (
    ALLOWED_EXTS,
    delete_download,
    extract_text_by_ext,
    index_download_file,
    list_downloads,
    safe_filename,
    search_downloads,
)
from ..utils.files import DOWNLOADS_DIR


router = APIRouter(prefix="/downloads", tags=["downloads"])


class SearchRequest(BaseModel):
    query: str
    top_k: int = 6


@router.post("/upload")
async def downloads_upload(
    db: Session = Depends(get_db),
    file: UploadFile | None = File(None),
    files: List[UploadFile] | None = File(None),
):
    incoming: List[UploadFile] = []
    if file is not None:
        incoming.append(file)
    if files:
        incoming.extend([f for f in files if f is not None])

    if not incoming:
        raise HTTPException(status_code=400, detail="Envie ao menos 1 arquivo.")

    results = []
    for up in incoming:
        filename = safe_filename(up.filename or "arquivo")
        ext = (Path(filename).suffix or "").lower().strip(".")
        if ext not in ALLOWED_EXTS:
            raise HTTPException(status_code=400, detail="Formato não suportado. Use PDF, XLSX, CSV ou TXT.")

        doc_id = uuid.uuid4().hex
        stored_path = Path(DOWNLOADS_DIR) / f"{doc_id}.{ext}"

        content = await up.read()
        stored_path.write_bytes(content)

        try:
            text = extract_text_by_ext(stored_path, ext)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Falha ao extrair texto: {e}")

        if not (text or "").strip():
            raise HTTPException(status_code=400, detail="Não consegui extrair conteúdo do arquivo.")

        try:
            chunks_added = index_download_file(
                db=db,
                file_id=doc_id,
                filename=filename,
                ext=ext,
                stored_path=stored_path,
                full_text=text,
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Falha ao indexar arquivo: {e}")

        results.append({"id": doc_id, "filename": filename, "ext": ext, "chunks_added": chunks_added})

    return {"ok": True, "items": results}


@router.get("")
@router.get("/")
def downloads_list(db: Session = Depends(get_db)):
    return {"files": list_downloads(db)}


@router.get("/{file_id}")
def downloads_get(file_id: str, db: Session = Depends(get_db)):
    f = db.get(DownloadFileDB, (file_id or "").strip())
    if not f:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")

    p = Path(f.stored_path or "")
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado no disco.")

    filename = (f.filename or p.name or "arquivo").strip() or "arquivo"
    ext = (f.ext or p.suffix.replace(".", "")).lower()
    media_type = {
        "pdf": "application/pdf",
        "csv": "text/csv",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "txt": "text/plain",
    }.get(ext, "application/octet-stream")

    return FileResponse(path=str(p), media_type=media_type, filename=filename)


@router.delete("/{file_id}")
def downloads_delete(file_id: str, db: Session = Depends(get_db)):
    f = delete_download(db, file_id)
    if not f:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")

    try:
        p = Path(f.stored_path or "")
        if p.exists() and p.is_file():
            p.unlink()
    except Exception:
        pass

    return {"ok": True}


@router.post("/search")
def downloads_search(payload: SearchRequest, db: Session = Depends(get_db)):
    items = search_downloads(db, payload.query, payload.top_k)
    return {"items": items}

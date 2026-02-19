from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy.orm import Session

from ..models import DownloadFileDB, FileDB, KBChunkDB
from .rag_service import extract_csv_text, extract_pdf_text, extract_xlsx_text


@dataclass
class ComputeResult:
    ok: bool
    op: str
    file_id: str
    filename: str
    result: Any
    meta: Dict[str, Any]


def _read_full_text_from_path(path: str) -> str:
    p = Path(str(path or "").strip())
    if not p.exists() or not p.is_file():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def _extract_text_from_stored_file(stored_path: str, ext: str) -> str:
    p = Path(str(stored_path or "").strip())
    if not p.exists() or not p.is_file():
        return ""
    e = (ext or p.suffix.replace(".", "")).lower().strip(".")
    if e == "pdf":
        return extract_pdf_text(p)
    if e == "csv":
        return extract_csv_text(p)
    if e == "xlsx":
        return extract_xlsx_text(p)
    if e == "txt":
        return p.read_text(encoding="utf-8", errors="replace")
    return p.read_text(encoding="utf-8", errors="replace")


def load_full_text_for_kb_file(db: Session, tenant_id: str, file_id: str) -> Tuple[Optional[FileDB], str]:
    f = db.query(FileDB).filter(FileDB.file_id == file_id, FileDB.tenant_id == tenant_id).first()
    if not f:
        return None, ""
    text = _read_full_text_from_path(f.full_text_path or "")
    if not text:
        # Fallback 1: re-extract from original stored file.
        text = _extract_text_from_stored_file(f.stored_path or "", f.ext or "")
    if not text:
        # Fallback 2: reconstruct by concatenating chunks (ordered).
        rows = (
            db.query(KBChunkDB)
            .filter(KBChunkDB.tenant_id == tenant_id, KBChunkDB.file_id == file_id)
            .order_by(KBChunkDB.id.asc())
            .all()
        )
        text = "\n\n".join([(r.text or "") for r in rows]).strip()
    return f, text or ""


def find_file_by_hint(db: Session, tenant_id: str, hint: str) -> Tuple[str, str] | None:
    """
    Returns (kind, file_id) where kind in {"kb","downloads"}.
    """
    h = (hint or "").strip().lower()
    if not h:
        return None
    # direct ids
    if re.fullmatch(r"[a-f0-9]{32}", h):
        if db.query(FileDB).filter(FileDB.tenant_id == tenant_id, FileDB.file_id == h).first():
            return ("kb", h)
        if db.query(DownloadFileDB).filter(DownloadFileDB.tenant_id == tenant_id, DownloadFileDB.id == h).first():
            return ("downloads", h)
    # filename contains
    row = (
        db.query(FileDB)
        .filter(FileDB.tenant_id == tenant_id)
        .filter(FileDB.filename.ilike(f"%{h}%"))
        .order_by(FileDB.createdAt.desc())
        .first()
    )
    if row:
        return ("kb", str(row.file_id))
    row2 = (
        db.query(DownloadFileDB)
        .filter(DownloadFileDB.tenant_id == tenant_id)
        .filter(DownloadFileDB.filename.ilike(f"%{h}%"))
        .order_by(DownloadFileDB.createdAt.desc())
        .first()
    )
    if row2:
        return ("downloads", str(row2.id))
    return None


def load_full_text_for_download(db: Session, tenant_id: str, file_id: str) -> Tuple[Optional[DownloadFileDB], str]:
    f = db.query(DownloadFileDB).filter(DownloadFileDB.id == file_id, DownloadFileDB.tenant_id == tenant_id).first()
    if not f:
        return None, ""
    text = _read_full_text_from_path(f.full_text_path or "")
    if not text:
        text = _extract_text_from_stored_file(f.stored_path or "", f.ext or "")
    return f, text or ""


def _rx_flags(flags: Dict[str, Any]) -> int:
    fl = 0
    if bool(flags.get("case_insensitive")):
        fl |= re.IGNORECASE
    if bool(flags.get("multiline")):
        fl |= re.MULTILINE
    if bool(flags.get("dotall")):
        fl |= re.DOTALL
    return fl


def compute_on_text(*, text: str, op: str, arg: str, flags: Dict[str, Any]) -> ComputeResult:
    opn = (op or "").strip()
    a = "" if arg is None else str(arg)
    s = text or ""
    meta: Dict[str, Any] = {"len": len(s)}

    if opn == "count_char":
        ch = a[:1]
        return ComputeResult(True, opn, "", "", s.count(ch) if ch else 0, meta)

    if opn == "count_regex":
        rx = re.compile(a, _rx_flags(flags))
        return ComputeResult(True, opn, "", "", len(rx.findall(s)), meta)

    if opn == "find_all":
        rx_mode = bool(flags.get("regex"))
        max_hits = int(flags.get("max_hits") or 50)
        ctx = int(flags.get("context") or 80)
        out: List[Dict[str, Any]] = []
        if rx_mode:
            rx = re.compile(a, _rx_flags(flags))
            for m in rx.finditer(s):
                if len(out) >= max_hits:
                    break
                start = max(0, m.start() - ctx)
                end = min(len(s), m.end() + ctx)
                out.append({"start": m.start(), "end": m.end(), "match": m.group(0), "context": s[start:end]})
        else:
            needle = a
            hay = s if not bool(flags.get("case_insensitive")) else s.lower()
            ndl = needle if not bool(flags.get("case_insensitive")) else needle.lower()
            i = 0
            while ndl and i < len(hay) and len(out) < max_hits:
                j = hay.find(ndl, i)
                if j < 0:
                    break
                start = max(0, j - ctx)
                end = min(len(s), j + len(needle) + ctx)
                out.append({"start": j, "end": j + len(needle), "match": s[j : j + len(needle)], "context": s[start:end]})
                i = j + max(1, len(ndl))
        return ComputeResult(True, opn, "", "", out, meta)

    if opn == "extract_lines":
        term = a
        ci = bool(flags.get("case_insensitive"))
        max_lines = int(flags.get("max_lines") or 200)
        lines = s.splitlines()
        out_lines: List[str] = []
        t = term.lower() if ci else term
        for line in lines:
            l = line.lower() if ci else line
            if t and t in l:
                out_lines.append(line)
                if len(out_lines) >= max_lines:
                    break
        return ComputeResult(True, opn, "", "", out_lines, {**meta, "lines": len(lines)})

    return ComputeResult(False, opn, "", "", None, {"error": "op não suportada"})


def compute_csv_filter(*, stored_path: str, ext: str, arg: str, flags: Dict[str, Any]) -> ComputeResult:
    p = Path(str(stored_path or "").strip())
    if not p.exists() or not p.is_file():
        return ComputeResult(False, "csv_filter", "", "", None, {"error": "arquivo não encontrado"})

    payload: Dict[str, Any] = {}
    try:
        payload = json.loads(arg or "{}") if isinstance(arg, str) else {}
    except Exception:
        payload = {}

    col = str(payload.get("column") or payload.get("col") or "").strip()
    val = payload.get("value")
    if not col:
        return ComputeResult(False, "csv_filter", "", "", None, {"error": "arg precisa de column"})

    try:
        if (ext or "").lower() == "xlsx":
            sheet = payload.get("sheet")
            df = pd.read_excel(p, sheet_name=sheet) if sheet else pd.read_excel(p)
        else:
            df = pd.read_csv(p)
    except Exception as e:
        return ComputeResult(False, "csv_filter", "", "", None, {"error": f"falha ao ler tabela: {e}"})

    if col not in df.columns:
        return ComputeResult(False, "csv_filter", "", "", None, {"error": f"coluna '{col}' não existe"})

    ci = bool(flags.get("case_insensitive"))
    if ci and isinstance(val, str):
        mask = df[col].astype(str).str.lower() == str(val).lower()
    else:
        mask = df[col].astype(str) == ("" if val is None else str(val))
    out = df[mask]
    max_rows = int(flags.get("max_rows") or 200)
    rows = out.head(max_rows).to_dict(orient="records")
    return ComputeResult(True, "csv_filter", "", "", rows, {"rows": int(out.shape[0]), "returned": len(rows), "columns": list(df.columns)})


def compute_table_stats(*, stored_path: str, ext: str) -> Dict[str, Any]:
    """
    Deterministic stats for CSV/XLSX (rows, cols, column_names).
    Best-effort: returns zeros if file can't be read.
    """
    p = Path(str(stored_path or "").strip())
    if not p.exists() or not p.is_file():
        return {"rows": 0, "cols": 0, "column_names": []}
    try:
        e = (ext or p.suffix.replace(".", "")).lower().strip(".")
        if e == "xlsx":
            df = pd.read_excel(p)
        else:
            df = pd.read_csv(p)
        cols = [str(c) for c in list(df.columns)]
        return {"rows": int(df.shape[0]), "cols": int(df.shape[1]), "column_names": cols}
    except Exception:
        return {"rows": 0, "cols": 0, "column_names": []}

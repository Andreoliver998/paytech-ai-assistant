from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from .rag_search import rag_search_downloads
from .export_service import render_conversation_docx_bytes, render_conversation_pdf_bytes


@dataclass
class ToolResult:
    sources: List[Dict[str, Any]]
    artifacts: List[Dict[str, Any]]


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def run_tools(
    *,
    db: Session,
    tenant_id: str,
    plan: Dict[str, Any],
    exports_dir: Path,
    conversation: Dict[str, Any],
) -> ToolResult:
    sources: List[Dict[str, Any]] = []
    artifacts: List[Dict[str, Any]] = []

    if plan.get("needs_rag"):
        q = str(plan.get("query") or "").strip()
        sources = rag_search_downloads(db, tenant_id, q, top_k=6)

    needs_export = str(plan.get("needs_export") or "none").lower()
    if needs_export and needs_export != "none":
        _ensure_dir(exports_dir)
        base = f"artifact-{uuid.uuid4().hex}"

        if needs_export in ("docx", "both"):
            data = render_conversation_docx_bytes(conversation)
            name = f"{base}.docx"
            (exports_dir / name).write_bytes(data)
            artifacts.append({"type": "docx", "name": name, "url": f"/exports/{name}"})

        if needs_export in ("pdf", "both"):
            data = render_conversation_pdf_bytes(conversation)
            name = f"{base}.pdf"
            (exports_dir / name).write_bytes(data)
            artifacts.append({"type": "pdf", "name": name, "url": f"/exports/{name}"})

    return ToolResult(sources=sources, artifacts=artifacts)

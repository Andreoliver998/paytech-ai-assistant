from __future__ import annotations

import concurrent.futures
import json
import re
from typing import Any, Dict, List, Optional

from .openai_service import gerar_resposta
from ..settings import settings


DEFAULT_PLAN: Dict[str, Any] = {
    "needs_rag": False,
    "needs_export": "none",  # none|pdf|docx|both
    "query": "",
    "response_mode": "normal",  # normal|didatico|executivo|tecnico
    "must_cite_sources": False,
}

PLANNER_TIMEOUT_S = 4.0


def _heuristic_plan(user_message: str) -> Dict[str, Any]:
    t = (user_message or "").lower()
    needs_rag = any(k in t for k in ["documento", "pdf", "csv", "xlsx", "planilha", "comprovante", "anexo", "downloads"])
    needs_export = "none"
    if any(k in t for k in ["baixar", "exportar", "gerar pdf", "pdf", "docx", "word"]):
        wants_pdf = "pdf" in t
        wants_docx = "docx" in t or "word" in t
        if wants_pdf and wants_docx:
            needs_export = "both"
        elif wants_pdf:
            needs_export = "pdf"
        elif wants_docx:
            needs_export = "docx"

    must_cite = bool(needs_rag) and any(k in t for k in ["fonte", "fontes", "citar", "cite", "evidência", "evidencias"])

    mode = "normal"
    if any(k in t for k in ["técnico", "tecnico"]):
        mode = "tecnico"
    elif any(k in t for k in ["executivo", "executiva"]):
        mode = "executivo"
    elif any(k in t for k in ["didático", "didatico", "explica"]):
        mode = "didatico"

    query = user_message.strip()

    return {
        "needs_rag": bool(needs_rag),
        "needs_export": needs_export,
        "query": query,
        "response_mode": mode,
        "must_cite_sources": bool(must_cite),
    }


def _safe_json_extract(text: str) -> Optional[Dict[str, Any]]:
    s = (text or "").strip()
    if not s:
        return None
    # Try direct json first
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else None
    except Exception:
        pass

    # Try fenced code block ```json ... ```
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, flags=re.S | re.I)
    if m:
        try:
            v = json.loads(m.group(1))
            return v if isinstance(v, dict) else None
        except Exception:
            return None

    # Try first {...} span
    m2 = re.search(r"(\{.*\})", s, flags=re.S)
    if m2:
        try:
            v = json.loads(m2.group(1))
            return v if isinstance(v, dict) else None
        except Exception:
            return None
    return None


def plan_next_action(
    *,
    user_message: str,
    thread_context: str,
    user_prefs: Dict[str, str],
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Planner returns a strict JSON plan. If LLM fails, fall back to heuristic.
    """
    # Fast path: for most short/normal messages, the heuristic is enough and avoids an extra
    # blocking LLM call before we can start streaming (UX: "chat não responde").
    heuristic = _heuristic_plan(user_message)
    if (
        not heuristic.get("needs_rag")
        and str(heuristic.get("needs_export") or "none") == "none"
        and not heuristic.get("must_cite_sources")
        and (user_message or "").strip()
    ):
        return heuristic

    prefs_lines: List[str] = []
    for k in ("response_mode", "use_downloads"):
        if k in user_prefs:
            prefs_lines.append(f"- {k}: {user_prefs.get(k)}")
    prefs = "\n".join(prefs_lines) if prefs_lines else "(sem prefs)"

    prompt = (
        "Você é um planner para um sistema de chat com ferramentas (RAG e export).\n"
        "Retorne APENAS um JSON válido (sem comentários, sem markdown) com este schema:\n"
        "{\n"
        '  "needs_rag": true|false,\n'
        '  "needs_export": "none|pdf|docx|both",\n'
        '  "query": "string",\n'
        '  "response_mode": "normal|didatico|executivo|tecnico",\n'
        '  "must_cite_sources": true|false\n'
        "}\n\n"
        f"Preferências do usuário:\n{prefs}\n\n"
        f"Contexto do thread (resumo):\n{(thread_context or '')[:1200]}\n\n"
        f"Mensagem do usuário:\n{user_message}\n"
    )

    def _call_planner_llm() -> str:
        return gerar_resposta(
            mensagens=[
                {"role": "system", "content": "Você retorna JSON estrito."},
                {"role": "user", "content": prompt},
            ],
            modelo=model or settings.OPENAI_MODEL,
            temperatura=0.0,
        )

    try:
        # Avoid blocking the whole stream if the planner call stalls.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_call_planner_llm)
            out = fut.result(timeout=PLANNER_TIMEOUT_S)
        parsed = _safe_json_extract(out)
        if not parsed:
            return heuristic

        plan = {**DEFAULT_PLAN, **parsed}
        # normalize
        plan["needs_rag"] = bool(plan.get("needs_rag"))
        plan["must_cite_sources"] = bool(plan.get("must_cite_sources"))
        plan["needs_export"] = str(plan.get("needs_export") or "none").lower()
        if plan["needs_export"] not in ("none", "pdf", "docx", "both"):
            plan["needs_export"] = "none"
        plan["response_mode"] = str(plan.get("response_mode") or "normal").lower()
        if plan["response_mode"] not in ("normal", "didatico", "executivo", "tecnico"):
            plan["response_mode"] = "normal"
        plan["query"] = str(plan.get("query") or "").strip()
        if plan["needs_rag"] and not plan["query"]:
            plan["query"] = user_message.strip()
        return plan
    except Exception:
        return heuristic

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def verify_and_fix(
    *,
    plan: Dict[str, Any],
    answer_text: str,
    sources: List[Dict[str, Any]],
) -> Tuple[str, List[str]]:
    """
    Returns (final_answer, warnings).
    Keeps it lightweight: fixes empty answers and citation requirements.
    """
    warnings: List[str] = []
    text = (answer_text or "").strip()

    if not text:
        warnings.append("empty_answer")
        text = "Não consegui gerar uma resposta agora. Pode repetir a pergunta com um pouco mais de contexto?"

    must_cite = bool(plan.get("must_cite_sources"))
    if must_cite and not sources:
        warnings.append("missing_sources")
        text += "\n\nFontes: não encontrei evidências nos documentos para sustentar uma citação."

    return text.strip(), warnings


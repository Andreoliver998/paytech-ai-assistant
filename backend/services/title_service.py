from __future__ import annotations

import re
from typing import Optional

from services.openai_service import gerar_resposta
from settings import settings


def _clean_title(s: str) -> str:
    t = (s or "").strip()
    t = t.replace("\r\n", " ").replace("\n", " ").strip()
    t = re.sub(r"\s+", " ", t)
    t = t.strip(" \"'“”‘’").strip()
    # hard cap
    if len(t) > 64:
        t = t[:63].rstrip() + "…"
    return t


def generate_conversation_title(
    *,
    first_user: str,
    first_assistant: str,
    model: Optional[str] = None,
) -> str:
    """
    Gera um título curto e humano (estilo ChatGPT).
    Mantém simples para não travar UX: 1 chamada, temperatura baixa.
    """
    u = (first_user or "").strip()
    a = (first_assistant or "").strip()
    if not u:
        return ""

    prompt = (
        "Crie um título curto (3–6 palavras) para esta conversa.\n"
        "Regras:\n"
        "- Português (pt-BR).\n"
        "- Sem aspas.\n"
        "- Sem ponto final.\n"
        "- Não inclua 'ChatGPT', 'IA', 'Assistente'.\n"
        "- Use capitalização natural.\n\n"
        f"Mensagem do usuário:\n{u}\n\n"
        f"Resposta do assistente (resumo):\n{a[:700]}\n"
    )

    out = gerar_resposta(
        mensagens=[
            {"role": "system", "content": "Você cria títulos curtos e bons para conversas."},
            {"role": "user", "content": prompt},
        ],
        modelo=model or settings.OPENAI_MODEL,
        temperatura=0.2,
    )
    return _clean_title(out)


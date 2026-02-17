from __future__ import annotations

from typing import List, Dict, Any, Optional, Iterable, Iterator
import os
import time

import numpy as np
from openai import OpenAI

from ..settings import settings

_CLIENT: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT

    api_key = settings.openai_api_key_value() or (os.getenv("OPENAI_API_KEY", "") or "").strip()
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY não encontrado. Configure no .env (ex.: OPENAI_API_KEY=sk-...)"
        )

    # timeout aqui é importante para não “travar” o stream sem erro
    _CLIENT = OpenAI(api_key=api_key, timeout=settings.OPENAI_TIMEOUT)
    return _CLIENT


def get_client() -> OpenAI:
    return _get_client()


def _build_formatting_system_message() -> Dict[str, str]:
    return {
        "role": "system",
        "content": (
            "Diretrizes de formatação:\n"
            "1) Quando a resposta envolver matemática, use KaTeX.\n"
            "   - Fórmulas em bloco: $$ . $$\n"
            "   - Fórmulas inline: \\( . \\)\n"
            "2) Não escreva fórmulas entre colchetes [ . ].\n"
            "3) Não coloque fórmulas dentro de blocos de código (```), a menos que o usuário peça código.\n"
            "4) Use Markdown limpo, organizado e legível."
        ),
    }


def _build_behavior_system_message() -> Dict[str, str]:
    return {
        "role": "system",
        "content": (
            "Você é um assistente de IA preciso, útil e direto.\n"
            "Regras:\n"
            "- Se faltar dado para responder com segurança, peça o mínimo necessário.\n"
            "- Evite afirmar certezas sem evidência.\n"
            "- Estruture respostas com headings/listas quando ajudar.\n"
            "- Quando houver passos práticos, forneça passo a passo.\n"
            "- Não revele chaves, segredos, nem dados sensíveis.\n"
        ),
    }


def _prepend_system_messages(mensagens: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(mensagens, list) or not mensagens:
        return []

    behavior = _build_behavior_system_message()
    formatting = _build_formatting_system_message()

    def has_system(content: str) -> bool:
        for m in mensagens:
            if m.get("role") == "system" and (m.get("content") == content):
                return True
        return False

    out = mensagens[:]
    if not has_system(behavior["content"]):
        out = [behavior] + out
    if not has_system(formatting["content"]):
        out = [formatting] + out
    return out


def embed_texts(texts: List[str], model: Optional[str] = None) -> List[List[float]]:
    if not isinstance(texts, list) or not texts:
        return []
    client = _get_client()
    emb_model = model or settings.OPENAI_EMBED_MODEL

    resp = client.embeddings.create(model=emb_model, input=texts)
    return [d.embedding for d in resp.data]


def gerar_resposta(
    mensagens: List[Dict[str, Any]],
    modelo: Optional[str] = None,
    temperatura: float = 0.2,
) -> str:
    if not isinstance(mensagens, list) or not mensagens:
        return ""

    client = _get_client()
    model_to_use = modelo or settings.OPENAI_MODEL
    final_msgs = _prepend_system_messages(mensagens)

    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=model_to_use,
                messages=final_msgs,
                temperature=temperatura,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            last_err = e
            time.sleep(0.4 * (attempt + 1))

    return f"[Erro ao gerar resposta: {last_err}]"


# -----------------------------
# Helpers robustos para stream
# (o SDK pode variar: attrs vs dict-like)
# -----------------------------
def _get_first_choice(chunk: Any) -> Any:
    try:
        choices = getattr(chunk, "choices", None)
        if choices and isinstance(choices, list) and len(choices) > 0:
            return choices[0]
    except Exception:
        pass
    if isinstance(chunk, dict):
        choices = chunk.get("choices")
        if isinstance(choices, list) and choices:
            return choices[0]
    return None


def _extract_token_from_choice(choice: Any) -> Optional[str]:
    """
    Extrai token do streaming de forma robusta.
    - SDK “moderno”: choice.delta.content (objeto)
    - Dict-like: choice["delta"]["content"]
    - Em alguns casos raros, pode vir message/content direto.
    """
    # 1) delta.content (attrs)
    try:
        delta = getattr(choice, "delta", None)
        if delta is not None:
            token = getattr(delta, "content", None)
            if isinstance(token, str) and token:
                return token
    except Exception:
        pass

    # 2) dict-like delta.content
    if isinstance(choice, dict):
        delta = choice.get("delta")
        if isinstance(delta, dict):
            token = delta.get("content")
            if isinstance(token, str) and token:
                return token

    # 3) fallback: message.content (não-stream / variações)
    try:
        msg = getattr(choice, "message", None)
        if msg is not None:
            token = getattr(msg, "content", None)
            if isinstance(token, str) and token:
                return token
    except Exception:
        pass

    if isinstance(choice, dict):
        msg = choice.get("message")
        if isinstance(msg, dict):
            token = msg.get("content")
            if isinstance(token, str) and token:
                return token

    return None


def gerar_resposta_stream(
    mensagens: List[Dict[str, Any]],
    modelo: Optional[str] = None,
    temperatura: float = 0.2,
) -> Iterable[str]:
    """
    IMPORTANTE:
    - Este stream NUNCA deve “dar sucesso” retornando vazio.
    - Se o stream não produzir tokens, faz fallback para gerar_resposta()
      e emite 1 bloco (garante que o frontend sempre veja algo).
    """
    if not isinstance(mensagens, list) or not mensagens:
        return iter(())

    client = _get_client()
    model_to_use = modelo or settings.OPENAI_MODEL
    final_msgs = _prepend_system_messages(mensagens)

    def _iterator() -> Iterator[str]:
        last_err: Optional[Exception] = None

        # retry leve para stream (evita casos transitórios)
        for attempt in range(2):
            produced_any = False
            try:
                stream = client.chat.completions.create(
                    model=model_to_use,
                    messages=final_msgs,
                    temperature=temperatura,
                    stream=True,
                )

                for chunk in stream:
                    choice0 = _get_first_choice(chunk)
                    if not choice0:
                        continue

                    token = _extract_token_from_choice(choice0)
                    if token:
                        produced_any = True
                        yield token

                # ✅ stream terminou sem tokens: força fallback
                if not produced_any:
                    raise RuntimeError("Stream finalizou sem tokens (vazio).")

                return

            except Exception as e:
                last_err = e
                time.sleep(0.3 * (attempt + 1))

        # fallback final (1 bloco)
        try:
            fallback = gerar_resposta(
                mensagens=mensagens,  # mantém sua lógica (mensagens originais)
                modelo=model_to_use,
                temperatura=temperatura,
            )
            if fallback and fallback.strip():
                yield fallback.strip()
            else:
                yield f"[Erro no streaming e fallback vazio: {last_err}]"
        except Exception as e:
            yield f"[Erro no streaming: {last_err} | Erro no fallback: {e}]"

    return _iterator()


def cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 0.0
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    denom = (np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)

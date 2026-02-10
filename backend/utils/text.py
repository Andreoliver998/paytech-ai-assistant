from __future__ import annotations

from typing import Any, Dict, List, Optional

from settings import settings

try:
    import tiktoken  # type: ignore
except Exception:  # pragma: no cover
    tiktoken = None


def estimate_tokens(text: str, model: Optional[str] = None) -> int:
    t = (text or "").strip()
    if not t:
        return 0
    if tiktoken is None:
        return max(1, len(t) // 4)
    try:
        enc = tiktoken.encoding_for_model(model or settings.OPENAI_MODEL)
    except Exception:
        enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(t))


def sanitize_and_trim_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    Mantém sua lógica original:
    - aceita roles system/user/assistant
    - corta mensagens muito longas
    - aplica janela (MAX_WINDOW_MESSAGES) no bloco não-system
    """
    if not isinstance(messages, list):
        raise ValueError("messages deve ser uma lista.")

    if len(messages) > settings.MAX_TOTAL_MESSAGES:
        messages = messages[-settings.MAX_TOTAL_MESSAGES :]

    sanitized: List[Dict[str, str]] = []
    for m in messages:
        role = (m.get("role") or "").strip()
        content = (m.get("content") or "").strip()

        if role not in ("system", "user", "assistant"):
            continue
        if not content:
            continue
        if len(content) > settings.MAX_CHARS_PER_MESSAGE:
            content = content[: settings.MAX_CHARS_PER_MESSAGE] + "…"

        sanitized.append({"role": role, "content": content})

    system_msgs = [m for m in sanitized if m["role"] == "system"]
    normal_msgs = [m for m in sanitized if m["role"] != "system"]

    if settings.MAX_WINDOW_MESSAGES > 0 and len(normal_msgs) > settings.MAX_WINDOW_MESSAGES:
        normal_msgs = normal_msgs[-settings.MAX_WINDOW_MESSAGES :]

    return system_msgs + normal_msgs

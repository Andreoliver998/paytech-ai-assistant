from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]


def _die(msg: str) -> None:
    raise SystemExit(msg)


def _base() -> str:
    return os.getenv("PAYTECH_BASE", "http://127.0.0.1:8000").rstrip("/")


def register_and_token() -> str:
    base = _base()
    email = f"smoke_state+{int(time.time())}@local"
    payload = {"tenant_name": "SmokeStateCo", "email": email, "password": "secret12"}
    r = requests.post(f"{base}/auth/register", json=payload, timeout=10)
    r.raise_for_status()
    token = (r.json() or {}).get("token") or ""
    if not token:
        _die("Token ausente em /auth/register")
    return str(token)


def upload_csv(token: str, csv_path: Path) -> str:
    base = _base()
    with csv_path.open("rb") as f:
        files = {"file": (csv_path.name, f, "text/csv")}
        r = requests.post(f"{base}/upload", headers={"Authorization": f"Bearer {token}"}, files=files, timeout=30)
    r.raise_for_status()
    data = r.json() or {}
    file_id = str(data.get("file_id") or "").strip()
    if not file_id:
        _die(f"upload retornou file_id vazio: {data}")
    return file_id


def chat(token: str, *, thread_id: str, user_text: str) -> dict:
    base = _base()
    payload = {
        "messages": [{"role": "user", "content": user_text}],
        "thread_id": thread_id,
        "precision": True,
        "show_sources": False,
    }
    r = requests.post(f"{base}/chat", headers={"Authorization": f"Bearer {token}"}, json=payload, timeout=60)
    r.raise_for_status()
    return r.json() or {}


def main() -> int:
    token = register_and_token()

    tmp = REPO_ROOT / "tmp_smoke_state"
    tmp.mkdir(parents=True, exist_ok=True)

    alunos = tmp / "alunos.csv"
    alunos.write_text(
        "nome,nota,comentario\n"
        "Ana Silva,9.5,ok?\n"
        "Bruno Souza,8.0,sim?\n"
        "Carla Lima,10.0,perfeito\n",
        encoding="utf-8",
    )

    outro = tmp / "outro.csv"
    # 10 linhas (diferente do alunos.csv)
    other_rows = ["nome,nota\n"] + [f"Pessoa {i},7.0\n" for i in range(1, 11)]
    outro.write_text("".join(other_rows), encoding="utf-8")

    file1 = upload_csv(token, alunos)
    _ = upload_csv(token, outro)

    thread_id = f"thread-smoke-{int(time.time())}"

    # Select active document
    r0 = chat(token, thread_id=thread_id, user_text="Preciso do alunos.csv")
    if "Documento atual definido" not in str(r0.get("reply") or ""):
        _die(f"Falha ao selecionar documento: {r0}")

    # Deterministic query must use the selected document (3 rows)
    r1 = chat(token, thread_id=thread_id, user_text="Quantos alunos tem?")
    if str(r1.get("reply") or "").strip() != "3":
        _die(f"Esperado 3 alunos, veio: {r1!r} (file_id={file1})")

    # Ensure we don't switch to the other uploaded file implicitly.
    r2 = chat(token, thread_id=thread_id, user_text="Quantos alunos tem?")
    if str(r2.get("reply") or "").strip() != "3":
        _die(f"Estado do documento não persistiu: {r2!r}")

    # Exit document mode
    _ = chat(token, thread_id=thread_id, user_text="voltar geral")

    print("OK: smoke_doc_session_state passou")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


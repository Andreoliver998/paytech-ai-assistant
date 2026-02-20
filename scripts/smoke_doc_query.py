from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _die(msg: str) -> None:
    raise SystemExit(msg)


def _base() -> str:
    return os.getenv("PAYTECH_BASE", "http://127.0.0.1:8000").rstrip("/")


def register_and_token() -> tuple[str, str]:
    base = _base()
    email = f"smoke+{int(time.time())}@local"
    payload = {"tenant_name": "SmokeCo", "email": email, "password": "secret12"}
    r = requests.post(f"{base}/auth/register", json=payload, timeout=10)
    r.raise_for_status()
    data = r.json() or {}
    token = data.get("token") or ""
    if not token:
        _die("Token ausente em /auth/register")
    tenant_id = ((data.get("tenant") or {}) or {}).get("id") or ""
    if not tenant_id:
        _die("tenant.id ausente em /auth/register")
    return str(token), str(tenant_id)


def debug_paths() -> dict:
    base = _base()
    r = requests.get(f"{base}/debug/paths", timeout=10)
    r.raise_for_status()
    return r.json() or {}


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


def doc_query(token: str, *, question: str, file_id: str, mode: str = "doc_query") -> str:
    base = _base()
    payload = {"question": question, "file_id": file_id, "mode": mode}
    r = requests.post(f"{base}/doc/query", headers={"Authorization": f"Bearer {token}"}, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json() or {}
    ans = str(data.get("answer") or "").strip()
    if not ans:
        _die(f"Resposta vazia em /doc/query: {json.dumps(data, ensure_ascii=False)[:500]}")
    return ans


def main() -> int:
    token, tenant_id = register_and_token()
    paths = debug_paths()
    data_dir = str(paths.get("DATA_DIR") or "").strip()
    if not data_dir:
        _die(f"DATA_DIR ausente em /debug/paths: {paths}")

    tmp = REPO_ROOT / "tmp_smoke"
    tmp.mkdir(parents=True, exist_ok=True)
    csv_path = tmp / "alunos.csv"
    csv_text = (
        "nome,nota,comentario\n"
        "Ana Silva,9.5,ok?\n"
        "Bruno Souza,8.0,sim?\n"
        "Carla Lima,10.0,perfeito\n"
    )
    csv_path.write_text(csv_text, encoding="utf-8")

    file_id = upload_csv(token, csv_path)

    # Compute expected punctuation count from the exact full_text persisted by the backend.
    # This avoids coupling the test to Pandas locally (the backend already extracts/indexes).
    fulltext_path = Path(data_dir) / "fulltexts" / tenant_id / f"{file_id}.txt"
    if not fulltext_path.exists():
        _die(f"full_text não encontrado em {fulltext_path} (verifique save_full_text no /upload)")
    fulltext = fulltext_path.read_text(encoding="utf-8", errors="replace")
    expected_qmarks = fulltext.count("?")

    a1 = doc_query(token, question="Quantos alunos tem?", file_id=file_id)
    if a1.strip() != "3":
        _die(f"Esperado 3 alunos, veio: {a1!r}")

    a2 = doc_query(token, question="Liste todos os nomes", file_id=file_id)
    got_names = [x.strip() for x in a2.splitlines() if x.strip()]
    expected_names = ["Ana Silva", "Bruno Souza", "Carla Lima"]
    if got_names != expected_names:
        _die(f"Nomes divergentes.\nEsperado: {expected_names}\nRecebido: {got_names}\nResposta bruta:\n{a2}")

    a3 = doc_query(token, question="Quantos pontos de interrogação existem?", file_id=file_id)
    if a3.strip() != str(expected_qmarks):
        _die(f"Esperado {expected_qmarks} '?' no texto extraído, veio: {a3!r}")

    print("OK: smoke_doc_query passou")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

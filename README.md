# PayTech AI Assistant

Projeto fullstack (backend FastAPI + frontend estático) com chat em streaming (SSE) e RAG em documentos (PDF/CSV/XLSX) via biblioteca “Downloads”.

## Stack
- Backend: Python + FastAPI + Uvicorn
- Streaming: Server-Sent Events (SSE) em `POST /chat/stream`
- Persistência local: SQLite (default em `backend/data/paytech.db`)
- Frontend: HTML/CSS/JS puro (Live Server ou qualquer static server)

## Estrutura
```
paytech-ai-assistant/
  backend/        # FastAPI (app.py/main.py), serviços, RAG, DB
  frontend/       # index.html, script.js, style.css
  scripts/        # scripts auxiliares (se houver)
```

## Requisitos
- Python 3.10+ (recomendado 3.11/3.12)
- (Opcional) VS Code + extensão “Live Server” para servir o frontend
- Conta OpenAI com API key (local)

## Executar (Windows / PowerShell)
```powershell
python -m venv backend\.venv
.\backend\.venv\Scripts\Activate.ps1

pip install -r backend\requirements.txt

# Configure ambiente (não vai para o Git)
# Preferência: .\.env (raiz do repo). (Compat) backend\.env também funciona.
Copy-Item .\backend\.env.example .\.env
# Edite .env e preencha OPENAI_API_KEY

.\backend\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Abra o Swagger:
- `http://127.0.0.1:8000/docs`

## Frontend (Live Server)
Sirva a pasta:
- `frontend`

URL típica (Live Server):
- `http://127.0.0.1:5500/frontend/index.html`

O frontend tenta usar `http://localhost:8000` (fallback para `http://127.0.0.1:8000`).

Para forçar outro backend:
```js
localStorage.setItem("paytech.backendBase","http://127.0.0.1:8000");
location.reload();
```

Ou por query param:
- `index.html?api=http://127.0.0.1:8000`

## Teste de streaming (debug)
Para validar o streaming incremental no frontend sem depender do LLM:
1) No `.env` (raiz): `DEBUG_STREAM=true`
2) Abra: `http://127.0.0.1:5500/frontend/index.html?debugstream=1`
3) Envie qualquer mensagem e confirme que aparece `A`, depois `B`, depois `C` antes de finalizar.

Observação: em `ENV=dev` o backend pode subir mesmo sem `OPENAI_API_KEY`, permitindo usar `/health` e `/debug/stream`.
Os endpoints `/chat` e `/chat/stream` retornam `503` até a chave ser configurada.

## Ambiente
- Nunca comite `.env` (já está protegido por `.gitignore`).
- O backend procura `.env` nesta ordem: `./.env` → `backend/.env`.

## Endpoints principais
- `GET /health` — healthcheck
- `GET /docs` — Swagger UI
- `POST /chat` — resposta JSON
- `POST /chat/stream` — streaming SSE (`text/event-stream`)
- Downloads (documentos / RAG):
  - `POST /downloads/upload` — upload (multipart)
  - `GET /downloads` — lista arquivos
  - `POST /downloads/search` — busca trechos

## Dados locais (não versionar)
Por padrão o projeto cria/usa:
- `backend/data/` (SQLite, KB, uploads)
- `backend/logs/`
- `backend/storage/` (artefatos/export)

Essas pastas são ignoradas pelo Git.

## Testes / lint
Este repositório não inclui suíte de testes automatizados no momento.

## Segurança (GitHub)
- Nunca publique `backend/.env`, bancos `.db`, uploads, logs ou `.venv`.
- Antes de fazer `git add .`, verifique `git status` e confirme que nada sensível entrou no staging.

# PayTech AI Assistant (PayTechAI_Senior)

Projeto Fullstack (backend FastAPI + frontend estático) com chat em streaming (SSE) e RAG em documentos (PDF/CSV/XLSX) via biblioteca “Downloads”.

## Stack
- Backend: Python + FastAPI + Uvicorn
- Streaming: Server-Sent Events (SSE) em `POST /chat/stream`
- Persistência local: SQLite (default em `backend/data/paytech.db`)
- Frontend: HTML/CSS/JS puro (Live Server ou qualquer static server)

## Estrutura
```
PayTechAI_Assistant/
  PayTechAI_Senior/
    backend/        # FastAPI (app.py/main.py), serviços, RAG, DB
    frontend/       # index.html, app.js, style.css
    scripts/        # scripts auxiliares (se houver)
```

## Requisitos
- Python 3.10+ (recomendado 3.11/3.12)
- (Opcional) VS Code + extensão “Live Server” para servir o frontend
- Conta OpenAI com API key (local)

## Setup rápido (Windows / PowerShell)
```powershell
cd .\PayTechAI_Assistant\PayTechAI_Senior\backend

python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt

# Configure ambiente (não vai para o Git)
Copy-Item .\.env.example .\.env
# Edite .env e preencha OPENAI_API_KEY

.\.venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Abra o Swagger:
- `http://127.0.0.1:8000/docs`

## Frontend (Live Server)
Sirva a pasta:
- `PayTechAI_Assistant/PayTechAI_Senior/frontend`

URL típica (Live Server):
- `http://127.0.0.1:5500/PayTechAI_Assistant/PayTechAI_Senior/frontend/index.html`

O frontend tenta usar `http://localhost:8000` (fallback para `http://127.0.0.1:8000`).

Para forçar outro backend:
```js
localStorage.setItem("paytech.backendBase","http://127.0.0.1:8000");
location.reload();
```

Ou por query param:
- `index.html?api=http://127.0.0.1:8000`

## Configuração de ambiente
O backend carrega variáveis a partir de:
- `PayTechAI_Assistant/PayTechAI_Senior/backend/.env`

Nunca comite `.env`. Ele já está protegido por `.gitignore` na raiz.

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
- `PayTechAI_Assistant/PayTechAI_Senior/backend/data/` (SQLite, KB, uploads)
- `PayTechAI_Assistant/PayTechAI_Senior/backend/logs/`
- `PayTechAI_Assistant/PayTechAI_Senior/backend/storage/` (artefatos/export)

Essas pastas são ignoradas pelo Git.

## Testes / lint
Este repositório não inclui suíte de testes automatizados no momento.

## Segurança (GitHub)
- Nunca publique `backend/.env`, bancos `.db`, uploads, logs ou `.venv`.
- Antes de fazer `git add .`, verifique `git status` e confirme que nada sensível entrou no staging.


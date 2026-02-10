# Executar no PowerShell dentro da pasta backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env -ErrorAction SilentlyContinue
uvicorn app:app --reload --host 127.0.0.1 --port 8000

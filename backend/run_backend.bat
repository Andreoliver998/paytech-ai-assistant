@echo off
cd /d %~dp0
python -m venv .venv
call .venv\Scripts\activate.bat
pip install -r requirements.txt
if not exist .env copy .env.example .env
uvicorn app:app --reload --host 127.0.0.1 --port 8000

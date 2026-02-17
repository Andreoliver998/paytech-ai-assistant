@echo off
cd /d %~dp0..
python -m venv backend\.venv
call backend\.venv\Scripts\activate.bat
pip install -r backend\requirements.txt
if not exist backend\.env copy backend\.env.example backend\.env
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000

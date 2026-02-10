import os
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1] / "backend"
DB_PATH = BASE_DIR / "data" / "paytech.db"

print("📌 Caminho do banco:", DB_PATH)
print("✅ Existe arquivo?", DB_PATH.exists())
print("📦 Tamanho (bytes):", DB_PATH.stat().st_size if DB_PATH.exists() else 0)

if not DB_PATH.exists():
    raise SystemExit("❌ paytech.db não existe. O app ainda não criou o banco.")

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
tables = [t[0] for t in cur.fetchall()]

print("\n🧱 Tabelas encontradas:", tables if tables else "❌ Nenhuma (banco não foi inicializado)")

for t in tables:
    cur.execute(f"SELECT COUNT(*) FROM {t};")
    count = cur.fetchone()[0]
    print(f"📊 Registros em {t}: {count}")

conn.close()
print("\n✅ Teste concluído.")

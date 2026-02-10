import os
import sqlite3
from pathlib import Path

DATA_ROOT = os.getenv("PAYTECH_DATA_DIR")
BASE_DIR = Path(__file__).resolve().parents[1] / "backend"

DATA_DIR = Path(DATA_ROOT) if DATA_ROOT else (BASE_DIR / "data")
DB_PATH = DATA_DIR / "paytech.db"
UPLOADS_DIR = DATA_DIR / "uploads"

print("🧭 DATA_DIR:", DATA_DIR)
print("🧭 DB_PATH:", DB_PATH)
print("🧭 UPLOADS_DIR:", UPLOADS_DIR)

disk_files = []
if UPLOADS_DIR.exists():
    disk_files = [p for p in UPLOADS_DIR.iterdir() if p.is_file()]

print("\n📁 Uploads no disco:", len(disk_files))
for p in sorted(disk_files)[-10:]:
    print(" -", p.name)

if not DB_PATH.exists():
    print("\n❌ DB não existe:", DB_PATH)
    raise SystemExit(1)

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

def safe_count(table: str) -> int:
    try:
        cur.execute(f"SELECT COUNT(*) FROM {table};")
        return cur.fetchone()[0]
    except Exception:
        return -1

files_count = safe_count("files")
chunks_count = safe_count("kb_chunks")
sessions_count = safe_count("sessions")
messages_count = safe_count("messages")

print("\n🗄️ Registros na tabela files:", files_count)

try:
    cur.execute("""
    SELECT file_id, filename, stored_path, size, createdAt
    FROM files
    ORDER BY createdAt DESC
    LIMIT 10;
    """)
    rows = cur.fetchall()
except Exception:
    rows = []

print("\n📌 Últimos registros em files:")
if not rows:
    print(" (vazio)")
else:
    for r in rows:
        print(" -", r)

print("\n🧠 Registros na tabela kb_chunks:", chunks_count)
print("\n💬 Sessões no banco:", sessions_count)
print("💬 Mensagens no banco:", messages_count)

conn.close()

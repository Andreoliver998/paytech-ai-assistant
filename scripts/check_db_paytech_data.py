import sqlite3
from pathlib import Path

DB = Path(r"C:\PayTechAI_Data\paytech.db")
print("DB exists:", DB.exists(), "-", DB)

con = sqlite3.connect(DB)
cur = con.cursor()

for table in ["files", "kb_chunks", "sessions", "messages"]:
    try:
        cur.execute(f"SELECT COUNT(*) FROM {table};")
        print(table, "=", cur.fetchone()[0])
    except Exception as e:
        print(table, "ERROR:", e)

con.close()

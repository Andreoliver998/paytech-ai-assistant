import os
import re
import sys
from pathlib import Path
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.db import SessionLocal, Base, engine
from backend.models import FileDB

Base.metadata.create_all(bind=engine)

def guess_original_filename(stored_name: str) -> str:
    # se vier no formato "timestamp_nome.ext", tenta remover só o prefixo timestamp_
    m = re.match(r"^\d{10}_(.+)$", stored_name)
    if m:
        return m.group(1)
    return stored_name

def main():
    DATA_ROOT = os.getenv("PAYTECH_DATA_DIR")
    if DATA_ROOT:
        data_dir = Path(DATA_ROOT)
    else:
        data_dir = REPO_ROOT / "backend" / "data"

    uploads_dir = data_dir / "uploads"
    if not uploads_dir.exists():
        print("Uploads dir não existe:", uploads_dir)
        return

    db: Session = SessionLocal()

    inserted = 0
    for p in uploads_dir.iterdir():
        if not p.is_file():
            continue

        file_id = p.stem
        ext = (p.suffix or "").lower().lstrip(".")
        filename = guess_original_filename(p.name)
        size = int(p.stat().st_size)

        existing = db.get(FileDB, file_id)
        if existing:
            continue

        db.add(FileDB(
            file_id=file_id,
            filename=filename,
            ext=ext,
            stored_path=str(p),
            size=size,
        ))
        inserted += 1

    db.commit()
    db.close()

    print(f"Backfill concluido. Inseridos: {inserted}")

if __name__ == "__main__":
    main()

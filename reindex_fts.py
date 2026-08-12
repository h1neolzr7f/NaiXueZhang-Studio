from pathlib import Path

from db import Database

db = Database(Path(__file__).with_name("data") / "aitag.db")
print("Rebuilding FTS indexes...")
db.rebuild_fts()
print(f"works={db.count_works()} indexed")
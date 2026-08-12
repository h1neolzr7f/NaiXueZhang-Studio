import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent / "data" / "aitag.db"
DONE = Path(__file__).resolve().parent / "logs" / "COMPLETED.txt"

if DONE.exists():
    DONE.unlink()

conn = sqlite3.connect(DB)
conn.execute(
    "DELETE FROM crawl_state WHERE key IN "
    "('search_page', 'search_done', 'search_total', 'search_total_pages')"
)
conn.executemany(
    "INSERT INTO crawl_state(key, value) VALUES (?, ?)",
    [
        ("search_page", "274"),
        ("search_done", "1"),
        ("search_total", "16376"),
        ("search_total_pages", "273"),
    ],
)
conn.commit()
for row in conn.execute("SELECT key, value FROM crawl_state ORDER BY key"):
    print(f"{row[0]}={row[1]}")
conn.close()
print("arknights crawl state restored (search_done=1)")
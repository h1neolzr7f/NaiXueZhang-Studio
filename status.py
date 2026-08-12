import sqlite3
from pathlib import Path

db_path = Path(__file__).with_name("data") / "aitag.db"
conn = sqlite3.connect(db_path)
works = conn.execute("SELECT COUNT(*) FROM works").fetchone()[0]
details = conn.execute(
    "SELECT COUNT(*) FROM works WHERE detail_json IS NOT NULL"
).fetchone()[0]
previews = conn.execute(
    "SELECT COUNT(*) FROM works WHERE preview_downloaded = 1"
).fetchone()[0]
images = conn.execute(
    "SELECT COUNT(*) FROM work_images WHERE downloaded = 1"
).fetchone()[0]
total_images = conn.execute("SELECT COUNT(*) FROM work_images").fetchone()[0]
states = dict(conn.execute("SELECT key, value FROM crawl_state"))
print(f"works={works}")
print(f"details={details}")
print(f"works_all_images_done={previews}")
print(f"images_downloaded={images}/{total_images}")
for key in ("search_page", "search_total", "search_total_pages", "search_done"):
    if key in states:
        print(f"{key}={states[key]}")
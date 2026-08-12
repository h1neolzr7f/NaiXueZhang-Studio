import json
import sqlite3
from pathlib import Path

conn = sqlite3.connect(Path(__file__).with_name("data") / "aitag.db")
states = dict(conn.execute("SELECT key, value FROM crawl_state"))
total = int(states.get("search_total", "0"))
pages = int(states.get("search_total_pages", "0"))
page_size = 60
print(f"search_total={total}")
print(f"search_total_pages={pages}")
print(f"calc_pages={(total + page_size - 1) // page_size}")
print("ai_type counts:")
for row in conn.execute(
    "SELECT ai_type, COUNT(*) FROM works GROUP BY ai_type ORDER BY 2 DESC"
):
    print(f"  {row[0]}: {row[1]}")
sample = json.loads(conn.execute("SELECT list_json FROM works LIMIT 1").fetchone()[0])
print("sample AI_type:", sample.get("AI_type"))
print("sample ai_png:", sample.get("ai_png"))
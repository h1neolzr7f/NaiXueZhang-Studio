import sqlite3
from search import build_works_fts_query, split_include_exclude

conn = sqlite3.connect("data/aitag.db")
queries = [
    "明日方舟",
    "NAI",
    build_works_fts_query("-NAI_X NAI 明日方舟")[0],
    '"NAI_X"',
    '-"NAI_X" "NAI" "明日方舟"',
]
for q in queries:
    if not q:
        continue
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM works_fts WHERE works_fts MATCH ?",
            (q,),
        ).fetchone()[0]
        print(f"{q!r} -> {count}")
    except Exception as exc:
        print(f"{q!r} -> ERROR {exc}")

print("split", split_include_exclude("-NAI_X NAI 明日方舟"))
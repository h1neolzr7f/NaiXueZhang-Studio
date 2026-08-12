#!/usr/bin/env python3
"""校验爬虫断点续传：已完成作品不应再次进入待爬队列。"""

from __future__ import annotations

import random
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db import Database

DB_PATH = ROOT / "data" / "aitag.db"
DATA_DIR = ROOT / "data"


def main() -> int:
    db = Database(DB_PATH)
    reconcile = db.reconcile_local_covers(DATA_DIR)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    detail_done = [
        int(r["id"])
        for r in conn.execute(
            "SELECT id FROM works WHERE detail_json IS NOT NULL ORDER BY RANDOM() LIMIT 50"
        ).fetchall()
    ]
    preview_done = [
        int(r["id"])
        for r in conn.execute(
            "SELECT id FROM works WHERE preview_downloaded = 1 ORDER BY RANDOM() LIMIT 50"
        ).fetchall()
    ]
    conn.close()

    pending_detail = set(db.pending_detail_ids(50000))
    pending_preview = set(db.pending_preview_work_ids(50000, max_attempts=999))

    detail_leaks = [wid for wid in detail_done if wid in pending_detail]
    preview_leaks = [wid for wid in preview_done if wid in pending_preview]

    print("reconcile", reconcile)
    print(
        f"works={db.count_works()} details={db.count_details()} "
        f"previews={db.count_previews()}"
    )
    print(f"pending_detail={len(pending_detail)} pending_preview={len(pending_preview)}")
    print(f"detail_leaks={len(detail_leaks)} preview_leaks={len(preview_leaks)}")

    ok = not detail_leaks and not preview_leaks
    if detail_leaks:
        print("detail leak sample:", detail_leaks[:5])
    if preview_leaks:
        print("preview leak sample:", preview_leaks[:5])
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
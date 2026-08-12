from __future__ import annotations

import json
from pathlib import Path

from db import Database


def test_year_quarter_month_and_older_filters_match_gallery_options(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "time.db")
    rows = [
        (1, "2022-12-31T23:00:00+00:00"),
        (2, "2024-02-01T00:00:00+00:00"),
        (3, "2024-04-01T00:00:00+00:00"),
        (4, "2024-06-30T23:59:59+00:00"),
        (5, "2024-07-01T00:00:00+00:00"),
        (6, "2025-01-01T00:00:00+00:00"),
    ]
    try:
        for work_id, created in rows:
            item = {"id": work_id, "title": str(work_id), "AI_type": "NAI"}
            db.conn.execute(
                """
                INSERT INTO works(id, title, ai_type, create_date, list_json)
                VALUES (?, ?, 'NAI', ?, ?)
                """,
                (work_id, str(work_id), created, json.dumps(item)),
            )
        db.conn.commit()

        year = db.search_works(time_range="y2024", page_size=20)
        quarter = db.search_works(time_range="q2024Q2", page_size=20)
        month = db.search_works(time_range="m2024-06", page_size=20)
        older = db.search_works(time_range="older", page_size=20)
    finally:
        db.close()

    assert [item["id"] for item in year["items"]] == [5, 4, 3, 2]
    assert [item["id"] for item in quarter["items"]] == [4, 3]
    assert [item["id"] for item in month["items"]] == [4]
    assert [item["id"] for item in older["items"]] == [1]

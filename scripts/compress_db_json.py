"""One-time migration: compress large JSON columns in the site DB in place.

Compresses `work_images.ai_json` and `works.detail_json` with zlib (Z1: prefix)
in small cursor-based batches so the running server keeps working. Resume-safe:
rows already stored as BLOB are skipped automatically. Use --reverse to
decompress back to plain text at any time.

Usage:
    python scripts/compress_db_json.py --dry-run
    python scripts/compress_db_json.py --batch 2000
    python scripts/compress_db_json.py --reverse --batch 2000
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compression import compress_text, decompress_if_needed, is_compressed

# (table, column, id_column)
TARGETS = [
    ("work_images", "ai_json", "id"),
    ("works", "detail_json", "id"),
]


def _connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path), timeout=60.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=60000")
    con.execute("PRAGMA wal_autocheckpoint=1000")
    return con


def stats(con: sqlite3.Connection) -> dict[str, tuple[int, int]]:
    """Return {column: (rows_to_process, rows_done)} (one full scan)."""
    out = {}
    for table, column, _ in TARGETS:
        total = con.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" IS NOT NULL'
        ).fetchone()[0]
        done = con.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE typeof("{column}") = "blob"'
        ).fetchone()[0]
        out[f"{table}.{column}"] = (int(total) - int(done), int(done))
    return out


def process_batch(
    con: sqlite3.Connection,
    table: str,
    column: str,
    id_col: str,
    batch: int,
    *,
    last_id: int,
    reverse: bool,
) -> tuple[int | None, int]:
    """Process up to `batch` rows after `last_id`.

    Returns (None, last_id) when no more rows exist past the cursor —
    the caller must stop then.  Returns (touched, new_last_id) otherwise;
    touched may be 0 when every row in the batch was already processed
    (compressed BLOB in forward mode, plain text in reverse mode), in which
    case the cursor still advanced and the caller must continue.
    """
    rows = con.execute(
        f'SELECT "{id_col}" AS id, "{column}" AS val FROM "{table}" '
        f'WHERE "{id_col}" > ? '
        f'ORDER BY "{id_col}" LIMIT ?',
        (last_id, batch),
    ).fetchall()
    if not rows:
        return None, last_id
    updates = []
    new_last = last_id
    for row in rows:
        rid = int(row["id"])
        new_last = max(new_last, rid)
        val = row["val"]
        if val is None:
            continue
        if reverse:
            # BLOB(Z1:) -> plain text; plain text passes through untouched
            if is_compressed(val):
                updates.append((decompress_if_needed(val), rid))
        else:
            # plain text -> BLOB(Z1:); already-compressed BLOB passes through
            if isinstance(val, str) and not is_compressed(val):
                updates.append((compress_text(val), rid))
    if updates:
        con.executemany(
            f'UPDATE "{table}" SET "{column}" = ? WHERE "{id_col}" = ?',
            updates,
        )
        con.commit()
    return len(updates), new_last


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="only show stats")
    parser.add_argument("--batch", type=int, default=2000)
    parser.add_argument("--reverse", action="store_true", help="decompress back to plain text")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "aitag.db")
    args = parser.parse_args()

    con = _connect(args.db)
    st = stats(con)
    verb = "回滚(解压)" if args.reverse else "压缩"
    print(f"=== {verb}迁移统计 ===", flush=True)
    total_bytes = 0
    for key, (pending, done) in st.items():
        table, column = key.split(".")
        sample = con.execute(
            f'SELECT AVG(LENGTH("{column}")) AS avg_len FROM ('
            f'SELECT "{column}" FROM "{table}" '
            f'WHERE "{column}" IS NOT NULL LIMIT 1000)'
        ).fetchone()["avg_len"] or 0
        raw_est = float(sample) * pending
        total_bytes += raw_est
        print(f"  {key}: 待处理 {pending:,} 行，已完成 {done:,} 行，~{raw_est/1e6:.1f} MB", flush=True)
    print(f"  预估影响 ~{total_bytes/1e9:.2f} GB", flush=True)
    if args.dry_run:
        print("dry-run：未做任何修改")
        con.close()
        return 0

    t0 = time.time()
    # 为无主键索引的表建临时迁移索引（id 非 rowid 时 ORDER BY id 会全表扫描）
    temp_indexes = []
    for table, column, id_col in TARGETS:
        try:
            con.execute(f'CREATE INDEX IF NOT EXISTS idx_migrate_{table}_id ON "{table}"("{id_col}")')
            con.commit()
            temp_indexes.append(f'idx_migrate_{table}_id')
            print(f"  临时索引就绪: idx_migrate_{table}_id", flush=True)
        except Exception as e:
            print(f"  提示: 无法为 {table} 建临时索引 ({e})，可能较慢", flush=True)
    for table, column, id_col in TARGETS:
        key = f"{table}.{column}"
        # 断点续传：跳到已处理的最大 id（已压缩 BLOB 行的末尾）
        if args.reverse:
            resume_row = con.execute(
                f'SELECT MAX("{id_col}") AS m FROM "{table}" '
                f'WHERE typeof("{column}") = "text"'
            ).fetchone()["m"]
        else:
            resume_row = con.execute(
                f'SELECT MAX("{id_col}") AS m FROM "{table}" '
                f'WHERE typeof("{column}") = "blob"'
            ).fetchone()["m"]
        last_id = int(resume_row or 0)
        total_touched = 0
        if last_id:
            print(f"  {key}: 断点续传，从 id>{last_id:,} 继续", flush=True)
        while True:
            touched, last_id = process_batch(
                con, table, column, id_col, args.batch,
                last_id=last_id, reverse=args.reverse,
            )
            if touched is None:
                break
            if touched == 0:
                # 本批全部是已处理行：游标已推进，继续下一批
                continue
            total_touched += touched
            print(f"  {key}: 本批 {touched:,} 行，累计 {total_touched:,} 行 ({time.time()-t0:.0f}s)", flush=True)
    for index_name in temp_indexes:
        try:
            con.execute(f'DROP INDEX IF EXISTS "{index_name}"')
            con.commit()
        except Exception:
            pass
    con.close()
    print(f"=== 完成，耗时 {time.time()-t0:.0f}s ===", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

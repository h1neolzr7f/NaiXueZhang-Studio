"""Clear all codex gallery assets (DB + images)."""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODEX = ROOT / "data" / "galleries" / "codex"
DB = CODEX / "gallery.db"
IMAGES = CODEX / "images"


def main() -> int:
    parser = argparse.ArgumentParser(description="Clear all codex gallery assets")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm irreversible deletion of the codex gallery database rows and images",
    )
    args = parser.parse_args()
    if not args.yes:
        print("Refusing to clear the codex gallery without explicit --yes confirmation")
        return 2

    CODEX.mkdir(parents=True, exist_ok=True)
    if IMAGES.exists():
        for child in list(IMAGES.iterdir()):
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                try:
                    child.unlink()
                except OSError as exc:
                    print(f"warn unlink {child}: {exc}")
    IMAGES.mkdir(parents=True, exist_ok=True)

    if DB.exists():
        conn = sqlite3.connect(str(DB), timeout=60)
        try:
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            ]
            conn.execute("PRAGMA foreign_keys=OFF")
            for name in tables:
                try:
                    conn.execute(f'DELETE FROM "{name}"')
                except sqlite3.Error as exc:
                    print(f"warn delete {name}: {exc}")
            conn.commit()
            try:
                conn.execute("VACUUM")
            except sqlite3.Error as exc:
                print(f"warn vacuum: {exc}")
            works = 0
            try:
                works = int(conn.execute("SELECT COUNT(*) FROM works").fetchone()[0])
            except sqlite3.Error:
                pass
            print(f"works_after={works}")
        finally:
            conn.close()
    else:
        print("db_missing")

    img_files = sum(1 for p in IMAGES.rglob("*") if p.is_file())
    print(f"images_files={img_files}")
    print(f"cleared={CODEX}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

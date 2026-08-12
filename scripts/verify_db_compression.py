"""Verify DB compression migration: SHA256 snapshot before/after + row counts.

Usage:
    python scripts/verify_db_compression.py snapshot --out verify_hashes.json
    python scripts/verify_db_compression.py check --in verify_hashes.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compression import decompress_if_needed

TARGETS = [
    ("work_images", "ai_json", "id"),
    ("works", "detail_json", "id"),
]
SAMPLE = 500


def _connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path), timeout=60.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA busy_timeout=60000")
    return con


def _normalized(con: sqlite3.Connection, table: str, column: str, id_col: str) -> list[dict]:
    """Deterministic sample: spread across id space for stable comparison."""
    count = int(con.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" IS NOT NULL').fetchone()[0])
    if count == 0:
        return []
    step = max(1, count // SAMPLE)
    rows = con.execute(
        f'SELECT "{id_col}" AS id, "{column}" AS val FROM "{table}" '
        f'WHERE "{column}" IS NOT NULL AND ("{id_col}" % ?) = 0 '
        f'ORDER BY "{id_col}" LIMIT ?',
        (step, SAMPLE),
    ).fetchall()
    out = []
    for row in rows:
        text = decompress_if_needed(row["val"])
        digest = hashlib.sha256(str(text).encode("utf-8", "replace")).hexdigest()
        out.append({"id": int(row["id"]), "sha256": digest, "len": len(str(text))})
    return out


def snapshot(args) -> int:
    con = _connect(args.db)
    data = {"targets": [], "counts": {}}
    for table, column, id_col in TARGETS:
        count = int(con.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" IS NOT NULL'
        ).fetchone()[0])
        data["counts"][f"{table}.{column}"] = count
        data["targets"].append({
            "table": table,
            "column": column,
            "id_col": id_col,
            "sample": _normalized(con, table, column, id_col),
        })
    Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"快照已写入 {args.out}：{len(data['targets'])} 张表，每表 {SAMPLE} 行抽样")
    con.close()
    return 0


def check(args) -> int:
    expected = json.loads(Path(args.in_).read_text(encoding="utf-8"))
    con = _connect(args.db)
    ok = True
    for target in expected["targets"]:
        table, column, id_col = target["table"], target["column"], target["id_col"]
        count = int(con.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" IS NOT NULL'
        ).fetchone()[0])
        if count != expected["counts"][f"{table}.{column}"]:
            print(f"FAIL {table}.{column}: 行数 {count} != {expected['counts'][f'{table}.{column}']}")
            ok = False
        # 逐行比对抽样 ID 的哈希（ID 相同、内容解压后一致）
        current = {item["id"]: item for item in _normalized(con, table, column, id_col)}
        mismatches = 0
        for item in target["sample"]:
            cur = current.get(item["id"])
            if cur is None or cur["sha256"] != item["sha256"]:
                mismatches += 1
        if mismatches:
            print(f"FAIL {table}.{column}: {mismatches}/{len(target['sample'])} 个抽样 ID 哈希不一致")
            ok = False
        else:
            print(f"OK {table}.{column}: 行数 {count:,}，抽样 {len(target['sample'])} 行哈希全部一致")
    con.close()
    print("验证通过" if ok else "验证失败")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("snapshot", help="capture pre-migration snapshot")
    sp.add_argument("--out", default="verify_hashes.json")
    sp.add_argument("--db", type=Path, default=ROOT / "data" / "aitag.db")
    sp.set_defaults(func=snapshot)
    ch = sub.add_parser("check", help="verify post-migration integrity")
    ch.add_argument("--in", dest="in_", default="verify_hashes.json")
    ch.add_argument("--db", type=Path, default=ROOT / "data" / "aitag.db")
    ch.set_defaults(func=check)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

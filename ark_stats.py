import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent / "data" / "aitag.db"
ARK_FILTER = """
    LOWER(COALESCE(title,'') || COALESCE(caption,'') || COALESCE(tags,'')) LIKE '%明日方舟%'
    OR LOWER(COALESCE(title,'') || COALESCE(caption,'') || COALESCE(tags,'')) LIKE '%arknights%'
    OR LOWER(COALESCE(title,'') || COALESCE(caption,'') || COALESCE(tags,'')) LIKE '%アークナイツ%'
"""

conn = sqlite3.connect(DB)
total = conn.execute("SELECT COUNT(*) FROM works").fetchone()[0]
ark = conn.execute(f"SELECT COUNT(*) FROM works WHERE {ARK_FILTER}").fetchone()[0]
ark_detail = conn.execute(
    f"SELECT COUNT(*) FROM works WHERE detail_json IS NOT NULL AND ({ARK_FILTER})"
).fetchone()[0]
ark_cover = conn.execute(
    f"SELECT COUNT(*) FROM works WHERE preview_downloaded=1 AND ({ARK_FILTER})"
).fetchone()[0]
ark_no_cover = conn.execute(
    f"""
    SELECT COUNT(*) FROM works
    WHERE detail_json IS NOT NULL AND preview_downloaded=0 AND ({ARK_FILTER})
    """
).fetchone()[0]
ark_no_detail = conn.execute(
    f"SELECT COUNT(*) FROM works WHERE detail_json IS NULL AND ({ARK_FILTER})"
).fetchone()[0]
non_ark = total - ark
print(f"total_works={total}")
print(f"ark_works={ark}")
print(f"ark_detail={ark_detail}")
print(f"ark_cover={ark_cover}")
print(f"ark_missing_detail={ark_no_detail}")
print(f"ark_missing_cover={ark_no_cover}")
print(f"non_ark_works={non_ark}")
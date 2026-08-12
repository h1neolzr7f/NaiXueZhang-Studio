import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db import Database
from tag_translate import TagTranslator

db = Database(ROOT / "data" / "aitag.db")


def bench(label, fn, repeat=3):
    times = []
    last = None
    for _ in range(repeat):
        t0 = time.perf_counter()
        last = fn()
        times.append((time.perf_counter() - t0) * 1000)
    avg = sum(times) / len(times)
    extra = ""
    if isinstance(last, dict):
        if "total" in last:
            extra = f" total={last.get('total')} items={len(last.get('items') or [])}"
        elif last.get("work"):
            extra = " ok"
    print(f"{label}: {avg:.0f}ms{extra}")


bench("search_blank_skip_total", lambda: db.search_works(page=1, page_size=60, skip_total=True, local_scope="local"))
bench("search_ark_count", lambda: db.search_works(q="明日方舟", page=1, page_size=60, local_scope="local"))
bench("search_ark_skip_total", lambda: db.search_works(q="明日方舟", page=1, page_size=60, skip_total=True, local_scope="local"))
bench("search_prompt", lambda: db.search_works(prompt="1girl", page=1, page_size=60, skip_total=True, local_scope="local"))
bench("work_detail", lambda: db.get_work_detail(145671660), repeat=5)

tr = TagTranslator()
bench("tag_reload", tr.reload)
bench("tag_translate_batch", lambda: tr.translate_many(["skadi_(arknights)", "amiya_(arknights)", "long_hair", "女の子"] * 20))

from ark_char_library import search_library

bench("ark_library_search", lambda: search_library(gender="female", q="skadi", limit=20))
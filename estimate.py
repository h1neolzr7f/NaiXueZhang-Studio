import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

conn = sqlite3.connect(Path(__file__).with_name("data") / "aitag.db")
states = dict(conn.execute("SELECT key, value FROM crawl_state"))
works = conn.execute("SELECT COUNT(*) FROM works").fetchone()[0]
details = conn.execute(
    "SELECT COUNT(*) FROM works WHERE detail_json IS NOT NULL"
).fetchone()[0]
page = int(states.get("search_page", "1")) - 1
total_pages = int(states.get("search_total_pages", "273"))
total_works = int(states.get("search_total", "16376"))
delay = 0.35
workers = 4
preview_mode = "cover_only"
try:
    import json
    from pathlib import Path

    cfg = json.loads(
        Path(__file__).with_name("config.json").read_text(encoding="utf-8")
    )
    preview_mode = cfg.get("preview_mode", "cover_only")
    delay = float(cfg.get("request_delay_sec", delay))
    workers = max(1, int(cfg.get("concurrent_workers", workers)))
except Exception:
    pass
effective_rate = workers / max(delay, 0.05)

search_left = max(total_pages - page, 0)
detail_left = max(total_works - details, 0)
# cover_only: 每作品只下 1 张封面；all: 平均约 16 张
images_per_work = 1 if preview_mode == "cover_only" else 16
preview_left = detail_left * images_per_work

search_sec = search_left / effective_rate
detail_sec = detail_left / effective_rate
preview_sec = preview_left / effective_rate

now = datetime.now()
print(f"当前作品列表: {works}/{total_works}")
print(f"搜索页进度: {page}/{total_pages}")
print(f"详情进度: {details}/{total_works}")
print(f"预计搜索完成: {(now + timedelta(seconds=search_sec)).strftime('%H:%M')}")
print(f"预计详情完成: {(now + timedelta(seconds=search_sec + detail_sec)).strftime('%H:%M')}")
label = "封面预览" if preview_mode == "cover_only" else "全部预览图"
print(f"预计{label}完成: {(now + timedelta(seconds=search_sec + detail_sec + preview_sec)).strftime('%Y-%m-%d %H:%M')}")
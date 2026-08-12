import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "aitag.db"
WATCH_LOG = ROOT / "logs" / "watch.log"
DONE = ROOT / "logs" / "COMPLETED.txt"


def snapshot() -> dict:
    conn = sqlite3.connect(DB)
    states = dict(conn.execute("SELECT key, value FROM crawl_state"))
    works = conn.execute("SELECT COUNT(*) FROM works").fetchone()[0]
    details = conn.execute(
        "SELECT COUNT(*) FROM works WHERE detail_json IS NOT NULL"
    ).fetchone()[0]
    previews = conn.execute(
        "SELECT COUNT(*) FROM works WHERE preview_downloaded = 1"
    ).fetchone()[0]
    search_total = int(states.get("search_total", "0") or 0)
    conn.close()
    target = works if states.get("search_done") == "1" and works > 0 else search_total
    if target <= 0:
        target = works
    return {
        "works": works,
        "details": details,
        "previews": previews,
        "total": target,
        "search_done": states.get("search_done") == "1",
    }


def is_complete(data: dict) -> bool:
    works = data["works"]
    if works <= 0:
        return False
    return (
        data["search_done"]
        and data["details"] >= works
        and data["previews"] >= works
    )


def main() -> None:
    WATCH_LOG.parent.mkdir(parents=True, exist_ok=True)
    print(f"[watch] started {datetime.now().isoformat()}", flush=True)
    while True:
        data = snapshot()
        line = (
            f"[watch] {datetime.now().strftime('%H:%M:%S')} "
            f"works={data['works']}/{data['total']} "
            f"details={data['details']}/{data['total']} "
            f"covers={data['previews']}/{data['total']}"
        )
        print(line, flush=True)
        with WATCH_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

        if is_complete(data):
            msg = (
                f"CRAWL_COMPLETE\n"
                f"time={datetime.now().isoformat()}\n"
                f"works={data['works']}\n"
                f"details={data['details']}\n"
                f"covers={data['previews']}\n"
            )
            DONE.write_text(msg, encoding="utf-8")
            print(msg, flush=True)
            return

        time.sleep(120)


if __name__ == "__main__":
    main()
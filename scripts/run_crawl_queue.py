"""Sequential Pixiv discovery queue with checkpoint-safe NAI-only intake."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pixiv_nai_crawler import crawl_once, default_task, save_task  # noqa: E402

QUEUE_FILE = ROOT / "logs" / "pixiv_crawl_queue.json"
STATE_FILE = ROOT / "logs" / "pixiv_crawl_queue_state.json"
LOG_FILE = ROOT / "logs" / "pixiv_crawl_queue.log"

DEFAULT_QUEUE = [
    {
        "id": "arknights_new",
        "label": "Arknights / 明日方舟",
        "task": {
            "scopes": [
                {
                    "id": "arknights",
                    "type": "search",
                    "query": "アークナイツ",
                    "sort": "date_desc",
                    "search_target": "partial_match_for_tags",
                    "enabled": True,
                }
            ]
        },
    },
    {
        "id": "novelai_new",
        "label": "NovelAI new works",
        "task": {
            "scopes": [
                {
                    "id": "novelai",
                    "type": "search",
                    "query": "NovelAI",
                    "sort": "date_desc",
                    "search_target": "partial_match_for_tags",
                    "enabled": True,
                }
            ]
        },
    },
    {
        "id": "pixiv_daily",
        "label": "Pixiv daily AI candidates",
        "task": {
            "scopes": [
                {
                    "id": "daily",
                    "type": "ranking",
                    "mode": "day",
                    "enabled": True,
                }
            ]
        },
    },
]


def log(message: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {message}"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    print(line, flush=True)


def load_queue() -> list[dict]:
    if QUEUE_FILE.is_file():
        raw = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, list) and raw:
            return [dict(item) for item in raw if isinstance(item, dict)]
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_FILE.write_text(
        json.dumps(DEFAULT_QUEUE, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return list(DEFAULT_QUEUE)


def load_state() -> dict:
    if STATE_FILE.is_file():
        try:
            raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except (OSError, ValueError):
            pass
    return {"completed_ids": [], "current_id": None}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def task_for_item(item: dict) -> dict:
    task = default_task()
    supplied = dict(item.get("task") or {})
    if not supplied and item.get("search_query"):
        query = " ".join(
            term
            for term in str(item["search_query"]).split()
            if term.casefold() not in {"nai", "-nai_x"}
        ).strip() or "NovelAI"
        supplied["scopes"] = [
            {
                "id": "compat-search",
                "type": "search",
                "query": query,
                "sort": "date_desc",
                "search_target": "partial_match_for_tags",
                "enabled": True,
            }
        ]
    task.update(supplied)
    task["enabled"] = True
    return task


def main() -> int:
    queue = load_queue()
    state = load_state()
    completed = set(state.get("completed_ids") or [])
    for item in queue:
        item_id = str(item.get("id") or "").strip()
        if not item_id or item_id in completed:
            continue
        state["current_id"] = item_id
        save_state(state)
        saved = save_task(task_for_item(item), root=ROOT)
        log(f"START {item_id} scopes={len(saved['scopes'])}")
        while True:
            report = crawl_once(root=ROOT)
            status = str(report.get("status") or "failed")
            log(
                f"RUN {item_id} status={status} "
                f"seen={int(report.get('works_seen') or 0)} "
                f"accepted={int(report.get('works_accepted') or 0)}"
            )
            if status == "budget_reached":
                continue
            if status != "completed":
                state["last_status"] = status
                save_state(state)
                return 1
            break
        completed.add(item_id)
        state["completed_ids"] = sorted(completed)
        state["current_id"] = None
        save_state(state)
    log("ALL PIXIV NAI QUEUE TASKS COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

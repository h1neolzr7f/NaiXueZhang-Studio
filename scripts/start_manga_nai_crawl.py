"""启动「-NAI_X NAI 漫画」全量爬取；库里已有作品自动跳过。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crawler_task import apply_task

DONE = ROOT / "logs" / "COMPLETED.txt"
if DONE.exists():
    DONE.unlink()

result = apply_task(
    {
        "search_query": "-NAI_X NAI 漫画",
        "search_sort": "new",
        "search_time_range": "all",
        "search_max_pages": 0,
        "crawler_phase": "all",
        "dataset_name": "nai-manga",
    },
    reset_search=True,
    restart=True,
)
print(json.dumps(result, ensure_ascii=False, indent=2))
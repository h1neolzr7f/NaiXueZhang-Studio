"""切换鸣潮任务为月榜排序并重启爬虫（已有作品自动跳过）。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crawler_task import apply_task

result = apply_task(
    {
        "search_query": "-NAI_X NAI 鸣潮",
        "search_sort": "monthly",
        "search_time_range": "current",
        "crawler_phase": "all",
    },
    reset_search=True,
    restart=True,
)
print(json.dumps(result, ensure_ascii=False, indent=2))
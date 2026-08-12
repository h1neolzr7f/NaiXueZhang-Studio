import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crawler_control import crawler_running, stop_crawler_processes

DONE = ROOT / "logs" / "COMPLETED.txt"
if DONE.exists():
    DONE.unlink()

stopped = stop_crawler_processes()
time.sleep(2)

log_out = ROOT / "logs" / "crawl_direct_out.log"
log_err = ROOT / "logs" / "crawl_direct_err.log"
with log_out.open("a", encoding="utf-8") as out, log_err.open("a", encoding="utf-8") as err:
    proc = subprocess.Popen(
        [sys.executable, "-u", str(ROOT / "pixiv_nai_crawler.py"), "--once"],
        cwd=str(ROOT),
        stdout=out,
        stderr=err,
        creationflags=0x08000000,
    )

print(json.dumps({"stopped": stopped, "pid": proc.pid, "running": crawler_running()}, ensure_ascii=False))

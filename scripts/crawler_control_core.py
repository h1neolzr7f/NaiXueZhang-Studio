"""Process control limited to the direct Pixiv NAI crawler."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import psutil


ROOT = Path(__file__).resolve().parent
CRAWLER_SCRIPT = (ROOT / "pixiv_nai_crawler.py").resolve()


def _owned_processes() -> list[psutil.Process]:
    result: list[psutil.Process] = []
    expected = os.path.normcase(str(CRAWLER_SCRIPT))
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            arguments = [os.path.normcase(str(arg)) for arg in (process.info.get("cmdline") or [])]
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
        if expected in arguments:
            result.append(process)
    return result


def multi_crawler_status() -> dict[str, dict[str, object]]:
    processes = _owned_processes()
    return {
        "pixiv": {
            "running": bool(processes),
            "pids": [process.pid for process in processes],
        }
    }


def start_crawler_target(
    target: str,
    *,
    phase: str | None = None,
    watch: bool = True,
) -> dict[str, object]:
    _ = phase
    if str(target or "").strip().casefold() != "pixiv":
        raise ValueError("Core supports only the Pixiv crawler")
    running = _owned_processes()
    if running:
        return {"pixiv": {"started": False, "already_running": True, "pids": [p.pid for p in running]}}
    command = [sys.executable, str(CRAWLER_SCRIPT), "--watch" if watch else "--once"]
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    logs_dir = ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "pixiv-crawler.log"
    with log_path.open("ab") as log_handle:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            close_fds=True,
        )
    return {"pixiv": {"started": True, "already_running": False, "pids": [process.pid], "log": "logs/pixiv-crawler.log"}}


def stop_crawler_target(target: str) -> dict[str, dict[str, list[int]]]:
    if str(target or "").strip().casefold() != "pixiv":
        raise ValueError("Core supports only the Pixiv crawler")
    stopped: list[int] = []
    for process in _owned_processes():
        try:
            process.terminate()
            process.wait(timeout=5)
            stopped.append(process.pid)
        except psutil.TimeoutExpired:
            process.kill()
            stopped.append(process.pid)
        except psutil.NoSuchProcess:
            continue
    return {"pixiv": {"crawler_pixiv": stopped}}

"""Prompt-index write helpers extracted from the main storage module."""

from __future__ import annotations

from db_compression import decompress_if_needed


def sync_prompt_fts(self, work_id: int) -> None:
    self.conn.execute("DELETE FROM prompt_fts WHERE work_id = ?", (work_id,))
    self.conn.execute("DELETE FROM prompt_work_fts WHERE work_id = ?", (work_id,))
    rows = self.conn.execute(
        "SELECT prompt_text, ai_json FROM work_images WHERE work_id = ?",
        (work_id,),
    ).fetchall()
    prompts: list[str] = []
    for row in rows:
        prompt = (
            row["prompt_text"]
            or decompress_if_needed(row["ai_json"])
            or ""
        ).strip()
        if prompt:
            prompts.append(prompt)
            self.conn.execute(
                "INSERT INTO prompt_fts(work_id, prompt_text) VALUES (?, ?)",
                (work_id, prompt),
            )
    if prompts:
        self.conn.execute(
            "INSERT INTO prompt_work_fts(work_id, prompt_text) VALUES (?, ?)",
            (work_id, "\n".join(prompts)),
        )


def prompt_search_table(self) -> str:
    # Read the flag fresh: another process (or rebuild_fts) may have rebuilt
    # or invalidated the index after this process cached readiness at open.
    try:
        ready = self.get_state("prompt_work_fts_ready", "0") == "1"
    except Exception:
        ready = bool(self._prompt_work_fts_ready)
    self._prompt_work_fts_ready = ready
    return "prompt_work_fts" if ready else "prompt_fts"


def rebuild_prompt_work_fts(self) -> int:
    """Build the one-document-per-work prompt index used by interactive search.

    Values are assembled in Python so compressed `ai_json` BLOBs (see
    db_compression) keep working as the fallback when `prompt_text` is empty.
    """
    with self._lock:
        self.conn.execute("DELETE FROM prompt_work_fts")
        prompts_by_work: dict[int, list[str]] = {}
        cursor = self.conn.execute(
            """
            SELECT work_id, prompt_text, ai_json
            FROM work_images
            WHERE TRIM(COALESCE(prompt_text, '')) <> ''
               OR TRIM(COALESCE(ai_json, '')) <> ''
            ORDER BY work_id, page_index
            """
        )
        for row in cursor:
            work_id = int(row["work_id"])
            prompt = (
                row["prompt_text"]
                or decompress_if_needed(row["ai_json"])
                or ""
            ).strip()
            if not prompt:
                continue
            prompts_by_work.setdefault(work_id, []).append(prompt)
        self.conn.executemany(
            "INSERT INTO prompt_work_fts(work_id, prompt_text) VALUES (?, ?)",
            [
                (work_id, "\n".join(prompts))
                for work_id, prompts in sorted(prompts_by_work.items())
            ],
        )
        count = len(prompts_by_work)
        self.conn.execute(
            "INSERT INTO crawl_state(key, value) VALUES('prompt_work_fts_ready', '1') "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        )
        self.conn.commit()
        self._prompt_work_fts_ready = True
        return count

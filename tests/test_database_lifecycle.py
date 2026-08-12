from __future__ import annotations

import gc
import tempfile
import threading
import unittest
from pathlib import Path

from db import Database


class DatabaseLifecycleTests(unittest.TestCase):
    def test_close_releases_reader_connections_created_by_worker_threads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "worker-reader.sqlite"
            db = Database(db_path)
            reader_ready = threading.Event()
            release_worker = threading.Event()

            def search_from_worker() -> None:
                db.search_works(page_size=1, skip_total=True)
                reader_ready.set()
                release_worker.wait(timeout=5)
                # Cleanup for the RED implementation so a failed assertion does
                # not leave the temporary SQLite file locked on Windows.
                reader = getattr(db._local, "reader_conn", None)
                if reader is not None:
                    reader.close()
                    db._local.reader_conn = None

            worker = threading.Thread(target=search_from_worker)
            worker.start()
            try:
                self.assertTrue(reader_ready.wait(timeout=5))
                db.close()
                # Database.close() is the public lifecycle boundary.  At this
                # point even readers created in another request thread must no
                # longer hold an OS handle or later emit ResourceWarning.
                db_path.unlink()
            finally:
                release_worker.set()
                worker.join(timeout=5)
                gc.collect()
                if db_path.exists():
                    db_path.unlink()


if __name__ == "__main__":
    unittest.main()

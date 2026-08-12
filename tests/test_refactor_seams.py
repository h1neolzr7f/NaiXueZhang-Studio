from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from aitag_core.storage.artifact_index import (
    artifact_rank,
    base_stem,
    build_artifact_index,
    find_artifact,
    pipeline_artifacts,
)


class ArtifactIndexSeamTests(unittest.TestCase):
    def test_base_stem_strips_pipeline_suffixes_in_order(self) -> None:
        self.assertEqual(base_stem("work_up2x_mosaic_clean"), "work")
        self.assertEqual(base_stem("work_final"), "work_final")

    def test_index_is_rebuilt_in_one_directory_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            names = (
                "work.png",
                "work_up2x.png",
                "work_up4x.png",
                "work_up4x_mosaic.png",
                "work_up4x_clean.png",
                "work_final.png",
                "work.txt",
            )
            for name in names:
                (root / name).write_bytes(b"x")

            index = build_artifact_index(root)

            self.assertEqual(
                set(index["work"]),
                {"upscale", "mosaic", "clean"},
            )
            self.assertEqual(index["work"]["upscale"][-1].name, "work_up4x.png")
            self.assertNotIn("work_final", index)

    def test_lookup_uses_index_or_scoped_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("work_up2x.png", "work_up2x_clean.png"):
                (root / name).write_bytes(b"x")

            index = build_artifact_index(root)
            self.assertEqual(
                find_artifact(root, "work", "clean", artifact_index=index).name,
                "work_up2x_clean.png",
            )
            self.assertEqual(
                find_artifact(root, "work", "upscale").name,
                "work_up2x.png",
            )

    def test_pipeline_artifacts_prioritize_clean_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("work_up2x.png", "work_up2x_mosaic.png", "work_up2x_clean.png"):
                (root / name).write_bytes(b"x")
            names = [path.name for path in pipeline_artifacts(root, "work")]
            self.assertEqual(names[0], "work_up2x_clean.png")
            self.assertGreater(artifact_rank(root / names[0]), artifact_rank(root / names[-1]))

if __name__ == "__main__":
    unittest.main()

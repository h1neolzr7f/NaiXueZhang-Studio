"""Rebuild codex taxonomy + previews without re-reading ANR source if DB is full.

Also re-imports from ANR when --source is given (recommended).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from import_codex_gallery import find_default_codex_root, import_codex  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force-preview", action="store_true", default=True)
    args = parser.parse_args()
    source = Path(args.source) if args.source else find_default_codex_root()
    if not source:
        print("ERROR: codex source not found")
        return 2
    result = import_codex(source, limit=args.limit, force_preview=bool(args.force_preview))
    import json

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

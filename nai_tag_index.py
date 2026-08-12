"""Deterministic SQLite facets for verified NovelAI prompt metadata."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from char_tag_db import (
    is_action_phrase,
    is_action_tag,
    is_character_tag,
    is_copyright_tag,
    is_framing_tag,
)
from db_compression import decompress_if_needed


FACETS = (
    "character",
    "copyright",
    "artist",
    "action",
    "clothing",
    "scene",
    "composition",
    "other",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS nai_tag_facets (
    work_id INTEGER NOT NULL,
    page_index INTEGER NOT NULL,
    tag_index INTEGER NOT NULL,
    facet TEXT NOT NULL,
    normalized_tag TEXT NOT NULL,
    display_tag TEXT NOT NULL,
    weight REAL NOT NULL,
    raw_syntax TEXT NOT NULL,
    syntax_type TEXT NOT NULL,
    derived INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (work_id, page_index, tag_index, facet)
);

CREATE INDEX IF NOT EXISTS idx_nai_tag_facets_popular
    ON nai_tag_facets(facet, normalized_tag, work_id, page_index);
CREATE INDEX IF NOT EXISTS idx_nai_tag_facets_work
    ON nai_tag_facets(work_id, page_index);
"""

_CHARACTER_SUFFIX = re.compile(r"^(.+?)\s+\(([^()]*)\)$")
_CLOTHING_WORDS = frozenset(
    {
        "apron",
        "armor",
        "bikini",
        "blazer",
        "boots",
        "bra",
        "cape",
        "coat",
        "dress",
        "gloves",
        "hat",
        "helmet",
        "hood",
        "jacket",
        "jeans",
        "kimono",
        "leotard",
        "pants",
        "panties",
        "pantyhose",
        "shirt",
        "shorts",
        "skirt",
        "socks",
        "stockings",
        "suit",
        "sweater",
        "swimsuit",
        "thighhighs",
        "uniform",
    }
)
_SCENE_WORDS = frozenset(
    {
        "alley",
        "beach",
        "bedroom",
        "building",
        "city",
        "classroom",
        "field",
        "forest",
        "garden",
        "indoors",
        "kitchen",
        "mountain",
        "ocean",
        "office",
        "outdoors",
        "park",
        "room",
        "school",
        "sky",
        "street",
        "underwater",
    }
)
_COMPOSITION_MARKERS = (
    "angle",
    "close up",
    "cowboy shot",
    "depth of field",
    "dutch angle",
    "fisheye",
    "focus",
    "from above",
    "from behind",
    "from below",
    "from side",
    "full body",
    "landscape",
    "looking at viewer",
    "perspective",
    "portrait",
    "pov",
    "shot",
    "upper body",
    "view",
)


def ensure_nai_tag_schema(connection) -> None:
    connection.executescript(SCHEMA)


def _normalize_tag(value: object) -> str:
    return " ".join(str(value or "").strip().replace("_", " ").casefold().split())


def _canonical_tags(value: object) -> list[dict[str, object]]:
    raw = decompress_if_needed(value)
    if not isinstance(raw, str):
        return []
    try:
        metadata = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(metadata, dict):
        return []
    local = metadata.get("_local")
    if not isinstance(local, dict) or not str(local.get("parser_version") or "").strip():
        return []
    tags = local.get("parsed_nai_tags")
    if not isinstance(tags, list):
        return []
    return [tag for tag in tags if isinstance(tag, dict)]


def _contains_word(tag: str, words: frozenset[str]) -> bool:
    tokens = set(re.findall(r"[a-z0-9]+", tag))
    return bool(tokens.intersection(words))


def _copyright_suffix(tag: str) -> str:
    match = _CHARACTER_SUFFIX.match(tag)
    if not match:
        return ""
    return _normalize_tag(match.group(2))


def _facet_for_tag(tag: str, copyright_hints: set[str]) -> str:
    if tag.startswith("artist:"):
        return "artist"
    if tag in copyright_hints or is_copyright_tag(tag):
        return "copyright"
    if is_character_tag(tag):
        return "character"
    if _contains_word(tag, _CLOTHING_WORDS):
        return "clothing"
    if _contains_word(tag, _SCENE_WORDS):
        return "scene"
    if is_framing_tag(tag) or any(marker in tag for marker in _COMPOSITION_MARKERS):
        return "composition"
    if is_action_tag(tag) or is_action_phrase(tag):
        return "action"
    return "other"


def _tag_rows(work_id: int, page_index: int, ai_json: object) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    tags = _canonical_tags(ai_json)
    copyright_hints = {
        suffix
        for item in tags
        if (suffix := _copyright_suffix(_normalize_tag(item.get("text"))))
    }
    for tag_index, item in enumerate(tags):
        display_tag = str(item.get("text") or "").strip()
        normalized_tag = _normalize_tag(display_tag)
        if not normalized_tag:
            continue
        facet = _facet_for_tag(normalized_tag, copyright_hints)
        try:
            weight = float(item.get("weight") or 1.0)
        except (TypeError, ValueError):
            weight = 1.0
        rows.append(
            (
                work_id,
                page_index,
                tag_index,
                facet,
                normalized_tag,
                display_tag,
                weight,
                str(item.get("raw_syntax") or display_tag),
                str(item.get("syntax_type") or "none"),
                0,
            )
        )
        derived_copyright = (
            _copyright_suffix(normalized_tag) if facet == "character" else ""
        )
        if derived_copyright:
            rows.append(
                (
                    work_id,
                    page_index,
                    tag_index,
                    "copyright",
                    derived_copyright,
                    derived_copyright,
                    weight,
                    str(item.get("raw_syntax") or display_tag),
                    str(item.get("syntax_type") or "none"),
                    1,
                )
            )
    return rows


def sync_work_nai_tag_index(self, work_id: int) -> int:
    """Replace one work's facets inside the caller's current transaction."""

    normalized_work_id = int(work_id)
    ensure_nai_tag_schema(self.conn)
    self.conn.execute(
        "DELETE FROM nai_tag_facets WHERE work_id = ?",
        (normalized_work_id,),
    )
    rows = self.conn.execute(
        """
        SELECT wi.page_index, wi.ai_json
        FROM work_images wi
        JOIN works w ON w.id = wi.work_id
        WHERE wi.work_id = ?
          AND LOWER(TRIM(COALESCE(w.ai_type, ''))) IN ('nai', 'nai_x')
        ORDER BY wi.page_index
        """,
        (normalized_work_id,),
    ).fetchall()
    rows_to_insert: list[tuple[object, ...]] = []
    for row in rows:
        rows_to_insert.extend(
            _tag_rows(
                normalized_work_id,
                int(row["page_index"] or 0),
                row["ai_json"],
            )
        )
    self.conn.executemany(
        """
        INSERT INTO nai_tag_facets(
            work_id, page_index, tag_index, facet, normalized_tag,
            display_tag, weight, raw_syntax, syntax_type, derived
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows_to_insert,
    )
    return len(rows_to_insert)


def rebuild_nai_tag_index(self) -> int:
    """Rebuild all facets from verified canonical metadata in the gallery DB."""

    def action() -> int:
        ensure_nai_tag_schema(self.conn)
        self.conn.execute("DELETE FROM nai_tag_facets")
        rows_to_insert: list[tuple[object, ...]] = []
        indexed_work_ids: set[int] = set()
        rows = self.conn.execute(
            """
            SELECT wi.work_id, wi.page_index, wi.ai_json
            FROM work_images wi
            JOIN works w ON w.id = wi.work_id
            WHERE LOWER(TRIM(COALESCE(w.ai_type, ''))) IN ('nai', 'nai_x')
            ORDER BY wi.work_id, wi.page_index
            """
        ).fetchall()
        for row in rows:
            tag_rows = _tag_rows(
                int(row["work_id"]),
                int(row["page_index"] or 0),
                row["ai_json"],
            )
            if tag_rows:
                indexed_work_ids.add(int(row["work_id"]))
                rows_to_insert.extend(tag_rows)
        self.conn.executemany(
            """
            INSERT INTO nai_tag_facets(
                work_id, page_index, tag_index, facet, normalized_tag,
                display_tag, weight, raw_syntax, syntax_type, derived
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows_to_insert,
        )
        self.conn.commit()
        return len(indexed_work_ids)

    return self._run(action)


def popular_nai_facets(
    self,
    *,
    facet: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    selected = str(facet or "").strip().casefold()
    if selected and selected not in FACETS:
        raise ValueError(f"unsupported NAI facet: {facet}")
    bounded_limit = max(1, min(int(limit), 500))
    where_sql = "WHERE facet = ?" if selected else ""
    params: list[object] = [selected] if selected else []
    rows = self._reader().execute(
        f"""
        SELECT facet, normalized_tag, MIN(display_tag) AS display_tag,
               COUNT(DISTINCT work_id) AS work_count,
               COUNT(DISTINCT CAST(work_id AS TEXT) || ':' || CAST(page_index AS TEXT)) AS page_count,
               MAX(weight) AS max_weight
        FROM nai_tag_facets
        {where_sql}
        GROUP BY facet, normalized_tag
        ORDER BY work_count DESC, page_count DESC, normalized_tag ASC
        LIMIT ?
        """,
        [*params, bounded_limit],
    ).fetchall()
    return [
        {
            "facet": str(row["facet"]),
            "tag": str(row["normalized_tag"]),
            "display_tag": str(row["display_tag"]),
            "work_count": int(row["work_count"]),
            "page_count": int(row["page_count"]),
            "max_weight": float(row["max_weight"]),
        }
        for row in rows
    ]


def build_nai_facet_filter(
    facets: Mapping[str, str | Sequence[str]] | None,
    *,
    works_alias: str = "works",
) -> tuple[list[str], list[object]]:
    """Build parameterized EXISTS clauses with OR-within/AND-across semantics."""

    if not facets:
        return [], []
    clauses: list[str] = []
    params: list[object] = []
    for index, (raw_facet, raw_values) in enumerate(facets.items()):
        facet = str(raw_facet or "").strip().casefold()
        if facet not in FACETS:
            raise ValueError(f"unsupported NAI facet: {raw_facet}")
        values = [raw_values] if isinstance(raw_values, str) else list(raw_values)
        normalized = list(
            dict.fromkeys(tag for value in values if (tag := _normalize_tag(value)))
        )
        if not normalized:
            continue
        alias = f"nai_facet_{index}"
        placeholders = ",".join("?" for _ in normalized)
        clauses.append(
            f"EXISTS (SELECT 1 FROM nai_tag_facets {alias} "
            f"WHERE {alias}.work_id = {works_alias}.id "
            f"AND {alias}.facet = ? "
            f"AND {alias}.normalized_tag IN ({placeholders}))"
        )
        params.extend((facet, *normalized))
    return clauses, params


def parse_nai_facet_selections(values: Sequence[str] | None) -> dict[str, list[str]]:
    selections: dict[str, list[str]] = {}
    for raw in values or ():
        facet, separator, tag = str(raw or "").partition(":")
        facet = facet.strip().casefold()
        normalized_tag = _normalize_tag(tag)
        if separator != ":" or facet not in FACETS or not normalized_tag:
            raise ValueError(f"invalid NAI facet selection: {raw}")
        bucket = selections.setdefault(facet, [])
        if normalized_tag not in bucket:
            bucket.append(normalized_tag)
    return selections

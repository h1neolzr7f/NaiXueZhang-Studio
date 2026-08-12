"""Local NAI character-reference catalog.

The catalog stores source records and their compiled NovelAI reference cards in
its own small SQLite database.  It deliberately does not share the gallery work
identity space and never calls NovelAI, a vision model, or an LLM.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from char_tag_db import classify_single_tag
from nai_anima_adapter import adapt_anima_character
from paths import data_dir

DEFAULT_SOURCE = "animadex"
MAX_IMPORT_RECORDS = 50_000
SCHEMA_VERSION = 2
REFERENCE_COMPILER_VERSION = 2

_TRAIT_FACETS = frozenset(
    {
        "identity",
        "appearance",
        "body",
        "hair",
        "eyes",
        "face",
        "clothing",
        "outfit",
        "accessories",
        "features",
        "species",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value: Any, *, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _loads(value: str, fallback: Any) -> Any:
    try:
        loaded = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback
    return loaded


def _source_key(value: Any) -> str:
    raw = _text(value, limit=80).lower().replace(" ", "-")
    clean = "".join(ch for ch in raw if ch.isalnum() or ch in {"-", "_", "."})
    return clean or DEFAULT_SOURCE


def _record_source_id(record: dict[str, Any]) -> str:
    role = record.get("role") if isinstance(record.get("role"), dict) else {}
    for value in (
        record.get("id"),
        record.get("slug"),
        record.get("character"),
        role.get("id"),
        role.get("slug"),
        record.get("trigger"),
    ):
        text = _text(value, limit=300)
        if text:
            return text
    return ""


def _normalize_record(
    record: dict[str, Any],
    *,
    source: str,
    version: str,
    license_name: str,
) -> dict[str, Any]:
    raw = dict(record)
    if not raw.get("slug") and raw.get("character"):
        raw["slug"] = raw.get("character")
    if not raw.get("core_tags") and raw.get("tags") is not None:
        raw["core_tags"] = raw.get("tags")
    if not raw.get("copyright") and raw.get("copyright_name"):
        raw["copyright"] = raw.get("copyright_name")
    raw["source"] = source
    if version:
        raw["version"] = version
    if license_name:
        raw["license"] = license_name
    return raw


def _reference_id(source: str, source_id: str) -> str:
    digest = hashlib.sha256(f"{source}\0{source_id}".encode("utf-8")).hexdigest()
    return f"ref_{digest[:24]}"


def _gender_from_card(card: dict[str, Any]) -> str:
    return {
        "1girl": "female",
        "1boy": "male",
        "1other": "other",
    }.get(_text(card.get("base_subject_tag"), limit=20), "unknown")


def _list_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = value.replace("\r", "\n").replace("|", ",").replace("\n", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = [value]
    return [text for item in values if (text := _text(item, limit=500))]


def _unique_values(values: Iterable[Any], *, limit: int = 100) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _text(value, limit=500)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _aliases_from_record(record: dict[str, Any], *, label: str, source_id: str, trigger: str) -> list[str]:
    role = record.get("role") if isinstance(record.get("role"), dict) else {}
    values: list[str] = []
    for key in ("aliases", "alias", "alt_names", "alternative_names", "other_names"):
        values.extend(_list_values(record.get(key)))
        values.extend(_list_values(role.get(key)))
    excluded = {item.casefold() for item in (label, source_id, trigger) if item}
    return [item for item in _unique_values(values, limit=40) if item.casefold() not in excluded]


def _nai_tag_text(value: Any) -> str:
    return " ".join(_text(value, limit=500).replace("_", " ").split())


def _derived_facets(
    record: dict[str, Any],
    card: dict[str, Any],
    *,
    label: str,
    source_id: str,
    trigger: str,
) -> tuple[list[str], list[dict[str, str]], list[dict[str, str]]]:
    aliases = _aliases_from_record(record, label=label, source_id=source_id, trigger=trigger)
    facets = record.get("facets") if isinstance(record.get("facets"), dict) else {}
    explicit: dict[str, str] = {}
    for raw_facet, raw_values in facets.items():
        facet = _text(raw_facet, limit=40).lower()
        if facet not in _TRAIT_FACETS:
            continue
        for raw_tag in _list_values(raw_values):
            tag = _nai_tag_text(raw_tag)
            if tag:
                explicit[tag.casefold()] = facet

    excluded = {
        _nai_tag_text(value).casefold()
        for value in ("girl", "boy", "other", "1girl", "1boy", "1other", trigger, record.get("copyright"), record.get("copyright_name"), record.get("series"))
        if _nai_tag_text(value)
    }
    traits: list[dict[str, str]] = []
    seen_traits: set[str] = set()
    for raw_tag in card.get("character_tags") or []:
        tag = _nai_tag_text(raw_tag)
        key = tag.casefold()
        if not tag or key in excluded or key in seen_traits:
            continue
        seen_traits.add(key)
        inferred = classify_single_tag(tag)
        facet = explicit.get(key) or {
            "identity": "identity",
            "body": "body",
            "appearance": "appearance",
            "creature": "species",
        }.get(inferred, "appearance")
        traits.append({"facet": facet, "trait": tag})

    styles: list[dict[str, str]] = []
    for tag in _unique_values(card.get("style_hints") or [], limit=40):
        clean = _nai_tag_text(tag)
        if clean:
            styles.append(
                {
                    "kind": "artist" if clean.casefold().startswith("artist:") else "style",
                    "tag": clean,
                }
            )
    return aliases, traits, styles


def _style_id(source: str, tag: str) -> str:
    digest = hashlib.sha256(f"{source}\0{tag.casefold()}".encode("utf-8")).hexdigest()
    return f"style_{digest[:24]}"


class ReferenceCatalog:
    """SQLite-backed store for NAI character reference cards."""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path else data_dir() / "reference_catalog.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._schema_lock = threading.RLock()
        self._ready = False
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @contextmanager
    def _connection(self):
        """Yield one transaction and always release its Windows file handle."""
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        if self._ready:
            return
        with self._schema_lock:
            if self._ready:
                return
            with self._connection() as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                current_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
                if current_version > SCHEMA_VERSION:
                    raise RuntimeError(
                        f"角色资料库版本 {current_version} 高于程序支持版本 {SCHEMA_VERSION}"
                    )
                conn.executescript(
                    """
                    BEGIN IMMEDIATE;
                    CREATE TABLE IF NOT EXISTS reference_sources (
                        source TEXT PRIMARY KEY,
                        label TEXT NOT NULL,
                        version TEXT NOT NULL DEFAULT '',
                        license TEXT NOT NULL DEFAULT '',
                        imported_at TEXT NOT NULL,
                        record_count INTEGER NOT NULL DEFAULT 0
                    );

                    CREATE TABLE IF NOT EXISTS character_references (
                        reference_id TEXT PRIMARY KEY,
                        source TEXT NOT NULL,
                        source_id TEXT NOT NULL,
                        fingerprint TEXT NOT NULL,
                        label TEXT NOT NULL,
                        normalized_label TEXT NOT NULL,
                        trigger TEXT NOT NULL DEFAULT '',
                        copyright TEXT NOT NULL DEFAULT '',
                        gender TEXT NOT NULL DEFAULT 'unknown',
                        thumb_url TEXT NOT NULL DEFAULT '',
                        image_url TEXT NOT NULL DEFAULT '',
                        popularity INTEGER NOT NULL DEFAULT 0,
                        character_caption TEXT NOT NULL,
                        base_subject_tag TEXT NOT NULL DEFAULT '',
                        model_dialect TEXT NOT NULL DEFAULT '',
                        style_hints_json TEXT NOT NULL DEFAULT '[]',
                        dropped_tags_json TEXT NOT NULL DEFAULT '[]',
                        provenance_json TEXT NOT NULL DEFAULT '{}',
                        raw_json TEXT NOT NULL,
                        search_text TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(source, source_id),
                        FOREIGN KEY(source) REFERENCES reference_sources(source)
                    );

                    CREATE INDEX IF NOT EXISTS idx_reference_label
                        ON character_references(normalized_label);
                    CREATE INDEX IF NOT EXISTS idx_reference_filters
                        ON character_references(source, gender, copyright);
                    CREATE INDEX IF NOT EXISTS idx_reference_popularity
                        ON character_references(popularity DESC, label);

                    CREATE TABLE IF NOT EXISTS reference_import_receipts (
                        receipt_id TEXT PRIMARY KEY,
                        source TEXT NOT NULL,
                        version TEXT NOT NULL DEFAULT '',
                        license TEXT NOT NULL DEFAULT '',
                        started_at TEXT NOT NULL,
                        completed_at TEXT NOT NULL,
                        inserted INTEGER NOT NULL DEFAULT 0,
                        updated INTEGER NOT NULL DEFAULT 0,
                        unchanged INTEGER NOT NULL DEFAULT 0,
                        rejected INTEGER NOT NULL DEFAULT 0,
                        rejected_json TEXT NOT NULL DEFAULT '[]'
                    );

                    CREATE TABLE IF NOT EXISTS reference_aliases (
                        reference_id TEXT NOT NULL,
                        alias TEXT NOT NULL,
                        normalized_alias TEXT NOT NULL,
                        position INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY(reference_id, normalized_alias),
                        FOREIGN KEY(reference_id) REFERENCES character_references(reference_id) ON DELETE CASCADE
                    );
                    CREATE INDEX IF NOT EXISTS idx_reference_alias
                        ON reference_aliases(normalized_alias);

                    CREATE TABLE IF NOT EXISTS reference_traits (
                        reference_id TEXT NOT NULL,
                        facet TEXT NOT NULL,
                        trait TEXT NOT NULL,
                        normalized_trait TEXT NOT NULL,
                        position INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY(reference_id, facet, normalized_trait),
                        FOREIGN KEY(reference_id) REFERENCES character_references(reference_id) ON DELETE CASCADE
                    );
                    CREATE INDEX IF NOT EXISTS idx_reference_trait
                        ON reference_traits(normalized_trait, facet);

                    CREATE TABLE IF NOT EXISTS style_references (
                        style_id TEXT PRIMARY KEY,
                        source TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        label TEXT NOT NULL,
                        normalized_tag TEXT NOT NULL,
                        tag TEXT NOT NULL,
                        provenance_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(source, normalized_tag),
                        FOREIGN KEY(source) REFERENCES reference_sources(source)
                    );
                    CREATE INDEX IF NOT EXISTS idx_style_reference_tag
                        ON style_references(normalized_tag, kind);

                    CREATE TABLE IF NOT EXISTS reference_style_links (
                        reference_id TEXT NOT NULL,
                        style_id TEXT NOT NULL,
                        position INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY(reference_id, style_id),
                        FOREIGN KEY(reference_id) REFERENCES character_references(reference_id) ON DELETE CASCADE,
                        FOREIGN KEY(style_id) REFERENCES style_references(style_id) ON DELETE CASCADE
                    );
                    """
                )
                if current_version < 2:
                    self._backfill_derived_facets(conn)
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self._ready = True

    @staticmethod
    def _replace_derived_facets(
        conn: sqlite3.Connection,
        *,
        reference_id: str,
        source: str,
        record: dict[str, Any],
        card: dict[str, Any],
        label: str,
        source_id: str,
        trigger: str,
        now: str,
    ) -> tuple[list[str], list[dict[str, str]], list[dict[str, str]]]:
        aliases, traits, styles = _derived_facets(
            record,
            card,
            label=label,
            source_id=source_id,
            trigger=trigger,
        )
        conn.execute("DELETE FROM reference_aliases WHERE reference_id=?", (reference_id,))
        conn.execute("DELETE FROM reference_traits WHERE reference_id=?", (reference_id,))
        conn.execute("DELETE FROM reference_style_links WHERE reference_id=?", (reference_id,))
        conn.executemany(
            "INSERT INTO reference_aliases(reference_id, alias, normalized_alias, position) VALUES (?, ?, ?, ?)",
            [(reference_id, alias, alias.casefold(), index) for index, alias in enumerate(aliases)],
        )
        conn.executemany(
            "INSERT INTO reference_traits(reference_id, facet, trait, normalized_trait, position) VALUES (?, ?, ?, ?, ?)",
            [
                (reference_id, item["facet"], item["trait"], item["trait"].casefold(), index)
                for index, item in enumerate(traits)
            ],
        )
        provenance = _json(card.get("provenance") or {})
        for index, item in enumerate(styles):
            tag = item["tag"]
            style_id = _style_id(source, tag)
            conn.execute(
                """
                INSERT INTO style_references(
                    style_id, source, kind, label, normalized_tag, tag,
                    provenance_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, normalized_tag) DO UPDATE SET
                    kind=excluded.kind,
                    label=excluded.label,
                    tag=excluded.tag,
                    provenance_json=excluded.provenance_json,
                    updated_at=excluded.updated_at
                """,
                (
                    style_id,
                    source,
                    item["kind"],
                    tag.removeprefix("artist:").strip() or tag,
                    tag.casefold(),
                    tag,
                    provenance,
                    now,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO reference_style_links(reference_id, style_id, position) VALUES (?, ?, ?)",
                (reference_id, style_id, index),
            )
        return aliases, traits, styles

    def _backfill_derived_facets(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            "SELECT reference_id, source, source_id, label, trigger, raw_json, model_dialect FROM character_references"
        ).fetchall()
        for row in rows:
            raw = _loads(row["raw_json"], {})
            card = adapt_anima_character(raw, model=str(row["model_dialect"] or ""))
            aliases, traits, styles = self._replace_derived_facets(
                conn,
                reference_id=str(row["reference_id"]),
                source=str(row["source"]),
                record=raw,
                card=card,
                label=str(row["label"]),
                source_id=str(row["source_id"]),
                trigger=str(row["trigger"]),
                now=_now(),
            )
            searchable = " ".join(
                [
                    str(row["label"]),
                    str(row["source_id"]),
                    str(row["trigger"]),
                    str(card.get("character_caption") or ""),
                    *aliases,
                    *(item["trait"] for item in traits),
                ]
            ).casefold()[:20_000]
            conn.execute(
                "UPDATE character_references SET search_text=? WHERE reference_id=?",
                (searchable, str(row["reference_id"])),
            )

    def import_records(
        self,
        records: Iterable[dict[str, Any]],
        *,
        source: str = DEFAULT_SOURCE,
        source_label: str = "",
        version: str = "",
        license_name: str = "",
        model: str = "",
    ) -> dict[str, Any]:
        rows = list(records)
        if not rows:
            raise ValueError("至少需要一条角色资料")
        if len(rows) > MAX_IMPORT_RECORDS:
            raise ValueError(f"单次最多导入 {MAX_IMPORT_RECORDS} 条角色资料")
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError("每条角色资料都必须是对象")

        source_key = _source_key(source)
        label = _text(source_label or source_key, limit=120)
        version_text = _text(version, limit=120)
        license_text = _text(license_name, limit=120)
        started_at = _now()
        receipt_id = f"import_{uuid.uuid4().hex}"
        counts = {"inserted": 0, "updated": 0, "unchanged": 0, "rejected": 0}
        rejected: list[dict[str, Any]] = []

        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO reference_sources(source, label, version, license, imported_at, record_count)
                VALUES (?, ?, ?, ?, ?, 0)
                ON CONFLICT(source) DO UPDATE SET
                    label=excluded.label,
                    version=CASE WHEN excluded.version <> '' THEN excluded.version ELSE reference_sources.version END,
                    license=CASE WHEN excluded.license <> '' THEN excluded.license ELSE reference_sources.license END,
                    imported_at=excluded.imported_at
                """,
                (source_key, label, version_text, license_text, started_at),
            )
            for index, record in enumerate(rows):
                raw = _normalize_record(
                    record,
                    source=source_key,
                    version=version_text,
                    license_name=license_text,
                )
                source_id = _record_source_id(raw)
                card = adapt_anima_character(raw, model=model)
                caption = _text(card.get("character_caption"), limit=8000)
                if not source_id or not caption:
                    counts["rejected"] += 1
                    if len(rejected) < 50:
                        rejected.append(
                            {
                                "index": index,
                                "source_id": source_id,
                                "reason": "缺少稳定 ID 或可用的 NAI 角色标签",
                            }
                        )
                    continue

                raw_json = _json(raw)
                fingerprint = hashlib.sha256(
                    f"{REFERENCE_COMPILER_VERSION}\0{model}\0{raw_json}".encode("utf-8")
                ).hexdigest()
                existing = conn.execute(
                    "SELECT fingerprint FROM character_references WHERE source=? AND source_id=?",
                    (source_key, source_id),
                ).fetchone()
                if existing and existing["fingerprint"] == fingerprint:
                    counts["unchanged"] += 1
                    continue

                reference_id = _reference_id(source_key, source_id)
                name = _text(card.get("label") or raw.get("name") or source_id, limit=300)
                trigger = _text(raw.get("trigger") or raw.get("trigger_tag"), limit=1000)
                copyright_name = _text(
                    raw.get("copyright_name") or raw.get("copyright") or raw.get("series"),
                    limit=300,
                )
                thumb_url = _text(raw.get("thumb_url") or raw.get("thumbnail") or raw.get("thumb"), limit=2000)
                image_url = _text(raw.get("img_url") or raw.get("image_url") or raw.get("image"), limit=2000)
                try:
                    popularity = max(0, int(raw.get("count") or raw.get("popularity") or 0))
                except (TypeError, ValueError):
                    popularity = 0
                search_text = " ".join(
                    [name, source_id, trigger, copyright_name, caption]
                ).casefold()[:20_000]
                now = _now()
                conn.execute(
                    """
                    INSERT INTO character_references(
                        reference_id, source, source_id, fingerprint, label, normalized_label,
                        trigger, copyright, gender, thumb_url, image_url, popularity,
                        character_caption, base_subject_tag, model_dialect,
                        style_hints_json, dropped_tags_json, provenance_json, raw_json,
                        search_text, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(source, source_id) DO UPDATE SET
                        fingerprint=excluded.fingerprint,
                        label=excluded.label,
                        normalized_label=excluded.normalized_label,
                        trigger=excluded.trigger,
                        copyright=excluded.copyright,
                        gender=excluded.gender,
                        thumb_url=excluded.thumb_url,
                        image_url=excluded.image_url,
                        popularity=excluded.popularity,
                        character_caption=excluded.character_caption,
                        base_subject_tag=excluded.base_subject_tag,
                        model_dialect=excluded.model_dialect,
                        style_hints_json=excluded.style_hints_json,
                        dropped_tags_json=excluded.dropped_tags_json,
                        provenance_json=excluded.provenance_json,
                        raw_json=excluded.raw_json,
                        search_text=excluded.search_text,
                        updated_at=excluded.updated_at
                    """,
                    (
                        reference_id,
                        source_key,
                        source_id,
                        fingerprint,
                        name,
                        name.casefold(),
                        trigger,
                        copyright_name,
                        _gender_from_card(card),
                        thumb_url,
                        image_url,
                        popularity,
                        caption,
                        _text(card.get("base_subject_tag"), limit=20),
                        _text(card.get("model_dialect"), limit=40),
                        "[]",
                        _json(card.get("dropped_tags") or []),
                        _json(card.get("provenance") or {}),
                        raw_json,
                        search_text,
                        now,
                        now,
                    ),
                )
                aliases, traits, styles = self._replace_derived_facets(
                    conn,
                    reference_id=reference_id,
                    source=source_key,
                    record=raw,
                    card=card,
                    label=name,
                    source_id=source_id,
                    trigger=trigger,
                    now=now,
                )
                search_text = " ".join(
                    [
                        name,
                        source_id,
                        trigger,
                        copyright_name,
                        caption,
                        *aliases,
                        *(item["trait"] for item in traits),
                    ]
                ).casefold()[:20_000]
                conn.execute(
                    "UPDATE character_references SET search_text=? WHERE reference_id=?",
                    (search_text, reference_id),
                )
                counts["updated" if existing else "inserted"] += 1

            total = int(
                conn.execute(
                    "SELECT COUNT(*) FROM character_references WHERE source=?",
                    (source_key,),
                ).fetchone()[0]
            )
            completed_at = _now()
            conn.execute(
                "UPDATE reference_sources SET record_count=?, imported_at=? WHERE source=?",
                (total, completed_at, source_key),
            )
            conn.execute(
                """
                INSERT INTO reference_import_receipts(
                    receipt_id, source, version, license, started_at, completed_at,
                    inserted, updated, unchanged, rejected, rejected_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    receipt_id,
                    source_key,
                    version_text,
                    license_text,
                    started_at,
                    completed_at,
                    counts["inserted"],
                    counts["updated"],
                    counts["unchanged"],
                    counts["rejected"],
                    _json(rejected),
                ),
            )
            conn.execute(
                """
                DELETE FROM style_references
                WHERE source=? AND NOT EXISTS (
                    SELECT 1 FROM reference_style_links links
                    WHERE links.style_id=style_references.style_id
                )
                """,
                (source_key,),
            )

        return {
            "ok": True,
            "receipt_id": receipt_id,
            "source": source_key,
            "source_total": total,
            **counts,
            "rejected_items": rejected,
            "schema_version": SCHEMA_VERSION,
            "compiler_version": REFERENCE_COMPILER_VERSION,
            "message": (
                f"已导入 {counts['inserted']} 条，更新 {counts['updated']} 条，"
                f"跳过未变化 {counts['unchanged']} 条，拒绝 {counts['rejected']} 条"
            ),
        }

    def search(
        self,
        *,
        query: str = "",
        gender: str = "",
        copyright_name: str = "",
        source: str = "",
        limit: int = 60,
        offset: int = 0,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 100))
        safe_offset = max(0, int(offset))
        where: list[str] = []
        params: list[Any] = []
        q = _text(query, limit=200).casefold()
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("search_text LIKE ? ESCAPE '\\'")
            params.append(f"%{escaped}%")
        if gender:
            where.append("gender=?")
            params.append(_text(gender, limit=20).lower())
        if copyright_name:
            where.append("copyright=?")
            params.append(_text(copyright_name, limit=300))
        if source:
            where.append("source=?")
            params.append(_source_key(source))
        clause = " WHERE " + " AND ".join(where) if where else ""
        with self._connection() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) FROM character_references{clause}", params).fetchone()[0])
            rows = conn.execute(
                f"""
                SELECT reference_id, source, source_id, label, trigger, copyright, gender,
                       thumb_url, image_url, popularity, character_caption, base_subject_tag,
                       model_dialect, updated_at
                FROM character_references{clause}
                ORDER BY popularity DESC, normalized_label ASC
                LIMIT ? OFFSET ?
                """,
                [*params, safe_limit, safe_offset],
            ).fetchall()
        return {
            "ok": True,
            "items": [dict(row) for row in rows],
            "total": total,
            "limit": safe_limit,
            "offset": safe_offset,
            "has_more": safe_offset + len(rows) < total,
        }

    def get(self, reference_id: str) -> dict[str, Any] | None:
        ref = _text(reference_id, limit=80)
        if not ref.startswith("ref_"):
            return None
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM character_references WHERE reference_id=?",
                (ref,),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item.pop("style_hints_json", None)
        item["dropped_tags"] = _loads(item.pop("dropped_tags_json"), [])
        item["provenance"] = _loads(item.pop("provenance_json"), {})
        item["raw"] = _loads(item.pop("raw_json"), {})
        with self._connection() as conn:
            item["aliases"] = [
                str(row["alias"])
                for row in conn.execute(
                    "SELECT alias FROM reference_aliases WHERE reference_id=? ORDER BY position, alias",
                    (ref,),
                ).fetchall()
            ]
            item["traits"] = [
                {"facet": str(row["facet"]), "trait": str(row["trait"])}
                for row in conn.execute(
                    "SELECT facet, trait FROM reference_traits WHERE reference_id=? ORDER BY position, trait",
                    (ref,),
                ).fetchall()
            ]
        item.pop("search_text", None)
        item.pop("fingerprint", None)
        item.pop("normalized_label", None)
        return item

    def search_styles(
        self,
        *,
        query: str = "",
        kind: str = "",
        source: str = "",
        limit: int = 60,
        offset: int = 0,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 100))
        safe_offset = max(0, int(offset))
        where: list[str] = []
        params: list[Any] = []
        q = _text(query, limit=200).casefold()
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("(styles.normalized_tag LIKE ? ESCAPE '\\' OR lower(styles.label) LIKE ? ESCAPE '\\')")
            params.extend((f"%{escaped}%", f"%{escaped}%"))
        kind_text = _text(kind, limit=20).lower()
        if kind_text:
            if kind_text not in {"artist", "style"}:
                raise ValueError("画风资料类型只支持 artist 或 style")
            where.append("styles.kind=?")
            params.append(kind_text)
        if source:
            where.append("styles.source=?")
            params.append(_source_key(source))
        clause = " WHERE " + " AND ".join(where) if where else ""
        with self._connection() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM style_references styles{clause}",
                    params,
                ).fetchone()[0]
            )
            rows = conn.execute(
                f"""
                SELECT styles.style_id, styles.source, styles.kind, styles.label, styles.tag,
                       styles.provenance_json, COUNT(links.reference_id) AS linked_characters
                FROM style_references styles
                LEFT JOIN reference_style_links links ON links.style_id=styles.style_id
                {clause}
                GROUP BY styles.style_id
                ORDER BY linked_characters DESC, styles.label ASC
                LIMIT ? OFFSET ?
                """,
                [*params, safe_limit, safe_offset],
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["provenance"] = _loads(item.pop("provenance_json"), {})
            items.append(item)
        return {
            "ok": True,
            "items": items,
            "total": total,
            "limit": safe_limit,
            "offset": safe_offset,
            "has_more": safe_offset + len(items) < total,
            "local_only": True,
            "generation_calls": 0,
            "schema_version": SCHEMA_VERSION,
            "compiler_version": REFERENCE_COMPILER_VERSION,
        }

    def get_style(self, style_id: str) -> dict[str, Any] | None:
        """Return one independent style reference without crossing into character data."""

        ref = _text(style_id, limit=80)
        if not ref.startswith("style_"):
            return None
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT styles.style_id, styles.source, styles.kind, styles.label, styles.tag,
                       styles.provenance_json, COUNT(links.reference_id) AS linked_characters
                FROM style_references styles
                LEFT JOIN reference_style_links links ON links.style_id=styles.style_id
                WHERE styles.style_id=?
                GROUP BY styles.style_id
                """,
                (ref,),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["provenance"] = _loads(item.pop("provenance_json"), {})
        return item

    def related_styles(self, reference_id: str) -> dict[str, Any]:
        ref = _text(reference_id, limit=80)
        if not ref.startswith("ref_"):
            return {"ok": True, "reference_id": ref, "items": [], "total": 0}
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT styles.style_id, styles.source, styles.kind, styles.label, styles.tag,
                       styles.provenance_json
                FROM reference_style_links links
                JOIN style_references styles ON styles.style_id=links.style_id
                WHERE links.reference_id=?
                ORDER BY links.position, styles.label
                """,
                (ref,),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["provenance"] = _loads(item.pop("provenance_json"), {})
            items.append(item)
        return {
            "ok": True,
            "reference_id": ref,
            "items": items,
            "total": len(items),
            "local_only": True,
            "generation_calls": 0,
            "schema_version": SCHEMA_VERSION,
            "compiler_version": REFERENCE_COMPILER_VERSION,
        }

    def stats(self) -> dict[str, Any]:
        with self._connection() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM character_references").fetchone()[0])
            sources = [dict(row) for row in conn.execute(
                "SELECT source, label, version, license, imported_at, record_count "
                "FROM reference_sources ORDER BY label"
            ).fetchall()]
            genders = {
                row["gender"]: int(row["count"])
                for row in conn.execute(
                    "SELECT gender, COUNT(*) AS count FROM character_references GROUP BY gender"
                ).fetchall()
            }
            copyrights = [dict(row) for row in conn.execute(
                """
                SELECT copyright AS name, COUNT(*) AS count
                FROM character_references
                WHERE copyright <> ''
                GROUP BY copyright
                ORDER BY count DESC, copyright ASC
                LIMIT 80
                """
            ).fetchall()]
            receipts = [dict(row) for row in conn.execute(
                """
                SELECT receipt_id, source, version, completed_at, inserted, updated, unchanged, rejected
                FROM reference_import_receipts
                ORDER BY completed_at DESC LIMIT 10
                """
            ).fetchall()]
            trait_facets = [dict(row) for row in conn.execute(
                """
                SELECT facet, COUNT(*) AS count
                FROM reference_traits
                GROUP BY facet
                ORDER BY count DESC, facet ASC
                """
            ).fetchall()]
            style_total = int(conn.execute("SELECT COUNT(*) FROM style_references").fetchone()[0])
            style_references = [dict(row) for row in conn.execute(
                """
                SELECT styles.style_id, styles.kind, styles.label, styles.tag,
                       COUNT(links.reference_id) AS linked_characters
                FROM style_references styles
                LEFT JOIN reference_style_links links ON links.style_id=styles.style_id
                GROUP BY styles.style_id
                ORDER BY linked_characters DESC, styles.label ASC
                LIMIT 80
                """
            ).fetchall()]
        return {
            "ok": True,
            "total": total,
            "sources": sources,
            "genders": genders,
            "copyrights": copyrights,
            "recent_imports": receipts,
            "trait_facets": trait_facets,
            "style_total": style_total,
            "style_references": style_references,
            "local_only": True,
            "generation_calls": 0,
            "schema_version": SCHEMA_VERSION,
            "compiler_version": REFERENCE_COMPILER_VERSION,
        }

    def close(self) -> None:
        """Compatibility hook for process resource cleanup.

        Connections are short-lived per operation, so there is no persistent
        connection to close.
        """


_DEFAULT_CATALOG: ReferenceCatalog | None = None
_DEFAULT_LOCK = threading.Lock()


def get_reference_catalog() -> ReferenceCatalog:
    global _DEFAULT_CATALOG
    if _DEFAULT_CATALOG is None:
        with _DEFAULT_LOCK:
            if _DEFAULT_CATALOG is None:
                _DEFAULT_CATALOG = ReferenceCatalog()
    return _DEFAULT_CATALOG


def _close_default() -> None:
    if _DEFAULT_CATALOG is not None:
        _DEFAULT_CATALOG.close()


atexit.register(_close_default)

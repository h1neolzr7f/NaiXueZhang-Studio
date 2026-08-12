from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

from PIL import Image, PngImagePlugin

from db import Database
from db_compression import compress_text
from pixiv_nai_intake import PixivNAIIntake, PixivPage, PixivWork


def _canonical_metadata(*tags: dict[str, object]) -> dict[str, object]:
    return {
        "Software": "NovelAI",
        "Source": "NovelAI Diffusion V4.5",
        "Description": ", ".join(str(tag["text"]) for tag in tags),
        "Comment": {"prompt": ", ".join(str(tag["text"]) for tag in tags)},
        "_local": {
            "parser_version": "test-parser-v1",
            "parsed_nai_tags": list(tags),
        },
    }


def _tag(text: str, *, weight: float = 1.0, raw: str | None = None) -> dict[str, object]:
    return {
        "text": text,
        "weight": weight,
        "raw_syntax": raw or text,
        "syntax_type": "numeric" if raw and "::" in raw else "none",
    }


def _insert_verified_work(
    db: Database,
    work_id: int,
    *tags: dict[str, object],
) -> None:
    item = {
        "id": work_id,
        "title": f"work-{work_id}",
        "AI_type": "NAI",
        "create_date": "2026-08-02T00:00:00Z",
        "image_count": 1,
    }
    db.conn.execute(
        """
        INSERT INTO works(id, title, ai_type, create_date, image_count, list_json)
        VALUES(?, ?, 'NAI', ?, 1, ?)
        """,
        (work_id, item["title"], item["create_date"], json.dumps(item)),
    )
    db.conn.execute(
        """
        INSERT INTO work_images(work_id, page_index, ai_json, downloaded)
        VALUES(?, 0, ?, 1)
        """,
        (work_id, compress_text(json.dumps(_canonical_metadata(*tags)))),
    )


def _write_verified_png(path: Path, prompt: str) -> None:
    png_info = PngImagePlugin.PngInfo()
    png_info.add_text("Software", "NovelAI")
    png_info.add_text("Source", "NovelAI Diffusion V4.5")
    png_info.add_text("Description", prompt)
    png_info.add_text("Comment", json.dumps({"prompt": prompt}, ensure_ascii=False))
    Image.new("RGB", (32, 48), (30, 40, 50)).save(path, pnginfo=png_info)


def test_verified_legacy_nai_metadata_builds_popular_artist_facet(tmp_path: Path) -> None:
    with Database(tmp_path / "gallery.sqlite") as db:
        db.conn.execute(
            "INSERT INTO works(id, ai_type) VALUES(?, ?)",
            (101, "NAI"),
        )
        db.conn.execute(
            """
            INSERT INTO work_images(work_id, page_index, ai_json, downloaded)
            VALUES(?, ?, ?, 1)
            """,
            (
                101,
                0,
                compress_text(
                    json.dumps(
                        _canonical_metadata(
                            _tag(
                                "artist:Foo_Bar",
                                weight=1.25,
                                raw="1.25::artist:Foo_Bar::",
                            )
                        ),
                        ensure_ascii=False,
                    )
                ),
            ),
        )
        db.conn.commit()

        assert db.rebuild_nai_tag_index() == 1
        assert db.popular_nai_facets(facet="artist") == [
            {
                "facet": "artist",
                "tag": "artist:foo bar",
                "display_tag": "artist:Foo_Bar",
                "work_count": 1,
                "page_count": 1,
                "max_weight": 1.25,
            }
        ]


def test_verified_nai_tags_are_normalized_into_domain_facets(tmp_path: Path) -> None:
    tags = (
        _tag("skadi_(arknights)"),
        _tag("arknights"),
        _tag("artist:Foo_Bar"),
        _tag("standing"),
        _tag("school_uniform"),
        _tag("outdoors"),
        _tag("cowboy_shot"),
        _tag("masterpiece"),
    )
    with Database(tmp_path / "gallery.sqlite") as db:
        db.conn.execute("INSERT INTO works(id, ai_type) VALUES(?, ?)", (102, "NAI"))
        db.conn.execute(
            """
            INSERT INTO work_images(work_id, page_index, ai_json, downloaded)
            VALUES(?, ?, ?, 1)
            """,
            (102, 0, compress_text(json.dumps(_canonical_metadata(*tags))),),
        )
        db.conn.commit()

        db.rebuild_nai_tag_index()

        assert {
            facet: [row["tag"] for row in db.popular_nai_facets(facet=facet)]
            for facet in (
                "character",
                "copyright",
                "artist",
                "action",
                "clothing",
                "scene",
                "composition",
                "other",
            )
        } == {
            "character": ["skadi (arknights)"],
            "copyright": ["arknights"],
            "artist": ["artist:foo bar"],
            "action": ["standing"],
            "clothing": ["school uniform"],
            "scene": ["outdoors"],
            "composition": ["cowboy shot"],
            "other": ["masterpiece"],
        }


def test_gallery_search_filters_by_multiple_nai_facets(tmp_path: Path) -> None:
    with Database(tmp_path / "gallery.sqlite") as db:
        _insert_verified_work(db, 201, _tag("skadi_(arknights)"), _tag("outdoors"))
        _insert_verified_work(db, 202, _tag("skadi_(arknights)"), _tag("indoors"))
        _insert_verified_work(db, 203, _tag("amiya_(arknights)"), _tag("outdoors"))
        db.conn.commit()
        db.rebuild_nai_tag_index()

        result = db.search_works(
            nai_only=True,
            nai_facets={
                "character": ["Skadi_(Arknights)"],
                "scene": "outdoors",
            },
        )

        assert [work["id"] for work in result["items"]] == [201]
        assert result["total"] == 1

        same_facet_or = db.search_works(
            nai_only=True,
            nai_facets={
                "character": ["Skadi_(Arknights)", "Amiya_(Arknights)"],
                "scene": "outdoors",
            },
        )
        assert [work["id"] for work in same_facet_or["items"]] == [203, 201]
        assert same_facet_or["total"] == 2


def test_pixiv_intake_updates_tag_facets_without_manual_rebuild(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    _write_verified_png(source, "skadi_(arknights), outdoors")
    work = PixivWork(
        work_id=301,
        user_id=30,
        user_name="artist",
        title="verified",
        caption="",
        tags=(),
        create_date="2026-08-02T00:00:00Z",
        total_view=0,
        total_bookmarks=0,
        pages=(PixivPage(0, "https://i.pximg.test/301_p0.png"),),
    )

    with Database(tmp_path / "gallery.sqlite") as db:
        intake = PixivNAIIntake(
            db=db,
            images_dir=tmp_path / "images",
            staging_dir=tmp_path / "staging",
            allowed_image_hosts=("i.pximg.test",),
        )

        receipt = intake.ingest_work(
            work,
            lambda _url, target: shutil.copyfile(source, target),
        )

        assert receipt.status == "accepted"
        assert [
            row["tag"] for row in db.popular_nai_facets(facet="character")
        ] == ["skadi (arknights)"]


def test_pixiv_replacement_without_verified_pages_removes_stale_facets(
    tmp_path: Path,
) -> None:
    verified = tmp_path / "verified.png"
    rejected = tmp_path / "plain.png"
    _write_verified_png(verified, "skadi_(arknights), outdoors")
    Image.new("RGB", (32, 48), (80, 40, 20)).save(rejected)
    first_url = "https://i.pximg.test/302_p0.png"
    replacement_url = "https://i.pximg.test/302_replacement_p0.png"
    work = PixivWork(
        work_id=302,
        user_id=30,
        user_name="artist",
        title="verified",
        caption="",
        tags=(),
        create_date="2026-08-02T00:00:00Z",
        total_view=0,
        total_bookmarks=0,
        pages=(PixivPage(0, first_url),),
    )
    sources = {first_url: verified, replacement_url: rejected}

    with Database(tmp_path / "gallery.sqlite") as db:
        intake = PixivNAIIntake(
            db=db,
            images_dir=tmp_path / "images",
            staging_dir=tmp_path / "staging",
            allowed_image_hosts=("i.pximg.test",),
        )
        intake.ingest_work(
            work,
            lambda url, target: shutil.copyfile(sources[url], target),
        )
        assert db.popular_nai_facets(facet="character")

        receipt = intake.ingest_work(
            replace(work, pages=(PixivPage(0, replacement_url),)),
            lambda url, target: shutil.copyfile(sources[url], target),
        )

        assert receipt.status == "rejected"
        assert db.popular_nai_facets(facet="character") == []

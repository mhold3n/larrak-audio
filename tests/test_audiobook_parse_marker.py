from __future__ import annotations

import json
from pathlib import Path

from larrak_audio.parse_marker import build_assets_and_chapters


def test_marker_parser_maps_assets_to_pages_and_chapters(tmp_path: Path) -> None:
    marker_dir = tmp_path / "marker"
    marker_dir.mkdir(parents=True)

    md_path = tmp_path / "source.md"
    md_path.write_text(
        "\n".join(
            [
                "# Intro",
                "Some text before visual.",
                "![](_page_3_Figure_1.jpeg)",
                "",
                "## Methods",
                "| ColA | ColB |",
                "|---|---|",
                "| 1 | 2 |",
                "![](_page_7_Figure_2.jpeg)",
            ]
        ),
        encoding="utf-8",
    )

    (marker_dir / "_page_3_Figure_1.jpeg").write_bytes(b"jpeg")
    (marker_dir / "_page_7_Figure_2.jpeg").write_bytes(b"jpeg")

    (marker_dir / "sample_meta.json").write_text(
        json.dumps(
            {
                "table_of_contents": [
                    {"title": "Intro", "page_id": 2},
                    {"title": "Methods", "page_id": 6},
                ]
            }
        ),
        encoding="utf-8",
    )
    (marker_dir / "blocks.json").write_text(
        json.dumps([
            {"block_type": "20", "block_id": 10, "page_id": 9},
            {"block_type": "23", "block_id": 11, "page_id": 9},
        ]),
        encoding="utf-8",
    )

    assets, chapters = build_assets_and_chapters(md_path, marker_dir, source_id="s1")

    assert len(assets) == 3
    assert assets[0].page_id == 3
    assert assets[1].page_id == 7
    assert assets[2].page_id == 9

    assert len(chapters) == 2
    assert chapters[0].title == "Intro"
    assert chapters[1].title == "Methods"
    assert chapters[0].page_start == 2
    assert chapters[0].page_end == 3
    assert chapters[1].page_start == 6
    assert chapters[1].page_end == 9
    assert chapters[1].asset_refs


def test_marker_parser_finds_nested_marker_artifacts_from_bundle_root(tmp_path: Path) -> None:
    marker_root = tmp_path / "marker"
    artifact_dir = marker_root / "doc"
    artifact_dir.mkdir(parents=True)

    md_path = marker_root / "source.md"
    md_path.write_text(
        "\n".join(
            [
                "# Doc",
                "![](_page_3_Figure_1.jpeg)",
            ]
        ),
        encoding="utf-8",
    )

    (artifact_dir / "_page_3_Figure_1.jpeg").write_bytes(b"jpeg")
    (artifact_dir / "doc_meta.json").write_text(
        json.dumps({"table_of_contents": [{"title": "Doc", "page_id": 2}]}),
        encoding="utf-8",
    )

    assets, chapters = build_assets_and_chapters(md_path, marker_root, source_id="s1")

    assert len(assets) == 1
    assert assets[0].page_id == 3
    assert assets[0].file_path == str((artifact_dir / "_page_3_Figure_1.jpeg").resolve())
    assert len(chapters) == 1
    assert chapters[0].page_start == 2
    assert chapters[0].page_end == 3

from __future__ import annotations

from larrak_audio.config import AudiobookConfig
from larrak_audio.enhance import VISUAL_NOTE, enhance_chapters
from larrak_audio.types import AssetRef, ChapterDoc


def test_enhancement_inserts_visual_note_and_asset_page() -> None:
    chapter = ChapterDoc(
        chapter_id="chapter_000",
        title="Intro",
        text="\n".join(
            [
                "# Intro",
                "![](_page_3_Figure_1.jpeg)",
                "| A | B |",
                "|---|---|",
                "| 1 | 2 |",
            ]
        ),
        page_start=0,
        page_end=1,
        asset_refs=["asset_00000"],
    )
    assets = [
        AssetRef(
            asset_id="asset_00000",
            page_id=3,
            file_path="/tmp/_page_3_Figure_1.jpeg",
            chapter_id="chapter_000",
            anchor_text="Intro",
        )
    ]

    out = enhance_chapters([chapter], assets, AudiobookConfig(), enable_cleanup=False)[0].text

    assert VISUAL_NOTE in out
    assert "(asset: _page_3_Figure_1.jpeg, page: 3)" in out

from __future__ import annotations

from larrak_audio.index_meili import build_chunk_documents, chunk_text
from larrak_audio.types import ChapterDoc


def test_chunking_is_deterministic_and_bounded() -> None:
    text = ("Sentence one. " * 30) + "\n\n" + ("Sentence two. " * 30)
    chunks_a = chunk_text(text, max_chars=120)
    chunks_b = chunk_text(text, max_chars=120)

    assert chunks_a == chunks_b
    assert chunks_a
    assert all(len(chunk) <= 120 for chunk in chunks_a)


def test_chunk_document_ids_are_stable() -> None:
    chapter = ChapterDoc(
        chapter_id="chapter_000",
        title="T",
        text="A short sentence. " * 40,
        page_start=1,
        page_end=2,
        asset_refs=[],
    )

    docs1 = build_chunk_documents("source-x", [chapter], chunk_size=100)
    docs2 = build_chunk_documents("source-x", [chapter], chunk_size=100)

    assert [d["id"] for d in docs1] == [d["id"] for d in docs2]
    assert all(d["source_id"] == "source-x" for d in docs1)

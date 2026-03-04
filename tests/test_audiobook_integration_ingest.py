from __future__ import annotations

import json
from pathlib import Path

from larrak_audio.config import AudiobookConfig
from larrak_audio.marker_adapter import MarkerIngestResult
from larrak_audio.pipeline import ingest_source
from larrak_audio.utils import read_json


def test_ingest_uses_marker_artifact_parsing_path(tmp_path: Path, monkeypatch) -> None:
    fixture_dir = tmp_path / "marker_fixture"
    fixture_dir.mkdir(parents=True)

    fixture_md = fixture_dir / "fixture.md"
    fixture_md.write_text(
        "\n".join(
            [
                "# Doc",
                "![](_page_1_Figure_1.jpeg)",
                "## Next",
                "text",
            ]
        ),
        encoding="utf-8",
    )
    (fixture_dir / "_page_1_Figure_1.jpeg").write_bytes(b"jpeg")
    (fixture_dir / "fixture_meta.json").write_text(
        json.dumps({"table_of_contents": [{"title": "Doc", "page_id": 1}]}),
        encoding="utf-8",
    )
    (fixture_dir / "blocks.json").write_text(
        json.dumps([{"block_type": "20", "block_id": 1, "page_id": 1}]),
        encoding="utf-8",
    )

    def fake_ingest(*, source_path, source_type, output_dir, cfg, marker_extra_args):
        target_md = output_dir / "source.md"
        target_md.write_text(fixture_md.read_text(encoding="utf-8"), encoding="utf-8")
        return MarkerIngestResult(markdown_path=target_md, marker_output_dir=fixture_dir)

    import larrak_audio.pipeline as pipeline

    monkeypatch.setattr(pipeline, "ingest_source_via_marker", fake_ingest)

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%fake")

    cfg = AudiobookConfig(
        output_root=str(tmp_path / "outputs"),
        queue_db_path=str(tmp_path / "outputs" / "jobs.sqlite3"),
    )

    manifest = ingest_source(pdf, "pdf", cfg)
    assets = read_json(Path(manifest.assets_manifest_path))
    chapters = read_json(Path(manifest.chapters_path))

    assert Path(manifest.markdown_path).exists()
    marker_dir = Path(manifest.output_root)
    audio_dir = Path(manifest.audio_output_root)
    assert marker_dir.name == "marker"
    assert audio_dir.name == "audio"
    assert marker_dir.parent.name == manifest.source_id
    assert audio_dir.parent.name == manifest.source_id
    assert marker_dir.parent == audio_dir.parent
    assert assets
    assert chapters
    assert any(isinstance(row.get("page_id"), int) for row in assets)

from __future__ import annotations

from pathlib import Path

from larrak_audio.config import AudiobookConfig
from larrak_audio.pipeline import load_source_manifest
from larrak_audio.utils import write_json


def test_load_source_manifest_recovers_flat_marker_artifact_dir(tmp_path: Path) -> None:
    output_root = tmp_path / "outputs"
    source_id = "doc-123"
    marker_dir = output_root / "sources" / source_id / "marker"
    marker_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = output_root / "sources" / source_id / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir = marker_dir / "doc"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    source_md = marker_dir / "source.md"
    source_md.write_text("# Doc", encoding="utf-8")
    (artifact_dir / "doc.md").write_text("# Doc", encoding="utf-8")
    chapters_path = marker_dir / "chapters.json"
    assets_path = marker_dir / "assets_manifest.json"
    write_json(chapters_path, [])
    write_json(assets_path, [])
    write_json(
        marker_dir / "source_manifest.json",
        {
            "source_id": source_id,
            "source_path": str(tmp_path / "doc.pdf"),
            "source_type": "pdf",
            "output_root": str(marker_dir),
            "marker_output_dir": str(marker_dir / "missing"),
            "markdown_path": str(source_md),
            "chapter_count": 0,
            "assets_manifest_path": str(assets_path),
            "chapters_path": str(chapters_path),
            "audio_output_root": str(audio_dir),
        },
    )

    cfg = AudiobookConfig(
        output_root=str(output_root),
        queue_db_path=str(tmp_path / "jobs.sqlite3"),
    )

    manifest = load_source_manifest(source_id, cfg)

    assert Path(manifest.marker_output_dir) == artifact_dir.resolve()

from __future__ import annotations

from pathlib import Path

import pytest

from larrak_audio.config import AudiobookConfig
from larrak_audio.pipeline import build_source
from larrak_audio.queue import JobQueue
from larrak_audio.service import create_app
from larrak_audio.types import SourceManifest
from larrak_audio.utils import write_json
from larrak_audio.worker import run_worker_once


def _seed_source(output_root: Path, source_id: str) -> SourceManifest:
    marker_dir = output_root / "sources" / source_id / "marker"
    marker_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = output_root / "sources" / source_id / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    source_md = marker_dir / "source.md"
    source_md.write_text("# Chapter\nHello world.", encoding="utf-8")

    chapters_path = marker_dir / "chapters.json"
    assets_path = marker_dir / "assets_manifest.json"

    write_json(
        chapters_path,
        [
            {
                "chapter_id": "chapter_000",
                "title": "Chapter",
                "text": "Hello world.",
                "page_start": 1,
                "page_end": 1,
                "asset_refs": [],
            }
        ],
    )
    write_json(assets_path, [])

    manifest = SourceManifest(
        source_id=source_id,
        source_path=str(source_md),
        source_type="md",
        output_root=str(marker_dir),
        marker_output_dir=str(marker_dir),
        markdown_path=str(source_md),
        chapter_count=1,
        assets_manifest_path=str(assets_path),
        chapters_path=str(chapters_path),
        audio_output_root=str(audio_dir),
    )
    write_json(marker_dir / "source_manifest.json", manifest.to_dict())
    return manifest


def test_build_pipeline_with_mocked_backends(tmp_path: Path, monkeypatch) -> None:
    source_id = "source-001"
    output_root = tmp_path / "outputs"
    _seed_source(output_root, source_id)

    cfg = AudiobookConfig(
        output_root=str(output_root),
        queue_db_path=str(tmp_path / "jobs.sqlite3"),
    )

    import larrak_audio.pipeline as pipeline

    class FakeMeili:
        def __init__(self, _cfg):
            self.cfg = _cfg

        def index_documents(self, source, chapters, assets):
            return {
                "counts": {"chunks": 1, "chapters": len(chapters), "assets": len(assets)},
                "indexes": {
                    "doc_chunks": self.cfg.meili_index_doc_chunks,
                    "doc_chapters": self.cfg.meili_index_doc_chapters,
                    "doc_assets": self.cfg.meili_index_doc_assets,
                },
            }

    class DummyBackend:
        def __init__(self, model_id, device):
            self.model_id = model_id
            self.device = device

    def fake_render(*, chapters, out_dir, backend, ffmpeg_bin):
        paths = []
        for i, _chapter in enumerate(chapters, start=1):
            p = out_dir / f"chapter_{i:02d}.mp3"
            p.write_bytes(b"ID3")
            paths.append(p)
        return paths

    def fake_package(*, ffmpeg_bin, chapter_mp3s, chapter_titles, output_path):
        output_path.write_bytes(b"m4b")

    monkeypatch.setattr(pipeline, "MeiliClient", FakeMeili)
    monkeypatch.setattr(pipeline, "QwenTTSBackend", DummyBackend)
    monkeypatch.setattr(pipeline, "render_chapters_to_audio", fake_render)
    monkeypatch.setattr(pipeline, "package_m4b", fake_package)

    result = build_source(source_id=source_id, cfg=cfg, enhance=False)

    assert Path(result["book_m4b"]).exists()
    assert Path(result["index_manifest"]).exists()
    assert len(result["chapter_mp3s"]) == 1
    output_dir = Path(result["output_dir"])
    marker_dir = Path(result["marker_dir"])
    assert output_dir.name == "audio"
    assert output_dir.parent.name == source_id
    assert marker_dir.name == "marker"
    assert marker_dir.parent.name == source_id
    assert output_dir.parent == marker_dir.parent


def test_rest_jobs_and_worker_lifecycle(tmp_path: Path, monkeypatch) -> None:
    fastapi = pytest.importorskip("fastapi")
    _ = fastapi
    from fastapi.testclient import TestClient

    cfg = AudiobookConfig(
        output_root=str(tmp_path / "outputs"),
        queue_db_path=str(tmp_path / "jobs.sqlite3"),
    )
    queue = JobQueue(cfg.queue_db)
    app = create_app(cfg=cfg, queue=queue)
    client = TestClient(app)

    import larrak_audio.worker as worker

    def fake_build(*, source_id, cfg, enhance):
        marker_dir = Path(cfg.output_root) / "sources" / source_id / "marker"
        marker_dir.mkdir(parents=True, exist_ok=True)
        out = Path(cfg.output_root) / "sources" / source_id / "audio"
        out.mkdir(parents=True, exist_ok=True)
        m4b = out / "book.m4b"
        index_manifest = marker_dir / "index_manifest.json"
        index_manifest.write_text("{}", encoding="utf-8")
        chapters_manifest = marker_dir / "chapters.json"
        chapters_manifest.write_text("[]", encoding="utf-8")
        m4b.write_bytes(b"m4b")
        return {
            "source_id": source_id,
            "output_dir": str(out),
            "book_m4b": str(m4b),
            "chapter_mp3s": [],
            "index_manifest": str(index_manifest),
            "chapters_manifest": str(chapters_manifest),
        }

    monkeypatch.setattr(worker, "build_source", fake_build)

    resp = client.post("/jobs", json={"job_type": "build", "payload": {"source_id": "s-1"}})
    assert resp.status_code == 200
    job_id = int(resp.json()["job_id"])

    processed = run_worker_once(queue=queue, cfg=cfg, max_retries=1)
    assert processed

    status = client.get(f"/jobs/{job_id}")
    assert status.status_code == 200
    assert status.json()["job"]["status"] == "complete"

    artifacts = client.get(f"/jobs/{job_id}/artifacts")
    assert artifacts.status_code == 200
    assert "book_m4b" in artifacts.json()["artifacts"]

from __future__ import annotations

from pathlib import Path

import larrak_audio.batch_run as batch_run
from larrak_audio.config import AudiobookConfig
from larrak_audio.types import SourceManifest
from larrak_audio.utils import read_json


def _make_manifest(cfg: AudiobookConfig, source_path: Path, source_id: str) -> SourceManifest:
    marker_dir = Path(cfg.output_root) / "sources" / source_id / "marker"
    marker_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = Path(cfg.output_root) / "sources" / source_id / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    marker_output_dir = marker_dir / "marker_raw"
    marker_output_dir.mkdir(parents=True, exist_ok=True)
    source_md = marker_dir / "source.md"
    source_md.write_text("stub", encoding="utf-8")
    chapters_path = marker_dir / "chapters.json"
    chapters_path.write_text("[]", encoding="utf-8")
    assets_path = marker_dir / "assets_manifest.json"
    assets_path.write_text("[]", encoding="utf-8")
    return SourceManifest(
        source_id=source_id,
        source_path=str(source_path),
        source_type="pdf",
        output_root=str(marker_dir),
        marker_output_dir=str(marker_output_dir),
        markdown_path=str(source_md),
        chapter_count=0,
        assets_manifest_path=str(assets_path),
        chapters_path=str(chapters_path),
        audio_output_root=str(audio_dir),
    )


def test_run_test_files_success_with_special_filenames(tmp_path: Path, monkeypatch) -> None:
    input_dir = tmp_path / "test files"
    input_dir.mkdir(parents=True)
    first = input_dir / "A file.pdf"
    second = input_dir / "Anna's file.pdf"
    first.write_bytes(b"%PDF-1.4")
    second.write_bytes(b"%PDF-1.4")

    cfg = AudiobookConfig(
        output_root=str(tmp_path / "outputs"),
        queue_db_path=str(tmp_path / "outputs" / "jobs.sqlite3"),
    )

    ingest_calls: list[tuple[str, str, list[str]]] = []
    build_calls: list[tuple[str, bool]] = []

    def fake_ready(_cfg: AudiobookConfig) -> None:
        return None

    def fake_ingest(*, source_path, source_type, cfg, marker_extra_args):
        ingest_calls.append((str(source_path), source_type, list(marker_extra_args)))
        source_id = source_path.stem.replace(" ", "-").replace("'", "").lower()
        return _make_manifest(cfg, Path(source_path), source_id=source_id)

    def fake_build(*, source_id, cfg, enhance):
        build_calls.append((source_id, bool(enhance)))
        return {"source_id": source_id}

    monkeypatch.setattr(batch_run, "ensure_marker_ready", fake_ready)
    monkeypatch.setattr(batch_run, "ingest_source", fake_ingest)
    monkeypatch.setattr(batch_run, "build_source", fake_build)

    summary = batch_run.run_test_files(
        cfg=cfg,
        input_dir=input_dir,
        marker_extra_args=["--max_pages", "2"],
    )

    assert summary["total"] == 2
    assert summary["succeeded"] == 2
    assert summary["failed"] == 0
    assert summary["exit_code"] == 0
    assert summary["error"] is None
    assert len(ingest_calls) == 2
    assert len(build_calls) == 2
    assert all(call[2] == ["--max_pages", "2"] for call in ingest_calls)
    assert [Path(row["source_path"]).name for row in summary["results"]] == ["A file.pdf", "Anna's file.pdf"]
    assert Path(summary["summary_path"]).exists()


def test_run_test_files_continues_after_ingest_and_build_failures(tmp_path: Path, monkeypatch) -> None:
    input_dir = tmp_path / "test files"
    input_dir.mkdir(parents=True)
    first = input_dir / "ok.pdf"
    second = input_dir / "ingest-fail.pdf"
    third = input_dir / "build-fail.pdf"
    first.write_bytes(b"%PDF-1.4")
    second.write_bytes(b"%PDF-1.4")
    third.write_bytes(b"%PDF-1.4")

    cfg = AudiobookConfig(
        output_root=str(tmp_path / "outputs"),
        queue_db_path=str(tmp_path / "outputs" / "jobs.sqlite3"),
    )

    build_calls: list[str] = []

    def fake_ready(_cfg: AudiobookConfig) -> None:
        return None

    def fake_ingest(*, source_path, source_type, cfg, marker_extra_args):
        _ = source_type, marker_extra_args
        if Path(source_path).name == "ingest-fail.pdf":
            raise RuntimeError("ingest boom")
        source_id = Path(source_path).stem
        return _make_manifest(cfg, Path(source_path), source_id=source_id)

    def fake_build(*, source_id, cfg, enhance):
        _ = cfg, enhance
        build_calls.append(source_id)
        if source_id == "build-fail":
            raise RuntimeError("build boom")
        return {"source_id": source_id}

    monkeypatch.setattr(batch_run, "ensure_marker_ready", fake_ready)
    monkeypatch.setattr(batch_run, "ingest_source", fake_ingest)
    monkeypatch.setattr(batch_run, "build_source", fake_build)

    summary = batch_run.run_test_files(cfg=cfg, input_dir=input_dir)

    assert summary["total"] == 3
    assert summary["succeeded"] == 1
    assert summary["failed"] == 2
    assert summary["exit_code"] == 1
    assert len(build_calls) == 2

    rows = {Path(row["source_path"]).name: row for row in summary["results"]}
    assert rows["ingest-fail.pdf"]["ingest_ok"] is False
    assert rows["ingest-fail.pdf"]["build_ok"] is False
    assert "ingest failed: ingest boom" in str(rows["ingest-fail.pdf"]["error"])

    assert rows["build-fail.pdf"]["ingest_ok"] is True
    assert rows["build-fail.pdf"]["build_ok"] is False
    assert "build failed: build boom" in str(rows["build-fail.pdf"]["error"])


def test_run_test_files_empty_match_returns_exit_one(tmp_path: Path, monkeypatch) -> None:
    input_dir = tmp_path / "test files"
    input_dir.mkdir(parents=True)

    cfg = AudiobookConfig(
        output_root=str(tmp_path / "outputs"),
        queue_db_path=str(tmp_path / "outputs" / "jobs.sqlite3"),
    )

    ready_called = False

    def fake_ready(_cfg: AudiobookConfig) -> None:
        nonlocal ready_called
        ready_called = True

    monkeypatch.setattr(batch_run, "ensure_marker_ready", fake_ready)

    summary = batch_run.run_test_files(cfg=cfg, input_dir=input_dir)

    assert ready_called is False
    assert summary["total"] == 0
    assert summary["succeeded"] == 0
    assert summary["failed"] == 0
    assert summary["exit_code"] == 1
    assert "no files matched" in str(summary["error"])
    assert summary["results"] == []
    assert Path(summary["summary_path"]).exists()


def test_run_test_files_honors_summary_path(tmp_path: Path, monkeypatch) -> None:
    input_dir = tmp_path / "test files"
    input_dir.mkdir(parents=True)
    one = input_dir / "one.pdf"
    one.write_bytes(b"%PDF-1.4")

    cfg = AudiobookConfig(
        output_root=str(tmp_path / "outputs"),
        queue_db_path=str(tmp_path / "outputs" / "jobs.sqlite3"),
    )

    custom_summary = tmp_path / "reports" / "summary.json"

    def fake_ready(_cfg: AudiobookConfig) -> None:
        return None

    def fake_ingest(*, source_path, source_type, cfg, marker_extra_args):
        _ = source_type, marker_extra_args
        return _make_manifest(cfg, Path(source_path), source_id="one")

    def fake_build(*, source_id, cfg, enhance):
        _ = source_id, cfg, enhance
        return {"ok": True}

    monkeypatch.setattr(batch_run, "ensure_marker_ready", fake_ready)
    monkeypatch.setattr(batch_run, "ingest_source", fake_ingest)
    monkeypatch.setattr(batch_run, "build_source", fake_build)

    summary = batch_run.run_test_files(cfg=cfg, input_dir=input_dir, summary_path=custom_summary)
    persisted = read_json(custom_summary.resolve())

    assert summary["summary_path"] == str(custom_summary.resolve())
    assert custom_summary.exists()
    assert persisted["exit_code"] == 0

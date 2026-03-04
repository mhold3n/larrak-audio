from __future__ import annotations

from pathlib import Path

import larrak_audio.research_annas as research
from larrak_audio.config import AudiobookConfig
from larrak_audio.types import SourceManifest


def _make_manifest(tmp_path: Path, source_path: Path, source_id: str) -> SourceManifest:
    marker_dir = tmp_path / "outputs" / "sources" / source_id / "marker"
    marker_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = tmp_path / "outputs" / "sources" / source_id / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
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
        marker_output_dir=str(marker_dir / "marker"),
        markdown_path=str(source_md),
        chapter_count=0,
        assets_manifest_path=str(assets_path),
        chapters_path=str(chapters_path),
        audio_output_root=str(audio_dir),
    )


def test_run_annas_search_builds_expected_command_and_parses_json(tmp_path: Path, monkeypatch) -> None:
    cfg = AudiobookConfig(
        annas_mcp_bin="annas-mcp",
        annas_secret_key="secret",
        annas_base_url="https://annas-archive.gl",
        annas_min_interval_s="0",
        output_root=str(tmp_path / "outputs"),
        queue_db_path=str(tmp_path / "outputs" / "jobs.sqlite3"),
    )
    captured: dict[str, object] = {}

    def fake_run(cmd, check, capture_output, text, env, timeout):
        _ = check, capture_output, text, timeout
        captured["cmd"] = cmd
        captured["env"] = env

        class Proc:
            returncode = 0
            stdout = '{"results":[{"title":"ISO 6336"}]}'
            stderr = ""

        return Proc()

    monkeypatch.setattr(research.subprocess, "run", fake_run)
    payload = research.run_annas_search(
        cfg=cfg,
        kind="book",
        query="ISO 6336",
        extra_args=["--limit", "5"],
    )

    assert captured["cmd"] == ["annas-mcp", "search", "ISO 6336", "--limit", "5"]
    assert captured["env"]["ANNAS_BASE_URL"] == "annas-archive.gl"
    assert payload["operation"] == "search"
    assert payload["parsed_json"]["results"][0]["title"] == "ISO 6336"


def test_run_annas_download_tracks_created_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(research, "DEFAULT_ANNAS_RAW_DIR", tmp_path / "annas raw")
    cfg = AudiobookConfig(
        annas_mcp_bin="annas-mcp",
        annas_secret_key="secret",
        annas_min_interval_s="0",
        output_root=str(tmp_path / "outputs"),
        queue_db_path=str(tmp_path / "outputs" / "jobs.sqlite3"),
    )

    def fake_run(cmd, check, capture_output, text, env, timeout):
        _ = cmd, check, capture_output, text, timeout
        download_root = Path(env["ANNAS_DOWNLOAD_PATH"])
        (download_root / "downloaded.pdf").write_bytes(b"%PDF-1.4")

        class Proc:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return Proc()

    monkeypatch.setattr(research.subprocess, "run", fake_run)
    payload = research.run_annas_download(
        cfg=cfg,
        kind="book",
        identifier="abc123",
        download_dir=tmp_path / "downloads",
    )

    assert payload["operation"] == "download"
    assert len(payload["downloaded_files"]) == 1
    assert payload["downloaded_files"][0].endswith("downloaded.pdf")


def test_run_annas_search_filters_small_candidates_when_larger_exist(tmp_path: Path, monkeypatch) -> None:
    cfg = AudiobookConfig(
        annas_mcp_bin="annas-mcp",
        annas_secret_key="secret",
        annas_min_interval_s="0",
        output_root=str(tmp_path / "outputs"),
        queue_db_path=str(tmp_path / "outputs" / "jobs.sqlite3"),
    )

    def fake_run(cmd, check, capture_output, text, env, timeout):
        _ = cmd, check, capture_output, text, env, timeout

        class Proc:
            returncode = 0
            stdout = "\n".join(
                [
                    "Book 1:",
                    "Title: Small one",
                    "Size: 0.3MB",
                    "URL: https://annas-archive.gl/md5/small",
                    "Hash: small",
                    "",
                    "Book 2:",
                    "Title: Large one",
                    "Size: 2.5MB",
                    "URL: https://annas-archive.gl/md5/large",
                    "Hash: large",
                    "",
                ]
            )
            stderr = ""

        return Proc()

    monkeypatch.setattr(research.subprocess, "run", fake_run)
    payload = research.run_annas_search(cfg=cfg, kind="book", query="size test", min_download_size_bytes=1024 * 1024)

    assert len(payload["all_candidates"]) == 2
    assert len(payload["candidates"]) == 1
    assert payload["candidates"][0]["hash"] == "large"
    assert len(payload["dropped_small_candidates"]) == 1
    assert payload["dropped_small_candidates"][0]["hash"] == "small"


def test_run_research_annas_download_keeps_all_when_all_below_threshold(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(research, "DEFAULT_ANNAS_RAW_DIR", tmp_path / "annas raw")
    cfg = AudiobookConfig(
        output_root=str(tmp_path / "outputs"),
        queue_db_path=str(tmp_path / "outputs" / "jobs.sqlite3"),
        annas_secret_key="secret",
        annas_min_interval_s="0",
    )
    first = tmp_path / "downloads" / "a.pdf"
    second = tmp_path / "downloads" / "b.pdf"
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"x" * 400_000)
    second.write_bytes(b"x" * 600_000)

    def fake_download(*, cfg, kind, identifier, filename, download_dir, extra_args):
        _ = cfg, kind, identifier, filename, download_dir, extra_args
        return {
            "operation": "book-download",
            "identifier": "x",
            "filename": None,
            "download_dir": str(first.parent),
            "command": ["annas-mcp", "book-download", "x"],
            "stdout": "ok",
            "stderr": "",
            "downloaded_files": [str(first), str(second)],
        }

    monkeypatch.setattr(research, "run_annas_download", fake_download)

    summary = research.run_research_annas(
        cfg=cfg,
        action="download",
        kind="book",
        identifier="x",
        ingest=False,
        build=False,
        min_download_size_mb=1.0,
    )

    assert summary["exit_code"] == 0
    assert len(summary["downloaded_files"]) == 2
    assert summary["dropped_small_files"] == []


def test_run_research_annas_download_drops_small_when_large_exists(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(research, "DEFAULT_ANNAS_RAW_DIR", tmp_path / "annas raw")
    cfg = AudiobookConfig(
        output_root=str(tmp_path / "outputs"),
        queue_db_path=str(tmp_path / "outputs" / "jobs.sqlite3"),
        annas_secret_key="secret",
        annas_min_interval_s="0",
    )
    small = tmp_path / "downloads" / "small.pdf"
    large = tmp_path / "downloads" / "large.pdf"
    small.parent.mkdir(parents=True, exist_ok=True)
    small.write_bytes(b"x" * 300_000)
    large.write_bytes(b"x" * 1_500_000)

    def fake_download(*, cfg, kind, identifier, filename, download_dir, extra_args):
        _ = cfg, kind, identifier, filename, download_dir, extra_args
        return {
            "operation": "book-download",
            "identifier": "x",
            "filename": None,
            "download_dir": str(small.parent),
            "command": ["annas-mcp", "book-download", "x"],
            "stdout": "ok",
            "stderr": "",
            "downloaded_files": [str(small), str(large)],
        }

    monkeypatch.setattr(research, "run_annas_download", fake_download)

    summary = research.run_research_annas(
        cfg=cfg,
        action="download",
        kind="book",
        identifier="x",
        ingest=False,
        build=False,
        min_download_size_mb=1.0,
    )

    assert summary["exit_code"] == 0
    assert summary["downloaded_files"] == [str(large)]
    assert summary["dropped_small_files"] == [str(small)]


def test_run_annas_search_falls_back_to_legacy_operation(tmp_path: Path, monkeypatch) -> None:
    cfg = AudiobookConfig(
        annas_mcp_bin="annas-mcp",
        annas_secret_key="secret",
        annas_min_interval_s="0",
        output_root=str(tmp_path / "outputs"),
        queue_db_path=str(tmp_path / "outputs" / "jobs.sqlite3"),
    )
    calls: list[list[str]] = []

    def fake_run(cmd, check, capture_output, text, env, timeout):
        _ = check, capture_output, text, env, timeout
        calls.append(list(cmd))

        class Proc:
            returncode = 0
            stdout = '{"results":[]}'
            stderr = ""

        if cmd[1] == "search":
            Proc.returncode = 2
            Proc.stderr = "unknown command"
        return Proc()

    monkeypatch.setattr(research.subprocess, "run", fake_run)
    payload = research.run_annas_search(cfg=cfg, kind="book", query="fallback test")

    assert calls[0][1] == "search"
    assert calls[1][1] == "book-search"
    assert payload["operation"] == "book-search"


def test_run_research_annas_download_ingest_build_continues_on_failures(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(research, "DEFAULT_ANNAS_RAW_DIR", tmp_path / "annas raw")
    cfg = AudiobookConfig(
        output_root=str(tmp_path / "outputs"),
        queue_db_path=str(tmp_path / "outputs" / "jobs.sqlite3"),
        annas_secret_key="secret",
        annas_min_interval_s="0",
    )
    first = tmp_path / "downloads" / "one.pdf"
    second = tmp_path / "downloads" / "two.pdf"
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"%PDF-1.4")
    second.write_bytes(b"%PDF-1.4")

    def fake_download(*, cfg, kind, identifier, filename, download_dir, extra_args):
        _ = cfg, kind, identifier, filename, download_dir, extra_args
        return {
            "operation": "book-download",
            "identifier": "x",
            "filename": None,
            "download_dir": str(first.parent),
            "command": ["annas-mcp", "book-download", "x"],
            "stdout": "ok",
            "stderr": "",
            "downloaded_files": [str(first), str(second)],
        }

    def fake_ready(_cfg: AudiobookConfig) -> None:
        return None

    def fake_ingest(*, source_path, source_type, cfg, marker_extra_args):
        _ = source_type, marker_extra_args
        return _make_manifest(tmp_path, Path(source_path), source_id=Path(source_path).stem)

    def fake_build(*, source_id, cfg, enhance):
        _ = cfg, enhance
        if source_id == "two":
            raise RuntimeError("build boom")
        return {"source_id": source_id}

    monkeypatch.setattr(research, "run_annas_download", fake_download)
    monkeypatch.setattr(research, "ensure_marker_ready", fake_ready)
    monkeypatch.setattr(research, "ingest_source", fake_ingest)
    monkeypatch.setattr(research, "build_source", fake_build)

    summary = research.run_research_annas(
        cfg=cfg,
        action="download",
        kind="book",
        identifier="x",
        ingest=True,
        build=True,
    )

    assert summary["total"] == 2
    assert summary["succeeded"] == 1
    assert summary["failed"] == 1
    assert summary["exit_code"] == 1
    rows = {Path(row["source_path"]).name: row for row in summary["results"]}
    assert rows["one.pdf"]["ingest_ok"] is True
    assert rows["one.pdf"]["build_ok"] is True
    assert rows["two.pdf"]["ingest_ok"] is True
    assert rows["two.pdf"]["build_ok"] is False
    assert "build failed: build boom" in str(rows["two.pdf"]["error"])


def test_run_annas_search_retries_transient_failure(tmp_path: Path, monkeypatch) -> None:
    cfg = AudiobookConfig(
        annas_mcp_bin="annas-mcp",
        annas_secret_key="secret",
        annas_min_interval_s="0",
        annas_max_retries="2",
        annas_retry_backoff_s="0.1",
        output_root=str(tmp_path / "outputs"),
        queue_db_path=str(tmp_path / "outputs" / "jobs.sqlite3"),
    )
    calls: list[list[str]] = []

    def fake_run(cmd, check, capture_output, text, env, timeout):
        _ = check, capture_output, text, env, timeout
        calls.append(list(cmd))

        class Proc:
            returncode = 1
            stdout = ""
            stderr = "429 too many requests"

        if len(calls) == 2:
            Proc.returncode = 0
            Proc.stdout = '{"results":[]}'
            Proc.stderr = ""
        return Proc()

    monkeypatch.setattr(research.subprocess, "run", fake_run)
    monkeypatch.setattr(research.time, "sleep", lambda _s: None)
    payload = research.run_annas_search(cfg=cfg, kind="book", query="retry test")

    assert payload["operation"] == "search"
    assert len(calls) == 2


def test_run_annas_search_does_not_retry_invalid_secret_key(tmp_path: Path, monkeypatch) -> None:
    cfg = AudiobookConfig(
        annas_mcp_bin="annas-mcp",
        annas_secret_key="secret",
        annas_min_interval_s="0",
        annas_max_retries="3",
        annas_retry_backoff_s="0.1",
        output_root=str(tmp_path / "outputs"),
        queue_db_path=str(tmp_path / "outputs" / "jobs.sqlite3"),
    )
    calls: list[list[str]] = []
    slept = False

    def fake_run(cmd, check, capture_output, text, env, timeout):
        _ = check, capture_output, text, env, timeout
        calls.append(list(cmd))

        class Proc:
            returncode = 1
            stdout = ""
            stderr = "Invalid secret key"

        return Proc()

    def fake_sleep(_seconds: float) -> None:
        nonlocal slept
        slept = True

    monkeypatch.setattr(research.subprocess, "run", fake_run)
    monkeypatch.setattr(research.time, "sleep", fake_sleep)

    try:
        research.run_annas_search(cfg=cfg, kind="book", query="bad key")
    except RuntimeError as exc:
        assert "invalid secret key" in str(exc).lower()
    else:
        raise AssertionError("expected RuntimeError")

    # Two attempts are for command fallback (search -> book-search), not retries.
    assert len(calls) == 2
    assert slept is False

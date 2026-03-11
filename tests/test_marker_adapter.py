from __future__ import annotations

import json
from pathlib import Path

import pytest

from larrak_audio.config import AudiobookConfig
from larrak_audio.marker_adapter import _build_marker_commands, ingest_source_via_marker


def test_build_marker_commands_excludes_positional_output_for_marker_single(tmp_path: Path) -> None:
    marker_output = tmp_path / "out"
    source_path = tmp_path / "doc.pdf"
    commands = _build_marker_commands(
        marker_bin="marker_single",
        source_path=source_path,
        marker_output=marker_output,
        marker_extra_args=["--page_range", "0"],
    )

    assert len(commands) == 2
    assert all(len(cmd) > 0 and cmd[0] == "marker_single" for cmd in commands)
    assert all("--output_dir" in cmd for cmd in commands)
    assert not any(cmd[1:3] == [str(source_path), str(marker_output)] for cmd in commands)


def test_ingest_pdf_error_reports_all_attempts_for_marker_single(tmp_path: Path, monkeypatch) -> None:
    cfg = AudiobookConfig(marker_bin="marker_single")
    source_pdf = tmp_path / "doc.pdf"
    source_pdf.write_bytes(b"%PDF-1.4")

    class FakeProc:
        def __init__(self, returncode: int, stderr: str = "", stdout: str = ""):
            self.returncode = returncode
            self.stderr = stderr
            self.stdout = stdout

    calls: list[list[str]] = []
    responses = [
        FakeProc(returncode=2, stderr="primary failed"),
        FakeProc(returncode=2, stderr="secondary failed"),
    ]

    def fake_run(cmd, check, capture_output, text):
        _ = check, capture_output, text
        calls.append(list(cmd))
        return responses[len(calls) - 1]

    monkeypatch.setattr("larrak_audio.marker_adapter.subprocess.run", fake_run)

    with pytest.raises(RuntimeError) as exc_info:
        ingest_source_via_marker(
            source_path=source_pdf,
            source_type="pdf",
            output_dir=tmp_path / "out",
            cfg=cfg,
            marker_extra_args=["--page_range", "0"],
        )

    message = str(exc_info.value)
    assert "primary failed" in message
    assert "secondary failed" in message
    assert "unexpected extra argument" not in message
    assert len(calls) == 2
    assert all("--output_dir" in cmd for cmd in calls)


def test_ingest_pdf_flattens_marker_output_root_and_tracks_artifact_dir(tmp_path: Path, monkeypatch) -> None:
    cfg = AudiobookConfig(marker_bin="marker_single")
    source_pdf = tmp_path / "doc.pdf"
    source_pdf.write_bytes(b"%PDF-1.4")
    out_dir = tmp_path / "out"
    calls: list[list[str]] = []

    class FakeProc:
        returncode = 0
        stderr = ""
        stdout = ""

    def fake_run(cmd, check, capture_output, text):
        _ = check, capture_output, text
        calls.append(list(cmd))
        artifact_dir = out_dir / "doc"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "doc.md").write_text("# Doc", encoding="utf-8")
        (artifact_dir / "doc_meta.json").write_text(
            json.dumps({"table_of_contents": [{"title": "Doc", "page_id": 1}]}),
            encoding="utf-8",
        )
        return FakeProc()

    monkeypatch.setattr("larrak_audio.marker_adapter.subprocess.run", fake_run)

    result = ingest_source_via_marker(
        source_path=source_pdf,
        source_type="pdf",
        output_dir=out_dir,
        cfg=cfg,
        marker_extra_args=["--page_range", "0"],
    )

    assert len(calls) == 1
    assert "--output_dir" in calls[0]
    assert str(out_dir) in calls[0]
    assert result.markdown_path == out_dir / "source.md"
    assert result.markdown_path.read_text(encoding="utf-8") == "# Doc"
    assert result.marker_output_dir == (out_dir / "doc").resolve()
    assert not (out_dir / "marker").exists()

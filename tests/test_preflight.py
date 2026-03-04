from __future__ import annotations

from larrak_audio.config import AudiobookConfig
from larrak_audio.preflight import ensure_marker_ready, run_doctor


def test_doctor_reports_missing_marker() -> None:
    cfg = AudiobookConfig(marker_bin="/path/does/not/exist/marker_single")
    report = run_doctor(cfg=cfg, check_services=False)

    checks = {item["name"]: item for item in report["checks"]}  # type: ignore[index]
    assert checks["marker"]["ok"] is False


def test_ensure_marker_ready_raises_actionable_error() -> None:
    cfg = AudiobookConfig(marker_bin="/path/does/not/exist/marker_single")
    try:
        ensure_marker_ready(cfg)
    except RuntimeError as exc:
        text = str(exc)
        assert "Marker preflight failed" in text
        assert "command not found" in text
    else:
        raise AssertionError("expected RuntimeError")

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import AudiobookConfig
from .pipeline import build_source, ingest_source
from .preflight import ensure_marker_ready
from .utils import infer_source_type, utc_now_iso, write_json


def run_test_files(
    *,
    cfg: AudiobookConfig,
    input_dir: str | Path = "test files",
    glob_pattern: str = "*.pdf",
    recursive: bool = False,
    enhance: bool = True,
    marker_extra_args: list[str] | None = None,
    summary_path: str | Path | None = None,
) -> dict[str, Any]:
    marker_extra_args = list(marker_extra_args or [])
    started_at = utc_now_iso()
    source_dir = Path(input_dir).expanduser().resolve()
    discovered_files = _discover_files(source_dir=source_dir, glob_pattern=glob_pattern, recursive=recursive)

    run_error: str | None = None
    if not source_dir.exists():
        run_error = f"input directory not found: {source_dir}"
    elif not source_dir.is_dir():
        run_error = f"input path is not a directory: {source_dir}"
    elif not discovered_files:
        run_error = f"no files matched '{glob_pattern}' in {source_dir}"

    rows: list[dict[str, Any]] = []
    if run_error is None:
        try:
            ensure_marker_ready(cfg)
        except Exception as exc:
            run_error = f"marker preflight failed: {exc}"
            rows = [
                {
                    "source_path": str(path),
                    "source_id": None,
                    "ingest_ok": False,
                    "build_ok": False,
                    "error": run_error,
                }
                for path in discovered_files
            ]

    if run_error is None:
        for file_path in discovered_files:
            row = {
                "source_path": str(file_path),
                "source_id": None,
                "ingest_ok": False,
                "build_ok": False,
                "error": None,
            }
            try:
                source_type = infer_source_type(file_path)
                manifest = ingest_source(
                    source_path=file_path,
                    source_type=source_type,
                    cfg=cfg,
                    marker_extra_args=marker_extra_args,
                )
                row["source_id"] = manifest.source_id
                row["ingest_ok"] = True
            except Exception as exc:
                row["error"] = f"ingest failed: {exc}"
                rows.append(row)
                continue

            try:
                build_source(source_id=manifest.source_id, cfg=cfg, enhance=enhance)
                row["build_ok"] = True
            except Exception as exc:
                row["error"] = f"build failed: {exc}"

            rows.append(row)

    succeeded = sum(1 for row in rows if bool(row["ingest_ok"]) and bool(row["build_ok"]))
    failed = sum(1 for row in rows if not (bool(row["ingest_ok"]) and bool(row["build_ok"])))
    total = len(discovered_files)

    summary_target = _resolve_summary_path(cfg=cfg, summary_path=summary_path)
    payload: dict[str, Any] = {
        "started_at": started_at,
        "finished_at": utc_now_iso(),
        "input_dir": str(source_dir),
        "glob": glob_pattern,
        "recursive": recursive,
        "enhance": enhance,
        "total": total,
        "succeeded": succeeded,
        "failed": failed,
        "error": run_error,
        "results": rows,
        "summary_path": str(summary_target),
        "ok": bool(total > 0 and failed == 0 and run_error is None),
    }
    payload["exit_code"] = 0 if payload["ok"] else 1
    write_json(summary_target, payload)
    return payload


def _resolve_summary_path(cfg: AudiobookConfig, summary_path: str | Path | None) -> Path:
    if summary_path is not None:
        return Path(summary_path).expanduser().resolve()

    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(cfg.output_root).resolve() / "batch_runs" / f"test_files_{stamp}.json"


def _discover_files(source_dir: Path, glob_pattern: str, recursive: bool) -> list[Path]:
    if not source_dir.exists() or not source_dir.is_dir():
        return []

    matches = source_dir.rglob(glob_pattern) if recursive else source_dir.glob(glob_pattern)
    return sorted((path.resolve() for path in matches if path.is_file()), key=lambda path: str(path))

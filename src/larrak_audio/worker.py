from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import AudiobookConfig
from .pipeline import build_source, ingest_source
from .queue import JobQueue


def run_worker_once(queue: JobQueue, cfg: AudiobookConfig, max_retries: int = 2) -> bool:
    """Process at most one pending queue job."""

    job = queue.claim_next()
    if job is None:
        return False

    payload = _load_payload(job.payload_json)

    try:
        if job.job_type == "ingest":
            queue.record_step(job.job_id, "ingest", "running", "starting ingest")
            manifest = ingest_source(
                source_path=payload["source_path"],
                source_type=payload.get("source_type"),
                cfg=cfg,
                marker_extra_args=list(payload.get("marker_extra_args", [])),
            )
            queue.set_artifact(job.job_id, "source_manifest", Path(manifest.output_root) / "source_manifest.json")
            queue.set_artifact(job.job_id, "chapters", manifest.chapters_path)
            queue.set_artifact(job.job_id, "assets", manifest.assets_manifest_path)
            queue.update_progress(job.job_id, 1.0)
            queue.record_step(job.job_id, "ingest", "complete", manifest.source_id)
            queue.complete(job.job_id)
            return True

        if job.job_type == "build":
            queue.record_step(job.job_id, "build", "running", "starting build")
            result = build_source(
                source_id=str(payload["source_id"]),
                cfg=cfg,
                enhance=bool(payload.get("enhance", True)),
            )
            for key, val in result.items():
                if isinstance(val, str):
                    queue.set_artifact(job.job_id, key, val)
            queue.update_progress(job.job_id, 1.0)
            queue.record_step(job.job_id, "build", "complete", result.get("book_m4b", ""))
            queue.complete(job.job_id)
            return True

        raise ValueError(f"unsupported job_type: {job.job_type}")
    except Exception as exc:
        message = str(exc)
        queue.record_step(job.job_id, job.job_type, "failed", message)
        if job.attempts < max_retries:
            queue.requeue(job.job_id, message)
        else:
            queue.fail(job.job_id, message)
        return True


def run_worker_loop(
    queue: JobQueue,
    cfg: AudiobookConfig,
    interval_s: float = 2.0,
    max_retries: int = 2,
) -> None:
    """Run worker loop until interrupted."""

    while True:
        worked = run_worker_once(queue=queue, cfg=cfg, max_retries=max_retries)
        if not worked:
            time.sleep(max(0.2, float(interval_s)))


def _load_payload(payload_json: str) -> dict[str, Any]:
    try:
        data = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid job payload JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("job payload must be a JSON object")
    return data

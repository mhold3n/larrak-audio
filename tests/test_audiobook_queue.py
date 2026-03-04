from __future__ import annotations

from pathlib import Path

from larrak_audio.queue import JobQueue


def test_queue_transitions_are_durable(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path / "jobs.sqlite3")
    job_id = queue.enqueue("build", {"source_id": "abc", "enhance": False})

    pending = queue.get_job(job_id)
    assert pending is not None
    assert pending.status == "pending"

    running = queue.claim_next()
    assert running is not None
    assert running.job_id == job_id

    row = queue.get_job(job_id)
    assert row is not None
    assert row.status == "running"

    queue.update_progress(job_id, 0.5)
    queue.record_step(job_id, "build", "running", "halfway")
    queue.set_artifact(job_id, "chapters", tmp_path / "chapters.json")
    queue.complete(job_id)

    done = queue.get_job(job_id)
    assert done is not None
    assert done.status == "complete"
    assert done.progress == 1.0
    assert queue.get_artifacts(job_id)["chapters"].endswith("chapters.json")

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .types import JobRecord
from .utils import utc_now_iso


class JobQueue:
    """SQLite-backed queue for ingest/build orchestration."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def enqueue(self, job_type: str, payload: dict[str, Any]) -> int:
        now = utc_now_iso()
        payload_json = json.dumps(payload, ensure_ascii=True)
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO jobs (
                    job_type, status, payload_json, progress, error, attempts,
                    created_at, updated_at, started_at, finished_at
                ) VALUES (?, 'pending', ?, 0.0, NULL, 0, ?, ?, NULL, NULL)
                """,
                (str(job_type), payload_json, now, now),
            )
            job_id = int(cur.lastrowid)
        return job_id

    def claim_next(self) -> JobRecord | None:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM jobs WHERE status = 'pending' ORDER BY id ASC LIMIT 1"
            ).fetchone()
            if row is None:
                conn.commit()
                return None

            job_id = int(row["id"])
            conn.execute(
                """
                UPDATE jobs
                SET status = 'running',
                    started_at = COALESCE(started_at, ?),
                    updated_at = ?,
                    attempts = attempts + 1
                WHERE id = ?
                """,
                (now, now, job_id),
            )
            conn.commit()

        return self.get_job(job_id)

    def update_progress(self, job_id: int, progress: float) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET progress = ?, updated_at = ? WHERE id = ?",
                (float(progress), now, int(job_id)),
            )

    def record_step(self, job_id: int, step: str, status: str, message: str = "") -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO job_steps (job_id, step, status, message, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (int(job_id), str(step), str(status), str(message), now),
            )

    def set_artifact(self, job_id: int, name: str, path: str | Path) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO job_artifacts (job_id, name, path, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(job_id, name)
                DO UPDATE SET path = excluded.path, created_at = excluded.created_at
                """,
                (int(job_id), str(name), str(Path(path).resolve()), now),
            )

    def complete(self, job_id: int) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'complete', progress = 1.0, error = NULL,
                    updated_at = ?, finished_at = ?
                WHERE id = ?
                """,
                (now, now, int(job_id)),
            )

    def fail(self, job_id: int, error_message: str) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'failed', error = ?, updated_at = ?, finished_at = ?
                WHERE id = ?
                """,
                (str(error_message), now, now, int(job_id)),
            )

    def requeue(self, job_id: int, error_message: str) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'pending', error = ?, updated_at = ?
                WHERE id = ?
                """,
                (str(error_message), now, int(job_id)),
            )

    def get_job(self, job_id: int) -> JobRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (int(job_id),)).fetchone()
        return _to_job_record(row) if row is not None else None

    def get_artifacts(self, job_id: int) -> dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name, path FROM job_artifacts WHERE job_id = ? ORDER BY name ASC",
                (int(job_id),),
            ).fetchall()
        return {str(r["name"]): str(r["path"]) for r in rows}

    def get_steps(self, job_id: int) -> list[dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT step, status, message, created_at
                FROM job_steps
                WHERE job_id = ?
                ORDER BY id ASC
                """,
                (int(job_id),),
            ).fetchall()
        return [
            {
                "step": str(row["step"]),
                "status": str(row["status"]),
                "message": str(row["message"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    progress REAL NOT NULL,
                    error TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS job_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    step TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS job_artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(job_id, name),
                    FOREIGN KEY(job_id) REFERENCES jobs(id)
                )
                """
            )


def _to_job_record(row: sqlite3.Row | None) -> JobRecord | None:
    if row is None:
        return None
    return JobRecord(
        job_id=int(row["id"]),
        job_type=str(row["job_type"]),
        status=str(row["status"]),
        payload_json=str(row["payload_json"]),
        progress=float(row["progress"]),
        error=None if row["error"] is None else str(row["error"]),
        attempts=int(row["attempts"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        started_at=None if row["started_at"] is None else str(row["started_at"]),
        finished_at=None if row["finished_at"] is None else str(row["finished_at"]),
    )

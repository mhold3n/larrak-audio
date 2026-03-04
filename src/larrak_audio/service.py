from pathlib import Path
from typing import Any

from .config import AudiobookConfig, load_audiobook_config
from .index_meili import MeiliClient
from .pipeline import load_assets, load_chapters, load_source_manifest
from .queue import JobQueue
from .types import SourceManifest


def create_app(cfg: AudiobookConfig | None = None, queue: JobQueue | None = None):
    """Create local REST API app for queue + artifact + search workflows."""

    try:
        from fastapi import Body, FastAPI, HTTPException
        from pydantic import BaseModel, Field
    except Exception as exc:  # pragma: no cover - dependency gate
        raise RuntimeError(
            "FastAPI is required for service mode. Install optional dependencies: "
            'pip install -e ".[api]"'
        ) from exc

    cfg = cfg or load_audiobook_config()
    queue = queue or JobQueue(cfg.queue_db)

    class JobCreate(BaseModel):
        job_type: str = Field(pattern="^(ingest|build)$")
        payload: dict[str, Any]

    class SearchRequest(BaseModel):
        query: str
        source_id: str
        limit: int = 10

    app = FastAPI(title="Larrak Audio API", version="1.0.0")

    @app.post("/jobs")
    def post_jobs(req: JobCreate = Body(...)) -> dict[str, Any]:
        job_id = queue.enqueue(req.job_type, req.payload)
        return {"job_id": job_id, "status": "pending"}

    @app.get("/jobs/{job_id}")
    def get_job(job_id: int) -> dict[str, Any]:
        job = queue.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return {"job": job.to_dict(), "steps": queue.get_steps(job_id)}

    @app.get("/jobs/{job_id}/artifacts")
    def get_artifacts(job_id: int) -> dict[str, Any]:
        job = queue.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return {"job_id": job_id, "artifacts": queue.get_artifacts(job_id)}

    @app.post("/search")
    def post_search(req: SearchRequest = Body(...)) -> dict[str, Any]:
        client = MeiliClient(cfg)
        try:
            return client.search_chunks(query=req.query, source_id=req.source_id, limit=req.limit)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/sources/{source_id}")
    def get_source(source_id: str) -> dict[str, Any]:
        try:
            source = load_source_manifest(source_id, cfg)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        assets = [a.to_dict() for a in load_assets(source)]
        chapters = [c.to_dict() for c in load_chapters(source)]
        return {
            "source": source.to_dict(),
            "chapters": chapters,
            "assets": assets,
            "artifact_files": _artifact_files(source),
        }

    return app


def run_api(host: str = "127.0.0.1", port: int = 8787, cfg: AudiobookConfig | None = None) -> None:
    """Run local FastAPI server for audiobook workflows."""

    try:
        import uvicorn
    except Exception as exc:  # pragma: no cover - dependency gate
        raise RuntimeError(
            "uvicorn is required for service mode. Install optional dependencies: "
            'pip install -e ".[api]"'
        ) from exc

    cfg = cfg or load_audiobook_config()
    app = create_app(cfg=cfg)
    uvicorn.run(app, host=host, port=int(port), log_level="info")


def _artifact_files(source: SourceManifest) -> list[str]:
    marker_dir = Path(source.output_root)
    audio_dir = Path(source.audio_output_root) if source.audio_output_root else marker_dir
    files: set[Path] = set()
    for pattern in ["*.json", "*.md", "*.jpeg", "*.jpg", "*.png", "*.webp", "*.gif"]:
        files.update(path for path in marker_dir.rglob(pattern) if path.is_file())
    for pattern in ["chapter_*.mp3", "book.m4b"]:
        files.update(path for path in audio_dir.rglob(pattern) if path.is_file())
    return [str(path) for path in sorted(files)]

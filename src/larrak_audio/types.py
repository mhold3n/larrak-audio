from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AssetRef:
    """Reference to a non-text artifact extracted from source material."""

    asset_id: str
    page_id: int | None
    file_path: str
    chapter_id: str
    anchor_text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ChapterDoc:
    """Narration-ready chapter content."""

    chapter_id: str
    title: str
    text: str
    page_start: int | None
    page_end: int | None
    asset_refs: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceManifest:
    """Canonical source metadata stored in output directory."""

    source_id: str
    source_path: str
    source_type: str
    output_root: str
    marker_output_dir: str
    markdown_path: str
    chapter_count: int
    assets_manifest_path: str
    chapters_path: str
    audio_output_root: str = ""

    @property
    def output_dir(self) -> Path:
        return Path(self.output_root)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class JobRecord:
    """Queue row used by CLI/API status endpoints."""

    job_id: int
    job_type: str
    status: str
    payload_json: str
    progress: float
    error: str | None
    attempts: int
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BuildOptions:
    """Runtime build knobs for enhancement/indexing/TTS."""

    enhance: bool
    marker_extra_args: list[str]
    meili_chunk_index: str
    meili_chapter_index: str
    meili_asset_index: str
    tts_model_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import AudiobookConfig
from .enhance import enhance_chapters
from .index_meili import MeiliClient, write_index_manifest
from .marker_adapter import ingest_source_via_marker
from .packager import package_m4b
from .parse_marker import build_assets_and_chapters
from .tts import render_chapters_to_audio
from .tts_macos import MacOSTTSBackend
from .tts_qwen import QwenTTSBackend
from .types import AssetRef, ChapterDoc, SourceManifest
from .utils import infer_source_type, read_json, stable_source_id, write_json


def marker_root(cfg: AudiobookConfig) -> Path:
    # Legacy marker root kept for read compatibility with pre-sources layout.
    return Path(cfg.output_root) / "marker"


def audio_root(cfg: AudiobookConfig) -> Path:
    # Legacy audio root kept for read compatibility with pre-sources layout.
    return Path(cfg.output_root) / "audio"


def sources_root(cfg: AudiobookConfig) -> Path:
    return Path(cfg.output_root) / "sources"


def source_bundle_dir(source_id: str, cfg: AudiobookConfig) -> Path:
    return sources_root(cfg) / source_id


def marker_source_dir(source_id: str, cfg: AudiobookConfig) -> Path:
    return source_bundle_dir(source_id, cfg) / "marker"


def audio_source_dir(source_id: str, cfg: AudiobookConfig) -> Path:
    return source_bundle_dir(source_id, cfg) / "audio"


def ingest_source(
    source_path: str | Path,
    source_type: str | None,
    cfg: AudiobookConfig,
    marker_extra_args: list[str] | None = None,
) -> SourceManifest:
    """Ingest input source into canonical markdown + chapter/asset manifests."""

    src = Path(source_path).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(f"source file not found: {src}")

    s_type = source_type or infer_source_type(src)
    source_id = stable_source_id(src, s_type)
    source_dir = source_bundle_dir(source_id, cfg)
    source_dir.mkdir(parents=True, exist_ok=True)
    marker_dir = marker_source_dir(source_id, cfg)
    marker_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = audio_source_dir(source_id, cfg)
    audio_dir.mkdir(parents=True, exist_ok=True)

    ingest_result = ingest_source_via_marker(
        source_path=src,
        source_type=s_type,
        output_dir=marker_dir,
        cfg=cfg,
        marker_extra_args=marker_extra_args,
    )

    assets, chapters = build_assets_and_chapters(
        markdown_path=ingest_result.markdown_path,
        marker_output_dir=ingest_result.marker_output_dir,
        source_id=source_id,
    )

    assets_path = marker_dir / "assets_manifest.json"
    chapters_path = marker_dir / "chapters.json"
    write_json(assets_path, [asset.to_dict() for asset in assets])
    write_json(chapters_path, [chapter.to_dict() for chapter in chapters])

    manifest = SourceManifest(
        source_id=source_id,
        source_path=str(src),
        source_type=s_type,
        output_root=str(marker_dir),
        audio_output_root=str(audio_dir),
        marker_output_dir=str(ingest_result.marker_output_dir),
        markdown_path=str(ingest_result.markdown_path),
        chapter_count=len(chapters),
        assets_manifest_path=str(assets_path),
        chapters_path=str(chapters_path),
    )
    write_json(marker_dir / "source_manifest.json", manifest.to_dict())
    return manifest


def build_source(source_id: str, cfg: AudiobookConfig, enhance: bool = True) -> dict[str, Any]:
    """Build enhanced chapters, search index docs, chapter MP3s and final M4B."""

    manifest = load_source_manifest(source_id, cfg)
    marker_dir = Path(manifest.output_root)
    out_dir = (
        Path(manifest.audio_output_root)
        if getattr(manifest, "audio_output_root", "")
        else audio_source_dir(manifest.source_id, cfg)
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    assets = load_assets(manifest)
    chapters = load_chapters(manifest)

    build_chapters = chapters
    if enhance:
        build_chapters = enhance_chapters(chapters, assets, cfg, enable_cleanup=True)
        write_json(marker_dir / "chapters_enhanced.json", [c.to_dict() for c in build_chapters])

    meili = MeiliClient(cfg)
    index_payload = meili.index_documents(manifest, build_chapters, assets)
    write_index_manifest(marker_dir / "index_manifest.json", index_payload)

    backend = make_tts_backend(cfg)
    chapter_mp3s = render_chapters_to_audio(
        chapters=build_chapters,
        out_dir=out_dir,
        backend=backend,
        ffmpeg_bin=cfg.ffmpeg_bin,
    )

    m4b_path = out_dir / "book.m4b"
    package_m4b(
        ffmpeg_bin=cfg.ffmpeg_bin,
        chapter_mp3s=chapter_mp3s,
        chapter_titles=[c.title for c in build_chapters],
        output_path=m4b_path,
    )

    payload = {
        "source_id": manifest.source_id,
        "output_dir": str(out_dir),
        "marker_dir": str(marker_dir),
        "index_manifest": str(marker_dir / "index_manifest.json"),
        "chapter_mp3s": [str(p) for p in chapter_mp3s],
        "book_m4b": str(m4b_path),
        "chapters_manifest": str(marker_dir / ("chapters_enhanced.json" if enhance else "chapters.json")),
    }
    write_json(marker_dir / "build_manifest.json", payload)
    return payload


def load_source_manifest(source_id: str, cfg: AudiobookConfig) -> SourceManifest:
    candidates = [
        marker_source_dir(source_id, cfg) / "source_manifest.json",
        marker_root(cfg) / source_id / "source_manifest.json",  # legacy mirrored marker/audio roots
        Path(cfg.output_root) / source_id / "source_manifest.json",  # legacy layout
    ]
    for path in candidates:
        if path.exists():
            data = read_json(path)
            return SourceManifest(**_normalize_manifest_paths(data, manifest_path=path.resolve(), cfg=cfg))
    raise FileNotFoundError(f"source manifest not found: {candidates[0]}")


def _normalize_manifest_paths(data: dict[str, Any], *, manifest_path: Path, cfg: AudiobookConfig) -> dict[str, Any]:
    row = dict(data)
    marker_dir = manifest_path.parent.resolve()

    configured_marker = Path(str(row.get("output_root", "") or "")).expanduser()
    if not configured_marker.exists():
        row["output_root"] = str(marker_dir)

    configured_audio_raw = str(row.get("audio_output_root", "") or "")
    configured_audio = Path(configured_audio_raw).expanduser() if configured_audio_raw else None
    if configured_audio is None or not configured_audio.exists():
        source_id = str(row.get("source_id", "") or "").strip()
        if marker_dir.name == "marker" and marker_dir.parent.name == source_id:
            row["audio_output_root"] = str(marker_dir.parent / "audio")
        elif source_id:
            row["audio_output_root"] = str(audio_source_dir(source_id, cfg))

    for key, default_name in (
        ("assets_manifest_path", "assets_manifest.json"),
        ("chapters_path", "chapters.json"),
        ("markdown_path", "source.md"),
    ):
        raw = str(row.get(key, "") or "")
        current = Path(raw).expanduser() if raw else None
        if current is not None and current.exists():
            continue
        fallback = marker_dir / default_name
        if fallback.exists():
            row[key] = str(fallback)

    marker_output_raw = str(row.get("marker_output_dir", "") or "")
    marker_output = Path(marker_output_raw).expanduser() if marker_output_raw else None
    if marker_output is None or not marker_output.exists():
        row["marker_output_dir"] = str(_resolve_marker_output_dir(marker_dir, source_path=row.get("source_path", "")))

    return row


def _resolve_marker_output_dir(marker_dir: Path, source_path: Any) -> Path:
    preferred_stem = Path(str(source_path or "")).expanduser().stem
    for candidate in (marker_dir / "marker", marker_dir):
        if not candidate.exists():
            continue
        return _detect_marker_artifact_dir(candidate, preferred_stem)
    return marker_dir.resolve()


def _detect_marker_artifact_dir(root: Path, preferred_stem: str) -> Path:
    canonical_md = (root / "source.md").resolve()
    markdown_candidates = sorted(path.resolve() for path in root.rglob("*.md"))

    if preferred_stem:
        for candidate in markdown_candidates:
            if candidate.stem == preferred_stem:
                return candidate.parent
        for candidate in markdown_candidates:
            if preferred_stem in candidate.stem:
                return candidate.parent

    for candidate in markdown_candidates:
        if candidate != canonical_md:
            return candidate.parent
    return root.resolve()


def load_assets(source: SourceManifest) -> list[AssetRef]:
    data = read_json(Path(source.assets_manifest_path))
    return [AssetRef(**row) for row in data]


def load_chapters(source: SourceManifest) -> list[ChapterDoc]:
    data = read_json(Path(source.chapters_path))
    return [ChapterDoc(**row) for row in data]


def source_paths(source_id: str, cfg: AudiobookConfig) -> dict[str, str]:
    source = load_source_manifest(source_id, cfg)
    marker_dir = Path(source.output_root)
    out_dir = (
        Path(source.audio_output_root)
        if getattr(source, "audio_output_root", "")
        else audio_source_dir(source.source_id, cfg)
    )
    payload = {
        "marker_dir": str(marker_dir),
        "audio_dir": str(out_dir),
        "source_manifest": str(marker_dir / "source_manifest.json"),
        "source_markdown": source.markdown_path,
        "chapters": source.chapters_path,
        "assets": source.assets_manifest_path,
    }
    for extra in ["chapters_enhanced.json", "index_manifest.json", "build_manifest.json"]:
        p = marker_dir / extra
        if p.exists():
            payload[p.stem] = str(p)

    book = out_dir / "book.m4b"
    if book.exists():
        payload[book.stem] = str(book)

    for mp3 in sorted(out_dir.glob("chapter_*.mp3")):
        payload[mp3.stem] = str(mp3)
    return payload


def make_tts_backend(cfg: AudiobookConfig):
    backend = cfg.tts_backend.strip().lower()
    if backend == "qwen":
        return QwenTTSBackend(model_id=cfg.qwen_tts_model_id, device=cfg.qwen_tts_device)
    if backend == "macos":
        return MacOSTTSBackend(
            ffmpeg_bin=cfg.ffmpeg_bin,
            voice=cfg.macos_tts_voice,
            rate_wpm=int(cfg.macos_tts_rate),
        )
    raise ValueError(f"unsupported tts backend: {cfg.tts_backend}")

from __future__ import annotations

import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import AudiobookConfig


@dataclass(frozen=True)
class MarkerIngestResult:
    markdown_path: Path
    marker_output_dir: Path


def ingest_source_via_marker(
    source_path: Path,
    source_type: str,
    output_dir: Path,
    cfg: AudiobookConfig,
    marker_extra_args: list[str] | None = None,
) -> MarkerIngestResult:
    """Ingest source into canonical markdown and marker artifact directory."""

    marker_extra_args = marker_extra_args or []
    source_path = source_path.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if source_type == "pdf":
        return _ingest_pdf(source_path, output_dir, cfg, marker_extra_args)
    if source_type == "md":
        target = output_dir / "source.md"
        target.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
        return MarkerIngestResult(markdown_path=target, marker_output_dir=output_dir)
    if source_type == "txt":
        target = output_dir / "source.md"
        text = source_path.read_text(encoding="utf-8")
        target.write_text(text, encoding="utf-8")
        return MarkerIngestResult(markdown_path=target, marker_output_dir=output_dir)
    raise ValueError(f"unsupported source_type: {source_type}")


def _ingest_pdf(
    source_path: Path,
    output_dir: Path,
    cfg: AudiobookConfig,
    marker_extra_args: list[str],
) -> MarkerIngestResult:
    marker_output = output_dir / "marker"
    marker_output.mkdir(parents=True, exist_ok=True)

    commands = _build_marker_commands(
        marker_bin=cfg.marker_bin,
        source_path=source_path,
        marker_output=marker_output,
        marker_extra_args=marker_extra_args,
    )
    attempt_errors: list[str] = []
    for cmd in commands:
        try:
            proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        except OSError as exc:
            attempt_errors.append(f"{_format_command(cmd)} -> os error: {exc}")
            continue
        if proc.returncode == 0:
            md_path = _find_markdown_file(marker_output, source_path.stem)
            canonical_md = output_dir / "source.md"
            shutil.copy2(md_path, canonical_md)
            return MarkerIngestResult(markdown_path=canonical_md, marker_output_dir=marker_output)
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit={proc.returncode}"
        attempt_errors.append(f"{_format_command(cmd)} -> {_truncate_error(detail)}")

    raise RuntimeError(
        "marker extraction failed. "
        f"source={source_path} marker_bin={cfg.marker_bin} attempts={'; '.join(attempt_errors) or 'unknown'}"
    )


def _build_marker_commands(
    marker_bin: str,
    source_path: Path,
    marker_output: Path,
    marker_extra_args: list[str],
) -> list[list[str]]:
    commands = [
        [marker_bin, str(source_path), "--output_dir", str(marker_output), *marker_extra_args],
        [marker_bin, "--output_dir", str(marker_output), *marker_extra_args, str(source_path)],
    ]
    # Legacy fallback kept for non-marker_single binaries that do not support --output_dir.
    if Path(marker_bin).name != "marker_single":
        commands.append([marker_bin, str(source_path), str(marker_output), *marker_extra_args])
    return commands


def _format_command(cmd: list[str]) -> str:
    return " ".join(shlex.quote(token) for token in cmd)


def _truncate_error(text: str, max_chars: int = 600) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...(truncated)"


def _find_markdown_file(root: Path, preferred_stem: str) -> Path:
    candidates = sorted(root.rglob("*.md"))
    if not candidates:
        raise FileNotFoundError(f"marker output markdown not found in {root}")

    for candidate in candidates:
        if candidate.stem == preferred_stem:
            return candidate
        if preferred_stem in candidate.stem:
            return candidate
    return candidates[0]

from __future__ import annotations

import subprocess
from pathlib import Path


def transcode_wav_to_mp3(ffmpeg_bin: str, wav_path: Path, mp3_path: Path) -> None:
    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(wav_path),
        "-vn",
        "-codec:a",
        "libmp3lame",
        "-q:a",
        "2",
        str(mp3_path),
    ]
    _run_cmd(cmd, "wav->mp3 transcode failed")


def package_m4b(
    ffmpeg_bin: str,
    chapter_mp3s: list[Path],
    chapter_titles: list[str],
    output_path: Path,
) -> None:
    """Package chapter MP3 files into a single M4B audiobook."""

    if not chapter_mp3s:
        raise ValueError("cannot package empty chapter list")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = output_path.parent / ".packaging"
    work_dir.mkdir(parents=True, exist_ok=True)

    concat_path = work_dir / "chapters.concat.txt"
    concat_path.write_text(
        "\n".join([f"file '{p.resolve()}'" for p in chapter_mp3s]),
        encoding="utf-8",
    )

    metadata_path = work_dir / "chapters.ffmeta"
    ffprobe_bin = _resolve_ffprobe(ffmpeg_bin)
    metadata_text = _build_ffmetadata(ffprobe_bin, chapter_mp3s, chapter_titles)
    metadata_path.write_text(metadata_text, encoding="utf-8")

    cmd = [
        ffmpeg_bin,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-i",
        str(metadata_path),
        "-map_metadata",
        "1",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        str(output_path),
    ]
    _run_cmd(cmd, "m4b packaging failed")


def _build_ffmetadata(ffprobe_bin: str, chapter_mp3s: list[Path], titles: list[str]) -> str:
    durations = [_probe_duration_ms(ffprobe_bin, path) for path in chapter_mp3s]
    if any(d is None for d in durations):
        # Keep basic metadata even when duration probing is unavailable.
        return ";FFMETADATA1\ntitle=Audiobook\nartist=Larrak Audio\n"

    text = [";FFMETADATA1", "title=Audiobook", "artist=Larrak Audio"]
    start = 0
    for idx, dur in enumerate(durations):
        assert dur is not None
        end = start + int(dur)
        title = titles[idx] if idx < len(titles) else f"Chapter {idx + 1}"
        text.extend(
            [
                "[CHAPTER]",
                "TIMEBASE=1/1000",
                f"START={start}",
                f"END={max(start + 1, end)}",
                f"title={title}",
            ]
        )
        start = end
    return "\n".join(text) + "\n"


def _probe_duration_ms(ffprobe_bin: str, audio_path: Path) -> int | None:
    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except OSError:
        return None
    if proc.returncode != 0:
        return None

    text = proc.stdout.strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        return None
    return int(seconds * 1000.0)


def _resolve_ffprobe(ffmpeg_bin: str) -> str:
    if ffmpeg_bin.endswith("ffmpeg"):
        return ffmpeg_bin[:-6] + "ffprobe"
    return "ffprobe"


def _run_cmd(cmd: list[str], err_prefix: str) -> None:
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except OSError as exc:
        raise RuntimeError(f"{err_prefix}: {exc}") from exc

    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit={proc.returncode}"
        raise RuntimeError(f"{err_prefix}: {detail}")

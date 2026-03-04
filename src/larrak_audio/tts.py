from __future__ import annotations

import re
import wave
from abc import ABC, abstractmethod
from pathlib import Path

from .packager import transcode_wav_to_mp3
from .types import ChapterDoc


class TTSBackend(ABC):
    """Abstract speech backend."""

    @abstractmethod
    def synthesize_to_wav(self, text: str, wav_path: Path) -> None:
        """Render text to mono/stereo WAV file."""


def render_chapters_to_audio(
    chapters: list[ChapterDoc],
    out_dir: Path,
    backend: TTSBackend,
    ffmpeg_bin: str,
) -> list[Path]:
    """Synthesize chapter WAV files and transcode to MP3 files."""

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = out_dir / "tmp_wav_parts"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    chapter_mp3s: list[Path] = []

    for chapter_idx, chapter in enumerate(chapters):
        chapter_name = f"chapter_{chapter_idx + 1:02d}"
        chapter_wav = out_dir / f"{chapter_name}.wav"
        chapter_mp3 = out_dir / f"{chapter_name}.mp3"

        segments = segment_text_for_tts(chapter.text)
        if not segments:
            segments = ["This chapter contains no readable text."]

        part_paths: list[Path] = []
        for seg_idx, segment in enumerate(segments):
            part = tmp_dir / f"{chapter_name}_part_{seg_idx:04d}.wav"
            backend.synthesize_to_wav(segment, part)
            part_paths.append(part)

        merge_wav_parts(part_paths, chapter_wav)
        transcode_wav_to_mp3(ffmpeg_bin=ffmpeg_bin, wav_path=chapter_wav, mp3_path=chapter_mp3)
        chapter_mp3s.append(chapter_mp3)

    return chapter_mp3s


def segment_text_for_tts(text: str, max_chars: int = 800) -> list[str]:
    """Split markdown-ish chapter text into narration-friendly chunks."""

    clean = strip_markdown_for_tts(text)
    if not clean:
        return []

    sentences = re.split(r"(?<=[.!?])\s+", clean)
    out: list[str] = []
    cur: list[str] = []
    cur_len = 0

    for sentence in sentences:
        s = sentence.strip()
        if not s:
            continue
        add = len(s) + (1 if cur else 0)
        if cur_len + add <= max_chars:
            cur.append(s)
            cur_len += add
            continue

        if cur:
            out.append(" ".join(cur))
        if len(s) <= max_chars:
            cur = [s]
            cur_len = len(s)
        else:
            # Hard-wrap very long sentence by words.
            words = s.split()
            block: list[str] = []
            block_len = 0
            for word in words:
                w_add = len(word) + (1 if block else 0)
                if block_len + w_add <= max_chars:
                    block.append(word)
                    block_len += w_add
                else:
                    out.append(" ".join(block))
                    block = [word]
                    block_len = len(word)
            if block:
                cur = block
                cur_len = block_len
            else:
                cur = []
                cur_len = 0

    if cur:
        out.append(" ".join(cur))
    return out


def strip_markdown_for_tts(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\|", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def merge_wav_parts(parts: list[Path], out_wav: Path) -> None:
    if not parts:
        raise ValueError("no wav parts to merge")

    with wave.open(str(parts[0]), "rb") as first:
        params = first.getparams()
        frames = [first.readframes(first.getnframes())]

    for path in parts[1:]:
        with wave.open(str(path), "rb") as wf:
            if wf.getnchannels() != params.nchannels:
                raise ValueError(f"wav channel mismatch for {path}")
            if wf.getsampwidth() != params.sampwidth:
                raise ValueError(f"wav sample-width mismatch for {path}")
            if wf.getframerate() != params.framerate:
                raise ValueError(f"wav sample-rate mismatch for {path}")
            frames.append(wf.readframes(wf.getnframes()))

    out_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_wav), "wb") as out:
        out.setnchannels(params.nchannels)
        out.setsampwidth(params.sampwidth)
        out.setframerate(params.framerate)
        for chunk in frames:
            out.writeframes(chunk)

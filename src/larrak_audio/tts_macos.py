from __future__ import annotations

import subprocess
from pathlib import Path

from .tts import TTSBackend


class MacOSTTSBackend(TTSBackend):
    """macOS `say`-based TTS backend with ffmpeg conversion to WAV."""

    def __init__(self, ffmpeg_bin: str, voice: str = "Samantha", rate_wpm: int = 185) -> None:
        self.ffmpeg_bin = ffmpeg_bin
        self.voice = voice
        self.rate_wpm = int(rate_wpm)

    def synthesize_to_wav(self, text: str, wav_path: Path) -> None:
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_aiff = wav_path.with_suffix(".aiff")

        # Some macOS `say` builds only support [-v voice] [-o out] [-f in | message].
        # Keep rate as a config field for future extensions, but do not pass `-r`.
        say_cmd = ["say", "-v", self.voice, "-o", str(tmp_aiff), text]
        _run_cmd(say_cmd, "macOS say synthesis failed")

        ffmpeg_cmd = [
            self.ffmpeg_bin,
            "-y",
            "-i",
            str(tmp_aiff),
            "-ac",
            "1",
            "-ar",
            "22050",
            str(wav_path),
        ]
        try:
            _run_cmd(ffmpeg_cmd, "macOS say wav conversion failed")
        finally:
            tmp_aiff.unlink(missing_ok=True)
        if not wav_path.exists() or wav_path.stat().st_size <= 44:
            raise RuntimeError("macOS say wav conversion failed: empty audio output")


def _run_cmd(cmd: list[str], prefix: str) -> None:
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except OSError as exc:
        raise RuntimeError(f"{prefix}: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit={proc.returncode}"
        raise RuntimeError(f"{prefix}: {detail}")

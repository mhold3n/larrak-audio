from __future__ import annotations

import math
import os
import wave
from pathlib import Path
from typing import Any

import numpy as np

from .tts import TTSBackend


class QwenTTSBackend(TTSBackend):
    """Local Hugging Face-backed Qwen TTS backend.

    The backend attempts to load `transformers.pipeline(task="text-to-speech")`.
    If loading or inference fails and `QWEN_TTS_ALLOW_FALLBACK=1`, a deterministic
    tone-based placeholder WAV is produced to keep pipeline integration testable.
    """

    def __init__(self, model_id: str, device: str = "cpu") -> None:
        self.model_id = model_id
        self.device = device
        self._pipe: Any = None

    def synthesize_to_wav(self, text: str, wav_path: Path) -> None:
        wav_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            pipe = self._pipeline()
            result = pipe(text)
            audio, sample_rate = _extract_audio(result)
            _write_wav(audio, sample_rate, wav_path)
            return
        except Exception as exc:
            if os.environ.get("QWEN_TTS_ALLOW_FALLBACK", "0") != "1":
                raise RuntimeError(
                    "Qwen TTS inference failed. Set QWEN_TTS_ALLOW_FALLBACK=1 to "
                    "emit placeholder audio during local bring-up. "
                    f"error={exc}"
                ) from exc
            _write_fallback_tone(text, wav_path)

    def _pipeline(self) -> Any:
        if self._pipe is not None:
            return self._pipe

        from transformers import pipeline

        if self.device == "cpu":
            self._pipe = pipeline("text-to-speech", model=self.model_id, device=-1)
            return self._pipe

        # On Apple Silicon, newer transformers builds accept device="mps".
        try:
            self._pipe = pipeline("text-to-speech", model=self.model_id, device=self.device)
        except TypeError:
            # Compatibility fallback.
            self._pipe = pipeline("text-to-speech", model=self.model_id, device=-1)
        return self._pipe


def _extract_audio(result: Any) -> tuple[np.ndarray, int]:
    if isinstance(result, dict):
        if "audio" in result and "sampling_rate" in result:
            audio = np.asarray(result["audio"], dtype=np.float32)
            sr = int(result["sampling_rate"])
            return audio, sr
        if "wav" in result and "sampling_rate" in result:
            audio = np.asarray(result["wav"], dtype=np.float32)
            sr = int(result["sampling_rate"])
            return audio, sr

    if isinstance(result, tuple) and len(result) == 2:
        audio = np.asarray(result[0], dtype=np.float32)
        sr = int(result[1])
        return audio, sr

    raise ValueError(f"unexpected TTS output format: {type(result)}")


def _write_wav(audio: np.ndarray, sample_rate: int, wav_path: Path) -> None:
    pcm = _normalize_audio_shape(audio)
    channels = 1 if pcm.ndim == 1 else int(pcm.shape[1])
    pcm = np.clip(pcm, -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype(np.int16)

    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())


def _normalize_audio_shape(audio: np.ndarray) -> np.ndarray:
    if audio.ndim == 1:
        return audio
    if audio.ndim != 2:
        raise ValueError(f"unsupported audio shape: {audio.shape}")

    # Many TTS pipelines emit [channels, samples]. Convert to [samples, channels].
    if audio.shape[0] <= 8 and audio.shape[1] > audio.shape[0]:
        audio = audio.T

    if audio.shape[1] > 8:
        raise ValueError(f"unsupported channel count in audio output: {audio.shape}")
    if audio.shape[1] == 1:
        return audio[:, 0]
    return audio


def _write_fallback_tone(text: str, wav_path: Path) -> None:
    """Deterministic local placeholder used only when explicitly allowed."""

    sample_rate = 22050
    duration = max(0.5, min(12.0, len(text) / 30.0))
    n_samples = int(sample_rate * duration)
    freq = 180.0 + float(len(text) % 80)

    data = np.zeros(n_samples, dtype=np.float32)
    for i in range(n_samples):
        t = i / sample_rate
        envelope = min(1.0, t * 3.0) * min(1.0, (duration - t) * 3.0)
        data[i] = 0.22 * envelope * math.sin(2.0 * math.pi * freq * t)

    _write_wav(data, sample_rate, wav_path)

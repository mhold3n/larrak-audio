from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from larrak_audio.tts_qwen import _normalize_audio_shape, _write_wav


def test_normalize_audio_shape_transposes_channel_first_mono() -> None:
    samples = 1600
    data = np.zeros((1, samples), dtype=np.float32)
    norm = _normalize_audio_shape(data)
    assert norm.shape == (samples,)


def test_normalize_audio_shape_transposes_channel_first_stereo() -> None:
    samples = 1600
    data = np.zeros((2, samples), dtype=np.float32)
    norm = _normalize_audio_shape(data)
    assert norm.shape == (samples, 2)


def test_write_wav_accepts_channel_first_layout(tmp_path: Path) -> None:
    samples = 2205
    data = np.zeros((1, samples), dtype=np.float32)
    out = tmp_path / "mono.wav"
    _write_wav(data, 22050, out)

    with wave.open(str(out), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == 22050
        assert wf.getnframes() == samples


def test_normalize_audio_shape_rejects_excessive_channels() -> None:
    data = np.zeros((100, 100), dtype=np.float32)
    with pytest.raises(ValueError, match="unsupported channel count"):
        _normalize_audio_shape(data)


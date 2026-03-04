from __future__ import annotations

import pytest

from larrak_audio.config import AudiobookConfig
from larrak_audio.pipeline import make_tts_backend
from larrak_audio.tts_macos import MacOSTTSBackend
from larrak_audio.tts_qwen import QwenTTSBackend


def test_make_tts_backend_qwen() -> None:
    cfg = AudiobookConfig(tts_backend="qwen", qwen_tts_model_id="x/y", qwen_tts_device="cpu")
    backend = make_tts_backend(cfg)
    assert isinstance(backend, QwenTTSBackend)


def test_make_tts_backend_macos() -> None:
    cfg = AudiobookConfig(tts_backend="macos", macos_tts_voice="Samantha", macos_tts_rate="180")
    backend = make_tts_backend(cfg)
    assert isinstance(backend, MacOSTTSBackend)


def test_make_tts_backend_rejects_unknown() -> None:
    cfg = AudiobookConfig(tts_backend="nope")
    with pytest.raises(ValueError, match="unsupported tts backend"):
        make_tts_backend(cfg)


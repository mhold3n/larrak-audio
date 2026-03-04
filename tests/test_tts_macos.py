from __future__ import annotations

from pathlib import Path

from larrak_audio.tts_macos import MacOSTTSBackend


def test_macos_backend_invokes_say_then_ffmpeg_and_cleans_tmp(tmp_path: Path, monkeypatch) -> None:
    wav_path = tmp_path / "chapter_01.wav"
    calls: list[list[str]] = []

    class FakeProc:
        def __init__(self, returncode: int = 0):
            self.returncode = returncode
            self.stderr = ""
            self.stdout = ""

    def fake_run(cmd, check, capture_output, text):
        _ = check, capture_output, text
        calls.append(list(cmd))
        if cmd[0] == "say":
            out_idx = cmd.index("-o") + 1
            Path(cmd[out_idx]).write_bytes(b"FORM")
        if cmd[0] == "ffmpeg":
            Path(cmd[-1]).write_bytes(b"RIFF" + (b"\x00" * 64))
        return FakeProc()

    monkeypatch.setattr("larrak_audio.tts_macos.subprocess.run", fake_run)

    backend = MacOSTTSBackend(ffmpeg_bin="ffmpeg", voice="Samantha", rate_wpm=180)
    backend.synthesize_to_wav("hello world", wav_path)

    assert len(calls) == 2
    assert calls[0][0] == "say"
    assert "-r" not in calls[0]
    assert calls[1][0] == "ffmpeg"
    assert wav_path.exists()
    assert not wav_path.with_suffix(".aiff").exists()

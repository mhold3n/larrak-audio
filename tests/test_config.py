from __future__ import annotations

from pathlib import Path

import larrak_audio.config as config


def _patch_project_root(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(config, "_project_root", lambda: root)
    monkeypatch.setattr(config, "load_dotenv", lambda *_args, **_kwargs: None)


def test_load_config_prefers_local_tools_bin_annas_mcp(tmp_path: Path, monkeypatch) -> None:
    _patch_project_root(monkeypatch, tmp_path)
    monkeypatch.delenv("ANNAS_MCP_BIN", raising=False)
    monkeypatch.setenv("LARRAK_AUDIO_OUTPUT_ROOT", str(tmp_path / "outputs"))
    monkeypatch.setenv("LARRAK_AUDIO_QUEUE_DB", str(tmp_path / "outputs" / "jobs.sqlite3"))

    annas_bin = tmp_path / "tools" / "bin" / "annas-mcp"
    annas_bin.parent.mkdir(parents=True, exist_ok=True)
    annas_bin.write_text("#!/bin/sh\nexit 0\n")
    annas_bin.chmod(0o755)

    cfg = config.load_audiobook_config()

    assert cfg.annas_mcp_bin == str(annas_bin.resolve())


def test_load_config_keeps_explicit_annas_bin_override(tmp_path: Path, monkeypatch) -> None:
    _patch_project_root(monkeypatch, tmp_path)
    monkeypatch.setenv("ANNAS_MCP_BIN", "/custom/path/annas-mcp")
    monkeypatch.setenv("LARRAK_AUDIO_OUTPUT_ROOT", str(tmp_path / "outputs"))
    monkeypatch.setenv("LARRAK_AUDIO_QUEUE_DB", str(tmp_path / "outputs" / "jobs.sqlite3"))

    cfg = config.load_audiobook_config()

    assert cfg.annas_mcp_bin == "/custom/path/annas-mcp"


def test_load_config_falls_back_to_path_annas_mcp_when_local_missing(tmp_path: Path, monkeypatch) -> None:
    _patch_project_root(monkeypatch, tmp_path)
    monkeypatch.delenv("ANNAS_MCP_BIN", raising=False)
    monkeypatch.setenv("LARRAK_AUDIO_OUTPUT_ROOT", str(tmp_path / "outputs"))
    monkeypatch.setenv("LARRAK_AUDIO_QUEUE_DB", str(tmp_path / "outputs" / "jobs.sqlite3"))

    cfg = config.load_audiobook_config()

    assert cfg.annas_mcp_bin == "annas-mcp"

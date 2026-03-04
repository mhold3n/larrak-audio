from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class AudiobookConfig:
    annas_mcp_bin: str = "annas-mcp"
    annas_secret_key: str = ""
    annas_download_path: str = ""
    annas_base_url: str = "annas-archive.gl"
    annas_min_download_size_mb: str = "1.0"
    annas_min_interval_s: str = "2.0"
    annas_max_retries: str = "2"
    annas_retry_backoff_s: str = "2.0"
    annas_cmd_timeout_s: str = "1800"
    scopus_api_key: str = ""
    scopus_base_url: str = "https://api.elsevier.com/"
    scopus_min_interval_s: str = "1.0"
    scopus_max_retries: str = "3"
    scopus_retry_backoff_s: str = "2.0"
    scopus_request_timeout_s: str = "30"
    scopus_min_remaining_quota: str = "25"
    marker_bin: str = "marker_single"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model_cleanup: str = "qwen2.5:7b-instruct"
    meili_url: str = "http://127.0.0.1:7700"
    meili_master_key: str = ""
    meili_key_doc_chunks: str = ""
    meili_key_doc_chapters: str = ""
    meili_key_doc_assets: str = ""
    meili_index_doc_chunks: str = "doc_chunks_v1"
    meili_index_doc_chapters: str = "doc_chapters_v1"
    meili_index_doc_assets: str = "doc_assets_v1"
    qwen_tts_model_id: str = "Qwen/Qwen3-TTS-0.6B"
    qwen_tts_device: str = "mps"
    tts_backend: str = "qwen"
    macos_tts_voice: str = "Samantha"
    macos_tts_rate: str = "185"
    ffmpeg_bin: str = "ffmpeg"
    output_root: str = "outputs/audiobooks"
    queue_db_path: str = "outputs/audiobooks/jobs.sqlite3"

    @property
    def output_root_path(self) -> Path:
        return Path(self.output_root)

    @property
    def queue_db(self) -> Path:
        return Path(self.queue_db_path)


_ENV_MAP = {
    "annas_mcp_bin": "ANNAS_MCP_BIN",
    "annas_secret_key": "ANNAS_SECRET_KEY",
    "annas_download_path": "ANNAS_DOWNLOAD_PATH",
    "annas_base_url": "ANNAS_BASE_URL",
    "annas_min_download_size_mb": "ANNAS_MIN_DOWNLOAD_SIZE_MB",
    "annas_min_interval_s": "ANNAS_MIN_INTERVAL_S",
    "annas_max_retries": "ANNAS_MAX_RETRIES",
    "annas_retry_backoff_s": "ANNAS_RETRY_BACKOFF_S",
    "annas_cmd_timeout_s": "ANNAS_CMD_TIMEOUT_S",
    "scopus_api_key": "SCOPUS_API_KEY",
    "scopus_base_url": "SCOPUS_BASE_URL",
    "scopus_min_interval_s": "SCOPUS_MIN_INTERVAL_S",
    "scopus_max_retries": "SCOPUS_MAX_RETRIES",
    "scopus_retry_backoff_s": "SCOPUS_RETRY_BACKOFF_S",
    "scopus_request_timeout_s": "SCOPUS_REQUEST_TIMEOUT_S",
    "scopus_min_remaining_quota": "SCOPUS_MIN_REMAINING_QUOTA",
    "marker_bin": "MARKER_BIN",
    "ollama_base_url": "OLLAMA_BASE_URL",
    "ollama_model_cleanup": "OLLAMA_MODEL_CLEANUP",
    "meili_url": "MEILI_URL",
    "meili_master_key": "MEILI_MASTER_KEY",
    "meili_key_doc_chunks": "MEILI_KEY_DOC_CHUNKS",
    "meili_key_doc_chapters": "MEILI_KEY_DOC_CHAPTERS",
    "meili_key_doc_assets": "MEILI_KEY_DOC_ASSETS",
    "meili_index_doc_chunks": "MEILI_INDEX_DOC_CHUNKS",
    "meili_index_doc_chapters": "MEILI_INDEX_DOC_CHAPTERS",
    "meili_index_doc_assets": "MEILI_INDEX_DOC_ASSETS",
    "qwen_tts_model_id": "QWEN_TTS_MODEL_ID",
    "qwen_tts_device": "QWEN_TTS_DEVICE",
    "tts_backend": "TTS_BACKEND",
    "macos_tts_voice": "MACOS_TTS_VOICE",
    "macos_tts_rate": "MACOS_TTS_RATE",
    "ffmpeg_bin": "FFMPEG_BIN",
    "output_root": "LARRAK_AUDIO_OUTPUT_ROOT",
    "queue_db_path": "LARRAK_AUDIO_QUEUE_DB",
}


def _project_root() -> Path:
    """Project root (directory containing pyproject.toml)."""
    root = Path(__file__).resolve().parent.parent.parent
    assert (root / "pyproject.toml").exists(), "expected pyproject.toml in project root"
    return root


def _resolve_default_annas_mcp_bin(project_root: Path) -> str:
    candidate = (project_root / "tools" / "bin" / "annas-mcp").resolve()
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return "annas-mcp"


def load_audiobook_config() -> AudiobookConfig:
    """Load static env-first settings for audiobook pipeline.
    Loads .env from project root if present; environment variables override.
    """
    project_root = _project_root()
    load_dotenv(project_root / ".env")

    kwargs: dict[str, str] = {}
    for field_name, env_name in _ENV_MAP.items():
        value = os.environ.get(env_name)
        if value is None:
            continue
        value = value.strip()
        if not value:
            continue
        kwargs[field_name] = value

    kwargs.setdefault("annas_mcp_bin", _resolve_default_annas_mcp_bin(project_root))

    cfg = AudiobookConfig(**kwargs)
    Path(cfg.output_root).mkdir(parents=True, exist_ok=True)
    Path(cfg.queue_db_path).parent.mkdir(parents=True, exist_ok=True)
    return cfg

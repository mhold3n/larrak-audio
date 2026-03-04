from __future__ import annotations

"""Audiobook pipeline package.

Pipeline: research sources -> markdown/chapters -> search index -> audio artifacts.
"""

from .config import AudiobookConfig, load_audiobook_config
from .pipeline import build_source, ingest_source
from .queue import JobQueue
from .research_annas import run_research_annas
from .research_scopus import run_research_scopus

__all__ = [
    "AudiobookConfig",
    "JobQueue",
    "build_source",
    "ingest_source",
    "load_audiobook_config",
    "run_research_annas",
    "run_research_scopus",
]

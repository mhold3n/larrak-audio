from __future__ import annotations

from pathlib import Path

import pytest

import larrak_audio.gui_controller as gui_controller
from larrak_audio.config import AudiobookConfig
from larrak_audio.gui_controller import AnnasCandidate, GuiController, GuiSettings
from larrak_audio.utils import read_json


def _make_cfg(tmp_path: Path) -> AudiobookConfig:
    return AudiobookConfig(
        output_root=str(tmp_path / "outputs"),
        queue_db_path=str(tmp_path / "outputs" / "jobs.sqlite3"),
        annas_secret_key="annas-secret",
        scopus_api_key="scopus-secret",
    )


def test_search_all_aggregates_annas_and_scopus(tmp_path: Path, monkeypatch) -> None:
    cfg = _make_cfg(tmp_path)
    controller = GuiController(cfg)

    def fake_annas_search(*, cfg, kind, query, min_download_size_bytes):
        _ = cfg, query, min_download_size_bytes
        return {
            "candidates": [
                {
                    "kind": kind,
                    "title": f"{kind} title",
                    "hash": f"{kind}-hash",
                    "size": "2.1MB",
                    "url": f"https://annas/{kind}",
                }
            ]
        }

    def fake_scopus_search(*, cfg, query, count, sort):
        _ = cfg, query, count, sort
        return {
            "results": [
                {
                    "title": "ISO 15550 test",
                    "creator": "Author A",
                    "doi": "10.1000/test",
                    "scopus_id": "123",
                    "cited_by_count": "7",
                }
            ]
        }

    monkeypatch.setattr(gui_controller, "run_annas_search", fake_annas_search)
    monkeypatch.setattr(gui_controller, "run_scopus_search", fake_scopus_search)

    bundle = controller.search_all("ISO 15550", GuiSettings())

    assert len(bundle.annas_results) == 2
    assert {row.annas_kind for row in bundle.annas_results} == {"book", "article"}
    assert len(bundle.scopus_results) == 1
    assert bundle.scopus_results[0]["scopus_id"] == "123"


def test_search_all_advanced_builds_provider_queries(tmp_path: Path, monkeypatch) -> None:
    cfg = _make_cfg(tmp_path)
    controller = GuiController(cfg)

    annas_queries: list[str] = []
    scopus_queries: list[str] = []

    def fake_annas_search(*, cfg, kind, query, min_download_size_bytes):
        _ = cfg, kind, min_download_size_bytes
        annas_queries.append(str(query))
        return {"candidates": []}

    def fake_scopus_search(*, cfg, query, count, sort):
        _ = cfg, count, sort
        scopus_queries.append(str(query))
        return {"results": []}

    monkeypatch.setattr(gui_controller, "run_annas_search", fake_annas_search)
    monkeypatch.setattr(gui_controller, "run_scopus_search", fake_scopus_search)

    controller.search_all(
        'author>="fitzgerald", title="the great gadsby", metadata>="great", metadata<="decaprio"',
        GuiSettings(),
        search_mode="advanced",
    )

    assert annas_queries == [
        'author:"fitzgerald" title:"the great gadsby" "great" -"decaprio"',
        'author:"fitzgerald" title:"the great gadsby" "great" -"decaprio"',
    ]
    assert scopus_queries == [
        'AUTH("fitzgerald") AND TITLE("the great gadsby") AND ALL("great") AND NOT (ALL("decaprio"))'
    ]


def test_advanced_parser_accepts_space_delimited_clauses() -> None:
    clauses = gui_controller._parse_advanced_search_clauses(
        'author>="fitzgerald" title="the great gadsby" metadata>="great" metadata<="decaprio"'
    )
    assert [(item.field, item.op, item.value) for item in clauses] == [
        ("author", ">=", "fitzgerald"),
        ("title", "=", "the great gadsby"),
        ("metadata", ">=", "great"),
        ("metadata", "<=", "decaprio"),
    ]


def test_advanced_parser_rejects_unknown_field() -> None:
    with pytest.raises(ValueError, match="unsupported advanced-search field"):
        gui_controller._parse_advanced_search_clauses('isbn="1234567890"')


def test_enqueue_annas_candidate_deduplicates(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    controller = GuiController(cfg)

    candidate = AnnasCandidate(
        annas_kind="book",
        annas_hash="abc123",
        annas_title="Test Book",
        annas_size="3.1MB",
        annas_url="https://annas/book",
        query_context="test",
    )

    first, added_first = controller.enqueue_annas_candidate(
        candidate,
        origin_meta={"origin_provider": "annas", "origin_title": "Test Book", "query_context": "test"},
    )
    second, added_second = controller.enqueue_annas_candidate(
        candidate,
        origin_meta={"origin_provider": "annas", "origin_title": "Test Book", "query_context": "test"},
    )

    assert added_first is True
    assert added_second is False
    assert first.item_id == second.item_id
    assert len(controller.queue_items()) == 1


def test_resolve_scopus_to_annas_requires_manual_enqueue(tmp_path: Path, monkeypatch) -> None:
    cfg = _make_cfg(tmp_path)
    controller = GuiController(cfg)

    def fake_annas_search(*, cfg, kind, query, min_download_size_bytes):
        _ = cfg, query, min_download_size_bytes
        return {
            "candidates": [
                {
                    "kind": kind,
                    "title": f"Resolved {kind}",
                    "hash": f"{kind}-resolved-hash",
                    "size": "1.8MB",
                    "url": f"https://annas/{kind}/resolved",
                }
            ]
        }

    monkeypatch.setattr(gui_controller, "run_annas_search", fake_annas_search)

    candidates = controller.resolve_scopus_to_annas(
        {"title": "ISO 3046-1", "doi": "10.1000/3046-1"},
        GuiSettings(),
    )

    assert len(candidates) == 2
    assert len(controller.queue_items()) == 0


def test_run_batch_continues_on_failures_and_writes_summary(tmp_path: Path, monkeypatch) -> None:
    cfg = _make_cfg(tmp_path)
    controller = GuiController(cfg)

    first_candidate = AnnasCandidate(
        annas_kind="book",
        annas_hash="ok-hash",
        annas_title="OK File",
        annas_size="2.0MB",
        annas_url="https://annas/ok",
        query_context="ok",
    )
    second_candidate = AnnasCandidate(
        annas_kind="article",
        annas_hash="fail-hash",
        annas_title="Fail File",
        annas_size="2.0MB",
        annas_url="https://annas/fail",
        query_context="fail",
    )

    first_item, _ = controller.enqueue_annas_candidate(
        first_candidate,
        origin_meta={"origin_provider": "annas", "origin_title": "OK File", "query_context": "ok"},
    )
    second_item, _ = controller.enqueue_annas_candidate(
        second_candidate,
        origin_meta={"origin_provider": "annas", "origin_title": "Fail File", "query_context": "fail"},
    )

    def fake_marker_ready(_cfg: AudiobookConfig) -> None:
        return None

    def fake_research_annas(
        *,
        cfg,
        action,
        kind,
        identifier,
        ingest,
        build,
        enhance,
        marker_extra_args,
        min_download_size_mb,
    ):
        _ = cfg, action, kind, ingest, build, enhance, marker_extra_args, min_download_size_mb
        if identifier == "ok-hash":
            return {
                "exit_code": 0,
                "summary_path": "summary_ok.json",
                "downloaded_files": ["/tmp/ok.pdf"],
                "results": [{"source_id": "src-ok", "error": None}],
            }
        return {
            "exit_code": 1,
            "summary_path": "summary_fail.json",
            "downloaded_files": ["/tmp/fail.pdf"],
            "error": "build failed",
            "results": [{"source_id": "src-fail", "error": "build failed"}],
        }

    def fake_source_paths(source_id: str, cfg: AudiobookConfig) -> dict[str, str]:
        _ = cfg
        return {"source_id": source_id, "book": f"/tmp/{source_id}.m4b"}

    monkeypatch.setattr(gui_controller, "ensure_marker_ready", fake_marker_ready)
    monkeypatch.setattr(gui_controller, "run_research_annas", fake_research_annas)
    monkeypatch.setattr(gui_controller, "source_paths", fake_source_paths)

    events: list[dict[str, object]] = []
    summary = controller.run_batch(
        [first_item, second_item],
        GuiSettings(enhance=False, annas_min_download_size_mb=1.0, marker_extra_args=("--page_range", "0")),
        progress_callback=lambda event: events.append(dict(event)),
    )

    assert summary["total"] == 2
    assert summary["succeeded"] == 1
    assert summary["failed"] == 1
    assert summary["exit_code"] == 1
    assert Path(summary["summary_path"]).exists()

    persisted = read_json(Path(summary["summary_path"]))
    assert persisted["total"] == 2
    assert persisted["items"][0]["status"] == "succeeded"
    assert persisted["items"][1]["status"] == "failed"

    event_types = [str(row.get("type")) for row in events]
    assert "batch_started" in event_types
    assert event_types.count("item_started") == 2
    assert event_types.count("item_finished") == 2
    assert "batch_finished" in event_types

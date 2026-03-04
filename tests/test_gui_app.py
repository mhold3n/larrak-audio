from __future__ import annotations

import os
import tkinter as tk

import pytest

from larrak_audio.config import AudiobookConfig
from larrak_audio.gui_app import LarrakGuiApp
from larrak_audio.gui_controller import AnnasCandidate

pytestmark = pytest.mark.skipif(
    os.environ.get("ENABLE_TK_TESTS", "").strip() != "1",
    reason="Tk smoke tests are opt-in (set ENABLE_TK_TESTS=1).",
)


def _make_root() -> tk.Tk:
    root = tk.Tk()
    root.withdraw()
    return root


def test_gui_instantiates_and_disables_controls_when_keys_missing(tmp_path) -> None:
    root = _make_root()
    cfg = AudiobookConfig(
        output_root=str(tmp_path / "outputs"),
        queue_db_path=str(tmp_path / "outputs" / "jobs.sqlite3"),
        annas_secret_key="",
        scopus_api_key="",
    )

    app = LarrakGuiApp(root, cfg=cfg)

    assert "ANNAS_SECRET_KEY" in app.warning_var.get()
    assert "SCOPUS_API_KEY" in app.warning_var.get()
    assert str(app.download_btn.cget("state")) == "disabled"
    assert str(app.add_scopus_btn.cget("state")) == "disabled"

    root.destroy()


def test_event_queue_updates_status_rows(tmp_path) -> None:
    root = _make_root()
    cfg = AudiobookConfig(
        output_root=str(tmp_path / "outputs"),
        queue_db_path=str(tmp_path / "outputs" / "jobs.sqlite3"),
        annas_secret_key="annas-secret",
        scopus_api_key="scopus-secret",
    )
    app = LarrakGuiApp(root, cfg=cfg)

    candidate = AnnasCandidate(
        annas_kind="book",
        annas_hash="abc123",
        annas_title="Queue Title",
        annas_size="2.2MB",
        annas_url="https://annas/abc123",
        query_context="query",
    )
    item, _ = app.controller.enqueue_annas_candidate(
        candidate,
        origin_meta={"origin_provider": "annas", "origin_title": "Queue Title", "query_context": "query"},
    )
    app._insert_queue_row(item)

    app._ui_events.put(
        {
            "type": "batch_progress",
            "event": {"type": "item_started", "item_id": item.item_id, "index": 1, "total": 1},
        }
    )
    app._drain_ui_events()

    values_running = app.queue_tree.item(str(item.item_id), "values")
    assert values_running[1] == "running"

    app._ui_events.put(
        {
            "type": "batch_progress",
            "event": {"type": "item_finished", "item_id": item.item_id, "ok": False, "error": "boom"},
        }
    )
    app._drain_ui_events()

    values_failed = app.queue_tree.item(str(item.item_id), "values")
    assert values_failed[1] == "failed"

    root.destroy()

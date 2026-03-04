from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from .config import AudiobookConfig
from .gui_controller import AnnasCandidate, GuiController, GuiSettings, QueueItem


class LarrakGuiApp:
    def __init__(
        self,
        root: tk.Tk,
        *,
        cfg: AudiobookConfig,
        enhance: bool = True,
        annas_min_download_size_mb: float | None = None,
        marker_extra_args: list[str] | None = None,
        controller: GuiController | None = None,
    ) -> None:
        self.root = root
        self.cfg = cfg
        self.controller = controller or GuiController(cfg)
        self.settings = GuiSettings(
            enhance=bool(enhance),
            annas_min_download_size_mb=annas_min_download_size_mb,
            marker_extra_args=tuple(marker_extra_args or []),
        )

        self._ui_events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._annas_rows: dict[str, AnnasCandidate] = {}
        self._scopus_rows: dict[str, dict[str, Any]] = {}
        self._queue_rows: dict[int, QueueItem] = {}
        self._last_query = ""
        self._pending_scopus_rows: list[dict[str, Any]] = []
        self._mapping_in_progress = False

        self._build_ui()
        self._refresh_key_warnings()
        self.root.after(100, self._poll_events)

    def _build_ui(self) -> None:
        self.root.title("Larrak Audio - Multi-Source Batch Downloader")
        self.root.geometry("1400x900")

        main = ttk.Frame(self.root, padding=8)
        main.pack(fill=tk.BOTH, expand=True)

        self.warning_var = tk.StringVar(value="")
        warning = ttk.Label(main, textvariable=self.warning_var, foreground="#8B0000")
        warning.pack(fill=tk.X, pady=(0, 8))

        search_frame = ttk.LabelFrame(main, text="Search")
        search_frame.pack(fill=tk.X, pady=(0, 8))

        self.query_var = tk.StringVar(value="")
        ttk.Label(search_frame, text="Query").pack(side=tk.LEFT, padx=(8, 4), pady=8)
        self.query_entry = ttk.Entry(search_frame, textvariable=self.query_var)
        self.query_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8), pady=8)
        self.search_btn = ttk.Button(search_frame, text="Search", command=self._on_search)
        self.search_btn.pack(side=tk.LEFT, padx=(0, 8), pady=8)

        self.search_status_var = tk.StringVar(value="Idle")
        ttk.Label(search_frame, textvariable=self.search_status_var).pack(side=tk.LEFT, padx=(0, 8), pady=8)

        results_frame = ttk.Frame(main)
        results_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        results_frame.columnconfigure(0, weight=1)
        results_frame.columnconfigure(1, weight=1)
        results_frame.rowconfigure(0, weight=1)

        annas_box = ttk.LabelFrame(results_frame, text="Anna's Results")
        annas_box.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        annas_box.rowconfigure(0, weight=1)
        annas_box.columnconfigure(0, weight=1)

        self.annas_tree = ttk.Treeview(
            annas_box,
            columns=("kind", "title", "size", "hash", "url"),
            show="headings",
            selectmode="extended",
            height=12,
        )
        for col, width in (("kind", 80), ("title", 380), ("size", 90), ("hash", 180), ("url", 320)):
            self.annas_tree.heading(col, text=col)
            self.annas_tree.column(col, width=width, anchor=tk.W)
        self.annas_tree.grid(row=0, column=0, sticky="nsew")
        annas_scroll = ttk.Scrollbar(annas_box, orient=tk.VERTICAL, command=self.annas_tree.yview)
        annas_scroll.grid(row=0, column=1, sticky="ns")
        self.annas_tree.configure(yscrollcommand=annas_scroll.set)

        annas_btns = ttk.Frame(annas_box)
        annas_btns.grid(row=1, column=0, columnspan=2, sticky="ew", pady=6)
        self.add_annas_btn = ttk.Button(annas_btns, text="Add To Download List", command=self._on_add_annas)
        self.add_annas_btn.pack(side=tk.LEFT, padx=(4, 0))

        scopus_box = ttk.LabelFrame(results_frame, text="Scopus Results")
        scopus_box.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        scopus_box.rowconfigure(0, weight=1)
        scopus_box.columnconfigure(0, weight=1)

        self.scopus_tree = ttk.Treeview(
            scopus_box,
            columns=("title", "creator", "doi", "scopus_id", "cited_by_count"),
            show="headings",
            selectmode="extended",
            height=12,
        )
        for col, width in (
            ("title", 360),
            ("creator", 180),
            ("doi", 190),
            ("scopus_id", 120),
            ("cited_by_count", 90),
        ):
            self.scopus_tree.heading(col, text=col)
            self.scopus_tree.column(col, width=width, anchor=tk.W)
        self.scopus_tree.grid(row=0, column=0, sticky="nsew")
        scopus_scroll = ttk.Scrollbar(scopus_box, orient=tk.VERTICAL, command=self.scopus_tree.yview)
        scopus_scroll.grid(row=0, column=1, sticky="ns")
        self.scopus_tree.configure(yscrollcommand=scopus_scroll.set)

        scopus_btns = ttk.Frame(scopus_box)
        scopus_btns.grid(row=1, column=0, columnspan=2, sticky="ew", pady=6)
        self.add_scopus_btn = ttk.Button(scopus_btns, text="Map + Add To Download List", command=self._on_add_scopus)
        self.add_scopus_btn.pack(side=tk.LEFT, padx=(4, 0))

        queue_frame = ttk.LabelFrame(main, text="Download Queue")
        queue_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        queue_frame.rowconfigure(0, weight=1)
        queue_frame.columnconfigure(0, weight=1)

        self.queue_tree = ttk.Treeview(
            queue_frame,
            columns=("item_id", "status", "source", "kind", "hash", "title", "size"),
            show="headings",
            selectmode="extended",
            height=10,
        )
        for col, width in (
            ("item_id", 70),
            ("status", 100),
            ("source", 90),
            ("kind", 70),
            ("hash", 160),
            ("title", 450),
            ("size", 90),
        ):
            self.queue_tree.heading(col, text=col)
            self.queue_tree.column(col, width=width, anchor=tk.W)
        self.queue_tree.grid(row=0, column=0, sticky="nsew")
        queue_scroll = ttk.Scrollbar(queue_frame, orient=tk.VERTICAL, command=self.queue_tree.yview)
        queue_scroll.grid(row=0, column=1, sticky="ns")
        self.queue_tree.configure(yscrollcommand=queue_scroll.set)

        queue_btns = ttk.Frame(queue_frame)
        queue_btns.grid(row=1, column=0, columnspan=2, sticky="ew", pady=6)
        self.remove_btn = ttk.Button(queue_btns, text="Remove Selected", command=self._on_remove_queue)
        self.remove_btn.pack(side=tk.LEFT, padx=(4, 8))
        self.clear_btn = ttk.Button(queue_btns, text="Clear Queue", command=self._on_clear_queue)
        self.clear_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.download_btn = ttk.Button(queue_btns, text="Download + Process", command=self._on_download_process)
        self.download_btn.pack(side=tk.RIGHT, padx=(8, 4))

        self.progress_var = tk.StringVar(value="No batch running")
        ttk.Label(main, textvariable=self.progress_var).pack(fill=tk.X, pady=(0, 4))

        logs_frame = ttk.LabelFrame(main, text="Log")
        logs_frame.pack(fill=tk.BOTH, expand=True)
        logs_frame.rowconfigure(0, weight=1)
        logs_frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(logs_frame, height=10, wrap=tk.WORD)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(logs_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.configure(state=tk.DISABLED)

    def _refresh_key_warnings(self) -> None:
        issues: list[str] = []
        has_annas = bool(str(self.cfg.annas_secret_key or "").strip())
        has_scopus = bool(str(self.cfg.scopus_api_key or "").strip())

        if not has_annas:
            issues.append("ANNAS_SECRET_KEY missing: download/process is disabled.")
        if not has_scopus:
            issues.append("SCOPUS_API_KEY missing: Scopus search pane is disabled.")

        self.warning_var.set(" | ".join(issues))
        self.download_btn.configure(state=(tk.NORMAL if has_annas else tk.DISABLED))
        self.add_annas_btn.configure(state=(tk.NORMAL if has_annas else tk.DISABLED))
        self.add_scopus_btn.configure(state=(tk.NORMAL if (has_annas and has_scopus) else tk.DISABLED))

    def _on_search(self) -> None:
        query = self.query_var.get().strip()
        if not query:
            self._log("Search skipped: query is empty.")
            return

        self._last_query = query
        self.search_btn.configure(state=tk.DISABLED)
        self.search_status_var.set("Searching...")
        self._clear_search_results()

        threading.Thread(target=self._search_worker, args=(query,), daemon=True).start()

    def _search_worker(self, query: str) -> None:
        try:
            bundle = self.controller.search_all(query=query, settings=self.settings)
            self._ui_events.put({"type": "search_done", "query": query, "bundle": bundle})
        except Exception as exc:
            self._ui_events.put({"type": "search_error", "query": query, "error": str(exc)})

    def _clear_search_results(self) -> None:
        self._annas_rows.clear()
        self._scopus_rows.clear()
        for tree in (self.annas_tree, self.scopus_tree):
            for item_id in tree.get_children():
                tree.delete(item_id)

    def _on_add_annas(self) -> None:
        selected = list(self.annas_tree.selection())
        if not selected:
            self._log("No Anna's result selected.")
            return

        added_count = 0
        for tree_id in selected:
            candidate = self._annas_rows.get(str(tree_id))
            if candidate is None:
                continue
            item, added = self.controller.enqueue_annas_candidate(
                candidate,
                origin_meta={
                    "origin_provider": "annas",
                    "origin_title": candidate.annas_title,
                    "query_context": candidate.query_context,
                },
            )
            if added:
                added_count += 1
                self._insert_queue_row(item)

        self._log(f"Added {added_count} Anna's result(s) to queue.")

    def _on_add_scopus(self) -> None:
        selected = list(self.scopus_tree.selection())
        if not selected:
            self._log("No Scopus result selected.")
            return

        self._pending_scopus_rows = [self._scopus_rows[item_id] for item_id in selected if item_id in self._scopus_rows]
        if not self._pending_scopus_rows:
            return
        self._resolve_next_scopus_row()

    def _resolve_next_scopus_row(self) -> None:
        if self._mapping_in_progress:
            return
        if not self._pending_scopus_rows:
            self._log("Finished Scopus mapping for selected rows.")
            return

        scopus_row = self._pending_scopus_rows.pop(0)
        self._mapping_in_progress = True
        self._log(f"Resolving Scopus result: {scopus_row.get('title')}")
        threading.Thread(target=self._resolve_scopus_worker, args=(scopus_row,), daemon=True).start()

    def _resolve_scopus_worker(self, scopus_row: dict[str, Any]) -> None:
        try:
            candidates = self.controller.resolve_scopus_to_annas(scopus_row=scopus_row, settings=self.settings)
            self._ui_events.put(
                {
                    "type": "scopus_candidates",
                    "scopus_row": scopus_row,
                    "candidates": candidates,
                    "error": None,
                }
            )
        except Exception as exc:
            self._ui_events.put(
                {
                    "type": "scopus_candidates",
                    "scopus_row": scopus_row,
                    "candidates": [],
                    "error": str(exc),
                }
            )

    def _on_remove_queue(self) -> None:
        selected = list(self.queue_tree.selection())
        if not selected:
            return
        item_ids = {int(item_id) for item_id in selected}
        self.controller.remove_queue_items(item_ids)
        for item_id in selected:
            self.queue_tree.delete(item_id)
            self._queue_rows.pop(int(item_id), None)

    def _on_clear_queue(self) -> None:
        self.controller.clear_queue()
        self._queue_rows.clear()
        for item_id in self.queue_tree.get_children():
            self.queue_tree.delete(item_id)

    def _on_download_process(self) -> None:
        queue_items = self.controller.queue_items()
        if not queue_items:
            self._log("Queue is empty.")
            return

        if not str(self.cfg.annas_secret_key or "").strip():
            messagebox.showerror("Missing Key", "ANNAS_SECRET_KEY is required for downloading and processing.")
            return

        self._set_busy_state(True)
        self.progress_var.set(f"Batch starting ({len(queue_items)} items)...")

        threading.Thread(target=self._batch_worker, args=(queue_items,), daemon=True).start()

    def _batch_worker(self, queue_items: list[QueueItem]) -> None:
        def progress_callback(event: dict[str, Any]) -> None:
            self._ui_events.put({"type": "batch_progress", "event": event})

        summary = self.controller.run_batch(
            queue_items=queue_items,
            settings=self.settings,
            progress_callback=progress_callback,
        )
        self._ui_events.put({"type": "batch_complete", "summary": summary})

    def _insert_queue_row(self, item: QueueItem) -> None:
        self._queue_rows[item.item_id] = item
        self.queue_tree.insert(
            "",
            tk.END,
            iid=str(item.item_id),
            values=(
                item.item_id,
                item.status,
                item.origin_provider,
                item.annas_kind,
                item.annas_hash,
                item.annas_title,
                item.annas_size or "",
            ),
        )

    def _set_busy_state(self, busy: bool) -> None:
        state = tk.DISABLED if busy else tk.NORMAL
        self.search_btn.configure(state=state)
        self.add_annas_btn.configure(state=state)
        self.add_scopus_btn.configure(state=state)
        self.remove_btn.configure(state=state)
        self.clear_btn.configure(state=state)
        self.download_btn.configure(state=state)
        if not busy:
            self._refresh_key_warnings()

    def _log(self, message: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _poll_events(self) -> None:
        self._drain_ui_events()
        self.root.after(100, self._poll_events)

    def _drain_ui_events(self) -> None:
        while True:
            try:
                event = self._ui_events.get_nowait()
            except queue.Empty:
                return
            self._handle_event(event)

    def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type", ""))
        if event_type == "search_done":
            self.search_btn.configure(state=tk.NORMAL)
            self.search_status_var.set("Search complete")
            bundle = event["bundle"]
            self._populate_annas_results(bundle.annas_results)
            self._populate_scopus_results(bundle.scopus_results)
            for key, value in bundle.errors.items():
                self._log(f"{key}: {value}")
            return

        if event_type == "search_error":
            self.search_btn.configure(state=tk.NORMAL)
            self.search_status_var.set("Search failed")
            self._log(f"Search failed: {event.get('error')}")
            return

        if event_type == "scopus_candidates":
            self._mapping_in_progress = False
            scopus_row = event.get("scopus_row", {})
            error = event.get("error")
            if error:
                self._log(f"Scopus mapping failed: {error}")
                self._resolve_next_scopus_row()
                return

            candidates = event.get("candidates", [])
            selected = self._open_mapping_modal(scopus_row=scopus_row, candidates=candidates)
            if selected is not None:
                item, added = self.controller.enqueue_annas_candidate(
                    selected,
                    origin_meta={
                        "origin_provider": "scopus",
                        "origin_title": str(scopus_row.get("title") or selected.annas_title),
                        "query_context": selected.query_context,
                    },
                )
                if added:
                    self._insert_queue_row(item)
                    self._log(f"Mapped and queued: {selected.annas_title}")
            self._resolve_next_scopus_row()
            return

        if event_type == "batch_progress":
            self._handle_batch_progress(event.get("event", {}))
            return

        if event_type == "batch_complete":
            summary = event.get("summary", {})
            self._set_busy_state(False)
            self.progress_var.set(
                f"Batch complete: succeeded={summary.get('succeeded', 0)} failed={summary.get('failed', 0)}"
            )
            self._log(f"Batch summary: {summary.get('summary_path')}")
            return

    def _populate_annas_results(self, rows: list[AnnasCandidate]) -> None:
        for idx, row in enumerate(rows, start=1):
            item_id = str(idx)
            self._annas_rows[item_id] = row
            self.annas_tree.insert(
                "",
                tk.END,
                iid=item_id,
                values=(row.annas_kind, row.annas_title, row.annas_size or "", row.annas_hash, row.annas_url or ""),
            )

    def _populate_scopus_results(self, rows: list[dict[str, Any]]) -> None:
        for idx, row in enumerate(rows, start=1):
            item_id = str(idx)
            safe = dict(row)
            self._scopus_rows[item_id] = safe
            self.scopus_tree.insert(
                "",
                tk.END,
                iid=item_id,
                values=(
                    safe.get("title") or "",
                    safe.get("creator") or "",
                    safe.get("doi") or "",
                    safe.get("scopus_id") or "",
                    safe.get("cited_by_count") or "",
                ),
            )

    def _open_mapping_modal(
        self,
        *,
        scopus_row: dict[str, Any],
        candidates: list[AnnasCandidate],
    ) -> AnnasCandidate | None:
        if not candidates:
            messagebox.showinfo(
                "No Download Candidates",
                f"No Anna's candidates found for Scopus result:\n{scopus_row.get('title', '')}",
            )
            return None

        top = tk.Toplevel(self.root)
        top.title("Map Scopus Result to Anna's Candidate")
        top.geometry("1100x400")
        top.transient(self.root)
        top.grab_set()

        ttk.Label(top, text=f"Scopus title: {scopus_row.get('title', '')}").pack(fill=tk.X, padx=8, pady=(8, 4))

        table = ttk.Treeview(
            top,
            columns=("kind", "title", "size", "hash", "url"),
            show="headings",
            selectmode="browse",
            height=12,
        )
        for col, width in (("kind", 80), ("title", 420), ("size", 90), ("hash", 180), ("url", 260)):
            table.heading(col, text=col)
            table.column(col, width=width, anchor=tk.W)
        table.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        for idx, candidate in enumerate(candidates, start=1):
            table.insert(
                "",
                tk.END,
                iid=str(idx),
                values=(
                    candidate.annas_kind,
                    candidate.annas_title,
                    candidate.annas_size or "",
                    candidate.annas_hash,
                    candidate.annas_url or "",
                ),
            )
        if table.get_children():
            table.selection_set(table.get_children()[0])

        selected: dict[str, AnnasCandidate | None] = {"value": None}

        def on_add() -> None:
            pick = table.selection()
            if not pick:
                return
            idx = int(pick[0]) - 1
            if 0 <= idx < len(candidates):
                selected["value"] = candidates[idx]
            top.destroy()

        def on_skip() -> None:
            top.destroy()

        buttons = ttk.Frame(top)
        buttons.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Button(buttons, text="Add Selected", command=on_add).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(buttons, text="Skip", command=on_skip).pack(side=tk.RIGHT)

        top.wait_window()
        return selected["value"]

    def _handle_batch_progress(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        if event_type == "batch_started":
            self.progress_var.set(f"Batch started: total={event.get('total', 0)}")
            self._log(self.progress_var.get())
            return

        if event_type == "item_started":
            item_id = int(event.get("item_id", 0))
            self._update_queue_status(item_id, "running")
            self.progress_var.set(
                f"Running item {event.get('index', 0)}/{event.get('total', 0)} (queue id={item_id})"
            )
            return

        if event_type == "item_finished":
            item_id = int(event.get("item_id", 0))
            ok = bool(event.get("ok"))
            status = "succeeded" if ok else "failed"
            self._update_queue_status(item_id, status)
            err = event.get("error")
            if err:
                self._log(f"Item {item_id} failed: {err}")
            else:
                self._log(f"Item {item_id} succeeded")
            return

        if event_type == "batch_finished":
            self._log(f"Batch finished. summary={event.get('summary_path')}")

    def _update_queue_status(self, item_id: int, status: str) -> None:
        item = self._queue_rows.get(item_id)
        if item is not None:
            item.status = status

        tree_id = str(item_id)
        if not self.queue_tree.exists(tree_id):
            return
        current = list(self.queue_tree.item(tree_id, "values"))
        if not current:
            return
        current[1] = status
        self.queue_tree.item(tree_id, values=tuple(current))


def run_gui_app(
    *,
    cfg: AudiobookConfig,
    enhance: bool = True,
    annas_min_download_size_mb: float | None = None,
    marker_extra_args: list[str] | None = None,
) -> int:
    root = tk.Tk()
    app = LarrakGuiApp(
        root,
        cfg=cfg,
        enhance=enhance,
        annas_min_download_size_mb=annas_min_download_size_mb,
        marker_extra_args=marker_extra_args,
    )
    _ = app
    root.mainloop()
    return 0

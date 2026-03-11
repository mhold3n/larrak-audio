from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import AudiobookConfig
from .pipeline import source_paths
from .preflight import ensure_marker_ready
from .research_annas import MB_BYTES, run_annas_search, run_research_annas
from .research_scopus import run_scopus_search
from .utils import utc_now_iso, write_json

ProgressCallback = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class GuiSettings:
    enhance: bool = True
    annas_min_download_size_mb: float | None = None
    marker_extra_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnnasCandidate:
    annas_kind: str
    annas_hash: str
    annas_title: str
    annas_size: str | None
    annas_url: str | None
    query_context: str = ""


@dataclass
class QueueItem:
    item_id: int
    origin_provider: str
    origin_title: str
    annas_kind: str
    annas_hash: str
    annas_title: str
    annas_size: str | None
    query_context: str
    status: str = "pending"


@dataclass(frozen=True)
class SearchBundle:
    annas_results: list[AnnasCandidate]
    scopus_results: list[dict[str, Any]]
    errors: dict[str, str]


@dataclass(frozen=True)
class SearchClause:
    field: str
    op: str
    value: str


@dataclass(frozen=True)
class PreparedSearchQueries:
    mode: str
    raw_query: str
    annas_query: str
    scopus_query: str
    clauses: tuple[SearchClause, ...] = ()


_ADVANCED_CLAUSE_RE = re.compile(
    r"""
    (?P<field>[A-Za-z_][A-Za-z0-9_]*)
    \s*
    (?P<op>>=|<=|=)
    \s*
    (?:
        "(?P<dq>(?:\\.|[^"\\])*)"
        |
        '(?P<sq>(?:\\.|[^'\\])*)'
        |
        (?P<bare>[^,\s]+)
    )
    """,
    re.VERBOSE,
)

_ADVANCED_FIELD_ALIASES = {
    "author": "author",
    "authors": "author",
    "creator": "author",
    "title": "title",
    "doi": "doi",
    "metadata": "metadata",
    "meta": "metadata",
    "abstract": "abstract",
    "keyword": "keyword",
    "keywords": "keyword",
    "journal": "journal",
    "publication": "journal",
    "source": "journal",
}

_SCOPUS_FIELD_TAGS = {
    "author": "AUTH",
    "title": "TITLE",
    "doi": "DOI",
    "metadata": "ALL",
    "abstract": "ABS",
    "keyword": "KEY",
    "journal": "SRCTITLE",
}


class GuiController:
    def __init__(self, cfg: AudiobookConfig) -> None:
        self.cfg = cfg
        self._queue_items: list[QueueItem] = []
        self._next_item_id = 1

    def queue_items(self) -> list[QueueItem]:
        return [
            QueueItem(
                item_id=item.item_id,
                origin_provider=item.origin_provider,
                origin_title=item.origin_title,
                annas_kind=item.annas_kind,
                annas_hash=item.annas_hash,
                annas_title=item.annas_title,
                annas_size=item.annas_size,
                query_context=item.query_context,
                status=item.status,
            )
            for item in self._queue_items
        ]

    def search_all(self, query: str, settings: GuiSettings, *, search_mode: str = "basic") -> SearchBundle:
        text = query.strip()
        if not text:
            raise ValueError("query is required")
        prepared = _prepare_search_queries(query=text, search_mode=search_mode)

        annas_results: list[AnnasCandidate] = []
        scopus_results: list[dict[str, Any]] = []
        errors: dict[str, str] = {}

        if str(self.cfg.annas_secret_key or "").strip():
            seen_keys: set[tuple[str, str]] = set()
            min_download_size_bytes = _resolve_min_download_size_bytes(settings.annas_min_download_size_mb)
            for kind in ("book", "article"):
                try:
                    payload = run_annas_search(
                        cfg=self.cfg,
                        kind=kind,
                        query=prepared.annas_query,
                        min_download_size_bytes=min_download_size_bytes,
                    )
                except Exception as exc:
                    errors[f"annas_{kind}"] = str(exc)
                    continue

                for row in payload.get("candidates", []):
                    candidate = _candidate_from_annas_row(
                        row=row,
                        fallback_kind=kind,
                        query_context=prepared.raw_query,
                    )
                    dedupe_key = (candidate.annas_kind, candidate.annas_hash)
                    if dedupe_key in seen_keys:
                        continue
                    seen_keys.add(dedupe_key)
                    annas_results.append(candidate)
        else:
            errors["annas"] = "ANNAS_SECRET_KEY is not configured."

        if str(self.cfg.scopus_api_key or "").strip():
            try:
                scopus_payload = run_scopus_search(
                    cfg=self.cfg,
                    query=prepared.scopus_query,
                    count=25,
                    sort="relevancy",
                )
                rows = scopus_payload.get("results", [])
                scopus_results = [dict(row) for row in rows if isinstance(row, dict)]
            except Exception as exc:
                errors["scopus"] = str(exc)
        else:
            errors["scopus"] = "SCOPUS_API_KEY is not configured."

        return SearchBundle(
            annas_results=annas_results,
            scopus_results=scopus_results,
            errors=errors,
        )

    def resolve_scopus_to_annas(self, scopus_row: dict[str, Any], settings: GuiSettings) -> list[AnnasCandidate]:
        if not str(self.cfg.annas_secret_key or "").strip():
            raise RuntimeError("ANNAS_SECRET_KEY is required to resolve Scopus results to downloadable candidates.")

        bridge_query = _build_scopus_bridge_query(scopus_row)
        min_download_size_bytes = _resolve_min_download_size_bytes(settings.annas_min_download_size_mb)
        candidates: list[AnnasCandidate] = []
        seen_keys: set[tuple[str, str]] = set()

        for kind in ("book", "article"):
            payload = run_annas_search(
                cfg=self.cfg,
                kind=kind,
                query=bridge_query,
                min_download_size_bytes=min_download_size_bytes,
            )
            for row in payload.get("candidates", []):
                candidate = _candidate_from_annas_row(row=row, fallback_kind=kind, query_context=bridge_query)
                dedupe_key = (candidate.annas_kind, candidate.annas_hash)
                if dedupe_key in seen_keys:
                    continue
                seen_keys.add(dedupe_key)
                candidates.append(candidate)

        return candidates

    def enqueue_annas_candidate(
        self,
        candidate: AnnasCandidate,
        origin_meta: dict[str, Any],
    ) -> tuple[QueueItem, bool]:
        dedupe_key = (candidate.annas_kind, candidate.annas_hash)
        for existing in self._queue_items:
            if (existing.annas_kind, existing.annas_hash) == dedupe_key:
                return existing, False

        item = QueueItem(
            item_id=self._next_item_id,
            origin_provider=str(origin_meta.get("origin_provider") or "annas"),
            origin_title=str(origin_meta.get("origin_title") or candidate.annas_title),
            annas_kind=candidate.annas_kind,
            annas_hash=candidate.annas_hash,
            annas_title=candidate.annas_title,
            annas_size=candidate.annas_size,
            query_context=str(origin_meta.get("query_context") or candidate.query_context),
            status="pending",
        )
        self._next_item_id += 1
        self._queue_items.append(item)
        return item, True

    def remove_queue_items(self, item_ids: set[int]) -> None:
        self._queue_items = [item for item in self._queue_items if item.item_id not in item_ids]

    def clear_queue(self) -> None:
        self._queue_items = []

    def run_batch(
        self,
        queue_items: list[QueueItem],
        settings: GuiSettings,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        cb = progress_callback or (lambda _event: None)
        started_at = utc_now_iso()
        cb({"type": "batch_started", "started_at": started_at, "total": len(queue_items)})

        rows: list[dict[str, Any]] = []
        preflight_error: str | None = None
        try:
            ensure_marker_ready(self.cfg)
        except Exception as exc:
            preflight_error = f"marker preflight failed: {exc}"

        for index, item in enumerate(queue_items, start=1):
            cb(
                {
                    "type": "item_started",
                    "item_id": item.item_id,
                    "index": index,
                    "total": len(queue_items),
                }
            )

            row = {
                "item_id": item.item_id,
                "origin_provider": item.origin_provider,
                "origin_title": item.origin_title,
                "annas_kind": item.annas_kind,
                "annas_hash": item.annas_hash,
                "annas_title": item.annas_title,
                "annas_size": item.annas_size,
                "query_context": item.query_context,
                "status": "failed",
                "error": None,
                "source_ids": [],
                "artifacts": {},
            }

            if preflight_error is not None:
                row["error"] = preflight_error
                rows.append(row)
                item.status = "failed"
                cb(
                    {
                        "type": "item_finished",
                        "item_id": item.item_id,
                        "ok": False,
                        "error": preflight_error,
                    }
                )
                continue

            try:
                payload = run_research_annas(
                    cfg=self.cfg,
                    action="download",
                    kind=item.annas_kind,
                    identifier=item.annas_hash,
                    ingest=True,
                    build=True,
                    enhance=bool(settings.enhance),
                    marker_extra_args=list(settings.marker_extra_args),
                    min_download_size_mb=settings.annas_min_download_size_mb,
                )
                row["run_summary_path"] = payload.get("summary_path")
                row["downloaded_files"] = list(payload.get("downloaded_files", []))
                row["downloaded_files_all"] = list(payload.get("downloaded_files_all", []))
                row["dropped_small_files"] = list(payload.get("dropped_small_files", []))

                source_ids = sorted(
                    {
                        str(result.get("source_id"))
                        for result in payload.get("results", [])
                        if isinstance(result, dict) and result.get("source_id")
                    }
                )
                row["source_ids"] = source_ids
                artifacts: dict[str, Any] = {}
                for source_id in source_ids:
                    try:
                        artifacts[source_id] = source_paths(source_id, self.cfg)
                    except Exception:
                        continue
                row["artifacts"] = artifacts

                ok = int(payload.get("exit_code", 1)) == 0
                row["status"] = "succeeded" if ok else "failed"
                if not ok:
                    row["error"] = payload.get("error") or _extract_first_result_error(payload.get("results", []))
            except Exception as exc:
                row["status"] = "failed"
                row["error"] = str(exc)

            rows.append(row)
            item.status = str(row["status"])
            cb(
                {
                    "type": "item_finished",
                    "item_id": item.item_id,
                    "ok": row["status"] == "succeeded",
                    "error": row["error"],
                }
            )

        succeeded = sum(1 for row in rows if row.get("status") == "succeeded")
        failed = sum(1 for row in rows if row.get("status") != "succeeded")
        finished_at = utc_now_iso()

        summary_path = _resolve_batch_summary_path(self.cfg)
        summary = {
            "started_at": started_at,
            "finished_at": finished_at,
            "total": len(rows),
            "succeeded": succeeded,
            "failed": failed,
            "settings": {
                "enhance": bool(settings.enhance),
                "annas_min_download_size_mb": settings.annas_min_download_size_mb,
                "marker_extra_args": list(settings.marker_extra_args),
            },
            "items": rows,
            "summary_path": str(summary_path),
            "ok": bool(rows and failed == 0),
            "exit_code": 0 if bool(rows and failed == 0) else 1,
        }
        write_json(summary_path, summary)
        cb({"type": "batch_finished", "summary_path": str(summary_path), "exit_code": summary["exit_code"]})
        return summary


def _prepare_search_queries(query: str, search_mode: str) -> PreparedSearchQueries:
    mode = str(search_mode or "basic").strip().lower()
    if mode in {"", "basic"}:
        return PreparedSearchQueries(
            mode="basic",
            raw_query=query,
            annas_query=query,
            scopus_query=query,
            clauses=(),
        )

    if mode != "advanced":
        raise ValueError(f"unsupported search mode: {search_mode}")

    clauses = _parse_advanced_search_clauses(query)
    annas_query = _build_annas_advanced_query(clauses)
    scopus_query = _build_scopus_advanced_query(clauses)
    return PreparedSearchQueries(
        mode="advanced",
        raw_query=query,
        annas_query=annas_query,
        scopus_query=scopus_query,
        clauses=tuple(clauses),
    )


def _parse_advanced_search_clauses(query: str) -> list[SearchClause]:
    matches = list(_ADVANCED_CLAUSE_RE.finditer(query))
    if not matches:
        raise ValueError(
            "advanced search requires clauses like field>=\"value\", field=\"value\", or field<=\"value\""
        )

    clauses: list[SearchClause] = []
    cursor = 0
    for match in matches:
        separator = query[cursor : match.start()]
        if not _is_clause_separator(separator):
            raise ValueError(
                "invalid advanced-search separator. Use commas and/or spaces between clauses."
            )

        field_raw = str(match.group("field") or "").strip().lower()
        field = _ADVANCED_FIELD_ALIASES.get(field_raw)
        if field is None:
            known = ", ".join(sorted(_ADVANCED_FIELD_ALIASES))
            raise ValueError(f"unsupported advanced-search field '{field_raw}'. Supported fields: {known}")

        value = _extract_clause_value(match)
        if not value:
            raise ValueError(f"advanced-search field '{field_raw}' has an empty value")

        clauses.append(SearchClause(field=field, op=str(match.group("op")), value=value))
        cursor = match.end()

    tail = query[cursor:]
    if not _is_clause_separator(tail):
        raise ValueError("invalid trailing text in advanced search query")
    return clauses


def _extract_clause_value(match: re.Match[str]) -> str:
    if match.group("dq") is not None:
        raw = str(match.group("dq"))
    elif match.group("sq") is not None:
        raw = str(match.group("sq"))
    else:
        raw = str(match.group("bare") or "")
    text = re.sub(r"\\(.)", r"\1", raw).strip()
    return text


def _is_clause_separator(text: str) -> bool:
    return all(ch in {" ", "\t", "\r", "\n", ","} for ch in text)


def _build_annas_advanced_query(clauses: list[SearchClause]) -> str:
    tokens: list[str] = []
    for clause in clauses:
        term = _annas_term_for_clause(clause)
        if clause.op == "<=":
            tokens.append(f"-{term}")
            continue
        tokens.append(term)

    query = " ".join(tokens).strip()
    if not query:
        raise ValueError("advanced search query did not produce any Anna's query terms")
    return query


def _annas_term_for_clause(clause: SearchClause) -> str:
    if clause.field == "metadata":
        return _quote(clause.value)
    if clause.field == "doi":
        return f"doi:{_quote(clause.value)}"
    return f"{clause.field}:{_quote(clause.value)}"


def _build_scopus_advanced_query(clauses: list[SearchClause]) -> str:
    positive: list[str] = []
    negative: list[str] = []
    for clause in clauses:
        expression = _scopus_expression_for_clause(clause)
        if clause.op == "<=":
            negative.append(expression)
            continue
        positive.append(expression)

    if not positive:
        raise ValueError("advanced search requires at least one include/exact clause (>= or =)")

    query = " AND ".join(positive)
    if negative:
        query = f"{query} AND NOT ({' OR '.join(negative)})"
    return query


def _scopus_expression_for_clause(clause: SearchClause) -> str:
    tag = _SCOPUS_FIELD_TAGS.get(clause.field, "ALL")
    return f'{tag}("{_escape_scopus(clause.value)}")'


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _escape_scopus(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _candidate_from_annas_row(row: dict[str, Any], fallback_kind: str, query_context: str) -> AnnasCandidate:
    kind_raw = str(row.get("kind") or fallback_kind).strip().lower()
    kind = kind_raw if kind_raw in {"book", "article"} else fallback_kind
    return AnnasCandidate(
        annas_kind=kind,
        annas_hash=str(row.get("hash") or "").strip(),
        annas_title=str(row.get("title") or "").strip(),
        annas_size=_coerce_str_or_none(row.get("size")),
        annas_url=_coerce_str_or_none(row.get("url")),
        query_context=query_context,
    )


def _build_scopus_bridge_query(scopus_row: dict[str, Any]) -> str:
    doi = str(scopus_row.get("doi") or "").strip()
    title = str(scopus_row.get("title") or "").strip()
    scopus_id = str(scopus_row.get("scopus_id") or "").strip()

    if doi and title:
        return f'{doi} "{title}"'
    if doi:
        return doi
    if title:
        return title
    if scopus_id:
        return scopus_id
    raise ValueError("Scopus row is missing DOI, title, and scopus_id")


def _resolve_min_download_size_bytes(value_mb: float | None) -> int:
    if value_mb is None:
        return MB_BYTES
    if value_mb < 0:
        raise ValueError("annas minimum download size must be >= 0")
    return int(value_mb * MB_BYTES)


def _coerce_str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_first_result_error(results: list[dict[str, Any]]) -> str | None:
    for result in results:
        if not isinstance(result, dict):
            continue
        message = _coerce_str_or_none(result.get("error"))
        if message:
            return message
    return None


def _resolve_batch_summary_path(cfg: AudiobookConfig) -> Path:
    stamp = dt.datetime.now(tz=dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path(cfg.output_root).resolve() / "batch_runs" / f"gui_batch_{stamp}.json"

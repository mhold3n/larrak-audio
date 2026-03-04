from __future__ import annotations

import io
import time
from pathlib import Path
from urllib import error

import larrak_audio.research_scopus as research
from larrak_audio.config import AudiobookConfig
from larrak_audio.utils import read_json


def test_run_scopus_search_builds_expected_query_and_cleans_results(monkeypatch) -> None:
    cfg = AudiobookConfig(scopus_api_key="secret")
    captured: dict[str, object] = {}

    def fake_request(*, cfg, endpoint, params):
        _ = cfg
        captured["endpoint"] = endpoint
        captured["params"] = params
        return {
            "request_url": "https://api.elsevier.com/content/search/scopus?query=TITLE(test)",
            "status_code": 200,
            "quota": {"limit": "100", "remaining": "97", "reset": "0"},
            "data": {
                "search-results": {
                    "opensearch:totalResults": "1",
                    "entry": [
                        {
                            "dc:identifier": "SCOPUS_ID:123456",
                            "dc:title": "Ring-pack modeling",
                            "dc:creator": "Tian",
                            "prism:publicationName": "SAE Paper",
                            "prism:coverDate": "2012-01-01",
                            "prism:doi": "10.4271/2012-01-1234",
                            "citedby-count": "42",
                            "prism:aggregationType": "Conference Proceeding",
                            "link": [
                                {
                                    "@ref": "scopus",
                                    "@href": "https://www.scopus.com/record/123456",
                                }
                            ],
                        }
                    ],
                }
            },
        }

    monkeypatch.setattr(research, "_run_scopus_request", fake_request)

    payload = research.run_scopus_search(cfg=cfg, query="TITLE(ring)", count=7, start=3, sort="relevancy")

    assert captured["endpoint"] == "content/search/scopus"
    assert captured["params"] == {
        "query": "TITLE(ring)",
        "count": 7,
        "start": 3,
        "sort": "relevancy",
        "view": "STANDARD",
    }
    assert payload["status_code"] == 200
    assert payload["api_total_results"] == 1
    assert payload["total_results"] == 1
    assert payload["results"][0]["scopus_id"] == "123456"
    assert payload["results"][0]["url"] == "https://www.scopus.com/record/123456"


def test_run_scopus_citing_uses_refeid_query(monkeypatch) -> None:
    cfg = AudiobookConfig(scopus_api_key="secret")
    captured: dict[str, object] = {}

    def fake_search(*, cfg, query, count, start, sort):
        _ = cfg, count, start, sort
        captured["query"] = query
        return {
            "query": query,
            "total_results": 0,
            "results": [],
        }

    monkeypatch.setattr(research, "run_scopus_search", fake_search)

    payload = research.run_scopus_citing(cfg=cfg, scopus_id="SCOPUS_ID:85012345678")

    assert captured["query"] == "REFEID(85012345678)"
    assert payload["citing_for_scopus_id"] == "85012345678"


def test_run_research_scopus_writes_summary_and_exit_zero(tmp_path: Path, monkeypatch) -> None:
    cfg = AudiobookConfig(
        output_root=str(tmp_path / "outputs"),
        queue_db_path=str(tmp_path / "outputs" / "jobs.sqlite3"),
        scopus_api_key="secret",
    )

    def fake_author(*, cfg, author_id):
        _ = cfg, author_id
        return {"author_id": "123", "profile": {"name": {"surname": "Heywood"}}}

    monkeypatch.setattr(research, "run_scopus_author", fake_author)

    summary = research.run_research_scopus(cfg=cfg, action="author", author_id="123")

    assert summary["exit_code"] == 0
    assert summary["ok"] is True
    assert summary["error"] is None
    summary_path = Path(summary["summary_path"])
    assert summary_path.exists()
    persisted = read_json(summary_path)
    assert persisted["action"] == "author"


def test_run_research_scopus_failure_sets_exit_one(tmp_path: Path, monkeypatch) -> None:
    cfg = AudiobookConfig(
        output_root=str(tmp_path / "outputs"),
        queue_db_path=str(tmp_path / "outputs" / "jobs.sqlite3"),
        scopus_api_key="secret",
    )

    def fake_abstract(*, cfg, scopus_id):
        _ = cfg, scopus_id
        raise RuntimeError("scopus boom")

    monkeypatch.setattr(research, "run_scopus_abstract", fake_abstract)

    summary = research.run_research_scopus(cfg=cfg, action="abstract", scopus_id="123")

    assert summary["exit_code"] == 1
    assert summary["ok"] is False
    assert "scopus boom" in str(summary["error"])


def test_run_scopus_request_requires_api_key() -> None:
    cfg = AudiobookConfig(scopus_api_key="")

    try:
        research._run_scopus_request(cfg=cfg, endpoint="content/search/scopus", params={"query": "x"})
    except RuntimeError as exc:
        assert "SCOPUS_API_KEY is required" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_run_scopus_request_retries_after_http_429(tmp_path: Path, monkeypatch) -> None:
    cfg = AudiobookConfig(
        output_root=str(tmp_path / "outputs"),
        queue_db_path=str(tmp_path / "outputs" / "jobs.sqlite3"),
        scopus_api_key="secret",
        scopus_min_interval_s="0",
        scopus_max_retries="2",
        scopus_retry_backoff_s="0.1",
        scopus_min_remaining_quota="0",
    )
    calls = 0
    sleeps: list[float] = []

    class FakeResponse:
        def __init__(self, *, body: str, headers: dict[str, str], status: int = 200) -> None:
            self._body = body.encode("utf-8")
            self.headers = headers
            self.status = status

        def read(self) -> bytes:
            return self._body

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            _ = exc_type, exc, tb
            return None

    def fake_urlopen(req, timeout):
        nonlocal calls
        _ = timeout
        calls += 1
        if calls == 1:
            raise error.HTTPError(
                req.full_url,
                429,
                "Too Many Requests",
                {"Retry-After": "0"},
                io.BytesIO(b'{"error":"rate_limited"}'),
            )
        return FakeResponse(
            body='{"search-results":{"entry":[]}}',
            headers={
                "X-RateLimit-Limit": "20000",
                "X-RateLimit-Remaining": "19999",
                "X-RateLimit-Reset": str(int(time.time()) + 3600),
            },
        )

    monkeypatch.setattr(research.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(research.time, "sleep", lambda seconds: sleeps.append(seconds))

    payload = research._run_scopus_request(
        cfg=cfg,
        endpoint="content/search/scopus",
        params={"query": "TITLE(test)"},
    )

    assert calls == 2
    assert payload["status_code"] == 200
    assert len(sleeps) == 1


def test_scopus_quota_guard_blocks_low_remaining_quota(tmp_path: Path, monkeypatch) -> None:
    cfg = AudiobookConfig(
        output_root=str(tmp_path / "outputs"),
        queue_db_path=str(tmp_path / "outputs" / "jobs.sqlite3"),
        scopus_api_key="secret",
        scopus_min_interval_s="0",
        scopus_min_remaining_quota="10",
    )
    research.record_provider_event(
        cfg,
        provider="scopus",
        quota_remaining=5,
        quota_reset=int(time.time()) + 3600,
    )

    def fake_urlopen(_req, timeout):
        _ = timeout
        raise AssertionError("urlopen should not be called while quota guard is active")

    monkeypatch.setattr(research.request, "urlopen", fake_urlopen)

    try:
        research._run_scopus_request(
            cfg=cfg,
            endpoint="content/search/scopus",
            params={"query": "TITLE(test)"},
        )
    except RuntimeError as exc:
        assert "quota guard is active" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")

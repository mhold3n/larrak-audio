from __future__ import annotations

import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from .config import AudiobookConfig
from .safeguards import (
    enforce_min_interval,
    get_provider_state,
    parse_float_setting,
    parse_int_setting,
    record_provider_event,
)
from .utils import utc_now_iso, write_json

DEFAULT_SCOPUS_BASE_URL = "https://api.elsevier.com/"


def run_research_scopus(
    *,
    cfg: AudiobookConfig,
    action: str,
    query: str | None = None,
    scopus_id: str | None = None,
    author_id: str | None = None,
    count: int = 5,
    start: int = 0,
    sort: str = "coverDate",
    summary_path: str | Path | None = None,
) -> dict[str, Any]:
    action = action.strip().lower()
    if action not in {"search", "abstract", "author", "citing"}:
        raise ValueError(f"unsupported action: {action}")
    if count < 1 or count > 25:
        raise ValueError("--count must be between 1 and 25")
    if start < 0:
        raise ValueError("--start must be >= 0")

    if action == "search" and not (query or "").strip():
        raise ValueError("--query is required for action=search")
    if action in {"abstract", "citing"} and not (scopus_id or "").strip():
        raise ValueError("--scopus-id is required for action=abstract|citing")
    if action == "author" and not (author_id or "").strip():
        raise ValueError("--author-id is required for action=author")

    started_at = utc_now_iso()
    summary_target = _resolve_summary_path(cfg=cfg, action=action, summary_path=summary_path)
    payload: dict[str, Any] = {
        "started_at": started_at,
        "finished_at": started_at,
        "action": action,
        "query": query,
        "scopus_id": scopus_id,
        "author_id": author_id,
        "count": int(count),
        "start": int(start),
        "sort": sort,
        "error": None,
        "ok": False,
        "exit_code": 1,
        "summary_path": str(summary_target),
    }

    try:
        if action == "search":
            assert query is not None
            payload["result"] = run_scopus_search(
                cfg=cfg,
                query=query,
                count=count,
                start=start,
                sort=sort,
            )
        elif action == "abstract":
            assert scopus_id is not None
            payload["result"] = run_scopus_abstract(cfg=cfg, scopus_id=scopus_id)
        elif action == "author":
            assert author_id is not None
            payload["result"] = run_scopus_author(cfg=cfg, author_id=author_id)
        else:
            assert scopus_id is not None
            payload["result"] = run_scopus_citing(
                cfg=cfg,
                scopus_id=scopus_id,
                count=count,
                start=start,
                sort=sort,
            )

        payload["ok"] = True
        payload["exit_code"] = 0
    except Exception as exc:
        payload["error"] = str(exc)

    return _finalize_summary(payload, summary_target)


def run_scopus_search(
    *,
    cfg: AudiobookConfig,
    query: str,
    count: int = 5,
    start: int = 0,
    sort: str = "coverDate",
) -> dict[str, Any]:
    response = _run_scopus_request(
        cfg=cfg,
        endpoint="content/search/scopus",
        params={
            "query": query,
            "count": int(count),
            "start": int(start),
            "sort": sort,
            "view": "STANDARD",
        },
    )
    results = _clean_search_results(response["data"])
    return {
        "query": query,
        "count": int(count),
        "start": int(start),
        "sort": sort,
        "endpoint": "content/search/scopus",
        "request_url": response["request_url"],
        "status_code": response["status_code"],
        "quota": response["quota"],
        "total_results": len(results),
        "api_total_results": _extract_api_total_results(response["data"]),
        "results": results,
    }


def run_scopus_abstract(*, cfg: AudiobookConfig, scopus_id: str) -> dict[str, Any]:
    clean_id = _clean_scopus_id(scopus_id)
    endpoint = f"content/abstract/scopus_id/{parse.quote(clean_id, safe='')}"
    response = _run_scopus_request(cfg=cfg, endpoint=endpoint, params=None)
    details = _clean_abstract_details(response["data"])
    return {
        "scopus_id": clean_id,
        "endpoint": endpoint,
        "request_url": response["request_url"],
        "status_code": response["status_code"],
        "quota": response["quota"],
        "details": details,
    }


def run_scopus_author(*, cfg: AudiobookConfig, author_id: str) -> dict[str, Any]:
    clean_id = _clean_author_id(author_id)
    endpoint = f"content/author/author_id/{parse.quote(clean_id, safe='')}"
    response = _run_scopus_request(cfg=cfg, endpoint=endpoint, params=None)
    profile = _clean_author_profile(response["data"])
    return {
        "author_id": clean_id,
        "endpoint": endpoint,
        "request_url": response["request_url"],
        "status_code": response["status_code"],
        "quota": response["quota"],
        "profile": profile,
    }


def run_scopus_citing(
    *,
    cfg: AudiobookConfig,
    scopus_id: str,
    count: int = 5,
    start: int = 0,
    sort: str = "coverDate",
) -> dict[str, Any]:
    clean_id = _clean_scopus_id(scopus_id)
    query = f"REFEID({clean_id})"
    payload = run_scopus_search(cfg=cfg, query=query, count=count, start=start, sort=sort)
    payload["citing_for_scopus_id"] = clean_id
    return payload


def _run_scopus_request(
    *,
    cfg: AudiobookConfig,
    endpoint: str,
    params: dict[str, str | int] | None,
) -> dict[str, Any]:
    api_key = str(cfg.scopus_api_key or "").strip()
    if not api_key:
        raise RuntimeError(
            "SCOPUS_API_KEY is required for Scopus operations. "
            "Set SCOPUS_API_KEY or AudiobookConfig.scopus_api_key."
        )

    base_url = _normalize_scopus_base_url(str(cfg.scopus_base_url or DEFAULT_SCOPUS_BASE_URL))
    url = parse.urljoin(base_url, endpoint.lstrip("/"))
    if params:
        encoded = parse.urlencode({key: str(value) for key, value in params.items() if value is not None})
        if encoded:
            url = f"{url}?{encoded}"

    timeout_s = parse_float_setting(cfg.scopus_request_timeout_s, default=30.0, minimum=1.0)
    min_interval_s = parse_float_setting(cfg.scopus_min_interval_s, default=1.0, minimum=0.0)
    max_retries = parse_int_setting(cfg.scopus_max_retries, default=3, minimum=0)
    retry_backoff_s = parse_float_setting(cfg.scopus_retry_backoff_s, default=2.0, minimum=0.1)
    min_remaining_quota = parse_int_setting(cfg.scopus_min_remaining_quota, default=25, minimum=0)

    _enforce_scopus_quota_guard(cfg=cfg, min_remaining_quota=min_remaining_quota)

    attempt = 0
    while True:
        waited_s = enforce_min_interval(cfg, provider="scopus", min_interval_s=min_interval_s)
        req = request.Request(
            url=url,
            headers={
                "X-ELS-APIKey": api_key,
                "Accept": "application/json",
                "User-Agent": "larrak-audio/0.1",
            },
            method="GET",
        )
        try:
            with request.urlopen(req, timeout=timeout_s) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                data = _try_parse_json(body)
                if data is None:
                    raise RuntimeError(f"Scopus API returned non-JSON response for {endpoint}")
                quota = _extract_quota(resp.headers)
                record_provider_event(
                    cfg,
                    provider="scopus",
                    last_status_code=int(resp.status),
                    request_url=url,
                    last_waited_seconds=round(waited_s, 3),
                    quota_limit=_coerce_int(quota.get("limit")),
                    quota_remaining=_coerce_int(quota.get("remaining")),
                    quota_reset=_coerce_int(quota.get("reset")),
                )
                return {
                    "request_url": url,
                    "status_code": int(resp.status),
                    "quota": quota,
                    "data": data,
                }
        except error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            detail = (body or str(exc.reason) or f"status={exc.code}").strip()
            quota = _extract_quota(exc.headers)
            record_provider_event(
                cfg,
                provider="scopus",
                last_status_code=int(exc.code),
                request_url=url,
                last_waited_seconds=round(waited_s, 3),
                quota_limit=_coerce_int(quota.get("limit")),
                quota_remaining=_coerce_int(quota.get("remaining")),
                quota_reset=_coerce_int(quota.get("reset")),
            )
            retry_delay_s = _retry_delay_for_scopus_http(
                status_code=int(exc.code),
                headers=exc.headers,
                attempt=attempt,
                backoff_s=retry_backoff_s,
            )
            if retry_delay_s is not None and attempt < max_retries:
                time.sleep(retry_delay_s)
                attempt += 1
                continue

            if _looks_like_cloudflare_block(detail):
                raise RuntimeError(
                    "Scopus API request was blocked by Cloudflare/WAF. "
                    "Try again from a non-blocked network path (for example, disable VPN)."
                ) from exc

            raise RuntimeError(
                f"Scopus API request failed ({exc.code}) for {endpoint}: {_clip_text(detail, 2000)}"
            ) from exc
        except error.URLError as exc:
            retry_delay_s = retry_backoff_s * (2**attempt)
            if attempt < max_retries:
                time.sleep(retry_delay_s)
                attempt += 1
                continue
            raise RuntimeError(f"Scopus API request failed for {endpoint}: {exc.reason}") from exc
        except TimeoutError as exc:
            retry_delay_s = retry_backoff_s * (2**attempt)
            if attempt < max_retries:
                time.sleep(retry_delay_s)
                attempt += 1
                continue
            raise RuntimeError(f"Scopus API request timed out for {endpoint}: {exc}") from exc


def _clean_search_results(data: dict[str, Any]) -> list[dict[str, Any]]:
    search_results = data.get("search-results")
    if not isinstance(search_results, dict):
        return []
    entries = search_results.get("entry")
    if not isinstance(entries, list):
        return []

    out: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        out.append(
            {
                "scopus_id": _clean_scopus_id(str(entry.get("dc:identifier", ""))),
                "title": entry.get("dc:title"),
                "creator": entry.get("dc:creator"),
                "publication_name": entry.get("prism:publicationName"),
                "cover_date": entry.get("prism:coverDate"),
                "doi": entry.get("prism:doi"),
                "cited_by_count": entry.get("citedby-count"),
                "aggregation_type": entry.get("prism:aggregationType"),
                "url": _extract_link_href(entry.get("link"), ref_name="scopus"),
            }
        )
    return out


def _clean_abstract_details(data: dict[str, Any]) -> dict[str, Any]:
    root = data.get("abstracts-retrieval-response") or data.get("abstract-retrieval-response")
    if not isinstance(root, dict):
        return {}

    coredata = root.get("coredata")
    core = coredata if isinstance(coredata, dict) else {}

    authors_section = root.get("authors")
    authors_data: Any = {}
    if isinstance(authors_section, dict):
        authors_data = authors_section.get("author", [])
    if isinstance(authors_data, dict):
        author_rows = [authors_data]
    elif isinstance(authors_data, list):
        author_rows = [row for row in authors_data if isinstance(row, dict)]
    else:
        author_rows = []

    authors: list[dict[str, Any]] = []
    for author in author_rows:
        authors.append(
            {
                "auth_id": author.get("@auid"),
                "name": author.get("ce:indexed-name"),
                "surname": author.get("ce:surname"),
                "initials": author.get("ce:initials"),
            }
        )

    return {
        "scopus_id": _clean_scopus_id(str(core.get("dc:identifier", ""))),
        "doi": core.get("prism:doi"),
        "title": core.get("dc:title"),
        "description": core.get("dc:description"),
        "publication_name": core.get("prism:publicationName"),
        "cover_date": core.get("prism:coverDate"),
        "cited_by_count": core.get("citedby-count"),
        "authors": authors,
        "url": _extract_link_href(core.get("link"), ref_name="scopus"),
    }


def _clean_author_profile(data: dict[str, Any]) -> dict[str, Any]:
    root_any = data.get("author-retrieval-response")
    if isinstance(root_any, list):
        root = root_any[0] if root_any else {}
    elif isinstance(root_any, dict):
        root = root_any
    else:
        return {}

    core_any = root.get("coredata")
    core = core_any if isinstance(core_any, dict) else {}

    profile_any = root.get("author-profile")
    profile = profile_any if isinstance(profile_any, dict) else {}

    preferred_any = profile.get("preferred-name")
    preferred = preferred_any if isinstance(preferred_any, dict) else {}

    return {
        "author_id": _clean_author_id(str(core.get("dc:identifier", ""))),
        "orcid": core.get("orcid"),
        "document_count": core.get("document-count"),
        "cited_by_count": core.get("cited-by-count"),
        "citation_count": core.get("citation-count"),
        "name": {
            "surname": preferred.get("surname"),
            "given_name": preferred.get("given-name"),
            "initials": preferred.get("initials"),
        },
        "current_affiliation": _extract_affiliation_name(profile),
        "url": _extract_link_href(core.get("link"), ref_name="scopus-author"),
    }


def _extract_affiliation_name(profile: dict[str, Any]) -> str | None:
    current = profile.get("affiliation-current")
    if not isinstance(current, dict):
        return None
    affiliation = current.get("affiliation")
    if isinstance(affiliation, list):
        node = affiliation[0] if affiliation else {}
    elif isinstance(affiliation, dict):
        node = affiliation
    else:
        return None
    if not isinstance(node, dict):
        return None
    ip_doc = node.get("ip-doc")
    if not isinstance(ip_doc, dict):
        return None
    name = ip_doc.get("afdispname")
    return str(name) if name is not None else None


def _extract_link_href(links: Any, *, ref_name: str) -> str | None:
    if isinstance(links, dict):
        candidates = [links]
    elif isinstance(links, list):
        candidates = [item for item in links if isinstance(item, dict)]
    else:
        candidates = []

    for link in candidates:
        if str(link.get("@ref", "")) == ref_name:
            href = link.get("@href")
            return str(href) if href else None
    return None


def _extract_api_total_results(data: dict[str, Any]) -> int | None:
    search_results = data.get("search-results")
    if not isinstance(search_results, dict):
        return None
    value = search_results.get("opensearch:totalResults")
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _extract_quota(headers: Any) -> dict[str, Any]:
    get = headers.get if hasattr(headers, "get") else lambda *_args, **_kwargs: None
    return {
        "limit": get("X-RateLimit-Limit"),
        "remaining": get("X-RateLimit-Remaining"),
        "reset": get("X-RateLimit-Reset"),
    }


def _retry_delay_for_scopus_http(
    *,
    status_code: int,
    headers: Any,
    attempt: int,
    backoff_s: float,
) -> float | None:
    retryable = {408, 425, 429, 500, 502, 503, 504}
    if status_code not in retryable:
        return None

    if status_code == 429:
        retry_after = _parse_retry_after(headers)
        if retry_after is not None:
            return max(1.0, retry_after)

        reset_ts = _coerce_int(headers.get("X-RateLimit-Reset")) if hasattr(headers, "get") else None
        if reset_ts is not None:
            wait_s = max(float(reset_ts - int(time.time())), 1.0)
            return wait_s

    return max(backoff_s * (2**attempt), 1.0)


def _parse_retry_after(headers: Any) -> float | None:
    if not hasattr(headers, "get"):
        return None
    raw = headers.get("Retry-After")
    if not raw:
        return None

    text = str(raw).strip()
    try:
        return float(text)
    except ValueError:
        pass

    try:
        when = parsedate_to_datetime(text)
    except Exception:
        return None
    now = datetime.now(tz=timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max((when - now).total_seconds(), 0.0)


def _looks_like_cloudflare_block(detail: str) -> bool:
    body = detail.lower()
    return "cloudflare" in body and ("blocked" in body or "attention required" in body)


def _enforce_scopus_quota_guard(*, cfg: AudiobookConfig, min_remaining_quota: int) -> None:
    if min_remaining_quota <= 0:
        return

    row = get_provider_state(cfg, provider="scopus")
    remaining = _coerce_int(row.get("quota_remaining"))
    reset_ts = _coerce_int(row.get("quota_reset"))
    now_ts = int(time.time())
    if remaining is None or reset_ts is None:
        return

    if remaining > min_remaining_quota:
        return

    if reset_ts <= now_ts:
        return

    reset_at = datetime.fromtimestamp(reset_ts, tz=timezone.utc).isoformat()
    raise RuntimeError(
        f"Scopus quota guard is active: remaining={remaining} "
        f"(threshold={min_remaining_quota}), reset_at={reset_at}."
    )


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _clean_scopus_id(value: str) -> str:
    return value.strip().replace("SCOPUS_ID:", "")


def _clean_author_id(value: str) -> str:
    return value.strip().replace("AUTHOR_ID:", "")


def _resolve_summary_path(cfg: AudiobookConfig, action: str, summary_path: str | Path | None) -> Path:
    if summary_path is not None:
        return Path(summary_path).expanduser().resolve()
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(cfg.output_root).resolve() / "research" / f"scopus_{action}_{stamp}.json"


def _finalize_summary(payload: dict[str, Any], summary_target: Path) -> dict[str, Any]:
    payload["finished_at"] = utc_now_iso()
    write_json(summary_target, payload)
    return payload


def _normalize_scopus_base_url(value: str) -> str:
    text = value.strip() or DEFAULT_SCOPUS_BASE_URL
    if not text.endswith("/"):
        text += "/"
    return text


def _try_parse_json(text: str) -> Any:
    body = text.strip()
    if not body:
        return None
    import json

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def _clip_text(text: str, max_chars: int = 12000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...(truncated)"

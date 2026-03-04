from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import AudiobookConfig
from .pipeline import build_source, ingest_source
from .preflight import ensure_marker_ready
from .safeguards import (
    enforce_min_interval,
    parse_float_setting,
    parse_int_setting,
    record_provider_event,
)
from .utils import infer_source_type, utc_now_iso, write_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ANNAS_RAW_DIR = PROJECT_ROOT / "annas raw"
MB_BYTES = 1024 * 1024


@dataclass(frozen=True)
class AnnasCommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def run_research_annas(
    *,
    cfg: AudiobookConfig,
    action: str,
    kind: str = "book",
    query: str | None = None,
    identifier: str | None = None,
    filename: str | None = None,
    download_dir: str | Path | None = None,
    extra_args: list[str] | None = None,
    ingest: bool = False,
    build: bool = False,
    enhance: bool = True,
    marker_extra_args: list[str] | None = None,
    min_download_size_mb: float | None = None,
    summary_path: str | Path | None = None,
) -> dict[str, Any]:
    action = action.strip().lower()
    kind = kind.strip().lower()
    extra_args = list(extra_args or [])
    marker_extra_args = list(marker_extra_args or [])
    resolved_min_download_size_mb = _resolve_min_download_size_mb(cfg, min_download_size_mb)
    min_download_size_bytes = int(resolved_min_download_size_mb * MB_BYTES)
    if action not in {"search", "download"}:
        raise ValueError(f"unsupported action: {action}")
    if kind not in {"book", "article"}:
        raise ValueError(f"unsupported kind: {kind}")
    if action == "search" and not query:
        raise ValueError("--query is required for action=search")
    if action == "download" and not identifier:
        raise ValueError("--identifier is required for action=download")
    if action == "search" and (ingest or build):
        raise ValueError("--ingest/--build are only valid for action=download")

    started_at = utc_now_iso()
    summary_target = _resolve_summary_path(cfg=cfg, action=action, summary_path=summary_path)
    payload: dict[str, Any] = {
        "started_at": started_at,
        "finished_at": started_at,
        "action": action,
        "kind": kind,
        "query": query,
        "identifier": identifier,
        "filename": filename,
        "download_dir": (
            str(_resolve_download_dir(cfg=cfg, download_dir=download_dir)) if action == "download" else None
        ),
        "ingest": bool(ingest),
        "build": bool(build),
        "enhance": bool(enhance),
        "min_download_size_mb": resolved_min_download_size_mb,
        "min_download_size_bytes": min_download_size_bytes,
        "error": None,
        "ok": False,
        "exit_code": 1,
        "summary_path": str(summary_target),
    }

    try:
        if action == "search":
            assert query is not None
            search_payload = run_annas_search(
                cfg=cfg,
                kind=kind,
                query=query,
                extra_args=extra_args,
                min_download_size_bytes=min_download_size_bytes,
            )
            payload["search"] = search_payload
            payload["total"] = 1
            payload["succeeded"] = 1
            payload["failed"] = 0
            payload["ok"] = True
            payload["exit_code"] = 0
            return _finalize_summary(payload, summary_target)

        assert identifier is not None
        download_payload = run_annas_download(
            cfg=cfg,
            kind=kind,
            identifier=identifier,
            filename=filename,
            download_dir=download_dir,
            extra_args=extra_args,
        )
        payload["download"] = download_payload
        downloaded_files_all = [Path(path) for path in download_payload["downloaded_files"]]
        selected_files, dropped_small_files, file_sizes = _select_downloaded_files(
            downloaded_files_all,
            min_download_size_bytes=min_download_size_bytes,
        )
        payload["download"]["downloaded_files_all"] = [str(path) for path in downloaded_files_all]
        payload["download"]["downloaded_files_selected"] = [str(path) for path in selected_files]
        payload["download"]["dropped_small_files"] = [str(path) for path in dropped_small_files]
        payload["download"]["file_sizes"] = file_sizes
        payload["download"]["size_filter"] = {
            "min_download_size_mb": resolved_min_download_size_mb,
            "min_download_size_bytes": min_download_size_bytes,
            "rule": "drop files below threshold when at least one file meets threshold; keep all if all files are below threshold",
        }
        downloaded_files = list(selected_files)
        payload["downloaded_files"] = [str(path) for path in downloaded_files]
        payload["downloaded_files_all"] = [str(path) for path in downloaded_files_all]
        payload["dropped_small_files"] = [str(path) for path in dropped_small_files]

        if not ingest and not build:
            payload["total"] = len(downloaded_files)
            payload["succeeded"] = len(downloaded_files)
            payload["failed"] = 0
            payload["ok"] = len(downloaded_files) > 0
            payload["exit_code"] = 0 if payload["ok"] else 1
            if not payload["ok"]:
                payload["error"] = "download succeeded but no files were detected in download directory"
            return _finalize_summary(payload, summary_target)

        if not downloaded_files:
            payload["total"] = 0
            payload["succeeded"] = 0
            payload["failed"] = 0
            payload["error"] = "download succeeded but no files were detected in download directory"
            return _finalize_summary(payload, summary_target)

        ensure_marker_ready(cfg)
        results: list[dict[str, Any]] = []
        for path in downloaded_files:
            row = {
                "source_path": str(path),
                "source_id": None,
                "ingest_ok": False,
                "build_ok": False,
                "error": None,
            }
            try:
                source_type = infer_source_type(path)
                manifest = ingest_source(
                    source_path=path,
                    source_type=source_type,
                    cfg=cfg,
                    marker_extra_args=marker_extra_args,
                )
                row["source_id"] = manifest.source_id
                row["ingest_ok"] = True
            except Exception as exc:
                row["error"] = f"ingest failed: {exc}"
                results.append(row)
                continue

            if build:
                try:
                    build_source(source_id=manifest.source_id, cfg=cfg, enhance=enhance)
                    row["build_ok"] = True
                except Exception as exc:
                    row["error"] = f"build failed: {exc}"
            else:
                row["build_ok"] = True
            results.append(row)

        payload["results"] = results
        payload["total"] = len(results)
        payload["succeeded"] = sum(1 for row in results if bool(row["ingest_ok"]) and bool(row["build_ok"]))
        payload["failed"] = sum(1 for row in results if not (bool(row["ingest_ok"]) and bool(row["build_ok"])))
        payload["ok"] = bool(payload["total"] > 0 and payload["failed"] == 0)
        payload["exit_code"] = 0 if payload["ok"] else 1
        return _finalize_summary(payload, summary_target)

    except Exception as exc:
        payload["error"] = str(exc)
        return _finalize_summary(payload, summary_target)


def run_annas_search(
    *,
    cfg: AudiobookConfig,
    kind: str,
    query: str,
    extra_args: list[str] | None = None,
    min_download_size_bytes: int = MB_BYTES,
) -> dict[str, Any]:
    cmd_candidates = [
        [cfg.annas_mcp_bin, "search", query, *(extra_args or [])],
        [cfg.annas_mcp_bin, f"{kind}-search", query, *(extra_args or [])],
    ]
    result = _run_annas_with_fallback(cfg=cfg, cmd_candidates=cmd_candidates, download_dir=None)
    op = result.command[1] if len(result.command) > 1 else "search"

    parsed = _try_parse_json(result.stdout)
    candidates = _parse_search_candidates(result.stdout)
    selected_candidates, dropped_small_candidates = _select_candidates(
        candidates,
        min_download_size_bytes=min_download_size_bytes,
    )
    return {
        "operation": op,
        "query": query,
        "command": result.command,
        "stdout": _clip_text(result.stdout),
        "stderr": _clip_text(result.stderr),
        "parsed_json": parsed,
        "candidates": selected_candidates,
        "all_candidates": candidates,
        "dropped_small_candidates": dropped_small_candidates,
        "size_filter": {
            "min_download_size_bytes": min_download_size_bytes,
            "min_download_size_mb": round(min_download_size_bytes / MB_BYTES, 3),
            "rule": "drop candidates below threshold when at least one candidate meets threshold; keep all if all candidates are below threshold",
        },
    }


def run_annas_download(
    *,
    cfg: AudiobookConfig,
    kind: str,
    identifier: str,
    filename: str | None = None,
    download_dir: str | Path | None = None,
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    resolved_download_dir = _resolve_download_dir(cfg=cfg, download_dir=download_dir)
    before = {path.resolve() for path in resolved_download_dir.rglob("*") if path.is_file()}

    base_args = [identifier]
    if filename:
        base_args.append(filename)
    base_args.extend(extra_args or [])
    cmd_candidates = [
        [cfg.annas_mcp_bin, "download", *base_args],
        [cfg.annas_mcp_bin, f"{kind}-download", *base_args],
    ]
    result = _run_annas_with_fallback(
        cfg=cfg,
        cmd_candidates=cmd_candidates,
        download_dir=resolved_download_dir,
    )
    op = result.command[1] if len(result.command) > 1 else "download"

    after = {path.resolve() for path in resolved_download_dir.rglob("*") if path.is_file()}
    created = sorted(after - before, key=lambda path: str(path))
    if filename:
        candidate = Path(filename).expanduser()
        expected = candidate if candidate.is_absolute() else (resolved_download_dir / candidate)
        expected = expected.resolve()
        if expected.exists() and expected not in created:
            created.append(expected)

    return {
        "operation": op,
        "identifier": identifier,
        "filename": filename,
        "download_dir": str(resolved_download_dir),
        "command": result.command,
        "stdout": _clip_text(result.stdout),
        "stderr": _clip_text(result.stderr),
        "downloaded_files": [str(path) for path in sorted(created, key=lambda path: str(path))],
    }


def _run_annas_command(
    *,
    cfg: AudiobookConfig,
    cmd: list[str],
    download_dir: Path | None,
) -> AnnasCommandResult:
    env = os.environ.copy()
    if cfg.annas_secret_key:
        env.setdefault("ANNAS_SECRET_KEY", cfg.annas_secret_key)
    if cfg.annas_base_url:
        env.setdefault("ANNAS_BASE_URL", _normalize_annas_base_url(cfg.annas_base_url))
    effective_download_dir = _resolve_download_dir(cfg=cfg, download_dir=download_dir)
    effective_download_dir.mkdir(parents=True, exist_ok=True)
    env["ANNAS_DOWNLOAD_PATH"] = str(effective_download_dir)

    if not env.get("ANNAS_SECRET_KEY", "").strip():
        raise RuntimeError(
            "ANNAS_SECRET_KEY is required for annas-mcp operations. "
            "Set ANNAS_SECRET_KEY or AudiobookConfig.annas_secret_key."
        )

    max_retries = parse_int_setting(cfg.annas_max_retries, default=2, minimum=0)
    retry_backoff_s = parse_float_setting(cfg.annas_retry_backoff_s, default=2.0, minimum=0.1)
    min_interval_s = parse_float_setting(cfg.annas_min_interval_s, default=2.0, minimum=0.0)
    timeout_s = parse_float_setting(cfg.annas_cmd_timeout_s, default=1800.0, minimum=5.0)

    attempt = 0
    while True:
        waited_s = enforce_min_interval(cfg, provider="annas", min_interval_s=min_interval_s)
        try:
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                env=env,
                timeout=timeout_s,
            )
            result = AnnasCommandResult(
                command=list(cmd),
                returncode=int(proc.returncode),
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
            )
        except subprocess.TimeoutExpired as exc:
            result = AnnasCommandResult(
                command=list(cmd),
                returncode=124,
                stdout=(exc.stdout or ""),
                stderr=f"annas-mcp command timeout after {int(timeout_s)}s",
            )
        except OSError as exc:
            rendered = " ".join(shlex.quote(part) for part in cmd)
            raise RuntimeError(f"failed to execute annas-mcp command ({rendered}): {exc}") from exc

        record_provider_event(
            cfg,
            provider="annas",
            last_status_code=int(result.returncode),
            last_waited_seconds=round(waited_s, 3),
            command=result.command,
        )
        if result.returncode == 0:
            return result

        if attempt >= max_retries:
            return result

        if not _is_retryable_annas_failure(result):
            return result

        time.sleep(retry_backoff_s * (2**attempt))
        attempt += 1


def _run_annas_with_fallback(
    *,
    cfg: AudiobookConfig,
    cmd_candidates: list[list[str]],
    download_dir: Path | None,
) -> AnnasCommandResult:
    attempts: list[AnnasCommandResult] = []
    for cmd in cmd_candidates:
        result = _run_annas_command(cfg=cfg, cmd=cmd, download_dir=download_dir)
        if result.returncode == 0:
            return result
        attempts.append(result)
    if attempts:
        messages = "; ".join(_command_error_text(item) for item in attempts)
        raise RuntimeError(messages)
    raise RuntimeError("no annas-mcp commands were attempted")


def _resolve_download_dir(cfg: AudiobookConfig, download_dir: str | Path | None) -> Path:
    # Anna's MCP downloads are always anchored to the project-level raw directory.
    _ = cfg, download_dir
    path = DEFAULT_ANNAS_RAW_DIR.resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_summary_path(cfg: AudiobookConfig, action: str, summary_path: str | Path | None) -> Path:
    if summary_path is not None:
        return Path(summary_path).expanduser().resolve()
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(cfg.output_root).resolve() / "research" / f"annas_{action}_{stamp}.json"


def _finalize_summary(payload: dict[str, Any], summary_target: Path) -> dict[str, Any]:
    payload["finished_at"] = utc_now_iso()
    write_json(summary_target, payload)
    return payload


def _command_error_text(result: AnnasCommandResult) -> str:
    rendered = " ".join(shlex.quote(part) for part in result.command)
    detail = result.stderr.strip() or result.stdout.strip() or f"exit={result.returncode}"
    return f"annas-mcp command failed ({rendered}): {_clip_text(detail)}"


def _is_retryable_annas_failure(result: AnnasCommandResult) -> bool:
    body = f"{result.stderr}\n{result.stdout}".lower()
    if result.returncode in {124, 408, 425, 429, 500, 502, 503, 504}:
        return True

    hard_fail_tokens = (
        "invalid secret key",
        "unauthorized",
        "forbidden",
        "unknown command",
        "unsupported",
    )
    if any(token in body for token in hard_fail_tokens):
        return False

    retry_tokens = (
        "timeout",
        "timed out",
        "temporary",
        "try again",
        "connection reset",
        "connection refused",
        "i/o timeout",
        "too many requests",
        "429",
        "502",
        "503",
        "504",
        "ddos-guard",
        "cloudflare",
        "eof",
    )
    return any(token in body for token in retry_tokens)


def _try_parse_json(text: str) -> Any:
    body = text.strip()
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def _clip_text(text: str, max_chars: int = 12000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...(truncated)"


def _normalize_annas_base_url(value: str) -> str:
    text = value.strip()
    if not text:
        return text
    for prefix in ("https://", "http://"):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :]
            break
    return text.strip().strip("/")


def _resolve_min_download_size_mb(cfg: AudiobookConfig, override: float | None) -> float:
    if override is not None:
        value = float(override)
    else:
        value = float((cfg.annas_min_download_size_mb or "1.0").strip())
    if value < 0:
        raise ValueError("min download size must be >= 0")
    return value


def _parse_search_candidates(stdout: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in stdout.splitlines():
        line = raw.strip()
        entry_match = re.match(r"^(Book|Article)\s+(\d+):$", line)
        if entry_match:
            if current is not None:
                candidates.append(_finalize_candidate(current))
            current = {"kind": entry_match.group(1).lower(), "index": int(entry_match.group(2))}
            continue
        if current is None:
            continue
        if line.startswith("Title: "):
            current["title"] = line[len("Title: ") :].strip()
            continue
        if line.startswith("Authors: "):
            current["authors"] = line[len("Authors: ") :].strip()
            continue
        if line.startswith("Publisher: "):
            current["publisher"] = line[len("Publisher: ") :].strip()
            continue
        if line.startswith("Language: "):
            current["language"] = line[len("Language: ") :].strip()
            continue
        if line.startswith("Format: "):
            current["format"] = line[len("Format: ") :].strip()
            continue
        if line.startswith("Size: "):
            current["size"] = line[len("Size: ") :].strip()
            continue
        if line.startswith("URL: "):
            current["url"] = line[len("URL: ") :].strip()
            continue
        if line.startswith("Hash: "):
            current["hash"] = line[len("Hash: ") :].strip()
            continue
    if current is not None:
        candidates.append(_finalize_candidate(current))
    return candidates


def _finalize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    out = dict(candidate)
    size_bytes = _parse_size_to_bytes(str(out.get("size", "")).strip())
    out["size_bytes"] = size_bytes
    out["size_mb"] = round(size_bytes / MB_BYTES, 4) if size_bytes is not None else None
    return out


def _parse_size_to_bytes(value: str) -> int | None:
    match = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?B)\s*$", value, flags=re.IGNORECASE)
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2).upper()
    factor = {
        "B": 1,
        "KB": 1024,
        "MB": MB_BYTES,
        "GB": MB_BYTES * 1024,
        "TB": MB_BYTES * 1024 * 1024,
    }.get(unit)
    if factor is None:
        return None
    return int(amount * factor)


def _select_candidates(
    candidates: list[dict[str, Any]],
    *,
    min_download_size_bytes: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not candidates:
        return [], []
    has_qualified = any(
        (item.get("size_bytes") is not None and int(item["size_bytes"]) >= min_download_size_bytes)
        for item in candidates
    )
    selected: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for candidate in candidates:
        size_bytes = candidate.get("size_bytes")
        is_small = size_bytes is not None and int(size_bytes) < min_download_size_bytes
        entry = dict(candidate)
        entry["poor_candidate"] = bool(is_small)
        if has_qualified and is_small:
            dropped.append(entry)
        else:
            selected.append(entry)
    return selected, dropped


def _select_downloaded_files(
    files: list[Path],
    *,
    min_download_size_bytes: int,
) -> tuple[list[Path], list[Path], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(files, key=lambda item: str(item)):
        size_bytes = path.stat().st_size if path.exists() and path.is_file() else None
        rows.append(
            {
                "path": str(path),
                "size_bytes": size_bytes,
                "size_mb": round(size_bytes / MB_BYTES, 4) if size_bytes is not None else None,
            }
        )
    has_qualified = any(
        row["size_bytes"] is not None and int(row["size_bytes"]) >= min_download_size_bytes for row in rows
    )
    selected: list[Path] = []
    dropped: list[Path] = []
    for row in rows:
        row["poor_candidate"] = bool(
            row["size_bytes"] is not None and int(row["size_bytes"]) < min_download_size_bytes
        )
        path = Path(row["path"])
        if has_qualified and bool(row["poor_candidate"]):
            dropped.append(path)
        else:
            selected.append(path)
    return selected, dropped, rows

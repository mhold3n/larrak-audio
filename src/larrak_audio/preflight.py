from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from dataclasses import dataclass
from urllib import error, request

from .config import AudiobookConfig


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    remediation: str = ""

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "remediation": self.remediation,
        }


def run_doctor(cfg: AudiobookConfig, check_services: bool = True) -> dict[str, object]:
    checks: list[CheckResult] = []

    checks.append(_check_executable("annas_mcp", cfg.annas_mcp_bin, smoke_help=False))
    checks.append(_check_executable("marker", cfg.marker_bin, smoke_help=True))
    checks.append(_check_executable("ffmpeg", cfg.ffmpeg_bin, smoke_help=True))
    checks.append(_check_executable("ffprobe", _resolve_ffprobe(cfg.ffmpeg_bin), smoke_help=True))
    checks.append(_check_executable("ollama", "ollama", smoke_help=False))
    checks.append(_check_executable("meilisearch", "meilisearch", smoke_help=False))

    checks.append(_check_module("numpy"))
    checks.append(_check_module("torch"))
    checks.append(_check_module("transformers"))

    if check_services:
        checks.append(_check_url("meilisearch.health", f"{cfg.meili_url.rstrip('/')}/health"))
        checks.append(_check_url("ollama.tags", f"{cfg.ollama_base_url.rstrip('/')}/api/tags"))

    ok = all(item.ok for item in checks)
    return {
        "ok": ok,
        "checks": [item.to_dict() for item in checks],
        "summary": f"{sum(1 for c in checks if c.ok)}/{len(checks)} checks passed",
    }


def ensure_marker_ready(cfg: AudiobookConfig) -> None:
    check = _check_executable("marker", cfg.marker_bin, smoke_help=True)
    if check.ok:
        return
    raise RuntimeError(f"Marker preflight failed: {check.detail}. {check.remediation}".strip())


def _check_executable(name: str, command: str, smoke_help: bool) -> CheckResult:
    resolved = _resolve_command(command)
    if resolved is None:
        return CheckResult(
            name=name,
            ok=False,
            detail=f"command not found: {command}",
            remediation=f"Install {name} and/or set {name.upper()}_BIN environment variable.",
        )

    if not smoke_help:
        return CheckResult(name=name, ok=True, detail=f"found at {resolved}")

    try:
        proc = subprocess.run(
            [command, "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception as exc:
        return CheckResult(
            name=name,
            ok=False,
            detail=f"failed to run '{command} --help': {exc}",
            remediation="Verify command path and execution permissions.",
        )

    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit={proc.returncode}"
        return CheckResult(
            name=name,
            ok=False,
            detail=f"help probe failed: {detail}",
            remediation="Reinstall binary and confirm it starts successfully.",
        )

    return CheckResult(name=name, ok=True, detail=f"found at {resolved}")


def _check_module(module_name: str) -> CheckResult:
    if importlib.util.find_spec(module_name) is not None:
        return CheckResult(name=f"python.{module_name}", ok=True, detail="installed")
    return CheckResult(
        name=f"python.{module_name}",
        ok=False,
        detail="module missing",
        remediation=f"Install dependencies (for example: uv pip install {module_name}).",
    )


def _check_url(name: str, url: str) -> CheckResult:
    try:
        with request.urlopen(url, timeout=3) as resp:
            status = int(resp.status)
    except (error.URLError, TimeoutError) as exc:
        return CheckResult(
            name=name,
            ok=False,
            detail=f"unreachable: {exc}",
            remediation="Start local service and verify host/port configuration.",
        )

    if 200 <= status < 300:
        return CheckResult(name=name, ok=True, detail=f"status={status}")
    return CheckResult(
        name=name,
        ok=False,
        detail=f"unexpected status={status}",
        remediation="Check service logs and health endpoint configuration.",
    )


def _resolve_command(command: str) -> str | None:
    if os.sep in command:
        return command if (os.path.isfile(command) and os.access(command, os.X_OK)) else None
    return shutil.which(command)


def _resolve_ffprobe(ffmpeg_bin: str) -> str:
    if ffmpeg_bin.endswith("ffmpeg"):
        return ffmpeg_bin[:-6] + "ffprobe"
    return "ffprobe"

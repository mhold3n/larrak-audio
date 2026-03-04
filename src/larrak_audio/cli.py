from __future__ import annotations

import argparse
import json
import sys

from larrak_audio.batch_run import run_test_files
from larrak_audio.config import load_audiobook_config
from larrak_audio.index_meili import MeiliClient
from larrak_audio.pipeline import build_source, ingest_source, source_paths
from larrak_audio.preflight import ensure_marker_ready, run_doctor
from larrak_audio.queue import JobQueue
from larrak_audio.research_annas import run_research_annas
from larrak_audio.research_scopus import run_research_scopus
from larrak_audio.service import run_api
from larrak_audio.utils import infer_source_type
from larrak_audio.worker import run_worker_loop, run_worker_once


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Larrak Audio local pipeline CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_doctor = sub.add_parser("doctor", help="Validate local dependencies and services")
    p_doctor.add_argument("--skip-services", action="store_true")

    p_ingest = sub.add_parser("ingest", help="Ingest source into markdown + manifests")
    p_ingest.add_argument("--source", required=True)
    p_ingest.add_argument("--type", choices=["pdf", "md", "txt"], default=None)
    p_ingest.add_argument(
        "--marker-extra-arg",
        action="append",
        default=[],
        help="Extra arg passed to marker binary (repeatable)",
    )

    p_build = sub.add_parser("build", help="Enhance/index/tts from source manifest")
    p_build.add_argument("--source-id", required=True)
    p_build.add_argument("--enhance", choices=["on", "off"], default="on")

    p_run_test_files = sub.add_parser("run-test-files", help="Run ingest+build for files in a directory")
    p_run_test_files.add_argument("--input-dir", default="test files")
    p_run_test_files.add_argument("--glob", default="*.pdf")
    p_run_test_files.add_argument("--recursive", action="store_true")
    p_run_test_files.add_argument("--enhance", choices=["on", "off"], default="on")
    p_run_test_files.add_argument(
        "--marker-extra-arg",
        action="append",
        default=[],
        help="Extra arg passed to marker binary (repeatable)",
    )
    p_run_test_files.add_argument("--summary-path", default=None)

    p_worker = sub.add_parser("worker", help="Run queued ingest/build jobs")
    p_worker.add_argument("--loop", action="store_true", help="Run forever")
    p_worker.add_argument("--interval-s", type=float, default=2.0)
    p_worker.add_argument("--max-retries", type=int, default=2)

    p_search = sub.add_parser("search", help="Search chunk index for source")
    p_search.add_argument("--query", required=True)
    p_search.add_argument("--source-id", required=True)
    p_search.add_argument("--limit", type=int, default=10)

    p_serve = sub.add_parser("serve", help="Run local REST API")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8787)

    p_research = sub.add_parser("research-annas", help="Search/download sources via annas-mcp")
    p_research.add_argument("--action", choices=["search", "download"], required=True)
    p_research.add_argument("--kind", choices=["book", "article"], default="book")
    p_research.add_argument("--query", default=None)
    p_research.add_argument("--identifier", default=None)
    p_research.add_argument("--filename", default=None)
    p_research.add_argument("--download-dir", default=None)
    p_research.add_argument("--extra-arg", action="append", default=[], help="Extra arg passed to annas-mcp")
    p_research.add_argument("--ingest", action="store_true")
    p_research.add_argument("--build", action="store_true")
    p_research.add_argument("--enhance", choices=["on", "off"], default="on")
    p_research.add_argument(
        "--min-download-size-mb",
        type=float,
        default=None,
        help="Minimum preferred file size in MB for candidate/file filtering (default: 1.0).",
    )
    p_research.add_argument(
        "--marker-extra-arg",
        action="append",
        default=[],
        help="Extra arg passed to marker binary during optional ingest",
    )
    p_research.add_argument("--summary-path", default=None)

    p_scopus = sub.add_parser("research-scopus", help="Query Elsevier Scopus API")
    p_scopus.add_argument("--action", choices=["search", "abstract", "author", "citing"], required=True)
    p_scopus.add_argument("--query", default=None)
    p_scopus.add_argument("--scopus-id", default=None)
    p_scopus.add_argument("--author-id", default=None)
    p_scopus.add_argument("--count", type=int, default=5)
    p_scopus.add_argument("--start", type=int, default=0)
    p_scopus.add_argument("--sort", default="coverDate")
    p_scopus.add_argument("--summary-path", default=None)

    p_gui = sub.add_parser("gui", help="Launch Tkinter multi-source search + batch processing UI")
    p_gui.add_argument("--enhance", choices=["on", "off"], default="on")
    p_gui.add_argument(
        "--annas-min-download-size-mb",
        type=float,
        default=None,
        help="Minimum preferred Anna's file size in MB for candidate filtering (defaults to config).",
    )
    p_gui.add_argument(
        "--marker-extra-arg",
        action="append",
        default=[],
        help="Extra arg passed to marker binary during ingest in GUI batch mode.",
    )

    args = parser.parse_args(argv)
    cfg = load_audiobook_config()

    if args.cmd == "doctor":
        report = run_doctor(cfg=cfg, check_services=not bool(args.skip_services))
        print(json.dumps(report, indent=2))
        return 0 if bool(report.get("ok")) else 1

    if args.cmd == "ingest":
        ensure_marker_ready(cfg)
        source_type = args.type or infer_source_type(args.source)
        manifest = ingest_source(
            source_path=args.source,
            source_type=source_type,
            cfg=cfg,
            marker_extra_args=list(args.marker_extra_arg),
        )
        print(json.dumps({"source_id": manifest.source_id, "paths": source_paths(manifest.source_id, cfg)}, indent=2))
        return 0

    if args.cmd == "build":
        ensure_marker_ready(cfg)
        enhance = args.enhance == "on"
        payload = build_source(source_id=args.source_id, cfg=cfg, enhance=enhance)
        print(json.dumps(payload, indent=2))
        return 0

    if args.cmd == "run-test-files":
        payload = run_test_files(
            cfg=cfg,
            input_dir=args.input_dir,
            glob_pattern=args.glob,
            recursive=bool(args.recursive),
            enhance=args.enhance == "on",
            marker_extra_args=list(args.marker_extra_arg),
            summary_path=args.summary_path,
        )
        print(json.dumps(payload, indent=2))
        return int(payload.get("exit_code", 1))

    if args.cmd == "worker":
        queue = JobQueue(cfg.queue_db)
        if args.loop:
            run_worker_loop(queue=queue, cfg=cfg, interval_s=args.interval_s, max_retries=args.max_retries)
            return 0

        worked = run_worker_once(queue=queue, cfg=cfg, max_retries=args.max_retries)
        print(json.dumps({"processed": bool(worked)}, indent=2))
        return 0

    if args.cmd == "search":
        client = MeiliClient(cfg)
        result = client.search_chunks(query=args.query, source_id=args.source_id, limit=args.limit)
        print(json.dumps(result, indent=2))
        return 0

    if args.cmd == "serve":
        run_api(host=args.host, port=args.port, cfg=cfg)
        return 0

    if args.cmd == "gui":
        from larrak_audio.gui_app import run_gui_app

        return run_gui_app(
            cfg=cfg,
            enhance=args.enhance == "on",
            annas_min_download_size_mb=args.annas_min_download_size_mb,
            marker_extra_args=list(args.marker_extra_arg),
        )

    if args.cmd == "research-annas":
        payload = run_research_annas(
            cfg=cfg,
            action=args.action,
            kind=args.kind,
            query=args.query,
            identifier=args.identifier,
            filename=args.filename,
            download_dir=args.download_dir,
            extra_args=list(args.extra_arg),
            ingest=bool(args.ingest),
            build=bool(args.build),
            enhance=args.enhance == "on",
            min_download_size_mb=args.min_download_size_mb,
            marker_extra_args=list(args.marker_extra_arg),
            summary_path=args.summary_path,
        )
        print(json.dumps(payload, indent=2))
        return int(payload.get("exit_code", 1))

    if args.cmd == "research-scopus":
        payload = run_research_scopus(
            cfg=cfg,
            action=args.action,
            query=args.query,
            scopus_id=args.scopus_id,
            author_id=args.author_id,
            count=args.count,
            start=args.start,
            sort=args.sort,
            summary_path=args.summary_path,
        )
        print(json.dumps(payload, indent=2))
        return int(payload.get("exit_code", 1))

    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

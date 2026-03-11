# Larrak Audio

Standalone local audiobook pipeline with:
- Marker PDF extraction
- Meilisearch indexing
- Qwen TTS synthesis
- ffmpeg/ffprobe packaging to chapter MP3 + M4B

## Quick Start (macOS)
1. Run bootstrap:
```bash
./tools/bootstrap_macos.sh
```
2. Activate venv:
```bash
source .venv/bin/activate
```
3. Validate local setup (Marker included):
```bash
larrak-audio doctor
```

## Environment Variables
- `ANNAS_MCP_BIN` (default: auto-detect `tools/bin/annas-mcp` if executable, otherwise `annas-mcp` on `PATH`)
- `ANNAS_SECRET_KEY` (required for `research-annas`)
- `ANNAS_DOWNLOAD_PATH` (optional override for default download root; default is project-local `annas raw/`)
- `ANNAS_BASE_URL` (default: `annas-archive.gl`; passed through to annas-mcp)
- `ANNAS_MIN_DOWNLOAD_SIZE_MB` (default: `1.0`; files below this are deprioritized)
- `ANNAS_MIN_INTERVAL_S` (default: `2.0`; minimum delay between annas-mcp calls)
- `ANNAS_MAX_RETRIES` (default: `2`; retries for transient annas failures)
- `ANNAS_RETRY_BACKOFF_S` (default: `2.0`; exponential retry base seconds)
- `ANNAS_CMD_TIMEOUT_S` (default: `1800`; timeout for a single annas-mcp command)
- `SCOPUS_API_KEY` (required for `research-scopus`)
- `SCOPUS_BASE_URL` (default: `https://api.elsevier.com/`)
- `SCOPUS_MIN_INTERVAL_S` (default: `1.0`; minimum delay between Scopus requests)
- `SCOPUS_MAX_RETRIES` (default: `3`; retries for transient Scopus failures)
- `SCOPUS_RETRY_BACKOFF_S` (default: `2.0`; exponential retry base seconds)
- `SCOPUS_REQUEST_TIMEOUT_S` (default: `30`; timeout per Scopus request)
- `SCOPUS_MIN_REMAINING_QUOTA` (default: `25`; blocks requests when previous known quota is below this before reset)
- `MARKER_BIN` (default: `marker_single`)
- `OLLAMA_BASE_URL` (default: `http://127.0.0.1:11434`)
- `OLLAMA_MODEL_CLEANUP` (default: `qwen2.5:7b-instruct`)
- `MEILI_URL` (default: `http://127.0.0.1:7700`)
- `MEILI_MASTER_KEY`
- `MEILI_KEY_DOC_CHUNKS`
- `MEILI_KEY_DOC_CHAPTERS`
- `MEILI_KEY_DOC_ASSETS`
- `QWEN_TTS_MODEL_ID` (default: `Qwen/Qwen3-TTS-0.6B`)
- `QWEN_TTS_DEVICE` (default: `mps`)
- `TTS_BACKEND` (default: `qwen`, options: `qwen|macos`)
- `MACOS_TTS_VOICE` (default: `Samantha`, used when `TTS_BACKEND=macos`)
- `MACOS_TTS_RATE` (default: `185`, words per minute; used when `TTS_BACKEND=macos`)
- `FFMPEG_BIN` (default: `ffmpeg`)
- `LARRAK_AUDIO_OUTPUT_ROOT` (default: `outputs/audiobooks`)
- `LARRAK_AUDIO_QUEUE_DB` (default: `outputs/audiobooks/jobs.sqlite3`)

## Commands
- `larrak-audio doctor [--skip-services]`
- `larrak-audio ingest --source <path> --type pdf|md|txt [--marker-extra-arg ...]`
- `larrak-audio build --source-id <id> --enhance on|off`
- `larrak-audio run-test-files [--input-dir "test files"] [--glob "*.pdf"] [--recursive] [--enhance on|off] [--marker-extra-arg ...] [--summary-path <path>]`
- `larrak-audio research-annas --action search|download --kind book|article ...`
- `larrak-audio research-scopus --action search|abstract|author|citing ...`
- `larrak-audio gui [--enhance on|off] [--annas-min-download-size-mb <float>] [--marker-extra-arg ...]`
- `larrak-audio worker --loop`
- `larrak-audio search --query "..." --source-id <id>`
- `larrak-audio serve --host 127.0.0.1 --port 8787`

## Marker Verification
The `doctor` command validates Marker binary discovery and executable health by running:
- `<MARKER_BIN> --help`

`ingest` and `build` also enforce Marker readiness before executing.

## Batch Marker Run (`test files`)
Run ingest + build for all matching files (default: PDFs in `test files`):

```bash
larrak-audio run-test-files
```

Override input directory or pattern:

```bash
larrak-audio run-test-files --input-dir "test files" --glob "*.pdf" --recursive
```

Pass extra marker args and save summary to an explicit location:

```bash
larrak-audio run-test-files \
  --marker-extra-arg=--max_pages \
  --marker-extra-arg=5 \
  --summary-path outputs/audiobooks/batch_runs/manual_run.json
```

Behavior:
- Processes files in deterministic sorted order.
- Continues after per-file ingest/build failures.
- Prints final JSON summary to stdout and writes it to disk.
- Default summary path: `outputs/audiobooks/batch_runs/test_files_<UTC_TIMESTAMP>.json`.
- Exit code is `0` only when at least one file is discovered and all files succeed; otherwise `1`.

## Research Stage (Annas MCP)
Use `annas-mcp` as the first step to find/download sources before marker ingest:

Setup notes:
- Install `annas-mcp` from its release artifacts and ensure it is on `PATH`.
- If the binary is not on `PATH`, set `ANNAS_MCP_BIN=/absolute/path/to/annas-mcp`.
- Set `ANNAS_SECRET_KEY` before running `research-annas`.

Search books:

```bash
larrak-audio research-annas \
  --action search \
  --kind book \
  --query "ISO 6336"
```

Download a book by identifier (MD5 in annas-mcp docs) into a local research folder:

```bash
larrak-audio research-annas \
  --action download \
  --kind book \
  --identifier "<MD5>"
```

Download directory for Anna's MCP is always:
- `./annas raw`

Download and immediately run marker ingest/build on the downloaded files:

```bash
larrak-audio research-annas \
  --action download \
  --kind book \
  --identifier "<MD5>" \
  --min-download-size-mb 1.0 \
  --ingest \
  --build \
  --enhance off
```

Behavior:
- Writes a JSON summary for each run (default: `outputs/audiobooks/research/annas_<action>_<UTC_TIMESTAMP>.json`).
- For `--action download --ingest --build`, each downloaded file continues on error and records per-file status.
- Marker is only required when `--ingest` or `--build` are requested.
- Size filter rule (`--min-download-size-mb`, default `1.0`):
  - Files/candidates below threshold are treated as poor candidates.
  - They are dropped only when at least one candidate/file is at or above threshold.
  - If all candidates/files are below threshold, all are kept.

## Research Stage (Scopus / Elsevier)
Use Scopus API lookups for standards/papers that are hard to resolve by ISBN/DOI-only workflows:

Search:

```bash
larrak-audio research-scopus \
  --action search \
  --query 'TITLE("ISO 15550") OR TITLE("ISO 3046-1")' \
  --count 10 \
  --sort relevancy
```

Get full abstract details:

```bash
larrak-audio research-scopus \
  --action abstract \
  --scopus-id 85012345678
```

Get author profile:

```bash
larrak-audio research-scopus \
  --action author \
  --author-id 7004212771
```

Get forward citations for a paper:

```bash
larrak-audio research-scopus \
  --action citing \
  --scopus-id 85012345678 \
  --count 10
```

Behavior:
- Writes a JSON summary for each run (default: `outputs/audiobooks/research/scopus_<action>_<UTC_TIMESTAMP>.json`).
- Uses Elsevier endpoint defaults compatible with `scopus-mcp` (`search`, `abstract`, `author`, `citing`).
- Exit code is `0` on successful API response, otherwise `1`.

## API Key Guardrails
Both research integrations persist lightweight request state in:
- `outputs/audiobooks/research/.api_guard_state.json`

Scopus safeguards:
- Enforces minimum inter-request delay (`SCOPUS_MIN_INTERVAL_S`).
- Retries transient failures (`429`, `5xx`, network/timeout) with exponential backoff.
- Honors `Retry-After` and `X-RateLimit-Reset` where available.
- Stores quota headers and blocks new requests when remaining quota is at/below `SCOPUS_MIN_REMAINING_QUOTA` until reset.

Anna’s safeguards:
- Enforces minimum delay between `annas-mcp` invocations (`ANNAS_MIN_INTERVAL_S`).
- Adds bounded retries with backoff for transient failures (timeouts, `429`/`5xx`, temporary gateway blocks).
- Does not retry hard failures such as invalid secret key.
- Applies command timeout via `ANNAS_CMD_TIMEOUT_S`.

## Tkinter GUI Workflow
Launch the desktop multi-source workflow UI:

```bash
larrak-audio gui
```

Optional startup settings:

```bash
larrak-audio gui \
  --enhance off \
  --annas-min-download-size-mb 1.0 \
  --marker-extra-arg=--page_range \
  --marker-extra-arg=0
```

GUI behavior:
- Searches Anna's and Scopus from one query.
- Supports `Basic` and `Advanced` search modes in the GUI:
  - Advanced syntax: `field>="value"`, `field="value"`, `field<="value"`
  - `>=` include, `=` exact, `<=` exclude
  - Combine clauses with commas or spaces
  - Example: `author>="fitzgerald", title="the great gadsby", metadata>="great", metadata<="decaprio"`
  - Supported fields: `author`, `title`, `doi`, `metadata`, `abstract`, `keyword`, `journal`
- Shows separate result tables for each source API.
- Lets you add Anna's results directly to a queue.
- For Scopus results, requires manual mapping to an Anna's candidate before queueing.
- `Download + Process` runs sequential batch download + ingest + build for all queued items.
- Continues on per-item failures and writes summary JSON to:
  - `outputs/audiobooks/batch_runs/gui_batch_<UTC_TIMESTAMP>.json`

Key handling:
- Reads keys from environment/config only.
- Missing `ANNAS_SECRET_KEY` disables download/process controls.
- Missing `SCOPUS_API_KEY` disables Scopus mapping flow while keeping Anna's-only flow usable.

## Output Layout
Build outputs are grouped per `source_id`, with separate `marker` and `audio` subfolders:

```text
outputs/audiobooks/
  sources/
    <source_id>/
      marker/
        source_manifest.json
        source.md
        chapters.json
        chapters_enhanced.json
        assets_manifest.json
        index_manifest.json
        build_manifest.json
        ...marker image artifacts...
      audio/
        chapter_01.mp3
        chapter_02.mp3
        book.m4b
```

This keeps each source self-contained while still separating text/JSON/image artifacts from audio artifacts.

## REST Endpoints
- `POST /jobs`
- `GET /jobs/{job_id}`
- `GET /jobs/{job_id}/artifacts`
- `POST /search`
- `GET /sources/{source_id}`

## Notes
- Non-text visual references are annotated in narration with:
  - `See additional materials for visual reference.`
- TTS is local via Hugging Face Qwen runtime.
- Initial model download can require internet; inference remains local.
- For reliable local English narration on macOS, you can use native voices:
  - `export TTS_BACKEND=macos`
  - `export MACOS_TTS_VOICE=Samantha`

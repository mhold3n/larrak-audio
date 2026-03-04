# Migration Manifest

Source repository: `/Users/maxholden/GitHub/Larrick_multi`
Destination repository: `/Users/maxholden/larrak-audio`

## Files moved
- `/Users/maxholden/GitHub/Larrick_multi/src/larrak2/audiobook/__init__.py` -> `src/larrak_audio/__init__.py`
- `/Users/maxholden/GitHub/Larrick_multi/src/larrak2/audiobook/config.py` -> `src/larrak_audio/config.py`
- `/Users/maxholden/GitHub/Larrick_multi/src/larrak2/audiobook/enhance.py` -> `src/larrak_audio/enhance.py`
- `/Users/maxholden/GitHub/Larrick_multi/src/larrak2/audiobook/index_meili.py` -> `src/larrak_audio/index_meili.py`
- `/Users/maxholden/GitHub/Larrick_multi/src/larrak2/audiobook/marker_adapter.py` -> `src/larrak_audio/marker_adapter.py`
- `/Users/maxholden/GitHub/Larrick_multi/src/larrak2/audiobook/packager.py` -> `src/larrak_audio/packager.py`
- `/Users/maxholden/GitHub/Larrick_multi/src/larrak2/audiobook/parse_marker.py` -> `src/larrak_audio/parse_marker.py`
- `/Users/maxholden/GitHub/Larrick_multi/src/larrak2/audiobook/pipeline.py` -> `src/larrak_audio/pipeline.py`
- `/Users/maxholden/GitHub/Larrick_multi/src/larrak2/audiobook/queue.py` -> `src/larrak_audio/queue.py`
- `/Users/maxholden/GitHub/Larrick_multi/src/larrak2/audiobook/service.py` -> `src/larrak_audio/service.py`
- `/Users/maxholden/GitHub/Larrick_multi/src/larrak2/audiobook/tts.py` -> `src/larrak_audio/tts.py`
- `/Users/maxholden/GitHub/Larrick_multi/src/larrak2/audiobook/tts_qwen.py` -> `src/larrak_audio/tts_qwen.py`
- `/Users/maxholden/GitHub/Larrick_multi/src/larrak2/audiobook/types.py` -> `src/larrak_audio/types.py`
- `/Users/maxholden/GitHub/Larrick_multi/src/larrak2/audiobook/utils.py` -> `src/larrak_audio/utils.py`
- `/Users/maxholden/GitHub/Larrick_multi/src/larrak2/audiobook/worker.py` -> `src/larrak_audio/worker.py`
- `/Users/maxholden/GitHub/Larrick_multi/src/larrak2/cli/audiobook.py` -> `src/larrak_audio/cli.py`
- `/Users/maxholden/GitHub/Larrick_multi/tests/ci/test_audiobook_chunking.py` -> `tests/test_audiobook_chunking.py`
- `/Users/maxholden/GitHub/Larrick_multi/tests/ci/test_audiobook_enhance.py` -> `tests/test_audiobook_enhance.py`
- `/Users/maxholden/GitHub/Larrick_multi/tests/ci/test_audiobook_parse_marker.py` -> `tests/test_audiobook_parse_marker.py`
- `/Users/maxholden/GitHub/Larrick_multi/tests/ci/test_audiobook_queue.py` -> `tests/test_audiobook_queue.py`
- `/Users/maxholden/GitHub/Larrick_multi/tests/ci/test_audiobook_integration_ingest.py` -> `tests/test_audiobook_integration_ingest.py`
- `/Users/maxholden/GitHub/Larrick_multi/tests/ci/test_audiobook_integration_build_service.py` -> `tests/test_audiobook_integration_build_service.py`
- `/Users/maxholden/GitHub/Larrick_multi/Docs/audiobook-local-module.md` -> `README.md` (adapted)

## Added in standalone
- `src/larrak_audio/preflight.py`
- `tools/bootstrap_macos.sh`
- `pyproject.toml`
- `README.md` (standalone runbook)
- `MIGRATION_MANIFEST.md`
- `tests/test_preflight.py`

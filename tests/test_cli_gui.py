from __future__ import annotations

import pytest

from larrak_audio import cli


def test_cli_gui_help_includes_expected_options(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["gui", "--help"])

    assert int(exc.value.code) == 0
    out = capsys.readouterr().out
    assert "--enhance" in out
    assert "--annas-min-download-size-mb" in out
    assert "--marker-extra-arg" in out

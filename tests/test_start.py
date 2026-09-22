import subprocess

import pytest

import start


def test_dependency_install_reports_unavailable_internet(monkeypatch, capsys):
    def fail_install(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(subprocess, "check_call", fail_install)

    with pytest.raises(SystemExit) as error:
        start.ensure_dependencies()

    assert error.value.code == 1
    assert "ERROR: Internet is unavailable." in capsys.readouterr().out

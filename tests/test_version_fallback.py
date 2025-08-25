from __future__ import annotations

import subprocess

import update
from __version__ import __version__ as file_version


def test_get_current_version_uses_version_file(monkeypatch):
    """Use ``__version__`` when git metadata is unavailable."""

    def raise_error(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "cmd")

    monkeypatch.setattr(update.subprocess, "check_output", raise_error)
    assert update.get_current_version() == file_version

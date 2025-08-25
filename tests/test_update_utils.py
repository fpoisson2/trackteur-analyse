"""Tests for update utility functions."""

import subprocess

from update import (
    _get_repo_releases_api_url,
    DEFAULT_REPO_RELEASES_API_URL,
    is_update_available,
)


def test_get_repo_releases_api_url_https(monkeypatch):
    """Origin URL via HTTPS should map to the correct API endpoint."""

    def fake_check_output(cmd, stderr=None, text=None):
        return "https://github.com/foo/bar.git"

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    assert (
        _get_repo_releases_api_url()
        == "https://api.github.com/repos/foo/bar/releases"
    )


def test_get_repo_releases_api_url_fallback(monkeypatch):
    """Missing or invalid remote should fall back to the default URL."""

    def fake_check_output(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    assert _get_repo_releases_api_url() == DEFAULT_REPO_RELEASES_API_URL


def test_is_update_available_beta():
    """Beta versions should compare lower than their stable counterparts."""

    assert is_update_available("2025.8.1b1", "2025.8.1")
    assert is_update_available("2025.8.1b1", "2025.8.1b2")
    assert not is_update_available("2025.8.1", "2025.8.1b1")

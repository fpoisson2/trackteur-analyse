"""Utility functions for application updates and version management."""

from __future__ import annotations

import subprocess
import sys
from typing import List, Tuple

import requests  # type: ignore[import-untyped]

# Default repository used when the Git remote cannot be determined. This
# points to the official fork that publishes releases.
DEFAULT_REPO_RELEASES_API_URL = (
    "https://api.github.com/repos/fpoisson2/trackteur-analyse/releases"
)


def _get_repo_releases_api_url() -> str:
    """Return the GitHub releases API URL for the current repository.

    The function inspects the configured ``remote.origin.url`` to build the
    proper API endpoint. If the remote cannot be read or does not match the
    expected GitHub format, it falls back to ``DEFAULT_REPO_RELEASES_API_URL``.
    """

    try:
        origin_url = subprocess.check_output(
            ["git", "config", "--get", "remote.origin.url"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, OSError):
        return DEFAULT_REPO_RELEASES_API_URL

    if origin_url.endswith(".git"):
        origin_url = origin_url[:-4]

    if "github.com" not in origin_url:
        return DEFAULT_REPO_RELEASES_API_URL

    try:
        if origin_url.startswith("git@"):
            path = origin_url.split(":", 1)[1]
        else:
            path = origin_url.split("github.com/", 1)[1]
    except IndexError:
        return DEFAULT_REPO_RELEASES_API_URL

    return f"https://api.github.com/repos/{path}/releases"


def _parse_version(version: str) -> Tuple[int, int, int, int, int]:
    """Parse ``year.month.patch`` or beta versions like ``2025.8.1b2``.

    The returned tuple is ``(year, month, patch, stable_flag, beta_number)``
    where ``stable_flag`` is ``1`` for stable releases and ``0`` for betas.
    Beta numbers start at ``1``.  Unknown formats return zeros so that any
    valid version is considered greater.
    """
    try:
        main, _, beta = version.partition("b")
        year_str, month_str, patch_str = main.split(".")
        if beta:
            return int(year_str), int(month_str), int(patch_str), 0, int(beta)
        return int(year_str), int(month_str), int(patch_str), 1, 0
    except (ValueError, AttributeError):
        return (0, 0, 0, 0, 0)


def get_current_version() -> str:
    """Return the latest Git tag or commit hash for the repository.

    If the repository metadata cannot be retrieved (for example when running
    from a release archive without the ``.git`` directory), the function falls
    back to reading the ``__version__`` module.  When that file is missing as
    well, ``"0.0.0"`` is returned.
    """

    try:
        return (
            subprocess.check_output(
                ["git", "describe", "--tags", "--abbrev=0"],
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except (subprocess.CalledProcessError, OSError):
        try:
            return (
                subprocess.check_output(
                    ["git", "rev-parse", "--short", "HEAD"],
                    stderr=subprocess.DEVNULL,
                )
                .decode()
                .strip()
            )
        except (subprocess.CalledProcessError, OSError):
            try:
                from __version__ import __version__

                return __version__
            except Exception:
                return "0.0.0"


def get_latest_version(include_prerelease: bool = False) -> str:
    """Return the latest release tag from GitHub.

    When ``include_prerelease`` is ``False`` (default), beta releases are
    ignored.
    """

    url = _get_repo_releases_api_url()
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        for release in data:
            if release.get("prerelease") and not include_prerelease:
                continue
            return release.get("tag_name", "")
        return ""
    except (requests.RequestException, ValueError):
        return ""


def is_update_available(current: str, latest: str) -> bool:
    """Return True if the latest version exceeds the current version."""
    return _parse_version(latest) > _parse_version(current)


def perform_update(version: str) -> None:
    """Fetch and apply the code and dependencies for ``version``.

    The repository is checked out at the requested tag and dependencies are
    reinstalled.  ``subprocess.CalledProcessError`` may be raised if any step
    fails.
    """
    subprocess.check_call(["git", "fetch", "--tags"])
    subprocess.check_call(["git", "checkout", version])
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"]
    )


def get_available_versions() -> List[str]:
    """Return a list of release tags available for updates."""
    url = _get_repo_releases_api_url()
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return [release.get("tag_name", "") for release in data if release.get("tag_name")]
    except (requests.RequestException, ValueError):
        return []

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


def _parse_version(version: str) -> Tuple[int, int, int]:
    """Parse a year.month.patch version string into a tuple of integers.

    Returns (0, 0, 0) if the format is unexpected.
    """
    try:
        year_str, month_str, patch_str = version.split(".")
        return int(year_str), int(month_str), int(patch_str)
    except (ValueError, AttributeError):
        return (0, 0, 0)


def get_current_version() -> str:
    """Return the latest Git tag or commit hash for the repository."""
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
            return "0.0.0"


def get_latest_version(branch: str = "main") -> str:
    """Return the latest release tag from GitHub for a given branch."""

    url = _get_repo_releases_api_url()
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        for release in data:
            target = release.get("target_commitish", "")
            if target.lower() == branch.lower():
                return release.get("tag_name", "")
        return ""
    except (requests.RequestException, ValueError):
        return ""


def is_update_available(current: str, latest: str) -> bool:
    """Return True if the latest version exceeds the current version."""
    return _parse_version(latest) > _parse_version(current)


def perform_update(branch: str = "main") -> None:
    """Fetch and apply the latest code and dependencies from ``branch``.

    This function checks out the requested branch, pulls the latest commits and
    reinstalls dependencies. It may raise ``subprocess.CalledProcessError`` if
    any command fails.
    """
    subprocess.check_call(["git", "fetch", "--tags"])
    subprocess.check_call(["git", "fetch", "origin", branch])
    subprocess.check_call(["git", "checkout", branch])
    subprocess.check_call(["git", "pull", "--ff-only", "origin", branch])
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"]
    )


def get_available_branches() -> List[str]:
    """Return a list of remote branches available for updates."""
    try:
        output = subprocess.check_output(
            ["git", "branch", "-r"], stderr=subprocess.DEVNULL, text=True
        )
        branches = [
            line.strip().split("/", 1)[1]
            for line in output.splitlines()
            if line.strip().startswith("origin/") and "->" not in line
        ]
        return branches or ["main", "dev"]
    except (subprocess.CalledProcessError, OSError):
        return ["main", "dev"]

"""Utility functions for application updates and version management."""

from __future__ import annotations

import subprocess
import sys
from typing import List, Tuple

import requests  # type: ignore[import-untyped]

REPO_RELEASES_API_URL = (
    "https://api.github.com/repos/trackteur/trackteur-analyse/releases"
)


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
    """Return the latest Git tag for the current repository."""
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
        return "0.0.0"


def get_latest_version(branch: str = "main") -> str:
    """Return the latest release tag from GitHub for a given branch."""
    try:
        resp = requests.get(REPO_RELEASES_API_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        for release in data:
            if release.get("target_commitish") == branch:
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
        return branches or ["main", "Dev"]
    except (subprocess.CalledProcessError, OSError):
        return ["main", "Dev"]

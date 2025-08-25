"""Utility functions for application updates and version management."""

from __future__ import annotations

import subprocess
import sys
from typing import Tuple

import requests  # type: ignore[import-untyped]

REPO_RELEASES_API_URL = (
    "https://api.github.com/repos/trackteur/trackteur-analyse/releases"
)


def _parse_version(version: str) -> Tuple[int, int, int, int]:
    """Parse a ``year.month.patch`` string with optional ``b`` suffix.

    The returned tuple is ``(year, month, patch, stability)`` where stability is
    ``0`` for beta versions (suffix ``b``) and ``1`` for stable releases. If the
    format is invalid, ``(0, 0, 0, 0)`` is returned.
    """
    try:
        year_str, month_str, patch_str = version.split(".")
        stability = 1
        if patch_str.endswith("b"):
            patch_str = patch_str[:-1]
            stability = 0
        return int(year_str), int(month_str), int(patch_str), stability
    except (ValueError, AttributeError):
        return (0, 0, 0, 0)


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


def get_latest_version() -> str:
    """Return the tag name for the most recent GitHub release."""
    try:
        resp = requests.get(REPO_RELEASES_API_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            return data[0].get("tag_name", "")
        return ""
    except (requests.RequestException, ValueError):
        return ""


def is_update_available(current: str, latest: str) -> bool:
    """Return ``True`` if the latest version exceeds the current version."""
    return _parse_version(latest) > _parse_version(current)


def perform_update(tag: str) -> None:
    """Fetch and apply the given release tag."""
    subprocess.check_call(["git", "fetch", "--tags"])
    subprocess.check_call(["git", "checkout", tag])
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"]
    )

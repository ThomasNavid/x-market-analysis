"""Filesystem anchors for the project."""

from pathlib import Path


def find_project_root(start: Path | None = None) -> Path:
    """Walk upward from `start` (default: this file) to the directory containing pyproject.toml."""
    current = (start or Path(__file__)).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError("Could not locate project root (pyproject.toml).")

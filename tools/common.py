"""Shared workspace confinement and subprocess helpers for media tools."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
from typing import Mapping, Sequence


class CapabilityUnavailable(RuntimeError):
    """An optional executable, package, or model is unavailable."""


def _path(root: Path, value: str) -> Path:
    root = root.resolve(strict=True)
    if not isinstance(value, str) or not value.strip() or "\x00" in value or "\\" in value:
        raise ValueError("Expected a nonempty workspace path using '/' separators")
    path = Path(value)
    if ".." in path.parts or "://" in value:
        raise ValueError("Traversal and URLs are not workspace paths")
    if not path.is_absolute():
        path = root / path
    else:
        actual_root = next((parent for parent in (path, *path.parents)
                            if parent.resolve() == root), None)
        path = (root / path.relative_to(actual_root) if actual_root is not None
                else root / value.lstrip("/"))  # FilesystemBackend virtual path.
    if path.is_symlink() and not path.exists():
        raise ValueError("Dangling symlinks are not valid media paths")
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise ValueError("Path must stay inside the workspace")
    return resolved


def input_path(root: Path, value: str) -> Path:
    path = _path(root, value)
    if not path.is_file():
        raise FileNotFoundError(f"Input file does not exist: {value}")
    return path


def output_path(root: Path, value: str) -> Path:
    path = _path(root, value)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Output already exists: {value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def run(
    args: Sequence[str | Path], timeout: float = 120, *,
    cwd: Path | None = None, env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    if not math.isfinite(timeout) or not 0 < timeout <= 3600:
        raise ValueError("Timeout must be between 0 and 3600 seconds")
    try:
        return subprocess.run(
            [str(arg) for arg in args], check=True, capture_output=True, text=True,
            timeout=timeout, cwd=cwd, env=None if env is None else {**os.environ, **env},
        )
    except FileNotFoundError as error:
        raise CapabilityUnavailable(f"Executable is unavailable: {args[0]}") from error


def probe(path: Path) -> dict:
    return json.loads(run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path,
    ]).stdout)


def artifact_path(root: Path, path: Path) -> str:
    resolved = path.resolve()
    root = root.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Artifact is outside the workspace")
    return "/" + resolved.relative_to(root).as_posix()


def publish(staged: Path, destination: Path) -> None:
    """Publish a complete file atomically, refusing to replace existing outputs."""
    if not staged.is_file():
        raise FileNotFoundError(f"Staged output is missing: {staged.name}")
    os.link(staged, destination)  # Atomic no-clobber on the workspace filesystem.
    staged.unlink()

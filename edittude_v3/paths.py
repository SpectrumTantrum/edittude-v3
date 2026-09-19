from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def install_root() -> Path:
    """Checkout that holds skills/ and tools/."""
    override = os.environ.get("EDITTUDE_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return PACKAGE_ROOT


def config_home() -> Path:
    """User data directory. The API key lives here, not in the project folder."""
    override = os.environ.get("EDITTUDE_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".local/share" / "edittude-v3"


def env_file() -> Path:
    return config_home() / ".env"


def unique_existing_dirs(*candidates: Path) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        path = candidate.expanduser().resolve()
        if not path.is_dir() or path in seen:
            continue
        found.append(path)
        seen.add(path)
    return found

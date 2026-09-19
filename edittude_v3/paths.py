from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def install_root() -> Path:
    """Checkout that holds skills/, tools/, and the install .env."""
    override = os.environ.get("EDITTUDE_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return PACKAGE_ROOT


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

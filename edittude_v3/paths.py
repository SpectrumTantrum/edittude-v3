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
    """User config directory. The API key lives here, not in the project folder."""
    override = os.environ.get("EDITTUDE_CONFIG_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".config" / "edittude-v3"


def env_file() -> Path:
    return config_home() / ".env"


def state_dir(workspace: Path) -> Path:
    """Per-project scratch: TUI history, offloaded conversation history, media."""
    path = workspace.expanduser().resolve() / ".edittude-v3"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SystemExit(f"cannot create {path}: {exc.strerror}") from None
    return path


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

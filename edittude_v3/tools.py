from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType

from langchain_core.tools import BaseTool, tool

from edittude_v3.paths import install_root

# One loaded package per tools/__init__.py; the TUI loads tools twice at startup.
_PACKAGES: dict[Path, ModuleType] = {}


def load_workspace_tools(workspace: Path) -> list[BaseTool]:
    """Load a portable tools package without changing cwd or importing other projects."""
    workspace = workspace.expanduser().resolve()
    entrypoint = None
    for root in (workspace, install_root()):
        candidate = root / "tools" / "__init__.py"
        if candidate.is_file():
            entrypoint = candidate.resolve()
            break
    if entrypoint is None:
        return []

    # A separate package name keeps relative imports isolated across workspaces.
    package = _PACKAGES.get(entrypoint)
    name = package.__name__ if package is not None else f"_edittude_tools_{uuid.uuid4().hex}"
    try:
        if package is None:
            spec = importlib.util.spec_from_file_location(
                name, entrypoint, submodule_search_locations=[str(entrypoint.parent)]
            )
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot load tools from {entrypoint}")
            package = importlib.util.module_from_spec(spec)
            sys.modules[name] = package
            spec.loader.exec_module(package)
        registered = package.get_tools(workspace)
        if not isinstance(registered, list):
            raise TypeError("get_tools(workspace) must return a list of callables or tools")
        result = [item if isinstance(item, BaseTool) else tool(item) for item in registered]
        names = [item.name for item in result]
        if len(names) != len(set(names)):
            raise ValueError("get_tools(workspace) returned duplicate tool names")
        _PACKAGES[entrypoint] = package
        return result
    except Exception:
        _PACKAGES.pop(entrypoint, None)
        for module_name in list(sys.modules):
            if module_name == name or module_name.startswith(name + "."):
                del sys.modules[module_name]
        raise


def list_tool_names(workspace: Path) -> list[str]:
    return [item.name for item in load_workspace_tools(workspace)]

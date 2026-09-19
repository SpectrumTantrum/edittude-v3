from __future__ import annotations

import functools
import importlib.util
import json
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import get_args

from deepagents.middleware.filesystem import FsToolName
from langchain_core.tools import BaseTool, ToolException, tool

from edittude_v3.paths import install_root

# One loaded package per tools/__init__.py; the TUI loads tools twice at startup.
_PACKAGES: dict[Path, ModuleType] = {}
# deepagents' own tools: the filesystem/shell set plus the subagent `task` tool,
# which has no exported name constant. A workspace tool reusing one of these
# replaces it silently, so reject the collision instead.
_BUILTIN_NAMES = frozenset(get_args(FsToolName)) | {"task"}


def _as_tool(item):
    """Turn a portable tool's {"status": "error"} dict into ToolMessage(status="error").

    tools/ stays harness-free and keeps returning dicts. The ToolException carries the
    exact JSON langchain would have stringified anyway, so the model reads the same text.
    """
    if isinstance(item, BaseTool):
        return item

    @functools.wraps(item)
    def wrapper(*args, **kwargs):
        result = item(*args, **kwargs)
        if isinstance(result, dict) and result.get("status") in {"error", "unavailable"}:
            raise ToolException(json.dumps(result, ensure_ascii=False))
        return result

    built = tool(wrapper)
    built.handle_tool_error = True
    return built


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
        result = [_as_tool(item) for item in registered]
        names = [item.name for item in result]
        if len(names) != len(set(names)):
            raise ValueError("get_tools(workspace) returned duplicate tool names")
        if shadowed := sorted(_BUILTIN_NAMES.intersection(names)):
            raise ValueError(
                f"get_tools(workspace) shadows built-in tools: {', '.join(shadowed)}"
            )
        _PACKAGES[entrypoint] = package
        return result
    except Exception as exc:  # noqa: BLE001 - whatever tools/ raises becomes one line
        _PACKAGES.pop(entrypoint, None)
        for module_name in list(sys.modules):
            if module_name == name or module_name.startswith(name + "."):
                del sys.modules[module_name]
        # Every caller (chat, ask, tools) wants one line here, not a traceback.
        raise SystemExit(f"{entrypoint}: {exc}") from None


def list_tool_names(workspace: Path) -> list[str]:
    return [item.name for item in load_workspace_tools(workspace)]

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.memory import InMemorySaver

from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend

from edittude_v3.paths import PACKAGE_ROOT, install_root
from edittude_v3.skills import skill_dirs
from edittude_v3.subagents import video_subagents
from edittude_v3.tools import load_workspace_tools

MODEL = "deepseek:deepseek-flash"
MODEL_LABEL = "DeepSeek Flash"

SYSTEM_PROMPT = """You are edittude-v3, a local cutter.

The user gives you a footage folder and a brief, or just a folder. You make a cut.
You do not wait for an EDL. You do not ask twenty questions. Defaults:

- 16:9, 30fps delivery, hard cuts
- chronology is the spine unless the brief names a different structure
- if a voiceover file exists, picture fits that duration and ambient ducks under it
- warm grade, one opening title if you have a name, about -16 LUFS
- write outputs next to the source under artifacts/ or a path they name
- never copy huge media into the git repo

House style: cut the day, don't illustrate it. One idea per shot. Get in late,
get out early. Alternate wide, person, detail. Cap screen recordings and static
holds. Dissolves are for when you failed to find the cut.

Workflow:
1. Read the matching skill under ./skills/. Start with zero-shot-cut when they
   want an edit from a folder.
2. ls the folder they named. Absolute paths are real.
3. Inventory with `edittude-v3 media`. Do not invent ffmpeg
   graphs when a subcommand exists.
4. Plan an EDL, assemble, finish, QC, recut if QC fails.
5. A cut is not done until the output file exists on disk. Run the media CLI.
   Do not stop at a command list. Then reply with the path, duration, and what
   you chose. Short.

Delegate with the task tool when it helps: inventory, editor, mixer, qc.
The general-purpose subagent is for leftover work, not for hiding from a cut.

You have a local shell. ffmpeg and ffprobe work. virtual_mode is off on purpose.

The registered portable media tools are also available. Call get_capabilities to
check dependencies before using them. Their paths are workspace-relative:
/clips/input.mp4 means clips/input.mp4 in this project. Built-in filesystem and
shell tools use host paths. Resolve returned media paths against the project
directory before opening them with filesystem tools. Use the existing media CLI
for footage outside the workspace. Keep originals intact and verify outputs.
"""


def default_workspace() -> Path:
    return Path.cwd().resolve()


def load_env(workspace: Path | None = None) -> None:
    workspace = workspace or default_workspace()
    load_dotenv(install_root() / ".env")
    load_dotenv(PACKAGE_ROOT / ".env")
    load_dotenv(workspace / ".env")


def require_api_key() -> None:
    if os.getenv("DEEPSEEK_API_KEY"):
        return
    raise SystemExit(
        "DEEPSEEK_API_KEY is missing. Add it to the install .env or the project .env."
    )


def build_agent(*, workspace: Path | None = None, model: BaseChatModel | None = None):
    workspace = (workspace or default_workspace()).expanduser().resolve()
    if model is None:
        require_api_key()
        model = init_chat_model(MODEL)
    backend = LocalShellBackend(
        root_dir=workspace,
        virtual_mode=False,
        inherit_env=True,
        timeout=600,
    )

    memory_files: list[str] = []
    for path in (install_root() / "AGENTS.md", workspace / "AGENTS.md"):
        if not path.is_file():
            continue
        resolved = str(path.resolve())
        if resolved not in memory_files:
            memory_files.append(resolved)
    memory = memory_files or None
    skills = [str(path) for path in skill_dirs(workspace)] or None
    subagents = [{**spec, "skills": skills or []} for spec in video_subagents()]

    return create_deep_agent(
        model=model,
        tools=load_workspace_tools(workspace),
        system_prompt=SYSTEM_PROMPT + f"\nProject directory: {workspace}\n",
        memory=memory,
        skills=skills,
        backend=backend,
        subagents=subagents,
        checkpointer=InMemorySaver(),
        name="edittude-v3",
    )

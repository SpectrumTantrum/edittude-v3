from __future__ import annotations

import argparse
import asyncio
import uuid
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.status import Status

from edittude_v3 import __version__
from edittude_v3.agent import MODEL_LABEL, build_agent, default_workspace, load_env
from edittude_v3.events import iter_turn, preview
from edittude_v3.skills import list_skill_names, list_skills
from edittude_v3.tools import list_tool_names
from edittude_v3.tui import run_tui


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-C",
        "--directory",
        type=Path,
        default=argparse.SUPPRESS,
        help="workspace directory (default: cwd)",
    )
    common.add_argument("--thread", default=argparse.SUPPRESS, help="reuse a thread id")

    parser = argparse.ArgumentParser(
        prog="edittude-v3",
        description="edittude-v3. Local video-editing agent in the current directory.",
        parents=[common],
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"edittude-v3 {__version__}",
    )

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("chat", help="interactive session (default)", parents=[common])

    ask = sub.add_parser("ask", help="run one prompt and exit", parents=[common])
    ask.add_argument("prompt", nargs="+", help="the prompt to send")

    sub.add_parser("skills", help="list skill folders in ./skills", parents=[common])
    sub.add_parser("tools", help="list callable tools in ./tools", parents=[common])

    media = sub.add_parser("media", help="local ffmpeg tools (inventory, cut, mix, qc)")
    media.add_argument(
        "media_args",
        nargs=argparse.REMAINDER,
        help="arguments forwarded to edittude-media",
    )

    return parser


def _workspace(directory: Path | None) -> Path:
    if directory is None:
        return default_workspace()
    return directory.expanduser().resolve()


def cmd_chat(*, workspace: Path, thread: str | None) -> None:
    load_env(workspace)
    run_tui(workspace=workspace, thread=thread)


async def _ask_async(prompt: str, workspace: Path, thread: str) -> None:
    console = Console()
    agent = build_agent(workspace=workspace)
    parts: list[str] = []
    console.print(f"[bold]edittude-v3[/]  {MODEL_LABEL}  {workspace}")

    with Status("thinking", console=console, spinner="dots"):
        async for kind, payload in iter_turn(agent, prompt, thread):
            if kind == "text":
                parts.append(payload)
            elif kind == "tool_start":
                name = payload["name"]
                args = payload.get("args") or {}
                hint = args.get("file_path") or args.get("query") or args.get("command") or ""
                console.print(f"[dim]  {name} {hint}[/]")
            elif kind == "tool_end":
                console.print(f"[dim]  done[/] {preview(payload.get('output'), limit=80)}")

    text = "".join(parts).strip()
    if text:
        console.print()
        console.print(Markdown(text))
        console.print()


def cmd_ask(*, workspace: Path, prompt: str, thread: str | None) -> None:
    load_env(workspace)
    asyncio.run(_ask_async(prompt, workspace, thread or uuid.uuid4().hex))


def cmd_skills(*, workspace: Path) -> None:
    rows = list_skills(workspace)
    if not rows:
        print("No skills yet. Add folders under skills/<name>/SKILL.md")
        return
    print(f"{len(rows)} skill(s):")
    for name, description in rows:
        extra = f"  {description}" if description else ""
        print(f"  {name}{extra}")


def cmd_tools(*, workspace: Path) -> None:
    names = list_tool_names(workspace)
    if not names:
        print("No tools. Add tools/__init__.py exporting get_tools(workspace).")
        return
    print(f"{len(names)} tool(s):")
    for name in names:
        print(f"  {name}")


def main(argv: list[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)
    workspace = _workspace(getattr(args, "directory", None))
    thread = getattr(args, "thread", None)

    if args.command is None or args.command == "chat":
        cmd_chat(workspace=workspace, thread=thread)
        return
    if args.command == "ask":
        cmd_ask(
            workspace=workspace,
            prompt=" ".join(args.prompt),
            thread=thread,
        )
        return
    if args.command == "skills":
        cmd_skills(workspace=workspace)
        return
    if args.command == "tools":
        cmd_tools(workspace=workspace)
        return
    if args.command == "media":
        from edittude_v3.media.cli import main as media_main

        media_args = list(args.media_args)
        if media_args and media_args[0] == "--":
            media_args = media_args[1:]
        if not media_args:
            media_args = ["--help"]
        media_main(media_args)
        return

    parser.print_help()

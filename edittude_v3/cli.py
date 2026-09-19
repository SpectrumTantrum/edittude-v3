from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import subprocess
import sys
import uuid
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.status import Status
from rich.table import Table

from edittude_v3 import __version__
from edittude_v3.agent import (
    API_KEY_URL,
    SETTINGS,
    get_settings,
    model_label,
    model_name,
    set_setting,
    build_agent,
    configured_api_key,
    default_workspace,
    load_env,
    normalize_api_key,
    require_api_key,
    save_api_key,
)
from edittude_v3.events import iter_turn, preview
from edittude_v3.paths import env_file, install_root
from edittude_v3.skills import list_skills
from edittude_v3.tools import list_tool_names
from edittude_v3.tui import ACCENT, BLUE, GREY, ORANGE, RED, TEAL, run_tui

console = Console()


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

    config = sub.add_parser("config", help="show or change models and limits")
    config.add_argument("action", nargs="?", choices=("show", "set", "unset"), default="show")
    config.add_argument("name", nargs="?", choices=tuple(SETTINGS))
    config.add_argument("value", nargs="?")

    media = sub.add_parser("media", help="local ffmpeg tools (inventory, cut, mix, qc)")
    media.add_argument("--force", action="store_true", help="overwrite existing output files")
    media.add_argument(
        "media_args",
        nargs=argparse.REMAINDER,
        help="arguments forwarded to edittude-media",
    )

    update = sub.add_parser("update", help="pull the latest edittude-v3 into this install")
    update.add_argument(
        "--force",
        action="store_true",
        help="overwrite local changes in the install checkout",
    )

    return parser


def _workspace(directory: Path | None) -> Path:
    if directory is None:
        return default_workspace()
    path = directory.expanduser().resolve()
    # Without this, a typo'd -C creates the whole tree via state_dir() and runs there.
    if not path.is_dir():
        raise SystemExit(f"not a directory: {path}")
    return path


def _home(path: Path) -> str:
    return str(path).replace(str(Path.home()), "~", 1)


def ensure_api_key() -> None:
    if configured_api_key() or not model_name().startswith("deepseek:"):
        return
    if not sys.stdin.isatty():
        require_api_key()
    console.print()
    console.print(f"[{ACCENT}]◆[/] [bold]edittude-v3[/] needs a DeepSeek API key.")
    console.print(f"[dim]Get one at {API_KEY_URL}[/]")
    console.print()
    while True:
        try:
            key = getpass.getpass("DeepSeek API key: ")
        except (EOFError, KeyboardInterrupt):
            raise SystemExit(1)
        key = normalize_api_key(key)
        if key:
            break
        console.print("[dim]Paste a key to continue.[/]")
    dest = save_api_key(key)
    console.print(f"[dim]Saved to {escape(_home(dest))}[/]")
    console.print()


def cmd_chat(*, workspace: Path, thread: str | None) -> None:
    load_env()
    ensure_api_key()
    run_tui(workspace=workspace, thread=thread)


async def _ask_async(prompt: str, workspace: Path, thread: str) -> None:
    agent = build_agent(workspace=workspace)
    parts: list[str] = []
    home = escape(_home(workspace))
    console.print(
        f"[bold]edittude-v3[/][dim] │ [/][{TEAL}]{model_label()}[/][dim] │ [/][{ORANGE}]{home}[/]"
    )
    console.print()

    with Status("Thinking…", console=console, spinner="dots", spinner_style=ACCENT):
        async for kind, payload in iter_turn(agent, prompt, thread):
            if kind == "text":
                parts.append(payload)
            elif kind == "tool_start":
                name = escape(payload["name"])
                args = payload.get("args") or {}
                hint = args.get("file_path") or args.get("query") or args.get("command") or ""
                console.print(
                    f"  [{GREY}]◆[/] [bold]{name}[/] [dim]{escape(str(hint))}[/]", highlight=False
                )
            elif kind == "tool_end":
                out = escape(preview(payload.get("output"), limit=80))
                if payload.get("status") == "error":
                    console.print(f"  [{RED}]✗ {out}[/]", highlight=False)
                else:
                    console.print(f"  [{GREY}]┃[/] [dim]{out}[/]", highlight=False)

    text = "".join(parts).strip()
    if text:
        console.print()
        console.print(Markdown(text))
        console.print()


def cmd_ask(*, workspace: Path, prompt: str, thread: str | None) -> None:
    load_env()
    ensure_api_key()
    asyncio.run(_ask_async(prompt, workspace, thread or uuid.uuid4().hex))


def cmd_skills(*, workspace: Path) -> None:
    rows = list_skills(workspace)
    if not rows:
        console.print("[dim]No skills yet. Add folders under skills/<name>/SKILL.md[/]")
        return
    console.print(f"[bold]{len(rows)} skill{'' if len(rows) == 1 else 's'}[/]")
    table = Table(box=None, show_header=False, padding=(0, 2, 0, 2))
    table.add_column(style=f"bold {BLUE}")
    table.add_column(style="dim")
    for name, description in rows:
        table.add_row(escape(name), escape(description or ""))
    console.print(table)


def cmd_tools(*, workspace: Path) -> None:
    names = list_tool_names(workspace)
    if not names:
        console.print("[dim]No tools. Add tools/__init__.py exporting get_tools(workspace).[/]")
        return
    console.print(f"[bold]{len(names)} tool{'' if len(names) == 1 else 's'}[/]")
    table = Table(box=None, show_header=False, padding=(0, 2, 0, 2))
    table.add_column(style="bold")
    for name in names:
        table.add_row(escape(name))
    console.print(table)


def cmd_config(*, action: str, name: str | None, value: str | None) -> None:
    load_env()
    if action != "show":
        if name is None or (action == "set" and value is None):
            raise SystemExit("usage: edittude-v3 config set NAME VALUE | config unset NAME")
        try:
            set_setting(name, value if action == "set" else None)
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
    table = Table(show_header=True, header_style=f"bold {ACCENT}")
    for column in ("setting", "value", "variable"):
        table.add_column(column)
    for setting, current in get_settings().items():
        shown = "set" if current and setting.endswith("key") else current
        table.add_row(setting, escape(shown), SETTINGS[setting][0])
    console.print(table)
    console.print(f"[dim]{escape(_home(env_file()))}[/]")


def cmd_update(*, force: bool = False) -> None:
    root = install_root()
    installer = root / "install.sh"
    if not installer.is_file():
        console.print(
            f"[bold {RED}]error[/] no installer at {escape(str(installer))}. "
            "This copy cannot update itself."
        )
        raise SystemExit(1)
    env = os.environ.copy()
    env["EDITTUDE_UPDATE"] = "1"
    if force:
        env["EDITTUDE_FORCE"] = "1"
    result = subprocess.run(["bash", str(installer), "update"], env=env, check=False)
    if result.returncode:
        raise SystemExit(result.returncode)


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
        if args.force:
            media_args.insert(0, "--force")
        media_main(media_args)
        return
    if args.command == "config":
        cmd_config(action=args.action, name=args.name, value=args.value)
        return
    if args.command == "update":
        cmd_update(force=args.force)
        return

    parser.print_help()

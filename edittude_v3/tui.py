from __future__ import annotations

import time
import uuid
from pathlib import Path

import xli
from rich.box import ROUNDED
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from edittude_v3 import __version__
from edittude_v3.agent import build_agent, model_label
from edittude_v3.events import iter_turn, preview
from edittude_v3.paths import state_dir
from edittude_v3.skills import list_skill_names
from edittude_v3.tools import list_tool_names

#: The accent knob for the whole CLI — magenta marks active/running state.
ACCENT = "#bb9af7"
#: The rest of the palette. Chromatic hues and mid-greys only: body text keeps
#: the terminal's own foreground, so the UI reads on dark and light terminals.
TEAL = "#1abc9c"
ORANGE = "#ff9e64"
BLUE = "#7aa2f7"
YELLOW = "#e0af68"
GREY = "#787878"
MUTED = "#6c6c6c"
RED = "#f7768e"
GREEN = "#9ece6a"
BORDER = "#505058"

#: Transcript grammar: ❯ on prompts, ◆ bullets on tools, ┃ rail on reasoning.
#: Two knobs are named for their xli role, not their colour: the spinner and the
#: busy toolbar use warning_color (hence magenta), finished tool cards use
#: success_color (hence grey) — running cards stay magenta via tool_color.
THEME = xli.CODEX.with_overrides(
    user_label="❯",
    assistant_label="edittude",
    user_color="default",
    assistant_color=ACCENT,
    system_color=BLUE,
    tool_glyph="◆",
    tool_done_glyph="◆",
    tool_error_glyph="✗",
    tool_color=ACCENT,
    reasoning_glyph="┃",
    reasoning_color=f"italic {MUTED}",
    plan_color="#FFDB8D",
    error_color=RED,
    warning_color=ACCENT,
    success_color=GREY,
    muted_color=MUTED,
    diff_add_color=GREEN,
    diff_del_color=RED,
    diff_hunk_color=MUTED,
    prompt_glyph="❯",
    prompt_color=f"bold {ACCENT}",
    command_color=f"bold {YELLOW}",
    code_theme="ansi_dark",
    status_separator=" │ ",
    status_color=MUTED,
)


def fmt_duration(seconds: float) -> str:
    """Elapsed time, Grok-style: 7.1s · 21s · 1m5s · 1h2m."""
    if seconds < 10:
        return f"{seconds:.1f}s"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m{int(seconds % 60)}s"
    return f"{int(seconds // 3600)}h{int(seconds % 3600) // 60}m"


def _history_file(workspace: Path) -> str:
    return str(state_dir(workspace) / "history")


def _banner(workspace: Path, skills: int, tools: int) -> RenderableType:
    head = Text()
    head.append("◆ ", style=ACCENT)
    head.append("edittude-v3", style="bold")
    head.append(f" v{__version__}", style="dim")

    body = Text()
    rows = (
        ("model", model_label(), TEAL),
        ("cwd", str(workspace).replace(str(Path.home()), "~", 1), ORANGE),
        ("", f"{skills} skills · {tools} tools", "dim"),
    )
    for i, (key, value, style) in enumerate(rows):
        if i:
            body.append("\n")
        body.append(f"{key:<6}", style=MUTED)
        body.append(value, style=style)

    tips = Text()
    hints = (("/", "commands"), ("@", "files"), ("esc", "interrupt"), ("ctrl+d", "quit"))
    for i, (key, label) in enumerate(hints):
        if i:
            tips.append("  │  ", style="dim")
        tips.append(key, style="bold")
        tips.append(f":{label}", style="dim")

    panel = Panel.fit(
        Group(head, Text(), body), box=ROUNDED, border_style=BORDER, padding=(1, 2)
    )
    return Group(panel, tips)


def run_tui(*, workspace: Path, thread: str | None = None) -> None:
    agent = build_agent(workspace=workspace)
    thread_id = thread or uuid.uuid4().hex
    skills = list_skill_names(workspace)

    ui = xli.UI(
        title="edittude-v3",
        intro="",  # the banner below replaces the built-in empty-state welcome
        theme=THEME,
        status_fields=("cwd", "model", "thread", "skills"),
        history_file=_history_file(workspace),
        notify_after=20,
    )
    ui.status.set(
        cwd=workspace.name,
        model=model_label(),
        thread=thread_id[:8],
        skills=f"{len(skills)} skills",
    )

    @ui.command("new", description="start a fresh thread")
    async def cmd_new(ui: xli.UI, args: str) -> None:
        nonlocal thread_id
        thread_id = uuid.uuid4().hex
        ui.status.set(thread=thread_id[:8])
        ui.note(f"new thread {thread_id[:8]}")

    @ui.command("skills", description="list project skills")
    async def cmd_skills(ui: xli.UI, args: str) -> None:
        names = list_skill_names(workspace)
        if not names:
            ui.note("No skills yet. Add folders under skills/<name>/SKILL.md")
            return
        ui.note("skills: " + ", ".join(names))

    @ui.command("status", description="show model, thread, and workspace")
    async def cmd_status(ui: xli.UI, args: str) -> None:
        ui.note(
            f"{model_label()} · thread {thread_id[:8]} · {workspace} · {len(list_skill_names(workspace))} skills"
        )

    @ui.on_prompt
    async def handle(prompt: str) -> None:
        cards: dict[str, object] = {}
        stream = None
        reasoning_buf: list[str] = []
        reasoning_started: float | None = None
        turn_started = time.monotonic()
        spinner = None

        def start_spinner() -> None:
            nonlocal spinner
            if spinner is None:
                spinner = ui.working("Thinking…")
                spinner.__enter__()

        def stop_spinner() -> None:
            nonlocal spinner
            if spinner is not None:
                spinner.__exit__(None, None, None)
                spinner = None

        start_spinner()

        def close_stream() -> None:
            nonlocal stream
            if stream is not None:
                stream.__exit__(None, None, None)
                stream = None

        def flush_reasoning() -> None:
            nonlocal reasoning_started
            if not reasoning_buf:
                return
            thought = "".join(reasoning_buf).strip()
            reasoning_buf.clear()
            elapsed = time.monotonic() - reasoning_started if reasoning_started else 0.0
            reasoning_started = None
            if thought:
                # No title param on the reasoning cell, so the header is the first railed line.
                ui.reasoning(f"◆ Thought for {fmt_duration(elapsed)}\n{preview(thought, limit=800)}")

        def write_text(text: str) -> None:
            nonlocal stream
            if stream is None:
                flush_reasoning()
                stream = ui.streaming("assistant")
                stream.__enter__()
            stream.write(text)

        try:
            async for kind, payload in iter_turn(agent, prompt, thread_id):
                if kind == "reasoning":
                    if reasoning_started is None:
                        reasoning_started = time.monotonic()
                    reasoning_buf.append(payload)
                    continue

                stop_spinner()

                if kind == "text":
                    write_text(payload)
                    continue

                if kind == "tool_start":
                    close_stream()
                    flush_reasoning()
                    cards[payload["id"]] = ui.tool(
                        payload["name"],
                        args=payload["args"],
                        status="running",
                    )
                    continue

                if kind == "tool_end":
                    card = cards.get(payload["id"])
                    if card is None:
                        continue
                    card.update(
                        status=payload.get("status", "done"),
                        output=preview(payload.get("output")),
                    )
                    # The next model call can be long; show activity again.
                    if stream is None:
                        start_spinner()
        finally:
            stop_spinner()
            close_stream()
            flush_reasoning()
            ui.note(f"Worked for {fmt_duration(time.monotonic() - turn_started)}")

    # ui.print() before run() has no printer attached, so banner goes out directly.
    Console().print(_banner(workspace, len(skills), len(list_tool_names(workspace))))
    ui.run()

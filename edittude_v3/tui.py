from __future__ import annotations

import uuid
from pathlib import Path

import xli
from rich.box import ROUNDED
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from edittude_v3 import __version__
from edittude_v3.agent import MODEL_LABEL, build_agent
from edittude_v3.events import iter_turn, preview
from edittude_v3.skills import list_skill_names
from edittude_v3.tools import list_tool_names

#: The single accent knob for the whole CLI (warm orange).
ACCENT = "#d7875f"

#: Transcript grammar: muted role labels, accent gutter glyphs, accent composer.
THEME = xli.CODEX.with_overrides(
    user_label="you",
    assistant_label="edittude",
    user_color="grey50",
    assistant_color=ACCENT,
    tool_glyph="⏺",
    tool_done_glyph="⏺",
    tool_color=ACCENT,
    reasoning_color="grey42",
    plan_color=ACCENT,
    prompt_glyph="›",
    prompt_color=f"bold {ACCENT}",
    command_color=f"bold {ACCENT}",
    code_theme="ansi_dark",
)


def _history_file(workspace: Path) -> str:
    path = workspace / ".edittude-v3" / "history"
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def _banner(workspace: Path, skills: int, tools: int) -> RenderableType:
    head = Text()
    head.append("✻ edittude-v3", style=f"bold {ACCENT}")
    head.append(f" v{__version__}", style="dim")

    body = Text(style="dim")
    rows = (
        ("model", MODEL_LABEL),
        ("cwd", str(workspace).replace(str(Path.home()), "~", 1)),
        ("", f"{skills} skills · {tools} tools"),
    )
    for i, (key, value) in enumerate(rows):
        if i:
            body.append("\n")
        body.append(f"{key:<6}")
        body.append(value)

    tips = Text("/help commands · @ mention files · esc interrupt · ctrl-d quit", style="dim")
    panel = Panel.fit(
        Group(head, Text(), body), box=ROUNDED, border_style=ACCENT, padding=(1, 2)
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
        status_fields=("model", "thread", "skills"),
        history_file=_history_file(workspace),
        notify_after=20,
    )
    ui.status.set(
        model=MODEL_LABEL,
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
            f"{MODEL_LABEL} · thread {thread_id[:8]} · {workspace} · {len(list_skill_names(workspace))} skills"
        )

    @ui.on_prompt
    async def handle(prompt: str) -> None:
        cards: dict[str, object] = {}
        stream = None
        reasoning_buf: list[str] = []
        spinner = ui.working("thinking")
        spinner.__enter__()
        spinning = True

        def stop_spinner() -> None:
            nonlocal spinning
            if spinning:
                spinner.__exit__(None, None, None)
                spinning = False

        def close_stream() -> None:
            nonlocal stream
            if stream is not None:
                stream.__exit__(None, None, None)
                stream = None

        def flush_reasoning() -> None:
            if not reasoning_buf:
                return
            thought = "".join(reasoning_buf).strip()
            reasoning_buf.clear()
            if thought:
                ui.reasoning(preview(thought, limit=800))

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
                    card.update(status="done", output=preview(payload.get("output")))
        finally:
            stop_spinner()
            close_stream()
            flush_reasoning()

    # ui.print() before run() has no printer attached, so banner goes out directly.
    Console().print(_banner(workspace, len(skills), len(list_tool_names(workspace))))
    ui.run()

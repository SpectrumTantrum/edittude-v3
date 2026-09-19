from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import BaseMessage
from langgraph.types import Command

from edittude_v3.agent import DEFAULT_RECURSION_LIMIT


def content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        return "".join(parts)
    return ""


def reasoning_text(chunk: object) -> str:
    extra = getattr(chunk, "additional_kwargs", None) or {}
    for key in ("reasoning_content", "reasoning"):
        value = extra.get(key)
        if isinstance(value, str) and value:
            return value
    content = getattr(chunk, "content", None)
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") in {"reasoning", "thinking"}:
                # "reasoning" is langchain-core's standard ReasoningContentBlock key.
                parts.append(str(block.get("text") or block.get("thinking")
                                 or block.get("reasoning") or ""))
        return "".join(parts)
    return ""


def as_args(raw: object) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"input": raw}
        if isinstance(parsed, dict):
            return parsed
        return {"input": parsed}
    return {"input": raw}


def preview(value: object, limit: int = 400) -> str:
    if isinstance(value, Command) and isinstance(value.update, dict):
        messages = value.update.get("messages")
        if messages:
            value = messages[-1]
    if isinstance(value, BaseMessage):
        value = content_text(value.content)
    text = value if isinstance(value, str) else str(value)
    text = text.strip()
    if len(text) > limit:
        return text[:limit] + "…"
    return text


async def iter_turn(
    agent,
    prompt: str,
    thread_id: str,
) -> AsyncIterator[tuple[str, Any]]:
    try:
        recursion_limit = int(os.environ.get("EDITTUDE_RECURSION_LIMIT", DEFAULT_RECURSION_LIMIT))
    except ValueError:
        recursion_limit = DEFAULT_RECURSION_LIMIT
    if recursion_limit <= 0:
        recursion_limit = DEFAULT_RECURSION_LIMIT
    async for event in agent.astream_events(
        {"messages": [{"role": "user", "content": prompt}]},
        config={"configurable": {"thread_id": thread_id}, "recursion_limit": recursion_limit},
        version="v2",
    ):
        kind = event.get("event")
        data = event.get("data") or {}
        run_id = str(event.get("run_id") or "")

        if kind == "on_chat_model_stream":
            # Subagent model calls stream through here too. The parent's checkpoint
            # namespace is a single "model:…" segment; a nested run carries the
            # subagent's node as well ("tools:…|model:…"), and its text is the
            # subagent's report, not the answer.
            if "|" in str((event.get("metadata") or {}).get("langgraph_checkpoint_ns") or ""):
                continue
            chunk = data.get("chunk")
            thought = reasoning_text(chunk)
            if thought:
                yield ("reasoning", thought)
            text = content_text(getattr(chunk, "content", None))
            if text:
                yield ("text", text)
            continue

        if kind == "on_tool_start":
            yield (
                "tool_start",
                {
                    "id": run_id,
                    "name": event.get("name") or "tool",
                    "args": as_args(data.get("input")),
                },
            )
            continue

        if kind == "on_tool_end":
            output = data.get("output")
            yield (
                "tool_end",
                {
                    "id": run_id,
                    "output": output,
                    "status": "error" if getattr(output, "status", None) == "error" else "done",
                },
            )
            continue

        # A tool that raises never reaches on_tool_end, so close the card here.
        if kind == "on_tool_error":
            yield (
                "tool_end",
                {
                    "id": run_id,
                    "output": data.get("error"),
                    "status": "error",
                },
            )

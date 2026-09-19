from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any


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
                parts.append(str(block.get("text") or block.get("thinking") or ""))
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
    async for event in agent.astream_events(
        {"messages": [{"role": "user", "content": prompt}]},
        config={"configurable": {"thread_id": thread_id}, "recursion_limit": 80},
        version="v2",
    ):
        kind = event.get("event")
        data = event.get("data") or {}
        run_id = str(event.get("run_id") or "")

        if kind == "on_chat_model_stream":
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
            yield (
                "tool_end",
                {
                    "id": run_id,
                    "output": data.get("output"),
                },
            )

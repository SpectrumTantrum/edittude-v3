"""Make tool-result images reach DeepSeek as real image input.

`ChatDeepSeek._get_request_payload` JSON-stringifies any tool message whose
content is a list, so the image block deepagents' `read_file` returns arrives as
a wall of base64 *text*: the model never sees pixels and a few frames eat the
context window. DeepSeek only accepts images in user messages anyway ("Images
are supported in user messages only. Images in system or assistant messages
return a 400 error." - https://api-docs.deepseek.com/guides/vision), so hoist
the blocks into a HumanMessage right after the tool batch - the same shape
deepagents already uses for sampled video frames.

Applied to the outgoing request only; stored state keeps the original messages
so history offload and summarization still see them.
"""

from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AnyMessage, HumanMessage, ToolMessage

DEEPSEEK_PROFILE = {"max_input_tokens": 1_000_000, "image_inputs": True}
"""Partial profile for deepseek-flash; `langchain_deepseek` ships none for it.

Without `max_input_tokens` deepagents' summarization budget is dead and falls
back to `keep=("messages", 6)`. Missing profile fields default to supported, so
do not add `image_tool_message: False` - that makes deepagents replace the image
with a text placeholder before this middleware can move it.
"""


def hoist_tool_images(messages: list[AnyMessage]) -> list[AnyMessage]:
    """Return `messages` with image blocks moved from tool results into user messages."""
    out: list[AnyMessage] = []
    pending: list[AnyMessage] = []
    for message in messages:
        if pending and not isinstance(message, ToolMessage):
            out.extend(pending)
            pending = []
        if isinstance(message, ToolMessage):
            blocks = message.content_blocks
            images = [block for block in blocks if block.get("type") == "image"]
            if images:
                path = message.additional_kwargs.get("read_file_path", "the file")
                text = "\n".join(block["text"] for block in blocks if block.get("type") == "text")
                note = f"Read image {path}. It is attached in the following message."
                message = message.model_copy(update={"content": f"{text}\n{note}" if text else note})
                pending.append(
                    HumanMessage(
                        content_blocks=[{"type": "text", "text": f"Image read from {path}:"}, *images],
                    )
                )
        out.append(message)
    out.extend(pending)
    return out


class DeepSeekImageMiddleware(AgentMiddleware):
    """Move tool-result images into a following user message before each model call."""

    name = "DeepSeekImageMiddleware"

    def wrap_model_call(self, request, handler):
        return handler(request.override(messages=hoist_tool_images(request.messages)))

    async def awrap_model_call(self, request, handler):
        return await handler(request.override(messages=hoist_tool_images(request.messages)))

"""Run with python -m unittest discover -s tests -p test_vision.py."""
from __future__ import annotations

import base64
import re
import tempfile
import unittest
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_deepseek import ChatDeepSeek
from pydantic import Field

from edittude_v3.agent import build_agent
from edittude_v3.deepseek_vision import DEEPSEEK_PROFILE, hoist_tool_images

# Long enough that a stringified block is unmistakable in the payload.
IMAGE_B64 = base64.b64encode(b"\xff\xd8\xff" + b"jpegbytes" * 30_000).decode()
BASE64_RUN = re.compile(r"[A-Za-z0-9+/]{1000,}")


def read_file_result(path: str) -> list:
    """The messages deepagents' read_file produces for an image, plus its caller."""
    return [
        HumanMessage("describe the frame"),
        AIMessage("", tool_calls=[{"name": "read_file", "args": {"file_path": path}, "id": "call_1"}]),
        ToolMessage(
            content_blocks=[{"type": "image", "base64": IMAGE_B64, "mime_type": "image/jpeg"}],
            name="read_file",
            tool_call_id="call_1",
            additional_kwargs={"read_file_path": path},
        ),
    ]


def text_parts(message: dict) -> list[str]:
    content = message.get("content")
    if not isinstance(content, list):
        return [content or ""]
    return [block.get("text", "") for block in content if block.get("type") == "text"]


class ToolImagePayloadTest(unittest.TestCase):
    def setUp(self):
        self.model = ChatDeepSeek(model="deepseek-flash", api_key="test-key", profile=DEEPSEEK_PROFILE)
        self.messages = read_file_result("/footage/00.jpg")

    def payload_messages(self, messages) -> list[dict]:
        return self.model._get_request_payload(messages)["messages"]

    def test_unfixed_payload_stringifies_the_image(self):
        """Mutation check: if hoist_tool_images stops working, the assertions below fail."""
        tool_message = self.payload_messages(self.messages)[-1]
        self.assertEqual(tool_message["role"], "tool")
        self.assertTrue(BASE64_RUN.search("".join(text_parts(tool_message))))

    def test_image_moves_to_a_user_message(self):
        payload = self.payload_messages(hoist_tool_images(self.messages))
        tool_message, user_message = payload[-2], payload[-1]

        self.assertEqual(tool_message["role"], "tool")
        self.assertEqual(tool_message["tool_call_id"], "call_1")
        self.assertIn("/footage/00.jpg", tool_message["content"])
        self.assertLess(len(tool_message["content"]), 200)

        self.assertEqual(user_message["role"], "user")
        images = [block for block in user_message["content"] if block["type"] == "image_url"]
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["image_url"]["url"], f"data:image/jpeg;base64,{IMAGE_B64}")

    def test_no_message_carries_base64_as_text(self):
        for message in self.payload_messages(hoist_tool_images(self.messages)):
            for part in text_parts(message):
                self.assertIsNone(BASE64_RUN.search(part), f"base64 leaked into {message['role']} text")

    def test_tool_batch_stays_intact(self):
        """Every tool result for a tool-call batch must precede the attached image."""
        messages = [
            *self.messages,
            ToolMessage(content="ok", name="ls", tool_call_id="call_2"),
            AIMessage("done"),
        ]
        roles = [message["role"] for message in self.payload_messages(hoist_tool_images(messages))]
        self.assertEqual(roles, ["user", "assistant", "tool", "tool", "user", "assistant"])

    def test_messages_without_images_are_untouched(self):
        plain = [HumanMessage("hi"), ToolMessage(content="ok", name="ls", tool_call_id="call_1")]
        self.assertEqual(hoist_tool_images(plain), plain)


class ReadThenStopModel(BaseChatModel):
    """Reads one image, then records the messages the next model call receives."""

    image_path: str
    seen: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "offline-vision-test"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if any(isinstance(message, ToolMessage) for message in messages):
            self.seen.append(list(messages))
            reply = AIMessage("done")
        else:
            reply = AIMessage("", tool_calls=[{"name": "read_file", "args": {"file_path": self.image_path}, "id": "call_1"}])
        return ChatResult(generations=[ChatGeneration(message=reply)])


class MiddlewareWiredTest(unittest.TestCase):
    """The hoist is useless unless build_agent actually puts it in the stack."""

    def test_agent_hoists_a_read_image_into_a_user_message(self):
        with tempfile.TemporaryDirectory() as workspace:
            image = Path(workspace) / "frame.jpg"
            image.write_bytes(base64.b64decode(IMAGE_B64))
            model = ReadThenStopModel(image_path=str(image))
            agent = build_agent(workspace=Path(workspace), model=model)
            agent.invoke(
                {"messages": [HumanMessage("read the frame")]},
                {"configurable": {"thread_id": "vision-test"}, "recursion_limit": 10},
            )

        self.assertTrue(model.seen, "model was never called with a tool result")
        messages = model.seen[-1]
        tool_message = next(message for message in messages if isinstance(message, ToolMessage))
        self.assertIsInstance(tool_message.content, str)
        self.assertIn("attached in the following message", tool_message.content)

        attached = messages[messages.index(tool_message) + 1]
        self.assertIsInstance(attached, HumanMessage)
        self.assertEqual([block["type"] for block in attached.content_blocks], ["text", "image"])


if __name__ == "__main__":
    unittest.main()

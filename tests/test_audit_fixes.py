"""Regression checks for the five repository audit fixes."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from edittude_v3.agent import set_setting
from edittude_v3.cli import main
from edittude_v3.media.qc import _parse_loudness
from edittude_v3.media.render import scale_filter
from tools.install_models import BACKENDS, _wanted


class AuditFixesTest(unittest.TestCase):
    def test_loudness_uses_summary(self):
        log = """t: 0.1 I: -70.0 LUFS LRA: 0.0 LU
Summary:
  Integrated loudness:
    I: -16.0 LUFS
    Threshold: -26.0 LUFS
  Loudness range:
    LRA: 4.0 LU
  True peak:
    Peak: -1.5 dBFS
"""
        self.assertEqual(_parse_loudness(log), {
            "I": -16.0, "LRA": 4.0, "TP": -1.5, "thresh": -26.0, "summary": True,
        })

    def test_cli_refuses_existing_output(self):
        commands = (
            ["assemble", "missing.json"],
            ["mix", "missing.mp4"],
            ["grade", "missing.mp4"],
            ["titles", "missing.mp4", "--title", "Title"],
            ["reframe", "missing.mp4", "--aspect", "1:1"],
            ["finish", "missing.mp4", "missing.json"],
            ["recut", "missing.json"],
            ["captions", "missing.mp4", "--srt", "missing.srt"],
        )
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "existing.mp4"
            out.write_text("keep", encoding="utf-8")
            for command in commands:
                with self.subTest(command=command[0]):
                    with self.assertRaises(SystemExit) as raised:
                        main(["media", *command, "--out", str(out)])
                    self.assertEqual(str(raised.exception), f"{out} exists; pass --force to overwrite")
            with patch("edittude_v3.media.cli.grade") as grade:
                main(["media", "--force", "grade", "missing.mp4", "--out", str(out)])
            grade.assert_called_once()
            self.assertEqual(out.read_text(encoding="utf-8"), "keep")

    def test_scale_filter_optional_fps(self):
        self.assertNotIn("fps=", scale_filter(1920, 1080, fps=None))
        self.assertIn("fps=30", scale_filter(1920, 1080))

    def test_asr_ignores_partial_download(self):
        self.assertEqual(_wanted(
            [".cache/huggingface/download/x.incomplete"], BACKENDS["asr"]["hf"][0].get("files"),
        ), [])

    def test_recursion_limit_requires_positive_integer(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env"
            with patch("edittude_v3.agent.env_file", return_value=path), patch.dict(os.environ):
                for value in ("abc", "0"):
                    with self.subTest(value=value), self.assertRaisesRegex(ValueError, "positive integer"):
                        set_setting("recursion-limit", value)
                self.assertFalse(path.exists())
                set_setting("recursion-limit", "200")
                self.assertEqual(os.environ["EDITTUDE_RECURSION_LIMIT"], "200")
                self.assertIn("200", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

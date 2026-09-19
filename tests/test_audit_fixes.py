"""Regression checks for the five repository audit fixes."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import get_type_hints
from unittest.mock import patch

from edittude_v3.agent import save_api_key, set_setting
from edittude_v3.cli import main
from edittude_v3.media.cli import main as media_main
from edittude_v3.media.core import ffmpeg, ffprobe
from edittude_v3.media.qc import _parse_loudness
from edittude_v3.media.render import scale_filter
from tools import get_tools
from tools.install_models import BACKENDS, _download, _wanted


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

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "FFmpeg/ffprobe unavailable")
    def test_assemble_with_apostrophe_in_work_path(self):
        with tempfile.TemporaryDirectory(prefix="edittude's-cut-") as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            ffmpeg(["-v", "error", "-f", "lavfi", "-i",
                    "testsrc=size=320x180:rate=30:duration=2", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=48000:duration=2", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)])
            edl = root / "edl.json"
            edl.write_text(json.dumps({"events": [{"src": str(source), "in": 0, "out": 2}]}),
                           encoding="utf-8")
            out = root / "cut.mp4"
            media_main(["assemble", str(edl), "--out", str(out), "--work", str(root / "work")])
            probe = ffprobe(out)
            self.assertAlmostEqual(float(probe["format"]["duration"]), 2, delta=.1)
            self.assertEqual({stream["codec_type"] for stream in probe["streams"]}, {"video", "audio"})
            ffmpeg(["-v", "error", "-xerror", "-i", str(out), "-f", "null", "-"])

    def test_media_cli_rejects_malformed_edl(self):
        cases = (
            ('{"events": [{"in": 0, "out": 1}]}', "missing src"),
            ('{"events": [{"src": "clip.mp4", "out": 1}]}', "missing in"),
            ('{"events": [{"src": "clip.mp4", "in": 0}]}', "missing out"),
            ("[]", "object"),
            ('{"events": {}}', "list"),
            ('{"events": null}', "list"),
            ('{"events": [null]}', "object"),
            ('{"events": [{"src": "clip.mp4", "in": 2, "out": 1}]}', "in must be less than out"),
            ('{"events": [{"src": "clip.mp4", "in": 1, "out": 1}]}', "in must be less than out"),
            ("not JSON", "not valid JSON"),
            (None, "No such file"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, (content, message) in enumerate(cases):
                with self.subTest(content=content):
                    edl = root / f"edl-{index}.json"
                    if content is not None:
                        edl.write_text(content, encoding="utf-8")
                    with self.assertRaises(SystemExit) as raised:
                        media_main(["assemble", str(edl), "--out", str(root / "cut.mp4")])
                    self.assertIn(message, str(raised.exception))
                    self.assertEqual(len(str(raised.exception).splitlines()), 1)

    def test_save_api_key_removes_duplicate_lines(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env"
            path.write_text("# keep\nexport DEEPSEEK_API_KEY=old1\nOTHER=1\nDEEPSEEK_API_KEY=old2\n",
                            encoding="utf-8")
            with patch("edittude_v3.agent.env_file", return_value=path), patch.dict(os.environ):
                save_api_key("sk-new")
            self.assertEqual(path.read_text(encoding="utf-8"),
                             "# keep\nDEEPSEEK_API_KEY=sk-new\nOTHER=1\n")

    def test_download_failure_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "model.bin"
            staged = root / "model.bin.part"
            staged.write_bytes(b"partial")
            with patch("tools.install_models.V2_MODELS", root / "empty"), patch(
                "tools.install_models.urllib.request.urlopen", side_effect=OSError("offline"),
            ) as urlopen, self.assertRaisesRegex(OSError, "offline"):
                _download(destination, "https://example.invalid/model.bin", "abc")
            urlopen.assert_called_once_with("https://example.invalid/model.bin", timeout=60)
            self.assertFalse(staged.exists())
            self.assertFalse(destination.exists())

    def test_audio_separate_annotation_allows_none(self):
        wrapper = next(tool for tool in get_tools(Path.cwd()) if tool.__name__ == "audio_separate")
        self.assertEqual(get_type_hints(wrapper)["stem"], str | None)


if __name__ == "__main__":
    unittest.main()

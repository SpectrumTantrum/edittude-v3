"""Regression checks for the EDL canvas, aspect, fps, and overwrite-guard fixes."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from edittude_v3.media.cli import main as media_main
from edittude_v3.media.core import MediaError
from edittude_v3.media.edl import edl_from_dict, tighten


class EdlGeometryFixesTest(unittest.TestCase):
    def test_recut_pins_source_canvas_before_shortening_holds(self):
        edl = edl_from_dict({"aspect": "source", "events": [
            {"src": "portrait.mov", "in": 0, "out": 3.5},
            {"src": "landscape.mov", "in": 0, "out": 3.4},
        ]})
        sizes = {"portrait.mov": (360, 640), "landscape.mov": (640, 360)}
        with patch("edittude_v3.media.inventory.describe_clip", side_effect=lambda path: {
            "has_video": True, "display_width": sizes[path.name][0],
            "display_height": sizes[path.name][1],
        }):
            saved = tighten(edl).to_dict()
        self.assertLess(saved["events"][0]["out"], 3.5)
        self.assertEqual((saved["width"], saved["height"]), (360, 640))

    def test_unknown_aspect_is_rejected(self):
        with self.assertRaises(MediaError) as raised:
            edl_from_dict({"aspect": "4:5"})
        self.assertEqual(str(raised.exception), "unknown aspect 4:5. use source or 16:9, 9:16, 1:1")
        with self.assertRaises(SystemExit):  # argparse choices reject it on the CLI too
            media_main(["plan", "inventory.json", "--out", "edl.json", "--aspect", "4:5"])

    def test_odd_source_canvas_rounds_down_to_even(self):
        edl = edl_from_dict({"aspect": "source", "width": 641, "height": 361})
        self.assertEqual((edl.width, edl.height), (640, 360))
        self.assertEqual((edl.to_dict()["width"], edl.to_dict()["height"]), (640, 360))

    def test_plan_and_proof_refuse_existing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            edl = root / "edl.json"
            edl.write_text("hand edited", encoding="utf-8")
            final = root / "final.mp4"
            cases = (["plan", "inventory.json", "--out", str(edl)], ["proof", str(root), "--out", str(root)])
            for command in cases:
                with self.subTest(command=command[0]):
                    with self.assertRaises(SystemExit) as raised:
                        media_main(command)
                    self.assertEqual(str(raised.exception), f"{edl} exists; pass --force to overwrite")
            final.write_text("deliverable", encoding="utf-8")
            edl.unlink()
            with self.assertRaises(SystemExit) as raised:
                media_main(["proof", str(root), "--out", str(root)])
            self.assertEqual(str(raised.exception), f"{final} exists; pass --force to overwrite")
            self.assertEqual(final.read_text(encoding="utf-8"), "deliverable")

    def test_plan_rejects_non_positive_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "edl.json"
            for target in ("0", "-4"):
                with self.subTest(target=target), self.assertRaises(SystemExit) as raised:
                    media_main(["plan", "inventory.json", "--out", str(out), "--target", target])
                self.assertEqual(str(raised.exception), "--target must be greater than 0 seconds")

    def test_negative_fps_is_rejected_and_zero_still_means_thirty(self):
        with self.assertRaises(MediaError) as raised:
            edl_from_dict({"fps": -30})
        self.assertIn("fps must be between 1 and 240", str(raised.exception))
        self.assertEqual(edl_from_dict({"fps": 0}).fps, 30)
        self.assertEqual(edl_from_dict({}).fps, 30)


if __name__ == "__main__":
    unittest.main()

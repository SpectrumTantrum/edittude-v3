"""Source canvas, display rotation, and deliberate crop regression checks."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from edittude_v3.media.cli import _parser
from edittude_v3.media.core import MediaError, ffmpeg
from edittude_v3.media.edl import EditDecision, Event, edl_from_dict, first_cut, source_canvas, tighten
from edittude_v3.media.inventory import describe_clip
from edittude_v3.media.render import assemble, reframe, scale_filter, titles


class RenderGeometryTests(unittest.TestCase):
    def test_scale_filter_defaults_to_pad(self):
        self.assertEqual(
            scale_filter(120, 200),
            "scale=120:200:force_original_aspect_ratio=decrease,"
            "pad=120:200:(ow-iw)/2:(oh-ih)/2:color=black,"
            "fps=30,setsar=1,setpts=PTS-STARTPTS,format=yuv420p",
        )

    def test_scale_filter_explicit_crop(self):
        self.assertEqual(
            scale_filter(200, 120, fit="crop"),
            "scale=200:120:force_original_aspect_ratio=increase,"
            "crop=200:120,fps=30,setsar=1,setpts=PTS-STARTPTS,format=yuv420p",
        )

    def test_zoom_precedes_fit_and_escapes_focus_clamps(self):
        for fit in ("pad", "crop"):
            with self.subTest(fit=fit):
                vf = scale_filter(120, 200, fit=fit, zoom=2, cx=.25, cy=.75)
                self.assertTrue(vf.startswith(
                    "crop=iw/2:ih/2:"
                    r"min(max(iw*0.25-iw/2/2\,0)\,iw-iw/2):"
                    r"min(max(ih*0.75-ih/2/2\,0)\,ih-ih/2),"
                ), vf)
                self.assertTrue(vf.endswith(scale_filter(120, 200, fit=fit)))
                self.assertNotIn("transpose", vf)

    def test_source_edl_round_trip_preserves_canvas_and_shot_geometry(self):
        data = {
            "aspect": "source", "width": 120, "height": 200, "fit": "crop",
            "events": [{"src": "portrait.mov", "in": 1, "out": 3,
                        "zoom": 2.5, "cx": .2, "cy": .8}],
        }
        with patch("edittude_v3.media.inventory.describe_clip") as probe:
            edl = edl_from_dict(data)
            serialized = json.loads(json.dumps(edl.to_dict()))
            restored = edl_from_dict(serialized)
            self.assertEqual((restored.width, restored.height, restored.fit), (120, 200, "crop"))
            self.assertEqual(serialized["width"], 120)
            self.assertEqual(serialized["height"], 200)
            self.assertEqual(restored.to_dict(), serialized)
            self.assertEqual((restored.events[0].zoom, restored.events[0].cx, restored.events[0].cy),
                             (2.5, .2, .8))
            probe.assert_not_called()

    def test_preset_canvas_wins_over_edl_dimensions(self):
        edl = edl_from_dict({"aspect": "9:16", "width": 120, "height": 200})
        self.assertEqual((edl.width, edl.height), (1080, 1920))

    def test_defaults_and_lenient_event_geometry(self):
        for edl in (EditDecision(), edl_from_dict({}), first_cut({"clips": []})):
            self.assertEqual((edl.aspect, edl.fit), ("source", "pad"))
        event = Event("clip.mov", 0, 2)
        self.assertFalse({"zoom", "cx", "cy"} & event.to_dict().keys())
        cases = [
            ({"zoom": "8", "cx": "-.2", "cy": "1.2"}, (4, 0, 1)),
            ({"zoom": "0", "cx": "0", "cy": "0"}, (1, 0, 0)),
            ({"zoom": None, "cx": "bad", "cy": None}, (1, .5, .5)),
        ]
        for geometry, expected in cases:
            with self.subTest(geometry=geometry):
                parsed = Event.from_dict({"src": "clip.mov", "in": 0, "out": 2, **geometry})
                self.assertEqual((parsed.zoom, parsed.cx, parsed.cy), expected)

    def test_source_canvas_uses_total_event_duration_and_even_display_size(self):
        events = [Event("wide.mov", 0, 4), Event("portrait.mov", 0, 2),
                  Event("portrait.mov", 4, 7)]
        clips = {
            "wide.mov": {"has_video": True, "display_width": 1920, "display_height": 1080},
            "portrait.mov": {"has_video": True, "width": 1921, "height": 1081,
                             "display_width": 1081, "display_height": 1921},
        }
        with patch("edittude_v3.media.inventory.describe_clip",
                   side_effect=lambda path: clips[path.name]) as probe:
            self.assertEqual(source_canvas(events), (1080, 1920))
            self.assertEqual(sum(call.args[0].name == "portrait.mov" for call in probe.call_args_list), 1)

    def test_source_canvas_ties_choose_first_source(self):
        events = [Event("portrait.mov", 0, 2), Event("wide.mov", 0, 4),
                  Event("portrait.mov", 2, 4)]
        clips = {"portrait.mov": (120, 200), "wide.mov": (200, 120)}
        with patch("edittude_v3.media.inventory.describe_clip", side_effect=lambda path: {
            "has_video": True, "display_width": clips[path.name][0],
            "display_height": clips[path.name][1],
        }):
            self.assertEqual(source_canvas(events), (120, 200))

    def test_source_canvas_skips_unprobeable_and_audio_sources_once(self):
        events = [Event("missing.mov", 0, 8), Event("missing.mov", 10, 18),
                  Event("audio.wav", 0, 10), Event("portrait.mov", 0, 2)]

        def probe_clip(path):
            if path.name == "missing.mov":
                raise MediaError("missing source")
            if path.name == "audio.wav":
                return {"has_video": False, "display_width": None, "display_height": None}
            return {"has_video": True, "display_width": 120, "display_height": 200}

        with patch("edittude_v3.media.inventory.describe_clip", side_effect=probe_clip) as probe:
            self.assertEqual(source_canvas(events), (120, 200))
            self.assertEqual(sum(call.args[0].name == "missing.mov" for call in probe.call_args_list), 1)
        with patch("edittude_v3.media.inventory.describe_clip", side_effect=MediaError("no video")):
            self.assertEqual(source_canvas(events), (1920, 1080))
        self.assertEqual(source_canvas([]), (1920, 1080))

    def test_source_edl_resolves_both_dimensions_without_reprobing(self):
        with patch("edittude_v3.media.inventory.describe_clip", return_value={
            "has_video": True, "display_width": 120, "display_height": 200,
        }) as probe:
            edl = EditDecision(events=[Event("portrait.mov", 0, 1)])
            self.assertEqual((edl.width, edl.height), (120, 200))
            self.assertEqual((edl.to_dict()["width"], edl.to_dict()["height"]), (120, 200))
            probe.assert_called_once()

    def test_display_dimensions_handle_both_rotation_signs(self):
        for rotation in (0, 90, -90, 180, 270):
            for metadata in ({"side_data_list": [{"rotation": rotation}]},
                             {"tags": {"rotate": str(rotation)}}):
                with self.subTest(rotation=rotation, metadata=metadata):
                    probe = {"format": {"size": "1", "duration": "1"}, "streams": [
                        {"codec_type": "video", "width": 200, "height": 120, **metadata},
                    ]}
                    with patch("edittude_v3.media.inventory.ffprobe", return_value=probe):
                        clip = describe_clip(Path("phone.mov"))
                    expected = (120, 200) if abs(rotation) % 180 == 90 else (200, 120)
                    self.assertEqual((clip["display_width"], clip["display_height"]), expected)
                    self.assertEqual((clip["width"], clip["height"], clip["rotation"]),
                                     (200, 120, rotation))

    def test_reframe_crops_without_changing_frame_rate(self):
        with patch("edittude_v3.media.render._video_filter") as render:
            reframe(Path("clip.mov"), Path("out.mp4"), "9:16")
            vf = render.call_args.args[2]
        self.assertIn("force_original_aspect_ratio=increase,crop=1080:1920", vf)
        self.assertNotIn("fps=", vf)
        # reframe copies audio untouched, so it must not shift video timestamps.
        self.assertNotIn("setpts", vf)
        self.assertTrue(vf.endswith("setsar=1,format=yuv420p"))

    def test_titles_use_display_dimensions(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "phone.mov"
            video.touch()
            out = Path(folder) / "titled.mp4"
            with patch("edittude_v3.media.render.describe_clip", return_value={
                "width": 200, "height": 120, "display_width": 120, "display_height": 200,
            }), patch("edittude_v3.media.render.write_title_png") as card, \
                    patch("edittude_v3.media.render.ffmpeg"):
                titles(video, out, title="A day out")
            self.assertEqual((card.call_args.kwargs["width"], card.call_args.kwargs["height"]),
                             (120, 200))

    def test_recut_preserves_shot_geometry(self):
        edl = EditDecision(events=[Event("clip.mov", 0, 5, zoom=2, cx=.2, cy=.8)])
        event = tighten(edl).events[0]
        self.assertLess(event.duration(), 5)
        self.assertEqual((event.zoom, event.cx, event.cy), (2, .2, .8))

    def test_cli_planning_defaults_and_fit_options(self):
        parser = _parser()
        for command in ("plan", "proof"):
            with self.subTest(command=command):
                args = parser.parse_args([command, "input", "--out", "output"])
                self.assertEqual((args.aspect, args.fit), ("source", "pad"))
                args = parser.parse_args([command, "input", "--out", "output",
                                          "--aspect", "9:16", "--fit", "crop"])
                self.assertEqual((args.aspect, args.fit), ("9:16", "crop"))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "FFmpeg/ffprobe unavailable")
    def test_default_assembly_preserves_portrait_resolution(self):
        with tempfile.TemporaryDirectory(prefix="edittude-geometry-test-") as folder:
            root = Path(folder)
            source = root / "portrait.mp4"
            ffmpeg(["-f", "lavfi", "-i", "testsrc=size=120x200:rate=30:duration=0.4",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    str(source)], capture=True)
            out = assemble(EditDecision(events=[Event(str(source), 0, .4)]),
                           root / "out.mp4", root / "work")
            clip = describe_clip(out)
            self.assertEqual((clip["width"], clip["height"]), (120, 200))
            self.assertEqual((clip["display_width"], clip["display_height"]), (120, 200))


if __name__ == "__main__":
    unittest.main()

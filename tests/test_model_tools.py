"""Run with python -m unittest discover -s tests -p test_model_tools.py."""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

from tools.common import CapabilityUnavailable
from tools import models


def _score() -> dict:
    return {"language": "zh", "duration_seconds": 2.0, "units": [
        {"id": "rest-01", "kind": "rest", "text": "", "start": 0.0, "end": .5, "notes": []},
        {"id": "word-01", "kind": "lyric", "text": "你", "start": .5, "end": 2.0,
         "notes": [{"pitch": "C4", "start": .5, "end": 1.0}, {"pitch": "D4", "start": 1.0, "end": 2.0}]},
    ]}


class ModelToolsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        with wave.open(str(self.root / "reference.wav"), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(b"".join(struct.pack("<h", round(2000 * math.sin(2 * math.pi * 440 * i / 16000))) for i in range(16000)))
        (self.root / "score.json").write_text(json.dumps(_score()), encoding="utf-8")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe") and
                         (shutil.which("say") or shutil.which("espeak-ng") or shutil.which("espeak")), "local stock TTS and FFmpeg required")
    def test_real_stock_speech_decodes_and_never_overwrites(self):
        result = models.speech_synthesize(self.root, "The train arrives at noon.", "/speech.wav")
        self.assertEqual(result["status"], "ok")
        self.assertGreater(result["duration"], .25)
        self.assertEqual(result["audio_path"], "/speech.wav")
        self.assertTrue(result["verification"]["decoded"])
        self.assertFalse(result["verification"]["listening"])
        output = self.root / "speech.wav"
        before = output.read_bytes()
        with wave.open(str(output), "rb") as audio:
            raw = audio.readframes(audio.getnframes())
            self.assertGreater(max(abs(value) for value in struct.unpack(f"<{len(raw)//2}h", raw)), 0)
        with self.assertRaises(FileExistsError):
            models.speech_synthesize(self.root, "Changed words.", "/speech.wav")
        self.assertEqual(before, output.read_bytes())

    def test_reference_voice_request_fails_without_stock_substitution(self):
        with self.assertRaisesRegex(CapabilityUnavailable, "does not support reference voices"):
            models.speech_synthesize(self.root, "Hello.", "/speech.wav", reference_audio_path="/reference.wav")
        self.assertFalse((self.root / "speech.wav").exists())

    def test_missing_model_configuration_is_explicit(self):
        missing = {key: "" for key in ("EDITTUDE_DEMUCS_REPO", "EDITTUDE_DIFFSINGER_DIR", "EDITTUDE_SEED_VC_DIR")}
        calls = [
            (lambda: models.audio_separate(self.root, "/reference.wav", "/stems"), "EDITTUDE_DEMUCS_REPO"),
            (lambda: models.singing_synthesize(self.root, "/score.json", "/song.wav"), "EDITTUDE_DIFFSINGER_DIR"),
            (lambda: models.voice_convert(self.root, "/reference.wav", "/reference.wav", "/converted.wav"), "EDITTUDE_SEED_VC_DIR"),
        ]
        with patch.dict(os.environ, missing):
            for call, expected in calls:
                with self.subTest(backend=expected), self.assertRaisesRegex(CapabilityUnavailable, expected):
                    call()
        self.assertFalse((self.root / "stems").exists())
        self.assertFalse((self.root / "song.wav").exists())
        self.assertFalse((self.root / "converted.wav").exists())

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_missing_asr_runtime_fails_without_transcript(self):
        with patch.dict(os.environ, {"EDITTUDE_ASR_PYTHON": str(self.root / "missing-python")}):
            with self.assertRaisesRegex(CapabilityUnavailable, "Python runtime unavailable"):
                models.speech_transcribe(self.root, "/reference.wav", "/transcript.json")
        self.assertFalse((self.root / "transcript.json").exists())

    def test_caller_validation_and_workspace_confinement(self):
        calls = [
            lambda: models.speech_synthesize(self.root, "", "/speech.wav"),
            lambda: models.speech_synthesize(self.root, "Hello.", "/speech.wav", rate=True),
            lambda: models.speech_synthesize(self.root, "Hello.", "../escape.wav"),
            lambda: models.speech_transcribe(self.root, "../escape.wav", "/out.json"),
            lambda: models.speech_transcribe(self.root, "/reference.wav", "/out.json", model="../../model"),
            lambda: models.audio_separate(self.root, "/reference.wav", "/stems", model="../unsafe"),
            lambda: models.voice_convert(self.root, "/reference.wav", "/reference.wav", "/out.wav", diffusion_steps=0),
        ]
        for call in calls:
            with self.subTest(call=call), self.assertRaises(ValueError):
                call()

    def test_singing_score_keeps_rests_and_melismas_and_rejects_gaps(self):
        data = _score()
        unchanged = copy.deepcopy(data)
        prepared = models._singing_input(data)
        self.assertEqual(prepared, {"text": "AP 你", "notes": "rest | C4 D4", "notes_duration": "0.5 | 0.5 1.0", "input_type": "word"})
        self.assertEqual(data, unchanged)
        data["units"][1]["notes"][1]["start"] = 1.1
        with self.assertRaisesRegex(ValueError, "sequentially cover"):
            models._singing_input(data)
        data = _score()
        data["language"] = "en"
        with self.assertRaisesRegex(ValueError, "Mandarin"):
            models._singing_input(data)

    def test_model_worker_disables_python_network(self):
        result = models.run([sys.executable, "-c", models._OFFLINE + """
try:
    socket.create_connection(('example.com', 443))
except OSError as error:
    assert 'Network is disabled' in str(error)
    print('blocked')
else:
    raise AssertionError('network was not blocked')
"""])
        self.assertEqual(result.stdout.strip(), "blocked")

    @unittest.skipUnless(os.environ.get("EDITTUDE_TEST_ASR_PYTHON") and shutil.which("ffmpeg") and
                         (shutil.which("say") or shutil.which("espeak-ng") or shutil.which("espeak")), "set EDITTUDE_TEST_ASR_PYTHON to exercise cached ASR")
    def test_real_cached_asr_preserves_source_id_and_words(self):
        models.speech_synthesize(self.root, "The train arrives at noon.", "/speech.wav")
        with patch.dict(os.environ, {"EDITTUDE_ASR_PYTHON": os.environ["EDITTUDE_TEST_ASR_PYTHON"]}):
            result = models.speech_transcribe(self.root, "/speech.wav", "/transcript.json", language="en",
                                              model=os.environ.get("EDITTUDE_TEST_ASR_MODEL", "base"), source_id="scene-01")
        self.assertEqual(result["source_id"], "scene-01")
        self.assertTrue(result["speech_detected"])
        self.assertIn("train", " ".join(row["text"] for row in result["segments"]).lower())
        self.assertTrue(all(row["id"].startswith("scene-01-") for row in result["segments"]))
        self.assertTrue(any(row["words"] for row in result["segments"]))
        self.assertEqual(json.loads((self.root / "transcript.json").read_text())["source_id"], "scene-01")


if __name__ == "__main__":
    unittest.main()

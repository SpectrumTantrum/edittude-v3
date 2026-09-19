"""Run with python -m unittest discover -s tests -p test_model_paths.py."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import models
from tools.common import model_python, models_root

# Every EDITTUDE_* knob these paths read, emptied so only EDITTUDE_ROOT decides.
UNSET = {key: "" for key in (
    "EDITTUDE_MODELS_DIR", "EDITTUDE_MODEL_PYTHON", "EDITTUDE_ASR_PYTHON", "EDITTUDE_DEMUCS_PYTHON",
    "EDITTUDE_ASR_MODEL_DIR", "EDITTUDE_DEMUCS_REPO", "EDITTUDE_SEED_VC_DIR",
    "EDITTUDE_SEED_VC_CHECKPOINT", "EDITTUDE_SEED_VC_CONFIG", "EDITTUDE_DIFFSINGER_DIR",
)}


class ModelPathDefaultsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        for relative in ("models/asr/faster-whisper-base", "models/demucs", "models/DiffSinger",
                         "models/seed-vc/ckpt", ".venv-models/bin"):
            (self.root / relative).mkdir(parents=True)
        for relative in ("models/seed-vc/ckpt/model.pth", "models/seed-vc/ckpt/config.yml"):
            (self.root / relative).write_bytes(b"")
        python = self.root / ".venv-models/bin/python"
        python.write_text("#!/bin/sh\n")
        python.chmod(0o755)
        self.environment = patch.dict(os.environ, {**UNSET, "EDITTUDE_ROOT": str(self.root)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_all_four_backends_default_inside_the_install_root(self):
        expected = {
            "EDITTUDE_ASR_MODEL_DIR": "asr/faster-whisper-base",
            "EDITTUDE_DEMUCS_REPO": "demucs",
            "EDITTUDE_SEED_VC_DIR": "seed-vc",
            "EDITTUDE_DIFFSINGER_DIR": "DiffSinger",
        }
        self.assertEqual(models_root(), self.root / "models")
        for name, relative in expected.items():
            with self.subTest(variable=name):
                self.assertEqual(models._configured_path(name), self.root / "models" / relative)
        for name in ("EDITTUDE_SEED_VC_CHECKPOINT", "EDITTUDE_SEED_VC_CONFIG"):
            with self.subTest(variable=name):
                self.assertEqual(models._configured_path(name, directory=False),
                                 self.root / "models" / models.DEFAULTS[name])

    def test_installed_model_python_is_preferred_over_the_harness_interpreter(self):
        self.assertEqual(model_python(), self.root / ".venv-models/bin/python")
        self.assertEqual(models._runtime("ASR"), str(self.root / ".venv-models/bin/python"))

    def test_environment_variables_still_override_the_defaults(self):
        elsewhere = self.root / "elsewhere"
        (elsewhere / "repo").mkdir(parents=True)
        with patch.dict(os.environ, {"EDITTUDE_DEMUCS_REPO": str(elsewhere / "repo")}):
            self.assertEqual(models._configured_path("EDITTUDE_DEMUCS_REPO"), elsewhere / "repo")
        with patch.dict(os.environ, {"EDITTUDE_MODELS_DIR": str(elsewhere)}):
            self.assertEqual(models_root(), elsewhere)
        with patch.dict(os.environ, {"EDITTUDE_MODEL_PYTHON": "python3"}):
            self.assertNotEqual(models._runtime("DEMUCS"), str(model_python()))

    def test_missing_default_names_the_install_command(self):
        (self.root / "models/demucs").rmdir()
        with self.assertRaisesRegex(models.CapabilityUnavailable, "models install"):
            models._configured_path("EDITTUDE_DEMUCS_REPO")


if __name__ == "__main__":
    unittest.main()

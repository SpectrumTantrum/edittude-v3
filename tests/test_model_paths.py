"""Run with python -m unittest discover -s tests -p test_model_paths.py."""
from __future__ import annotations

from fnmatch import fnmatch
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import install_models, models
from tools.common import model_python, models_root

# Every EDITTUDE_* knob these paths read, emptied so only EDITTUDE_ROOT decides.
UNSET = {key: "" for key in (
    "EDITTUDE_MODELS_DIR", "EDITTUDE_MODEL_PYTHON", "EDITTUDE_ASR_PYTHON", "EDITTUDE_DEMUCS_PYTHON",
    "EDITTUDE_ASR_MODEL_DIR", "EDITTUDE_DEMUCS_REPO", "EDITTUDE_SEED_VC_DIR",
    "EDITTUDE_SEED_VC_CHECKPOINT", "EDITTUDE_SEED_VC_CONFIG", "EDITTUDE_DIFFSINGER_DIR",
    "EDITTUDE_DIFFSINGER_EXP",
)}


class ModelPathDefaultsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        for relative in ("models/asr/faster-whisper-base", "models/demucs", "models/DiffSinger",
                         "models/seed-vc/ckpt", ".venv-models/bin"):
            (self.root / relative).mkdir(parents=True)
        for name in ("EDITTUDE_SEED_VC_CHECKPOINT", "EDITTUDE_SEED_VC_CONFIG"):
            (self.root / "models" / models.DEFAULTS[name]).write_bytes(b"")
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

    def test_the_diffsinger_experiment_defaults_to_a_checkpoint_the_installer_fetches(self):
        entry = install_models.BACKENDS["diffsinger"]["hf"][0]
        self.assertEqual(models.DEFAULTS["EDITTUDE_DIFFSINGER_DIR"] + "/checkpoints", entry["into"])
        self.assertIn(models.DIFFSINGER_EXP + "/*", entry["files"])

    def test_the_seed_vc_checkpoint_defaults_match_the_installed_filenames(self):
        entry = install_models.BACKENDS["seed-vc"]["hf"][0]
        for name in ("EDITTUDE_SEED_VC_CHECKPOINT", "EDITTUDE_SEED_VC_CONFIG"):
            relative = Path(models.DEFAULTS[name])
            self.assertEqual(relative.parent.as_posix(), entry["into"])
            self.assertTrue(any(fnmatch(relative.name, pattern) for pattern in entry["files"]), relative)

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


class InstallerSchemaTest(unittest.TestCase):
    """BACKENDS is data install() unpacks; drift here is what broke `models install --models asr`."""

    def test_every_hf_entry_carries_exactly_the_keys_install_consumes(self):
        for name, backend in install_models.BACKENDS.items():
            for entry in backend["hf"]:
                with self.subTest(backend=name, repo=entry.get("repo")):
                    self.assertIsInstance(entry, dict)
                    self.assertLessEqual({"repo", "rev", "into"}, entry.keys())
                    self.assertLessEqual(entry.keys(), {"repo", "rev", "into", "files", "cache"})
                    self.assertRegex(entry["rev"], r"^[0-9a-f]{40}$")

    def test_asr_downloads_the_pinned_revision_into_the_directory_models_py_reads(self):
        entry = install_models.BACKENDS["asr"]["hf"][0]
        destination = Path("/nowhere/models") / entry["into"]
        # No V2_MODELS to adopt from and nothing on disk, so this takes the download branch.
        with patch.object(install_models, "_run") as run, \
                patch.object(install_models, "V2_MODELS", Path("/nonexistent")):
            install_models._snapshot(entry, destination)
        payload = json.loads(run.call_args[0][0][-1])
        self.assertEqual(payload["revision"], entry["rev"])
        self.assertEqual(payload["local_dir"], str(destination))
        self.assertEqual(entry["into"], models.DEFAULTS["EDITTUDE_ASR_MODEL_DIR"])

    def test_v2_reuse_skips_a_same_revision_directory_holding_different_files(self):
        """Plachta/Seed-VC ships both checkpoints at one revision; only the f0 one will do."""
        entry = next(item for item in install_models.BACKENDS["seed-vc"]["hf"]
                     if item["repo"] == "Plachta/Seed-VC")
        v2 = Path(tempfile.mkdtemp()) / "v2-models"
        for name, files in (("seed-vc", ["DiT_seed_v2_uvit_whisper_small_wavenet.pth"]),
                            ("seed-vc-f0", [pattern.replace("*", "x") for pattern in entry["files"]])):
            (v2 / name).mkdir(parents=True)
            (v2 / name / ".edittude-model.json").write_text(
                json.dumps({"revision": entry["rev"], "files": files}), encoding="utf-8")
            for file in files:
                (v2 / name / file).write_bytes(b"")
        with patch.object(install_models, "V2_MODELS", v2):
            source, names = install_models._installed_v2(entry)
        self.assertEqual(source.name, "seed-vc-f0")
        self.assertEqual(len(names), len(entry["files"]))


class InstallFailureTest(unittest.TestCase):
    """Missing tools and missing patches exit with a message, not a traceback."""

    def test_missing_executable_exits_cleanly(self):
        with self.assertRaises(SystemExit) as raised:
            install_models._run(["definitely-not-a-command-xyz"])
        self.assertIn("definitely-not-a-command-xyz is missing", str(raised.exception))

    def test_failing_command_exits_cleanly(self):
        with self.assertRaises(SystemExit) as raised:
            install_models._run(["sh", "-c", "exit 3"])
        self.assertIn("exit code 3", str(raised.exception))

    def test_clone_checks_the_patch_before_cloning(self):
        spec = {"url": "https://example.invalid/repo", "into": "no-such-backend", "sha": "0" * 40}
        with patch.object(install_models, "_run") as run, \
                self.assertRaises(SystemExit) as raised:
            install_models._clone(spec, Path("/nowhere/clone"))
        self.assertIn("no-such-backend.patch", str(raised.exception))
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

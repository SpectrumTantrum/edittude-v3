"""Installer checks. Run: python -m unittest tests.test_install -v."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class InstallScriptTest(unittest.TestCase):
    def test_install_sh_is_valid_bash(self):
        subprocess.check_call(["bash", "-n", str(ROOT / "install.sh")])

    def test_local_install_writes_working_launcher(self):
        with tempfile.TemporaryDirectory() as temporary:
            bindir = Path(temporary) / "bin"
            env = os.environ.copy()
            env["EDITTUDE_BIN"] = str(bindir)
            subprocess.check_call(["bash", str(ROOT / "install.sh")], env=env)
            launcher = bindir / "edittude-v3"
            self.assertTrue(os.access(launcher, os.X_OK))
            text = launcher.read_text(encoding="utf-8")
            self.assertIn(f'EDITTUDE_ROOT="{ROOT}"', text)
            version = subprocess.check_output([str(launcher), "--version"], text=True)
            self.assertIn("edittude-v3", version)
            skills = subprocess.check_output(
                [str(launcher), "skills", "-C", temporary],
                text=True,
            )
            self.assertIn("zero-shot-cut", skills)


if __name__ == "__main__":
    unittest.main()

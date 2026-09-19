"""Installer checks. Run: python -m unittest tests.test_install -v."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from edittude_v3.cli import _parser, main

ROOT = Path(__file__).resolve().parents[1]
MINIMAL_PYPROJECT = """[project]
name = "edittude-v3"
version = "0.0.0"
requires-python = ">=3.13"
dependencies = []
"""


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TEMPLATE_DIR"] = ""
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def _git(cwd: Path, *args: str) -> None:
    subprocess.check_call(["git", "-C", str(cwd), *args], env=_git_env())


def _git_output(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(cwd), *args], env=_git_env(), text=True)


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True)
    subprocess.check_call(
        ["git", "-c", "init.templateDir=", "init", "-b", "main", str(path)],
        env=_git_env(),
    )
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")


def _write_checkout(path: Path, marker: str) -> None:
    shutil.copy(ROOT / "install.sh", path / "install.sh")
    (path / "pyproject.toml").write_text(MINIMAL_PYPROJECT, encoding="utf-8")
    (path / "marker.txt").write_text(marker, encoding="utf-8")


def _commit(path: Path, message: str) -> None:
    _git(path, "add", "-A")
    _git(path, "commit", "-m", message)


class InstallScriptTest(unittest.TestCase):
    def test_install_sh_is_valid_bash(self):
        subprocess.check_call(["bash", "-n", str(ROOT / "install.sh")])

    def test_local_install_writes_working_launcher(self):
        with tempfile.TemporaryDirectory() as temporary:
            bindir = Path(temporary) / "bin"
            env = os.environ.copy()
            env["EDITTUDE_BIN"] = str(bindir)
            config_home = Path(temporary) / "home"
            env["EDITTUDE_CONFIG_HOME"] = str(config_home)
            subprocess.check_call(["bash", str(ROOT / "install.sh")], env=env)
            self.assertIn("DEEPSEEK_API_KEY", (config_home / ".env").read_text(encoding="utf-8"))
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

    def test_update_pulls_new_commit_from_current_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary = Path(temporary)
            origin = temporary / "origin"
            home = temporary / "home"
            bindir = temporary / "bin"
            _init_repo(origin)
            _write_checkout(origin, "one")
            _commit(origin, "one")
            subprocess.check_call(["git", "clone", str(origin), str(home)], env=_git_env())
            _write_checkout(origin, "two")
            _commit(origin, "two")

            env = _git_env()
            env["EDITTUDE_BIN"] = str(bindir)
            env["EDITTUDE_CONFIG_HOME"] = str(temporary / "config")
            env["EDITTUDE_SKIP_SYNC"] = "1"
            subprocess.check_call(["bash", str(home / "install.sh"), "update"], env=env)

            self.assertEqual((home / "marker.txt").read_text(encoding="utf-8"), "two")
            self.assertIn("two", _git_output(home, "log", "-1", "--oneline"))
            launcher = (bindir / "edittude-v3").read_text(encoding="utf-8")
            root_line = next(line for line in launcher.splitlines() if "EDITTUDE_ROOT=" in line)
            self.assertRegex(root_line, r'^export EDITTUDE_ROOT="[^"]+"$')
            quoted = root_line.split("=", 1)[1].strip('"')
            self.assertEqual(Path(quoted).resolve(), home.resolve())

    def test_update_refuses_dirty_checkout_without_force(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary = Path(temporary)
            origin = temporary / "origin"
            home = temporary / "home"
            _init_repo(origin)
            _write_checkout(origin, "one")
            _commit(origin, "one")
            subprocess.check_call(["git", "clone", str(origin), str(home)], env=_git_env())
            (home / "marker.txt").write_text("dirty", encoding="utf-8")

            env = _git_env()
            env["EDITTUDE_BIN"] = str(temporary / "bin")
            env["EDITTUDE_CONFIG_HOME"] = str(temporary / "config")
            env["EDITTUDE_SKIP_SYNC"] = "1"
            failed = subprocess.run(
                ["bash", str(home / "install.sh"), "update"],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("local changes", failed.stderr)
            self.assertEqual((home / "marker.txt").read_text(encoding="utf-8"), "dirty")

            subprocess.check_call(
                ["bash", str(home / "install.sh"), "update", "--force"],
                env=env,
            )
            self.assertEqual((home / "marker.txt").read_text(encoding="utf-8"), "one")

    def test_update_command_runs_installer(self):
        self.assertEqual(_parser().parse_args(["update"]).command, "update")
        self.assertTrue(_parser().parse_args(["update", "--force"]).force)
        with patch("edittude_v3.cli.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
            main(["update"])
            args, kwargs = run.call_args
            self.assertEqual(args[0][:2], ["bash", str(ROOT / "install.sh")])
            self.assertIn("update", args[0])
            self.assertEqual(kwargs["env"]["EDITTUDE_UPDATE"], "1")

            run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
            main(["update", "--force"])
            self.assertEqual(run.call_args.kwargs["env"]["EDITTUDE_FORCE"], "1")


if __name__ == "__main__":
    unittest.main()

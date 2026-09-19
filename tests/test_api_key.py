"""First-launch DeepSeek key prompt. Run: python -m unittest tests.test_api_key -v."""
from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from edittude_v3.agent import (
    API_KEY_ENV,
    configured_api_key,
    load_env,
    normalize_api_key,
    save_api_key,
)
from edittude_v3.cli import ensure_api_key
from edittude_v3.paths import env_file


class SaveApiKeyTest(unittest.TestCase):
    def test_creates_env_file_and_sets_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env"
            with patch.dict(os.environ, {API_KEY_ENV: ""}, clear=False):
                saved = save_api_key("sk-test-create", path)
                self.assertEqual(os.environ[API_KEY_ENV], "sk-test-create")
            self.assertEqual(saved, path)
            self.assertEqual(path.read_text(encoding="utf-8"), "DEEPSEEK_API_KEY=sk-test-create\n")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_replaces_empty_key_and_keeps_other_lines(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env"
            path.write_text("# keep\nDEEPSEEK_API_KEY=\nOTHER=1\n", encoding="utf-8")
            with patch.dict(os.environ, {API_KEY_ENV: ""}, clear=False):
                save_api_key("sk-test-replace", path)
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "# keep\nDEEPSEEK_API_KEY=sk-test-replace\nOTHER=1\n",
            )

    def test_appends_when_file_has_no_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env"
            path.write_text("OTHER=1", encoding="utf-8")
            with patch.dict(os.environ, {API_KEY_ENV: ""}, clear=False):
                save_api_key("sk-test-append", path)
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "OTHER=1\nDEEPSEEK_API_KEY=sk-test-append\n",
            )

    def test_normalize_strips_assignment_and_quotes(self):
        self.assertEqual(normalize_api_key('  DEEPSEEK_API_KEY="sk-pasted"  '), "sk-pasted")
        self.assertEqual(normalize_api_key("sk-plain"), "sk-plain")

    def test_empty_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                save_api_key("   ", Path(temporary) / ".env")


class EnsureApiKeyTest(unittest.TestCase):
    def test_skips_prompt_when_key_is_already_set(self):
        with patch.dict(os.environ, {API_KEY_ENV: "sk-already"}):
            with patch("edittude_v3.cli.getpass.getpass") as prompt:
                ensure_api_key()
            prompt.assert_not_called()

    def test_noninteractive_missing_key_exits(self):
        with patch.dict(os.environ, {API_KEY_ENV: ""}, clear=False):
            with patch("edittude_v3.cli.sys.stdin.isatty", return_value=False):
                with self.assertRaises(SystemExit) as raised:
                    ensure_api_key()
        self.assertIn("DEEPSEEK_API_KEY is missing", str(raised.exception))

    def test_first_launch_prompts_and_saves(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env"
            with patch.dict(os.environ, {API_KEY_ENV: ""}, clear=False):
                with (
                    patch("edittude_v3.cli.sys.stdin.isatty", return_value=True),
                    patch("edittude_v3.cli.getpass.getpass", side_effect=["", "sk-from-prompt"]),
                    patch("edittude_v3.cli.save_api_key", return_value=path) as save,
                    patch("edittude_v3.cli.console.print"),
                ):
                    ensure_api_key()
            save.assert_called_once_with("sk-from-prompt")

    def test_first_launch_writes_install_env(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".env").write_text("DEEPSEEK_API_KEY=\n", encoding="utf-8")
            with patch.dict(os.environ, {API_KEY_ENV: "", "EDITTUDE_ROOT": str(root)}):
                with (
                    patch("edittude_v3.cli.sys.stdin.isatty", return_value=True),
                    patch("edittude_v3.cli.getpass.getpass", return_value="sk-written"),
                    patch("edittude_v3.cli.console.print"),
                ):
                    ensure_api_key()
                    self.assertEqual(os.environ[API_KEY_ENV], "sk-written")
            self.assertEqual((root / ".env").read_text(encoding="utf-8"), "DEEPSEEK_API_KEY=sk-written\n")

    def test_configured_api_key_treats_whitespace_as_missing(self):
        with patch.dict(os.environ, {API_KEY_ENV: "   "}):
            self.assertEqual(configured_api_key(), "")


if __name__ == "__main__":
    unittest.main()

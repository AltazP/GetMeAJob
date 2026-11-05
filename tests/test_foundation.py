"""Test configuration safety and the actual command-line entry point."""

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from getmeajob import __version__
from getmeajob.__main__ import main
from getmeajob.config import ConfigurationError, Settings, load_settings


ROOT = Path(__file__).resolve().parents[1]


class ConfigurationTests(unittest.TestCase):
    def load_toml(self, content: str) -> Settings:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.toml"
            path.write_text(content, encoding="utf-8")
            return load_settings(path)

    def test_defaults_are_local(self):
        self.assertEqual(load_settings(), Settings(host="127.0.0.1", port=8000))

    def test_example_is_valid(self):
        self.assertEqual(load_settings(ROOT / "config/settings.example.toml"), Settings())

    def test_port_can_be_configured(self):
        self.assertEqual(self.load_toml('[dashboard]\nport = 9000\n').port, 9000)

    def test_port_boundaries(self):
        for port in (1024, 65535):
            with self.subTest(port=port):
                self.assertEqual(Settings(port=port).port, port)

    def test_unsafe_hosts_are_rejected(self):
        for host in ("0.0.0.0", "::", "localhost", "192.168.1.1", "", 123):
            with self.subTest(host=host), self.assertRaises(ConfigurationError):
                Settings(host=host)

    def test_invalid_ports_are_rejected(self):
        for port in (0, 80, 1023, 65536, True, 8000.0, "8000", None):
            with self.subTest(port=port), self.assertRaises(ConfigurationError):
                Settings(port=port)

    def test_unknown_settings_are_rejected(self):
        for content in ('[unsupported]\ncity="Example"', '[dashboard]\nprot=8000', 'dashboard="bad"'):
            with self.subTest(content=content), self.assertRaises(ConfigurationError):
                self.load_toml(content)

    def test_malformed_toml_does_not_leak_content(self):
        with self.assertRaises(ConfigurationError) as context:
            self.load_toml('secret = "sensitive-test-marker')
        self.assertNotIn("sensitive-test-marker", str(context.exception))

    def test_missing_explicit_config_fails(self):
        with TemporaryDirectory() as directory, self.assertRaises(ConfigurationError):
            load_settings(Path(directory) / "missing.toml")


class CommandTests(unittest.TestCase):
    def test_main_reports_current_phase(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([]), 0)
        self.assertIn("Phase 2 job discovery ready", output.getvalue())
        self.assertIn("not running", output.getvalue())

    def test_config_error_returns_nonzero(self):
        output = io.StringIO()
        with TemporaryDirectory() as directory, redirect_stderr(output):
            self.assertEqual(main(["--config", str(Path(directory) / "missing.toml")]), 2)
        self.assertIn("Configuration error", output.getvalue())

    def test_module_entry_point(self):
        result = subprocess.run(
            [sys.executable, "-m", "getmeajob", "--config", "config/settings.example.toml"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("127.0.0.1:8000", result.stdout)

    def test_version_command(self):
        result = subprocess.run(
            [sys.executable, "-m", "getmeajob", "--version"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"GetMeAJob {__version__}")

    def test_help_command(self):
        result = subprocess.run(
            [sys.executable, "-m", "getmeajob", "--help"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--config", result.stdout)


if __name__ == "__main__":
    unittest.main()

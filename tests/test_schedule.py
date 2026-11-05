"""Offline tests for the launchd schedule and the refresh runner. launchctl and the network are mocked."""
import fcntl
import plistlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from getmeajob import schedule


class ScheduleTests(unittest.TestCase):
    def test_calendar_has_three_weekday_runs_and_one_weekend_run(self):
        entries = schedule.calendar()
        weekday = [e for e in entries if e["Weekday"] in (1, 2, 3, 4, 5)]; weekend = [e for e in entries if e["Weekday"] in (0, 6)]
        self.assertEqual(len(weekday), 15); self.assertEqual(len(weekend), 2)
        for day in range(1, 6): self.assertEqual(len([e for e in weekday if e["Weekday"] == day]), 3)
        for day in (0, 6): self.assertEqual(len([e for e in weekend if e["Weekday"] == day]), 1)
        self.assertEqual({(e["Hour"], e["Minute"]) for e in weekday}, {(8, 0), (12, 30), (17, 0)})

    def test_time_validation(self):
        for bad in ("25:00", "08:75", "abc", "8", ""):
            with self.assertRaises(ValueError): schedule.parse_times(bad)
        with self.assertRaises(ValueError): schedule.parse_times(",".join(["08:00"] * 7))

    def test_plist_is_user_level_runs_our_command_and_holds_no_secrets(self):
        data = schedule.plist(Path("/p/config/live.toml"), python="/p/.venv/bin/python", root=Path("/p"))
        self.assertEqual(data["ProgramArguments"], ["/p/.venv/bin/python", "-m", "getmeajob", "schedule", "run", "--config", "/p/config/live.toml"])
        self.assertEqual(data["WorkingDirectory"], "/p"); self.assertFalse(data["RunAtLoad"])
        self.assertNotIn("password", str(data).lower())
        plistlib.loads(plistlib.dumps(data))

    def test_install_uninstall_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "live.toml"; config.write_text("x"); path = Path(tmp) / "agents" / "x.plist"
            runner = MagicMock(return_value=SimpleNamespace(returncode=0, stderr="", stdout=""))
            with patch.object(schedule, "ROOT", Path(tmp)):
                schedule.install(config, runner, path, root=Path(tmp))
            self.assertTrue(path.exists())
            commands = [call.args[0][:2] for call in runner.call_args_list]
            self.assertEqual(commands, [["launchctl", "bootout"], ["launchctl", "bootstrap"]])
            self.assertEqual(schedule.status(runner, path), "Installed and loaded.")
            self.assertTrue(schedule.uninstall(runner, path)); self.assertFalse(path.exists())
            self.assertEqual(schedule.status(runner, path), "Not installed.")
            self.assertFalse(schedule.uninstall(runner, path))

    def test_install_reports_launchctl_failure_and_missing_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError): schedule.install(Path(tmp) / "nope.toml", MagicMock(), Path(tmp) / "a.plist")
            config = Path(tmp) / "c.toml"; config.write_text("x")
            failing = MagicMock(return_value=SimpleNamespace(returncode=5, stderr="denied", stdout=""))
            with self.assertRaises(RuntimeError): schedule.install(config, failing, Path(tmp) / "a.plist", root=Path(tmp))


class RefreshTests(unittest.TestCase):
    def settings(self, tmp):
        from dataclasses import replace
        from getmeajob.config import load_settings
        base = load_settings(Path(__file__).resolve().parents[1] / "config" / "search.example.toml")
        return replace(base, database=Path(tmp) / "data" / "jobs.sqlite3")

    def test_overlapping_run_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = self.settings(tmp); settings.database.parent.mkdir()
            with open(settings.database.parent / "refresh.lock", "w") as held:
                fcntl.flock(held, fcntl.LOCK_EX)
                messages = []
                self.assertEqual(schedule.refresh(settings, False, messages.append), 0)
            self.assertIn("already running", messages[0])

    def test_runs_passes_until_nothing_is_deferred_then_notifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = self.settings(tmp)
            reports = [SimpleNamespace(errors=("Source 1 deferred: cap",), fetched=10, successful_boards=3),
                       SimpleNamespace(errors=("Source 2: broken",), fetched=5, successful_boards=2)]
            search = MagicMock(side_effect=reports); verify = MagicMock(return_value={"planned_sources": 0})
            registry = MagicMock(); registry.eligible.return_value = [1]
            with patch("getmeajob.source_management.search_enabled", search), patch("getmeajob.source_management.verify", verify), \
                 patch("getmeajob.source_management.client_for", MagicMock()), patch("getmeajob.registry.Registry", return_value=registry), \
                 patch("getmeajob.notify_cli.after_search") as notify:
                messages = []
                self.assertEqual(schedule.refresh(settings, True, messages.append), 0)
            self.assertEqual(search.call_count, 2); notify.assert_called_once()
            self.assertTrue(any("1 failed" in m for m in messages))

    def test_pass_count_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = self.settings(tmp)
            search = MagicMock(return_value=SimpleNamespace(errors=("Source 1 deferred: cap",), fetched=0, successful_boards=0))
            registry = MagicMock(); registry.eligible.return_value = [1]
            with patch("getmeajob.source_management.search_enabled", search), patch("getmeajob.source_management.verify", MagicMock(return_value={"planned_sources": 5})), \
                 patch("getmeajob.source_management.client_for", MagicMock()), patch("getmeajob.registry.Registry", return_value=registry):
                schedule.refresh(settings, False, lambda m: None)
            self.assertEqual(search.call_count, schedule.MAX_PASSES)


if __name__ == "__main__":
    unittest.main()

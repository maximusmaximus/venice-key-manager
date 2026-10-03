"""
Unit tests for Venice Key Manager ServiceSupervisor & Auto-Restart Watchdog.
"""

import os
import sys
import json
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.supervisor import ServiceSupervisor, is_port_open, is_process_running


class TestServiceSupervisor(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)
        self.supervisor = ServiceSupervisor(root_dir=self.root_path, port=8844)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_initial_state(self):
        st = self.supervisor.load_state()
        self.assertEqual(st.get("status"), "STOPPED")
        self.assertEqual(st.get("restarts_count"), 0)
        self.assertEqual(st.get("crash_history"), [])

    def test_save_and_load_state(self):
        test_state = {
            "status": "RUNNING",
            "supervisor_pid": 12345,
            "child_pid": 12346,
            "restarts_count": 3,
            "started_at": "2026-09-29T12:00:00Z",
            "last_restart_at": "2026-09-29T12:05:00Z",
            "last_exit_code": 1,
            "crash_history": [
                {"timestamp": "2026-09-29T12:05:00Z", "duration_seconds": 1.5, "exit_code": 1, "pid": 12346}
            ]
        }
        self.supervisor.save_state(test_state)
        loaded = self.supervisor.load_state()
        self.assertEqual(loaded["supervisor_pid"], 12345)
        self.assertEqual(loaded["restarts_count"], 3)
        self.assertEqual(len(loaded["crash_history"]), 1)

    def test_request_restart_sentinel(self):
        sentinel = self.root_path / ".restart_requested"
        self.assertFalse(sentinel.exists())

        res = self.supervisor.request_restart()
        self.assertTrue(res.get("success"))
        self.assertTrue(sentinel.exists())

    def test_get_status_structure(self):
        st = self.supervisor.get_status()
        self.assertTrue(st.get("success"))
        self.assertIn("platform", st)
        self.assertIn("port", st)
        self.assertIn("port_listening", st)
        self.assertIn("supervisor_running", st)
        self.assertIn("child_running", st)
        self.assertIn("restarts_count", st)
        self.assertIn("autostart_installed", st)
        self.assertIn("autostart_methods", st)
        self.assertIn("crash_history", st)

    def test_watchdog_restart_simulation(self):
        """Simulate a crashing child process and verify supervisor auto-restarts and tracks crash."""
        # Create a dummy script that exits with code 42
        crash_script = self.root_path / "crash_dummy.py"
        crash_script.write_text("import sys; sys.exit(42)\n", encoding="utf-8")

        # Run supervisor with max_restarts=2 so it stops after 2 restarts
        child_args = [sys.executable, str(crash_script)]
        self.supervisor.run_supervisor(child_args=child_args, max_restarts=2)

        # Check recorded state
        st = self.supervisor.load_state()
        self.assertGreaterEqual(st.get("restarts_count", 0), 2)
        self.assertEqual(st.get("last_exit_code"), 42)
        crashes = st.get("crash_history", [])
        self.assertGreaterEqual(len(crashes), 2)
        self.assertEqual(crashes[-1]["exit_code"], 42)

    def test_windows_install_uninstall_dry(self):
        """Test Windows installation paths."""
        with patch("platform.system", return_value="Windows"):
            with patch.dict(os.environ, {"APPDATA": str(self.root_path)}), \
                 patch("subprocess.run") as mock_run:
                # Never touch the real Task Scheduler from tests: the previous
                # version of this test overwrote and then deleted the live
                # 'VeniceKeyManagerSupervisor' autostart task.
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                startup_dir = self.root_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
                startup_dir.mkdir(parents=True, exist_ok=True)

                res = self.supervisor.install()
                self.assertTrue(res.get("success"))
                vbs = startup_dir / "start_venice_manager.vbs"
                self.assertTrue(vbs.exists())
                content = vbs.read_text(encoding="utf-8")
                self.assertIn("supervisor.py", content)

                # Now test uninstall
                un_res = self.supervisor.uninstall()
                self.assertTrue(un_res.get("success"))
                self.assertFalse(vbs.exists())
                for call in mock_run.call_args_list:
                    self.assertIn("schtasks", str(call))

    def test_macos_install_uninstall_dry(self):
        """Test macOS LaunchAgent plist creation and removal."""
        with patch("platform.system", return_value="Darwin"):
            with patch("pathlib.Path.home", return_value=self.root_path):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                    res = self.supervisor.install()
                    self.assertTrue(res.get("success"))

                    plist = self.root_path / "Library" / "LaunchAgents" / "com.venice.keymanager.plist"
                    self.assertTrue(plist.exists())
                    content = plist.read_text(encoding="utf-8")
                    self.assertIn("<key>KeepAlive</key>", content)
                    self.assertIn("<true/>", content)
                    self.assertIn("supervisor.py", content)

                    un_res = self.supervisor.uninstall()
                    self.assertTrue(un_res.get("success"))
                    self.assertFalse(plist.exists())

    def test_linux_install_uninstall_dry(self):
        """Test Linux systemd service creation and removal."""
        with patch("platform.system", return_value="Linux"):
            with patch("pathlib.Path.home", return_value=self.root_path):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                    res = self.supervisor.install()
                    self.assertTrue(res.get("success"))

                    svc = self.root_path / ".config" / "systemd" / "user" / "venice-key-manager.service"
                    self.assertTrue(svc.exists())
                    content = svc.read_text(encoding="utf-8")
                    self.assertIn("Restart=always", content)
                    self.assertIn("supervisor.py", content)

                    un_res = self.supervisor.uninstall()
                    self.assertTrue(un_res.get("success"))
                    self.assertFalse(svc.exists())


if __name__ == "__main__":
    unittest.main()

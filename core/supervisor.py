"""
Cross-Platform Process Supervisor & Boot Auto-Restart Manager for Venice Key Manager.

Guarantees 24/7 Zero-Downtime:
1. Process Watchdog: Spawns and supervises `run.py --all`. If the process crashes,
   encounters an unhandled exception, or exits unexpectedly, the watchdog immediately
   restarts it with crash tracking and exponential backoff protection.
2. Boot Auto-Start (Cross-Platform):
   - Windows Workstation: Startup VBScript (silent background window) + Windows Task Scheduler (ONLOGON).
   - macOS (Apple Silicon mcmini): Native launchd LaunchAgent (~/Library/LaunchAgents) with KeepAlive=true.
   - Linux: Native systemd user/system service with Restart=always.
3. Remote / Local IPC: Supports instant restart via sentinel file or API call.
"""

import os
import sys
import time
import json
import signal
import socket
import platform
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = PROJECT_ROOT / ".supervisor_state.json"
SENTINEL_RESTART = PROJECT_ROOT / ".restart_requested"
LOG_FILE = PROJECT_ROOT / ".supervisor.log"


def is_port_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    """Check if a TCP port is open and listening locally."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def is_process_running(pid: int) -> bool:
    """Check if a process with given PID is alive."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            # Query tasklist on Windows
            cmd = f'tasklist /FI "PID eq {pid}" /NH'
            out = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL)
            return str(pid) in out
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


class ServiceSupervisor:
    """Manages the lifecycle, auto-restart watchdog, and boot registration of Venice Key Manager."""

    def __init__(self, root_dir: Optional[Path] = None, port: int = 8844):
        self.root_dir = root_dir or PROJECT_ROOT
        self.port = port
        self.state_file = self.root_dir / ".supervisor_state.json"
        self.sentinel_restart = self.root_dir / ".restart_requested"
        self.log_file = self.root_dir / ".supervisor.log"
        self._stop_requested = False

    def log(self, message: str):
        """Log message with timestamp to stdout and supervisor log file."""
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{ts} UTC] [Supervisor] {message}"
        try:
            print(entry, flush=True)
        except UnicodeEncodeError:
            try:
                enc = getattr(sys.stdout, "encoding", "utf-8") or "utf-8"
                safe_entry = entry.encode(enc, errors="replace").decode(enc)
                print(safe_entry, flush=True)
            except Exception:
                pass
        except Exception:
            pass

        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(entry + "\n")
        except Exception:
            pass

    # ---------------------------------------------------------
    # State Persistence
    # ---------------------------------------------------------

    def load_state(self) -> Dict[str, Any]:
        """Load supervisor runtime state."""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "status": "STOPPED",
            "supervisor_pid": None,
            "child_pid": None,
            "restarts_count": 0,
            "started_at": None,
            "last_restart_at": None,
            "last_exit_code": None,
            "crash_history": []
        }

    def save_state(self, state: Dict[str, Any]):
        """Save supervisor runtime state atomically."""
        try:
            tmp_path = self.state_file.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            tmp_path.replace(self.state_file)
        except Exception as e:
            self.log(f"Warning: could not save state file: {e}")

    # ---------------------------------------------------------
    # Watchdog & Child Process Supervision
    # ---------------------------------------------------------

    def run_supervisor(self, child_args: Optional[List[str]] = None, max_restarts: int = 0):
        """
        Run the supervisor loop. Spawns child process and auto-restarts indefinitely
        if child crashes or exits unexpectedly.
        """
        self._stop_requested = False

        # Set up signal handlers for graceful shutdown
        def handle_signal(sig, frame):
            self.log(f"Received termination signal ({sig}). Initiating clean shutdown...")
            self._stop_requested = True

        try:
            signal.signal(signal.SIGINT, handle_signal)
            signal.signal(signal.SIGTERM, handle_signal)
        except Exception:
            pass

        if child_args is None:
            child_args = [sys.executable, "-u", str(self.root_dir / "run.py"), "--all", "--port", str(self.port)]

        state = self.load_state()
        state["status"] = "RUNNING"
        state["supervisor_pid"] = os.getpid()
        state["started_at"] = datetime.utcnow().isoformat() + "Z"
        self.save_state(state)

        self.log(f"🚀 Venice Key Manager Supervisor started (PID: {os.getpid()})")
        self.log(f"Supervising command: {' '.join(child_args)}")

        consecutive_fast_crashes = 0

        while not self._stop_requested:
            # Check if sentinel restart was requested prior to spawn
            if self.sentinel_restart.exists():
                try:
                    self.sentinel_restart.unlink()
                except Exception:
                    pass

            spawn_time = time.time()
            self.log(f"Starting child process...")

            child_log_file = None
            try:
                child_log_path = self.root_dir / ".child_service.log"
                child_log_file = open(child_log_path, "a", encoding="utf-8", errors="replace", buffering=1)
                proc = subprocess.Popen(
                    child_args,
                    cwd=str(self.root_dir),
                    stdin=subprocess.DEVNULL,
                    stdout=child_log_file,
                    stderr=subprocess.STDOUT
                )
            except Exception as e:
                self.log(f"❌ Failed to spawn child process: {e}")
                if child_log_file:
                    try:
                        child_log_file.close()
                    except Exception:
                        pass
                time.sleep(5)
                continue

            state["child_pid"] = proc.pid
            state["status"] = "RUNNING"
            self.save_state(state)
            self.log(f"Child process running (PID: {proc.pid})")

            # Monitoring loop for this child
            while True:
                if self._stop_requested:
                    self.log(f"Terminating child process (PID: {proc.pid}) due to stop request...")
                    try:
                        proc.terminate()
                        proc.wait(timeout=5)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    if child_log_file:
                        try:
                            child_log_file.close()
                        except Exception:
                            pass
                    state["status"] = "STOPPED"
                    state["child_pid"] = None
                    self.save_state(state)
                    self.log("Supervisor stopped gracefully.")
                    return

                # Check if restart was requested via sentinel file
                if self.sentinel_restart.exists():
                    self.log(f"🔄 Restart sentinel detected! Recycling child process (PID: {proc.pid})...")
                    try:
                        self.sentinel_restart.unlink()
                    except Exception:
                        pass
                    try:
                        proc.terminate()
                        proc.wait(timeout=5)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    if child_log_file:
                        try:
                            child_log_file.close()
                        except Exception:
                            pass
                    break  # Break inner loop to spawn new child

                # Check child status
                ret = proc.poll()
                if ret is not None:
                    if child_log_file:
                        try:
                            child_log_file.close()
                        except Exception:
                            pass
                    duration = time.time() - spawn_time
                    self.log(f"⚠️ Child process (PID: {proc.pid}) exited with code {ret} after {duration:.1f}s.")

                    restarts = state.get("restarts_count", 0) + 1
                    state["restarts_count"] = restarts
                    state["last_restart_at"] = datetime.utcnow().isoformat() + "Z"
                    state["last_exit_code"] = ret

                    crashes = state.get("crash_history", [])
                    crashes.append({
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                        "duration_seconds": round(duration, 2),
                        "exit_code": ret,
                        "pid": proc.pid
                    })
                    state["crash_history"] = crashes[-20:]  # Keep last 20
                    self.save_state(state)

                    if max_restarts > 0 and restarts >= max_restarts:
                        self.log(f"Reached maximum restart limit ({max_restarts}). Exiting supervisor.")
                        return

                    # Backoff logic if crashing immediately (< 3s)
                    if duration < 3.0:
                        consecutive_fast_crashes += 1
                        backoff = min(10.0, 1.5 * consecutive_fast_crashes)
                        self.log(f"Rapid exit detected ({consecutive_fast_crashes} in a row). Waiting {backoff:.1f}s before restart...")
                        time.sleep(backoff)
                    else:
                        consecutive_fast_crashes = 0
                        self.log("Waiting 2.0s before automatic restart...")
                        time.sleep(2.0)

                    break  # Break inner loop to spawn next child

                time.sleep(1.0)

    def request_restart(self) -> Dict[str, Any]:
        """Request supervisor to restart the child process immediately."""
        try:
            self.sentinel_restart.touch()
            self.log("Wrote restart sentinel file.")
            state = self.load_state()
            child_pid = state.get("child_pid")
            if child_pid and is_process_running(child_pid):
                # Send terminate or kill to expedite restart
                try:
                    if os.name == "nt":
                        subprocess.run(f"taskkill /PID {child_pid} /F", shell=True, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    else:
                        os.kill(child_pid, signal.SIGTERM)
                except Exception:
                    pass
            return {"success": True, "message": "Restart signal dispatched to supervisor"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ---------------------------------------------------------
    # Boot Auto-Start Installation (Windows / macOS / Linux)
    # ---------------------------------------------------------

    def install(self) -> Dict[str, Any]:
        """Install boot auto-start and crash supervisor for the current OS."""
        system = platform.system().lower()
        results: Dict[str, Any] = {
            "system": system,
            "installed_methods": [],
            "messages": [],
            "success": False
        }

        if system == "windows":
            return self._install_windows(results)
        elif system == "darwin":
            return self._install_macos(results)
        elif system == "linux":
            return self._install_linux(results)
        else:
            results["error"] = f"Unsupported platform: {system}"
            return results

    def _install_windows(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """Install Windows startup VBScript and Task Scheduler job."""
        python_exe = sys.executable
        supervisor_py = self.root_dir / "supervisor.py"

        # 1. Startup Folder VBScript
        appdata = os.environ.get("APPDATA")
        if appdata:
            startup_dir = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
            if startup_dir.exists():
                vbs_path = startup_dir / "start_venice_manager.vbs"
                vbs_content = (
                    f'Set WshShell = CreateObject("WScript.Shell")\r\n'
                    f'WshShell.Run """{python_exe}"" ""{supervisor_py}"" --run", 0, False\r\n'
                )
                try:
                    vbs_path.write_text(vbs_content, encoding="utf-8")
                    results["installed_methods"].append("windows_startup_vbs")
                    results["messages"].append(f"Installed Startup VBScript to {vbs_path}")
                except Exception as e:
                    results["messages"].append(f"Failed to write VBScript: {e}")

        # 2. Windows Task Scheduler (ONLOGON)
        try:
            task_name = "VeniceKeyManagerSupervisor"
            tr_command = f'"{python_exe}" "{supervisor_py}" --run'
            cmd = [
                "schtasks", "/Create",
                "/TN", task_name,
                "/TR", tr_command,
                "/SC", "ONLOGON",
                "/F"
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                results["installed_methods"].append("windows_task_scheduler")
                results["messages"].append(f"Registered Windows Scheduled Task '{task_name}'")
            else:
                results["messages"].append(f"Scheduled task notice: {res.stderr.strip() or res.stdout.strip()}")
        except Exception as e:
            results["messages"].append(f"Scheduled task registration skipped: {e}")

        # 3. Inter-service integration with tailscale-a2a-manager launcher.py if present
        launcher_py = self.root_dir.parent / "tailscale-a2a-manager" / "launcher.py"
        if launcher_py.exists():
            try:
                content = launcher_py.read_text(encoding="utf-8")
                if "8844" not in content and "venice" not in content.lower():
                    # We will update launcher.py in a dedicated step
                    results["messages"].append("A2A launcher detected; port 8844 check will be integrated.")
            except Exception:
                pass

        results["success"] = len(results["installed_methods"]) > 0
        return results

    def _install_macos(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """Install macOS LaunchAgent plist (~/Library/LaunchAgents)."""
        launch_agents_dir = Path.home() / "Library" / "LaunchAgents"
        launch_agents_dir.mkdir(parents=True, exist_ok=True)
        plist_path = launch_agents_dir / "com.venice.keymanager.plist"
        python_exe = sys.executable
        supervisor_py = self.root_dir / "supervisor.py"
        std_log = self.root_dir / ".launchd.log"

        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.venice.keymanager</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_exe}</string>
        <string>{supervisor_py}</string>
        <string>--run</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{self.root_dir}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{std_log}</string>
    <key>StandardErrorPath</key>
    <string>{std_log}</string>
    <key>ProcessType</key>
    <string>Interactive</string>
</dict>
</plist>
"""
        try:
            plist_path.write_text(plist_content, encoding="utf-8")
            results["installed_methods"].append("macos_launchagent")
            results["messages"].append(f"Written LaunchAgent to {plist_path}")

            # Reload via launchctl
            subprocess.run(["launchctl", "unload", "-w", str(plist_path)], capture_output=True)
            res = subprocess.run(["launchctl", "load", "-w", str(plist_path)], capture_output=True, text=True)
            if res.returncode == 0:
                results["messages"].append("Loaded LaunchAgent into launchd")
            else:
                results["messages"].append(f"launchctl load notice: {res.stderr.strip()}")
            results["success"] = True
        except Exception as e:
            results["messages"].append(f"Failed to install LaunchAgent: {e}")
            results["success"] = False

        return results

    def _install_linux(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """Install Linux systemd service (user service or system)."""
        systemd_user_dir = Path.home() / ".config" / "systemd" / "user"
        systemd_user_dir.mkdir(parents=True, exist_ok=True)
        service_path = systemd_user_dir / "venice-key-manager.service"
        python_exe = sys.executable
        supervisor_py = self.root_dir / "supervisor.py"

        service_content = f"""[Unit]
Description=Venice & Telegram Key Management Supervisor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={self.root_dir}
ExecStart={python_exe} {supervisor_py} --run
Restart=always
RestartSec=3
KillMode=control-group

[Install]
WantedBy=default.target
"""
        try:
            service_path.write_text(service_content, encoding="utf-8")
            results["installed_methods"].append("linux_systemd_user")
            results["messages"].append(f"Written systemd service to {service_path}")

            # Enable and start
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
            res = subprocess.run(["systemctl", "--user", "enable", "--now", "venice-key-manager"], capture_output=True, text=True)
            if res.returncode == 0:
                results["messages"].append("Enabled and started systemd user service")
            else:
                results["messages"].append(f"systemctl notice: {res.stderr.strip()}")
            results["success"] = True
        except Exception as e:
            results["messages"].append(f"Failed to install systemd service: {e}")
            results["success"] = False

        return results

    # ---------------------------------------------------------
    # Boot Auto-Start Uninstallation
    # ---------------------------------------------------------

    def uninstall(self) -> Dict[str, Any]:
        """Uninstall boot auto-start services."""
        system = platform.system().lower()
        results: Dict[str, Any] = {"system": system, "removed": [], "messages": []}

        if system == "windows":
            appdata = os.environ.get("APPDATA")
            if appdata:
                vbs = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "start_venice_manager.vbs"
                if vbs.exists():
                    try:
                        vbs.unlink()
                        results["removed"].append("windows_startup_vbs")
                    except Exception as e:
                        results["messages"].append(f"Failed removing VBS: {e}")

            try:
                subprocess.run(["schtasks", "/Delete", "/TN", "VeniceKeyManagerSupervisor", "/F"], capture_output=True)
                results["removed"].append("windows_task_scheduler")
            except Exception:
                pass

        elif system == "darwin":
            plist_path = Path.home() / "Library" / "LaunchAgents" / "com.venice.keymanager.plist"
            if plist_path.exists():
                subprocess.run(["launchctl", "unload", "-w", str(plist_path)], capture_output=True)
                try:
                    plist_path.unlink()
                    results["removed"].append("macos_launchagent")
                except Exception as e:
                    results["messages"].append(f"Failed removing plist: {e}")

        elif system == "linux":
            service_path = Path.home() / ".config" / "systemd" / "user" / "venice-key-manager.service"
            if service_path.exists():
                subprocess.run(["systemctl", "--user", "disable", "--now", "venice-key-manager"], capture_output=True)
                try:
                    service_path.unlink()
                    results["removed"].append("linux_systemd_user")
                except Exception as e:
                    results["messages"].append(f"Failed removing service: {e}")

        results["success"] = len(results["removed"]) > 0
        return results

    # ---------------------------------------------------------
    # Status Query
    # ---------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Query comprehensive health and auto-restart status."""
        state = self.load_state()
        sys_name = platform.system().lower()
        port_open = is_port_open(self.port)

        sup_pid = state.get("supervisor_pid")
        sup_running = is_process_running(sup_pid) if sup_pid else False

        child_pid = state.get("child_pid")
        child_running = is_process_running(child_pid) if child_pid else False

        # Autostart check
        autostart_methods = []
        if sys_name == "windows":
            appdata = os.environ.get("APPDATA")
            if appdata:
                vbs = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "start_venice_manager.vbs"
                if vbs.exists():
                    autostart_methods.append("windows_startup_vbs")
            try:
                res = subprocess.run('schtasks /Query /TN "VeniceKeyManagerSupervisor"', shell=True, capture_output=True, text=True)
                if res.returncode == 0:
                    autostart_methods.append("windows_task_scheduler")
            except Exception:
                pass

        elif sys_name == "darwin":
            plist = Path.home() / "Library" / "LaunchAgents" / "com.venice.keymanager.plist"
            if plist.exists():
                autostart_methods.append("macos_launchagent")

        elif sys_name == "linux":
            srv = Path.home() / ".config" / "systemd" / "user" / "venice-key-manager.service"
            sys_srv = Path("/etc/systemd/system/venice-key-manager.service")
            if srv.exists() or sys_srv.exists():
                autostart_methods.append("linux_systemd")

        # Compute uptime
        uptime_sec = 0.0
        started_at = state.get("started_at")
        if started_at and (sup_running or child_running or port_open):
            try:
                dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                uptime_sec = max(0.0, (datetime.now(dt.tzinfo) - dt).total_seconds())
            except Exception:
                pass

        return {
            "success": True,
            "platform": sys_name,
            "port": self.port,
            "port_listening": port_open,
            "service_active": port_open or child_running,
            "supervisor_running": sup_running,
            "supervisor_pid": sup_pid if sup_running else None,
            "child_running": child_running,
            "child_pid": child_pid if child_running else None,
            "uptime_seconds": round(uptime_sec, 1),
            "restarts_count": state.get("restarts_count", 0),
            "last_restart_at": state.get("last_restart_at"),
            "last_exit_code": state.get("last_exit_code"),
            "autostart_installed": len(autostart_methods) > 0,
            "autostart_methods": autostart_methods,
            "crash_history": state.get("crash_history", [])[-5:]
        }

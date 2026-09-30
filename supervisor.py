#!/usr/bin/env python3
"""
Venice Key Manager - Auto-Restart Supervisor & Watchdog CLI.

Ensures the key manager runs continuously, automatically restarts upon failure,
and boots up seamlessly on Windows, macOS, and Linux.

Usage:
  python supervisor.py --run         # Launch the supervisor watchdog and child process
  python supervisor.py --install     # Install OS boot auto-start (Windows, macOS, Linux)
  python supervisor.py --uninstall   # Remove OS boot auto-start
  python supervisor.py --status      # Check supervisor & auto-restart status
  python supervisor.py --restart     # Trigger an immediate graceful restart
"""

import sys
import io
import argparse
from pathlib import Path

# Fix Windows console UTF-8 output
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.supervisor import ServiceSupervisor


def main():
    parser = argparse.ArgumentParser(description="Venice Key Manager Watchdog & Service Supervisor")
    parser.add_argument("--run", action="store_true", help="Run supervisor watchdog loop")
    parser.add_argument("--install", action="store_true", help="Install OS boot auto-start service")
    parser.add_argument("--uninstall", action="store_true", help="Uninstall OS boot auto-start service")
    parser.add_argument("--status", action="store_true", help="Check supervisor and service status")
    parser.add_argument("--restart", action="store_true", help="Request immediate restart of supervised process")
    parser.add_argument("--port", type=int, default=8844, help="Service port (default: 8844)")

    args, unknown = parser.parse_known_args()
    supervisor = ServiceSupervisor(root_dir=PROJECT_ROOT, port=args.port)

    if args.install:
        print("\n🔧 Installing Venice Key Manager auto-restart and boot services...")
        res = supervisor.install()
        if res.get("success"):
            print("✅ Auto-restart installed successfully!")
            for m in res.get("messages", []):
                print(f"  • {m}")
            print(f"  Methods: {', '.join(res.get('installed_methods', []))}\n")
        else:
            print(f"❌ Installation failed or incomplete: {res.get('messages')}\n")

    elif args.uninstall:
        print("\n🗑️ Uninstalling Venice Key Manager auto-restart services...")
        res = supervisor.uninstall()
        if res.get("success"):
            print("✅ Auto-restart removed successfully!")
            for m in res.get("messages", []):
                print(f"  • {m}")
            print(f"  Removed: {', '.join(res.get('removed', []))}\n")
        else:
            print("Notice: No active auto-restart services were found to remove.\n")

    elif args.status:
        st = supervisor.get_status()
        print("\n🛡️ Venice Key Manager Supervisor & Resilience Status:")
        print(f"  Platform:            {st.get('platform', '').upper()}")
        print(f"  Port:                {st.get('port')} ({'LISTENING ✅' if st.get('port_listening') else 'CLOSED ⚪'})")
        print(f"  Service Active:      {'YES ✅' if st.get('service_active') else 'NO ⚪'}")
        print(f"  Supervisor Running:  {'YES ✅' if st.get('supervisor_running') else 'NO ⚪'} (PID: {st.get('supervisor_pid') or 'None'})")
        print(f"  Child Process:       {'RUNNING ✅' if st.get('child_running') else 'STOPPED ⚪'} (PID: {st.get('child_pid') or 'None'})")
        print(f"  Total Auto-Restarts: {st.get('restarts_count', 0)}")
        print(f"  Uptime:              {st.get('uptime_seconds', 0):.1f} seconds")
        print(f"  Last Restart At:     {st.get('last_restart_at') or 'Never'}")
        print(f"  Last Exit Code:      {st.get('last_exit_code') if st.get('last_exit_code') is not None else 'None'}")
        print(f"  Boot Auto-Start:     {'INSTALLED ✅' if st.get('autostart_installed') else 'NOT CONFIGURED ⚠️'}")
        if st.get("autostart_methods"):
            print(f"  Active Methods:      {', '.join(st.get('autostart_methods'))}")
        crashes = st.get("crash_history", [])
        if crashes:
            print(f"\n  Recent Exits/Crashes ({len(crashes)}):")
            for c in crashes:
                print(f"    • [{c.get('timestamp')}] Exit Code: {c.get('exit_code')} (alive {c.get('duration_seconds')}s)")
        print()

    elif args.restart:
        print("\n🔄 Sending restart signal to supervisor...")
        res = supervisor.request_restart()
        if res.get("success"):
            print("✅ Restart signal sent successfully. Supervisor will recycle the process immediately.\n")
        else:
            print(f"❌ Failed to request restart: {res.get('error')}\n")

    elif args.run:
        supervisor.run_supervisor()

    else:
        # Default action: show status and brief help
        st = supervisor.get_status()
        if not st.get("service_active"):
            print("\nVenice Key Manager is not running. Starting supervisor...\n")
            supervisor.run_supervisor()
        else:
            parser.print_help()


if __name__ == "__main__":
    main()

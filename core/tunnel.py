"""
Cloudflare Tunnel & DNS Automation Manager.
Safely connects local Venice Key Manager to Cloudflare DNS
without storing private domains or credentials in version control.
"""

import os
import sys
import json
import logging
import subprocess
import threading
import time
import re
from pathlib import Path
from typing import Dict, Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger("venice_tunnel")

CLOUDFLARED_CANDIDATE_PATHS = [
    Path.home() / ".gemini" / "antigravity" / "skills" / "secure-share" / "scripts" / "cloudflared.exe",
    Path(__file__).resolve().parent / "cloudflared.exe",
    Path(__file__).resolve().parent / "cloudflared"
]


class CloudflareTunnelManager:
    def __init__(self, vault=None):
        self.vault = vault
        self._process: Optional[subprocess.Popen] = None
        self._public_url: Optional[str] = None
        self._is_running = False
        self._status_details: Dict[str, Any] = {
            "configured_domain": self.get_configured_domain(),
            "active_url": None,
            "status": "stopped",
            "type": "none"
        }

    def get_configured_domain(self) -> str:
        if self.vault and hasattr(self.vault, "get_cloudflare_config"):
            return self.vault.get_cloudflare_config().get("domain", "")
        return os.environ.get("CLOUDFLARE_DOMAIN", "")

    def get_tunnel_token(self) -> str:
        if self.vault and hasattr(self.vault, "get_cloudflare_config"):
            return self.vault.get_cloudflare_config().get("tunnel_token", "")
        return os.environ.get("CLOUDFLARE_TUNNEL_TOKEN", "")

    def find_cloudflared_binary(self) -> Optional[Path]:
        """Locates cloudflared executable."""
        # 1. Check custom path in environment
        env_path = os.environ.get("CLOUDFLARED_BIN")
        if env_path and Path(env_path).exists():
            return Path(env_path)

        # 2. Check candidate local paths
        for p in CLOUDFLARED_CANDIDATE_PATHS:
            if p.exists():
                return p

        # 3. Check system PATH
        import shutil
        bin_name = "cloudflared.exe" if sys.platform == "win32" else "cloudflared"
        found = shutil.which(bin_name)
        if found:
            return Path(found)

        return None

    def start_tunnel(self, local_port: int = 8844, daemon: bool = True) -> Dict[str, Any]:
        """Launches cloudflared tunnel to expose local_port."""
        if self._is_running and self._process:
            return {
                "success": True,
                "status": "running",
                "domain": self.get_configured_domain(),
                "public_url": self._public_url or f"https://{self.get_configured_domain()}"
            }

        bin_path = self.find_cloudflared_binary()
        if not bin_path:
            logger.error("cloudflared binary not found on this system.")
            return {
                "success": False,
                "error": "cloudflared binary not found. Place cloudflared in scripts/ or PATH."
            }

        token = self.get_tunnel_token()
        domain = self.get_configured_domain()

        if token:
            # Token-based Cloudflare named tunnel
            cmd = [str(bin_path), "tunnel", "run", "--token", token]
            tunnel_type = "named_token"
        else:
            # Ephemeral / direct URL tunnel with DNS proxying
            cmd = [str(bin_path), "tunnel", "--url", f"http://127.0.0.1:{local_port}"]
            tunnel_type = "url_proxy"

        logger.info(f"Starting cloudflared ({tunnel_type}) targeting 127.0.0.1:{local_port}...")

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            self._is_running = True
            self._status_details = {
                "configured_domain": domain,
                "active_url": f"https://{domain}" if domain else None,
                "status": "starting",
                "type": tunnel_type
            }

            # Thread to monitor stdout and extract ephemeral or confirmed URLs
            def monitor_output():
                domain_pat = re.escape(domain) if domain else r"[a-zA-Z0-9.-]+"
                url_pattern = re.compile(r"https://[a-zA-Z0-9.-]+\.(?:trycloudflare\.com|" + domain_pat + ")")
                while self._is_running and self._process and self._process.poll() is None:
                    line = self._process.stdout.readline()
                    if not line:
                        break
                    match = url_pattern.search(line)
                    if match:
                        found_url = match.group(0)
                        self._public_url = found_url
                        self._status_details["active_url"] = found_url
                        self._status_details["status"] = "online"
                        logger.info(f"[Cloudflare Tunnel Established] {found_url}")
                    elif "Registered tunnel connection" in line or "Connection established" in line:
                        self._status_details["status"] = "online"
                        if not self._public_url and domain:
                            self._public_url = f"https://{domain}"
                            self._status_details["active_url"] = self._public_url

                self._is_running = False
                self._status_details["status"] = "stopped"

            t = threading.Thread(target=monitor_output, daemon=True)
            t.start()

            # Give it a moment to establish
            time.sleep(1.5)

            return {
                "success": True,
                "status": "online" if self._is_running else "starting",
                "configured_domain": domain,
                "public_url": self._public_url or (f"https://{domain}" if domain else "Initializing..."),
                "tunnel_type": tunnel_type
            }

        except Exception as e:
            self._is_running = False
            self._status_details["status"] = "error"
            self._status_details["error"] = str(e)
            return {"success": False, "error": str(e)}

    def get_status(self) -> Dict[str, Any]:
        running = self._is_running and self._process and (self._process.poll() is None)
        return {
            "success": True,
            "running": running,
            "configured_domain": self.get_configured_domain(),
            "active_url": self._public_url or (f"https://{self.get_configured_domain()}" if running else None),
            "status": "online" if running else "offline",
            "details": self._status_details
        }

    def stop_tunnel(self) -> bool:
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
        self._is_running = False
        self._public_url = None
        self._status_details["status"] = "stopped"
        return True


if __name__ == "__main__":
    mgr = CloudflareTunnelManager()
    print("Cloudflare Binary:", mgr.find_cloudflared_binary())
    print("Configured Domain:", mgr.get_configured_domain())

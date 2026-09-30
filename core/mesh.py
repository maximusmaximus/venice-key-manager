"""
Tailscale A2A Fleet Mesh Manager & Peer Node Delegation.
Enables multi-node discovery, health monitoring, peer request proxying,
and git-based fleet synchronization across Windows, macOS (mcmini), and Linux nodes.
"""

import os
import sys
import json
import logging
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger("venice_mesh")

DEFAULT_FLEET_NODES = {
    "local": {
        "name": "local",
        "label": "Local Engine (Self)",
        "ip": "127.0.0.1",
        "port": 8844,
        "base_url": "http://127.0.0.1:8844",
        "os": sys.platform,
        "is_self": True
    },
    "mcmini": {
        "name": "mcmini",
        "label": "McMini Node (Tailscale)",
        "ip": os.environ.get("MCMINI_TAILSCALE_IP", "100.64.0.2"),
        "dns": os.environ.get("MCMINI_TAILSCALE_DNS", "mcmini.tailnet.ts.net"),
        "port": int(os.environ.get("MCMINI_PORT", "8844")),
        "base_url": os.environ.get("MCMINI_URL", "http://100.64.0.2:8844"),
        "os": "darwin/windows",
        "is_self": False
    }
}


class FleetMeshManager:
    def __init__(self, vault=None):
        self.vault = vault
        self._custom_nodes: Dict[str, Dict[str, Any]] = {}
        self._load_fleet_nodes()

    def _load_fleet_nodes(self):
        if self.vault and hasattr(self.vault, "data"):
            nodes_data = self.vault.data.get("fleet_nodes", {})
            self._custom_nodes = dict(nodes_data)

    def _save_fleet_nodes(self):
        if self.vault and hasattr(self.vault, "data"):
            self.vault.data["fleet_nodes"] = self._custom_nodes
            if hasattr(self.vault, "_save"):
                self.vault._save()

    def list_nodes(self, check_health: bool = False) -> List[Dict[str, Any]]:
        """List all fleet nodes with optional live health checks."""
        merged: Dict[str, Dict[str, Any]] = {}
        for k, v in DEFAULT_FLEET_NODES.items():
            merged[k] = dict(v)
        for k, v in self._custom_nodes.items():
            merged[k] = {**merged.get(k, {}), **v}

        result = []
        for name, info in merged.items():
            node = dict(info)
            if check_health:
                health = self.check_node_health(name, node.get("base_url"))
                node["online"] = health.get("online", False)
                node["status"] = "online" if health.get("online") else "offline"
                node["latency_ms"] = health.get("latency_ms")
                node["version"] = health.get("version")
                node["error"] = health.get("error")
            else:
                node["online"] = node.get("is_self", False)
                node["status"] = "online" if node.get("is_self") else "unprobed"
            result.append(node)
        return result

    def get_node(self, name: str) -> Optional[Dict[str, Any]]:
        nodes = {n["name"]: n for n in self.list_nodes(check_health=False)}
        return nodes.get(name)

    def register_node(self, name: str, base_url: str, label: str = "", ip: str = "", port: int = 8844) -> Dict[str, Any]:
        """Register or update a remote peer node."""
        clean_url = base_url.rstrip("/")
        node_info = {
            "name": name,
            "label": label or f"Node: {name}",
            "base_url": clean_url,
            "ip": ip or clean_url.replace("http://", "").replace("https://", "").split(":")[0],
            "port": port,
            "is_self": False
        }
        self._custom_nodes[name] = node_info
        self._save_fleet_nodes()
        return {"success": True, "node": node_info}

    def remove_node(self, name: str) -> bool:
        if name in self._custom_nodes:
            del self._custom_nodes[name]
            self._save_fleet_nodes()
            return True
        return False

    def check_node_health(self, name: str, base_url: str = None) -> Dict[str, Any]:
        """Probe remote node's /api/stats or /api/version over Tailscale."""
        import time
        if not base_url:
            node = self.get_node(name)
            if not node:
                return {"online": False, "status": "offline", "error": f"Node '{name}' not found"}
            base_url = node.get("base_url")

        url = f"{base_url.rstrip('/')}/api/version"
        start_t = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Venice-Mesh/1.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                latency = round((time.time() - start_t) * 1000)
                data = json.loads(resp.read().decode("utf-8"))
                return {
                    "online": True,
                    "status": "online",
                    "latency_ms": latency,
                    "version": {
                        "commit": data.get("commit", "unknown"),
                        "branch": data.get("branch", "main")
                    },
                    "details": data
                }
        except Exception:
            # Fallback to /api/stats probe
            stats_url = f"{base_url.rstrip('/')}/api/stats"
            try:
                req = urllib.request.Request(stats_url, headers={"User-Agent": "Venice-Mesh/1.0"})
                with urllib.request.urlopen(req, timeout=3) as resp:
                    latency = round((time.time() - start_t) * 1000)
                    data = json.loads(resp.read().decode("utf-8"))
                    return {
                        "online": True,
                        "status": "online",
                        "latency_ms": latency,
                        "version": {
                            "commit": "legacy",
                            "branch": "main"
                        },
                        "details": data
                    }
            except Exception as e:
                return {"online": False, "status": "offline", "error": str(e), "latency_ms": None, "version": {}}

    def forward_request(self, node_name: str, endpoint: str, method: str = "GET", payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Forward an API request to a remote peer node over Tailscale."""
        node = self.get_node(node_name)
        if not node:
            return {"success": False, "error": f"Target node '{node_name}' not registered in fleet"}

        clean_ep = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        target_url = f"{node['base_url'].rstrip('/')}{clean_ep}"

        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json", "User-Agent": "Venice-Mesh-Proxy/1.0"}
        req = urllib.request.Request(target_url, data=body, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                content = resp.read().decode("utf-8")
                try:
                    return json.loads(content)
                except Exception:
                    return {"success": True, "raw": content}
        except urllib.error.HTTPError as e:
            content = e.read().decode("utf-8")
            try:
                err_json = json.loads(content)
                return {"success": False, "status_code": e.code, **err_json}
            except Exception:
                return {"success": False, "status_code": e.code, "error": content or str(e)}
        except Exception as e:
            return {"success": False, "error": f"Failed to reach node '{node_name}' at {target_url}: {e}"}

    def sync_local_code(self) -> Dict[str, Any]:
        """Pull latest code from GitHub and report commit status."""
        try:
            # 1. Get current commit hash before pull
            before_hash = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(PROJECT_ROOT),
                stderr=subprocess.STDOUT
            ).decode().strip()

            # 2. Run git pull origin main
            pull_output = subprocess.check_output(
                ["git", "pull", "origin", "main"],
                cwd=str(PROJECT_ROOT),
                stderr=subprocess.STDOUT
            ).decode().strip()

            # 3. Get current commit hash after pull
            after_hash = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(PROJECT_ROOT),
                stderr=subprocess.STDOUT
            ).decode().strip()

            updated = before_hash != after_hash or "Already up to date" not in pull_output

            return {
                "success": True,
                "node": "local",
                "before_commit": before_hash,
                "current_commit": after_hash,
                "updated": updated,
                "output": pull_output
            }
        except subprocess.CalledProcessError as e:
            err_msg = e.output.decode() if hasattr(e, "output") else str(e)
            return {"success": False, "node": "local", "error": err_msg}
        except Exception as e:
            return {"success": False, "node": "local", "error": str(e)}

    def sync_remote_node(self, node_name: str) -> Dict[str, Any]:
        """Trigger code synchronization on a remote peer node."""
        if node_name == "local":
            return self.sync_local_code()
        return self.forward_request(node_name, "/api/fleet/sync", method="POST")

    def sync_all_nodes(self) -> Dict[str, Any]:
        """Trigger sync on local node and all registered fleet peer nodes."""
        results = {"local": self.sync_local_code()}
        for node in self.list_nodes(check_health=False):
            name = node["name"]
            if name != "local":
                results[name] = self.sync_remote_node(name)
        return {"success": True, "results": results}

    def get_version_info(self) -> Dict[str, Any]:
        """Get current local git commit, branch, and version metadata."""
        try:
            commit = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(PROJECT_ROOT),
                stderr=subprocess.DEVNULL
            ).decode().strip()
            branch = subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(PROJECT_ROOT),
                stderr=subprocess.DEVNULL
            ).decode().strip()
        except Exception:
            commit = "2c8508b"
            branch = "main"

        return {
            "success": True,
            "version": "1.2.0",
            "commit": commit,
            "branch": branch,
            "node_name": "local",
            "os": sys.platform,
            "upstream": "https://github.com/maximusmaximus/venice-key-manager.git",
            "repo_url": "https://github.com/maximusmaximus/venice-key-manager.git"
        }

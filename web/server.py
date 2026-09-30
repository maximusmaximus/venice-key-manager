"""
Web Server for Venice.ai & Telegram Key Management Dashboard.
Provides a multi-threaded HTTP server and REST API for real-time monitoring and control.
"""

import sys
import json
import logging
import hashlib
import threading
from datetime import datetime
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from typing import Dict, Any, Optional

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.vault import KeyVault
from core.venice_client import VeniceClient
from core.tg_manager import TelegramAgentManager
from core.deployer import ConfigDeployer
from core.tiers import MODEL_TIER_ORDER, MODEL_TIER_MAPPING, is_tier_allowed, resolve_model_tier, get_model_for_tier
from core.mesh import FleetMeshManager

logger = logging.getLogger("venice_web")
STATIC_DIR = Path(__file__).resolve().parent / "static"


class DashboardRequestHandler(SimpleHTTPRequestHandler):
    vault = KeyVault()
    tg_manager = TelegramAgentManager(vault)
    mesh = FleetMeshManager(vault)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def _get_venice_client(self) -> VeniceClient:
        return VeniceClient(
            admin_key=self.vault.get_venice_admin_key(),
            inference_key=self.vault.get_venice_inference_key(),
            base_url=self.vault.data.get("venice", {}).get("base_url", "")
        )

    def _send_json(self, data: Any, status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length > 0:
            raw = self.rfile.read(length).decode("utf-8")
            return json.loads(raw)
        return {}

    def do_GET(self):
        client = self._get_venice_client()
        raw_path = self.path
        path = raw_path.split("?")[0]
        query = {}
        if "?" in raw_path:
            for part in raw_path.split("?")[1].split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    query[k] = v

        # Peer node forwarding for GET requests
        target_node = query.get("node")
        if target_node and target_node != "local":
            clean_rel = raw_path.replace(f"node={target_node}", "").replace("&&", "&").rstrip("?&")
            res = self.mesh.forward_request(target_node, clean_rel, method="GET")
            self._send_json(res)
            return

        if path == "/api/version":
            self._send_json(self.mesh.get_version_info())

        elif path == "/api/fleet/nodes":
            probe = query.get("probe") in ("1", "true")
            self._send_json({
                "success": True,
                "nodes": self.mesh.list_nodes(check_health=probe)
            })

        elif path == "/api/stats":
            rate_res = client.get_rate_limits()
            balances = rate_res.get("balances", {}) if rate_res.get("success") else {"USD": 0, "DIEM": 0}
            tier = rate_res.get("apiTier", {}) if rate_res.get("success") else {}
            next_epoch = rate_res.get("nextEpochBegins") if rate_res.get("success") else None

            # Get registered keys and agent bots
            vault_keys = self.vault.get_venice_keys()
            agent_bots = self.vault.get_agent_bots()

            self._send_json({
                "success": True,
                "balances": balances,
                "apiTier": tier,
                "nextEpochBegins": next_epoch,
                "has_admin_key": bool(self.vault.get_venice_admin_key()),
                "has_inference_key": bool(self.vault.get_venice_inference_key()),
                "keys_count": len(vault_keys),
                "agent_bots_count": len(agent_bots)
            })

        elif path == "/api/keys":
            admin_key = self.vault.get_venice_admin_key()
            remote_res = client.list_keys(admin_key=admin_key) if admin_key else {"keys": []}
            vault_keys = self.vault.get_venice_keys()

            # Merge remote and vault keys by id
            merged_dict = {}
            for k in vault_keys:
                merged_dict[k.get("id")] = k
            for k in remote_res.get("keys", []):
                merged_dict[k.get("id")] = {**merged_dict.get(k.get("id"), {}), **k}

            self._send_json({
                "success": True,
                "requires_admin": remote_res.get("requires_admin", not bool(admin_key)),
                "keys": list(merged_dict.values())
            })

        elif path == "/api/rate_limits":
            res = client.get_rate_limits()
            if res.get("success"):
                raw = res.get("raw", {})
                self._send_json({
                    "success": True,
                    "rateLimits": raw.get("rateLimits", []),
                    "apiTier": raw.get("apiTier", {}),
                    "nextEpochBegins": raw.get("nextEpochBegins")
                })
            else:
                self._send_json(res, status=400)

        elif path == "/api/models":
            res = client.list_models()
            self._send_json(res)

        elif path == "/api/model_tiers":
            self._send_json({
                "success": True,
                "tier_order": MODEL_TIER_ORDER,
                "tiers": MODEL_TIER_MAPPING
            })

        elif path == "/api/agent_bots":
            bots = self.tg_manager.list_agent_bots(check_live_status=False)
            self._send_json({"success": True, "agent_bots": bots})

        elif path == "/api/config":
            self._send_json({
                "success": True,
                "admin_key_set": bool(self.vault.get_venice_admin_key()),
                "admin_key_preview": f"***{self.vault.get_venice_admin_key()[-4:]}" if self.vault.get_venice_admin_key() else "",
                "inference_key_set": bool(self.vault.get_venice_inference_key()),
                "inference_key_preview": f"***{self.vault.get_venice_inference_key()[-4:]}" if self.vault.get_venice_inference_key() else "",
                "authorized_chat_id": self.vault.get_authorized_chat_id(),
                "base_url": self.vault.data.get("venice", {}).get("base_url")
            })

        else:
            # Serve static files
            super().do_GET()

    def do_POST(self):
        client = self._get_venice_client()
        body = self._read_json_body()
        path = self.path.split("?")[0]

        # Peer node forwarding for POST requests
        target_node = body.get("target_node")
        if target_node and target_node != "local":
            res = self.mesh.forward_request(target_node, path, method="POST", payload=body)
            self._send_json(res)
            return

        if path == "/api/fleet/sync":
            node = body.get("node", "local")
            if node == "all":
                res = self.mesh.sync_all_nodes()
            elif node == "local":
                res = self.mesh.sync_local_code()
            else:
                res = self.mesh.sync_remote_node(node)
            self._send_json(res)

        elif path == "/api/fleet/register_node":
            name = body.get("name", "")
            base_url = body.get("base_url", "")
            label = body.get("label", "")
            ip = body.get("ip", "")
            port = int(body.get("port", 8844))
            res = self.mesh.register_node(name, base_url, label=label, ip=ip, port=port)
            self._send_json(res)

        elif path == "/api/fleet/remove_node":
            name = body.get("name", "")
            removed = self.mesh.remove_node(name)
            self._send_json({"success": removed, "name": name})

        elif path in ("/api/create_key", "/api/keys/import"):
            mode = body.get("mode")
            key_str = body.get("key_string", "").strip()
            desc = body.get("description", "Agent Key")
            k_type = body.get("key_type", "INFERENCE").upper()
            limit = body.get("limit_usd")
            period = body.get("limit_period", "MONTH")
            expires = body.get("expires_at")
            admin_key_param = body.get("admin_key", "").strip()
            max_tier = (body.get("max_model_tier") or "xl").lower().strip()
            if max_tier not in MODEL_TIER_ORDER:
                max_tier = "xl"

            # Mode 1: Import existing key into vault
            if mode == "import" or (key_str and mode != "generate"):
                if not key_str:
                    self._send_json({"success": False, "error": "API Key string cannot be empty"}, status=400)
                    return

                # Validate key against Venice live endpoint
                rate_res = client.get_rate_limits(key=key_str)
                if not rate_res.get("success"):
                    err_msg = rate_res.get("error", "Key verification failed")
                    if rate_res.get("status_code") == 401:
                        err_msg = "Venice rejected this API key (401 Unauthorized). Verify the key is valid."
                    self._send_json({"success": False, "error": err_msg, "status_code": rate_res.get("status_code", 400)})
                    return

                # Generate a stable key identifier
                h = hashlib.sha256(key_str.encode()).hexdigest()[:10]
                key_id = f"vk_{h}"

                key_obj = {
                    "id": key_id,
                    "apiKey": key_str,
                    "description": desc or "Imported Venice Key",
                    "apiKeyType": k_type,
                    "maxModelTier": max_tier,
                    "consumptionLimits": {"usd": float(limit)} if limit else {},
                    "usage": {"trailingSevenDays": {"usd": 0.0}},
                    "createdAt": datetime.utcnow().isoformat() + "Z",
                    "status": "ACTIVE",
                    "balances": rate_res.get("balances", {}),
                    "apiTier": rate_res.get("apiTier", {})
                }
                self.vault.store_venice_key(key_obj)

                # If imported key is marked ADMIN, update vault admin key
                if k_type == "ADMIN":
                    self.vault.set_venice_admin_key(key_str)
                if not self.vault.get_venice_inference_key():
                    self.vault.set_venice_inference_key(key_str)

                self._send_json({
                    "success": True,
                    "imported": True,
                    "key": key_obj,
                    "message": "Key imported and verified successfully"
                })
                return

            # Mode 2: Generate remotely via Venice Admin API
            if admin_key_param:
                self.vault.set_venice_admin_key(admin_key_param)

            active_admin = self.vault.get_venice_admin_key()
            if not active_admin:
                self._send_json({
                    "success": False,
                    "error": "Venice Admin API Key is required to issue keys remotely. Enter your Admin Key or import an existing key.",
                    "requires_admin": True
                })
                return

            res = client.create_key(
                description=desc,
                key_type=k_type,
                limit_usd=float(limit) if limit else None,
                limit_period=period,
                expires_at=expires,
                admin_key=active_admin
            )
            if res.get("success"):
                key_obj = res.get("key", {})
                key_obj["maxModelTier"] = max_tier
                self.vault.store_venice_key(key_obj)
            self._send_json(res)

        elif path == "/api/revoke_key":
            key_id = body.get("key_id", "")
            removed_from_vault = self.vault.remove_venice_key(key_id)

            remote_res = {}
            if self.vault.get_venice_admin_key() and not key_id.startswith("vk_"):
                remote_res = client.delete_key(key_id=key_id)

            if removed_from_vault or remote_res.get("success"):
                self._send_json({"success": True, "key_id": key_id, "message": "Key revoked and removed from vault"})
            else:
                self._send_json({"success": False, "error": remote_res.get("error", "Key not found in vault")})

        elif path == "/api/deploy_key":
            key_str = body.get("key_string", "")
            target_path = body.get("target_path")
            # If key_str is an ID, find actual secret apiKey in vault
            for k in self.vault.get_venice_keys():
                if k.get("id") == key_str and k.get("apiKey"):
                    key_str = k.get("apiKey")
                    break
            res = ConfigDeployer.deploy_venice_key(key_str, Path(target_path) if target_path else None)
            self._send_json(res)

        elif path == "/api/infer":
            prompt = body.get("prompt", "")
            model = body.get("model", "deepseek-v4-flash")
            max_tokens = int(body.get("max_tokens", 120))
            temperature = float(body.get("temperature", 0.7))
            key_id = body.get("key_id", "")

            # Check model tier gating if key_id is provided
            if key_id:
                key_entry = next((k for k in self.vault.get_venice_keys() if k.get("id") == key_id or k.get("apiKey") == key_id), None)
                if key_entry:
                    key_max_tier = key_entry.get("maxModelTier", "xl")
                    req_tier = resolve_model_tier(model)
                    if not is_tier_allowed(req_tier, key_max_tier):
                        self._send_json({
                            "success": False,
                            "error": f"Model tier '{req_tier.upper()}' ({model}) exceeds key's maximum allowed tier '{key_max_tier.upper()}'. Access denied.",
                            "requested_tier": req_tier,
                            "max_tier": key_max_tier
                        }, status=403)
                        return

            res = client.test_inference(prompt=prompt, model=model, max_tokens=max_tokens, temperature=temperature)
            self._send_json(res)

        elif path == "/api/agent_bots/register":
            agent_name = body.get("agent_name", "")
            bot_token = body.get("bot_token", "")
            config_path = body.get("config_path", "")
            notes = body.get("notes", "")
            res = self.tg_manager.register_agent_bot(agent_name, bot_token, config_path=config_path, notes=notes)
            self._send_json(res)

        elif path == "/api/agent_bots/deploy":
            agent_name = body.get("agent_name", "")
            target_path = body.get("target_path")
            res = self.tg_manager.deploy_agent_bot(agent_name, target_path=target_path)
            self._send_json(res)

        elif path == "/api/agent_bots/test":
            agent_name = body.get("agent_name", "")
            bot = self.vault.get_agent_bot(agent_name)
            if not bot:
                self._send_json({"success": False, "error": f"Agent '{agent_name}' not found"}, status=404)
                return
            res = self.tg_manager.send_test_message(bot["bot_token"])
            self._send_json(res)

        elif path == "/api/agent_bots/botfather_wizard":
            agent_name = body.get("agent_name", "hermes-agent")
            sug_name = body.get("suggested_name", "")
            res = self.tg_manager.generate_botfather_wizard(agent_name, suggested_name=sug_name)
            self._send_json({"success": True, "wizard": res})

        elif path == "/api/config":
            if "admin_key" in body:
                self.vault.set_venice_admin_key(body["admin_key"])
            if "inference_key" in body:
                self.vault.set_venice_inference_key(body["inference_key"])
            if "authorized_chat_id" in body:
                self.vault.data.setdefault("telegram", {})["authorized_chat_id"] = str(body["authorized_chat_id"])
                self.vault._save()
            self._send_json({"success": True, "message": "Configuration updated"})

        else:
            self._send_json({"error": "Endpoint not found"}, status=404)


def start_web_server(host: str = "0.0.0.0", port: int = 8844, daemon: bool = False) -> ThreadingHTTPServer:
    server_address = (host, port)
    httpd = ThreadingHTTPServer(server_address, DashboardRequestHandler)
    print(f"⚡ Venice & Telegram Key Management Dashboard running at http://localhost:{port}")
    if daemon:
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        return httpd
    else:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down web server...")
            httpd.shutdown()
        return httpd


if __name__ == "__main__":
    start_web_server(port=8844, daemon=False)

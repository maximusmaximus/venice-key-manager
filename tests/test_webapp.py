"""
End-to-end tests for the web app server, its hardening, the new API endpoints,
and the supporting core fixes (mesh auth forwarding, vault cross-instance
reload, supervisor sandboxing, Telegram command dispatch).

Run: python -m unittest tests/test_webapp.py
"""

import os
import sys
import json
import shutil
import tempfile
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch, MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.vault import KeyVault
from core.venice_client import VeniceClient
from core.mesh import FleetMeshManager
from core.supervisor import ServiceSupervisor
import web.server as web_server
from web.server import DashboardRequestHandler, start_web_server, redact

PAIR = "TEST-PAIR+CODE 7"   # contains characters that must be URL-decoded correctly
FAKE_KEY = "VENICE_INFERENCE_KEY_unit_test_secret_value_123456"


def _rate_limits_ok(usd=0.5):
    return {
        "success": True,
        "balances": {"USD": usd, "DIEM": 0.0, "BUNDLED_CREDITS": 0.0},
        "apiTier": {"id": "paid"},
        "nextEpochBegins": "2026-10-04T00:00:00Z",
        "raw": {"rateLimits": [], "apiTier": {"id": "paid"}},
    }


class WebAppServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="vkm_webapp_"))
        cls.vault = KeyVault(vault_path=cls.tmp / "venice_vault.json")
        cls.vault.set_pairing_code(PAIR)
        cls.vault.set_venice_inference_key(FAKE_KEY)
        cls._old_vault = DashboardRequestHandler.vault
        cls._old_tunnel = DashboardRequestHandler.tunnel_manager
        cls.httpd = start_web_server(host="127.0.0.1", port=0, daemon=True, vault=cls.vault)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.patcher = patch.object(VeniceClient, "get_rate_limits", return_value=_rate_limits_ok())
        cls.patcher.start()

    @classmethod
    def tearDownClass(cls):
        cls.patcher.stop()
        cls.httpd.shutdown()
        cls.httpd.server_close()
        DashboardRequestHandler.vault = cls._old_vault
        DashboardRequestHandler.tunnel_manager = cls._old_tunnel
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    # ------------------------------------------------------------ helpers
    def req(self, path, method="GET", body=None, code=None, raw_body=None, headers=None):
        data = raw_body if raw_body is not None else (json.dumps(body).encode() if body is not None else None)
        hdrs = {"Content-Type": "application/json"}
        if code:
            hdrs["X-Pairing-Code"] = code
        hdrs.update(headers or {})
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def jreq(self, path, method="GET", body=None, code=PAIR, **kw):
        status, headers, raw = self.req(path, method=method, body=body, code=code, **kw)
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            data = None
        return status, data

    @property
    def code(self):
        return self.vault.get_pairing_code()

    # ------------------------------------------------------------ static app
    def test_01_index_serves_new_app_with_guest_gate_and_security_headers(self):
        status, headers, body = self.req("/")
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertIn("text/html", headers.get("Content-Type", ""))
        for marker in ("VENICE // TG ENGINE", "guest-gate-card", "input-guest-key", "btn-guest-validate-key",
                       "authenticated-view", "/assets/js/app.js"):
            self.assertIn(marker, html)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertIn("default-src 'self'", headers.get("Content-Security-Policy", ""))
        # no third-party fonts / CDNs in the new app
        self.assertNotIn("googleapis", html)

    def test_02_static_mime_types(self):
        cases = {
            "/assets/js/app.js": "text/javascript",
            "/assets/js/views/overview.js": "text/javascript",
            "/assets/app.css": "text/css",
            "/manifest.webmanifest": "application/manifest+json",
            "/assets/icon.svg": "image/svg+xml",
        }
        for path, ctype in cases.items():
            status, headers, _ = self.req(path)
            self.assertEqual(status, 200, path)
            self.assertIn(ctype, headers.get("Content-Type", ""), path)

    def test_03_path_traversal_and_private_files_blocked(self):
        for path in ("/../venice_vault.json", "/%2e%2e/venice_vault.json", "/assets/../../core/vault.py",
                     "/legacy/../../venice_vault.json", "/venice_vault.json", "/..%5c..%5cvenice_vault.json"):
            status, _, body = self.req(path)
            self.assertEqual(status, 404, path)
            self.assertNotIn(b"inference_key", body)

    def test_04_legacy_dashboard_still_served(self):
        status, headers, body = self.req("/legacy/")
        self.assertEqual(status, 200)
        self.assertIn(b"app.js", body)
        status, headers, _ = self.req("/legacy/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers.get("Content-Type", ""))

    # ------------------------------------------------------------ auth gate
    def test_05_new_endpoints_require_pairing(self):
        for path in ("/api/overview", "/api/balance", "/api/pairing/code", "/api/mcp/tools", "/api/keys", "/api/subkeys"):
            status, data = self.jreq(path, code=None)
            self.assertEqual(status, 401, path)
            self.assertTrue(data.get("requires_pairing"))
        for path in ("/api/pairing/rotate", "/api/mcp/call", "/api/mcp/rpc", "/api/balance/thresholds",
                     "/api/subkeys/remove", "/api/agent_bots/remove", "/api/vault/restore"):
            status, data = self.jreq(path, method="POST", body={}, code=None)
            self.assertEqual(status, 401, path)
        # wrong code also rejected
        status, _ = self.jreq("/api/overview", code="WRONG")
        self.assertEqual(status, 401)

    def test_06_query_string_is_url_decoded(self):
        from urllib.parse import quote
        status, data = self.jreq("/api/pairing_status?pairing_code=" + quote(self.code), code=None)
        self.assertEqual(status, 200)
        self.assertTrue(data["authenticated"])
        status, data = self.jreq("/api/overview?pairing_code=" + quote(self.code), code=None)
        self.assertEqual(status, 200)

    def test_07_malformed_json_returns_400(self):
        status, data = self.jreq("/api/pair", method="POST", raw_body=b"{not json", code=None)
        self.assertEqual(status, 400)
        self.assertFalse(data["success"])
        status, data = self.jreq("/api/pair", method="POST", raw_body=b"[1,2]", code=None)
        self.assertEqual(status, 400)

    def test_08_unknown_api_returns_json_404(self):
        status, data = self.jreq("/api/does-not-exist")
        self.assertEqual(status, 404)
        status, data = self.jreq("/api/does-not-exist", method="POST", body={})
        self.assertEqual(status, 404)

    # ------------------------------------------------------------ claim portal
    def test_09_claim_portal_escapes_untrusted_values(self):
        evil_label = "<script>alert('x')</script>"
        evil_agent = "\"><img src=x onerror=alert(1)>"
        status, data = self.jreq("/api/allocations/mint", method="POST",
                                 body={"label": evil_label, "target_agent": evil_agent, "allocated_keys_count": 2})
        self.assertEqual(status, 200)
        self.assertTrue(data["claim_url"].startswith("https://"))
        self.assertIn("/claim/vclm_", data["claim_url"])
        # raw key never in mint response
        self.assertNotIn(FAKE_KEY, json.dumps(data))
        token = data["claim_token"]

        status, headers, body = self.req(f"/claim/{token}")
        html = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertNotIn("<script>alert", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn(FAKE_KEY, html)
        self.assertIn("default-src 'self'", headers.get("Content-Security-Policy", ""))

        status, headers, body = self.req("/claim/%3Cscript%3Ealert(1)%3C%2Fscript%3E")
        self.assertEqual(status, 404)
        self.assertNotIn(b"<script>alert", body)

        # JSON form for agents
        status, data = self.jreq(f"/claim/{token}?format=json", code=None)
        self.assertEqual(status, 200)
        self.assertEqual(data["allocated_keys_count"], 2)
        self.assertEqual(data["remaining_claims"], 2)

    def test_10_claim_auto_deploy_requires_pairing(self):
        status, data = self.jreq("/api/allocations/mint", method="POST", body={"target_agent": "unit-agent"})
        token = data["claim_token"]
        with patch.object(web_server.ConfigDeployer, "deploy_venice_key") as dep:
            status, res = self.jreq("/api/allocations/claim", method="POST",
                                    body={"claim_token": token, "auto_deploy": True}, code=None)
            self.assertEqual(status, 200)
            self.assertEqual(res["api_key"], FAKE_KEY)
            dep.assert_not_called()

    # ------------------------------------------------------------ vault restore
    def test_11_restore_only_known_backups(self):
        status, data = self.jreq("/api/vault/restore", method="POST", body={"path": str(PROJECT_ROOT / "README.md")})
        self.assertEqual(status, 404)
        status, data = self.jreq("/api/vault/backup", method="POST", body={"label": "unit/../evil"})
        self.assertTrue(data["success"])
        self.assertNotIn("/", data["filename"])
        self.assertNotIn("..", data["filename"])
        status, data = self.jreq("/api/vault/restore", method="POST", body={"filename": data["filename"]})
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])

    # ------------------------------------------------------------ redaction
    def test_12_secrets_redacted_from_lists(self):
        self.vault.store_venice_key({"id": "vk_unit", "apiKey": FAKE_KEY, "description": "Unit key"})
        status, data = self.jreq("/api/keys")
        self.assertEqual(status, 200)
        blob = json.dumps(data)
        self.assertNotIn(FAKE_KEY, blob)
        unit = next(k for k in data["keys"] if k["id"] == "vk_unit")
        self.assertTrue(unit["has_apiKey"])
        self.assertTrue(unit["apiKey_preview"].endswith(FAKE_KEY[-4:]))

        status, data = self.jreq("/api/subkeys/create", method="POST",
                                 body={"name": "unit-sub", "budget_usd": 0.5, "max_model_tier": "m"})
        self.assertEqual(status, 200)
        self.assertNotIn(FAKE_KEY, json.dumps(data))
        status, data = self.jreq("/api/subkeys")
        self.assertNotIn(FAKE_KEY, json.dumps(data))

    def test_13_subkey_validation_and_removal(self):
        status, data = self.jreq("/api/subkeys/create", method="POST", body={"name": "bad", "max_model_tier": "zz"})
        self.assertEqual(status, 400)
        status, data = self.jreq("/api/subkeys/create", method="POST", body={"name": "bad", "budget_usd": "abc"})
        self.assertEqual(status, 400)
        status, data = self.jreq("/api/subkeys/create", method="POST", body={"name": "to-remove"})
        sk_id = data["subkey"]["id"]
        status, data = self.jreq("/api/subkeys/remove", method="POST", body={"id": sk_id})
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])
        status, data = self.jreq("/api/subkeys/remove", method="POST", body={"id": sk_id})
        self.assertEqual(status, 404)

    def test_14_agent_bot_removal(self):
        self.vault.register_agent_bot("unit-bot", {"bot_token": "123:SECRET_TOKEN_VALUE_XYZ"})
        status, data = self.jreq("/api/agent_bots")
        self.assertNotIn("SECRET_TOKEN_VALUE_XYZ", json.dumps(data))
        status, data = self.jreq("/api/agent_bots/remove", method="POST", body={"agent_name": "unit-bot"})
        self.assertEqual(status, 200)
        self.assertIsNone(self.vault.get_agent_bot("unit-bot"))

    # ------------------------------------------------------------ overview / balance
    def test_15_overview_and_balance(self):
        status, data = self.jreq("/api/overview")
        self.assertEqual(status, 200)
        self.assertEqual(data["balance"]["status"], "LOW")  # 0.5 USD < 1.0 default low threshold
        for key in ("service", "tunnel", "version", "vault", "counts"):
            self.assertIn(key, data)
        status, data = self.jreq("/api/balance")
        self.assertEqual(data["status"], "LOW")
        self.assertIn("thresholds", data)

    def test_16_thresholds_validation(self):
        status, data = self.jreq("/api/balance/thresholds", method="POST", body={"low_usd": 0.1, "out_usd": 0.5})
        self.assertEqual(status, 400)
        status, data = self.jreq("/api/balance/thresholds", method="POST", body={"low_usd": 0.25, "out_usd": 0.02})
        self.assertEqual(status, 200)
        self.assertEqual(self.vault.get_balance_thresholds()["low_usd"], 0.25)
        status, data = self.jreq("/api/balance")
        self.assertEqual(data["status"], "HEALTHY")  # 0.5 > 0.25
        self.vault.set_balance_thresholds(low_usd=1.0, out_usd=0.05)

    # ------------------------------------------------------------ MCP over HTTP
    def test_17_mcp_tools_call_and_rpc(self):
        status, data = self.jreq("/api/mcp/tools")
        self.assertEqual(status, 200)
        names = {t["name"] for t in data["tools"]}
        self.assertIn("venice_list_allocations", names)
        self.assertIn("venice_claim_allocated_key", names)

        status, data = self.jreq("/api/mcp/call", method="POST", body={"name": "venice_list_allocations", "arguments": {}})
        self.assertEqual(status, 200)
        self.assertTrue(data["result"]["success"])
        self.assertNotIn(FAKE_KEY, json.dumps(data))

        status, data = self.jreq("/api/mcp/call", method="POST", body={"name": "nope"})
        self.assertEqual(status, 404)
        status, data = self.jreq("/api/mcp/call", method="POST", body={"name": "venice_list_allocations", "arguments": "x"})
        self.assertEqual(status, 400)

        status, data = self.jreq("/api/mcp/rpc", method="POST", body={"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
        self.assertEqual(status, 200)
        self.assertEqual(data["id"], 7)
        self.assertTrue(len(data["result"]["tools"]) > 5)

    def test_18_mcp_shares_the_server_vault(self):
        status, data = self.jreq("/api/allocations/mint", method="POST", body={"target_agent": "shared-check"})
        status, data = self.jreq("/api/mcp/call", method="POST",
                                 body={"name": "venice_list_allocations", "arguments": {"target_agent": "shared-check"}})
        self.assertEqual(data["result"]["total"], 1)

    # ------------------------------------------------------------ pairing rotation (keep last)
    def test_99_pairing_rotation(self):
        old = self.code
        status, data = self.jreq("/api/pairing/code", code=old)
        self.assertEqual(data["pairing_code"], old)
        status, data = self.jreq("/api/pairing/rotate", method="POST", body={}, code=old)
        self.assertEqual(status, 200)
        new = data["pairing_code"]
        self.assertNotEqual(new, old)
        self.assertEqual(self.jreq("/api/overview", code=old)[0], 401)
        self.assertEqual(self.jreq("/api/overview", code=new)[0], 200)
        self.vault.set_pairing_code(PAIR)


class CoreFixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vkm_core_"))

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def test_redact_helper(self):
        out = redact({"a": [{"apiKey": "abcdefghijklmnopqrstuvwxyz"}], "bot_token": "1:2", "x": 1})
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", json.dumps(out))
        self.assertEqual(out["x"], 1)
        self.assertTrue(out["a"][0]["has_apiKey"])

    def test_vault_cross_instance_reload(self):
        p = self.tmp / "v.json"
        a = KeyVault(vault_path=p)
        b = KeyVault(vault_path=p)
        a.set_venice_inference_key("key-from-a-123456")
        # b must see a's write instead of overwriting it with stale memory
        self.assertEqual(b.get_venice_inference_key() if not os.environ.get("VENICE_INFERENCE_KEY") else "key-from-a-123456",
                         "key-from-a-123456")
        b.store_subkey({"id": "sk_b", "label": "from b"})
        self.assertEqual(a.data.get("venice", {}).get("inference_key"), "key-from-a-123456")
        self.assertTrue(any(s.get("id") == "sk_b" for s in a.get_subkeys()))
        self.assertFalse(a.is_primary)
        self.assertTrue(str(a.snapshot_dir).startswith(str(self.tmp)))

    def test_vault_pairing_rotation_and_unicode_compare(self):
        v = KeyVault(vault_path=self.tmp / "v.json")
        v.set_pairing_code("ABC")
        new = v.generate_new_pairing_code()
        self.assertTrue(new.startswith("VK-"))
        self.assertFalse(v.verify_pairing_code("ABC"))
        self.assertFalse(v.verify_pairing_code("ünïcødé"))  # must not raise

    def test_mesh_forwarding_sends_pairing_code_and_masks_it(self):
        v = KeyVault(vault_path=self.tmp / "v.json")
        mesh = FleetMeshManager(v)
        res = mesh.register_node("peer", "http://peer.example:8844", pairing_code="PEER-SECRET")
        self.assertNotIn("pairing_code", res["node"])
        self.assertTrue(res["node"]["has_pairing_code"])
        listed = {n["name"]: n for n in mesh.list_nodes()}
        self.assertNotIn("pairing_code", listed["peer"])
        self.assertTrue(listed["peer"]["has_pairing_code"])

        captured = {}

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"success": true}'

        def fake_urlopen(req, timeout=0):
            captured["headers"] = {k.lower(): v for k, v in req.header_items()}
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            return FakeResp()

        with patch("core.mesh.urllib.request.urlopen", side_effect=fake_urlopen):
            out = mesh.call_remote_node("peer", "/api/subkeys/apply", method="POST", data={"a": 1})
        self.assertTrue(out["success"])
        self.assertEqual(captured["headers"].get("x-pairing-code"), "PEER-SECRET")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["url"], "http://peer.example:8844/api/subkeys/apply")

        # Re-registering without a code keeps the stored one
        mesh.register_node("peer", "http://peer.example:8845")
        self.assertEqual(mesh.get_node("peer", include_secret=True)["pairing_code"], "PEER-SECRET")

    def test_supervisor_state_dir_env(self):
        with patch.dict(os.environ, {"VENICE_SUPERVISOR_STATE_DIR": str(self.tmp)}):
            sup = ServiceSupervisor()
            self.assertEqual(sup.sentinel_restart, self.tmp / ".restart_requested")
            sup.request_restart()
            self.assertTrue((self.tmp / ".restart_requested").exists())
        # explicit root_dir still wins
        sup2 = ServiceSupervisor(root_dir=self.tmp / "x")
        self.assertEqual(sup2.sentinel_restart, self.tmp / "x" / ".restart_requested")

    def test_test_suite_is_sandboxed(self):
        # tests/__init__.py must have redirected the live vault and supervisor state
        self.assertTrue(os.environ.get("VENICE_TEST_SANDBOX"))
        v = KeyVault()
        self.assertFalse(v.is_primary)
        self.assertNotEqual(ServiceSupervisor().sentinel_restart, PROJECT_ROOT / ".restart_requested")


class TelegramDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vkm_tg_"))
        self.vault = KeyVault(vault_path=self.tmp / "v.json")
        self.vault.set_pairing_code("OLD-CODE")
        self.vault.set_venice_inference_key("test_inf_key_abc123")
        from telegram.bot import VeniceTelegramBot
        self.bot = VeniceTelegramBot(vault=self.vault)
        self.bot.authorized_chat_id = "123456789"

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def _send(self, text):
        self.bot.handle_message({"chat": {"id": 123456789}, "text": text})

    @patch("telegram.bot.requests.post")
    def test_exact_command_matching(self, mock_post):
        mock_post.return_value.json.return_value = {"ok": True, "result": {"message_id": 1}}
        with patch.object(self.bot, "_handle_balance_command") as bal:
            self._send("/balance_threshold")
            bal.assert_not_called()
            self._send("/balance@SomeBot")
            bal.assert_called_once()

    @patch("telegram.bot.requests.post")
    def test_rotate_pair_code(self, mock_post):
        mock_post.return_value.json.return_value = {"ok": True, "result": {"message_id": 1}}
        self._send("/rotate_pair_code")
        new = self.vault.get_pairing_code()
        self.assertNotEqual(new, "OLD-CODE")
        texts = " ".join(str(c[1].get("json", {}).get("text", "")) for c in mock_post.call_args_list)
        self.assertIn(new, texts)

    @patch("telegram.bot.requests.post")
    def test_empty_message_ignored(self, mock_post):
        self.bot.handle_message({"chat": {"id": 123456789}})
        self.bot.handle_message({"chat": {"id": 123456789}, "text": "   "})
        mock_post.assert_not_called()


class PortBindingTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "SO_REUSEADDR double-bind only affects Windows")
    def test_second_server_cannot_share_port(self):
        first = web_server.VeniceHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
        try:
            port = first.server_address[1]
            with self.assertRaises(OSError):
                second = web_server.VeniceHTTPServer(("127.0.0.1", port), DashboardRequestHandler)
                second.server_close()
        finally:
            first.server_close()


class FrontendStaticTests(unittest.TestCase):
    VIEWS = PROJECT_ROOT / "web" / "app" / "assets" / "js" / "views"

    def test_table_calls_pass_rows(self):
        import re
        offenders = []
        for js in sorted(self.VIEWS.glob("*.js")):
            src = js.read_text(encoding="utf-8")
            # table(columns, rows, opts) - options directly after the column array means rows were forgotten.
            for m in re.finditer(r"\n[ \t]*\]\s*,\s*\{\s*caption", src):
                offenders.append("%s:%d" % (js.name, src.count("\n", 0, m.start()) + 2))
        self.assertEqual(offenders, [], "table() called without rows: %s" % offenders)


if __name__ == "__main__":
    unittest.main()

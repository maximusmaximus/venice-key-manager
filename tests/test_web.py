"""
Test for Web Dashboard Server and REST APIs.
"""

import sys
import io
import time
import requests
import unittest
from pathlib import Path

# Add project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web.server import start_web_server
from core.vault import KeyVault


class TestWebServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = start_web_server(port=8899, daemon=True)
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.server.shutdown()
            cls.server.server_close()
        except Exception:
            pass

    def test_01_public_gate_and_guest_isolation(self):
        r_html = requests.get("http://127.0.0.1:8899/")
        self.assertEqual(r_html.status_code, 200)
        self.assertIn("VENICE // TG ENGINE", r_html.text)
        self.assertIn('id="authenticated-view"', r_html.text)
        self.assertIn('id="guest-gate-card"', r_html.text)
        self.assertIn('id="input-guest-key"', r_html.text)
        self.assertIn('btn-guest-validate-key', r_html.text)

        r_pub = requests.get("http://127.0.0.1:8899/api/pairing_status")
        self.assertEqual(r_pub.status_code, 200)
        self.assertTrue(r_pub.json().get("requires_pairing"))

        # Unauthenticated privileged endpoints must strictly return 401
        self.assertEqual(requests.get("http://127.0.0.1:8899/api/stats").status_code, 401)
        self.assertEqual(requests.get("http://127.0.0.1:8899/api/keys").status_code, 401)
        self.assertEqual(requests.get("http://127.0.0.1:8899/api/subkeys").status_code, 401)
        self.assertEqual(requests.get("http://127.0.0.1:8899/api/vault/status").status_code, 401)

        # Public candidate key validator must be accessible without pairing
        r_val = requests.post("http://127.0.0.1:8899/api/validate_key", json={"api_key": ""})
        self.assertEqual(r_val.status_code, 400)

    def test_02_authenticated_endpoints(self):
        vault = KeyVault()
        pairing_code = vault.get_pairing_code()
        headers = {"X-Pairing-Code": pairing_code}

        r_stats = requests.get("http://127.0.0.1:8899/api/stats", headers=headers)
        self.assertEqual(r_stats.status_code, 200)
        self.assertTrue(r_stats.json().get("success"))

        r_vault_status = requests.get("http://127.0.0.1:8899/api/vault/status", headers=headers)
        self.assertEqual(r_vault_status.status_code, 200)
        self.assertTrue(r_vault_status.json().get("backup_mirror_exists"))

        r_backups = requests.get("http://127.0.0.1:8899/api/vault/backups", headers=headers)
        self.assertEqual(r_backups.status_code, 200)
        self.assertIn("backups", r_backups.json())

        r_bots = requests.get("http://127.0.0.1:8899/api/agent_bots", headers=headers)
        self.assertEqual(r_bots.status_code, 200)

        r_keys = requests.get("http://127.0.0.1:8899/api/keys", headers=headers)
        self.assertEqual(r_keys.status_code, 200)


if __name__ == "__main__":
    unittest.main()

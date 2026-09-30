"""
End-to-End Test Suite for Machine Pairing, Guest Key Validation, and Agent Sub-Keys.
"""

import sys
import os
import json
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.vault import KeyVault
from core.venice_client import VeniceClient
from core.deployer import ConfigDeployer
from web.server import start_web_server, DashboardRequestHandler
from telegram.bot import VeniceTelegramBot
from mcp.server import VeniceMCPServer


class TestPairingAndSubkeys(unittest.TestCase):
    def setUp(self):
        # Create a test vault with isolated temp json
        self.test_vault_path = PROJECT_ROOT / "tests" / "test_temp_vault.json"
        for p in (self.test_vault_path, self.test_vault_path.with_suffix(".backup.json")):
            if p.exists():
                try:
                    os.remove(p)
                except Exception:
                    pass
        self.vault = KeyVault(vault_path=self.test_vault_path)
        self.vault.set_pairing_code("TEST-PAIR-9999")
        self.vault.set_venice_inference_key("test_inf_key_abc123")

    def tearDown(self):
        for p in (self.test_vault_path, self.test_vault_path.with_suffix(".backup.json")):
            if p.exists():
                try:
                    os.remove(p)
                except Exception:
                    pass

    def test_vault_pairing_and_subkeys(self):
        """Verify vault pairing verification and subkey storage."""
        self.assertTrue(self.vault.verify_pairing_code("TEST-PAIR-9999"))
        self.assertFalse(self.vault.verify_pairing_code("WRONG-CODE"))
        self.assertFalse(self.vault.verify_pairing_code(""))

        # Subkey storage
        sk = {
            "id": "sub_test_001",
            "label": "Test Subkey",
            "budget_usd": 0.25,
            "period": "DAY",
            "quality_tier": "s",
            "assigned_agent": "hermes-music",
            "assigned_node": "local",
            "key_string": "test_sub_key_str"
        }
        self.vault.store_subkey(sk)
        subkeys = self.vault.get_subkeys()
        self.assertEqual(len(subkeys), 1)
        self.assertEqual(subkeys[0]["id"], "sub_test_001")
        self.assertEqual(subkeys[0]["quality_tier"], "s")

    @patch.object(VeniceClient, "get_rate_limits")
    @patch.object(VeniceClient, "test_inference")
    def test_guest_validate_key(self, mock_infer, mock_limits):
        """Verify guest key validation performs balance check and latency test."""
        mock_limits.return_value = {
            "success": True,
            "balances": {"USD": 4.95, "DIEM": 10.0, "BUNDLED_CREDITS": 25},
            "apiTier": {"id": "paid"},
            "raw": {"rateLimits": []}
        }
        mock_infer.return_value = {
            "success": True,
            "content": "Hello from Venice test prompt",
            "latency_ms": 210,
            "usage": {"total_tokens": 8}
        }

        client = VeniceClient(inference_key="dummy")
        res = client.test_key("venice_guest_candidate_key_123456789")

        self.assertTrue(res["success"])
        self.assertTrue(res["valid"])
        self.assertEqual(res["balances"]["USD"], 4.95)
        self.assertEqual(res["tier"], "paid")
        self.assertEqual(res["latency_ms"], 210)
        self.assertTrue(res["masked_key"].startswith("venice_g"))

    def test_pairing_auth_gate_in_request_handler(self):
        """Verify _is_authenticated properly requires and validates pairing code."""
        handler = DashboardRequestHandler.__new__(DashboardRequestHandler)
        handler.vault = self.vault

        # Unauthenticated query and headers
        handler.headers = {}
        self.assertFalse(handler._is_authenticated({}, {}))

        # Query parameter auth
        self.assertTrue(handler._is_authenticated({"pairing_code": ["TEST-PAIR-9999"]}, {}))

        # Header auth
        handler.headers = {"X-Pairing-Code": "TEST-PAIR-9999"}
        self.assertTrue(handler._is_authenticated({}, {}))

        # Wrong header auth
        handler.headers = {"X-Pairing-Code": "BAD-CODE"}
        self.assertFalse(handler._is_authenticated({}, {}))

    @patch("telegram.bot.requests.post")
    def test_telegram_bot_pairing_and_subkeys(self, mock_post):
        """Verify /pair_code and /subkeys commands in Telegram bot."""
        mock_post.return_value.json.return_value = {"ok": True, "result": {"message_id": 101}}

        bot = VeniceTelegramBot(vault=self.vault)
        bot.authorized_chat_id = "123456789"

        # Test /pair_code
        bot.handle_message({"chat": {"id": 123456789}, "text": "/pair_code"})
        mock_post.assert_called()
        call_payload = mock_post.call_args[1]["json"]
        self.assertIn("TEST-PAIR-9999", call_payload["text"])

        # Test /subkeys
        self.vault.store_subkey({
            "id": "sub_tg_002",
            "label": "Hermes Subkey",
            "budget_usd": 0.25,
            "period": "DAY",
            "quality_tier": "xs"
        })
        bot.handle_message({"chat": {"id": 123456789}, "text": "/subkeys"})
        call_payload = mock_post.call_args[1]["json"]
        self.assertIn("Hermes Subkey", call_payload["text"])
        self.assertIn("0.25", call_payload["text"])

    def test_mcp_tools_pairing_and_subkeys(self):
        """Verify MCP server tools for pairing and subkeys."""
        server = VeniceMCPServer()
        server.vault = self.vault

        # Test venice_get_pairing_status
        status_res = server.execute_tool("venice_get_pairing_status", {})
        self.assertTrue(status_res["success"])
        self.assertEqual(status_res["pairing_code"], "TEST-PAIR-9999")

        # Test venice_create_subkey
        create_res = server.execute_tool("venice_create_subkey", {
            "label": "Worker MCP Subkey",
            "budget_usd": 0.50,
            "quality_tier": "m",
            "period": "DAY"
        })
        self.assertTrue(create_res["success"])
        self.assertEqual(create_res["subkey"]["label"], "Worker MCP Subkey")
        self.assertEqual(create_res["subkey"]["budget_usd"], 0.50)
        self.assertEqual(create_res["subkey"]["quality_tier"], "m")


if __name__ == "__main__":
    unittest.main()

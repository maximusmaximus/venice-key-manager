"""
Unit tests for Venice Inference Balance Telemetry, Low/Out Detection, and Proactive Telegram Alerts.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.venice_client import VeniceClient
from core.vault import KeyVault
from telegram.bot import VeniceTelegramBot
from mcp.server import VeniceMCPServer


class TestBalanceAlerts(unittest.TestCase):
    def setUp(self):
        self.client = VeniceClient(inference_key="test-mock-key")
        self.vault = KeyVault()
        self.bot = VeniceTelegramBot(vault=self.vault)
        self.test_chat_id = "123456789"
        self.bot.authorized_chat_id = self.test_chat_id

        self.sent_messages = []
        def mock_send(chat_id, text, reply_markup=None, parse_mode="Markdown"):
            self.sent_messages.append({
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup
            })
            return {"ok": True}
        self.bot.send_message = mock_send

    def test_check_balance_status_healthy(self):
        """Test healthy balance detection when USD is above low threshold."""
        with patch.object(self.client, "get_rate_limits") as mock_limits:
            mock_limits.return_value = {
                "success": True,
                "balances": {"USD": 5.0, "DIEM": 0.0, "BUNDLED_CREDITS": 0.0},
                "apiTier": {"id": "paid"}
            }
            status = self.client.check_balance_status(low_threshold_usd=1.0, out_threshold_usd=0.05)
            self.assertTrue(status["success"])
            self.assertEqual(status["status"], "HEALTHY")
            self.assertFalse(status["is_low"])
            self.assertFalse(status["is_out"])
            self.assertIn("HEALTHY", status["badge"])
            self.assertIsNone(status["warning"])

    def test_check_balance_status_low(self):
        """Test low balance detection when USD is between out and low thresholds."""
        with patch.object(self.client, "get_rate_limits") as mock_limits:
            mock_limits.return_value = {
                "success": True,
                "balances": {"USD": 0.65, "DIEM": 0.0, "BUNDLED_CREDITS": 0.0},
                "apiTier": {"id": "paid"}
            }
            status = self.client.check_balance_status(low_threshold_usd=1.0, out_threshold_usd=0.05)
            self.assertTrue(status["success"])
            self.assertEqual(status["status"], "LOW")
            self.assertTrue(status["is_low"])
            self.assertFalse(status["is_out"])
            self.assertIn("LOW", status["badge"])
            self.assertIsNotNone(status["warning"])
            self.assertIn("0.6500", status["warning"])
            self.assertIn("venice.ai/settings/api", status["recharge_url"])

    def test_check_balance_status_out(self):
        """Test out of credits detection when USD is at or below out threshold."""
        with patch.object(self.client, "get_rate_limits") as mock_limits:
            mock_limits.return_value = {
                "success": True,
                "balances": {"USD": 0.02, "DIEM": 0.0, "BUNDLED_CREDITS": 0.0},
                "apiTier": {"id": "paid"}
            }
            status = self.client.check_balance_status(low_threshold_usd=1.0, out_threshold_usd=0.05)
            self.assertTrue(status["success"])
            self.assertEqual(status["status"], "OUT")
            self.assertFalse(status["is_low"])
            self.assertTrue(status["is_out"])
            self.assertIn("OUT", status["badge"])
            self.assertIn("depleted", status["warning"].lower())

    def test_check_balance_status_402_rate_limits(self):
        """Test detection when rate limits call itself returns 402 or insufficient balance."""
        with patch.object(self.client, "get_rate_limits") as mock_limits:
            mock_limits.return_value = {
                "success": False,
                "status_code": 402,
                "error": "Insufficient USD or Diem balance to complete request."
            }
            status = self.client.check_balance_status()
            self.assertTrue(status["success"])
            self.assertEqual(status["status"], "OUT")
            self.assertTrue(status["is_out"])
            self.assertIn("OUT", status["badge"])

    def test_test_inference_402_handling(self):
        """Test that test_inference flags out_of_credits on 402 response."""
        mock_resp = MagicMock()
        mock_resp.status_code = 402
        mock_resp.text = '{"error": "Insufficient USD or Diem balance"}'

        with patch("requests.post", return_value=mock_resp):
            res = self.client.test_inference(prompt="hello")
            self.assertFalse(res["success"])
            self.assertTrue(res.get("out_of_credits"))
            self.assertEqual(res.get("status_code"), 402)
            self.assertIn("venice.ai/settings/api", res.get("recharge_url"))

    def test_vault_thresholds_persistence(self):
        """Test getting and setting custom balance thresholds in KeyVault."""
        self.vault.set_balance_thresholds(low_usd=2.5, out_usd=0.10)
        t = self.vault.get_balance_thresholds()
        self.assertEqual(t["low_usd"], 2.5)
        self.assertEqual(t["out_usd"], 0.10)

        # Reset back to defaults
        self.vault.set_balance_thresholds(low_usd=1.0, out_usd=0.05)
        t = self.vault.get_balance_thresholds()
        self.assertEqual(t["low_usd"], 1.0)
        self.assertEqual(t["out_usd"], 0.05)

    def test_bot_proactive_alert_on_out_transition(self):
        """Test that Telegram bot dispatches an alert when balance transitions to OUT."""
        self.bot.last_notified_balance_status = "HEALTHY"

        with patch.object(self.bot, "_get_venice_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.check_balance_status.return_value = {
                "success": True,
                "status": "OUT",
                "is_low": False,
                "is_out": True,
                "usd": 0.00,
                "diem": 0.00,
                "api_tier": "paid",
                "badge": "🔴 OUT OF CREDITS",
                "recharge_url": "https://venice.ai/settings/api"
            }
            mock_get_client.return_value = mock_client

            self.bot.check_and_notify_balance()

            self.assertEqual(len(self.sent_messages), 1)
            msg = self.sent_messages[0]
            self.assertEqual(msg["chat_id"], self.test_chat_id)
            self.assertIn("Credits Depleted", msg["text"])
            self.assertIn("venice.ai/settings/api", msg["text"])
            self.assertEqual(self.bot.last_notified_balance_status, "OUT")

    def test_bot_proactive_alert_on_low_transition(self):
        """Test that Telegram bot dispatches a warning when balance transitions to LOW."""
        self.bot.last_notified_balance_status = "HEALTHY"

        with patch.object(self.bot, "_get_venice_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.check_balance_status.return_value = {
                "success": True,
                "status": "LOW",
                "is_low": True,
                "is_out": False,
                "usd": 0.75,
                "diem": 0.00,
                "api_tier": "paid",
                "badge": "🟡 LOW CREDITS",
                "recharge_url": "https://venice.ai/settings/api"
            }
            mock_get_client.return_value = mock_client

            self.bot.check_and_notify_balance()

            self.assertEqual(len(self.sent_messages), 1)
            msg = self.sent_messages[0]
            self.assertIn("Credits Running Low", msg["text"])
            self.assertIn("0.7500", msg["text"])
            self.assertEqual(self.bot.last_notified_balance_status, "LOW")

    def test_bot_alert_deduplication(self):
        """Test that repeated checks with the same status do not spam duplicate alerts."""
        self.bot.last_notified_balance_status = "LOW"

        with patch.object(self.bot, "_get_venice_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.check_balance_status.return_value = {
                "success": True,
                "status": "LOW",
                "is_low": True,
                "is_out": False,
                "usd": 0.70,
                "diem": 0.00,
                "api_tier": "paid"
            }
            mock_get_client.return_value = mock_client

            self.bot.check_and_notify_balance()

            # No new message sent because status didn't transition
            self.assertEqual(len(self.sent_messages), 0)

    def test_bot_recovery_alert(self):
        """Test that Telegram bot dispatches a recovery notification when credits are restored."""
        self.bot.last_notified_balance_status = "OUT"

        with patch.object(self.bot, "_get_venice_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.check_balance_status.return_value = {
                "success": True,
                "status": "HEALTHY",
                "is_low": False,
                "is_out": False,
                "usd": 5.00,
                "diem": 0.00,
                "api_tier": "paid",
                "badge": "🟢 HEALTHY"
            }
            mock_get_client.return_value = mock_client

            self.bot.check_and_notify_balance()

            self.assertEqual(len(self.sent_messages), 1)
            msg = self.sent_messages[0]
            self.assertIn("Credits Restored", msg["text"])
            self.assertIn("5.0000", msg["text"])
            self.assertEqual(self.bot.last_notified_balance_status, "HEALTHY")

    def test_bot_balance_command_output(self):
        """Test /balance output formats health badge and recharge link."""
        with patch.object(self.bot, "_get_venice_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.check_balance_status.return_value = {
                "success": True,
                "status": "LOW",
                "is_low": True,
                "is_out": False,
                "usd": 0.45,
                "diem": 0.00,
                "api_tier": "paid",
                "badge": "🟡 LOW CREDITS"
            }
            mock_get_client.return_value = mock_client

            self.bot.handle_message({"chat": {"id": self.test_chat_id}, "text": "/balance"})

            self.assertTrue(len(self.sent_messages) > 0)
            last = self.sent_messages[-1]
            self.assertIn("LOW CREDITS", last["text"])
            self.assertIn("0.4500", last["text"])
            self.assertIn("Top-up recommended", last["text"])
            # Verify inline button
            kb = last.get("reply_markup", {}).get("inline_keyboard", [])
            has_recharge_button = any(
                btn.get("url") == "https://venice.ai/settings/api"
                for row in kb for btn in row
            )
            self.assertTrue(has_recharge_button)

    def test_bot_threshold_command(self):
        """Test /threshold command viewing and updating."""
        self.bot.handle_message({"chat": {"id": self.test_chat_id}, "text": "/threshold"})
        last = self.sent_messages[-1]["text"]
        self.assertIn("Current Balance Alert Thresholds", last)

        self.bot.handle_message({"chat": {"id": self.test_chat_id}, "text": "/threshold 1.80 0.08"})
        last = self.sent_messages[-1]["text"]
        self.assertIn("Thresholds Updated", last)
        self.assertIn("1.80", last)
        self.assertIn("0.08", last)

        # Restore default
        self.vault.set_balance_thresholds(low_usd=1.0, out_usd=0.05)

    def test_mcp_server_balance_tool(self):
        """Test that MCP server venice_get_balances_and_tier includes status telemetry."""
        server = VeniceMCPServer()
        with patch.object(server, "_get_venice_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_rate_limits.return_value = {
                "success": True,
                "balances": {"USD": 4.99, "DIEM": 0.0},
                "apiTier": {"id": "paid"},
                "keyExpiration": None,
                "nextEpochBegins": "2026-10-04"
            }
            mock_client.check_balance_status.return_value = {
                "success": True,
                "status": "HEALTHY",
                "is_low": False,
                "is_out": False,
                "badge": "🟢 HEALTHY",
                "warning": None,
                "recharge_url": "https://venice.ai/settings/api"
            }
            mock_get_client.return_value = mock_client

            res = server.execute_tool("venice_get_balances_and_tier", {})
            self.assertEqual(res.get("status"), "HEALTHY")
            self.assertFalse(res.get("is_low"))
            self.assertFalse(res.get("is_out"))
            self.assertIn("HEALTHY", res.get("badge"))
            self.assertEqual(res.get("recharge_url"), "https://venice.ai/settings/api")


if __name__ == "__main__":
    unittest.main()

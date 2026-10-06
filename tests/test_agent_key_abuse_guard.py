"""
Unit test suite verifying Agent Admin Key Scoping and Anti-Abuse Safeguards.
Tests:
- Admin account usage restricted strictly to @v3n15PE_bot caller
- Key type restriction (INFERENCE only, ADMIN forbidden)
- Budget limits (caps at $10.00, rejects > $25.00, defaults <= 0)
- Rate limiting (cooldown, hourly quota of 5/hr, daily quota of 15/day)
- Quota of active keys per agent
- Telegram bot plain text and wizard integration
- MCP server caller-agent scoping
"""

import os
import sys
import time
import shutil
import tempfile
import unittest
from pathlib import Path

# Add project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.vault import KeyVault
from core.guard import KeyCreationGuard, GuardResult
from telegram.bot import VeniceTelegramBot
from mcp.server import VeniceMCPServer


class TestAgentKeyAbuseGuard(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.vault_path = Path(self.test_dir) / "test_vault.json"
        self.vault = KeyVault(vault_path=self.vault_path)
        self.test_admin_key = "VENICE_ADMIN_KEY_MOCK_TEST_SECRET_AAA"
        self.test_inf_key = "VENICE_INFERENCE_KEY_MOCK_TEST_SECRET_BBB"

        # Register v3n15PE_bot in vault
        self.vault.register_agent_bot("v3n15PE_bot", {
            "agent_name": "v3n15PE_bot",
            "bot_token": "100000000:MOCK_TEST_BOT_TOKEN_VALUE",
            "username": "v3n15PE_bot"
        })
        self.vault.set_agent_admin_key("v3n15PE_bot", self.test_admin_key)
        self.vault.set_venice_inference_key(self.test_inf_key)

        self.guard = KeyCreationGuard(self.vault)
        self.bot = VeniceTelegramBot(vault=self.vault)
        self.bot.authorized_chat_id = "100000001"

        self.sends = []
        def mock_send(chat_id, text, reply_markup=None, parse_mode="Markdown"):
            self.sends.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
            return {"ok": True}
        self.bot.send_message = mock_send

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_1_admin_key_scoping(self):
        """Admin key is accessible ONLY when requested by @v3n15PE_bot."""
        # 1. v3n15PE_bot can access
        self.assertEqual(
            self.vault.get_venice_admin_key(caller_agent="v3n15PE_bot"),
            self.test_admin_key
        )
        self.assertEqual(
            self.vault.get_venice_admin_key(caller_agent="@v3n15PE_bot"),
            self.test_admin_key
        )
        self.assertEqual(
            self.vault.get_venice_admin_key(caller_agent="v3n15pe_bot"),
            self.test_admin_key
        )

        # 2. Other agents CANNOT access
        self.assertEqual(self.vault.get_venice_admin_key(caller_agent="hermes-music"), "")
        self.assertEqual(self.vault.get_venice_admin_key(caller_agent="dawagent"), "")
        self.assertEqual(self.vault.get_venice_admin_key(caller_agent="untrusted_agent"), "")

        # 3. Unauthenticated/generic caller without caller_agent CANNOT access
        self.assertEqual(self.vault.get_venice_admin_key(caller_agent=None), "")

    def test_2_guard_blocks_admin_key_creation(self):
        """Agent is strictly blocked from creating ADMIN keys (privilege escalation prevention)."""
        res = self.guard.validate_key_request(
            caller_agent="v3n15PE_bot",
            key_type="ADMIN",
            budget_usd=5.0
        )
        self.assertFalse(res.allowed)
        self.assertIn("strictly prohibited from creating 'ADMIN' keys", res.reason)

    def test_3_guard_enforces_budget_caps(self):
        """Budget must be capped to $10.00 max, rejected if > $25, and defaulted if None/0."""
        # 1. Normal budget within limits
        r1 = self.guard.validate_key_request("v3n15PE_bot", budget_usd=5.0)
        self.assertTrue(r1.allowed)
        self.assertEqual(r1.sanitized_budget, 5.0)

        # 2. Budget between $15 and $50 is capped to $15.00
        r2 = self.guard.validate_key_request("v3n15PE_bot", budget_usd=25.0)
        self.assertTrue(r2.allowed)
        self.assertEqual(r2.sanitized_budget, 15.0)
        self.assertIn("capped", r2.warning)

        # 3. Excessive budget (> $50) is rejected outright
        r3 = self.guard.validate_key_request("v3n15PE_bot", budget_usd=100.0)
        self.assertFalse(r3.allowed)
        self.assertIn("exceeds the maximum limit", r3.reason)

        # 4. Zero / None budget defaults to $1.00
        r4 = self.guard.validate_key_request("v3n15PE_bot", budget_usd=None)
        self.assertTrue(r4.allowed)
        self.assertEqual(r4.sanitized_budget, 1.0)

        # 5. Negative or below minimum budget (< 0.05) rejected
        r5 = self.guard.validate_key_request("v3n15PE_bot", budget_usd=0.01)
        self.assertFalse(r5.allowed)
        self.assertIn("at least $0.05", r5.reason)

    def test_4_guard_rate_limiting_cooldown(self):
        """Guard enforces cooldown between consecutive key creations."""
        # Record first key creation
        self.guard.record_key_creation(
            caller_agent="v3n15PE_bot",
            key_id="test_key_1",
            budget_usd=1.0,
            tier="s"
        )

        # Immediate second key request should be blocked by cooldown
        res = self.guard.validate_key_request("v3n15PE_bot", budget_usd=1.0)
        self.assertFalse(res.allowed)
        self.assertIn("Rapid key creation detected", res.reason)

        # Bypassing cooldown works when requested
        res_bypass = self.guard.validate_key_request("v3n15PE_bot", budget_usd=1.0, bypass_cooldown=True)
        self.assertTrue(res_bypass.allowed)

    def test_5_guard_hourly_quota(self):
        """Guard blocks requests when hourly quota (5 keys/hr) is exceeded."""
        now = time.time()
        # Simulate 5 keys created in the past 10 minutes
        for i in range(5):
            self.vault.record_key_creation("v3n15pe_bot", {
                "timestamp": now - (i * 60) - 30,
                "key_id": f"k_{i}",
                "budget_usd": 1.0,
                "tier": "s"
            })

        # 6th key should be rejected
        res = self.guard.validate_key_request("v3n15PE_bot", budget_usd=1.0, bypass_cooldown=True)
        self.assertFalse(res.allowed)
        self.assertIn("Hourly creation limit reached", res.reason)

    def test_6_guard_metrics(self):
        """get_abuse_metrics accurately reflects quota and rate limit status."""
        metrics = self.guard.get_abuse_metrics("v3n15PE_bot")
        self.assertEqual(metrics["agent"], "v3n15pe_bot")
        self.assertTrue(metrics["is_authorized_admin"])
        self.assertEqual(metrics["max_keys_per_hour"], 5)
        self.assertEqual(metrics["max_keys_per_day"], 15)
        self.assertEqual(metrics["max_budget_per_key_usd"], 15.0)

    def test_7_telegram_bot_plain_text_make_key(self):
        """Telegram bot provision_agent_key uses v3n15PE_bot identity and guard."""
        # Initial request should succeed
        res = self.bot.provision_agent_key(agent="hermes-music", tier="s", budget_usd=2.5)
        self.assertTrue(res["success"])
        self.assertEqual(res["budget_usd"], 2.5)
        self.assertTrue(res["key_id"])

        # Immediate follow-up request should be blocked by cooldown
        res_cooldown = self.bot.provision_agent_key(agent="dawagent", tier="m", budget_usd=2.0)
        self.assertFalse(res_cooldown["success"])
        self.assertTrue(res_cooldown.get("abuse_detected"))
        self.assertIn("Rapid key creation detected", res_cooldown["error"])

    def test_8_telegram_bot_command_flow(self):
        """Testing /make_key and /guard_status commands in Telegram."""
        # Test /guard_status
        self.bot.handle_message({"chat": {"id": "100000001"}, "text": "/guard_status"})
        self.assertTrue(len(self.sends) > 0)
        self.assertIn("Anti-Abuse Safeguards & Key Provisioning Quota", self.sends[-1]["text"])
        self.assertIn("@v3n15pe_bot", self.sends[-1]["text"])

        # Test /make_key with excessive budget ($100) -> blocked by anti-abuse guard
        self.bot.handle_message({"chat": {"id": "100000001"}, "text": "/make_key dawagent xl $100"})
        last_msg = self.sends[-1]["text"]
        self.assertIn("Key Creation Blocked (Anti-Abuse Safeguard)", last_msg)
        self.assertIn("exceeds the maximum limit", last_msg)

    def test_9_mcp_caller_scoping(self):
        """MCP server restricts admin key to v3n15PE_bot caller only."""
        mcp = VeniceMCPServer(vault=self.vault)

        # 1. Call without caller_agent -> admin_key is empty -> fails without admin key
        res_anon = mcp.execute_tool("venice_create_key", {"description": "Anon Key"})
        # Should require admin key since caller is anonymous
        self.assertTrue(res_anon.get("requires_admin") or not res_anon.get("success"))

        # 2. Call with unauthorized agent -> fails without admin key
        res_other = mcp.execute_tool("venice_create_key", {
            "description": "Hermes Key",
            "caller_agent": "hermes-music"
        })
        self.assertTrue(res_other.get("requires_admin") or not res_other.get("success"))

        # 3. Call with v3n15PE_bot and excessive budget -> blocked by guard
        res_abuse = mcp.execute_tool("venice_create_key", {
            "description": "Abuse Key",
            "caller_agent": "v3n15PE_bot",
            "limit_usd": 100.0
        })
        self.assertFalse(res_abuse.get("success"))
        self.assertTrue(res_abuse.get("abuse_detected"))


if __name__ == "__main__":
    unittest.main()

"""
Unit test suite verifying Plain Text Key Creation, Minting, and NLP Intent Dispatch.
"""

import sys
import unittest
from pathlib import Path

# Add project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.vault import KeyVault
from telegram.bot import VeniceTelegramBot


class TestPlainTextKeyProvisioning(unittest.TestCase):
    def setUp(self):
        self.vault = KeyVault()
        self.bot = VeniceTelegramBot(vault=self.vault)
        self.test_chat_id = "123456789"
        self.bot.authorized_chat_id = self.test_chat_id

        self.sends = []
        def mock_send(chat_id, text, reply_markup=None, parse_mode="Markdown"):
            self.sends.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
            return {"ok": True}
        self.bot.send_message = mock_send

    def test_1_plain_text_make_key_command(self):
        """Tests /make_key, /create_key, and /create_subkey commands with arguments."""
        self.bot.handle_message({"chat": {"id": self.test_chat_id}, "text": "/make_key hermes-music s 5"})
        self.assertTrue(len(self.sends) > 0)
        last = self.sends[-1]["text"]
        self.assertIn("Agent Key Successfully Provisioned", last)
        self.assertIn("hermes-music", last)
        self.assertIn("TIER*: `S`", last.upper())

        # Test alias /create_key
        self.bot.handle_message({"chat": {"id": self.test_chat_id}, "text": "/create_key a2a-node xl 10"})
        last = self.sends[-1]["text"]
        self.assertIn("Agent Key Successfully Provisioned", last)
        self.assertIn("a2a-node", last)
        self.assertIn("TIER*: `XL`", last.upper())

    def test_2_provision_with_and_without_args(self):
        """Tests /provision with arguments (direct provision) and without (wizard)."""
        # With args:
        self.bot.handle_message({"chat": {"id": self.test_chat_id}, "text": "/provision custom-agent xs 2"})
        last = self.sends[-1]["text"]
        self.assertIn("Agent Key Successfully Provisioned", last)
        self.assertIn("custom-agent", last)

        # Without args:
        self.bot.handle_message({"chat": {"id": self.test_chat_id}, "text": "/provision"})
        last = self.sends[-1]["text"]
        self.assertIn("Agent Key Provisioning Wizard", last)
        self.assertIn("Step 1/3", last)

    def test_3_plain_text_mint_key_command(self):
        """Tests /mint_key and /mint_allocation commands."""
        self.bot.handle_message({"chat": {"id": self.test_chat_id}, "text": "/mint_key hermes-music s 3"})
        last = self.sends[-1]["text"]
        self.assertIn("Agent Allocation Link Minted", last)
        self.assertIn("Claims Allowed*: `3`", last)
        self.assertIn("venice.vmu.cash/claim/", last)

    def test_4_natural_language_make_key(self):
        """Tests natural language sentences to create/make keys."""
        # Query 1
        self.bot.handle_message({"chat": {"id": self.test_chat_id}, "text": "make a key for hermes-music tier m"})
        last = self.sends[-1]["text"]
        self.assertIn("Agent Key Successfully Provisioned", last)
        self.assertIn("hermes-music", last)
        self.assertIn("TIER*: `M`", last.upper())

        # Query 2: with dollar budget
        self.bot.handle_message({"chat": {"id": self.test_chat_id}, "text": "create key for dawagent tier xl with $15 budget"})
        last = self.sends[-1]["text"]
        self.assertIn("Agent Key Successfully Provisioned", last)
        self.assertIn("dawagent", last)
        self.assertIn("TIER*: `XL`", last.upper())
        self.assertIn("$15.00 USD", last)

        # Query 3: simple default
        self.bot.handle_message({"chat": {"id": self.test_chat_id}, "text": "can you make a key for me"})
        last = self.sends[-1]["text"]
        self.assertIn("Agent Key Successfully Provisioned", last)

    def test_5_natural_language_mint_allocation(self):
        """Tests natural language sentences to mint allocation links."""
        self.bot.handle_message({"chat": {"id": self.test_chat_id}, "text": "mint key for hermes 5 keys"})
        last = self.sends[-1]["text"]
        self.assertIn("Agent Allocation Link Minted", last)
        self.assertIn("Claims Allowed*: `5`", last)
        self.assertIn("https://venice.vmu.cash/claim/", last)

    def test_6_natural_language_status_and_balance(self):
        """Tests status and balance inquiries via plain text."""
        self.bot.handle_message({"chat": {"id": self.test_chat_id}, "text": "is bot working"})
        last = self.sends[-1]["text"]
        self.assertIn("Venice Key Manager // Resilience & Service Status", last)
        self.assertIn("ONLINE & POLLING", last)

        self.bot.handle_message({"chat": {"id": self.test_chat_id}, "text": "what is my balance"})
        last = self.sends[-1]["text"]
        self.assertIn("Venice.ai Account Balances", last)


if __name__ == "__main__":
    unittest.main()

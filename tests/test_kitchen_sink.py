"""
Comprehensive 'Kitchen Sink' Integration Test Suite for Telegram Agent Bot & Workflows.
Tests EVERY interactive button callback, wizard navigation path, and service workflow.
"""

import sys
import io
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

# Add project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.vault import KeyVault
from telegram.bot import VeniceTelegramBot


class TestKitchenSinkBotButtons(unittest.TestCase):
    """Verifies all interactive buttons in the Telegram Bot kitchen sink."""

    def setUp(self):
        self.vault = KeyVault()
        self.bot = VeniceTelegramBot(vault=self.vault)
        self.test_chat_id = "123456789"
        self.bot.authorized_chat_id = self.test_chat_id

        # Captured calls
        self.edits = []
        self.sends = []
        self.callback_answers = []

        def mock_edit(chat_id, message_id, text, reply_markup=None, parse_mode="Markdown"):
            self.edits.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
            return {"ok": True}

        def mock_send(chat_id, text, reply_markup=None, parse_mode="Markdown"):
            self.sends.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
            return {"ok": True}

        def mock_answer(cb_id, text="", show_alert=False):
            self.callback_answers.append({"id": cb_id, "text": text, "show_alert": show_alert})

        self.bot.edit_message = mock_edit
        self.bot.send_message = mock_send
        self.bot.answer_callback = mock_answer

    def _click(self, callback_data: str):
        cb = {
            "id": f"cb_{callback_data}",
            "data": callback_data,
            "message": {
                "chat": {"id": self.test_chat_id},
                "message_id": 999
            }
        }
        self.bot.handle_callback_query(cb)

    def test_01_main_menu_kitchen_sink_buttons(self):
        """Verify all buttons in the main menu dashboard exist and are properly structured."""
        kb = self.bot.main_menu_keyboard()
        buttons = [btn for row in kb.get("inline_keyboard", []) for btn in row]
        cb_datas = [b["callback_data"] for b in buttons]

        expected_buttons = [
            "menu_balance",
            "menu_keys",
            "menu_tg_bots",
            "wiz_key_agent",
            "menu_infer_tiers",
            "act_deploy_config",
            "menu_fleet",
            "act_sync_fleet",
            "menu_subkeys",
            "menu_allocations",
            "menu_pair_code",
            "menu_service",
            "act_backup_vault",
            "act_recall_vault",
            "menu_limits",
            "menu_main"
        ]
        for expected in expected_buttons:
            self.assertIn(expected, cb_datas, f"Missing main menu button: {expected}")

    def test_02_click_menu_main(self):
        """Click 'Refresh Dashboard'."""
        self.edits.clear()
        self._click("menu_main")
        self.assertEqual(len(self.edits), 1)
        self.assertIn("VENICE & TG KEY ENGINE", self.edits[0]["text"])

    def test_03_click_menu_balance(self):
        """Click 'Balances & Tier'."""
        self.edits.clear()
        self._click("menu_balance")
        self.assertEqual(len(self.edits), 1)
        self.assertTrue("Balances" in self.edits[0]["text"] or "USD" in self.edits[0]["text"])

    def test_04_click_menu_keys(self):
        """Click 'Venice Keys'."""
        self.edits.clear()
        self._click("menu_keys")
        self.assertEqual(len(self.edits), 1)
        self.assertIn("Venice", self.edits[0]["text"])

    def test_05_click_menu_tg_bots(self):
        """Click 'Agent TG Bots'."""
        self.edits.clear()
        self._click("menu_tg_bots")
        self.assertEqual(len(self.edits), 1)
        self.assertIn("Agent Telegram Bots", self.edits[0]["text"])

    def test_06_click_menu_infer_tiers_and_all_runs(self):
        """Click 'Tier Inference' menu and test all 5 quality tiers (XS to XL)."""
        self.edits.clear()
        self._click("menu_infer_tiers")
        self.assertEqual(len(self.edits), 1)
        self.assertIn("Model Quality Tier Sandbox", self.edits[0]["text"])

        # Test each tier button with mocked client
        for tier in ["xs", "s", "m", "l", "xl"]:
            self.edits.clear()
            with patch.object(self.bot, "_get_venice_client") as mock_client:
                mock_client.return_value.test_inference.return_value = {
                    "success": True,
                    "content": f"Hello from Tier {tier.upper()}",
                    "latency_ms": 120,
                    "usage": {"total_tokens": 15},
                    "cost": {"usd": 0.0001}
                }
                self._click(f"infer_run_{tier}")
                self.assertEqual(len(self.edits), 1)
                self.assertIn(f"Tier {tier.upper()}", self.edits[0]["text"])
                self.assertIn(f"Hello from Tier {tier.upper()}", self.edits[0]["text"])

    def test_07_provision_key_walkthrough_wizard(self):
        """Test full 4-step Provision Key walkthrough wizard."""
        # Step 1: Agent selection
        self.edits.clear()
        self._click("wiz_key_agent")
        self.assertIn("Step 1/3", self.edits[0]["text"])

        # Step 2: Quality tier selection
        self.edits.clear()
        self._click("wiz_agent_hermes-music")
        self.assertIn("Step 2/3", self.edits[0]["text"])
        self.assertIn("hermes-music", self.edits[0]["text"])

        # Step 3: Budget cap selection
        self.edits.clear()
        self._click("wiz_tier_hermes-music_s")
        self.assertIn("Step 3/3", self.edits[0]["text"])
        self.assertIn("Allocated Tier", self.edits[0]["text"])

        # Step 4: Issue / Bind key
        self.edits.clear()
        self._click("wiz_do_hermes-music_s_5")
        self.assertTrue(len(self.edits) >= 1)

    def test_08_deploy_key_buttons(self):
        """Test deploy key callbacks."""
        self.edits.clear()
        with patch("core.deployer.ConfigDeployer.deploy_venice_key") as mock_deploy:
            mock_deploy.return_value = {
                "success": True,
                "target": "config.yaml",
                "backup": "config.yaml.bak",
                "key_preview": "VENICE_...1234"
            }
            self._click("act_deploy_config")
            self.assertIn("Configuration Deployed", self.edits[0]["text"])

    def test_09_fleet_and_sync_buttons(self):
        """Test fleet nodes menu and sync buttons."""
        self.edits.clear()
        self._click("menu_fleet")
        self.assertIn("A2A Fleet Mesh", self.edits[0]["text"])

        self.edits.clear()
        with patch.object(self.bot.mesh, "sync_all_nodes") as mock_sync:
            mock_sync.return_value = {"results": {"local": {"success": True, "commit_after": "76b8f66"}}}
            self._click("act_sync_fleet")
            self.assertIn("Fleet Git Sync Result", self.edits[0]["text"])

    def test_10_pair_code_and_subkeys_buttons(self):
        """Test pairing code and sub-keys buttons."""
        self.edits.clear()
        self._click("menu_pair_code")
        self.assertIn("Pairing Code", self.edits[0]["text"])

        self.edits.clear()
        self._click("menu_subkeys")
        self.assertIn("Sub-Keys", self.edits[0]["text"])

    def test_11_backup_and_recall_buttons(self):
        """Test backup and recall buttons."""
        self.edits.clear()
        self._click("act_backup_vault")
        self.assertIn("Backup Snapshot", self.edits[0]["text"])

        self.edits.clear()
        self._click("act_recall_vault")
        self.assertIn("Auto-Recall", self.edits[0]["text"])

    def test_12_service_and_restart_buttons(self):
        """Test auto-restart supervisor menu and restart trigger."""
        self.edits.clear()
        self._click("menu_service")
        self.assertIn("Service & Auto-Restart", self.edits[0]["text"])

        self.edits.clear()
        self._click("act_restart_service")
        self.assertIn("Restart Signal Dispatched", self.edits[0]["text"])

    def test_13_allocations_kitchen_sink_walkthrough(self):
        """Test full allocations menu and 3-step minting walkthrough."""
        # 1. View Allocations menu
        self.edits.clear()
        self._click("menu_allocations")
        self.assertIn("Agent Key Allocations", self.edits[0]["text"])

        # 2. Step 1: Agent selection
        self.edits.clear()
        self._click("wiz_alloc_agent")
        self.assertIn("Step 1/3", self.edits[0]["text"])

        # 3. Step 2: Quality tier selection
        self.edits.clear()
        self._click("wiz_alloc_for_v3n15PE_bot")
        self.assertIn("Step 2/3", self.edits[0]["text"])
        self.assertIn("v3n15PE_bot", self.edits[0]["text"])

        # 4. Step 3: Allocated keys count
        self.edits.clear()
        self._click("wiz_alloc_tier_v3n15PE_bot_s")
        self.assertIn("Step 3/3", self.edits[0]["text"])

        # 5. Mint execution
        self.edits.clear()
        self._click("wiz_alloc_do_v3n15PE_bot_s_3")
        self.assertEqual(len(self.edits), 1)
        self.assertIn("Agent Allocation Link Minted", self.edits[0]["text"])
        self.assertIn("v3n15PE_bot", self.edits[0]["text"])
        self.assertIn("https://venice.vmu.cash/claim/", self.edits[0]["text"])

    def test_14_message_commands(self):
        """Test Telegram text commands."""
        commands = [
            "/start",
            "/menu",
            "/pair_code",
            "/subkeys",
            "/allocations"
        ]
        for cmd in commands:
            self.sends.clear()
            self.bot.handle_message({"chat": {"id": self.test_chat_id}, "text": cmd})
            self.assertEqual(len(self.sends), 1, f"Failed on command {cmd}")


if __name__ == "__main__":
    unittest.main()

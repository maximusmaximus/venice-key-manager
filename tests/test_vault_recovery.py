"""
Unit Tests for KeyVault Atomic Persistence, Multi-Tier Backups, and Crash Recovery.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent

from core.vault import KeyVault, _extract_from_env_file, _extract_from_yaml_file


class TestVaultRecovery(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.vault_file = Path(self.test_dir) / "test_venice_vault.json"
        self.backup_file = self.vault_file.with_suffix(".backup.json")
        self.vault = KeyVault(vault_path=self.vault_file)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_save_and_backup_mirror(self):
        """Verify atomic write creates both primary file and secondary backup mirror."""
        self.vault.set_venice_inference_key("INF_KEY_12345")
        self.vault.set_venice_admin_key("ADMIN_KEY_67890")
        self.vault.store_subkey({
            "id": "vsk_unit_01",
            "name": "Unit Subkey",
            "quality_tier": "m",
            "budget_usd": 0.50
        })

        self.assertTrue(self.vault_file.exists())
        self.assertTrue(self.backup_file.exists())

        # Verify both have identical keys
        v_check = KeyVault(vault_path=self.vault_file)
        self.assertEqual(v_check.get_venice_inference_key(), "INF_KEY_12345")
        self.assertEqual(v_check.get_venice_admin_key(), "ADMIN_KEY_67890")
        self.assertEqual(len(v_check.get_subkeys()), 1)

    def test_recovery_from_deleted_vault_file(self):
        """Verify automatic recall when primary vault file is completely deleted."""
        self.vault.set_venice_inference_key("INF_KEY_PERSIST")
        self.vault.set_venice_admin_key("ADMIN_KEY_PERSIST")
        self.vault.store_subkey({"id": "vsk_persist", "name": "Persist Subkey"})

        # Catastrophic deletion of primary vault file
        os.remove(self.vault_file)
        self.assertFalse(self.vault_file.exists())

        # Reload KeyVault — should transparently restore from backup
        reloaded = KeyVault(vault_path=self.vault_file)
        self.assertEqual(reloaded.get_venice_inference_key(), "INF_KEY_PERSIST")
        self.assertEqual(reloaded.get_venice_admin_key(), "ADMIN_KEY_PERSIST")
        self.assertEqual(len(reloaded.get_subkeys()), 1)
        self.assertTrue(self.vault_file.exists())

    def test_recovery_from_corrupted_vault_file(self):
        """Verify automatic recall when primary vault file contains corrupted garbage."""
        self.vault.set_venice_inference_key("INF_KEY_VALID")
        self.vault.set_venice_admin_key("ADMIN_KEY_VALID")

        # Corrupt primary vault file (e.g., sudden power loss or incomplete write)
        self.vault_file.write_text("CORRUPTED_INCOMPLETE_JSON_{{{", encoding="utf-8")

        # Reload KeyVault — should recover from valid backup
        reloaded = KeyVault(vault_path=self.vault_file)
        self.assertEqual(reloaded.get_venice_inference_key(), "INF_KEY_VALID")
        self.assertEqual(reloaded.get_venice_admin_key(), "ADMIN_KEY_VALID")

    def test_safety_guard_prevents_empty_overwrite(self):
        """Verify safety guard refuses to wipe valuable keys with empty defaults."""
        self.vault.set_venice_inference_key("INF_KEY_IMPORTANT")
        self.vault.set_venice_admin_key("ADMIN_KEY_IMPORTANT")

        # Attempt to save empty data without force
        empty_data = {
            "version": "1.0",
            "venice": {"admin_key": "", "inference_key": "", "keys": []},
            "telegram": {"master_bot_token": ""},
            "subkeys": []
        }
        self.vault._save(empty_data, force=False)

        # Safety guard should have preserved important keys
        self.assertEqual(self.vault.get_venice_inference_key(), "INF_KEY_IMPORTANT")
        self.assertEqual(self.vault.get_venice_admin_key(), "ADMIN_KEY_IMPORTANT")

    def test_create_and_restore_backup(self):
        """Verify explicit backup creation and restoring from snapshot."""
        self.vault.set_venice_inference_key("ORIGINAL_KEY")
        snap = self.vault.create_backup(label="test_snapshot")
        self.assertTrue(snap.get("success"))
        snap_path = Path(snap.get("path"))
        self.assertTrue(snap_path.exists())

        # Modify vault
        self.vault.set_venice_inference_key("MODIFIED_KEY")
        self.assertEqual(self.vault.get_venice_inference_key(), "MODIFIED_KEY")

        # Restore from snapshot
        restore_res = self.vault.restore_from_file(snap_path)
        self.assertTrue(restore_res.get("success"))
        self.assertEqual(self.vault.get_venice_inference_key(), "ORIGINAL_KEY")

    def test_extract_from_env_and_yaml(self):
        """Verify helper parsers extract keys from YAML configs and .env files."""
        # Test .env parser
        env_file = Path(self.test_dir) / ".env"
        env_file.write_text("VENICE_API_KEY=test_env_venice_key\nTELEGRAM_BOT_TOKEN=test_env_bot_token\n", encoding="utf-8")
        extracted_env = _extract_from_env_file(env_file)
        self.assertEqual(extracted_env.get("VENICE_API_KEY"), "test_env_venice_key")
        self.assertEqual(extracted_env.get("TELEGRAM_BOT_TOKEN"), "test_env_bot_token")

        # Test YAML parser
        yaml_file = Path(self.test_dir) / "config.yaml"
        yaml_file.write_text("model:\n  api_key: test_yaml_venice_key\nchannels:\n  telegram:\n    bot_token: test_yaml_bot_token\n", encoding="utf-8")
        extracted_yaml = _extract_from_yaml_file(yaml_file)
        self.assertEqual(extracted_yaml.get("VENICE_INFERENCE_KEY"), "test_yaml_venice_key")
        self.assertEqual(extracted_yaml.get("TELEGRAM_BOT_TOKEN"), "test_yaml_bot_token")


if __name__ == "__main__":
    unittest.main()

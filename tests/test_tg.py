"""
Unit & Integration Tests for Telegram Agent Manager & @BotFather Integration.
"""

import sys
import io
from pathlib import Path

# Fix Windows console UTF-8 encoding
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Add project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.vault import KeyVault
from core.tg_manager import TelegramAgentManager
from core.deployer import ConfigDeployer


def test_tg_validation():
    print("[1/5] Testing Telegram bot token validation (getMe)...")
    vault = KeyVault()
    mgr = TelegramAgentManager(vault)
    token = vault.get_telegram_master_token()
    assert token, "Master bot token should be present"

    res = mgr.validate_token(token)
    assert res.get("valid"), f"Bot token validation failed: {res.get('error')}"
    print(f"  [+] Validated Bot: @{res.get('username')} (ID: {res.get('bot_id')}, Name: '{res.get('first_name')}')")


def test_agent_registration():
    print("[2/5] Testing agent bot registration in vault...")
    vault = KeyVault()
    mgr = TelegramAgentManager(vault)
    token = vault.get_telegram_master_token()

    res = mgr.register_agent_bot(
        agent_name="a2a-test-agent",
        bot_token=token,
        notes="Automated test agent bot mapping"
    )
    assert res.get("success"), f"Registration failed: {res.get('error')}"
    assert vault.get_agent_bot("a2a-test-agent") is not None
    print(f"  [+] Successfully registered agent 'a2a-test-agent'")

    # Cleanup test bot
    vault.remove_agent_bot("a2a-test-agent")
    print(f"  [+] Cleaned up temporary test agent")


def test_botfather_wizard():
    print("[3/5] Testing @BotFather wizard generator...")
    vault = KeyVault()
    mgr = TelegramAgentManager(vault)
    wiz = mgr.generate_botfather_wizard("dawagent")
    assert "botfather_deep_link" in wiz
    assert len(wiz.get("steps", [])) >= 5
    print(f"  [+] Generated BotFather deep link: {wiz.get('botfather_deep_link')}")
    print(f"  [+] Suggested Username: {wiz.get('suggested_username')}")


def test_test_message_ping():
    print("[4/5] Testing Telegram test ping transmission...")
    vault = KeyVault()
    mgr = TelegramAgentManager(vault)
    token = vault.get_telegram_master_token()
    chat_id = vault.get_authorized_chat_id()
    if not token or not chat_id:
        print("  [~] Skipping live ping test (token or chat_id not configured in environment).")
        return

    res = mgr.send_test_message(
        bot_token=token,
        chat_id=chat_id,
        text="⚡ *Venice & TG Key Manager* initialized successfully!\n• Status: All systems online."
    )
    assert res.get("success"), f"Failed to send test message: {res.get('error')}"
    print(f"  [+] Test ping delivered! Message ID: {res.get('message_id')}")


def test_tg_deployer():
    print("[5/5] Testing Telegram bot token deployment to target config...")
    vault = KeyVault()
    token = vault.get_telegram_master_token() or "dummy_token_12345:ABC"
    test_yaml = PROJECT_ROOT / "tests" / "test_temp_tg_config.yaml"
    test_yaml.write_text("channels:\n  telegram:\n    bot_token: old-token\n", encoding="utf-8")
    try:
        res = ConfigDeployer.deploy_telegram_token(token, target_path=test_yaml)
        assert res.get("success"), f"Deploy failed: {res.get('error')}"
        print(f"  [+] Deployed Telegram token safely to {res.get('target')}")
    finally:
        if test_yaml.exists():
            test_yaml.unlink()
        bak = test_yaml.with_suffix(f"{test_yaml.suffix}.bak")
        if bak.exists():
            bak.unlink()


if __name__ == "__main__":
    test_tg_validation()
    test_agent_registration()
    test_botfather_wizard()
    test_test_message_ping()
    test_tg_deployer()
    print("\nALL TELEGRAM & BOTFATHER TESTS PASSED!")

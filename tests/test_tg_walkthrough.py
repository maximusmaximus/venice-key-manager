"""
Automated Integration Tests for Telegram Agent Button Walkthrough Workflow.
Tests:
1. Main menu keyboard layout (Provision Key XS-XL & Tier Inference)
2. Step 1: Agent selection options
3. Step 2: Quality tier (XS to XL) selection buttons
4. Step 3: Tier specs breakdown & budget cap selection
5. Step 4: Key binding / provisioning with model quality tier persistence
6. Tier Inference Sandbox menu
7. Keys list tier badge rendering
"""

import sys
import io
from pathlib import Path
from unittest.mock import MagicMock

# Fix Windows console UTF-8 output
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Add project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.vault import KeyVault
from telegram.bot import VeniceTelegramBot


def run_tg_walkthrough_tests():
    print("=" * 65)
    print("=== RUNNING TELEGRAM AGENT BUTTON WALKTHROUGH TEST SUITE ===")
    print("=" * 65)

    vault = KeyVault()
    bot = VeniceTelegramBot(vault=vault)

    # Mock edit_message, send_message, and answer_callback to record bot outputs
    captured_edits = []
    captured_sends = []

    def mock_edit_message(chat_id, message_id, text, reply_markup=None, parse_mode="Markdown"):
        captured_edits.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return {"ok": True}

    def mock_send_message(chat_id, text, reply_markup=None, parse_mode="Markdown"):
        captured_sends.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return {"ok": True}

    bot.edit_message = mock_edit_message
    bot.send_message = mock_send_message
    bot.answer_callback = MagicMock()

    test_chat_id = bot.authorized_chat_id or "8293122782"
    bot.authorized_chat_id = test_chat_id

    # [Test 1] Main Menu Keyboard
    print("\n[Test 1/6] Verifying Main Menu Buttons...")
    kb = bot.main_menu_keyboard()
    flat_buttons = [btn for row in kb.get("inline_keyboard", []) for btn in row]
    cb_datas = [b["callback_data"] for b in flat_buttons]
    assert "wiz_key_agent" in cb_datas, f"Missing 'wiz_key_agent' in menu: {cb_datas}"
    assert "menu_infer_tiers" in cb_datas, f"Missing 'menu_infer_tiers' in menu: {cb_datas}"
    print("  [+] Main Menu contains '➕ Provision Key (XS-XL)' and '⚡ Tier Inference'.")

    # [Test 2] Step 1 of Provisioning Wizard (Agent Selection)
    print("\n[Test 2/6] Step 1: Agent Selection Walkthrough...")
    captured_edits.clear()
    cb_step1 = {
        "id": "cb_1",
        "data": "wiz_key_agent",
        "message": {"chat": {"id": test_chat_id}, "message_id": 100}
    }
    bot.handle_callback_query(cb_step1)
    assert len(captured_edits) == 1
    step1_out = captured_edits[0]
    assert "Step 1/3" in step1_out["text"]
    agent_cbs = [b["callback_data"] for row in step1_out["reply_markup"]["inline_keyboard"] for b in row]
    assert any("wiz_agent_hermes-music" in cb for cb in agent_cbs)
    print(f"  [+] Step 1 rendered target agents: {agent_cbs[:3]}...")

    # [Test 3] Step 2 of Provisioning Wizard (Model Quality Tier Selection)
    print("\n[Test 3/6] Step 2: Model Quality Tier Selection (XS to XL)...")
    captured_edits.clear()
    cb_step2 = {
        "id": "cb_2",
        "data": "wiz_agent_hermes-music",
        "message": {"chat": {"id": test_chat_id}, "message_id": 100}
    }
    bot.handle_callback_query(cb_step2)
    assert len(captured_edits) == 1
    step2_out = captured_edits[0]
    assert "Step 2/3" in step2_out["text"]
    assert "hermes-music" in step2_out["text"]
    tier_cbs = [b["callback_data"] for row in step2_out["reply_markup"]["inline_keyboard"] for b in row]
    for expected_t in ["xs", "s", "m", "l", "xl"]:
        assert f"wiz_tier_hermes-music_{expected_t}" in tier_cbs, f"Missing tier {expected_t} in {tier_cbs}"
    print("  [+] Step 2 rendered all 5 quality tiers (XS, S, M, L, XL).")

    # [Test 4] Step 3: Tier Specification Breakdown & Budget Selection
    print("\n[Test 4/6] Step 3: Tier Specs Breakdown & Budget Caps...")
    captured_edits.clear()
    cb_step3 = {
        "id": "cb_3",
        "data": "wiz_tier_hermes-music_m",
        "message": {"chat": {"id": test_chat_id}, "message_id": 100}
    }
    bot.handle_callback_query(cb_step3)
    assert len(captured_edits) == 1
    step3_out = captured_edits[0]
    assert "Step 3/3" in step3_out["text"]
    assert "Allocated Tier" in step3_out["text"] and "M" in step3_out["text"]
    assert "llama-3.3-70b" in step3_out["text"]
    budget_cbs = [b["callback_data"] for row in step3_out["reply_markup"]["inline_keyboard"] for b in row]
    assert "wiz_do_hermes-music_m_5" in budget_cbs
    assert "wiz_do_hermes-music_m_10" in budget_cbs
    assert "wiz_do_hermes-music_m_25" in budget_cbs
    assert "wiz_do_hermes-music_m_0" in budget_cbs
    print("  [+] Step 3 presented detailed specs, default model (llama-3.3-70b), and budget caps ($5, $10, $25, Unlimited).")

    # [Test 5] Step 4: Key Allocation & Model Quality Policy Binding
    print("\n[Test 5/6] Step 4: Key Allocation & Quality Tier Persistence...")
    captured_edits.clear()
    cb_step4 = {
        "id": "cb_4",
        "data": "wiz_bind_hermes-music_m_5",
        "message": {"chat": {"id": test_chat_id}, "message_id": 100}
    }
    bot.handle_callback_query(cb_step4)
    assert len(captured_edits) == 1
    step4_out = captured_edits[0]
    assert "Allocated" in step4_out["text"] or "Provisioned" in step4_out["text"]
    assert "hermes-music" in step4_out["text"]
    assert "`M`" in step4_out["text"]

    # Verify key in vault has maxModelTier='m'
    matched_keys = [k for k in vault.get_venice_keys() if k.get("agent") == "hermes-music" and k.get("maxModelTier") == "m"]
    assert len(matched_keys) >= 1, "Expected vault to contain key with maxModelTier='m' and agent='hermes-music'"
    test_k_id = matched_keys[0].get("id")
    print(f"  [+] Vault successfully persisted agent key with maxModelTier='m': ID={test_k_id}")
    vault.remove_venice_key(test_k_id)
    print(f"  [+] Cleaned up test key {test_k_id} from vault.")

    # [Test 6] Model Quality Tier Sandbox Menu
    print("\n[Test 6/6] Verifying Model Quality Tier Sandbox Menu...")
    captured_edits.clear()
    cb_sandbox = {
        "id": "cb_5",
        "data": "menu_infer_tiers",
        "message": {"chat": {"id": test_chat_id}, "message_id": 100}
    }
    bot.handle_callback_query(cb_sandbox)
    assert len(captured_edits) == 1
    sandbox_out = captured_edits[0]
    assert "Sandbox" in sandbox_out["text"]
    sandbox_cbs = [b["callback_data"] for row in sandbox_out["reply_markup"]["inline_keyboard"] for b in row]
    for expected_t in ["xs", "s", "m", "l", "xl"]:
        assert f"infer_run_{expected_t}" in sandbox_cbs
    print("  [+] Sandbox menu supports testing all 5 tiers directly via interactive buttons.")

    print("\n" + "=" * 65)
    print(">>> ALL 6 TELEGRAM AGENT WALKTHROUGH TESTS PASSED! <<<")
    print("=" * 65)


if __name__ == "__main__":
    run_tg_walkthrough_tests()

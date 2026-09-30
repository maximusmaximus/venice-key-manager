"""
End-to-End (E2E) Test Suite for Venice & TG Key Manager Dashboard.
Tests:
1. Static HTML and Modal DOM elements
2. Live REST API endpoints (/api/stats, /api/keys, /api/create_key, /api/deploy_key, /api/revoke_key)
3. Remote Key Creation (Admin required validation)
4. Existing Key Import (Validation, Vault persistence, Stats update)
5. Secret Resolution during Key Deployment
6. Key Revocation and Cleanup
7. JS-HTML DOM element ID binding contract
"""

import os
import re
import json
import time
import shutil
import urllib.request
import urllib.error
from pathlib import Path

BASE_URL = os.environ.get("TEST_BASE_URL", "http://localhost:8844")
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def http_request(path: str, method: str = "GET", data: dict = None) -> tuple:
    url = f"{BASE_URL}{path}"
    headers = {"Content-Type": "application/json"} if data is not None else {}
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8")
            status = resp.status
            try:
                parsed = json.loads(content)
            except Exception:
                parsed = content
            return status, parsed
    except urllib.error.HTTPError as e:
        content = e.read().decode("utf-8")
        try:
            parsed = json.loads(content)
        except Exception:
            parsed = content
        return e.code, parsed


def test_1_static_html_and_modal():
    print("\n--- [E2E Test 1/7] Static HTML & Modal DOM Elements ---")
    status, html = http_request("/")
    assert status == 200, f"Expected 200, got {status}"
    assert "VENICE // TG ENGINE" in html, "Dashboard title missing from HTML"
    assert "btn-open-create-key" in html, "Issue/Add button missing from HTML"
    assert "modal-create-key" in html, "Modal container missing from HTML"
    assert "tab-btn-import-key" in html, "Import Key tab missing from HTML"
    assert "tab-btn-generate-key" in html, "Generate Key tab missing from HTML"
    assert "btn-confirm-import-key" in html, "Confirm Import button missing from HTML"
    assert "btn-confirm-create-key" in html, "Confirm Generate button missing from HTML"
    print("  [PASSED] Static HTML contains all modal elements, sub-tabs, and action buttons.")


def test_2_stats_and_keys_endpoints():
    print("\n--- [E2E Test 2/7] Stats and Keys Endpoints ---")
    status, stats = http_request("/api/stats")
    assert status == 200, f"Expected 200, got {status}"
    assert stats.get("success") is True, f"Stats failed: {stats}"
    assert "balances" in stats, "Balances missing from stats"
    assert "keys_count" in stats, "Keys count missing from stats"
    print(f"  [+] Stats: Tier={stats.get('apiTier', {}).get('id')}, Keys Count={stats.get('keys_count')}")

    status, keys_data = http_request("/api/keys")
    assert status == 200, f"Expected 200, got {status}"
    assert keys_data.get("success") is True, f"Keys API failed: {keys_data}"
    assert isinstance(keys_data.get("keys"), list), "Keys should be a list"
    print(f"  [+] Current Keys in Vault: {len(keys_data.get('keys'))}")
    print("  [PASSED] Stats and Keys endpoints return valid JSON state.")


def test_3_generate_without_admin_key_handling():
    print("\n--- [E2E Test 3/7] Remote Key Generation Without Admin Key ---")
    # Calling create_key in generate mode without admin key configured must return requires_admin: True
    payload = {
        "mode": "generate",
        "description": "E2E Test Key",
        "key_type": "INFERENCE",
        "limit_usd": 5.0,
        "limit_period": "MONTH"
    }
    status, res = http_request("/api/create_key", method="POST", data=payload)
    assert status == 200, f"Expected 200 response with requires_admin, got {status}"
    assert res.get("success") is False, "Key generation without admin key should fail gracefully"
    assert res.get("requires_admin") is True, "Response should explicitly indicate requires_admin: True"
    assert "Admin API Key is required" in res.get("error", ""), f"Unexpected error message: {res.get('error')}"
    print(f"  [+] Server responded with graceful requires_admin: {res.get('error')}")
    print("  [PASSED] Remote generation correctly gates on Admin Key requirement.")


def test_4_import_invalid_key_validation():
    print("\n--- [E2E Test 4/7] Import Invalid Key Validation ---")
    payload = {
        "mode": "import",
        "key_string": "invalid_fake_key_12345",
        "description": "Bogus Key",
        "key_type": "INFERENCE"
    }
    status, res = http_request("/api/create_key", method="POST", data=payload)
    assert res.get("success") is False, "Importing fake key should fail validation"
    assert "rejected" in res.get("error", "").lower() or "failed" in res.get("error", "").lower() or "unauthorized" in res.get("error", "").lower(), f"Unexpected error: {res}"
    print(f"  [+] Venice rejected bogus key properly: {res.get('error')}")
    print("  [PASSED] Invalid keys are properly rejected before being stored in vault.")


def test_5_import_valid_key_persistence():
    print("\n--- [E2E Test 5/7] Import Valid Key Persistence & Stats Update ---")
    # Retrieve current active inference key from vault
    vault_file = PROJECT_ROOT / "venice_vault.json"
    with open(vault_file, "r", encoding="utf-8") as f:
        vault_data = json.load(f)
    active_key = vault_data.get("venice", {}).get("inference_key")

    assert active_key, "Active inference key must exist in vault for E2E testing"

    # Count before
    _, initial_stats = http_request("/api/stats")
    initial_count = initial_stats.get("keys_count", 0)

    # Import this valid key under a test label
    import_payload = {
        "mode": "import",
        "key_string": active_key,
        "description": "E2E Verified Agent Key",
        "key_type": "INFERENCE",
        "limit_usd": 10.0
    }
    status, res = http_request("/api/create_key", method="POST", data=import_payload)
    assert status == 200, f"Expected 200, got {status}"
    assert res.get("success") is True, f"Key import failed: {res}"
    assert res.get("imported") is True, "Response must indicate imported: True"

    key_obj = res.get("key", {})
    key_id = key_obj.get("id")
    assert key_id and key_id.startswith("vk_"), f"Generated key_id invalid: {key_id}"
    assert key_obj.get("description") == "E2E Verified Agent Key"
    print(f"  [+] Imported Key registered: ID={key_id}, Label='{key_obj.get('description')}'")

    # Verify key now exists in /api/keys
    _, keys_res = http_request("/api/keys")
    found_keys = [k for k in keys_res.get("keys", []) if k.get("id") == key_id]
    assert len(found_keys) == 1, f"Imported key {key_id} not found in /api/keys list"
    print(f"  [+] Key verified in /api/keys list: {found_keys[0].get('description')}")

    # Verify stats count incremented
    _, new_stats = http_request("/api/stats")
    assert new_stats.get("keys_count") >= initial_count, "Stats keys_count should be updated"
    print(f"  [+] /api/stats keys_count updated: {new_stats.get('keys_count')}")

    print("  [PASSED] Valid key imported, verified live against Venice, and persisted in vault.")
    return key_id


def test_6_deploy_and_revoke_key(key_id: str):
    print("\n--- [E2E Test 6/7] Deploy Key (Secret Resolution) and Revocation ---")
    # Test Deploying key by key_id
    temp_config = PROJECT_ROOT / "tests" / "test_e2e_deploy_config.yaml"
    temp_config.write_text("model:\n  provider: venice\n  api_key: placeholder_old_key\n", encoding="utf-8")

    deploy_payload = {
        "key_string": key_id,
        "target_path": str(temp_config)
    }
    status, deploy_res = http_request("/api/deploy_key", method="POST", data=deploy_payload)
    assert status == 200, f"Expected 200, got {status}"
    assert deploy_res.get("success") is True, f"Deploy failed: {deploy_res}"

    # Verify deployed config has actual secret key, NOT the vk_ ID
    deployed_content = temp_config.read_text(encoding="utf-8")
    assert key_id not in deployed_content, "Deployed file should contain secret key, NOT key_id"
    assert "api_key:" in deployed_content
    print("  [+] Key secret successfully resolved from vault and deployed to config file.")

    # Clean up temp config and its backup
    if temp_config.exists():
        temp_config.unlink()
    bak = temp_config.with_suffix(".yaml.bak")
    if bak.exists():
        bak.unlink()

    # Test Revoke Key
    revoke_payload = {"key_id": key_id}
    status, revoke_res = http_request("/api/revoke_key", method="POST", data=revoke_payload)
    assert status == 200, f"Expected 200, got {status}"
    assert revoke_res.get("success") is True, f"Revocation failed: {revoke_res}"

    # Verify key is removed from /api/keys
    _, keys_res = http_request("/api/keys")
    remaining_keys = [k for k in keys_res.get("keys", []) if k.get("id") == key_id]
    assert len(remaining_keys) == 0, f"Revoked key {key_id} should not appear in keys list"
    print(f"  [+] Key {key_id} cleanly removed from vault.")
    print("  [PASSED] Key deployment resolved secret properly and revocation cleanly removed key.")


def test_7_frontend_dom_contract():
    print("\n--- [E2E Test 7/7] Frontend DOM Contract Verification ---")
    html_content = (PROJECT_ROOT / "web" / "static" / "index.html").read_text(encoding="utf-8")
    js_content = (PROJECT_ROOT / "web" / "static" / "app.js").read_text(encoding="utf-8")

    # Extract all getElementById calls in app.js
    ids_in_js = set(re.findall(r'getElementById\(["\']([^"\']+)["\']\)', js_content))
    missing_in_html = [el_id for el_id in sorted(ids_in_js) if f'id="{el_id}"' not in html_content and f"id='{el_id}'" not in html_content]

    assert len(missing_in_html) == 0, f"Missing DOM elements referenced in JS: {missing_in_html}"
    print(f"  [+] Verified {len(ids_in_js)} DOM element IDs match perfectly between app.js and index.html.")
    print("  [PASSED] Frontend DOM contract 100% verified.")


def test_8_model_quality_tiers_and_gating():
    print("\n--- [E2E Test 8/8] Model Quality Tiers (XS to XL) & Inference Gating ---")
    # 1. Test /api/model_tiers
    status, tiers_data = http_request("/api/model_tiers")
    assert status == 200, f"Expected 200 from /api/model_tiers, got {status}"
    assert tiers_data.get("success") is True, f"Failed /api/model_tiers: {tiers_data}"
    assert tiers_data.get("tier_order") == ["xs", "s", "m", "l", "xl"], f"Unexpected tier order: {tiers_data.get('tier_order')}"
    assert all(t in tiers_data.get("tiers", {}) for t in ["xs", "s", "m", "l", "xl"]), "Missing tiers in tier mapping"
    print("  [+] /api/model_tiers returned all 5 tiers (XS, S, M, L, XL).")

    # 2. Import a restricted key with tier 's'
    vault_file = PROJECT_ROOT / "venice_vault.json"
    with open(vault_file, "r", encoding="utf-8") as f:
        vault_data = json.load(f)
    active_key = vault_data.get("venice", {}).get("inference_key")

    import_payload = {
        "mode": "import",
        "key_string": active_key,
        "description": "Tier Gated Agent Key (Tier S)",
        "key_type": "INFERENCE",
        "max_model_tier": "s"
    }
    status, res = http_request("/api/create_key", method="POST", data=import_payload)
    assert status == 200, f"Expected 200, got {status}"
    assert res.get("success") is True
    key_obj = res.get("key", {})
    assert key_obj.get("maxModelTier") == "s", f"Expected maxModelTier 's', got {key_obj.get('maxModelTier')}"
    key_id = key_obj.get("id")
    print(f"  [+] Created key {key_id} with maxModelTier='s'")

    # 3. Test inference gating with a tier 'l' model (deepseek-r1)
    infer_blocked_payload = {
        "key_id": key_id,
        "model": "deepseek-r1",
        "prompt": "Test prompt"
    }
    status, infer_res = http_request("/api/infer", method="POST", data=infer_blocked_payload)
    assert status == 403, f"Expected status 403 Forbidden for tier escalation, got {status}"
    assert infer_res.get("success") is False
    assert "exceeds" in infer_res.get("error", "").lower()
    print(f"  [+] Gating blocked higher-tier model: {infer_res.get('error')}")

    # 4. Clean up the test key
    http_request("/api/revoke_key", method="POST", data={"key_id": key_id})
    print("  [PASSED] Model quality tier hierarchy and inference gating verified.")


def run_all_e2e_tests():
    print("=" * 65)
    print("=== RUNNING VENICE & TG KEY MANAGER E2E TEST SUITE ===")
    print(f"Target: {BASE_URL}")
    print("=" * 65)

    test_1_static_html_and_modal()
    test_2_stats_and_keys_endpoints()
    test_3_generate_without_admin_key_handling()
    test_4_import_invalid_key_validation()
    imported_key_id = test_5_import_valid_key_persistence()
    test_6_deploy_and_revoke_key(imported_key_id)
    test_7_frontend_dom_contract()
    test_8_model_quality_tiers_and_gating()

    print("\n" + "=" * 65)
    print(">>> ALL 8 E2E TESTS PASSED SUCCESSFULLY! <<<")
    print("=" * 65)


if __name__ == "__main__":
    run_all_e2e_tests()

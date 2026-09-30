"""
Unit & Integration Tests for Venice Client and Key Vault.
"""

import sys
import io
from pathlib import Path

# Fix Windows console UTF-8 encoding
if hasattr(sys.stdout, "buffer") and not getattr(sys.stdout, "closed", False):
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

# Add project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.vault import KeyVault
from core.venice_client import VeniceClient
from core.deployer import ConfigDeployer


def test_vault():
    print("[1/4] Testing KeyVault initialization...")
    vault = KeyVault()
    assert vault.get_active_venice_key(), "Active Venice key should be discovered"
    print("  [+] Vault initialized successfully.")


def test_venice_rate_limits():
    print("[2/4] Testing Venice.ai live balance & rate limits query...")
    vault = KeyVault()
    client = VeniceClient(
        inference_key=vault.get_venice_inference_key(),
        base_url=vault.data.get("venice", {}).get("base_url")
    )
    res = client.get_rate_limits()
    assert res.get("success"), f"Failed to get rate limits: {res.get('error')}"
    balances = res.get("balances", {})
    print(f"  [+] Live Balances verified: USD=${balances.get('USD')}, DIEM={balances.get('DIEM')}")
    print(f"  [+] Subscription Tier: {res.get('apiTier', {}).get('id')}")


def test_venice_inference():
    print("[3/4] Testing Venice.ai light inference (deepseek-v4-flash)...")
    vault = KeyVault()
    client = VeniceClient(
        inference_key=vault.get_venice_inference_key(),
        base_url=vault.data.get("venice", {}).get("base_url")
    )
    res = client.test_inference(prompt="Say OK in 1 word", model="deepseek-v4-flash", max_tokens=10)
    assert res.get("success"), f"Inference failed: {res.get('error')}"
    print(f"  [+] Inference succeeded in {res.get('latency_ms')} ms")
    print(f"  [+] Total Tokens: {res.get('usage', {}).get('total_tokens')}, Cost: ${res.get('cost', {}).get('usd', 0):.6f}")
    print(f"  [+] Output: {res.get('content') or res.get('reasoning')}")


def test_config_deployer():
    print("[4/4] Testing ConfigDeployer on target configuration...")
    vault = KeyVault()
    active_key = vault.get_active_venice_key()
    test_yaml = PROJECT_ROOT / "tests" / "test_temp_config.yaml"
    test_yaml.write_text("model:\n  api_key: old-key\n", encoding="utf-8")
    try:
        res = ConfigDeployer.deploy_venice_key(active_key, target_path=test_yaml)
        assert res.get("success"), f"Deploy failed: {res.get('error')}"
        print(f"  [+] Deployed key safely to {res.get('target')}")
        print(f"  [+] Backup preserved at {res.get('backup')}")
    finally:
        if test_yaml.exists():
            test_yaml.unlink()
        bak = test_yaml.with_suffix(f"{test_yaml.suffix}.bak")
        if bak.exists():
            bak.unlink()


if __name__ == "__main__":
    test_vault()
    test_venice_rate_limits()
    test_venice_inference()
    test_config_deployer()
    print("\nALL VENICE TESTS PASSED!")

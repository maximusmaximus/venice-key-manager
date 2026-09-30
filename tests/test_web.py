"""
Test for Web Dashboard Server and REST APIs.
"""

import sys
import io
import time
import requests
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

from web.server import start_web_server


def test_web_server():
    print("[1/4] Starting Web Server daemon on port 8899...")
    server = start_web_server(port=8899, daemon=True)
    time.sleep(1)

    print("[2/4] Testing Public Gate and Pairing Security...")
    r_html = requests.get("http://localhost:8899/")
    assert r_html.status_code == 200
    assert "VENICE // TG ENGINE" in r_html.text
    print("  [+] Static HTML successfully served.")

    r_pub = requests.get("http://localhost:8899/api/pairing_status")
    assert r_pub.status_code == 200
    assert r_pub.json().get("requires_pairing") is True
    print("  [+] Public endpoint /api/pairing_status returns HTTP 200.")

    # Unauthenticated /api/stats should be gated with 401
    r_unauth = requests.get("http://localhost:8899/api/stats")
    assert r_unauth.status_code == 401
    print("  [+] Protected Mode Gate verified: Unpaired requests return 401.")

    print("[3/4] Testing Authenticated Requests & Vault APIs...")
    from core.vault import KeyVault
    vault = KeyVault()
    pairing_code = vault.get_pairing_code()
    headers = {"X-Pairing-Code": pairing_code}

    r_stats = requests.get("http://localhost:8899/api/stats", headers=headers)
    assert r_stats.status_code == 200
    data = r_stats.json()
    assert data.get("success")
    print(f"  [+] /api/stats response: USD={data.get('balances', {}).get('USD')}, Tier={data.get('apiTier', {}).get('id')}")

    r_vault_status = requests.get("http://localhost:8899/api/vault/status", headers=headers)
    assert r_vault_status.status_code == 200
    v_st = r_vault_status.json()
    assert v_st.get("success")
    assert v_st.get("backup_mirror_exists") is True
    print(f"  [+] /api/vault/status verified: Backup Mirror Active, Snapshots={v_st.get('total_snapshots')}.")

    r_backups = requests.get("http://localhost:8899/api/vault/backups", headers=headers)
    assert r_backups.status_code == 200
    assert "backups" in r_backups.json()
    print(f"  [+] /api/vault/backups returned {len(r_backups.json().get('backups'))} snapshots.")

    print("[4/4] Querying /api/agent_bots and /api/keys...")
    r_bots = requests.get("http://localhost:8899/api/agent_bots", headers=headers)
    assert r_bots.status_code == 200
    bots_data = r_bots.json()
    assert bots_data.get("success")
    print(f"  [+] /api/agent_bots returned {len(bots_data.get('agent_bots', []))} bots.")

    r_keys = requests.get("http://localhost:8899/api/keys", headers=headers)
    assert r_keys.status_code == 200
    print("  [+] /api/keys returned HTTP 200.")

    server.shutdown()
    print("  [+] Web Server daemon cleanly stopped.")


if __name__ == "__main__":
    test_web_server()
    print("\nALL WEB SERVER TESTS PASSED!")

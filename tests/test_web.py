"""
Test for Web Dashboard Server and REST APIs.
"""

import sys
import io
import time
import requests
from pathlib import Path

# Fix Windows console UTF-8 encoding
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Add project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web.server import start_web_server


def test_web_server():
    print("[1/3] Starting Web Server daemon on port 8899...")
    server = start_web_server(port=8899, daemon=True)
    time.sleep(1)

    print("[2/3] Querying static index.html and /api/stats...")
    r_html = requests.get("http://localhost:8899/")
    assert r_html.status_code == 200
    assert "VENICE // TG ENGINE" in r_html.text
    print("  [+] Static HTML successfully served.")

    r_stats = requests.get("http://localhost:8899/api/stats")
    assert r_stats.status_code == 200
    data = r_stats.json()
    assert data.get("success")
    print(f"  [+] /api/stats response: USD={data.get('balances', {}).get('USD')}, Tier={data.get('apiTier', {}).get('id')}")

    print("[3/3] Querying /api/agent_bots and /api/keys...")
    r_bots = requests.get("http://localhost:8899/api/agent_bots")
    assert r_bots.status_code == 200
    bots_data = r_bots.json()
    assert bots_data.get("success")
    print(f"  [+] /api/agent_bots returned {len(bots_data.get('agent_bots', []))} bots.")

    r_keys = requests.get("http://localhost:8899/api/keys")
    assert r_keys.status_code == 200
    print("  [+] /api/keys returned HTTP 200.")

    server.shutdown()
    print("  [+] Web Server daemon cleanly stopped.")


if __name__ == "__main__":
    test_web_server()
    print("\nALL WEB SERVER TESTS PASSED!")

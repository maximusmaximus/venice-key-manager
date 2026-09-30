"""
End-to-End Unit & Integration Tests for Venice Agent Key Allocations (venice.vmu.cash/claim/...).
Tests link minting, Cloud DNS patterns, inspection privacy, claiming schedule,
quota enforcement, MCP tools, and Web API endpoints.
"""

import os
import json
import pytest
import tempfile
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from http.server import HTTPServer
import threading
import urllib.request
import urllib.error

from core.vault import KeyVault
from mcp.server import VeniceMCPServer
from web.server import DashboardRequestHandler


@pytest.fixture
def temp_vault():
    temp_dir = tempfile.mkdtemp(prefix="test_venice_alloc_")
    vault_file = Path(temp_dir) / "venice_vault.json"
    
    vault = KeyVault(vault_path=vault_file)
    vault.data = {
        "venice": {
            "admin_key": "test_admin_key_9999",
            "inference_key": "test_inf_key_1111",
            "keys": []
        },
        "cloudflare": {
            "domain": "venice.vmu.cash",
            "tunnel_name": "venice-tunnel"
        },
        "security": {
            "pairing_code": "VK-TEST-PAIR",
            "require_pairing": True
        },
        "allocations": []
    }
    vault._save(force=True)
    yield vault
    shutil.rmtree(temp_dir, ignore_errors=True)


class TestAllocationVaultCore:
    def test_mint_allocation_pattern(self, temp_vault):
        """Verifies Cloud DNS URL pattern and high-entropy claim token."""
        alloc = temp_vault.mint_allocation(
            label="Hermes Music Agent Key",
            target_agent="hermes-music",
            allocated_keys_count=3,
            quality_tier="s",
            budget_usd=0.25,
            limit_period="DAY",
            target_node="local",
            api_key="venice_sec_test_secret_12345"
        )

        assert alloc["claim_url"].startswith("https://venice.vmu.cash/claim/vclm_")
        assert alloc["claim_token"].startswith("vclm_")
        assert len(alloc["claim_token"]) > 30
        assert alloc["allocated_keys_count"] == 3
        assert alloc["claimed_keys_count"] == 0
        assert alloc["status"] == "ACTIVE"
        assert alloc["target_agent"] == "hermes-music"
        assert alloc["quality_tier"] == "s"
        assert alloc["budget_usd"] == 0.25
        assert alloc["api_key"] == "venice_sec_test_secret_12345"

    def test_inspect_allocation_privacy(self, temp_vault):
        """Verifies that inspecting allocation does not leak the secret Venice API key."""
        alloc = temp_vault.mint_allocation(
            label="Private Agent Alloc",
            target_agent="dawagent",
            allocated_keys_count=2,
            api_key="venice_super_secret_master_key"
        )

        insp = temp_vault.inspect_allocation(alloc["claim_token"])
        assert insp["success"] is True
        assert insp["allocated_keys_count"] == 2
        assert insp["claimed_keys_count"] == 0
        assert insp["remaining_claims"] == 2
        assert insp["can_claim_now"] is True
        assert insp["target_agent"] == "dawagent"
        # Secret key MUST not be present in public inspection
        assert "api_key" not in insp
        assert "apiKey" not in insp

        # Also inspect via full claim URL
        insp_by_url = temp_vault.inspect_allocation(alloc["claim_url"])
        assert insp_by_url["success"] is True
        assert insp_by_url["id"] == alloc["id"]

    def test_future_valid_from_schedule(self, temp_vault):
        """Verifies allocation with a future valid_from date cannot be claimed yet."""
        future_date = (datetime.utcnow() + timedelta(days=2)).isoformat() + "Z"
        alloc = temp_vault.mint_allocation(
            label="Future Key Allocation",
            target_agent="worker-agent",
            allocated_keys_count=1,
            valid_from=future_date
        )

        insp = temp_vault.inspect_allocation(alloc["claim_token"])
        assert insp["can_claim_now"] is False
        assert insp["status"] == "PENDING"

        # Attempt to claim
        claim_res = temp_vault.claim_allocation(alloc["claim_token"], agent_id="worker-agent")
        assert claim_res["success"] is False
        assert "not yet claimable" in claim_res["error"]

    def test_expired_valid_until_schedule(self, temp_vault):
        """Verifies expired allocation cannot be claimed."""
        past_date = (datetime.utcnow() - timedelta(days=1)).isoformat() + "Z"
        alloc = temp_vault.mint_allocation(
            label="Expired Allocation",
            target_agent="worker-agent",
            allocated_keys_count=1,
            valid_until=past_date
        )

        insp = temp_vault.inspect_allocation(alloc["claim_token"])
        assert insp["can_claim_now"] is False
        assert insp["status"] == "EXPIRED"

        claim_res = temp_vault.claim_allocation(alloc["claim_token"], agent_id="worker-agent")
        assert claim_res["success"] is False
        assert "expired" in claim_res["error"].lower()

    def test_quota_exhaustion_and_claim_history(self, temp_vault):
        """Verifies quota decrements, status changes to CLAIMED, and duplicate claim is blocked."""
        alloc = temp_vault.mint_allocation(
            label="Single Use Alloc",
            target_agent="hermes-music",
            allocated_keys_count=1,
            api_key="venice_claimed_key_xyz"
        )

        # First claim succeeds
        claim1 = temp_vault.claim_allocation(alloc["claim_token"], agent_id="hermes-music")
        assert claim1["success"] is True
        assert claim1["api_key"] == "venice_claimed_key_xyz"
        assert claim1["remaining_claims"] == 0
        assert claim1["status"] == "CLAIMED"

        # Second claim fails
        claim2 = temp_vault.claim_allocation(alloc["claim_token"], agent_id="hermes-music-2")
        assert claim2["success"] is False
        assert "already been claimed" in claim2["error"]

        # Revoke test
        alloc2 = temp_vault.mint_allocation(label="Revocable", target_agent="test-agent")
        assert temp_vault.revoke_allocation(alloc2["claim_token"]) is True
        claim_rev = temp_vault.claim_allocation(alloc2["claim_token"])
        assert claim_rev["success"] is False
        assert "revoked" in claim_rev["error"]


class TestAllocationMCPTools:
    def test_mcp_mint_inspect_and_claim(self, temp_vault, monkeypatch):
        """Tests the full MCP lifecycle: minting, inspecting, claiming, and listing."""
        server = VeniceMCPServer()
        server.vault = temp_vault

        # 1. Mint allocation via MCP
        mint_res = server.execute_tool("venice_mint_allocation", {
            "label": "MCP Allocated Key",
            "target_agent": "hermes-music",
            "allocated_keys_count": 2,
            "quality_tier": "m",
            "budget_usd": 0.50,
            "limit_period": "DAY"
        })
        assert mint_res["success"] is True
        claim_url = mint_res["claim_url"]
        assert "venice.vmu.cash/claim/" in claim_url

        # 2. Inspect via MCP
        insp_res = server.execute_tool("venice_inspect_allocation", {
            "claim_token_or_url": claim_url
        })
        assert insp_res["success"] is True
        assert insp_res["allocated_keys_count"] == 2
        assert insp_res["remaining_claims"] == 2
        assert insp_res["quality_tier"] == "m"

        # 3. Claim via MCP
        claim_res = server.execute_tool("venice_claim_allocated_key", {
            "claim_token_or_url": claim_url,
            "agent_id": "hermes-music"
        })
        assert claim_res["success"] is True
        assert claim_res["api_key"] is not None
        assert claim_res["remaining_claims"] == 1

        # 4. List allocations via MCP
        list_res = server.execute_tool("venice_list_allocations", {
            "target_agent": "hermes-music"
        })
        assert list_res["success"] is True
        assert list_res["total"] >= 1


class TestAllocationWebEndpoints:
    @pytest.fixture(autouse=True)
    def setup_web_server(self, temp_vault):
        old_vault = DashboardRequestHandler.vault
        DashboardRequestHandler.vault = temp_vault
        # Use an ephemeral localhost port
        self.server = HTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
        self.port = self.server.server_port
        self.base_url = f"http://127.0.0.1:{self.port}"

        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        try:
            yield
        finally:
            DashboardRequestHandler.vault = old_vault
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass

    def test_public_inspect_and_claim_api(self, temp_vault):
        """Tests public /claim/<token>, /api/allocations/inspect, and /api/allocations/claim."""
        alloc = temp_vault.mint_allocation(
            label="Web Endpoint Alloc",
            target_agent="hermes-music",
            allocated_keys_count=1,
            api_key="venice_web_test_key_888"
        )
        token = alloc["claim_token"]

        # 1. GET /claim/<token>?format=json
        req = urllib.request.Request(f"{self.base_url}/claim/{token}?format=json")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["success"] is True
            assert data["remaining_claims"] == 1
            assert "api_key" not in data

        # 2. GET /claim/<token> (HTML portal page)
        req_html = urllib.request.Request(f"{self.base_url}/claim/{token}")
        with urllib.request.urlopen(req_html) as resp:
            assert resp.status == 200
            html = resp.read().decode("utf-8")
            assert "Venice Key Allocation" in html
            assert "venice.vmu.cash/claim/" in html
            assert "Claim Allocated Key Now" in html

        # 3. GET /api/allocations/inspect?token=...
        req_insp = urllib.request.Request(f"{self.base_url}/api/allocations/inspect?token={token}")
        with urllib.request.urlopen(req_insp) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data["success"] is True
            assert data["allocated_keys_count"] == 1

        # 4. POST /api/allocations/claim
        claim_payload = json.dumps({"claim_token": token, "agent_id": "hermes-music"}).encode("utf-8")
        req_claim = urllib.request.Request(
            f"{self.base_url}/api/allocations/claim",
            data=claim_payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_claim) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data["success"] is True
            assert data["api_key"] == "venice_web_test_key_888"
            assert data["status"] == "CLAIMED"

    def test_protected_mint_and_list_api(self, temp_vault):
        """Verifies /api/allocations/mint and /api/allocations require pairing code."""
        # Unauthenticated mint attempt fails
        mint_body = json.dumps({
            "label": "Unauthorized Alloc",
            "target_agent": "hacker-bot"
        }).encode("utf-8")
        req_unauth = urllib.request.Request(
            f"{self.base_url}/api/allocations/mint",
            data=mint_body,
            headers={"Content-Type": "application/json"}
        )
        try:
            urllib.request.urlopen(req_unauth)
            pytest.fail("Expected 401 Unauthorized")
        except urllib.error.HTTPError as e:
            assert e.code == 401

        # Authenticated mint succeeds
        req_auth = urllib.request.Request(
            f"{self.base_url}/api/allocations/mint",
            data=mint_body,
            headers={
                "Content-Type": "application/json",
                "X-Pairing-Code": "VK-TEST-PAIR"
            }
        )
        with urllib.request.urlopen(req_auth) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["success"] is True
            assert "venice.vmu.cash/claim/" in data["claim_url"]

        # Authenticated list succeeds
        req_list = urllib.request.Request(
            f"{self.base_url}/api/allocations",
            headers={"X-Pairing-Code": "VK-TEST-PAIR"}
        )
        with urllib.request.urlopen(req_list) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["success"] is True
            assert len(data["allocations"]) >= 1

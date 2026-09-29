import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from venice_key_manager.state import state_store
from venice_key_manager.web.app import app
from venice_key_manager.models import KeyCreatedResponse, ConsumptionLimit
from venice_key_manager.mcp_server import VeniceMCPServer

client = TestClient(app)


def test_batch_auth_tokens_state():
    """Test state_store batch auth token creation with custom prefix and validation."""
    prefix = "alpha_team_"
    tokens = state_store.create_batch_auth_tokens(
        prefix=prefix,
        count=4,
        created_by="test_suite",
        ttl_hours=48,
        notes="Automated test batch"
    )

    assert len(tokens) == 4
    for idx, item in enumerate(tokens, 1):
        token_str = item["token"]
        assert token_str.startswith(prefix)
        assert item["index"] == idx
        assert item["notes"] == "Automated test batch"
        # Validate that each token passes validation
        assert state_store.validate_auth_token(token_str) is True

    # Test filtering by prefix
    filtered = state_store.list_active_tokens(prefix=prefix)
    assert len(filtered) >= 4
    for f in filtered:
        assert f["token"].startswith(prefix)

    # Test revocation
    to_revoke = tokens[0]["token"]
    assert state_store.revoke_auth_token(to_revoke) is True
    assert state_store.validate_auth_token(to_revoke) is False


def test_batch_auth_tokens_api_endpoints():
    """Test REST API endpoints for batch auth tokens."""
    # 1. Admin login token
    admin_token = state_store.create_auth_token(created_by="api_test_admin")

    # 2. Unauthenticated request to batch endpoint should return 401
    res_unauth = client.post("/api/auth/tokens/batch", json={"prefix": "unauth-", "count": 2})
    assert res_unauth.status_code == 401

    # 3. Authenticated request to create batch tokens
    headers = {"Authorization": f"Bearer {admin_token}"}
    payload = {
        "prefix": "partner-node-",
        "count": 3,
        "ttl_hours": 72,
        "notes": "Partner nodes access"
    }
    res = client.post("/api/auth/tokens/batch", json=payload, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["count"] == 3
    assert data["prefix"] == "partner-node-"
    assert len(data["tokens"]) == 3

    for item in data["tokens"]:
        assert item["token"].startswith("partner-node-")
        assert "magic_url" in item
        assert item["token"] in item["magic_url"]

    # 4. List tokens with prefix filter
    res_list = client.get("/api/auth/tokens?prefix=partner-node-", headers=headers)
    assert res_list.status_code == 200
    tokens_list = res_list.json()
    assert len(tokens_list) >= 3

    # 5. Revoke a token via DELETE endpoint
    rev_target = data["tokens"][0]["token"]
    res_del = client.delete(f"/api/auth/tokens/{rev_target}", headers=headers)
    assert res_del.status_code == 200
    assert res_del.json()["success"] is True
    assert state_store.validate_auth_token(rev_target) is False


@pytest.mark.asyncio
async def test_batch_keys_api_with_mock():
    """Test /api/keys/batch endpoint with mocked Venice client."""
    admin_token = state_store.create_auth_token(created_by="keys_batch_test")
    headers = {"Authorization": f"Bearer {admin_token}"}

    mock_results = [
        KeyCreatedResponse(
            apiKey=f"vkm_mock_key_{i}",
            id=f"kid_mock_{i}",
            description=f"fleet-worker-{i:02d}",
            apiKeyType="INFERENCE",
            consumptionLimits=ConsumptionLimit(usd=0.50),
            limitPeriod="EPOCH",
            category="Agents"
        )
        for i in range(1, 4)
    ]

    with patch("venice_key_manager.client.VeniceClient.create_batch_keys", new_callable=AsyncMock) as mock_batch:
        mock_batch.return_value = mock_results

        payload = {
            "prefix": "fleet-worker-",
            "count": 3,
            "daily_usd": 0.50,
            "category": "Agents",
            "apiKeyType": "INFERENCE",
            "limitPeriod": "EPOCH"
        }
        res = client.post("/api/keys/batch", json=payload, headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["count"] == 3
        assert len(data["keys"]) == 3
        assert data["keys"][0]["description"] == "fleet-worker-01"
        assert data["keys"][0]["apiKey"] == "vkm_mock_key_1"


@pytest.mark.asyncio
async def test_mcp_batch_tools():
    """Test MCP tools for batch codes and batch tokens."""
    server = VeniceMCPServer()

    # 1. Test venice_create_batch_codes
    code_res_raw = await server.execute_tool("venice_create_batch_codes", {
        "prefix": "mcp_batch_",
        "count": 3,
        "ttl_hours": 24,
        "notes": "MCP created batch"
    })
    import json
    code_res = json.loads(code_res_raw)
    assert code_res["status"] == "success"
    assert code_res["count"] == 3
    assert len(code_res["tokens"]) == 3
    assert code_res["tokens"][0]["token"].startswith("mcp_batch_")

    # 2. Test venice_list_auth_tokens
    list_res_raw = await server.execute_tool("venice_list_auth_tokens", {
        "prefix": "mcp_batch_"
    })
    list_res = json.loads(list_res_raw)
    assert list_res["total"] >= 3

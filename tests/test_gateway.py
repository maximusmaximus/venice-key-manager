import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from venice_key_manager.web.app import app
from venice_key_manager.state import state_store
from venice_key_manager.models import (
    MODEL_TIER_MAPPING,
    MODEL_TIER_ORDER,
    is_tier_allowed,
    resolve_model_tier,
    get_model_for_tier,
)
from venice_key_manager.mcp_server import VeniceMCPServer

client = TestClient(app)


def test_model_tier_hierarchy_and_helpers():
    """Verify tier ordering xs < s < m < l < xl and boundary helpers."""
    assert MODEL_TIER_ORDER == ["xs", "s", "m", "l", "xl"]

    # Allowed checks
    assert is_tier_allowed("xs", "m") is True
    assert is_tier_allowed("m", "m") is True
    assert is_tier_allowed("l", "m") is False
    assert is_tier_allowed("xl", "l") is False
    assert is_tier_allowed("xl", "xl") is True

    # Resolution checks
    assert resolve_model_tier("xs") == "xs"
    assert resolve_model_tier("llama-3.2-3b") == "xs"
    assert resolve_model_tier("deepseek-v4-flash") == "s"
    assert resolve_model_tier("llama-3.3-70b") == "m"
    assert resolve_model_tier("deepseek-r1") == "l"
    assert resolve_model_tier("llama-3.1-405b") == "xl"

    # Default model mapping
    assert get_model_for_tier("xs") == "llama-3.2-3b"
    assert get_model_for_tier("s") == "deepseek-v4-flash"
    assert get_model_for_tier("m") == "llama-3.3-70b"
    assert get_model_for_tier("l") == "deepseek-r1"
    assert get_model_for_tier("xl") == "llama-3.1-405b"


def test_project_crud_and_sub_key_defaults():
    """Verify project allocations and default $0.25/day sub-key limits."""
    # 1. Create project
    proj = state_store.create_project(
        name="Agent Swarm Project",
        description="Autonomous testing agents",
        daily_limit_usd=3.00,
        weekly_limit_usd=15.00,
        default_sub_key_daily_usd=0.25,
        max_model_tier="m",
    )
    assert proj["id"].startswith("proj_")
    assert proj["name"] == "Agent Swarm Project"
    assert proj["daily_limit_usd"] == 3.00
    assert proj["default_sub_key_daily_usd"] == 0.25
    assert proj["max_model_tier"] == "m"

    # 2. Create external key under this project without specifying limit (should default to 0.25)
    ext_key = state_store.create_external_key(
        project_id=proj["id"],
        name="Lead Agent Key",
        daily_limit_usd=None,  # should pick up project default
        max_model_tier="m",
    )
    assert ext_key["daily_limit_usd"] == 0.25
    assert ext_key["max_model_tier"] == "m"

    # 3. Create sub-key from external key (default amount should be $0.25 per project per day)
    sub_key = state_store.create_sub_key(
        parent_key_or_token=ext_key["token"],
        name="Worker Sub-Agent 1",
    )
    assert sub_key["daily_limit_usd"] == 0.25
    assert sub_key["key_type"] == "SUB_KEY"
    assert sub_key["project_id"] == proj["id"]
    assert sub_key["max_model_tier"] == "m"

    # 4. Project list includes connected keys count
    projects = state_store.list_projects()
    matched = [p for p in projects if p["id"] == proj["id"]][0]
    assert matched["connected_keys_count"] >= 2


def test_validate_external_key_and_spending_caps():
    """Verify validation, spend limits, and over-budget rejection."""
    proj = state_store.create_project(
        name="Budget Test Project",
        daily_limit_usd=0.50,
        default_sub_key_daily_usd=0.25,
        max_model_tier="l",
    )

    ext_key = state_store.create_external_key(
        project_id=proj["id"],
        name="Capped Agent",
        daily_limit_usd=0.10,
        max_model_tier="l",
    )

    # Initially valid
    valid, k_meta, p_meta, msg = state_store.validate_external_key(ext_key["token"])
    assert valid is True
    assert msg == "OK"

    # Record usage under cap
    state_store.record_external_usage(ext_key["id"], proj["id"], cost_usd=0.05, tokens=200)
    k_updated = state_store.get_external_key(ext_key["id"])
    assert k_updated["current_period_spend"] == 0.05
    assert k_updated["total_requests"] == 1

    # Record usage exceeding daily cap ($0.10 limit, adding $0.06 -> $0.11)
    state_store.record_external_usage(ext_key["id"], proj["id"], cost_usd=0.06, tokens=300)
    valid_after, _, _, err_after = state_store.validate_external_key(ext_key["token"])
    assert valid_after is False
    assert "Daily allocation ceiling reached" in err_after


def test_gateway_rest_endpoints():
    """Verify admin REST endpoints and external client gateway endpoints."""
    # Setup admin auth token
    admin_token = state_store.create_auth_token(created_by="admin_test")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 1. Cloudflare gateway config
    res_cfg = client.post(
        "/api/gateway/config",
        headers=admin_headers,
        json={"cloudflare_gateway_url": "https://gateway.example.com"},
    )
    assert res_cfg.status_code == 200
    assert res_cfg.json()["cloudflare_gateway_url"] == "https://gateway.example.com"

    # 2. Gateway Info
    res_info = client.get("/api/gateway/info", headers=admin_headers)
    assert res_info.status_code == 200
    info_data = res_info.json()
    assert info_data["cloudflare_gateway_url"] == "https://gateway.example.com"
    assert "xs" in info_data["tiers"]

    # 3. Create project via REST
    res_p = client.post(
        "/api/projects",
        headers=admin_headers,
        json={
            "name": "Cloudflare Client Team",
            "daily_limit_usd": 2.00,
            "default_sub_key_daily_usd": 0.25,
            "max_model_tier": "m",
        },
    )
    assert res_p.status_code == 200
    p_id = res_p.json()["project"]["id"]

    # 4. Create external key via REST
    res_k = client.post(
        "/api/external-keys",
        headers=admin_headers,
        json={
            "project_id": p_id,
            "name": "Hermes Remote Client",
            "daily_limit_usd": 0.25,
            "max_model_tier": "s",
        },
    )
    assert res_k.status_code == 200
    ext_token = res_k.json()["key"]["token"]
    assert ext_token.startswith("vkm_ext_")

    # 5. External client calls /v1/allocation using external key
    client_headers = {"Authorization": f"Bearer {ext_token}"}
    res_alloc = client.get("/v1/allocation", headers=client_headers)
    assert res_alloc.status_code == 200
    alloc_data = res_alloc.json()
    assert alloc_data["daily_limit_usd"] == 0.25
    assert alloc_data["max_model_tier"] == "s"
    assert alloc_data["project_name"] == "Cloudflare Client Team"

    # 6. External client calls /v1/models (should only see models up to tier 's')
    res_models = client.get("/v1/models", headers=client_headers)
    assert res_models.status_code == 200
    models_data = res_models.json()["data"]
    tier_ids = [m["id"] for m in models_data]
    assert "xs" in tier_ids
    assert "s" in tier_ids
    assert "m" not in tier_ids
    assert "xl" not in tier_ids

    # 7. External client delegates a subkey via /v1/subkeys (defaults to 0.25)
    res_sub = client.post(
        "/v1/subkeys",
        headers=client_headers,
        json={"name": "Sub Agent Alpha"},
    )
    assert res_sub.status_code == 200
    sub_data = res_sub.json()
    assert sub_data["success"] is True
    assert sub_data["sub_key"]["daily_limit_usd"] == 0.25
    assert sub_data["sub_key"]["max_model_tier"] == "s"


@pytest.mark.asyncio
async def test_gateway_chat_completion_proxy_and_tier_restriction():
    """Verify tier enforcement, proxy forwarding, and usage metering in /v1/chat/completions."""
    # Create an external key with max tier 's'
    p = state_store.create_project(name="Proxy Test", daily_limit_usd=1.00, max_model_tier="s")
    k = state_store.create_external_key(
        project_id=p["id"],
        name="Tier Enforced Key",
        daily_limit_usd=0.25,
        max_model_tier="s",
    )
    client_headers = {"Authorization": f"Bearer {k['token']}"}

    # 1. Attempt to call tier 'xl' (exceeds 's' limit) -> Expect 403 Forbidden
    res_denied = client.post(
        "/v1/chat/completions",
        headers=client_headers,
        json={
            "model": "xl",
            "messages": [{"role": "user", "content": "Hello"}],
        },
    )
    assert res_denied.status_code == 403
    assert res_denied.json()["detail"]["error"]["code"] == "model_tier_exceeded"

    # 2. Call tier 'xs' with mocked Venice backend
    mock_venice_response = {
        "id": "chatcmpl-test-123",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "llama-3.2-3b",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Venice API connection verified successfully"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 15,
            "completion_tokens": 7,
            "total_tokens": 22,
        },
    }

    with patch("venice_key_manager.client.VeniceClient.chat_completion", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = mock_venice_response

        res_ok = client.post(
            "/v1/chat/completions",
            headers=client_headers,
            json={
                "model": "xs",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )
        assert res_ok.status_code == 200
        data = res_ok.json()
        assert data["choices"][0]["message"]["content"] == "Venice API connection verified successfully"
        assert res_ok.headers["X-VKM-Model-Tier"] == "xs"
        assert "X-VKM-Cost-USD" in res_ok.headers

        # Verify usage was recorded in state_store
        key_after = state_store.get_external_key(k["id"])
        assert key_after["total_requests"] == 1
        assert key_after["current_period_spend"] > 0


@pytest.mark.asyncio
async def test_mcp_gateway_and_project_tools():
    """Verify MCP tools for project management and external keys."""
    server = VeniceMCPServer()

    # 1. Create project tool
    create_proj_res = await server.execute_tool(
        "venice_create_project",
        {
            "name": "MCP Multi-Agent Pool",
            "daily_limit_usd": 5.00,
            "default_sub_key_daily_usd": 0.25,
            "max_model_tier": "l",
        },
    )
    import json
    proj_data = json.loads(create_proj_res)
    assert proj_data["status"] == "success"
    p_id = proj_data["project"]["id"]

    # 2. List projects tool
    list_p_res = await server.execute_tool("venice_list_projects", {})
    list_p_data = json.loads(list_p_res)
    assert list_p_data["total"] >= 1

    # 3. Create external key tool
    create_key_res = await server.execute_tool(
        "venice_create_external_key",
        {
            "project_id": p_id,
            "name": "MCP Test Agent",
            "daily_limit_usd": 0.25,
            "max_model_tier": "m",
        },
    )
    key_data = json.loads(create_key_res)
    assert key_data["status"] == "success"
    token = key_data["key"]["token"]
    assert "curl_example" in key_data["key"]

    # 4. Create sub-key tool (defaults to 0.25)
    create_sub_res = await server.execute_tool(
        "venice_create_sub_key",
        {
            "parent_key_or_token": token,
            "name": "MCP Sub-Worker",
        },
    )
    sub_data = json.loads(create_sub_res)
    assert sub_data["status"] == "success"
    assert sub_data["sub_key"]["daily_limit_usd"] == 0.25

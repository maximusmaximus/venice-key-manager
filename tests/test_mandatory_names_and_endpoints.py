"""
Tests for mandatory naming enforcement and dual network endpoints delivery
(Tailscale address and Cloudflare DNS URL).
"""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

from venice_key_manager.models import (
    KeyCreateRequest,
    ExternalKeyCreateRequest,
    SubKeyCreateRequest,
    ProjectCreateRequest,
)
from venice_key_manager.state import state_store
from venice_key_manager.mcp_server import VeniceMCPServer
from venice_key_manager.web.app import app


def test_mandatory_name_model_validation():
    """Verify models reject empty or whitespace names."""
    # 1. Native KeyCreateRequest
    with pytest.raises(Exception):
        KeyCreateRequest(description="")
    with pytest.raises(Exception):
        KeyCreateRequest(description="   ")

    # 2. ExternalKeyCreateRequest
    with pytest.raises(Exception):
        ExternalKeyCreateRequest(project_id="proj_1", name="")
    with pytest.raises(Exception):
        ExternalKeyCreateRequest(project_id="proj_1", name="   ")

    # 3. SubKeyCreateRequest
    with pytest.raises(Exception):
        SubKeyCreateRequest(name="")
    with pytest.raises(Exception):
        SubKeyCreateRequest(name="  ")

    # 4. ProjectCreateRequest
    with pytest.raises(Exception):
        ProjectCreateRequest(name="")
    with pytest.raises(Exception):
        ProjectCreateRequest(name="  ")


def test_state_store_mandatory_names():
    """Verify state_store methods reject empty names."""
    # Project
    with pytest.raises(ValueError):
        state_store.create_project(name="")

    # External Key
    p = state_store.create_project(name="Test Mandatory Proj")
    with pytest.raises(ValueError):
        state_store.create_external_key(project_id=p["id"], name="")

    # Sub-Key
    ext_key = state_store.create_external_key(project_id=p["id"], name="Parent Ext Key")
    with pytest.raises(ValueError):
        state_store.create_sub_key(parent_key_or_token=ext_key["token"], name="")


def test_get_network_endpoints():
    """Verify get_network_endpoints returns both Tailscale address and Cloudflare DNS URL."""
    endpoints = state_store.get_network_endpoints()
    assert "tailscale_address" in endpoints
    assert "tailscale_v1_url" in endpoints
    assert "cloudflare_dns_url" in endpoints
    assert "cloudflare_v1_url" in endpoints

    assert "100.99.202.75" in endpoints["tailscale_v1_url"]
    assert "trycloudflare.com" in endpoints["cloudflare_v1_url"] or endpoints["cloudflare_dns_url"].startswith("http")


@pytest.mark.asyncio
async def test_mcp_mandatory_names_and_dual_endpoints():
    """Verify MCP tools enforce names and return both Tailscale and Cloudflare endpoints."""
    mock_client = MagicMock()
    mock_client.create_key = AsyncMock(return_value=MagicMock(
        model_dump=lambda: {
            "id": "key_test_123",
            "apiKey": "vapi_test_abc123",
            "description": "Named MCP Key",
        }
    ))
    mock_client.create_batch_keys = AsyncMock(return_value=[
        MagicMock(model_dump=lambda: {"id": "k1", "apiKey": "v1", "description": "pfx-01"})
    ])

    server = VeniceMCPServer(client=mock_client)

    # 1. Native key without name rejected
    err_res = await server.execute_tool("venice_create_key", {"description": "   "})
    assert "error" in json.loads(err_res)

    # 2. Native key with name returns endpoints
    ok_res = await server.execute_tool("venice_create_key", {"description": "Named MCP Key"})
    ok_data = json.loads(ok_res)
    assert "endpoints" in ok_data
    assert "endpoints_delivery" in ok_data
    assert "tailscale_address" in ok_data["endpoints_delivery"]
    assert "cloudflare_dns_url" in ok_data["endpoints_delivery"]

    # 3. External key without name rejected
    p = state_store.create_project(name="MCP Proj For Endpoints")
    err_ext = await server.execute_tool("venice_create_external_key", {"project_id": p["id"], "name": ""})
    assert "error" in json.loads(err_ext)

    # 4. External key with name returns dual endpoints & dual cURLs
    ok_ext = await server.execute_tool("venice_create_external_key", {"project_id": p["id"], "name": "Agent Remote 1"})
    ext_data = json.loads(ok_ext)
    assert ext_data["status"] == "success"
    assert "endpoints" in ext_data
    assert "tailscale_address" in ext_data["key"]
    assert "cloudflare_dns_url" in ext_data["key"]
    assert "tailscale_curl_example" in ext_data["key"]
    assert "cloudflare_curl_example" in ext_data["key"]

    # 5. Sub-key with name returns dual endpoints
    ok_sub = await server.execute_tool("venice_create_sub_key", {
        "parent_key_or_token": ext_data["key"]["token"],
        "name": "Sub-Agent 1"
    })
    sub_data = json.loads(ok_sub)
    assert sub_data["status"] == "success"
    assert "endpoints" in sub_data
    assert "tailscale_curl_example" in sub_data["sub_key"]
    assert "cloudflare_curl_example" in sub_data["sub_key"]


def test_rest_api_dual_endpoints_delivery():
    """Verify REST API returns endpoints on key creation."""
    client = TestClient(app)
    # Auth token for test
    token = state_store.create_auth_token(notes="test_rest")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Project creation
    p_res = client.post("/api/projects", json={"name": "REST Test Proj"}, headers=headers)
    assert p_res.status_code == 200
    p_id = p_res.json()["project"]["id"]

    # 2. External key creation
    ext_res = client.post(
        "/api/external-keys",
        json={"project_id": p_id, "name": "REST External Agent"},
        headers=headers
    )
    assert ext_res.status_code == 200
    ext_json = ext_res.json()
    assert "endpoints" in ext_json
    assert "tailscale_v1_url" in ext_json["endpoints"]
    assert "cloudflare_v1_url" in ext_json["endpoints"]

    # 3. Sub-key creation
    sub_res = client.post(
        f"/api/external-keys/{ext_json['key']['id']}/subkeys",
        json={"name": "REST Child Subkey"},
        headers=headers
    )
    assert sub_res.status_code == 200
    sub_json = sub_res.json()
    assert "endpoints" in sub_json
    assert "tailscale_v1_url" in sub_json["endpoints"]
    assert "cloudflare_v1_url" in sub_json["endpoints"]

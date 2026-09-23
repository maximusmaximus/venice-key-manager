import json
import pytest
from venice_key_manager.mcp_server import VeniceMCPServer


@pytest.mark.asyncio
async def test_mcp_initialize():
    server = VeniceMCPServer()
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"}
    }
    resp_str = await server.handle_json_rpc(json.dumps(req))
    assert resp_str is not None
    resp = json.loads(resp_str)
    assert resp["id"] == 1
    assert "capabilities" in resp["result"]
    assert "tools" in resp["result"]["capabilities"]


@pytest.mark.asyncio
async def test_mcp_tools_list():
    server = VeniceMCPServer()
    req = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {}
    }
    resp_str = await server.handle_json_rpc(json.dumps(req))
    assert resp_str is not None
    resp = json.loads(resp_str)
    tools = resp["result"]["tools"]
    tool_names = [t["name"] for t in tools]

    assert "venice_list_keys" in tool_names
    assert "venice_create_key" in tool_names
    assert "venice_cycle_key" in tool_names
    assert "venice_update_key_limit" in tool_names
    assert "venice_revoke_key" in tool_names
    assert "venice_get_account_balance" in tool_names
    assert "venice_list_models" in tool_names
    assert "venice_test_inference" in tool_names
    assert "venice_export_backup" in tool_names

"""
Unit & Integration Tests for MCP Server JSON-RPC 2.0 protocol.
"""

import sys
import io
import json
from pathlib import Path

# Fix Windows console UTF-8 encoding
if "pytest" not in sys.modules and hasattr(sys.stdout, "buffer") and not getattr(sys.stdout, "closed", False):
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

# Add project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp.server import VeniceMCPServer


def test_mcp_protocol():
    print("[1/3] Testing MCP Server initialize & tools/list...")
    server = VeniceMCPServer()

    # 1. Initialize
    init_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0.0"}
        }
    }
    init_res = server.handle_request(init_req)
    assert init_res.get("result", {}).get("protocolVersion") == "2024-11-05"
    assert "tools" in init_res.get("result", {}).get("capabilities", {})
    print("  [+] Initialize handshake verified.")

    # 2. Tools List
    tools_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    tools_res = server.handle_request(tools_req)
    tools = tools_res.get("result", {}).get("tools", [])
    assert len(tools) >= 10, f"Expected at least 10 tools, found {len(tools)}"
    tool_names = [t["name"] for t in tools]
    print(f"  [+] Registered MCP Tools ({len(tools)}): {', '.join(tool_names[:6])}...")


def test_mcp_tool_execution():
    print("[2/3] Testing MCP tool execution (venice_get_balances_and_tier)...")
    server = VeniceMCPServer()
    call_req = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "venice_get_balances_and_tier",
            "arguments": {}
        }
    }
    call_res = server.handle_request(call_req)
    assert not call_res.get("result", {}).get("isError")
    content = call_res.get("result", {}).get("content", [])[0]["text"]
    parsed = json.loads(content)
    assert "balances" in parsed
    print(f"  [+] MCP balance tool execution verified: {parsed.get('balances')}")


def test_mcp_tg_tool():
    print("[3/3] Testing MCP tool execution (tg_list_agent_bots)...")
    server = VeniceMCPServer()
    call_req = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "tg_list_agent_bots",
            "arguments": {}
        }
    }
    call_res = server.handle_request(call_req)
    assert not call_res.get("result", {}).get("isError")
    content = call_res.get("result", {}).get("content", [])[0]["text"]
    parsed = json.loads(content)
    assert "agent_bots" in parsed
    print(f"  [+] MCP agent bots tool execution verified ({len(parsed['agent_bots'])} bots found).")


if __name__ == "__main__":
    test_mcp_protocol()
    test_mcp_tool_execution()
    test_mcp_tg_tool()
    print("\nALL MCP TESTS PASSED!")

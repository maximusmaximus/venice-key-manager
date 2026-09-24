import sys
import json
import asyncio
import logging
from typing import Dict, Any, List, Optional

from .client import VeniceClient
from .models import (
    KeyCreateRequest,
    KeyUpdateRequest,
    KeyCycleRequest,
    InferenceTestRequest,
)
from .state import state_store
from .report import DailyKeyReport

logger = logging.getLogger("venice_mcp")


class VeniceMCPServer:
    def __init__(self, client: Optional[VeniceClient] = None):
        self.client = client or VeniceClient()
        self.tools = self._build_tool_definitions()

    def _build_tool_definitions(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "venice_list_keys",
                "description": "List all Venice.ai API keys with their budget limits, reset period (EPOCH/MONTH/LIFETIME), current usage, remaining USD, and low-balance warnings.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "Optional category filter (e.g. Agents, Production, Testing)"},
                        "only_low_balance": {"type": "boolean", "description": "If true, only return keys with remaining balance below warning threshold"}
                    }
                }
            },
            {
                "name": "venice_create_key",
                "description": "Create a new Venice.ai API key with spending caps (USD), reset period, key type, and category.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string", "description": "Human-readable label for this key"},
                        "daily_usd": {"type": "number", "description": "Spending limit in USD (e.g. 0.50, 2.0)"},
                        "limit_period": {
                            "type": "string",
                            "enum": ["EPOCH", "MONTH", "LIFETIME"],
                            "default": "EPOCH",
                            "description": "Reset period: EPOCH (daily), MONTH (monthly), or LIFETIME (permanent cap)"
                        },
                        "api_key_type": {
                            "type": "string",
                            "enum": ["INFERENCE", "ADMIN"],
                            "default": "INFERENCE",
                            "description": "Key type: INFERENCE for model calls, ADMIN for key management"
                        },
                        "category": {"type": "string", "default": "Default", "description": "Category group for this key"},
                        "custom_threshold": {"type": "number", "description": "Custom low-balance alert threshold in USD"}
                    },
                    "required": ["description"]
                }
            },
            {
                "name": "venice_cycle_key",
                "description": "Rotate/cycle an existing API key: generates a replacement key with matching limits/category and optionally revokes the old key.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "key_id": {"type": "string", "description": "The ID of the key to cycle"},
                        "revoke_old": {"type": "boolean", "default": True, "description": "Whether to delete the old key after replacement"},
                        "new_daily_usd": {"type": "number", "description": "Optional new USD spend limit for replacement key"},
                        "new_description": {"type": "string", "description": "Optional new description label"}
                    },
                    "required": ["key_id"]
                }
            },
            {
                "name": "venice_update_key_limit",
                "description": "Update the spending ceiling, reset period, or category of an existing Venice key.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "key_id": {"type": "string", "description": "The ID of the key to update"},
                        "daily_usd": {"type": "number", "description": "New spend limit in USD"},
                        "limit_period": {"type": "string", "enum": ["EPOCH", "MONTH", "LIFETIME"]},
                        "category": {"type": "string", "description": "New category group"},
                        "custom_threshold": {"type": "number", "description": "Custom low-balance alert threshold"}
                    },
                    "required": ["key_id"]
                }
            },
            {
                "name": "venice_revoke_key",
                "description": "Permanently revoke and delete a Venice.ai API key by ID.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "key_id": {"type": "string", "description": "The key ID to delete"}
                    },
                    "required": ["key_id"]
                }
            },
            {
                "name": "venice_get_account_balance",
                "description": "Fetch real-time Venice.ai account balances (USD, DIEM, Credits), access permission state, and low-balance warning.",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "venice_list_models",
                "description": "List all available Venice.ai models with capability flags (E2EE hardware enclaves, ZDR, Vision, Function calling) and token pricing.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "privacy_filter": {
                            "type": "string",
                            "enum": ["all", "e2ee", "private", "anonymized"],
                            "default": "all",
                            "description": "Filter by privacy level (e2ee = confidential hardware enclaves)"
                        },
                        "query": {"type": "string", "description": "Search query by model name or id"}
                    }
                }
            },
            {
                "name": "venice_test_inference",
                "description": "Run a lightweight test inference against a Venice model to verify key health and measure latency.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "default": "Say 'Venice is online' in 4 words."},
                        "model": {"type": "string", "default": "deepseek-v4-flash"},
                        "api_key": {"type": "string", "description": "Optional specific key to test; defaults to master key"}
                    }
                }
            },
            {
                "name": "venice_export_backup",
                "description": "Export a complete downloadable backup of local categories, per-key thresholds, notes, and global settings in JSON format.",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "venice_daily_report",
                "description": "Generate a comprehensive daily report of Venice key usage, spending metrics (current period & 7-day trailing), category breakdowns, top consumers, and low-balance warnings. Optionally dispatch directly to Telegram.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "send_telegram": {
                            "type": "boolean",
                            "default": False,
                            "description": "Whether to send the generated report directly to configured Telegram chat"
                        },
                        "chat_id": {
                            "type": "string",
                            "description": "Optional Telegram chat ID override (defaults to configured allowed chat)"
                        }
                    }
                }
            }
        ]

    async def execute_tool(self, name: str, args: Dict[str, Any]) -> str:
        try:
            if name == "venice_list_keys":
                cat = args.get("category")
                only_low = args.get("only_low_balance", False)
                keys = await self.client.list_keys(category_filter=cat)
                if only_low:
                    keys = [k for k in keys if k.is_low_balance]
                return json.dumps([k.model_dump() for k in keys], indent=2)

            elif name == "venice_create_key":
                req = KeyCreateRequest(
                    description=args["description"],
                    daily_usd=args.get("daily_usd"),
                    limitPeriod=args.get("limit_period", "EPOCH"),
                    apiKeyType=args.get("api_key_type", "INFERENCE"),
                    category=args.get("category", "Default"),
                    custom_threshold=args.get("custom_threshold")
                )
                res = await self.client.create_key(req)
                return json.dumps(res.model_dump(), indent=2)

            elif name == "venice_cycle_key":
                req = KeyCycleRequest(
                    id=args["key_id"],
                    revoke_old=args.get("revoke_old", True),
                    new_daily_usd=args.get("new_daily_usd"),
                    new_description=args.get("new_description")
                )
                new_key_resp, revoked = await self.client.cycle_key(req)
                return json.dumps({
                    "status": "cycled",
                    "new_key": new_key_resp.model_dump(),
                    "old_key_id": req.id,
                    "old_key_revoked": revoked
                }, indent=2)

            elif name == "venice_update_key_limit":
                req = KeyUpdateRequest(
                    id=args["key_id"],
                    daily_usd=args.get("daily_usd"),
                    limitPeriod=args.get("limit_period"),
                    category=args.get("category"),
                    custom_threshold=args.get("custom_threshold")
                )
                success = await self.client.update_key(req)
                return json.dumps({"success": success, "key_id": req.id}, indent=2)

            elif name == "venice_revoke_key":
                success = await self.client.revoke_key(args["key_id"])
                return json.dumps({"success": success, "revoked_key_id": args["key_id"]}, indent=2)

            elif name == "venice_get_account_balance":
                rates = await self.client.get_rate_limits()
                thresh = state_store.get_global_threshold()
                is_low = rates.balances.USD <= thresh
                return json.dumps({
                    "balances": rates.balances.model_dump(),
                    "access_permitted": rates.accessPermitted,
                    "next_epoch_begins": rates.nextEpochBegins,
                    "warning_threshold_usd": thresh,
                    "is_low_balance": is_low
                }, indent=2)

            elif name == "venice_list_models":
                pfilter = args.get("privacy_filter", "all")
                query = (args.get("query") or "").lower()
                models = await self.client.list_models()
                filtered = []
                for m in models:
                    if pfilter != "all" and m.privacy.lower() != pfilter.lower():
                        continue
                    if query and query not in m.id.lower() and query not in m.name.lower():
                        continue
                    filtered.append(m.model_dump())
                return json.dumps({"total": len(filtered), "models": filtered}, indent=2)

            elif name == "venice_test_inference":
                res = await self.client.test_inference(
                    prompt=args.get("prompt", "Say hello in 3 words"),
                    model=args.get("model", "deepseek-v4-flash"),
                    api_key=args.get("api_key")
                )
                return json.dumps(res.model_dump(), indent=2)

            elif name == "venice_export_backup":
                backup = state_store.export_backup()
                return json.dumps(backup.model_dump(), indent=2)

            elif name == "venice_daily_report":
                reporter = DailyKeyReport(client=self.client)
                data = await reporter.generate_report_data()
                formatted_md = reporter.format_markdown(data)
                sent_tg = False
                if args.get("send_telegram", False):
                    sent_tg = await reporter.send_to_telegram(chat_id=args.get("chat_id"))
                return json.dumps({
                    "markdown": formatted_md,
                    "metrics": data,
                    "sent_to_telegram": sent_tg
                }, indent=2)

            else:
                return json.dumps({"error": f"Unknown tool: {name}"})

        except Exception as e:
            logger.exception(f"Error executing {name}")
            return json.dumps({"error": str(e)})

    async def handle_json_rpc(self, line: str) -> Optional[str]:
        if not line.strip():
            return None
        try:
            req = json.loads(line)
        except Exception:
            return json.dumps({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None})

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method == "initialize":
            return json.dumps({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False}
                    },
                    "serverInfo": {
                        "name": "venice-key-manager",
                        "version": "1.0.0"
                    }
                }
            })

        elif method == "notifications/initialized":
            return None

        elif method == "tools/list":
            return json.dumps({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": self.tools
                }
            })

        elif method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments", {})
            output_text = await self.execute_tool(name, arguments)
            return json.dumps({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": output_text
                        }
                    ]
                }
            })

        elif method == "resources/list":
            return json.dumps({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "resources": [
                        {
                            "uri": "venice://account/overview",
                            "name": "Venice Account Overview",
                            "mimeType": "application/json"
                        },
                        {
                            "uri": "venice://models/privacy",
                            "name": "Venice E2EE & Privacy Models",
                            "mimeType": "application/json"
                        }
                    ]
                }
            })

        elif method == "resources/read":
            uri = params.get("uri")
            if uri == "venice://account/overview":
                rates = await self.client.get_rate_limits()
                text = json.dumps(rates.model_dump(), indent=2)
            else:
                models = await self.client.list_models()
                e2ee = [m.model_dump() for m in models if m.privacy == "e2ee"]
                text = json.dumps(e2ee, indent=2)

            return json.dumps({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "contents": [
                        {
                            "uri": uri,
                            "mimeType": "application/json",
                            "text": text
                        }
                    ]
                }
            })

        else:
            return json.dumps({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method {method} not found"}
            })

    async def run_stdio(self):
        """Run MCP server over standard input/output."""
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        while True:
            line_bytes = await reader.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8").strip()
            if not line:
                continue
            response = await self.handle_json_rpc(line)
            if response:
                sys.stdout.write(response + "\n")
                sys.stdout.flush()


def main():
    server = VeniceMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()

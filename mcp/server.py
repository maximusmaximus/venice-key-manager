"""
Model Context Protocol (MCP) Server for Venice.ai & Telegram Agent Key Management.
Implements the JSON-RPC 2.0 Stdio specification for integration with Antigravity / Claude.
"""

import sys
import json
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.vault import KeyVault
from core.venice_client import VeniceClient
from core.tg_manager import TelegramAgentManager
from core.deployer import ConfigDeployer

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("venice_mcp")


class VeniceMCPServer:
    PROTOCOL_VERSION = "2024-11-05"
    SERVER_NAME = "venice-key-manager"
    SERVER_VERSION = "1.0.0"

    def __init__(self):
        self.vault = KeyVault()
        self.tg_manager = TelegramAgentManager(self.vault)

    def _get_venice_client(self) -> VeniceClient:
        return VeniceClient(
            admin_key=self.vault.get_venice_admin_key(),
            inference_key=self.vault.get_venice_inference_key(),
            base_url=self.vault.data.get("venice", {}).get("base_url", "")
        )

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "venice_get_balances_and_tier",
                "description": "Retrieves real-time USD balance, DIEM balance, bundled credits, and subscription tier for the Venice.ai account.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "api_key": {
                            "type": "string",
                            "description": "Optional specific API key to check. Defaults to active vault key."
                        }
                    }
                }
            },
            {
                "name": "venice_list_rate_limits",
                "description": "Retrieves rate limits per model (RPM, TPM) and current epoch reset timestamp from Venice.ai.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "api_key": {
                            "type": "string",
                            "description": "Optional specific API key. Defaults to active vault key."
                        }
                    }
                }
            },
            {
                "name": "venice_list_keys",
                "description": "Lists all active API keys associated with the Venice.ai account. (Requires ADMIN key).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "admin_key": {
                            "type": "string",
                            "description": "Optional master Admin key. Defaults to vault admin key."
                        }
                    }
                }
            },
            {
                "name": "venice_create_key",
                "description": "Generates a new Venice.ai API key (INFERENCE or ADMIN) with optional consumption limit. (Requires ADMIN key).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "Label or description for the key (e.g., 'Agent-Worker-01')"
                        },
                        "key_type": {
                            "type": "string",
                            "enum": ["INFERENCE", "ADMIN"],
                            "description": "Type of key to issue. Defaults to 'INFERENCE'."
                        },
                        "limit_usd": {
                            "type": "number",
                            "description": "Optional monthly budget consumption limit in USD (e.g., 5.0)."
                        },
                        "limit_period": {
                            "type": "string",
                            "enum": ["MONTH", "DAY"],
                            "description": "Consumption limit period. Defaults to 'MONTH'."
                        },
                        "expires_at": {
                            "type": "string",
                            "description": "Optional ISO 8601 expiration timestamp."
                        }
                    },
                    "required": ["description"]
                }
            },
            {
                "name": "venice_revoke_key",
                "description": "Revokes and deletes an API key on Venice.ai by ID. (Requires ADMIN key).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "key_id": {
                            "type": "string",
                            "description": "The unique ID of the API key to revoke."
                        }
                    },
                    "required": ["key_id"]
                }
            },
            {
                "name": "venice_deploy_key",
                "description": "Deploys a Venice API key to local configuration (e.g. config.yaml or path set in AGENT_CONFIG_PATH).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "key_string": {
                            "type": "string",
                            "description": "The API key string to deploy."
                        },
                        "target_path": {
                            "type": "string",
                            "description": "Optional target file path. Defaults to AGENT_CONFIG_PATH or config.yaml."
                        }
                    },
                    "required": ["key_string"]
                }
            },
            {
                "name": "venice_test_inference",
                "description": "Runs a light inference test against Venice models (defaulting to deepseek-v4-flash) to measure latency and token cost.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "The test prompt to send."
                        },
                        "model": {
                            "type": "string",
                            "description": "Model ID to test (default: 'deepseek-v4-flash')."
                        },
                        "max_tokens": {
                            "type": "integer",
                            "description": "Max response tokens (default: 100)."
                        }
                    },
                    "required": ["prompt"]
                }
            },
            {
                "name": "tg_list_agent_bots",
                "description": "Lists all registered Telegram agent bots, usernames, statuses, and assigned configuration targets.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "check_live": {
                            "type": "boolean",
                            "description": "Whether to perform live getMe health check against Telegram API."
                        }
                    }
                }
            },
            {
                "name": "tg_register_agent_bot",
                "description": "Validates a Telegram bot token via getMe and registers it to a named agent in the vault.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "agent_name": {
                            "type": "string",
                            "description": "Name of the agent (e.g. 'primary-agent', 'worker-agent', 'swarm-node')."
                        },
                        "bot_token": {
                            "type": "string",
                            "description": "The Telegram bot HTTP API token from @BotFather."
                        },
                        "config_path": {
                            "type": "string",
                            "description": "Optional path to the agent's config file (default: config.yaml)."
                        },
                        "notes": {
                            "type": "string",
                            "description": "Optional description of the bot's purpose."
                        }
                    },
                    "required": ["agent_name", "bot_token"]
                }
            },
            {
                "name": "tg_deploy_agent_bot",
                "description": "Deploys an agent's registered Telegram bot token to its assigned configuration file.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "agent_name": {
                            "type": "string",
                            "description": "The agent name whose bot token should be deployed."
                        },
                        "target_path": {
                            "type": "string",
                            "description": "Optional override target path."
                        }
                    },
                    "required": ["agent_name"]
                }
            },
            {
                "name": "tg_test_agent_bot",
                "description": "Sends a verification test ping from an agent bot to the authorized Telegram chat.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "agent_name": {
                            "type": "string",
                            "description": "The agent name whose bot to test."
                        },
                        "custom_text": {
                            "type": "string",
                            "description": "Optional custom message text."
                        }
                    },
                    "required": ["agent_name"]
                }
            },
            {
                "name": "tg_botfather_wizard",
                "description": "Generates guided instructions and deep link to create a new bot with @BotFather for a specific agent.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "agent_name": {
                            "type": "string",
                            "description": "The name of the agent needing a new Telegram bot."
                        },
                        "suggested_name": {
                            "type": "string",
                            "description": "Optional display name for the bot."
                        }
                    },
                    "required": ["agent_name"]
                }
            }
        ]

    def execute_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        client = self._get_venice_client()

        if name == "venice_get_balances_and_tier":
            key = args.get("api_key")
            res = client.get_rate_limits(key=key)
            if res.get("success"):
                return {
                    "balances": res.get("balances"),
                    "apiTier": res.get("apiTier"),
                    "keyExpiration": res.get("keyExpiration"),
                    "nextEpochBegins": res.get("nextEpochBegins")
                }
            return {"error": res.get("error")}

        elif name == "venice_list_rate_limits":
            key = args.get("api_key")
            res = client.get_rate_limits(key=key)
            if res.get("success"):
                raw = res.get("raw", {})
                return {
                    "rateLimits": raw.get("rateLimits", []),
                    "nextEpochBegins": raw.get("nextEpochBegins"),
                    "apiTier": raw.get("apiTier")
                }
            return {"error": res.get("error")}

        elif name == "venice_list_keys":
            admin_key = args.get("admin_key")
            return client.list_keys(admin_key=admin_key)

        elif name == "venice_create_key":
            desc = args.get("description", "Agent Key")
            k_type = args.get("key_type", "INFERENCE")
            limit = args.get("limit_usd")
            period = args.get("limit_period", "MONTH")
            expires = args.get("expires_at")
            res = client.create_key(
                description=desc,
                key_type=k_type,
                limit_usd=limit,
                limit_period=period,
                expires_at=expires
            )
            if res.get("success"):
                key_obj = res.get("key", {})
                self.vault.store_venice_key(key_obj)
            return res

        elif name == "venice_revoke_key":
            key_id = args.get("key_id")
            res = client.delete_key(key_id=key_id)
            if res.get("success"):
                self.vault.remove_venice_key(key_id)
            return res

        elif name == "venice_deploy_key":
            key_str = args.get("key_string")
            tgt = args.get("target_path")
            return ConfigDeployer.deploy_venice_key(key_str, Path(tgt) if tgt else None)

        elif name == "venice_test_inference":
            prompt = args.get("prompt")
            model = args.get("model", "deepseek-v4-flash")
            max_t = args.get("max_tokens", 100)
            return client.test_inference(prompt=prompt, model=model, max_tokens=max_t)

        elif name == "tg_list_agent_bots":
            chk = args.get("check_live", False)
            return {"agent_bots": self.tg_manager.list_agent_bots(check_live_status=chk)}

        elif name == "tg_register_agent_bot":
            agent = args.get("agent_name")
            token = args.get("bot_token")
            cfg = args.get("config_path", "")
            notes = args.get("notes", "")
            return self.tg_manager.register_agent_bot(agent, token, config_path=cfg, notes=notes)

        elif name == "tg_deploy_agent_bot":
            agent = args.get("agent_name")
            tgt = args.get("target_path")
            return self.tg_manager.deploy_agent_bot(agent, target_path=tgt)

        elif name == "tg_test_agent_bot":
            agent = args.get("agent_name")
            text = args.get("custom_text", "")
            bot = self.vault.get_agent_bot(agent)
            if not bot:
                return {"error": f"Agent '{agent}' not found in vault"}
            return self.tg_manager.send_test_message(bot["bot_token"], text=text)

        elif name == "tg_botfather_wizard":
            agent = args.get("agent_name")
            sug = args.get("suggested_name", "")
            return self.tg_manager.generate_botfather_wizard(agent, suggested_name=sug)

        return {"error": f"Unknown tool: {name}"}

    def handle_request(self, req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": self.PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": self.SERVER_NAME,
                        "version": self.SERVER_VERSION
                    }
                }
            }

        elif method == "notifications/initialized":
            return None

        elif method == "ping":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {}
            }

        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": self.get_tool_definitions()
                }
            }

        elif method == "tools/call":
            tool_name = params.get("name")
            args = params.get("arguments", {})
            try:
                tool_result = self.execute_tool(tool_name, args)
                is_error = "error" in tool_result and not tool_result.get("success", True)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(tool_result, indent=2)
                            }
                        ],
                        "isError": is_error
                    }
                }
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": f"Error executing {tool_name}: {str(e)}"}
                        ],
                        "isError": True
                    }
                }

        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Method '{method}' not found"
                }
            }

    def run_stdio(self):
        """Runs the JSON-RPC 2.0 loop reading from stdin and writing to stdout."""
        logger.info(f"Venice MCP Server started (Stdio transport).")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                resp = self.handle_request(req)
                if resp is not None:
                    sys.stdout.write(json.dumps(resp) + "\n")
                    sys.stdout.flush()
            except json.JSONDecodeError:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"}
                }
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()
            except Exception as e:
                logger.error(f"Server loop error: {e}")


if __name__ == "__main__":
    server = VeniceMCPServer()
    server.run_stdio()

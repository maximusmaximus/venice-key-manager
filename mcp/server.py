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
from core.mesh import FleetMeshManager

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("venice_mcp")


class VeniceMCPServer:
    PROTOCOL_VERSION = "2024-11-05"
    SERVER_NAME = "venice-key-manager"
    SERVER_VERSION = "1.0.0"

    def __init__(self):
        self.vault = KeyVault()
        self.tg_manager = TelegramAgentManager(self.vault)
        self.mesh = FleetMeshManager(self.vault)

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
            },
            {
                "name": "venice_fleet_list_nodes",
                "description": "Lists all Tailscale machines in the A2A mesh, checking live status, latency, and git versions.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "check_health": {
                            "type": "boolean",
                            "description": "Whether to perform live HTTP ping checks on nodes (default: true)."
                        }
                    }
                }
            },
            {
                "name": "venice_fleet_sync_node",
                "description": "Triggers git pull update on a specified machine ('local', 'mcmini', etc.) or 'all' nodes.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "node": {
                            "type": "string",
                            "description": "Target machine identifier ('local', 'mcmini', or 'all'). Defaults to 'local'."
                        }
                    }
                }
            },
            {
                "name": "venice_fleet_register_node",
                "description": "Pairs a new machine into the A2A fleet mesh with its base URL/port.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Unique identifier for the machine (e.g. 'mcmini', 'gpu-server')."
                        },
                        "base_url": {
                            "type": "string",
                            "description": "Base URL of the machine's Venice Key Manager service (e.g. 'http://100.64.0.2:8844')."
                        },
                        "label": {
                            "type": "string",
                            "description": "Display label for the machine (e.g. 'McMini (macOS)')."
                        }
                    },
                    "required": ["name", "base_url"]
                }
            },
            {
                "name": "venice_validate_guest_key",
                "description": "Ingests and validates a candidate Venice key without storing it, performing live balance check and inference latency test.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "description": "The candidate Venice API key to test and validate."
                        }
                    },
                    "required": ["key"]
                }
            },
            {
                "name": "venice_create_subkey",
                "description": "Mints an agent sub-key with custom or default ($0.25) daily budget cap and quality tier limit (xs to xl), with optional automatic deployment to an agent.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "label": {
                            "type": "string",
                            "description": "Description / label for the sub-key (e.g. 'Hermes Audio Worker')."
                        },
                        "budget_usd": {
                            "type": "number",
                            "description": "Maximum USD consumption budget limit (default: 0.25)."
                        },
                        "period": {
                            "type": "string",
                            "enum": ["DAY", "MONTH"],
                            "description": "Budget reset cycle (DAY or MONTH). Defaults to 'DAY'."
                        },
                        "quality_tier": {
                            "type": "string",
                            "enum": ["xs", "s", "m", "l", "xl"],
                            "description": "Maximum model quality tier permitted for this sub-key (default: 's')."
                        },
                        "target_agent": {
                            "type": "string",
                            "description": "Optional agent to immediately deploy this sub-key to (e.g. 'hermes-music', 'a2a-node')."
                        },
                        "target_node": {
                            "type": "string",
                            "description": "Target machine node ('local' or 'mcmini'). Defaults to 'local'."
                        }
                    },
                    "required": ["label"]
                }
            },
            {
                "name": "venice_apply_agent_key",
                "description": "Deploys a sub-key or master key to a target agent configuration on local or remote fleet node.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "agent_name": {
                            "type": "string",
                            "description": "Name of the agent (e.g. 'hermes-music', 'a2a-node', 'dawagent', 'worker-audio')."
                        },
                        "subkey_id": {
                            "type": "string",
                            "description": "Optional ID of the subkey to deploy."
                        },
                        "key_string": {
                            "type": "string",
                            "description": "Optional explicit key string to deploy."
                        },
                        "target_node": {
                            "type": "string",
                            "description": "Target machine node ('local' or 'mcmini'). Defaults to 'local'."
                        }
                    },
                    "required": ["agent_name"]
                }
            },
            {
                "name": "venice_get_pairing_status",
                "description": "Returns current machine pairing code, configured domain, and whether pairing is active.",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "venice_mint_allocation",
                "description": "Mints a claimable Venice API key allocation link (https://venice.vmu.cash/claim/<token>) with specified key count, validity window, and model quality tier, without directly sharing raw secret keys with agents.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "label": {
                            "type": "string",
                            "description": "Descriptive label for the key allocation (e.g. 'Hermes Music Producer Key')."
                        },
                        "target_agent": {
                            "type": "string",
                            "description": "Recipient agent identifier (e.g. 'hermes-music', 'dawagent', 'a2a-node')."
                        },
                        "allocated_keys_count": {
                            "type": "integer",
                            "description": "Number of keys allocated to this claim token (default: 1)."
                        },
                        "valid_from": {
                            "type": "string",
                            "description": "Optional ISO 8601 timestamp when claiming window opens (defaults to immediate)."
                        },
                        "valid_until": {
                            "type": "string",
                            "description": "Optional ISO 8601 timestamp when claiming window closes/expires."
                        },
                        "quality_tier": {
                            "type": "string",
                            "enum": ["xs", "s", "m", "l", "xl"],
                            "description": "Model quality tier limit for this key (default: 's')."
                        },
                        "budget_usd": {
                            "type": "number",
                            "description": "Budget consumption cap in USD (default: 0.25)."
                        },
                        "limit_period": {
                            "type": "string",
                            "enum": ["DAY", "MONTH"],
                            "description": "Budget limit reset period (DAY or MONTH). Defaults to 'DAY'."
                        },
                        "target_node": {
                            "type": "string",
                            "description": "Target machine node ('local' or 'mcmini'). Defaults to 'local'."
                        },
                        "api_key": {
                            "type": "string",
                            "description": "Optional specific API key to allocate. Defaults to active vault key."
                        }
                    },
                    "required": ["label", "target_agent"]
                }
            },
            {
                "name": "venice_inspect_allocation",
                "description": "Inspects an allocation link or token without exposing the secret Venice API key: reveals allocated keys count, claimed count, remaining claims, validity dates, quality tier, and whether it can currently be claimed.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "claim_token_or_url": {
                            "type": "string",
                            "description": "Full claim URL (e.g. https://venice.vmu.cash/claim/vclm_...) or raw claim token."
                        }
                    },
                    "required": ["claim_token_or_url"]
                }
            },
            {
                "name": "venice_claim_allocated_key",
                "description": "Agent claims its allocated Venice API key using a claim link or token, validating claim schedule and remaining quota. Optionally auto-deploys to local agent config.yaml.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "claim_token_or_url": {
                            "type": "string",
                            "description": "Full claim URL (e.g. https://venice.vmu.cash/claim/vclm_...) or raw claim token."
                        },
                        "agent_id": {
                            "type": "string",
                            "description": "Identifier of the agent claiming the key (e.g. 'hermes-music')."
                        },
                        "auto_deploy": {
                            "type": "boolean",
                            "description": "Whether to automatically deploy claimed key to local agent configuration (default: false)."
                        },
                        "target_path": {
                            "type": "string",
                            "description": "Optional explicit configuration file path to deploy to."
                        }
                    },
                    "required": ["claim_token_or_url"]
                }
            },
            {
                "name": "venice_list_allocations",
                "description": "Lists all key allocations across the fleet with claim URLs, validity dates, remaining claims count, and status.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target_agent": {
                            "type": "string",
                            "description": "Optional filter by target agent identifier."
                        },
                        "include_secret": {
                            "type": "boolean",
                            "description": "Whether to include raw unmasked secret keys (default: false)."
                        }
                    }
                }
            }
        ]

    def execute_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        client = self._get_venice_client()

        if name == "venice_get_balances_and_tier":
            key = args.get("api_key")
            thresholds = self.vault.get_balance_thresholds()
            status_info = client.check_balance_status(
                key=key,
                low_threshold_usd=thresholds.get("low_usd", 1.0),
                out_threshold_usd=thresholds.get("out_usd", 0.05)
            )
            res = client.get_rate_limits(key=key)
            if res.get("success"):
                return {
                    "balances": res.get("balances"),
                    "apiTier": res.get("apiTier"),
                    "keyExpiration": res.get("keyExpiration"),
                    "nextEpochBegins": res.get("nextEpochBegins"),
                    "status": status_info.get("status", "HEALTHY"),
                    "is_low": status_info.get("is_low", False),
                    "is_out": status_info.get("is_out", False),
                    "badge": status_info.get("badge", "🟢 HEALTHY"),
                    "warning": status_info.get("warning"),
                    "recharge_url": status_info.get("recharge_url", "https://venice.ai/settings/api")
                }
            if status_info.get("status") == "OUT":
                return {
                    "balances": {"USD": 0.0, "DIEM": 0.0},
                    "status": "OUT",
                    "is_low": False,
                    "is_out": True,
                    "badge": "🔴 OUT OF CREDITS",
                    "error": "Insufficient USD or Diem balance. Visit https://venice.ai/settings/api to add credits.",
                    "recharge_url": "https://venice.ai/settings/api"
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
            thresholds = self.vault.get_balance_thresholds()
            bal_status = client.check_balance_status(
                low_threshold_usd=thresholds.get("low_usd", 1.0),
                out_threshold_usd=thresholds.get("out_usd", 0.05)
            )
            if bal_status.get("is_out"):
                return {
                    "success": False,
                    "out_of_credits": True,
                    "status_code": 402,
                    "error": "Insufficient USD or Diem balance to complete inference request. Visit https://venice.ai/settings/api to add credits.",
                    "recharge_url": "https://venice.ai/settings/api"
                }
            res = client.test_inference(prompt=prompt, model=model, max_tokens=max_t)
            if res.get("success") and bal_status.get("is_low"):
                res["warning"] = bal_status.get("warning")
            return res

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

        elif name == "venice_fleet_list_nodes":
            chk = args.get("check_health", True)
            return {"nodes": self.mesh.list_nodes(check_health=chk)}

        elif name == "venice_fleet_sync_node":
            target = args.get("node", "local")
            if target == "all":
                return self.mesh.sync_all_nodes()
            elif target == "local":
                return self.mesh.sync_local_code()
            else:
                return self.mesh.sync_remote_node(target)

        elif name == "venice_fleet_register_node":
            name_val = args.get("name")
            base_url = args.get("base_url")
            label_val = args.get("label", "")
            return self.mesh.register_node(name_val, base_url, label=label_val)

        elif name == "venice_validate_guest_key":
            key_val = args.get("key", "").strip()
            return client.test_key(key_val)

        elif name == "venice_create_subkey":
            label = args.get("label", "SubKey")
            budget = float(args.get("budget_usd", 0.25))
            period = args.get("period", "DAY")
            tier = args.get("quality_tier", "s").lower()
            target_agent = args.get("target_agent")
            target_node = args.get("target_node", "local")

            admin_key = self.vault.get_venice_admin_key()
            if admin_key:
                res = client.create_key(
                    admin_key=admin_key,
                    description=label,
                    key_type="INFERENCE",
                    limit_usd=budget,
                    limit_period=period
                )
                if not res.get("success"):
                    return res
                created_key = res.get("key", {})
                key_id = created_key.get("id")
                key_str = res.get("key_string", key_id)
            else:
                import uuid
                key_id = f"sub_{uuid.uuid4().hex[:12]}"
                key_str = key_id

            sk_record = {
                "id": key_id,
                "label": label,
                "budget_usd": budget,
                "period": period,
                "quality_tier": tier,
                "assigned_agent": target_agent,
                "assigned_node": target_node,
                "key_string": key_str
            }
            self.vault.store_subkey(sk_record)

            deploy_result = None
            if target_agent:
                if target_node == "local" or not target_node:
                    deploy_result = ConfigDeployer.deploy_venice_key(key_str, agent_name=target_agent)
                else:
                    remote_res = self.mesh.call_remote_node(target_node, "/api/subkeys/apply", method="POST", data={
                        "key_string": key_str,
                        "agent_name": target_agent,
                        "subkey_id": key_id
                    })
                    deploy_result = remote_res

            return {
                "success": True,
                "subkey": sk_record,
                "deployment": deploy_result
            }

        elif name == "venice_apply_agent_key":
            agent_name = args.get("agent_name")
            target_node = args.get("target_node", "local")
            subkey_id = args.get("subkey_id")
            key_string = args.get("key_string")

            if not key_string and subkey_id:
                for sk in self.vault.get_subkeys():
                    if sk.get("id") == subkey_id:
                        key_string = sk.get("key_string")
                        break

            if not key_string:
                key_string = self.vault.get_venice_inference_key()

            if target_node == "local" or not target_node:
                return ConfigDeployer.deploy_venice_key(key_string, agent_name=agent_name)
            else:
                return self.mesh.call_remote_node(target_node, "/api/subkeys/apply", method="POST", data={
                    "key_string": key_string,
                    "agent_name": agent_name,
                    "subkey_id": subkey_id
                })

        elif name == "venice_get_pairing_status":
            cf = self.vault.get_cloudflare_config()
            return {
                "success": True,
                "pairing_code": self.vault.get_pairing_code(),
                "pairing_required": self.vault.is_pairing_required(),
                "domain": cf.get("domain", "")
            }

        elif name == "venice_mint_allocation":
            label = args.get("label", "")
            target_agent = args.get("target_agent", "")
            allocated_keys_count = args.get("allocated_keys_count", 1)
            valid_from = args.get("valid_from")
            valid_until = args.get("valid_until")
            quality_tier = args.get("quality_tier", "s")
            budget_usd = args.get("budget_usd", 0.25)
            limit_period = args.get("limit_period", "DAY")
            target_node = args.get("target_node", "local")
            api_key = args.get("api_key")

            alloc = self.vault.mint_allocation(
                label=label,
                target_agent=target_agent,
                allocated_keys_count=allocated_keys_count,
                valid_from=valid_from,
                valid_until=valid_until,
                quality_tier=quality_tier,
                budget_usd=budget_usd,
                limit_period=limit_period,
                target_node=target_node,
                api_key=api_key
            )
            return {
                "success": True,
                "allocation": alloc,
                "claim_url": alloc["claim_url"],
                "claim_token": alloc["claim_token"],
                "allocated_keys_count": alloc["allocated_keys_count"],
                "valid_from": alloc["valid_from"],
                "valid_until": alloc["valid_until"],
                "target_agent": alloc["target_agent"],
                "quality_tier": alloc["quality_tier"],
                "budget_usd": alloc["budget_usd"],
                "message": f"Successfully minted key allocation for '{alloc['target_agent']}'. Share the claim link: {alloc['claim_url']}"
            }

        elif name == "venice_inspect_allocation":
            token_or_url = args.get("claim_token_or_url", "")
            return self.vault.inspect_allocation(token_or_url)

        elif name == "venice_claim_allocated_key":
            token_or_url = args.get("claim_token_or_url", "")
            agent_id = args.get("agent_id", "")
            auto_deploy = args.get("auto_deploy", False)
            target_path = args.get("target_path")

            claim_res = self.vault.claim_allocation(
                token_or_url,
                agent_id=agent_id,
                client_info="MCP Tool venice_claim_allocated_key"
            )
            if not claim_res.get("success"):
                return claim_res

            if auto_deploy:
                key_str = claim_res.get("api_key")
                ag_name = agent_id or claim_res.get("target_agent")
                deploy_res = ConfigDeployer.deploy_venice_key(
                    key_str,
                    target_path=target_path,
                    agent_name=ag_name
                )
                claim_res["deployment"] = deploy_res
            return claim_res

        elif name == "venice_list_allocations":
            target_agent = args.get("target_agent")
            include_secret = args.get("include_secret", False)
            allocs = self.vault.get_allocations(include_secret=include_secret, target_agent=target_agent)
            return {
                "success": True,
                "total": len(allocs),
                "allocations": allocs
            }

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

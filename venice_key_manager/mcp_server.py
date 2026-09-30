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
    MODEL_TIER_MAPPING,
    MODEL_TIER_ORDER,
    is_tier_allowed,
    resolve_model_tier,
    get_model_for_tier,
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
            },
            {
                "name": "venice_create_batch_codes",
                "description": "Create a batch of cryptographically random access/pairing codes with a custom prefix for web dashboard pairing.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "prefix": {
                            "type": "string",
                            "default": "vkm_code_",
                            "description": "Prefix for generated codes (e.g. 'team-', 'vip-', 'agent-')"
                        },
                        "count": {
                            "type": "integer",
                            "default": 5,
                            "description": "Number of codes to generate (1 to 100)"
                        },
                        "ttl_hours": {
                            "type": "integer",
                            "default": 168,
                            "description": "Code validity in hours (default: 168, 7 days)"
                        },
                        "notes": {
                            "type": "string",
                            "description": "Optional notes or tag for this batch"
                        }
                    }
                }
            },
            {
                "name": "venice_create_batch_keys",
                "description": "Create a batch of Venice.ai API keys with common prefix in descriptions, budget limits, and category.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "prefix": {
                            "type": "string",
                            "description": "Prefix for key descriptions (e.g. 'worker-', 'agent-')"
                        },
                        "count": {
                            "type": "integer",
                            "default": 3,
                            "description": "Number of keys to mint (1 to 25)"
                        },
                        "daily_usd": {
                            "type": "number",
                            "default": 0.50,
                            "description": "Daily spend cap in USD for each key"
                        },
                        "category": {
                            "type": "string",
                            "default": "Default",
                            "description": "Category group"
                        },
                        "api_key_type": {
                            "type": "string",
                            "enum": ["INFERENCE", "ADMIN"],
                            "default": "INFERENCE"
                        },
                        "limit_period": {
                            "type": "string",
                            "enum": ["EPOCH", "MONTH", "LIFETIME"],
                            "default": "EPOCH"
                        }
                    },
                    "required": ["prefix"]
                }
            },
            {
                "name": "venice_list_auth_tokens",
                "description": "List active dashboard access/pairing codes with creation date, expiry, and prefix filter.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "prefix": {
                            "type": "string",
                            "description": "Optional prefix filter"
                        }
                    }
                }
            },
            {
                "name": "venice_list_projects",
                "description": "List all inference allocation projects with spending caps, live spend, connected external keys count, and max model tier.",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "venice_create_project",
                "description": "Create a new project allocation for external services or agents. Sets daily/weekly spend limits, maximum model tier (xs to xl), and default sub-key daily cap (default: $0.25).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Project or service group name"},
                        "description": {"type": "string", "description": "Description of project purpose or agent fleet"},
                        "daily_limit_usd": {"type": "number", "default": 1.00, "description": "Daily total spending ceiling for this project in USD"},
                        "weekly_limit_usd": {"type": "number", "description": "Optional weekly spending ceiling in USD"},
                        "default_sub_key_daily_usd": {"type": "number", "default": 0.25, "description": "Default daily spend cap for generated sub-keys (default: 0.25 USD)"},
                        "max_model_tier": {
                            "type": "string",
                            "enum": ["xs", "s", "m", "l", "xl"],
                            "default": "xl",
                            "description": "Maximum model size tier permitted (xs: 3B/flash, s: 70B-lite, m: 70B, l: R1, xl: 405B/E2EE)"
                        }
                    },
                    "required": ["name"]
                }
            },
            {
                "name": "venice_update_project",
                "description": "Modify an existing project allocation: change spend ceilings, max tier, or status (active/paused).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string", "description": "ID of the project to update"},
                        "name": {"type": "string", "description": "Updated project name"},
                        "daily_limit_usd": {"type": "number", "description": "New daily spend limit in USD"},
                        "weekly_limit_usd": {"type": "number", "description": "New weekly spend limit in USD"},
                        "default_sub_key_daily_usd": {"type": "number", "description": "New default sub-key cap"},
                        "max_model_tier": {"type": "string", "enum": ["xs", "s", "m", "l", "xl"], "description": "New max tier ceiling"},
                        "status": {"type": "string", "enum": ["active", "paused"], "description": "Project status"}
                    },
                    "required": ["project_id"]
                }
            },
            {
                "name": "venice_list_external_keys",
                "description": "List all external use keys and sub-keys with their spend limits, spent today, max tier, and parent project.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string", "description": "Optional project filter"}
                    }
                }
            },
            {
                "name": "venice_create_external_key",
                "description": "Generate an external use key from local dashboard for an agent or client service, tied to a project with model tier (xs to xl) and spending allocation.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string", "description": "Target project ID"},
                        "name": {"type": "string", "description": "Key label or agent name"},
                        "daily_limit_usd": {"type": "number", "description": "Daily spend limit in USD (defaults to project default_sub_key_daily_usd: 0.25)"},
                        "weekly_limit_usd": {"type": "number", "description": "Optional weekly spend limit in USD"},
                        "limit_period": {"type": "string", "enum": ["DAY", "WEEK"], "default": "DAY"},
                        "max_model_tier": {
                            "type": "string",
                            "enum": ["xs", "s", "m", "l", "xl"],
                            "default": "xl",
                            "description": "Max model tier (xs: low cost/fast, xl: 405B/E2EE)"
                        },
                        "prefix": {"type": "string", "default": "vkm_ext_", "description": "Key token prefix"},
                        "notes": {"type": "string", "description": "Optional notes or agent contact info"}
                    },
                    "required": ["project_id", "name"]
                }
            },
            {
                "name": "venice_create_sub_key",
                "description": "Create a sub-key with an allocated amount per project per day or week. By default unless created by admin is: 25 cents per project per day ($0.25 USD).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "parent_key_or_token": {"type": "string", "description": "Parent external key token, key ID, or valid pairing code"},
                        "name": {"type": "string", "description": "Name for the sub-key or agent"},
                        "amount_usd": {"type": "number", "default": 0.25, "description": "Allocated amount in USD (default: 0.25)"},
                        "period": {"type": "string", "enum": ["DAY", "WEEK"], "default": "DAY", "description": "Allocation period"},
                        "max_model_tier": {"type": "string", "enum": ["xs", "s", "m", "l", "xl"], "description": "Optional tier restriction"},
                        "notes": {"type": "string", "description": "Optional description"}
                    },
                    "required": ["parent_key_or_token", "name"]
                }
            },
            {
                "name": "venice_modify_external_key_allocation",
                "description": "Modify the inference allocation, model tier, or active status for a connected agent or external key.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "key_id": {"type": "string", "description": "The external key ID to modify"},
                        "name": {"type": "string", "description": "Updated name"},
                        "daily_limit_usd": {"type": "number", "description": "New daily spend limit in USD"},
                        "weekly_limit_usd": {"type": "number", "description": "New weekly spend limit in USD"},
                        "max_model_tier": {"type": "string", "enum": ["xs", "s", "m", "l", "xl"], "description": "New max tier"},
                        "status": {"type": "string", "enum": ["active", "paused", "revoked"], "description": "Status"}
                    },
                    "required": ["key_id"]
                }
            },
            {
                "name": "venice_revoke_external_key",
                "description": "Revoke an external key or sub-key, immediately cutting off gateway inference access.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "key_id_or_token": {"type": "string", "description": "The key ID or token string to revoke"}
                    },
                    "required": ["key_id_or_token"]
                }
            },
            {
                "name": "venice_get_gateway_info",
                "description": "Get Cloudflare DNS gateway endpoint URL, model tier details (xs to xl), and sample client integration code.",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "venice_set_cloudflare_gateway_url",
                "description": "Set or update the public Cloudflare DNS gateway URL for external clients.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Public gateway URL (e.g. https://venice-gateway.yourdomain.com)"}
                    },
                    "required": ["url"]
                }
            },
            {
                "name": "venice_gateway_chat_completion",
                "description": "Execute an inference request through the gateway using an external key or pairing code. Enforces tier limits (xs to xl) and meters budget.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "auth_token": {"type": "string", "description": "External key or pairing code token"},
                        "model": {
                            "type": "string",
                            "default": "xs",
                            "description": "Model tier (xs, s, m, l, xl) or concrete model name"
                        },
                        "prompt": {"type": "string", "description": "User prompt message"},
                        "system_prompt": {"type": "string", "description": "Optional system prompt instruction"},
                        "max_tokens": {"type": "integer", "default": 256, "description": "Max response tokens"}
                    },
                    "required": ["auth_token", "prompt"]
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
                desc = args.get("description")
                if not desc or not str(desc).strip():
                    return json.dumps({"error": "Key name/description is required and cannot be empty. Prompt user to provide a name."})
                req = KeyCreateRequest(
                    description=str(desc).strip(),
                    daily_usd=args.get("daily_usd"),
                    limitPeriod=args.get("limit_period", "EPOCH"),
                    apiKeyType=args.get("api_key_type", "INFERENCE"),
                    category=args.get("category", "Default"),
                    custom_threshold=args.get("custom_threshold")
                )
                res = await self.client.create_key(req)
                res_dict = res.model_dump()
                endpoints = state_store.get_network_endpoints()
                res_dict["endpoints"] = endpoints
                res_dict["endpoints_delivery"] = {
                    "tailscale_address": endpoints.get("tailscale_v1_url"),
                    "cloudflare_dns_url": endpoints.get("cloudflare_v1_url")
                }
                return json.dumps(res_dict, indent=2)

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

            elif name == "venice_create_batch_codes":
                prefix = args.get("prefix", "vkm_code_")
                count = int(args.get("count", 5))
                ttl = int(args.get("ttl_hours", 168))
                notes = args.get("notes")
                tokens = state_store.create_batch_auth_tokens(
                    prefix=prefix,
                    count=count,
                    created_by="mcp_server",
                    ttl_hours=ttl,
                    notes=notes,
                )
                base_url = "http://localhost:8660"
                for t in tokens:
                    t["magic_url"] = f"{base_url}/?token={t['token']}"
                return json.dumps({
                    "status": "success",
                    "count": len(tokens),
                    "prefix": prefix,
                    "tokens": tokens
                }, indent=2)

            elif name == "venice_create_batch_keys":
                prefix = args.get("prefix")
                if not prefix or not str(prefix).strip():
                    return json.dumps({"error": "Key prefix/name is required and cannot be empty."})
                count = int(args.get("count", 3))
                daily_usd = args.get("daily_usd", 0.50)
                category = args.get("category", "Default")
                key_type = args.get("api_key_type", "INFERENCE")
                limit_period = args.get("limit_period", "EPOCH")
                keys = await self.client.create_batch_keys(
                    prefix=str(prefix).strip(),
                    count=count,
                    daily_usd=daily_usd,
                    category=category,
                    api_key_type=key_type,
                    limit_period=limit_period,
                )
                endpoints = state_store.get_network_endpoints()
                return json.dumps({
                    "status": "success",
                    "count": len(keys),
                    "prefix": prefix,
                    "keys": [k.model_dump() for k in keys],
                    "endpoints": endpoints,
                    "endpoints_delivery": {
                        "tailscale_address": endpoints.get("tailscale_v1_url"),
                        "cloudflare_dns_url": endpoints.get("cloudflare_v1_url")
                    }
                }, indent=2)

            elif name == "venice_list_auth_tokens":
                prefix = args.get("prefix")
                tokens = state_store.list_active_tokens(prefix=prefix)
                base_url = "http://localhost:8660"
                for t in tokens:
                    t["magic_url"] = f"{base_url}/?token={t['token']}"
                return json.dumps({"total": len(tokens), "tokens": tokens}, indent=2)

            elif name == "venice_list_projects":
                projects = state_store.list_projects()
                return json.dumps({"total": len(projects), "projects": projects}, indent=2)

            elif name == "venice_create_project":
                name_val = args.get("name")
                if not name_val or not str(name_val).strip():
                    return json.dumps({"error": "Project name is required and cannot be empty. Prompt user to provide a name."})
                desc = args.get("description", "")
                daily = float(args.get("daily_limit_usd", 1.00))
                weekly = float(args["weekly_limit_usd"]) if args.get("weekly_limit_usd") is not None else None
                default_sub = float(args.get("default_sub_key_daily_usd", 0.25))
                tier = args.get("max_model_tier", "xl")
                proj = state_store.create_project(
                    name=str(name_val).strip(),
                    description=desc,
                    daily_limit_usd=daily,
                    weekly_limit_usd=weekly,
                    default_sub_key_daily_usd=default_sub,
                    max_model_tier=tier,
                )
                return json.dumps({"status": "success", "project": proj}, indent=2)

            elif name == "venice_update_project":
                pid = args["project_id"]
                updates = {k: v for k, v in args.items() if k != "project_id" and v is not None}
                res = state_store.update_project(pid, **updates)
                if not res:
                    return json.dumps({"error": f"Project '{pid}' not found."})
                return json.dumps({"status": "success", "project": res}, indent=2)

            elif name == "venice_list_external_keys":
                pid = args.get("project_id")
                ext_keys = state_store.list_external_keys(project_id=pid)
                return json.dumps({"total": len(ext_keys), "keys": ext_keys}, indent=2)

            elif name == "venice_create_external_key":
                name_val = args.get("name")
                if not name_val or not str(name_val).strip():
                    return json.dumps({"error": "External key name is required and cannot be empty. Prompt user to provide a name."})
                key_record = state_store.create_external_key(
                    project_id=args["project_id"],
                    name=str(name_val).strip(),
                    daily_limit_usd=args.get("daily_limit_usd"),
                    weekly_limit_usd=args.get("weekly_limit_usd"),
                    limit_period=args.get("limit_period", "DAY"),
                    max_model_tier=args.get("max_model_tier", "xl"),
                    prefix=args.get("prefix", "vkm_ext_"),
                    notes=args.get("notes", ""),
                    created_by="mcp_admin",
                    key_type="ADMIN_EXTERNAL",
                )
                endpoints = state_store.get_network_endpoints()
                key_record["endpoints"] = endpoints
                key_record["tailscale_address"] = endpoints.get("tailscale_v1_url")
                key_record["cloudflare_dns_url"] = endpoints.get("cloudflare_v1_url")
                key_record["tailscale_curl_example"] = (
                    f"curl -X POST {endpoints.get('tailscale_v1_url')}/chat/completions \\\n"
                    f"  -H 'Authorization: Bearer {key_record['token']}' \\\n"
                    f"  -H 'Content-Type: application/json' \\\n"
                    f"  -d '{{\"model\": \"{key_record['max_model_tier']}\", \"messages\": [{{\"role\": \"user\", \"content\": \"Hello!\"}}]}}'"
                )
                key_record["cloudflare_curl_example"] = (
                    f"curl -X POST {endpoints.get('cloudflare_v1_url')}/chat/completions \\\n"
                    f"  -H 'Authorization: Bearer {key_record['token']}' \\\n"
                    f"  -H 'Content-Type: application/json' \\\n"
                    f"  -d '{{\"model\": \"{key_record['max_model_tier']}\", \"messages\": [{{\"role\": \"user\", \"content\": \"Hello!\"}}]}}'"
                )
                key_record["curl_example"] = key_record["cloudflare_curl_example"]
                return json.dumps({"status": "success", "key": key_record, "endpoints": endpoints}, indent=2)

            elif name == "venice_create_sub_key":
                name_val = args.get("name")
                if not name_val or not str(name_val).strip():
                    return json.dumps({"error": "Sub-key name is required and cannot be empty. Prompt user to provide a name."})
                sub_key = state_store.create_sub_key(
                    parent_key_or_token=args["parent_key_or_token"],
                    name=str(name_val).strip(),
                    amount_usd=args.get("amount_usd", 0.25),
                    period=args.get("period", "DAY"),
                    max_model_tier=args.get("max_model_tier"),
                    notes=args.get("notes"),
                )
                endpoints = state_store.get_network_endpoints()
                sub_key["endpoints"] = endpoints
                sub_key["tailscale_address"] = endpoints.get("tailscale_v1_url")
                sub_key["cloudflare_dns_url"] = endpoints.get("cloudflare_v1_url")
                sub_key["tailscale_curl_example"] = (
                    f"curl -X POST {endpoints.get('tailscale_v1_url')}/chat/completions \\\n"
                    f"  -H 'Authorization: Bearer {sub_key['token']}' \\\n"
                    f"  -H 'Content-Type: application/json' \\\n"
                    f"  -d '{{\"model\": \"{sub_key['max_model_tier']}\", \"messages\": [{{\"role\": \"user\", \"content\": \"Hello!\"}}]}}'"
                )
                sub_key["cloudflare_curl_example"] = (
                    f"curl -X POST {endpoints.get('cloudflare_v1_url')}/chat/completions \\\n"
                    f"  -H 'Authorization: Bearer {sub_key['token']}' \\\n"
                    f"  -H 'Content-Type: application/json' \\\n"
                    f"  -d '{{\"model\": \"{sub_key['max_model_tier']}\", \"messages\": [{{\"role\": \"user\", \"content\": \"Hello!\"}}]}}'"
                )
                sub_key["curl_example"] = sub_key["cloudflare_curl_example"]
                return json.dumps({"status": "success", "sub_key": sub_key, "endpoints": endpoints}, indent=2)

            elif name == "venice_modify_external_key_allocation":
                kid = args["key_id"]
                updates = {k: v for k, v in args.items() if k != "key_id" and v is not None}
                res = state_store.update_external_key(kid, **updates)
                if not res:
                    return json.dumps({"error": f"External key '{kid}' not found."})
                return json.dumps({"status": "success", "key": res}, indent=2)

            elif name == "venice_revoke_external_key":
                target = args["key_id_or_token"]
                ok = state_store.revoke_external_key(target)
                if not ok:
                    return json.dumps({"error": f"Key '{target}' not found or could not be revoked."})
                return json.dumps({"status": "success", "message": f"External key '{target}' revoked."}, indent=2)

            elif name == "venice_get_gateway_info":
                gw_url = state_store.get_cloudflare_gateway_url() or "http://localhost:8660"
                endpoints = state_store.get_network_endpoints()
                return json.dumps({
                    "cloudflare_gateway_url": state_store.get_cloudflare_gateway_url(),
                    "effective_gateway_url": gw_url,
                    "endpoints": endpoints,
                    "tiers": MODEL_TIER_MAPPING,
                    "tier_order": MODEL_TIER_ORDER,
                    "total_projects": len(state_store.list_projects()),
                    "total_external_keys": len(state_store.list_external_keys()),
                }, indent=2)

            elif name == "venice_set_cloudflare_gateway_url":
                url_val = args["url"]
                state_store.set_cloudflare_gateway_url(url_val)
                return json.dumps({
                    "status": "success",
                    "cloudflare_gateway_url": state_store.get_cloudflare_gateway_url(),
                }, indent=2)

            elif name == "venice_gateway_chat_completion":
                token = args["auth_token"]
                model_arg = args.get("model", "xs")
                prompt_arg = args["prompt"]
                sys_prompt = args.get("system_prompt")
                max_tokens = int(args.get("max_tokens", 256))

                # Validate token & spending
                is_valid, key_meta, project, err = state_store.validate_external_key(token)
                if not is_valid:
                    return json.dumps({"error": f"Gateway access denied: {err}"})

                req_tier = resolve_model_tier(model_arg)
                max_tier = key_meta.get("max_model_tier", "xl")
                if not is_tier_allowed(req_tier, max_tier):
                    return json.dumps({
                        "error": f"Requested tier '{req_tier.upper()}' exceeds key maximum permitted tier '{max_tier.upper()}'."
                    })

                venice_model = get_model_for_tier(req_tier) if model_arg in MODEL_TIER_ORDER else model_arg
                messages = []
                if sys_prompt:
                    messages.append({"role": "system", "content": sys_prompt})
                messages.append({"role": "user", "content": prompt_arg})

                payload = {
                    "model": venice_model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                }
                resp = await self.client.chat_completion(payload)
                if "error" in resp:
                    return json.dumps(resp, indent=2)

                usage = resp.get("usage", {})
                p_tok = int(usage.get("prompt_tokens", 0))
                c_tok = int(usage.get("completion_tokens", 0))
                tot_tok = int(usage.get("total_tokens", p_tok + c_tok))

                tier_info = MODEL_TIER_MAPPING.get(req_tier, MODEL_TIER_MAPPING["m"])
                cost = max(0.0001, round(
                    ((p_tok * tier_info["cost_per_m_in"]) + (c_tok * tier_info["cost_per_m_out"])) / 1_000_000.0,
                    6
                ))
                state_store.record_external_usage(key_meta["id"], project["id"], cost, tot_tok)

                return json.dumps({
                    "status": "success",
                    "tier_used": req_tier,
                    "model": venice_model,
                    "cost_usd": cost,
                    "usage": usage,
                    "choices": resp.get("choices", []),
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

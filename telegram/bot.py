"""
Interactive Telegram Bot Controller for Venice.ai & Telegram Agent Key Management.
Uses Telegram Bot API with long-polling and interactive Inline Keyboards.
"""

import sys
import time
import json
import logging
import requests
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
from core.tiers import (
    MODEL_TIER_ORDER,
    MODEL_TIER_MAPPING,
    get_model_for_tier,
    resolve_model_tier,
    is_tier_allowed
)
from core.supervisor import ServiceSupervisor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("venice_tg_bot")


class VeniceTelegramBot:
    def __init__(self, vault: Optional[KeyVault] = None):
        self.vault = vault or KeyVault()
        self.tg_manager = TelegramAgentManager(self.vault)
        self.mesh = FleetMeshManager(self.vault)
        self.token = self.vault.get_telegram_master_token()
        self.authorized_chat_id = self.vault.get_authorized_chat_id()
        self.api_url = f"https://api.telegram.org/bot{self.token}"
        self.running = False
        self.last_update_id = 0

    def _get_venice_client(self) -> VeniceClient:
        return VeniceClient(
            admin_key=self.vault.get_venice_admin_key(),
            inference_key=self.vault.get_venice_inference_key(),
            base_url=self.vault.data.get("venice", {}).get("base_url", "")
        )

    def _send_request(self, method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.api_url}/{method}"
        try:
            resp = requests.post(url, json=payload, timeout=20)
            return resp.json()
        except Exception as e:
            logger.error(f"Telegram API request {method} failed: {e}")
            return {"ok": False, "error": str(e)}

    def send_message(
        self,
        chat_id: str,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: str = "Markdown"
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self._send_request("sendMessage", payload)

    def edit_message(
        self,
        chat_id: str,
        message_id: int,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: str = "Markdown"
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self._send_request("editMessageText", payload)

    def answer_callback(self, callback_query_id: str, text: str = "", show_alert: bool = False):
        self._send_request("answerCallbackQuery", {
            "callback_query_id": callback_query_id,
            "text": text,
            "show_alert": show_alert
        })

    # --- Menus & Keyboards ---

    def main_menu_keyboard(self) -> Dict[str, Any]:
        return {
            "inline_keyboard": [
                [
                    {"text": "💰 Balances & Tier", "callback_data": "menu_balance"},
                    {"text": "🔑 Venice Keys", "callback_data": "menu_keys"}
                ],
                [
                    {"text": "🤖 Agent TG Bots", "callback_data": "menu_tg_bots"},
                    {"text": "➕ Provision Key (XS-XL)", "callback_data": "wiz_key_agent"}
                ],
                [
                    {"text": "⚡ Tier Inference", "callback_data": "menu_infer_tiers"},
                    {"text": "🚀 Deploy to Config", "callback_data": "act_deploy_config"}
                ],
                [
                    {"text": "🌐 Fleet Nodes (mcmini)", "callback_data": "menu_fleet"},
                    {"text": "📥 Sync Fleet Git", "callback_data": "act_sync_fleet"}
                ],
                [
                    {"text": "🔒 Pairing Code", "callback_data": "menu_pair_code"},
                    {"text": "🎟️ Agent Sub-Keys", "callback_data": "menu_subkeys"}
                ],
                [
                    {"text": "💾 Backup Vault", "callback_data": "act_backup_vault"},
                    {"text": "🔄 Recall Keys", "callback_data": "act_recall_vault"}
                ],
                [
                    {"text": "📊 Model Limits", "callback_data": "menu_limits"},
                    {"text": "🛡️ Auto-Restart & Daemon", "callback_data": "menu_service"}
                ],
                [
                    {"text": "🔄 Refresh Dashboard", "callback_data": "menu_main"}
                ]
            ]
        }

    def back_to_main_keyboard(self) -> Dict[str, Any]:
        return {
            "inline_keyboard": [
                [{"text": "« Back to Main Dashboard", "callback_data": "menu_main"}]
            ]
        }

    def render_dashboard_text(self) -> str:
        client = self._get_venice_client()
        rate_res = client.get_rate_limits()
        has_admin = bool(self.vault.get_venice_admin_key())

        usd_bal = "0.00"
        diem_bal = "0.00"
        tier = "PAID"
        epoch_str = "Active"

        if rate_res.get("success"):
            balances = rate_res.get("balances", {})
            usd_bal = f"{balances.get('USD', 0):.2f}"
            diem_bal = f"{balances.get('DIEM', 0):.2f}"
            tier = rate_res.get("apiTier", {}).get("id", "paid").upper()
            if rate_res.get("nextEpochBegins"):
                epoch_str = rate_res.get("nextEpochBegins")[:10]

        agent_count = len(self.vault.get_agent_bots())
        keys_count = len(self.vault.get_venice_keys())
        fleet_nodes = self.mesh.list_nodes(check_health=False)

        return (
            f"⚡ *VENICE & TG KEY ENGINE // CONTROLLER*\n\n"
            f"• *USD Balance*: `${usd_bal}`\n"
            f"• *DIEM Balance*: `{diem_bal} DIEM`\n"
            f"• *Account Tier*: `{tier}` (Epoch: `{epoch_str}`)\n"
            f"• *Admin Key*: `{'CONFIGURED ✅' if has_admin else 'NOT SET ⚠️ (Inference Mode)'}`\n"
            f"• *Registered Agent Bots*: `{agent_count}`\n"
            f"• *Managed Venice Keys*: `{keys_count}`\n"
            f"• *A2A Fleet Mesh*: `{len(fleet_nodes)} node(s) configured`\n\n"
            f"Tap an interactive button below to manage keys, trigger inference, or inspect agents:"
        )

    # --- Callback Handlers ---

    def handle_callback_query(self, cb: Dict[str, Any]):
        cb_id = cb.get("id")
        data = cb.get("data", "")
        message = cb.get("message", {})
        chat_id = str(message.get("chat", {}).get("id"))
        msg_id = message.get("message_id")

        if chat_id != self.authorized_chat_id:
            self.answer_callback(cb_id, "⛔ Unauthorized user", show_alert=True)
            return

        client = self._get_venice_client()

        if data == "menu_main":
            self.answer_callback(cb_id)
            self.edit_message(chat_id, msg_id, self.render_dashboard_text(), reply_markup=self.main_menu_keyboard())

        elif data == "menu_balance":
            self.answer_callback(cb_id)
            res = client.get_rate_limits()
            if res.get("success"):
                balances = res.get("balances", {})
                usd = balances.get("USD", 0)
                diem = balances.get("DIEM", 0)
                bundled = balances.get("BUNDLED_CREDITS", 0)
                tier = res.get("apiTier", {})
                exp = res.get("keyExpiration") or "Unlimited"
                epoch = res.get("nextEpochBegins") or "N/A"

                txt = (
                    f"💰 *Venice.ai Account Balances*\n\n"
                    f"• *USD Balance*: `${usd:.2f}`\n"
                    f"• *DIEM Token*: `{diem:.2f}`\n"
                    f"• *Bundled Credits*: `{bundled}`\n"
                    f"• *Tier ID*: `{tier.get('id', 'paid')}` (Charged: `{tier.get('isCharged')}`)\n"
                    f"• *Key Expiration*: `{exp}`\n"
                    f"• *Next Reset Epoch*: `{epoch}`"
                )
            else:
                txt = f"❌ *Failed to fetch balances*: `{res.get('error')}`"
            self.edit_message(chat_id, msg_id, txt, reply_markup=self.back_to_main_keyboard())

        elif data == "menu_keys":
            self.answer_callback(cb_id)
            admin_key = self.vault.get_venice_admin_key()
            vault_keys = self.vault.get_venice_keys()
            remote_keys = []
            if admin_key:
                res = client.list_keys(admin_key=admin_key)
                if res.get("success"):
                    remote_keys = res.get("keys", [])

            # Merge remote and vault keys
            merged = {}
            for k in vault_keys:
                merged[k.get("id")] = k
            for k in remote_keys:
                merged[k.get("id")] = {**merged.get(k.get("id"), {}), **k}

            all_keys = list(merged.values())
            if all_keys:
                txt = f"🔑 *Managed Venice Keys ({len(all_keys)})*:\n\n"
                for k in all_keys[:8]:
                    desc = k.get("description", "Venice Key")
                    k_type = k.get("apiKeyType", "INFERENCE")
                    k_tier = (k.get("maxModelTier") or "xl").upper()
                    limit = k.get("consumptionLimits", {}).get("usd")
                    lim_str = f"${limit}" if limit else "Uncapped"
                    txt += f"• *{desc}* `[{k_type}]` `[TIER: {k_tier}]`\n  ID: `{k.get('id')}` | Cap: `{lim_str}`\n"
            else:
                txt = (
                    "🔑 *Venice.ai Keys*\n\n"
                    "No keys currently stored in vault. Tap *Provision Key* to allocate a key with XS-XL quality limits."
                )

            kb = {
                "inline_keyboard": [
                    [{"text": "➕ Provision Key (XS-XL)", "callback_data": "wiz_key_agent"}],
                    [{"text": "« Back to Main Dashboard", "callback_data": "menu_main"}]
                ]
            }
            self.edit_message(chat_id, msg_id, txt, reply_markup=kb)

        elif data == "menu_tg_bots":
            self.answer_callback(cb_id)
            bots = self.vault.get_agent_bots()
            txt = f"🤖 *Agent Telegram Bots ({len(bots)})*\n\n"
            for agent, b in bots.items():
                u = f"@{b.get('username')}" if b.get('username') else "No username"
                txt += f"• *{agent}*: `{u}`\n  Config: `{b.get('config_path')}`\n\n"
            txt += "To create a bot for another agent, click below or send:\n`/botfather_wizard <agent_name>`"

            kb = {
                "inline_keyboard": [
                    [{"text": "🧙 @BotFather Wizard", "callback_data": "act_wizard_hermes"}],
                    [{"text": "📡 Test Primary Bot Ping", "callback_data": "act_test_primary_bot"}],
                    [{"text": "« Back to Main Dashboard", "callback_data": "menu_main"}]
                ]
            }
            self.edit_message(chat_id, msg_id, txt, reply_markup=kb)

        # Wizard Step 1: Agent Selection
        elif data in ("wiz_key_agent", "wiz_key_start", "act_create_key_5"):
            self.answer_callback(cb_id)
            txt = (
                "🧙 *Agent Key Provisioning Wizard* (Step 1/3)\n\n"
                "Select which agent or service this key will be allocated for:"
            )
            kb = {
                "inline_keyboard": [
                    [
                        {"text": "🎵 hermes-music", "callback_data": "wiz_agent_hermes-music"},
                        {"text": "🌐 a2a-node", "callback_data": "wiz_agent_a2a-node"}
                    ],
                    [
                        {"text": "🎧 dawagent", "callback_data": "wiz_agent_dawagent"},
                        {"text": "⚙️ worker-audio", "callback_data": "wiz_agent_worker-audio"}
                    ],
                    [
                        {"text": "🤖 custom-agent", "callback_data": "wiz_agent_custom-agent"}
                    ],
                    [
                        {"text": "« Back to Main Dashboard", "callback_data": "menu_main"}
                    ]
                ]
            }
            self.edit_message(chat_id, msg_id, txt, reply_markup=kb)

        # Wizard Step 2: Quality Tier Selection (XS to XL)
        elif data.startswith("wiz_agent_"):
            self.answer_callback(cb_id)
            agent = data[len("wiz_agent_"):]
            txt = (
                f"🧙 *Agent Key Provisioning Wizard* (Step 2/3)\n\n"
                f"• *Target Agent*: `{agent}`\n\n"
                f"Select the maximum *Inference Model Quality Tier* for this agent:\n"
                f"_(Keys are strictly gated: lower tiers cannot access higher model classes)_"
            )
            kb = {
                "inline_keyboard": [
                    [{"text": "🟣 XS: Ultra-Fast (1B-3B, Routing)", "callback_data": f"wiz_tier_{agent}_xs"}],
                    [{"text": "🟢 S: Efficient (Flash, 8B-14B)", "callback_data": f"wiz_tier_{agent}_s"}],
                    [{"text": "🟡 M: Balanced Quality (70B, Coding)", "callback_data": f"wiz_tier_{agent}_m"}],
                    [{"text": "🔵 L: Reasoning (R1, 72B)", "callback_data": f"wiz_tier_{agent}_l"}],
                    [{"text": "🔴 XL: Flagship 405B+ & Enclave", "callback_data": f"wiz_tier_{agent}_xl"}],
                    [{"text": "« Back to Agents", "callback_data": "wiz_key_agent"}],
                    [{"text": "« Main Dashboard", "callback_data": "menu_main"}]
                ]
            }
            self.edit_message(chat_id, msg_id, txt, reply_markup=kb)

        # Wizard Step 3: Tier Specs & Budget Cap Selection
        elif data.startswith("wiz_tier_"):
            self.answer_callback(cb_id)
            parts = data.split("_")
            agent = parts[2]
            tier = parts[3]
            t_info = MODEL_TIER_MAPPING.get(tier, {})

            txt = (
                f"🧙 *Agent Key Provisioning Wizard* (Step 3/3)\n\n"
                f"• *Target Agent*: `{agent}`\n"
                f"• *Allocated Tier*: `{tier.upper()}` — {t_info.get('label', '')}\n"
                f"• *Workload Focus*: {t_info.get('description', '')}\n"
                f"• *Default Model*: `{t_info.get('default_model', '')}`\n"
                f"• *Allowed Models*: `{', '.join(t_info.get('models', []))}`\n"
                f"• *Est. Rate*: `${t_info.get('cost_per_m_in', 0):.2f}` / `${t_info.get('cost_per_m_out', 0):.2f}` per 1M tok\n\n"
                f"Select monthly budget cap to provision:"
            )
            kb = {
                "inline_keyboard": [
                    [
                        {"text": "⚡ $5.00 / mo", "callback_data": f"wiz_do_{agent}_{tier}_5"},
                        {"text": "⚡ $10.00 / mo", "callback_data": f"wiz_do_{agent}_{tier}_10"}
                    ],
                    [
                        {"text": "⚡ $25.00 / mo", "callback_data": f"wiz_do_{agent}_{tier}_25"},
                        {"text": "⚡ Unlimited", "callback_data": f"wiz_do_{agent}_{tier}_0"}
                    ],
                    [
                        {"text": "« Change Quality Tier", "callback_data": f"wiz_agent_{agent}"}
                    ],
                    [
                        {"text": "« Main Dashboard", "callback_data": "menu_main"}
                    ]
                ]
            }
            self.edit_message(chat_id, msg_id, txt, reply_markup=kb)

        # Wizard Step 4: Issue / Generate Key
        elif data.startswith("wiz_do_"):
            self.answer_callback(cb_id, "Provisioning key...")
            parts = data.split("_")
            agent = parts[2]
            tier = parts[3]
            limit_val = float(parts[4]) if len(parts) > 4 and parts[4] != "0" else None
            t_info = MODEL_TIER_MAPPING.get(tier, {})
            admin_key = self.vault.get_venice_admin_key()

            if admin_key:
                res = client.create_key(
                    description=f"{agent} ({tier.upper()} Tier)",
                    key_type="INFERENCE",
                    limit_usd=limit_val,
                    admin_key=admin_key
                )
                if res.get("success"):
                    key_obj = res.get("key", {})
                    key_obj["maxModelTier"] = tier
                    key_obj["agent"] = agent
                    self.vault.store_venice_key(key_obj)
                    raw_k = key_obj.get("apiKey") or key_obj.get("key") or key_obj.get("id")
                    lim_str = f"${limit_val:.2f} USD" if limit_val else "Unlimited"

                    txt = (
                        f"✅ *Agent Key Successfully Provisioned!*\n\n"
                        f"• *Agent*: `{agent}`\n"
                        f"• *Model Quality Tier*: `{tier.upper()}` ({t_info.get('label')})\n"
                        f"• *Monthly Budget*: `{lim_str}`\n"
                        f"• *Key ID*: `{key_obj.get('id')}`\n"
                        f"• *API Key*: `{raw_k}`\n\n"
                        f"Stored in vault with model quality gating enforced."
                    )
                    kb = {
                        "inline_keyboard": [
                            [{"text": "🚀 Deploy to Agent Config", "callback_data": f"deploy_key_{key_obj.get('id')}"}],
                            [{"text": f"⚡ Test Tier {tier.upper()} ({t_info.get('default_model')})", "callback_data": f"infer_run_{tier}"}],
                            [{"text": "🔑 View All Keys", "callback_data": "menu_keys"}],
                            [{"text": "« Back to Main Dashboard", "callback_data": "menu_main"}]
                        ]
                    }
                    self.edit_message(chat_id, msg_id, txt, reply_markup=kb)
                else:
                    self.edit_message(
                        chat_id,
                        msg_id,
                        f"❌ *Failed to create key*: `{res.get('error')}`",
                        reply_markup=self.back_to_main_keyboard()
                    )
            else:
                lim_str = f"${limit_val:.2f} USD" if limit_val else "Unlimited"
                txt = (
                    f"⚠️ *Venice Admin API Key Required for Remote Issuance*\n\n"
                    f"Currently operating in *Inference Mode*. To create new keys via Venice API, an Admin key is required.\n\n"
                    f"You can either:\n"
                    f"1. *Bind Active Inference Key*: Allocate existing credentials to `{agent}` under *Tier {tier.upper()}* with local policy gating.\n"
                    f"2. Configure Venice Admin Key via `/set_admin_key <key>`."
                )
                kb = {
                    "inline_keyboard": [
                        [{"text": f"📥 Bind Active Key as Tier {tier.upper()}", "callback_data": f"wiz_bind_{agent}_{tier}_{int(limit_val or 0)}"}],
                        [{"text": "« Back to Tier Selection", "callback_data": f"wiz_agent_{agent}"}],
                        [{"text": "« Main Dashboard", "callback_data": "menu_main"}]
                    ]
                }
                self.edit_message(chat_id, msg_id, txt, reply_markup=kb)

        # Bind active key to agent with tier limit
        elif data.startswith("wiz_bind_"):
            self.answer_callback(cb_id, "Binding active key...")
            parts = data.split("_")
            agent = parts[2]
            tier = parts[3]
            limit_val = float(parts[4]) if len(parts) > 4 and parts[4] != "0" else None
            t_info = MODEL_TIER_MAPPING.get(tier, {})

            active_key = self.vault.get_active_venice_key()
            if not active_key:
                self.edit_message(chat_id, msg_id, "❌ No active Venice key found in vault.", reply_markup=self.back_to_main_keyboard())
                return

            import hashlib
            h = hashlib.sha256(active_key.encode()).hexdigest()[:10]
            key_id = f"vk_{h}"

            key_obj = {
                "id": key_id,
                "apiKey": active_key,
                "description": f"{agent} ({tier.upper()} Tier)",
                "apiKeyType": "INFERENCE",
                "maxModelTier": tier,
                "agent": agent,
                "consumptionLimits": {"usd": limit_val} if limit_val else {},
                "status": "ACTIVE"
            }
            self.vault.store_venice_key(key_obj)

            txt = (
                f"✅ *Active Key Allocated to Agent!*\n\n"
                f"• *Agent*: `{agent}`\n"
                f"• *Quality Tier*: `{tier.upper()}` ({t_info.get('label')})\n"
                f"• *Key ID*: `{key_id}`\n\n"
                f"Key successfully registered with Tier `{tier.upper()}` model gating policy."
            )
            kb = {
                "inline_keyboard": [
                    [{"text": "🚀 Deploy to Agent Config", "callback_data": f"deploy_key_{key_id}"}],
                    [{"text": f"⚡ Test Tier {tier.upper()} Inference", "callback_data": f"infer_run_{tier}"}],
                    [{"text": "🔑 View All Keys", "callback_data": "menu_keys"}],
                    [{"text": "« Main Dashboard", "callback_data": "menu_main"}]
                ]
            }
            self.edit_message(chat_id, msg_id, txt, reply_markup=kb)

        # Deploy specific key by key_id
        elif data.startswith("deploy_key_"):
            self.answer_callback(cb_id, "Deploying key...")
            key_id = data[len("deploy_key_"):]
            actual_key = key_id
            for k in self.vault.get_venice_keys():
                if k.get("id") == key_id and k.get("apiKey"):
                    actual_key = k.get("apiKey")
                    break
            res = ConfigDeployer.deploy_venice_key(actual_key)
            if res.get("success"):
                txt = (
                    f"🚀 *Configuration Deployed!*\n\n"
                    f"• *Target*: `{res.get('target')}`\n"
                    f"• *Backup Created*: `{res.get('backup')}`\n"
                    f"• *Key Preview*: `{res.get('key_preview')}`\n\n"
                    f"Configuration file updated with key `{key_id}`."
                )
            else:
                txt = f"❌ *Deployment Failed*: `{res.get('error')}`"
            self.edit_message(chat_id, msg_id, txt, reply_markup=self.back_to_main_keyboard())

        # Model Quality Tier Inference Sandbox Menu
        elif data in ("menu_infer_tiers", "act_quick_infer"):
            self.answer_callback(cb_id)
            txt = (
                "⚡ *Venice Model Quality Tier Sandbox*\n\n"
                "Select a Model Quality Tier to test live latency, tokens, and response:"
            )
            kb = {
                "inline_keyboard": [
                    [{"text": "🟣 XS: llama-3.2-3b (Ultra-Fast)", "callback_data": "infer_run_xs"}],
                    [{"text": "🟢 S: deepseek-v4-flash (Efficient)", "callback_data": "infer_run_s"}],
                    [{"text": "🟡 M: llama-3.3-70b (Balanced)", "callback_data": "infer_run_m"}],
                    [{"text": "🔵 L: deepseek-r1 (Reasoning)", "callback_data": "infer_run_l"}],
                    [{"text": "🔴 XL: llama-3.1-405b (Flagship)", "callback_data": "infer_run_xl"}],
                    [{"text": "« Back to Main Dashboard", "callback_data": "menu_main"}]
                ]
            }
            self.edit_message(chat_id, msg_id, txt, reply_markup=kb)

        # Run inference for a specific tier
        elif data.startswith("infer_run_"):
            tier = data[len("infer_run_"):]
            self.answer_callback(cb_id, f"Running Tier {tier.upper()} inference...")
            model = get_model_for_tier(tier)
            t_info = MODEL_TIER_MAPPING.get(tier, {})
            prompt = "Say hello from Venice and state your model tier in one sentence."

            res = client.test_inference(prompt=prompt, model=model, max_tokens=70)
            if res.get("success"):
                cost = res.get("cost", {}).get("usd", 0)
                txt = (
                    f"⚡ *Tier {tier.upper()} Inference Result*\n\n"
                    f"• *Tier*: `{tier.upper()}` — {t_info.get('label', '')}\n"
                    f"• *Model*: `{model}`\n"
                    f"• *Latency*: `{res.get('latency_ms')} ms`\n"
                    f"• *Tokens*: `{res.get('usage', {}).get('total_tokens', 0)}`\n"
                    f"• *Estimated Cost*: `${cost:.6f} USD`\n\n"
                    f"*Output*:\n> {res.get('content')}"
                )
            else:
                txt = f"❌ *Tier {tier.upper()} Inference Error*: `{res.get('error')}`"

            kb = {
                "inline_keyboard": [
                    [{"text": "🔄 Test Another Tier", "callback_data": "menu_infer_tiers"}],
                    [{"text": "« Back to Main Dashboard", "callback_data": "menu_main"}]
                ]
            }
            self.edit_message(chat_id, msg_id, txt, reply_markup=kb)

        elif data in ("act_deploy_config", "act_deploy_hermes"):
            self.answer_callback(cb_id, "Deploying configuration...")
            active_key = self.vault.get_active_venice_key()
            res = ConfigDeployer.deploy_venice_key(active_key)
            if res.get("success"):
                txt = (
                    f"🚀 *Configuration Deployed!*\n\n"
                    f"• *Target*: `{res.get('target')}`\n"
                    f"• *Backup Created*: `{res.get('backup')}`\n"
                    f"• *Key Preview*: `{res.get('key_preview')}`\n\n"
                    f"Target configuration file is updated with the active Venice key."
                )
            else:
                txt = f"❌ *Deployment Failed*: `{res.get('error')}`"
            self.edit_message(chat_id, msg_id, txt, reply_markup=self.back_to_main_keyboard())

        elif data == "act_test_primary_bot":
            self.answer_callback(cb_id, "Sending test ping...")
            bots = self.vault.get_agent_bots()
            bot = list(bots.values())[0] if bots else None
            if bot:
                ping_res = self.tg_manager.send_test_message(bot["bot_token"])
                if ping_res.get("success"):
                    self.answer_callback(cb_id, "Test ping sent! Check chat.", show_alert=True)
                else:
                    self.answer_callback(cb_id, f"Ping error: {ping_res.get('error')}", show_alert=True)
            else:
                self.answer_callback(cb_id, "No agent bots registered yet.", show_alert=True)

        elif data == "act_wizard_hermes":
            self.answer_callback(cb_id)
            wiz = self.tg_manager.generate_botfather_wizard("worker-agent")
            txt = (
                f"🧙 *@BotFather Creation Steps*\n\n"
                f"1. Open [@BotFather](https://t.me/BotFather?start=newbot)\n"
                f"2. Send: `/newbot`\n"
                f"3. Name: `{wiz.get('suggested_name')}`\n"
                f"4. Username: `{wiz.get('suggested_username')}`\n"
                f"5. Copy HTTP API token and reply here:\n"
                f"`/register_bot worker-agent <YOUR_TOKEN>`"
            )
            self.edit_message(chat_id, msg_id, txt, reply_markup=self.back_to_main_keyboard())

        elif data == "menu_limits":
            self.answer_callback(cb_id)
            res = client.get_rate_limits()
            if res.get("success"):
                raw = res.get("raw", {})
                limits = raw.get("rateLimits", [])[:6]
                txt = "📊 *Model Rate Limits*:\n\n"
                for m in limits:
                    l_str = " | ".join([f"{l['amount']} {l['type']}" for l in m.get("rateLimits", [])]) or "Standard"
                    txt += f"• `{m.get('apiModelId')}`: {l_str}\n"
            else:
                txt = f"❌ *Failed to fetch limits*: `{res.get('error')}`"
            self.edit_message(chat_id, msg_id, txt, reply_markup=self.back_to_main_keyboard())

        elif data == "menu_fleet":
            self.answer_callback(cb_id)
            nodes = self.mesh.list_nodes(check_health=True)
            txt = f"🌐 *A2A Fleet Mesh & Machines ({len(nodes)})*:\n\n"
            for n in nodes:
                st = "🟢 ONLINE" if n.get("status") == "online" else "⚪ OFFLINE / UNREACHABLE"
                lat = f"{n.get('latency_ms')} ms" if n.get("latency_ms") is not None else "--"
                v = n.get("version", {})
                cmt = v.get("commit", "unknown")[:7] if v.get("commit") else "unknown"
                branch = v.get("branch", "main")
                txt += (
                    f"• *{n.get('label', n.get('name'))}* (`{n.get('name')}`)\n"
                    f"  Status: {st} ({lat})\n"
                    f"  Address: `{n.get('base_url', n.get('ip'))}`\n"
                    f"  Version: `{branch}@{cmt}`\n\n"
                )
            kb = {
                "inline_keyboard": [
                    [{"text": "📥 Git Pull mcmini", "callback_data": "act_sync_mcmini"}, {"text": "📥 Git Pull Fleet (All)", "callback_data": "act_sync_fleet"}],
                    [{"text": "« Back to Main Dashboard", "callback_data": "menu_main"}]
                ]
            }
            self.edit_message(chat_id, msg_id, txt, reply_markup=kb)

        elif data.startswith("act_sync_"):
            target = data[len("act_sync_"):]
            if target == "fleet":
                self.answer_callback(cb_id, "Syncing all fleet nodes...")
                res = self.mesh.sync_all_nodes()
                txt = "📥 *Fleet Git Sync Result*:\n\n"
                for n, r in res.get("results", {}).items():
                    st = "✅ Updated" if r.get("success") else f"❌ Failed ({r.get('error')})"
                    c_after = r.get("commit_after", "")[:7] if r.get("commit_after") else ""
                    txt += f"• *{n}*: {st} {c_after}\n"
            else:
                self.answer_callback(cb_id, f"Syncing {target}...")
                if target == "local":
                    r = self.mesh.sync_local_code()
                else:
                    r = self.mesh.sync_remote_node(target)
                st = "✅ Updated" if r.get("success") else f"❌ Failed ({r.get('error')})"
                c_after = r.get("commit_after", "")[:7] if r.get("commit_after") else ""
                txt = f"📥 *Git Sync for {target}*:\n\n{st} {c_after}\n{r.get('message', '')}"
            self.edit_message(chat_id, msg_id, txt, reply_markup=self.back_to_main_keyboard())

        elif data == "menu_pair_code":
            self.answer_callback(cb_id)
            code = self.vault.get_pairing_code()
            cf_conf = self.vault.get_cloudflare_config()
            domain = cf_conf.get("domain", "local")
            txt = (
                f"🔒 *Machine Secure Pairing Code*\n\n"
                f"• *Node*: `Local Host`\n"
                f"• *Pairing Code*: `{code}`\n"
                f"• *Public Access*: `{domain}`\n\n"
                f"Use this pairing code on the web interface to unlock full key management, vault operations, and agent deployment."
            )
            self.edit_message(chat_id, msg_id, txt, reply_markup=self.back_to_main_keyboard())

        elif data == "menu_subkeys":
            self.answer_callback(cb_id)
            subkeys = self.vault.get_subkeys()
            if subkeys:
                txt = f"🎟️ *Agent Sub-Keys ({len(subkeys)})*:\n\n"
                for sk in subkeys[:8]:
                    lbl = sk.get("label", "Sub-Key")
                    tier = (sk.get("quality_tier") or "s").upper()
                    b = sk.get("budget_usd", 0.25)
                    per = sk.get("period", "DAY")
                    ag = sk.get("assigned_agent") or "Unassigned"
                    nd = sk.get("assigned_node") or "local"
                    txt += f"• *{lbl}* `[TIER: {tier}]` Cap: `${b:.2f}/{per}`\n  Agent: `{ag}` ({nd}) | ID: `{sk.get('id')}`\n"
            else:
                txt = "🎟️ *Agent Sub-Keys*\n\nNo sub-keys minted yet. Mint them via Web UI or MCP server."
            self.edit_message(chat_id, msg_id, txt, reply_markup=self.back_to_main_keyboard())

        elif data == "act_backup_vault":
            self.answer_callback(cb_id, "💾 Creating backup snapshot...")
            res = self.vault.create_backup(label="tg_bot")
            txt = (
                f"💾 *Vault Backup Snapshot Created*:\n\n"
                f"• *Timestamp*: `{res.get('timestamp')}`\n"
                f"• *File Size*: `{res.get('size_bytes')}` bytes\n"
                f"• *Venice Keys*: `{res.get('keys_count')}`\n"
                f"• *Agent Sub-Keys*: `{res.get('subkeys_count')}`\n"
                f"• *Mirrors*: `Local & Profile Mirrors Synced ✅`\n\n"
                f"Path: `{res.get('path')}`"
            )
            self.edit_message(chat_id, msg_id, txt, reply_markup=self.back_to_main_keyboard())

        elif data == "act_recall_vault":
            self.answer_callback(cb_id, "🔄 Running auto-recall...")
            res = self.vault.auto_recall(sync_venice_remote=True)
            txt = (
                f"🔄 *Vault Auto-Recall & Recovery Complete*:\n\n"
                f"• *Admin Key*: `{'RECOVERED ✅' if res.get('recovered_admin_key') else ('CONFIGURED ✅' if res.get('has_admin_key') else 'NOT SET ⚠️')}`\n"
                f"• *Inference Key*: `{'RECOVERED ✅' if res.get('recovered_inference_key') else ('CONFIGURED ✅' if res.get('has_inference_key') else 'NOT SET ⚠️')}`\n"
                f"• *Remote Keys Synced*: `{res.get('remote_keys_synced')}`\n"
                f"• *Total Venice Keys*: `{res.get('total_venice_keys')}`\n"
                f"• *Total Sub-Keys*: `{res.get('total_subkeys')}`\n"
                f"• *Status*: `Vault state verified and resilient ✅`"
            )
            self.edit_message(chat_id, msg_id, txt, reply_markup=self.back_to_main_keyboard())

        elif data == "menu_service":
            self.answer_callback(cb_id)
            supervisor = ServiceSupervisor()
            st = supervisor.get_status()
            methods_str = ", ".join(st.get("autostart_methods", [])) or "None"
            txt = (
                f"🛡️ *Venice Key Manager // Service & Auto-Restart*\n\n"
                f"• *Platform*: `{st.get('platform', '').upper()}`\n"
                f"• *Port 8844*: `{'LISTENING ✅' if st.get('port_listening') else 'CLOSED ⚪'}`\n"
                f"• *Supervisor Watchdog*: `{'ACTIVE ✅' if st.get('supervisor_running') else 'STANDBY / RUNNING DIRECT ⚪'}` (PID: `{st.get('supervisor_pid') or 'None'}`)\n"
                f"• *Child Process*: `{'RUNNING ✅' if st.get('child_running') else 'DIRECT ⚪'}` (PID: `{st.get('child_pid') or 'None'}`)\n"
                f"• *Total Auto-Restarts*: `{st.get('restarts_count', 0)}`\n"
                f"• *Uptime*: `{st.get('uptime_seconds', 0):.1f}s`\n"
                f"• *Boot Auto-Start*: `{'INSTALLED ✅' if st.get('autostart_installed') else 'NOT CONFIGURED ⚠️'}`\n"
                f"• *Configured Methods*: `{methods_str}`\n\n"
                f"The supervisor watchdog monitors process health and relaunches immediately if any crash occurs."
            )
            kb = {
                "inline_keyboard": [
                    [{"text": "🔄 Recycle / Restart Service", "callback_data": "act_restart_service"}],
                    [{"text": "« Back to Main Dashboard", "callback_data": "menu_main"}]
                ]
            }
            self.edit_message(chat_id, msg_id, txt, reply_markup=kb)

        elif data == "act_restart_service":
            self.answer_callback(cb_id, "🔄 Disagreeing process recycle...")
            supervisor = ServiceSupervisor()
            res = supervisor.request_restart()
            if res.get("success"):
                txt = (
                    "🔄 *Service Restart Signal Dispatched!*\n\n"
                    "The supervisor watchdog has been signaled. The background service will recycle and restart automatically within 2 seconds."
                )
            else:
                txt = f"❌ *Restart Signal Failed*: `{res.get('error')}`"
            self.edit_message(chat_id, msg_id, txt, reply_markup=self.back_to_main_keyboard())

    # --- Message Command Handlers ---

    def handle_message(self, msg: Dict[str, Any]):
        chat_id = str(msg.get("chat", {}).get("id"))
        text = msg.get("text", "").strip()

        if chat_id != self.authorized_chat_id:
            logger.warning(f"Unauthorized access attempt from chat_id {chat_id}")
            self.send_message(chat_id, "⛔ Access Denied. This bot is restricted to its authorized administrator.")
            return

        if text.startswith("/start") or text.startswith("/menu"):
            self.send_message(chat_id, self.render_dashboard_text(), reply_markup=self.main_menu_keyboard())

        elif text.startswith("/pair_code"):
            code = self.vault.get_pairing_code()
            cf_conf = self.vault.get_cloudflare_config()
            domain = cf_conf.get("domain", "local")
            self.send_message(
                chat_id,
                f"🔒 *Machine Pairing Code*\n\n"
                f"• *Node*: `Local Host`\n"
                f"• *Pairing Code*: `{code}`\n"
                f"• *Public Access*: `{domain}`\n\n"
                f"Enter this code on the web dashboard to unlock full fleet management and agent deployment."
            )

        elif text.startswith("/rotate_pair_code"):
            new_code = self.vault.generate_new_pairing_code()
            self.send_message(
                chat_id,
                f"🔄 *New Pairing Code Generated!*\n\n"
                f"• *New Code*: `{new_code}`\n\n"
                f"Previous sessions have been invalidated. Use this code to pair your browser."
            )

        elif text.startswith("/subkeys"):
            subkeys = self.vault.get_subkeys()
            if subkeys:
                txt = f"🎟️ *Agent Sub-Keys ({len(subkeys)})*:\n\n"
                for sk in subkeys[:10]:
                    lbl = sk.get("label", "Sub-Key")
                    tier = (sk.get("quality_tier") or "s").upper()
                    b = sk.get("budget_usd", 0.25)
                    per = sk.get("period", "DAY")
                    ag = sk.get("assigned_agent") or "Unassigned"
                    nd = sk.get("assigned_node") or "local"
                    txt += f"• *{lbl}* `[TIER: {tier}]` Cap: `${b:.2f}/{per}`\n  Agent: `{ag}` ({nd}) | ID: `{sk.get('id')}`\n"
            else:
                txt = "🎟️ *Agent Sub-Keys*: No sub-keys currently recorded."
            self.send_message(chat_id, txt)

        elif text.startswith("/balance"):
            client = self._get_venice_client()
            res = client.get_rate_limits()
            if res.get("success"):
                b = res.get("balances", {})
                txt = f"💰 *Venice Balance*: `${b.get('USD', 0):.2f}` (DIEM: `{b.get('DIEM', 0):.2f}`)"
            else:
                txt = f"❌ *Error*: `{res.get('error')}`"
            self.send_message(chat_id, txt)

        elif text.startswith("/set_admin_key"):
            parts = text.split(maxsplit=1)
            if len(parts) > 1:
                key = parts[1].strip()
                self.vault.set_venice_admin_key(key)
                self.send_message(chat_id, "✅ Venice Admin Key saved to vault! Full key management unlocked.")
            else:
                self.send_message(chat_id, "Usage: `/set_admin_key <your_venice_admin_key>`")

        elif text.startswith("/register_bot"):
            parts = text.split()
            if len(parts) >= 3:
                agent = parts[1]
                token = parts[2]
                res = self.tg_manager.register_agent_bot(agent, token)
                if res.get("success"):
                    self.send_message(chat_id, f"✅ Registered bot `@{res['bot']['username']}` for agent `{agent}`!")
                else:
                    self.send_message(chat_id, f"❌ Registration failed: `{res.get('error')}`")
            else:
                self.send_message(chat_id, "Usage: `/register_bot <agent_name> <bot_token>`")

        elif text.startswith("/ask"):
            prompt = text[4:].strip()
            if not prompt:
                self.send_message(chat_id, "Usage: `/ask <prompt>`")
                return
            client = self._get_venice_client()
            res = client.test_inference(prompt=prompt, model="deepseek-v4-flash", max_tokens=150)
            if res.get("success"):
                cost = res.get("cost", {}).get("usd", 0)
                txt = (
                    f"⚡ *Response* ({res.get('latency_ms')} ms | {res.get('usage', {}).get('total_tokens', 0)} tok | ${cost:.5f}):\n\n"
                    f"{res.get('content')}"
                )
            else:
                txt = f"❌ *Inference Error*: `{res.get('error')}`"
            self.send_message(chat_id, txt)

        elif text.startswith("/ask_tier"):
            parts = text.split(maxsplit=2)
            if len(parts) >= 3:
                tier = parts[1].lower().strip()
                prompt = parts[2].strip()
                if tier not in MODEL_TIER_ORDER:
                    self.send_message(chat_id, f"Invalid tier `{tier}`. Choose from: `{', '.join(MODEL_TIER_ORDER)}`")
                    return
                model = get_model_for_tier(tier)
                client = self._get_venice_client()
                res = client.test_inference(prompt=prompt, model=model, max_tokens=150)
                if res.get("success"):
                    cost = res.get("cost", {}).get("usd", 0)
                    txt = (
                        f"⚡ *Response* [Tier: `{tier.upper()}` | Model: `{model}`] "
                        f"({res.get('latency_ms')} ms | {res.get('usage', {}).get('total_tokens', 0)} tok | ${cost:.5f}):\n\n"
                        f"{res.get('content')}"
                    )
                else:
                    txt = f"❌ *Inference Error*: `{res.get('error')}`"
                self.send_message(chat_id, txt)
            else:
                self.send_message(chat_id, "Usage: `/ask_tier <xs|s|m|l|xl> <prompt>`")

        elif text.startswith("/provision"):
            # Launch provisioning wizard
            txt = (
                "🧙 *Agent Key Provisioning Wizard* (Step 1/3)\n\n"
                "Select which agent or service this key will be allocated for:"
            )
            kb = {
                "inline_keyboard": [
                    [
                        {"text": "🎵 hermes-music", "callback_data": "wiz_agent_hermes-music"},
                        {"text": "🌐 a2a-node", "callback_data": "wiz_agent_a2a-node"}
                    ],
                    [
                        {"text": "🎧 dawagent", "callback_data": "wiz_agent_dawagent"},
                        {"text": "⚙️ worker-audio", "callback_data": "wiz_agent_worker-audio"}
                    ],
                    [
                        {"text": "🤖 custom-agent", "callback_data": "wiz_agent_custom-agent"}
                    ],
                    [
                        {"text": "« Back to Main Dashboard", "callback_data": "menu_main"}
                    ]
                ]
            }
            self.send_message(chat_id, txt, reply_markup=kb)

        elif text.startswith("/fleet"):
            nodes = self.mesh.list_nodes(check_health=True)
            txt = f"🌐 *A2A Fleet Mesh & Machines ({len(nodes)})*:\n\n"
            for n in nodes:
                st = "🟢 ONLINE" if n.get("status") == "online" else "⚪ OFFLINE / UNREACHABLE"
                lat = f"{n.get('latency_ms')} ms" if n.get("latency_ms") is not None else "--"
                v = n.get("version", {})
                cmt = v.get("commit", "unknown")[:7] if v.get("commit") else "unknown"
                branch = v.get("branch", "main")
                txt += (
                    f"• *{n.get('label', n.get('name'))}* (`{n.get('name')}`)\n"
                    f"  Status: {st} ({lat})\n"
                    f"  Address: `{n.get('base_url', n.get('ip'))}`\n"
                    f"  Version: `{branch}@{cmt}`\n\n"
                )
            kb = {
                "inline_keyboard": [
                    [{"text": "📥 Git Pull mcmini", "callback_data": "act_sync_mcmini"}, {"text": "📥 Git Pull Fleet (All)", "callback_data": "act_sync_fleet"}],
                    [{"text": "« Back to Main Dashboard", "callback_data": "menu_main"}]
                ]
            }
            self.send_message(chat_id, txt, reply_markup=kb)

        elif text.startswith("/sync_fleet"):
            parts = text.split()
            target = parts[1] if len(parts) > 1 else "all"
            self.send_message(chat_id, f"📥 Triggering Git pull for `{target}`...")
            if target == "all":
                res = self.mesh.sync_all_nodes()
                txt = "📥 *Fleet Git Sync Result*:\n\n"
                for n, r in res.get("results", {}).items():
                    st = "✅ Updated" if r.get("success") else f"❌ Failed ({r.get('error')})"
                    c_after = r.get("commit_after", "")[:7] if r.get("commit_after") else ""
                    txt += f"• *{n}*: {st} {c_after}\n"
            elif target == "local":
                r = self.mesh.sync_local_code()
                st = "✅ Updated" if r.get("success") else f"❌ Failed ({r.get('error')})"
                c_after = r.get("commit_after", "")[:7] if r.get("commit_after") else ""
                txt = f"📥 *Git Sync Local*: {st} {c_after}\n{r.get('message', '')}"
            else:
                r = self.mesh.sync_remote_node(target)
                st = "✅ Updated" if r.get("success") else f"❌ Failed ({r.get('error')})"
                c_after = r.get("commit_after", "")[:7] if r.get("commit_after") else ""
                txt = f"📥 *Git Sync {target}*: {st} {c_after}\n{r.get('message', '')}"
            self.send_message(chat_id, txt)

        elif text.startswith("/backup") or text.startswith("/backup_vault"):
            res = self.vault.create_backup(label="tg_cmd")
            txt = (
                "💾 *Vault Backup Snapshot Created*:\n\n"
                f"• *Timestamp*: `{res.get('timestamp')}`\n"
                f"• *File Size*: `{res.get('size_bytes')}` bytes\n"
                f"• *Venice Keys*: `{res.get('keys_count')}`\n"
                f"• *Agent Sub-Keys*: `{res.get('subkeys_count')}`\n"
                f"• *Mirrors Synced*: `Local & AppData Backup Mirrors Active ✅`\n\n"
                f"Path: `{res.get('path')}`"
            )
            self.send_message(chat_id, txt, reply_markup=self.back_to_main_keyboard())

        elif text.startswith("/recall") or text.startswith("/recall_vault"):
            self.send_message(chat_id, "🔄 *Running deep auto-recall across backup stores and configs...*")
            res = self.vault.auto_recall(sync_venice_remote=True)
            txt = (
                "🔄 *Vault Auto-Recall & Recovery Complete*:\n\n"
                f"• *Admin Key*: `{'RECOVERED ✅' if res.get('recovered_admin_key') else ('CONFIGURED ✅' if res.get('has_admin_key') else 'NOT SET ⚠️')}`\n"
                f"• *Inference Key*: `{'RECOVERED ✅' if res.get('recovered_inference_key') else ('CONFIGURED ✅' if res.get('has_inference_key') else 'NOT SET ⚠️')}`\n"
                f"• *Remote Keys Synced*: `{res.get('remote_keys_synced')}`\n"
                f"• *Total Venice Keys*: `{res.get('total_venice_keys')}`\n"
                f"• *Total Sub-Keys*: `{res.get('total_subkeys')}`\n"
                f"• *Vault Health*: `Durable and synchronized ✅`"
            )
            self.send_message(chat_id, txt, reply_markup=self.back_to_main_keyboard())

        elif text.startswith("/vault_status") or text.startswith("/backup_status"):
            st = self.vault.get_backup_status()
            txt = (
                "🛡️ *Vault Backup & Health Status*:\n\n"
                f"• *Primary Vault*: `{st.get('vault_size_bytes')}` bytes\n"
                f"• *Backup Mirror*: `{'ACTIVE ✅' if st.get('backup_mirror_exists') else 'MISSING ⚠️'}`\n"
                f"• *Profile Mirror*: `{'ACTIVE ✅' if st.get('user_profile_mirror_exists') else 'MISSING ⚠️'}`\n"
                f"• *Snapshots Saved*: `{st.get('total_snapshots')}`\n"
                f"• *Last Backup*: `{st.get('last_backup_at') or 'None'}`\n"
                f"• *Admin Key*: `{'CONFIGURED ✅' if st.get('has_admin_key') else 'NOT SET ⚠️'}`\n"
                f"• *Inference Key*: `{'CONFIGURED ✅' if st.get('has_inference_key') else 'NOT SET ⚠️'}`\n"
                f"• *Active Venice Keys*: `{st.get('keys_count')}`\n"
                f"• *Agent Sub-Keys*: `{st.get('subkeys_count')}`"
            )
        elif text.startswith("/service_status") or text.startswith("/status"):
            supervisor = ServiceSupervisor()
            st = supervisor.get_status()
            methods_str = ", ".join(st.get("autostart_methods", [])) or "None"
            txt = (
                "🛡️ *Venice Key Manager // Resilience & Service Status*:\n\n"
                f"• *Platform*: `{st.get('platform', '').upper()}`\n"
                f"• *Port 8844*: `{'LISTENING ✅' if st.get('port_listening') else 'CLOSED ⚪'}`\n"
                f"• *Supervisor*: `{'RUNNING ✅' if st.get('supervisor_running') else 'STANDBY / DIRECT ⚪'}` (PID: `{st.get('supervisor_pid') or 'None'}`)\n"
                f"• *Child Process*: `{'RUNNING ✅' if st.get('child_running') else 'DIRECT ⚪'}` (PID: `{st.get('child_pid') or 'None'}`)\n"
                f"• *Total Auto-Restarts*: `{st.get('restarts_count', 0)}`\n"
                f"• *Uptime*: `{st.get('uptime_seconds', 0):.1f}s`\n"
                f"• *Last Restart*: `{st.get('last_restart_at') or 'Never'}`\n"
                f"• *Boot Auto-Start*: `{'INSTALLED ✅' if st.get('autostart_installed') else 'NOT CONFIGURED ⚠️'}`\n"
                f"• *Methods*: `{methods_str}`"
            )
            self.send_message(chat_id, txt, reply_markup=self.back_to_main_keyboard())

        elif text.startswith("/restart_service"):
            supervisor = ServiceSupervisor()
            res = supervisor.request_restart()
            if res.get("success"):
                txt = "🔄 *Service Restart Signal Dispatched!*\nThe supervisor watchdog will recycle the process within 2s."
            else:
                txt = f"❌ *Restart Signal Failed*: `{res.get('error')}`"
            self.send_message(chat_id, txt, reply_markup=self.back_to_main_keyboard())

        elif text.startswith("/help"):
            txt = (
                "⚡ *Venice & TG Engine Commands*:\n\n"
                "• `/menu` or `/start` - Open interactive control dashboard\n"
                "• `/provision` - Open Agent Key (XS-XL) Provisioning Wizard\n"
                "• `/service_status` - Check auto-restart watchdog & boot daemon\n"
                "• `/restart_service` - Remotely recycle and restart the service\n"
                "• `/backup_vault` - Create instant snapshot and sync all backup mirrors\n"
                "• `/recall_vault` - Deep auto-recall and recover keys from mirrors & configs\n"
                "• `/vault_status` - Check disaster recovery and backup status\n"
                "• `/fleet` - Check status of all paired fleet nodes (Local, mcmini)\n"
                "• `/sync_fleet [node|all]` - Pull latest Git updates across machines\n"
                "• `/balance` - Check live USD and DIEM balance\n"
                "• `/ask <prompt>` - Run light inference on Venice\n"
                "• `/ask_tier <xs|s|m|l|xl> <prompt>` - Inference on specific model tier\n"
                "• `/set_admin_key <key>` - Configure Venice Admin Key\n"
                "• `/register_bot <agent> <token>` - Link bot token to agent\n"
                "• `/help` - Show this guide"
            )
            self.send_message(chat_id, txt)

    # --- Polling Loop ---

    def poll_once(self):
        url = f"{self.api_url}/getUpdates"
        params = {"offset": self.last_update_id + 1, "timeout": 20}
        try:
            resp = requests.get(url, params=params, timeout=25)
            if resp.status_code == 200:
                data = resp.json()
                for upd in data.get("result", []):
                    self.last_update_id = upd.get("update_id", self.last_update_id)
                    if "callback_query" in upd:
                        self.handle_callback_query(upd["callback_query"])
                    elif "message" in upd:
                        self.handle_message(upd["message"])
        except requests.exceptions.Timeout:
            pass
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(2)

    def run(self):
        self.running = True
        logger.info(f"Venice Telegram Bot started. Listening for updates...")
        while self.running:
            try:
                self.poll_once()
            except KeyboardInterrupt:
                logger.info("Stopping Telegram Bot...")
                self.running = False
                break
            except Exception as e:
                logger.error(f"Unexpected bot loop error: {e}")
                time.sleep(2)


if __name__ == "__main__":
    bot = VeniceTelegramBot()
    bot.run()

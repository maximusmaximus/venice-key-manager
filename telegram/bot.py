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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("venice_tg_bot")


class VeniceTelegramBot:
    def __init__(self, vault: Optional[KeyVault] = None):
        self.vault = vault or KeyVault()
        self.tg_manager = TelegramAgentManager(self.vault)
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
                    {"text": "➕ Issue Key ($5)", "callback_data": "act_create_key_5"}
                ],
                [
                    {"text": "⚡ Light Inference", "callback_data": "act_quick_infer"},
                    {"text": "🚀 Deploy to Config", "callback_data": "act_deploy_config"}
                ],
                [
                    {"text": "📊 Model Limits", "callback_data": "menu_limits"},
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

        return (
            f"⚡ *VENICE & TG KEY ENGINE // CONTROLLER*\n\n"
            f"• *USD Balance*: `${usd_bal}`\n"
            f"• *DIEM Balance*: `{diem_bal} DIEM`\n"
            f"• *Account Tier*: `{tier}` (Epoch: `{epoch_str}`)\n"
            f"• *Admin Key*: `{'CONFIGURED ✅' if has_admin else 'NOT SET ⚠️ (Inference Mode)'}`\n"
            f"• *Registered Agent Bots*: `{agent_count}`\n"
            f"• *Managed Venice Keys*: `{keys_count}`\n\n"
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
            if not admin_key:
                txt = (
                    f"🔑 *Venice.ai Keys*\n\n"
                    f"⚠️ *Admin Key Not Configured*\n"
                    f"Listing remote keys requires an ADMIN key. You can configure your Admin Key in the Web Dashboard (`http://localhost:8844`) or set it via command:\n"
                    f"`/set_admin_key <your_key>`\n\n"
                    f"Currently active inference key is operational for chat completions."
                )
            else:
                res = client.list_keys(admin_key=admin_key)
                if res.get("success"):
                    keys = res.get("keys", [])
                    txt = f"🔑 *Active Venice Keys ({len(keys)})*:\n\n"
                    for k in keys[:8]:
                        txt += f"• *{k.get('description', 'Key')}* (`{k.get('apiKeyType')}`)\n  ID: `{k.get('id')}`\n"
                else:
                    txt = f"❌ *Error listing keys*: `{res.get('error')}`"

            self.edit_message(chat_id, msg_id, txt, reply_markup=self.back_to_main_keyboard())

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

        elif data == "act_create_key_5":
            self.answer_callback(cb_id, "Issuing key...")
            admin_key = self.vault.get_venice_admin_key()
            if not admin_key:
                self.edit_message(
                    chat_id,
                    msg_id,
                    "⚠️ *Admin Key Required*\nTo issue new Venice keys via API, an Admin key is required. Please set it via `/set_admin_key <key>` or in the Web Dashboard.",
                    reply_markup=self.back_to_main_keyboard()
                )
                return

            res = client.create_key(
                description="Telegram Issued Key",
                key_type="INFERENCE",
                limit_usd=5.0
            )
            if res.get("success"):
                k = res.get("key", {})
                self.vault.store_venice_key(k)
                raw_k = k.get("apiKey") or k.get("key") or k.get("id")
                txt = (
                    f"✅ *New Venice Key Generated!*\n\n"
                    f"• *Description*: `Telegram Issued Key`\n"
                    f"• *Type*: `INFERENCE`\n"
                    f"• *Monthly Limit*: `$5.00 USD`\n"
                    f"• *Key*: `{raw_k}`\n\n"
                    f"Stored in local vault. Tap Deploy to push it to `hermes config.yaml`."
                )
                kb = {
                    "inline_keyboard": [
                        [{"text": "🚀 Deploy to Hermes config.yaml", "callback_data": f"deploy_key_{k.get('id')}"}],
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

        elif data == "act_quick_infer":
            self.answer_callback(cb_id, "Running light inference...")
            prompt = "Say hello from Venice AI in 1 short sentence."
            res = client.test_inference(prompt=prompt, model="deepseek-v4-flash", max_tokens=60)
            if res.get("success"):
                cost = res.get("cost", {}).get("usd", 0)
                txt = (
                    f"⚡ *Venice Light Inference Result*\n\n"
                    f"• *Model*: `{res.get('model')}`\n"
                    f"• *Latency*: `{res.get('latency_ms')} ms`\n"
                    f"• *Tokens*: `{res.get('usage', {}).get('total_tokens', 0)}`\n"
                    f"• *Cost*: `${cost:.6f} USD`\n\n"
                    f"*Output*:\n> {res.get('content')}\n\n"
                    f"_Send /ask <prompt> to test any custom prompt!_"
                )
            else:
                txt = f"❌ *Inference Error*: `{res.get('error')}`"
            self.edit_message(chat_id, msg_id, txt, reply_markup=self.back_to_main_keyboard())

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

        elif text.startswith("/help"):
            txt = (
                "⚡ *Venice & TG Engine Commands*:\n\n"
                "• `/menu` or `/start` - Open interactive control dashboard\n"
                "• `/balance` - Check live USD and DIEM balance\n"
                "• `/ask <prompt>` - Run light inference on Venice\n"
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

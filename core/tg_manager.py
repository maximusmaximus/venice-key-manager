"""
Telegram Agent Bot Manager & @BotFather Automation/Wizard.
Validates tokens, maps bots to agents, tests connectivity, and integrates with @BotFather.
"""

import os
import re
import time
import requests
from datetime import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path

from .vault import KeyVault
from .deployer import ConfigDeployer


class TelegramAgentManager:
    API_BASE = "https://api.telegram.org/bot"

    def __init__(self, vault: Optional[KeyVault] = None):
        self.vault = vault or KeyVault()

    def validate_token(self, bot_token: str) -> Dict[str, Any]:
        """
        Validates a Telegram bot token by querying getMe.
        Returns bot info if valid.
        """
        token = bot_token.strip()
        url = f"{self.API_BASE}{token}/getMe"
        try:
            resp = requests.get(url, timeout=10)
            data = resp.json()
            if resp.status_code == 200 and data.get("ok"):
                result = data.get("result", {})
                return {
                    "valid": True,
                    "bot_id": result.get("id"),
                    "first_name": result.get("first_name"),
                    "username": result.get("username"),
                    "can_join_groups": result.get("can_join_groups"),
                    "can_read_all_group_messages": result.get("can_read_all_group_messages"),
                    "raw": result
                }
            return {
                "valid": False,
                "error": data.get("description", "Invalid bot token or failed authentication")
            }
        except Exception as e:
            return {"valid": False, "error": str(e)}

    def send_test_message(self, bot_token: str, chat_id: Optional[str] = None, text: str = "") -> Dict[str, Any]:
        """
        Sends a test verification message to the specified chat (defaults to authorized chat).
        """
        target_chat = chat_id or self.vault.get_authorized_chat_id()
        token = bot_token.strip()
        msg_text = text or (
            f"⚡ **Agent Bot Verification**\n"
            f"• Time: `{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}`\n"
            f"• Status: **Connected & Operational** 🚀\n"
            f"• Managed via Venice & TG Key Manager"
        )
        url = f"{self.API_BASE}{token}/sendMessage"
        try:
            resp = requests.post(url, json={
                "chat_id": target_chat,
                "text": msg_text,
                "parse_mode": "Markdown"
            }, timeout=10)
            data = resp.json()
            if resp.status_code == 200 and data.get("ok"):
                return {"success": True, "message_id": data.get("result", {}).get("message_id")}
            return {"success": False, "error": data.get("description", resp.text)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def register_agent_bot(
        self,
        agent_name: str,
        bot_token: str,
        config_path: str = "",
        notes: str = ""
    ) -> Dict[str, Any]:
        """
        Validates token, maps it to an agent, and records it in the vault.
        """
        val = self.validate_token(bot_token)
        if not val.get("valid"):
            return {"success": False, "error": f"Invalid bot token: {val.get('error')}"}

        bot_info = {
            "agent_name": agent_name,
            "bot_token": bot_token.strip(),
            "bot_id": val.get("bot_id"),
            "username": val.get("username"),
            "first_name": val.get("first_name"),
            "config_path": config_path or str(Path(os.environ.get("AGENT_CONFIG_PATH", "config.yaml"))),
            "notes": notes,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "last_verified_at": datetime.utcnow().isoformat() + "Z",
            "is_valid": True
        }

        self.vault.register_agent_bot(agent_name, bot_info)
        return {"success": True, "agent": agent_name, "bot": bot_info}

    def list_agent_bots(self, check_live_status: bool = False) -> List[Dict[str, Any]]:
        """
        Returns all registered agent bots with their current status.
        """
        bots = self.vault.get_agent_bots()
        result = []
        for name, info in bots.items():
            item = dict(info)
            if check_live_status:
                val = self.validate_token(info.get("bot_token", ""))
                item["is_live"] = val.get("valid", False)
                if not val.get("valid"):
                    item["live_error"] = val.get("error")
            result.append(item)
        return result

    def deploy_agent_bot(self, agent_name: str, target_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Deploys the agent's bot token to its assigned or target configuration file.
        """
        bot = self.vault.get_agent_bot(agent_name)
        if not bot:
            return {"success": False, "error": f"No agent bot found with name '{agent_name}'"}

        dest = target_path or bot.get("config_path") or str(Path(os.environ.get("AGENT_CONFIG_PATH", "config.yaml")))
        res = ConfigDeployer.deploy_telegram_token(bot["bot_token"], Path(dest))
        if res.get("success"):
            bot["config_path"] = dest
            bot["last_deployed_at"] = datetime.utcnow().isoformat() + "Z"
            self.vault.register_agent_bot(agent_name, bot)
        return res

    # --- @BotFather Integration ---

    def generate_botfather_wizard(self, agent_name: str, suggested_name: str = "", suggested_username: str = "") -> Dict[str, Any]:
        """
        Generates guided steps and deep link to create a bot via @BotFather.
        """
        clean_agent = re.sub(r'[^a-zA-Z0-9]', '', agent_name).lower()
        bot_name = suggested_name or f"{agent_name.capitalize()} Agent"
        bot_username = suggested_username or f"{clean_agent}_{int(time.time()) % 10000}_bot"
        if not bot_username.endswith("_bot") and not bot_username.endswith("bot"):
            bot_username += "_bot"

        return {
            "agent_name": agent_name,
            "suggested_name": bot_name,
            "suggested_username": bot_username,
            "botfather_link": "https://t.me/BotFather",
            "botfather_deep_link": "https://t.me/BotFather?start=newbot",
            "steps": [
                "1. Click the BotFather link: https://t.me/BotFather",
                f"2. Send command: /newbot",
                f"3. Provide the bot display name: {bot_name}",
                f"4. Provide the username (must end in 'bot'): {bot_username}",
                "5. BotFather will reply with your HTTP API token (format: 1234567890:ABC-DEF...).",
                "6. Paste that token into the Web UI or send it via Telegram to register it to your agent!"
            ]
        }

    async def automate_botfather_create_mtproto(
        self,
        agent_name: str,
        bot_name: str,
        bot_username: str,
        api_id: Optional[str] = None,
        api_hash: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Automates bot creation via Telethon MTProto client by messaging @BotFather.
        Requires active Telegram user session credentials.
        """
        try:
            from telethon import TelegramClient
        except ImportError:
            return {"success": False, "error": "telethon is not installed"}

        mt = self.vault.get_mtproto_config()
        c_api_id = api_id or mt.get("api_id")
        c_api_hash = api_hash or mt.get("api_hash")

        if not c_api_id or not c_api_hash:
            return {
                "success": False,
                "error": "Telegram MTProto API ID and API Hash are required for automatic @BotFather conversation. Use the guided wizard or configure MTProto credentials in settings."
            }

        session_path = Path(__file__).resolve().parent.parent / "botfather_user.session"
        client = TelegramClient(str(session_path), int(c_api_id), c_api_hash)

        try:
            await client.connect()
            if not await client.is_user_authorized():
                await client.disconnect()
                return {
                    "success": False,
                    "error": "Telegram user session is not authorized. Please log in via terminal once or use the guided wizard."
                }

            # Conversation with @BotFather
            async with client.conversation("@BotFather", timeout=30) as conv:
                await conv.send_message("/newbot")
                resp1 = await conv.get_response()

                await conv.send_message(bot_name)
                resp2 = await conv.get_response()

                # Ensure username ends with 'bot'
                u_name = bot_username if (bot_username.endswith("bot") or bot_username.endswith("Bot")) else f"{bot_username}_bot"
                await conv.send_message(u_name)
                resp3 = await conv.get_response()

                # Parse token from message
                match = re.search(r"(\d{8,10}:[a-zA-Z0-9_-]{35})", resp3.text)
                if match:
                    token = match.group(1)
                    await client.disconnect()
                    reg = self.register_agent_bot(agent_name, token, notes=f"Automated via @BotFather on {datetime.utcnow().strftime('%Y-%m-%d')}")
                    return {
                        "success": True,
                        "token": token,
                        "bot_username": u_name,
                        "bot_name": bot_name,
                        "registration": reg
                    }
                else:
                    await client.disconnect()
                    return {
                        "success": False,
                        "error": f"BotFather response: {resp3.text}"
                    }
        except Exception as e:
            try:
                await client.disconnect()
            except Exception:
                pass
            return {"success": False, "error": str(e)}

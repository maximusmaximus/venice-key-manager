import asyncio
import logging
from typing import Dict, Any, List, Optional
import httpx

from ..config import config
from ..client import VeniceClient
from ..models import (
    KeyCreateRequest,
    KeyCycleRequest,
    InferenceTestRequest,
)
from ..state import state_store
from ..report import DailyKeyReport

logger = logging.getLogger("venice_tg_bot")


class VeniceTelegramBot:
    def __init__(self, token: Optional[str] = None, client: Optional[VeniceClient] = None):
        self.token = token or config.telegram_bot_token
        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN must be configured.")
        self.api_url = f"https://api.telegram.org/bot{self.token}"
        self.client = client or VeniceClient()
        self.allowed_users = config.telegram_allowed_users
        self.offset = 0
        self.running = False
        # In-memory user state for multi-step prompts (e.g. key minting)
        self.user_state: Dict[int, Dict[str, Any]] = {}

    async def _api(self, method: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=35.0) as http:
            res = await http.post(f"{self.api_url}/{method}", json=payload or {})
            if res.status_code != 200:
                logger.error(f"Telegram API {method} error: {res.text}")
            return res.json()

    def is_user_allowed(self, user_id: int) -> bool:
        if not self.allowed_users:
            return True  # If no allowlist is configured, open to all (or warn)
        return user_id in self.allowed_users

    # =========================================================================
    # Keyboard Builders (Kitchen Sink Style)
    # =========================================================================

    def _main_reply_keyboard(self) -> Dict[str, Any]:
        return {
            "keyboard": [
                [{"text": "📊 Account & Balance"}, {"text": "🔑 List Keys"}],
                [{"text": "📋 Daily Keys Report"}, {"text": "⚠️ Low Balance Alert"}],
                [{"text": "➕ Mint Key"}, {"text": "🔄 Cycle Key"}],
                [{"text": "⚡ Quick Test"}, {"text": "🔒 E2EE Models"}],
                [{"text": "🌐 Web Dashboard"}, {"text": "💾 Download Backup"}],
            ],
            "resize_keyboard": True,
            "is_persistent": True,
        }

    # =========================================================================
    # Handlers
    # =========================================================================

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: str = "Markdown",
    ):
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "reply_markup": reply_markup or self._main_reply_keyboard(),
        }
        await self._api("sendMessage", payload)

    async def handle_start(self, chat_id: int):
        msg = (
            "⚡ *Venice.ai Key Manager Control Plane*\n\n"
            "Welcome! You can create, manage, rotate, and monitor Venice API keys, "
            "track inference balances, and test models in real time.\n\n"
            "Use the touch buttons below or slash commands:\n"
            "• `/balance` - Check live USD/DIEM balance & rate limits\n"
            "• `/keys` - List all active keys & remaining budgets\n"
            "• `/create <name> [usd]` - Mint dedicated sub-key\n"
            "• `/cycle <key_id>` - Rotate/replace an existing key\n"
            "• `/test` - Run lightweight inference benchmark\n"
            "• `/models` - Confidential E2EE enclave models\n"
            "• `/backup` - Download state & settings JSON"
        )
        await self.send_message(chat_id, msg)

    async def handle_balance(self, chat_id: int):
        try:
            rates = await self.client.get_rate_limits()
            thresh = state_store.get_global_threshold()
            usd = rates.balances.USD
            diem = rates.balances.DIEM
            is_low = usd <= thresh

            warn_badge = "\n\n⚠️ *LOW BALANCE WARNING:* Account balance is below threshold!" if is_low else ""

            msg = (
                f"📊 *Venice Account Status*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💵 *USD Balance:* `${usd:.4f}`\n"
                f"💎 *DIEM Balance:* `{diem:.4f}`\n"
                f"🛡️ *Access Status:* `{'Active / Permitted' if rates.accessPermitted else 'Restricted'}`\n"
                f"⚙️ *Tier:* `{rates.apiTier.id if rates.apiTier else 'paid'}`\n"
                f"⏳ *Next Epoch Reset:* `{rates.nextEpochBegins or '00:00 UTC'}`\n"
                f"🔔 *Warning Threshold:* `${thresh:.2f}`"
                f"{warn_badge}"
            )
            await self.send_message(chat_id, msg)
        except Exception as e:
            await self.send_message(chat_id, f"❌ Failed to query balance: {e}")

    async def handle_list_keys(self, chat_id: int):
        try:
            keys = await self.client.list_keys()
            if not keys:
                await self.send_message(chat_id, "ℹ️ No API keys found in this Venice account.")
                return

            low_keys = [k for k in keys if k.is_low_balance]
            msg = f"🔑 *Active Venice Keys ({len(keys)} total, {len(low_keys)} low-balance)*\n━━━━━━━━━━━━━━━━━━\n"

            for k in keys[:10]:  # Show top 10
                name = k.description or "Unnamed"
                last6 = k.last6Chars or "••••••"
                spent = float(k.currentPeriodUsage.usd or 0)
                limit = f"${k.consumptionLimits.usd:.2f}" if (k.consumptionLimits and k.consumptionLimits.usd is not None) else "Unlimited"
                rem = f"${k.remaining_usd:.4f}" if k.remaining_usd is not None else "--"
                warn = " ⚠️ *LOW*" if k.is_low_balance else ""

                msg += (
                    f"• *{name}* `(...{last6})` [{k.category}]\n"
                    f"  Spend: `${spent:.4f}` / {limit} ({k.limitPeriod})\n"
                    f"  Remaining: {rem}{warn}\n"
                    f"  _ID: `{k.id}`_\n\n"
                )

            if len(keys) > 10:
                msg += f"_...and {len(keys) - 10} more keys. View Web Dashboard for complete list._"

            # Provide inline button to cycle or create
            inline_kb = {
                "inline_keyboard": [
                    [{"text": "➕ Mint New Key", "callback_data": "start_mint"}],
                    [{"text": "🔄 Rotate / Cycle Key", "callback_data": "start_cycle"}],
                ]
            }
            await self.send_message(chat_id, msg, reply_markup=inline_kb)
        except Exception as e:
            await self.send_message(chat_id, f"❌ Failed to list keys: {e}")

    async def handle_start_mint_flow(self, chat_id: int, user_id: int):
        self.user_state[user_id] = {"action": "awaiting_mint_name"}
        await self.send_message(
            chat_id,
            "➕ *Mint New Key: Step 1/2*\n\nPlease reply with the **name or description** for the new key (e.g. `agent-worker-1`):"
        )

    async def handle_mint_name_input(self, chat_id: int, user_id: int, name: str):
        self.user_state[user_id] = {"action": "awaiting_mint_budget", "name": name}
        inline_kb = {
            "inline_keyboard": [
                [
                    {"text": "$0.50/day", "callback_data": "mint_budget:0.50"},
                    {"text": "$1.00/day", "callback_data": "mint_budget:1.00"},
                ],
                [
                    {"text": "$2.00/day", "callback_data": "mint_budget:2.00"},
                    {"text": "$5.00/day", "callback_data": "mint_budget:5.00"},
                ],
                [
                    {"text": "Unlimited", "callback_data": "mint_budget:none"},
                ]
            ]
        }
        await self.send_message(
            chat_id,
            f"➕ *Mint New Key: Step 2/2*\n\nKey Name: *{name}*\nSelect daily spending budget ceiling:",
            reply_markup=inline_kb
        )

    async def handle_mint_complete(self, chat_id: int, user_id: int, budget_val: Optional[float]):
        state = self.user_state.get(user_id, {})
        name = state.get("name", "Telegram Agent Key")
        self.user_state.pop(user_id, None)

        req = KeyCreateRequest(
            description=name,
            daily_usd=budget_val,
            limitPeriod="EPOCH",
            category="Telegram"
        )

        try:
            res = await self.client.create_key(req)
            msg = (
                f"🎉 *Venice Key Successfully Minted!*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🏷️ *Name:* `{res.description}`\n"
                f"🆔 *Key ID:* `{res.id}`\n"
                f"💵 *Daily Limit:* `${budget_val:.2f}`\n"
                f"📁 *Category:* `{res.category}`\n\n"
                f"🔑 *API Key Token:*\n`{res.apiKey}`\n\n"
                f"⚠️ *Important:* Copy and store this token now. It will not be shown again."
            )
            await self.send_message(chat_id, msg)
        except Exception as e:
            await self.send_message(chat_id, f"❌ Key minting failed: {e}")

    async def handle_cycle_prompt(self, chat_id: int):
        try:
            keys = await self.client.list_keys()
            if not keys:
                await self.send_message(chat_id, "ℹ️ No keys available to rotate.")
                return

            buttons = []
            for k in keys[:6]:
                label = f"🔄 {k.description[:18]} (...{k.last6Chars})"
                buttons.append([{"text": label, "callback_data": f"do_cycle:{k.id}"}])

            inline_kb = {"inline_keyboard": buttons}
            await self.send_message(
                chat_id,
                "🔄 *Select Key to Rotate / Cycle:*\n\nThe old key will be replaced with a fresh token having matching budget limits.",
                reply_markup=inline_kb
            )
        except Exception as e:
            await self.send_message(chat_id, f"❌ Error: {e}")

    async def handle_execute_cycle(self, chat_id: int, key_id: str):
        req = KeyCycleRequest(id=key_id, revoke_old=True)
        try:
            new_key_resp, revoked = await self.client.cycle_key(req)
            msg = (
                f"🔄 *Key Rotated Successfully!*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🏷️ *Name:* `{new_key_resp.description}`\n"
                f"🆔 *New Key ID:* `{new_key_resp.id}`\n"
                f"🗑️ *Old Key Revoked:* `{'Yes' if revoked else 'No'}`\n\n"
                f"🔑 *New Token:*\n`{new_key_resp.apiKey}`\n\n"
                f"Update your services with the new token."
            )
            await self.send_message(chat_id, msg)
        except Exception as e:
            await self.send_message(chat_id, f"❌ Key rotation failed: {e}")

    async def handle_quick_test(self, chat_id: int):
        await self.send_message(chat_id, "⚡ Running test inference against Venice `deepseek-v4-flash`...")
        res = await self.client.test_inference(
            prompt="Respond with 'Venice API connection verified' in 4 words.",
            model="deepseek-v4-flash"
        )
        if res.success:
            msg = (
                f"✅ *Inference Verified!*\n"
                f"• *Model:* `{res.model}`\n"
                f"• *Latency:* `{res.latency_ms} ms`\n"
                f"• *Tokens:* `{res.total_tokens}`\n\n"
                f"*Output:* {res.output}"
            )
        else:
            msg = f"❌ *Inference Failed:*\n`{res.error}`"
        await self.send_message(chat_id, msg)

    async def handle_e2ee_models(self, chat_id: int):
        models = await self.client.list_models()
        e2ee = [m for m in models if m.privacy == "e2ee"]

        msg = f"🔒 *Venice Hardware Enclave (E2EE) Models ({len(e2ee)} total)*\n━━━━━━━━━━━━━━━━━━\n"
        for m in e2ee[:8]:
            in_p = f"${m.pricing.input.get('usd', 0):.2f}" if (m.pricing and m.pricing.input) else "--"
            out_p = f"${m.pricing.output.get('usd', 0):.2f}" if (m.pricing and m.pricing.output) else "--"
            msg += f"• *{m.name}*\n  `{m.id}`\n  Price: {in_p} / {out_p} per 1M\n\n"

        await self.send_message(chat_id, msg)

    async def handle_daily_report(self, chat_id: int):
        await self.send_message(chat_id, "⏳ Generating real-time Venice keys operations report...")
        reporter = DailyKeyReport(client=self.client)
        data = await reporter.generate_report_data()
        msg = reporter.format_markdown(data)
        await self.send_message(chat_id, msg)

    async def handle_low_balance_alert(self, chat_id: int):
        rates = await self.client.get_rate_limits()
        keys = await self.client.list_keys()
        thresh = state_store.get_global_threshold()
        low_keys = [k for k in keys if k.is_low_balance]
        acc_low = rates.balances.USD <= thresh

        if not acc_low and not low_keys:
            await self.send_message(
                chat_id,
                f"✅ *All Balances Healthy!*\n\n"
                f"• Master USD: `${rates.balances.USD:.4f}` (Threshold: `${thresh:.2f}`)\n"
                f"• All `{len(keys)}` active keys have sufficient remaining budget."
            )
            return

        lines = ["⚠️ *VENICE LOW BALANCE WARNINGS*", "━━━━━━━━━━━━━━━━━━"]
        if acc_low:
            lines.append(f"• 🚨 *Master Account:* `${rates.balances.USD:.4f}` USD remaining (Under `${thresh:.2f}`)")
        for k in low_keys:
            rem = f"${k.remaining_usd:.4f}" if k.remaining_usd is not None else "--"
            lim = f"${k.consumptionLimits.usd:.2f}" if k.consumptionLimits else "--"
            lines.append(f"• ⚠️ `{k.description or k.id}`: `{rem}` left / `{lim}` budget [{k.category}]")

        lines.append("\nTop up your balance on Venice or rotate/cycle saturated keys.")
        await self.send_message(chat_id, "\n".join(lines))

    async def handle_dashboard_link(self, chat_id: int):
        msg = (
            "🌐 *Venice Key Manager Control Plane*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Dashboard URL: `http://localhost:8660`\n\n"
            "• Live SSE spending graphs & epoch countdowns\n"
            "• Category grouping & per-key alert thresholds\n"
            "• 1-Click key rotation & clipboard copying\n"
            "• Downloadable settings & state backups"
        )
        await self.send_message(chat_id, msg)

    # =========================================================================
    # Polling Loop
    # =========================================================================

    async def poll_once(self):
        payload = {"offset": self.offset, "timeout": 20}
        data = await self._api("getUpdates", payload)
        if not data.get("ok"):
            return

        for update in data.get("result", []):
            self.offset = update["update_id"] + 1

            # Handle Callback Queries (inline buttons)
            if "callback_query" in update:
                cq = update["callback_query"]
                user_id = cq["from"]["id"]
                chat_id = cq["message"]["chat"]["id"]
                cdata = cq.get("data", "")

                await self._api("answerCallbackQuery", {"callback_query_id": cq["id"]})

                if not self.is_user_allowed(user_id):
                    await self.send_message(chat_id, "🚫 Unauthorized. Your user ID is not allowlisted.")
                    continue

                if cdata == "start_mint":
                    await self.handle_start_mint_flow(chat_id, user_id)
                elif cdata.startswith("mint_budget:"):
                    b_str = cdata.split(":", 1)[1]
                    b_val = float(b_str) if b_str != "none" else None
                    await self.handle_mint_complete(chat_id, user_id, b_val)
                elif cdata == "start_cycle":
                    await self.handle_cycle_prompt(chat_id)
                elif cdata.startswith("do_cycle:"):
                    kid = cdata.split(":", 1)[1]
                    await self.handle_execute_cycle(chat_id, kid)

            # Handle Regular Messages
            elif "message" in update:
                msg = update["message"]
                user_id = msg.get("from", {}).get("id")
                chat_id = msg["chat"]["id"]
                text = (msg.get("text") or "").strip()

                if not self.is_user_allowed(user_id):
                    await self.send_message(chat_id, "🚫 Unauthorized. Your user ID is not allowlisted.")
                    continue

                # Check if user is in an interactive multi-step flow
                if user_id in self.user_state:
                    state = self.user_state[user_id]
                    if state.get("action") == "awaiting_mint_name":
                        await self.handle_mint_name_input(chat_id, user_id, text)
                        continue

                # Commands & Button text matches
                if text in ("/start", "/help"):
                    await self.handle_start(chat_id)
                elif text in ("/balance", "📊 Account & Balance"):
                    await self.handle_balance(chat_id)
                elif text in ("/keys", "🔑 List Keys"):
                    await self.handle_list_keys(chat_id)
                elif text in ("/report", "📋 Daily Keys Report"):
                    await self.handle_daily_report(chat_id)
                elif text in ("/alert", "⚠️ Low Balance Alert"):
                    await self.handle_low_balance_alert(chat_id)
                elif text in ("/create", "➕ Mint Key"):
                    await self.handle_start_mint_flow(chat_id, user_id)
                elif text in ("/cycle", "🔄 Cycle Key"):
                    await self.handle_cycle_prompt(chat_id)
                elif text in ("/test", "⚡ Quick Test"):
                    await self.handle_quick_test(chat_id)
                elif text in ("/models", "🔒 E2EE Models"):
                    await self.handle_e2ee_models(chat_id)
                elif text in ("/dashboard", "🌐 Web Dashboard"):
                    await self.handle_dashboard_link(chat_id)
                elif text in ("/backup", "💾 Download Backup"):
                    backup = state_store.export_backup()
                    await self.send_message(chat_id, f"💾 *Backup JSON:*\n```json\n{backup.model_dump_json(indent=2)}\n```")
                elif text.startswith("/create "):
                    parts = text.split()
                    name = parts[1]
                    limit = float(parts[2]) if len(parts) > 2 else 0.50
                    self.user_state[user_id] = {"name": name}
                    await self.handle_mint_complete(chat_id, user_id, limit)
                elif text.startswith("/cycle "):
                    kid = text.split()[1]
                    await self.handle_execute_cycle(chat_id, kid)

    async def run(self):
        logger.info("Venice Telegram Bot starting...")
        self.running = True
        while self.running:
            try:
                await self.poll_once()
            except Exception as e:
                logger.error(f"Telegram polling exception: {e}")
                await asyncio.sleep(5)


def run_bot():
    bot = VeniceTelegramBot()
    asyncio.run(bot.run())


if __name__ == "__main__":
    run_bot()

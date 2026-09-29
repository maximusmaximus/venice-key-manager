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


def clean_html(s: Any) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
                [{"text": "🎟️ Batch Codes"}, {"text": "⚡ Quick Test"}],
                [{"text": "🌐 Web Dashboard"}, {"text": "🔒 E2EE Models"}],
                [{"text": "💾 Download Backup"}],
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
        parse_mode: str = "HTML",
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
            "⚡ <b>VENICE KEY MANAGER — CONTROL PLANE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Enterprise key management, spend telemetry, and confidential enclave model orchestration for Venice.ai.\n\n"
            "📋 <b>OPERATIONAL TOUCH CONTROLS</b>\n"
            "─────────────────────────────────────\n"
            "• 📊 <b>/balance</b> — Live USD &amp; DIEM treasury\n"
            "• 🔑 <b>/keys</b> — Active key inventory &amp; limits\n"
            "• 📋 <b>/report</b> — Comprehensive operations report\n"
            "• ⚠️ <b>/alert</b> — Low balance keys &amp; budget alerts\n"
            "• ➕ <b>/create</b> — Mint dedicated key with budget\n"
            "• 🔄 <b>/cycle</b> — Rotate/replace active key\n"
            "• 🎟️ <b>/batchcodes</b> [prefix] [count] — Batch web pairing codes\n"
            "• 📦 <b>/batchkeys</b> [prefix] [count] — Batch Venice API keys\n"
            "• ⚡ <b>/test</b> — Lightweight inference benchmark\n"
            "• 🔒 <b>/models</b> — Confidential E2EE enclave models\n"
            "• 🌐 <b>/dashboard</b> — Web control plane magic link\n"
            "• 💾 <b>/backup</b> — Download state JSON backup\n\n"
            "🌐 <b>Web Control Plane:</b> <code>http://localhost:8660</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        await self.send_message(chat_id, msg, parse_mode="HTML")

    async def handle_balance(self, chat_id: int):
        try:
            rates = await self.client.get_rate_limits()
            thresh = state_store.get_global_threshold()
            usd = rates.balances.USD
            diem = rates.balances.DIEM
            credits_val = rates.balances.BUNDLED_CREDITS
            is_low = usd <= thresh
            status_icon = "🟢" if rates.accessPermitted else "🔴"
            status_text = "Active &amp; Permitted" if rates.accessPermitted else "Restricted / Inactive"
            alert_str = " ⚠️ <b>[CRITICAL LOW]</b>" if is_low else ""

            msg = (
                "📊 <b>VENICE.AI TREASURY &amp; RATE LIMITS</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "💰 <b>FINANCIAL HEALTH &amp; BALANCES</b>\n"
                "─────────────────────────────────────\n"
                f"  💵 <b>Master USD Balance:</b>        <code>${usd:.4f} USD</code>{alert_str}\n"
                f"  💎 <b>DIEM Token Balance:</b>        <code>{diem:.4f} DIEM</code>\n"
                f"  🎟️ <b>Bundled Compute Credits:</b>   <code>{credits_val:.4f}</code>\n\n"
                "⚙️ <b>SYSTEM PERMISSIONS &amp; TIER</b>\n"
                "─────────────────────────────────────\n"
                f"  {status_icon} <b>Access Status:</b>          <code>{status_text}</code>\n"
                f"  🏷️ <b>Subscription Tier:</b>         <code>{rates.apiTier.id if rates.apiTier else 'paid'}</code>\n"
                f"  ⏳ <b>Next Epoch Reset:</b>          <code>{clean_html(rates.nextEpochBegins or '00:00 UTC')}</code>\n"
                f"  🛡️ <b>Warning Threshold:</b>         <code>${thresh:.2f} USD</code>\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            await self.send_message(chat_id, msg, parse_mode="HTML")
        except Exception as e:
            await self.send_message(chat_id, f"❌ Failed to query balance: {clean_html(e)}")

    async def handle_list_keys(self, chat_id: int):
        try:
            keys = await self.client.list_keys()
            if not keys:
                await self.send_message(chat_id, "ℹ️ No API keys found in this Venice account.")
                return

            low_keys = [k for k in keys if k.is_low_balance]
            lines = [
                "🔑 <b>VENICE.AI PROVISIONED API KEYS</b>",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"📊 <b>Fleet Inventory:</b> <code>{len(keys)} active keys</code>" + (f" (⚠️ <b>{len(low_keys)} low balance</b>)" if low_keys else " (🟢 <i>All healthy</i>)"),
                "",
                "🏷️ <b>KEY INVENTORY &amp; CONSUMPTION CEILINGS</b>",
                "─────────────────────────────────────",
            ]

            for idx, k in enumerate(keys[:10], 1):
                name = clean_html(k.description or "Unnamed")
                last6 = clean_html(k.last6Chars or "••••••")
                cat = clean_html(k.category)
                spent = float(k.currentPeriodUsage.usd or 0)
                limit = f"${k.consumptionLimits.usd:.2f}" if (k.consumptionLimits and k.consumptionLimits.usd is not None) else "Unlimited"
                rem = f"${k.remaining_usd:.4f}" if k.remaining_usd is not None else "--"
                status_badge = "🚨 <b>LOW</b>" if k.is_low_balance else "🟢"

                lines.append(f"{idx}️⃣ <b>{name}</b> (<code>...{last6}</code>) · [<code>{cat}</code>]")
                lines.append(f"   ↳ {status_badge} Remaining: <code>{rem}</code> / <code>{limit}</code> ({k.limitPeriod})")
                lines.append("")

            if len(keys) > 10:
                lines.append(f"ℹ️ <i>Showing 10 of {len(keys)} keys. View full fleet in Web Dashboard.</i>\n")

            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

            inline_kb = {
                "inline_keyboard": [
                    [{"text": "➕ Mint New Key", "callback_data": "start_mint"}],
                    [{"text": "🔄 Rotate / Cycle Key", "callback_data": "start_cycle"}],
                ]
            }
            await self.send_message(chat_id, "\n".join(lines), reply_markup=inline_kb, parse_mode="HTML")
        except Exception as e:
            await self.send_message(chat_id, f"❌ Failed to list keys: {clean_html(e)}")

    async def handle_start_mint_flow(self, chat_id: int, user_id: int):
        self.user_state[user_id] = {"action": "awaiting_mint_name"}
        await self.send_message(
            chat_id,
            "➕ <b>Mint New Key: Step 1/2</b>\n\nPlease reply with the <b>name or description</b> for the new key (e.g. <code>agent-worker-1</code>):",
            parse_mode="HTML"
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
            f"➕ <b>Mint New Key: Step 2/2</b>\n\nKey Name: <b>{clean_html(name)}</b>\nSelect daily spending budget ceiling:",
            reply_markup=inline_kb,
            parse_mode="HTML"
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
            b_label = f"${budget_val:.2f} USD (daily)" if budget_val else "Unlimited"
            msg = (
                "🎉 <b>VENICE KEY SUCCESSFULLY MINTED</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "📋 <b>PROVISIONED KEY DETAILS</b>\n"
                "─────────────────────────────────────\n"
                f"  🏷️ <b>Description:</b>  <code>{clean_html(res.description)}</code>\n"
                f"  🆔 <b>Key ID:</b>       <code>{res.id}</code>\n"
                f"  💵 <b>Spend Limit:</b>  <code>{b_label}</code>\n"
                f"  📁 <b>Category:</b>     <code>{clean_html(res.category)}</code>\n\n"
                "🔑 <b>API SECRET TOKEN</b>\n"
                "─────────────────────────────────────\n"
                f"<code>{res.apiKey}</code>\n\n"
                "⚠️ <b>Important:</b> <i>Copy and store this secret key now. It will never be displayed again.</i>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            await self.send_message(chat_id, msg, parse_mode="HTML")
        except Exception as e:
            await self.send_message(chat_id, f"❌ Key minting failed: {clean_html(e)}")

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
                "🔄 <b>Select Key to Rotate / Cycle:</b>\n\nThe previous key will be revoked and replaced with a fresh token having matching budget limits.",
                reply_markup=inline_kb,
                parse_mode="HTML"
            )
        except Exception as e:
            await self.send_message(chat_id, f"❌ Error: {clean_html(e)}")

    async def handle_execute_cycle(self, chat_id: int, key_id: str):
        req = KeyCycleRequest(id=key_id, revoke_old=True)
        try:
            new_key_resp, revoked = await self.client.cycle_key(req)
            msg = (
                "🔄 <b>VENICE KEY ROTATED SUCCESSFULLY</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "📋 <b>ROTATION AUDIT DETAILS</b>\n"
                "─────────────────────────────────────\n"
                f"  🏷️ <b>Name:</b>             <code>{clean_html(new_key_resp.description)}</code>\n"
                f"  🆔 <b>New Key ID:</b>       <code>{new_key_resp.id}</code>\n"
                f"  🗑️ <b>Previous Key:</b>      <code>{'Revoked' if revoked else 'Active'}</code>\n\n"
                "🔑 <b>NEW API SECRET TOKEN</b>\n"
                "─────────────────────────────────────\n"
                f"<code>{new_key_resp.apiKey}</code>\n\n"
                "⚠️ <i>Update dependent agents and microservices with the new token immediately.</i>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            await self.send_message(chat_id, msg, parse_mode="HTML")
        except Exception as e:
            await self.send_message(chat_id, f"❌ Key rotation failed: {clean_html(e)}")

    async def handle_quick_test(self, chat_id: int):
        await self.send_message(chat_id, "⚡ Running test inference against Venice <code>deepseek-v4-flash</code>...", parse_mode="HTML")
        res = await self.client.test_inference(
            prompt="Respond with 'Venice API connection verified' in 4 words.",
            model="deepseek-v4-flash"
        )
        status_icon = "🟢" if res.success else "🔴"
        status_word = "SUCCESS" if res.success else "FAILED"
        out_text = clean_html(res.output.strip() if res.success else (res.error or "Unknown error"))
        
        msg = (
            "⚡ <b>VENICE INFERENCE BENCHMARK TEST</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{status_icon} <b>STATUS &amp; PERFORMANCE</b>\n"
            "─────────────────────────────────────\n"
            f"  • <b>Status:</b>       <code>{status_word}</code>\n"
            f"  • <b>Model:</b>        <code>{res.model}</code>\n"
            f"  • <b>Latency:</b>      <code>{res.latency_ms} ms</code>\n"
            f"  • <b>Total Tokens:</b> <code>{res.total_tokens}</code> (Prompt: <code>{res.prompt_tokens}</code> | Completion: <code>{res.completion_tokens}</code>)\n\n"
            "💬 <b>COMPLETION OUTPUT</b>\n"
            "─────────────────────────────────────\n"
            f"<code>{out_text}</code>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        await self.send_message(chat_id, msg, parse_mode="HTML")

    async def handle_e2ee_models(self, chat_id: int):
        models = await self.client.list_models()
        e2ee = [m for m in models if m.privacy == "e2ee"]

        lines = [
            f"🔒 <b>VENICE CONFIDENTIAL HARDWARE ENCLAVE MODELS ({len(e2ee)} TOTAL)</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "Hardware-isolated TEE models with zero logging &amp; confidential inference.",
            "",
            "🛡️ <b>CONFIDENTIAL MODEL INVENTORY</b>",
            "─────────────────────────────────────",
        ]
        for m in e2ee[:8]:
            in_p = f"${m.pricing.input.get('usd', 0):.2f}" if (m.pricing and m.pricing.input) else "--"
            out_p = f"${m.pricing.output.get('usd', 0):.2f}" if (m.pricing and m.pricing.output) else "--"
            m_name = clean_html(m.name)
            lines.append(f"• <b>{m_name}</b>")
            lines.append(f"  ↳ ID: <code>{m.id}</code>")
            lines.append(f"  ↳ Pricing: <code>{in_p}</code> in / <code>{out_p}</code> out per 1M tokens")
            lines.append("")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        await self.send_message(chat_id, "\n".join(lines), parse_mode="HTML")

    async def handle_daily_report(self, chat_id: int):
        await self.send_message(chat_id, "⏳ Generating real-time Venice keys operations report...")
        reporter = DailyKeyReport(client=self.client)
        data = await reporter.generate_report_data()
        msg = reporter.format_telegram_html(data)
        await self.send_message(chat_id, msg, parse_mode="HTML")

    async def handle_low_balance_alert(self, chat_id: int):
        rates = await self.client.get_rate_limits()
        keys = await self.client.list_keys()
        thresh = state_store.get_global_threshold()
        low_keys = [k for k in keys if k.is_low_balance]
        acc_low = rates.balances.USD <= thresh

        if not acc_low and not low_keys:
            msg = (
                "⚠️ <b>VENICE BUDGET &amp; LOW BALANCE ALERTS</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "✅ <b>ALL BALANCES HEALTHY</b>\n"
                "─────────────────────────────────────\n"
                f"  💵 <b>Master USD:</b> <code>${rates.balances.USD:.4f} USD</code> (Threshold: <code>${thresh:.2f}</code>)\n"
                f"  🔑 <b>Key Fleet:</b> All <code>{len(keys)}</code> active keys operating above warning threshold.\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            await self.send_message(chat_id, msg, parse_mode="HTML")
            return

        lines = [
            "⚠️ <b>VENICE BUDGET &amp; LOW BALANCE ALERTS</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "🚨 <b>CRITICAL BUDGET WARNINGS</b>",
            "─────────────────────────────────────",
        ]
        if acc_low:
            lines.append(f"  🚨 <b>Master Treasury:</b> <code>${rates.balances.USD:.4f} USD</code> remaining (Under <code>${thresh:.2f}</code> threshold)")
            lines.append("")

        for k in low_keys:
            rem = f"${k.remaining_usd:.4f}" if k.remaining_usd is not None else "--"
            lim = f"${k.consumptionLimits.usd:.2f}" if (k.consumptionLimits and k.consumptionLimits.usd is not None) else "Unlimited"
            k_name = clean_html(k.description or k.id)
            k_cat = clean_html(k.category)
            lines.append(f"  ⚠️ <b>{k_name}</b> (<code>...{k.last6Chars}</code>) · [<code>{k_cat}</code>]")
            lines.append(f"     ↳ Remaining: <code>{rem}</code> / <code>{lim}</code> ceiling")
            lines.append("")

        lines.append("💡 <i>Action Required: Top up account or rotate/cycle saturated keys.</i>")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        await self.send_message(chat_id, "\n".join(lines), parse_mode="HTML")

    async def handle_dashboard_link(self, chat_id: int):
        token = state_store.create_auth_token(created_by=f"telegram:{chat_id}")
        base_url = config.dashboard_base_url.rstrip("/")
        magic_url = f"{base_url}/?token={token}"

        msg = (
            "🌐 <b>VENICE CONTROL PLANE — WEB DASHBOARD</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "The web dashboard is protected behind an authentication gate.\n"
            "Your cryptographic session token has been minted below:\n\n"
            "🔗 <b>SINGLE-CLICK MAGIC LINK</b>\n"
            "─────────────────────────────────────\n"
            f"<a href=\"{magic_url}\">{magic_url}</a>\n\n"
            "🔑 <b>PASTEABLE ACCESS KEY</b>\n"
            "─────────────────────────────────────\n"
            f"<code>{token}</code>\n\n"
            "⏱️ <b>Session Validity:</b> <code>7 days</code>\n"
            "🔒 <i>Tap the magic link to unlock your real-time control plane automatically.</i>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        await self.send_message(chat_id, msg, parse_mode="HTML")

    async def handle_batch_codes(self, chat_id: int, prefix: str = "vkm_code_", count: int = 3):
        try:
            tokens = state_store.create_batch_auth_tokens(
                prefix=prefix,
                count=count,
                created_by=f"telegram:{chat_id}",
                ttl_hours=168,
                notes=f"Telegram Batch ({prefix})"
            )
            base_url = config.dashboard_base_url.rstrip("/")
            lines = [
                "🎟️ <b>BATCH ACCESS CODES CREATED</b>",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"🏷️ <b>Prefix:</b> <code>{clean_html(prefix)}</code>",
                f"🔢 <b>Count:</b> <code>{len(tokens)} codes</code>",
                f"⏳ <b>Validity:</b> <code>7 Days (168h)</code>",
                "",
                "🔑 <b>GENERATED CODES &amp; MAGIC LINKS</b>",
                "─────────────────────────────────────",
            ]
            for idx, item in enumerate(tokens, 1):
                tok = item["token"]
                url = f"{base_url}/?token={tok}"
                lines.append(f"<b>Code #{idx}:</b> <code>{clean_html(tok)}</code>")
                lines.append(f"🔗 <a href=\"{url}\">Single-Click Magic Link #{idx}</a>\n")

            lines.append("💡 <i>Recipients can open the link directly or paste the code on the web lock screen.</i>")
            lines.append(f"ℹ️ <i>To generate more with a custom prefix:</i> <code>/batchcodes &lt;prefix&gt; [count]</code>")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            await self.send_message(chat_id, "\n".join(lines), parse_mode="HTML")
        except Exception as e:
            await self.send_message(chat_id, f"❌ Failed to generate batch codes: {clean_html(e)}")

    async def handle_batch_keys(self, chat_id: int, prefix: str = "agent-", count: int = 3, daily_usd: float = 0.50):
        try:
            keys = await self.client.create_batch_keys(
                prefix=prefix,
                count=count,
                daily_usd=daily_usd,
                category="Agents",
            )
            lines = [
                "🔑 <b>BATCH VENICE API KEYS MINTED</b>",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"🏷️ <b>Prefix:</b> <code>{clean_html(prefix)}</code>",
                f"🔢 <b>Count:</b> <code>{len(keys)} keys</code>",
                f"💵 <b>Budget Cap:</b> <code>${daily_usd:.2f}/day each</code>",
                "",
                "⚠️ <b>SECRET KEYS (SHOWN ONLY ONCE)</b>",
                "─────────────────────────────────────",
            ]
            for idx, k in enumerate(keys, 1):
                lines.append(f"<b>#{idx} — {clean_html(k.description)}:</b>")
                lines.append(f"<code>{clean_html(k.apiKey)}</code>")
                lines.append(f"ID: <code>{clean_html(k.id)}</code>\n")

            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            await self.send_message(chat_id, "\n".join(lines), parse_mode="HTML")
        except Exception as e:
            await self.send_message(chat_id, f"❌ Failed to mint batch keys: {clean_html(e)}")

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
                elif text in ("/batchcodes", "🎟️ Batch Codes"):
                    await self.handle_batch_codes(chat_id, prefix="vkm_code_", count=3)
                elif text.startswith(("/batchcodes ", "/batchcode ", "/batch ")):
                    parts = text.split()
                    pfx = parts[1] if len(parts) > 1 else "vkm_code_"
                    cnt = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 3
                    await self.handle_batch_codes(chat_id, prefix=pfx, count=cnt)
                elif text.startswith(("/batchkeys ", "/batchkey ")):
                    parts = text.split()
                    pfx = parts[1] if len(parts) > 1 else "agent-"
                    cnt = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 3
                    usd = float(parts[3]) if len(parts) > 3 else 0.50
                    await self.handle_batch_keys(chat_id, prefix=pfx, count=cnt, daily_usd=usd)
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

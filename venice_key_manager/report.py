import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
import httpx

from .client import VeniceClient
from .state import state_store
from .config import config

logger = logging.getLogger(__name__)


class DailyKeyReport:
    def __init__(self, client: Optional[VeniceClient] = None):
        self.client = client or VeniceClient()

    async def generate_report_data(self) -> Dict[str, Any]:
        """Aggregate real-time metrics across all Venice keys and account balances."""
        rates = await self.client.get_rate_limits()
        keys = await self.client.list_keys()
        global_thresh = state_store.get_global_threshold()

        total_keys = len(keys)
        low_balance_keys = [k for k in keys if k.is_low_balance]

        total_spend_today = sum(
            float(k.currentPeriodUsage.usd or 0) for k in keys if k.currentPeriodUsage
        )
        total_spend_7d = sum(
            float(k.usage.trailingSevenDays.usd or 0)
            for k in keys
            if k.usage and k.usage.trailingSevenDays and k.usage.trailingSevenDays.usd
        )

        # Category breakdown
        categories = state_store.get_categories()
        category_stats: Dict[str, Dict[str, Any]] = {}
        for cat in categories:
            cat_keys = [k for k in keys if k.category.lower() == cat.lower()]
            cat_spend = sum(float(k.currentPeriodUsage.usd or 0) for k in cat_keys if k.currentPeriodUsage)
            cat_low = sum(1 for k in cat_keys if k.is_low_balance)
            category_stats[cat] = {
                "key_count": len(cat_keys),
                "total_period_spend": round(cat_spend, 4),
                "low_balance_count": cat_low,
            }

        # Top consumers today
        sorted_by_spend = sorted(
            keys,
            key=lambda k: float(k.currentPeriodUsage.usd or 0) if k.currentPeriodUsage else 0.0,
            reverse=True
        )
        top_consumers = []
        for k in sorted_by_spend[:5]:
            spend = float(k.currentPeriodUsage.usd or 0) if k.currentPeriodUsage else 0.0
            if spend > 0 or k == sorted_by_spend[0]:
                limit_str = f"${k.consumptionLimits.usd:.2f}" if (k.consumptionLimits and k.consumptionLimits.usd is not None) else "Unlimited"
                top_consumers.append({
                    "name": k.description or "Unnamed",
                    "id": k.id,
                    "last6": k.last6Chars or "••••••",
                    "category": k.category,
                    "spend": round(spend, 4),
                    "limit": limit_str,
                    "period": k.limitPeriod,
                    "remaining": round(k.remaining_usd, 4) if k.remaining_usd is not None else None,
                    "is_low": k.is_low_balance
                })

        usd_balance = rates.balances.USD
        is_account_low = usd_balance <= global_thresh
        bundled_credits = float(getattr(rates.balances, "BUNDLED_CREDITS", 0.0) or 0.0)
        now_dt = datetime.utcnow()

        # Generate authenticated dashboard link for this report
        token = state_store.create_auth_token(created_by="daily_report")
        base_url = config.dashboard_base_url.rstrip("/")
        magic_url = f"{base_url}/?token={token}"

        return {
            "timestamp": now_dt.isoformat() + "Z",
            "timestamp_human": now_dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "account": {
                "balance_usd": round(usd_balance, 4),
                "balance_diem": round(rates.balances.DIEM, 4),
                "bundled_credits": round(bundled_credits, 4),
                "access_permitted": rates.accessPermitted,
                "is_low_balance": is_account_low,
                "global_threshold": global_thresh,
                "next_epoch_begins": rates.nextEpochBegins,
                "api_tier": rates.apiTier.id if rates.apiTier else "paid",
            },
            "control_plane": {
                "base_url": base_url,
                "magic_url": magic_url,
                "token": token,
            },
            "summary": {
                "total_keys": total_keys,
                "low_balance_keys_count": len(low_balance_keys),
                "total_spend_today": round(total_spend_today, 4),
                "total_spend_7d": round(total_spend_7d, 4),
            },
            "categories": category_stats,
            "top_consumers": top_consumers,
            "low_keys": [
                {
                    "name": k.description or "Unnamed",
                    "id": k.id,
                    "last6": k.last6Chars,
                    "remaining": round(k.remaining_usd, 4) if k.remaining_usd is not None else None,
                    "limit": k.consumptionLimits.usd if k.consumptionLimits else None,
                    "category": k.category
                }
                for k in low_balance_keys[:8]
            ]
        }

    def format_markdown(self, data: Dict[str, Any]) -> str:
        """Format aggregated metrics with organizational practices, emoji headlines, and clean spacing."""
        acc = data["account"]
        summary = data["summary"]
        status_icon = "🟢" if acc["access_permitted"] else "🔴"
        status_text = "Active & Permitted" if acc["access_permitted"] else "Restricted"
        overall_health = "🟢 HEALTHY" if not acc["is_low_balance"] and summary["low_balance_keys_count"] == 0 else "⚠️ ATTENTION REQUIRED"
        alert_flag = " ⚠️ [LOW BALANCE CRITICAL]" if acc["is_low_balance"] else ""
        gen_time = data.get("timestamp_human") or (data.get("timestamp", "")[:19].replace("T", " ") + " UTC")

        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]

        lines = [
            "📋 VENICE.AI DAILY KEY OPERATIONS & USAGE REPORT",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📅 Generated: {gen_time}  |  Status: {overall_health}",
            "",
            "💰 FINANCIAL HEALTH & TREASURY",
            "─────────────────────────────────────────────────",
            f"  💵 Master USD Balance:        ${acc['balance_usd']:.4f} USD{alert_flag}",
            f"  💎 DIEM Token Balance:        {acc['balance_diem']:.4f} DIEM",
            f"  🎟️ Bundled Compute Credits:   {acc.get('bundled_credits', 0.0):.4f}",
            f"  {status_icon} API Access Status:         {status_text}",
            f"  ⏳ Rate-Limit Epoch Reset:    {acc['next_epoch_begins'] or '00:00 UTC'}",
            f"  🛡️ Global Warning Threshold:  ${acc['global_threshold']:.2f} USD",
            "",
            "📊 FLEET CAPACITY & SPEND TELEMETRY",
            "─────────────────────────────────────────────────",
            f"  🔑 Total Provisioned Keys:    {summary['total_keys']} active keys",
            f"  📈 Spend Today (Current Epoch): ${summary['total_spend_today']:.4f} USD",
            f"  📉 7-Day Trailing Fleet Spend:  ${summary['total_spend_7d']:.4f} USD",
            f"  🛡️ Keys Under Warning Ceiling:  {summary['low_balance_keys_count']} keys",
            "",
            "📁 SPEND ALLOCATION BY CATEGORY",
            "─────────────────────────────────────────────────",
        ]

        active_categories = {k: v for k, v in data["categories"].items() if v["key_count"] > 0}
        if active_categories:
            for cat, cdata in active_categories.items():
                low_warn = f" (⚠️ {cdata['low_balance_count']} low)" if cdata["low_balance_count"] > 0 else ""
                k_label = "key" if cdata["key_count"] == 1 else "keys"
                lines.append(f"  🏷️ {cat:<12} · {cdata['key_count']:>2} {k_label} · ${cdata['total_period_spend']:.4f} USD spent today{low_warn}")
        else:
            lines.append("  • No active category keys found.")

        lines.append("")
        lines.append("🔝 TOP CONSUMING KEYS (TODAY)")
        lines.append("─────────────────────────────────────────────────")
        if data["top_consumers"]:
            for idx, tc in enumerate(data["top_consumers"][:5]):
                medal = medals[idx] if idx < len(medals) else f"{idx + 1}️⃣"
                rem_str = f" | Remaining: ${tc['remaining']:.4f}" if tc['remaining'] is not None else ""
                period_str = f" ({tc.get('period', 'EPOCH')})" if tc.get('period') else ""
                lines.append(f"  {medal} {tc['name']} (...{tc['last6']}) · [{tc['category']}]")
                lines.append(f"     ↳ Spend: ${tc['spend']:.4f} / {tc['limit']}{rem_str}{period_str}")
        else:
            lines.append("  • No billable key usage recorded during current epoch.")

        lines.append("")
        lines.append("⚠️ KEY BUDGET & THRESHOLD ALERTS")
        lines.append("─────────────────────────────────────────────────")
        if data["low_keys"]:
            for lk in data["low_keys"]:
                rem = f"${lk['remaining']:.4f}" if lk['remaining'] is not None else "--"
                lim = f"${lk['limit']:.2f}" if isinstance(lk['limit'], (int, float)) else str(lk['limit'])
                lines.append(f"  🚨 {lk['name']} (...{lk['last6']}) · [{lk['category']}]")
                lines.append(f"     ↳ Remaining: {rem} (Ceiling: {lim})")
        else:
            lines.append("  ✅ All active keys are healthy and operating above warning thresholds.")

        lines.append("")
        lines.append("🌐 OPERATIONS & CONTROL PLANE")
        lines.append("─────────────────────────────────────────────────")
        cp = data.get("control_plane", {})
        web_link = cp.get("magic_url") or f"{config.dashboard_base_url.rstrip('/')}/"
        token_str = cp.get("token")
        lines.append(f"  🖥️ Web Link:        {web_link}")
        if token_str:
            lines.append(f"  🔑 Access Key:     {token_str}")
        lines.append("  🤖 Telegram Bot:   @v3n15_bot")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        return "\n".join(lines)

    def format_telegram_html(self, data: Dict[str, Any]) -> str:
        """Format aggregated metrics into Telegram-safe HTML with emojis, spacing, and organizational practices."""
        acc = data["account"]
        summary = data["summary"]
        status_icon = "🟢" if acc["access_permitted"] else "🔴"
        status_text = "Active &amp; Permitted" if acc["access_permitted"] else "Restricted"
        overall_health = "🟢 <b>HEALTHY</b>" if not acc["is_low_balance"] and summary["low_balance_keys_count"] == 0 else "⚠️ <b>ATTENTION REQUIRED</b>"
        alert_flag = " ⚠️ <b>[LOW BALANCE CRITICAL]</b>" if acc["is_low_balance"] else ""
        gen_time = data.get("timestamp_human") or (data.get("timestamp", "")[:19].replace("T", " ") + " UTC")

        def clean(s: Any) -> str:
            return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]

        lines = [
            "📋 <b>VENICE.AI DAILY KEY OPERATIONS &amp; USAGE REPORT</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📅 <code>{clean(gen_time)}</code> | Status: {overall_health}",
            "",
            "💰 <b>FINANCIAL HEALTH &amp; TREASURY</b>",
            "─────────────────────────────────────",
            f"  💵 <b>Master USD Balance:</b>        <code>${acc['balance_usd']:.4f} USD</code>{alert_flag}",
            f"  💎 <b>DIEM Token Balance:</b>        <code>{acc['balance_diem']:.4f} DIEM</code>",
            f"  🎟️ <b>Bundled Credits:</b>           <code>{acc.get('bundled_credits', 0.0):.4f}</code>",
            f"  {status_icon} <b>API Access Status:</b>         <code>{status_text}</code>",
            f"  ⏳ <b>Rate-Limit Epoch Reset:</b>   <code>{clean(acc['next_epoch_begins'] or '00:00 UTC')}</code>",
            f"  🛡️ <b>Warning Threshold:</b>        <code>${acc['global_threshold']:.2f} USD</code>",
            "",
            "📊 <b>FLEET CAPACITY &amp; SPEND TELEMETRY</b>",
            "─────────────────────────────────────",
            f"  🔑 <b>Total Provisioned Keys:</b>    <code>{summary['total_keys']} active keys</code>",
            f"  📈 <b>Spend Today (Current Epoch):</b> <code>${summary['total_spend_today']:.4f} USD</code>",
            f"  📉 <b>7-Day Trailing Fleet Spend:</b>  <code>${summary['total_spend_7d']:.4f} USD</code>",
            f"  🛡️ <b>Keys Under Warning Ceiling:</b>  <code>{summary['low_balance_keys_count']} keys</code>",
            "",
            "📁 <b>SPEND ALLOCATION BY CATEGORY</b>",
            "─────────────────────────────────────",
        ]

        active_categories = {k: v for k, v in data["categories"].items() if v["key_count"] > 0}
        if active_categories:
            for cat, cdata in active_categories.items():
                low_warn = f" (⚠️ <b>{cdata['low_balance_count']} low</b>)" if cdata["low_balance_count"] > 0 else ""
                k_label = "key" if cdata["key_count"] == 1 else "keys"
                lines.append(f"  🏷️ <b>{clean(cat)}:</b> <code>{cdata['key_count']} {k_label}</code> · <code>${cdata['total_period_spend']:.4f} USD</code>{low_warn}")
        else:
            lines.append("  • <i>No active category keys found.</i>")

        lines.append("")
        lines.append("🔝 <b>TOP CONSUMING KEYS (TODAY)</b>")
        lines.append("─────────────────────────────────────")
        if data["top_consumers"]:
            for idx, tc in enumerate(data["top_consumers"][:5]):
                medal = medals[idx] if idx < len(medals) else f"{idx + 1}️⃣"
                rem_str = f" | Remaining: <code>${tc['remaining']:.4f}</code>" if tc['remaining'] is not None else ""
                period_str = f" ({clean(tc.get('period', 'EPOCH'))})" if tc.get('period') else ""
                lines.append(f"  {medal} <b>{clean(tc['name'])}</b> (<code>...{clean(tc['last6'])}</code>) · [<code>{clean(tc['category'])}</code>]")
                lines.append(f"     ↳ Spend: <code>${tc['spend']:.4f}</code> / <code>{clean(tc['limit'])}</code>{rem_str}{period_str}")
        else:
            lines.append("  • <i>No billable key usage recorded during current epoch.</i>")

        lines.append("")
        lines.append("⚠️ <b>KEY BUDGET &amp; THRESHOLD ALERTS</b>")
        lines.append("─────────────────────────────────────")
        if data["low_keys"]:
            for lk in data["low_keys"]:
                rem = f"${lk['remaining']:.4f}" if lk['remaining'] is not None else "--"
                lim = f"${lk['limit']:.2f}" if isinstance(lk['limit'], (int, float)) else str(lk['limit'])
                lines.append(f"  🚨 <b>{clean(lk['name'])}</b> (<code>...{clean(lk['last6'])}</code>) · [<code>{clean(lk['category'])}</code>]")
                lines.append(f"     ↳ Remaining: <code>{rem}</code> (Ceiling: <code>{lim}</code>)")
        else:
            lines.append("  ✅ <i>All active keys are healthy and operating above warning thresholds.</i>")

        lines.append("")
        lines.append("🌐 <b>OPERATIONS &amp; CONTROL PLANE</b>")
        lines.append("─────────────────────────────────────")
        cp = data.get("control_plane", {})
        web_link = cp.get("magic_url") or f"{config.dashboard_base_url.rstrip('/')}/"
        token_str = cp.get("token")
        lines.append(f'  🖥️ <b>Web Link:</b> <a href="{web_link}">{web_link}</a>')
        if token_str:
            lines.append(f"  🔑 <b>Access Key:</b> <code>{token_str}</code>")
        lines.append("  🤖 <b>Telegram Bot:</b> <code>@v3n15_bot</code>")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        return "\n".join(lines)

    async def send_to_telegram(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,
        reply_markup: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Generate and dispatch daily report directly to Telegram."""
        bot_token = token or config.telegram_bot_token
        target_chat = chat_id or (str(config.telegram_allowed_users[0]) if config.telegram_allowed_users else None)

        if not bot_token or not target_chat:
            logger.error("Missing Telegram bot token or chat ID for report delivery.")
            return False

        data = await self.generate_report_data()
        text = self.format_telegram_html(data)

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload: Dict[str, Any] = {
            "chat_id": target_chat,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        async with httpx.AsyncClient(timeout=20.0) as http:
            res = await http.post(url, json=payload)
            if res.status_code != 200:
                logger.error(f"Failed to send Telegram report: {res.text}")
                return False
            return True


async def run_daily_report(send_tg: bool = False, chat_id: Optional[str] = None) -> str:
    reporter = DailyKeyReport()
    data = await reporter.generate_report_data()
    md = reporter.format_markdown(data)
    if send_tg:
        await reporter.send_to_telegram(chat_id=chat_id)
    return md

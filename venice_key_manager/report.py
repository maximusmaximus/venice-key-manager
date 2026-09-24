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

        return {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "account": {
                "balance_usd": round(usd_balance, 4),
                "balance_diem": round(rates.balances.DIEM, 4),
                "access_permitted": rates.accessPermitted,
                "is_low_balance": is_account_low,
                "global_threshold": global_thresh,
                "next_epoch_begins": rates.nextEpochBegins,
                "api_tier": rates.apiTier.id if rates.apiTier else "paid",
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
        """Format aggregated metrics into a clean, human-readable Telegram / terminal card."""
        acc = data["account"]
        summary = data["summary"]
        status_icon = "🟢" if acc["access_permitted"] else "🔴"
        alert_flag = " ⚠️ *LOW BALANCE ALERT*" if acc["is_low_balance"] else ""

        lines = [
            "📋 *VENICE.AI DAILY KEY OPERATIONS & USAGE REPORT*",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"💰 *Account Financial Health:*",
            f"• *USD Balance:* `${acc['balance_usd']:.4f}`{alert_flag}",
            f"• *DIEM Balance:* `{acc['balance_diem']:.4f}`",
            f"• *Access Status:* {status_icon} `{'Active / Permitted' if acc['access_permitted'] else 'Restricted'}`",
            f"• *Reset Window:* `{acc['next_epoch_begins'] or '00:00 UTC'}`",
            "",
            f"📊 *Fleet Spend & Capacity Metrics:*",
            f"• *Total Active Keys:* `{summary['total_keys']}`",
            f"• *Spend Today (Current Period):* `${summary['total_spend_today']:.4f}`",
            f"• *7-Day Trailing Spend:* `${summary['total_spend_7d']:.4f}`",
            f"• *Keys Under Warning Ceiling:* `{summary['low_balance_keys_count']}` keys",
            "",
            "📁 *Category Spend Breakdown:*",
        ]

        for cat, cdata in data["categories"].items():
            if cdata["key_count"] > 0:
                warn_s = f" ({cdata['low_balance_count']} low)" if cdata["low_balance_count"] > 0 else ""
                lines.append(f"• *{cat}:* `{cdata['key_count']}` keys | `${cdata['total_period_spend']:.4f}` spent today{warn_s}")

        # Top Consumers
        if data["top_consumers"]:
            lines.append("")
            lines.append("🔝 *Top Consumers Today:*")
            for tc in data["top_consumers"]:
                rem_str = f" (${tc['remaining']} left)" if tc['remaining'] is not None else ""
                lines.append(f"• `{tc['name']}` (...{tc['last6']}) [{tc['category']}]: `${tc['spend']:.4f}` / {tc['limit']}{rem_str}")

        # Warning alerts
        if data["low_keys"]:
            lines.append("")
            lines.append("⚠️ *Keys Nearing Spend Ceiling (< threshold):*")
            for lk in data["low_keys"]:
                lines.append(f"• ⚠️ `{lk['name']}`: `${lk['remaining']:.4f}` remaining (Limit: ${lk['limit']})")

        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("🌐 *Web Control Plane:* `http://localhost:8660`")

        return "\n".join(lines)

    def format_telegram_html(self, data: Dict[str, Any]) -> str:
        """Format aggregated metrics into Telegram-safe HTML."""
        acc = data["account"]
        summary = data["summary"]
        status_icon = "🟢" if acc["access_permitted"] else "🔴"
        alert_flag = " ⚠️ <b>LOW BALANCE ALERT</b>" if acc["is_low_balance"] else ""

        def clean(s: Any) -> str:
            return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        lines = [
            "📋 <b>VENICE.AI DAILY KEY OPERATIONS & USAGE REPORT</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "💰 <b>Account Financial Health:</b>",
            f"• <b>USD Balance:</b> <code>${acc['balance_usd']:.4f}</code>{alert_flag}",
            f"• <b>DIEM Balance:</b> <code>{acc['balance_diem']:.4f}</code>",
            f"• <b>Access Status:</b> {status_icon} <code>{'Active / Permitted' if acc['access_permitted'] else 'Restricted'}</code>",
            f"• <b>Reset Window:</b> <code>{clean(acc['next_epoch_begins'] or '00:00 UTC')}</code>",
            "",
            "📊 <b>Fleet Spend & Capacity Metrics:</b>",
            f"• <b>Total Active Keys:</b> <code>{summary['total_keys']}</code>",
            f"• <b>Spend Today (Current Period):</b> <code>${summary['total_spend_today']:.4f}</code>",
            f"• <b>7-Day Trailing Spend:</b> <code>${summary['total_spend_7d']:.4f}</code>",
            f"• <b>Keys Under Warning Ceiling:</b> <code>{summary['low_balance_keys_count']}</code> keys",
            "",
            "📁 <b>Category Spend Breakdown:</b>",
        ]

        for cat, cdata in data["categories"].items():
            if cdata["key_count"] > 0:
                warn_s = f" ({cdata['low_balance_count']} low)" if cdata["low_balance_count"] > 0 else ""
                lines.append(f"• <b>{clean(cat)}:</b> <code>{cdata['key_count']}</code> keys | <code>${cdata['total_period_spend']:.4f}</code> spent today{warn_s}")

        if data["top_consumers"]:
            lines.append("")
            lines.append("🔝 <b>Top Consumers Today:</b>")
            for tc in data["top_consumers"]:
                rem_str = f" (${tc['remaining']} left)" if tc['remaining'] is not None else ""
                lines.append(f"• <code>{clean(tc['name'])}</code> (...{clean(tc['last6'])}) [{clean(tc['category'])}]: <code>${tc['spend']:.4f}</code> / {clean(tc['limit'])}{rem_str}")

        if data["low_keys"]:
            lines.append("")
            lines.append("⚠️ <b>Keys Nearing Spend Ceiling (&lt; threshold):</b>")
            for lk in data["low_keys"]:
                lines.append(f"• ⚠️ <code>{clean(lk['name'])}</code>: <code>${lk['remaining']:.4f}</code> remaining (Limit: ${lk['limit']})")

        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("🌐 <b>Web Control Plane:</b> <code>http://localhost:8660</code>")

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

"""
Agent Inference Usage & Service Resilience Reporter for Venice Key Manager.

Aggregates live Venice.ai balance & key spend telemetry, local per-agent inference
metrics, sub-keys, Cloud DNS allocation claims, anti-abuse guard quotas, and
24/7 supervisor/tunnel uptime into structured reports and scheduled Telegram updates.
"""

import re
import time
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

from core.vault import KeyVault
from core.venice_client import VeniceClient
from core.guard import KeyCreationGuard
from core.supervisor import ServiceSupervisor
from core.tunnel import CloudflareTunnelManager

logger = logging.getLogger("venice_reporter")

KNOWN_AGENT_SLUGS = (
    "v3n15pe_bot",
    "hermes-music",
    "a2a-node",
    "dawagent",
    "worker-audio",
)


def _safe_float(val: Any, default: float = 0.0) -> float:
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _format_uptime(seconds: float) -> str:
    sec = max(0, int(seconds or 0))
    days, rem = divmod(sec, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours}h {mins}m"
    if hours > 0:
        return f"{hours}h {mins}m"
    if mins > 0:
        return f"{mins}m {secs}s"
    return f"{secs}s"


def _mask_key_id_or_tail(key_obj: Dict[str, Any]) -> str:
    last6 = str(key_obj.get("last6Chars") or "").strip()
    if last6:
        return f"...{last6}"
    raw = str(key_obj.get("apiKey") or key_obj.get("api_key") or key_obj.get("key_string") or "").strip()
    if len(raw) >= 4:
        return f"...{raw[-4:]}"
    kid = str(key_obj.get("id") or "")
    return kid[-6:] if len(kid) > 6 else (kid or "••••")


class AgentInferenceReporter:
    """Builds per-agent inference usage and keep-alive health reports."""

    PRIMARY_AGENT = "v3n15PE_bot"

    def __init__(self, vault: Optional[KeyVault] = None, client: Optional[VeniceClient] = None):
        self.vault = vault if vault is not None else KeyVault()
        self._explicit_client = client
        self.guard = KeyCreationGuard(self.vault)

    def _get_read_only_client(self, caller_agent: str = PRIMARY_AGENT) -> VeniceClient:
        if self._explicit_client is not None:
            return self._explicit_client
        # Use global admin key if present, else scoped v3n15PE_bot admin key strictly for read-only telemetry
        admin_key = self.vault.get_venice_admin_key() or self.vault.get_venice_admin_key(caller_agent=caller_agent)
        return VeniceClient(
            admin_key=admin_key,
            inference_key=self.vault.get_venice_inference_key(),
            base_url=self.vault.data.get("venice", {}).get("base_url", ""),
        )

    @staticmethod
    def canonical_agent_name(raw_name: Optional[str], known_bots: Optional[Dict[str, Any]] = None) -> str:
        if not raw_name:
            return "shared"
        cleaned = str(raw_name).strip().lstrip("@")
        if not cleaned:
            return "shared"
        low = cleaned.lower()
        if low in ("v3n15pe_bot", "v3n15pe", "v3n15_bot"):
            return "v3n15PE_bot"
        if low in ("hermes", "hermes-music", "hermes_music"):
            return "hermes-music"
        if low in ("a2a", "a2a-node", "a2a_node"):
            return "a2a-node"
        if low in ("daw", "dawagent"):
            return "dawagent"
        if low in ("worker-audio", "worker_audio"):
            return "worker-audio"
        if known_bots:
            for k in known_bots.keys():
                if str(k).strip().lower().lstrip("@") == low:
                    return str(k).strip().lstrip("@")
        return cleaned

    def infer_agent_from_key(self, key_obj: Dict[str, Any], known_bots: Dict[str, Any]) -> str:
        explicit = key_obj.get("agent") or key_obj.get("assigned_agent") or key_obj.get("target_agent")
        if explicit:
            return self.canonical_agent_name(str(explicit), known_bots)

        desc = str(key_obj.get("description") or key_obj.get("label") or key_obj.get("name") or "").strip()
        desc_low = desc.lower()

        m = re.search(r"\[agent:\s*([^\]]+)\]", desc, re.IGNORECASE)
        if m:
            return self.canonical_agent_name(m.group(1), known_bots)

        # Match "<agent> (S Tier)" style descriptions created by provision_agent_key
        m_paren = re.match(r"^([a-zA-Z0-9_\-]+)\s*\((?:XS|S|M|L|XL)\s+Tier\)", desc, re.IGNORECASE)
        if m_paren:
            return self.canonical_agent_name(m_paren.group(1), known_bots)

        for slug in KNOWN_AGENT_SLUGS:
            if slug in desc_low:
                return self.canonical_agent_name(slug, known_bots)
        if "hermes" in desc_low:
            return "hermes-music"
        if "v3n15" in desc_low:
            return "v3n15PE_bot"

        for bot_agent in known_bots.keys():
            clean_bot = str(bot_agent).strip().lower().lstrip("@")
            if clean_bot and clean_bot in desc_low:
                return self.canonical_agent_name(bot_agent, known_bots)

        return "shared"

    def generate_report(
        self,
        caller_agent: str = PRIMARY_AGENT,
        include_service: bool = True
    ) -> Dict[str, Any]:
        """Generates a complete, secret-redacted agent inference & service report."""
        now_ts = time.time()
        now_dt = datetime.utcnow()
        now_iso = now_dt.isoformat() + "Z"
        now_human = now_dt.strftime("%Y-%m-%d %H:%M:%S UTC")

        client = self._get_read_only_client(caller_agent=caller_agent)
        thresholds = self.vault.get_balance_thresholds()
        low_usd = thresholds.get("low_usd", 1.0)
        out_usd = thresholds.get("out_usd", 0.05)

        bal_status = client.check_balance_status(
            low_threshold_usd=low_usd,
            out_threshold_usd=out_usd,
        )
        rate_res = client.get_rate_limits()
        next_epoch = rate_res.get("nextEpochBegins") if rate_res.get("success") else None
        access_permitted = bool(rate_res.get("accessPermitted", not bal_status.get("is_out", False)))

        usd_now = round(_safe_float(bal_status.get("usd", 0.0)), 4)
        diem_now = round(_safe_float(bal_status.get("diem", 0.0)), 4)
        bundled_now = round(_safe_float(bal_status.get("bundled_credits", 0.0)), 4)
        api_tier = str(bal_status.get("api_tier") or "paid")

        # Schedule & delta from previous report
        sched = self.vault.get_report_settings()
        prev_usd = sched.get("last_usd_balance")
        prev_diem = sched.get("last_diem_balance")
        usd_delta = round(usd_now - prev_usd, 4) if prev_usd is not None else None
        diem_delta = round(diem_now - prev_diem, 4) if prev_diem is not None else None
        spend_since_last = round(max(0.0, -usd_delta), 4) if usd_delta is not None else 0.0

        # Merge local vault keys + live remote Venice keys (if admin key available)
        known_bots = self.vault.get_agent_bots() or {}
        merged_keys: Dict[str, Dict[str, Any]] = {}
        for vk in self.vault.get_venice_keys():
            kid = str(vk.get("id") or "")
            if kid:
                merged_keys[kid] = dict(vk, _source="vault")

        remote_synced = False
        admin_key = self.vault.get_venice_admin_key() or self.vault.get_venice_admin_key(caller_agent=caller_agent)
        if admin_key:
            remote_res = client.list_keys(admin_key=admin_key)
            if remote_res.get("success"):
                remote_synced = True
                for rk in remote_res.get("keys", []) or []:
                    kid = str(rk.get("id") or "")
                    if not kid:
                        continue
                    existing = merged_keys.get(kid, {})
                    combined = dict(existing)
                    combined.update(rk)
                    # Preserve local agent / tier tags when remote key doesn't carry them
                    if existing.get("agent") and not combined.get("agent"):
                        combined["agent"] = existing["agent"]
                    if existing.get("maxModelTier") and not combined.get("maxModelTier"):
                        combined["maxModelTier"] = existing["maxModelTier"]
                    combined["_source"] = "venice_api"
                    merged_keys[kid] = combined

        # Per-agent aggregation bucket helper
        agents_map: Dict[str, Dict[str, Any]] = {}

        def ensure_agent(name: str) -> Dict[str, Any]:
            canon = self.canonical_agent_name(name, known_bots)
            if canon not in agents_map:
                bot_entry = None
                for bk, bv in known_bots.items():
                    if self.canonical_agent_name(bk, known_bots) == canon:
                        bot_entry = bv
                        break
                agents_map[canon] = {
                    "agent": canon,
                    "bot_username": ("@" + str(bot_entry.get("username")).lstrip("@")) if (bot_entry and bot_entry.get("username")) else None,
                    "has_scoped_admin": self.guard.is_agent_authorized_for_admin(canon),
                    "active_keys_count": 0,
                    "subkeys_count": 0,
                    "allocations_total": 0,
                    "allocations_claimed": 0,
                    "allocations_remaining": 0,
                    "tiers": set(),
                    "budget_cap_usd": 0.0,
                    "spend_period_usd": 0.0,
                    "spend_7d_usd": 0.0,
                    "spend_7d_diem": 0.0,
                    "local_calls_24h": 0,
                    "local_calls_7d": 0,
                    "local_tokens_24h": 0,
                    "local_cost_24h_usd": 0.0,
                    "local_errors_24h": 0,
                    "avg_latency_ms_24h": 0,
                    "_latencies_24h": [],
                    "last_used_at": None,
                    "keys": [],
                }
            return agents_map[canon]

        # Always include primary controller bot & registered agent bots
        ensure_agent(self.PRIMARY_AGENT)
        for bot_agent in known_bots.keys():
            ensure_agent(bot_agent)

        sanitized_keys: List[Dict[str, Any]] = []
        total_spend_period = 0.0
        total_spend_7d = 0.0
        total_spend_7d_diem = 0.0

        for kid, k in merged_keys.items():
            ktype = str(k.get("apiKeyType") or "INFERENCE").upper()
            agent_name = self.infer_agent_from_key(k, known_bots)
            bucket = ensure_agent(agent_name)

            usage_7d = (k.get("usage") or {}).get("trailingSevenDays") or {}
            spend_7d_usd = _safe_float(usage_7d.get("usd"), 0.0)
            spend_7d_diem = _safe_float(usage_7d.get("diem"), 0.0)

            period_usage = k.get("currentPeriodUsage") or {}
            spend_period_usd = _safe_float(period_usage.get("usd"), 0.0)

            limits_obj = k.get("consumptionLimits") or k.get("consumptionLimit") or {}
            limit_usd = limits_obj.get("usd")
            limit_val = _safe_float(limit_usd, 0.0) if limit_usd is not None else None

            tier = str(k.get("maxModelTier") or "xl").lower()
            last_used = k.get("lastUsedAt") or k.get("createdAt")

            if k.get("status", "ACTIVE") != "REVOKED":
                bucket["active_keys_count"] += 1
            bucket["tiers"].add(tier.upper())
            if limit_val is not None and limit_val > 0:
                bucket["budget_cap_usd"] = round(bucket["budget_cap_usd"] + limit_val, 2)
            bucket["spend_period_usd"] = round(bucket["spend_period_usd"] + spend_period_usd, 4)
            bucket["spend_7d_usd"] = round(bucket["spend_7d_usd"] + spend_7d_usd, 4)
            bucket["spend_7d_diem"] = round(bucket["spend_7d_diem"] + spend_7d_diem, 4)
            if last_used and (not bucket["last_used_at"] or str(last_used) > str(bucket["last_used_at"])):
                bucket["last_used_at"] = last_used

            total_spend_period += spend_period_usd
            total_spend_7d += spend_7d_usd
            total_spend_7d_diem += spend_7d_diem

            clean_key = {
                "id": kid,
                "name": str(k.get("description") or "Unnamed Key"),
                "masked": _mask_key_id_or_tail(k),
                "agent": agent_name,
                "key_type": ktype,
                "tier": tier.upper(),
                "limit_usd": round(limit_val, 2) if limit_val is not None else None,
                "limit_period": k.get("limitPeriod") or "MONTH",
                "spend_period_usd": round(spend_period_usd, 4),
                "spend_7d_usd": round(spend_7d_usd, 4),
                "spend_7d_diem": round(spend_7d_diem, 4),
                "last_used_at": k.get("lastUsedAt"),
                "created_at": k.get("createdAt"),
                "source": k.get("_source", "vault"),
            }
            sanitized_keys.append(clean_key)
            bucket["keys"].append(clean_key)

        # Sub-keys
        subkeys = self.vault.get_subkeys() or []
        for sk in subkeys:
            ag = sk.get("assigned_agent") or sk.get("target_agent") or "shared"
            bucket = ensure_agent(ag)
            bucket["subkeys_count"] += 1
            sk_tier = str(sk.get("quality_tier") or sk.get("maxModelTier") or "s").upper()
            bucket["tiers"].add(sk_tier)
            b_usd = _safe_float(sk.get("budget_usd"), 0.0)
            if b_usd > 0 and bucket["active_keys_count"] == 0:
                bucket["budget_cap_usd"] = round(bucket["budget_cap_usd"] + b_usd, 2)

        # Cloud DNS Allocations
        allocations = self.vault.get_allocations(include_secret=False) or []
        total_alloc_claimed = 0
        total_alloc_remaining = 0
        for al in allocations:
            ag = al.get("target_agent") or "shared"
            bucket = ensure_agent(ag)
            allocated_cnt = int(al.get("allocated_keys_count", 1) or 1)
            claimed_cnt = int(al.get("claimed_keys_count", 0) or 0)
            rem_cnt = int(al.get("remaining_claims", max(0, allocated_cnt - claimed_cnt)) or 0)
            bucket["allocations_total"] += allocated_cnt
            bucket["allocations_claimed"] += claimed_cnt
            bucket["allocations_remaining"] += rem_cnt
            total_alloc_claimed += claimed_cnt
            total_alloc_remaining += rem_cnt
            for ch in al.get("claims_history", []) or []:
                c_at = ch.get("claimed_at")
                if c_at and (not bucket["last_used_at"] or str(c_at) > str(bucket["last_used_at"])):
                    bucket["last_used_at"] = c_at

        # Local inference telemetry events (24h & 7d)
        cutoff_24h = now_ts - 86400.0
        cutoff_7d = now_ts - (7 * 86400.0)
        events_7d = self.vault.get_inference_events(since_ts=cutoff_7d)
        total_local_calls_24h = 0
        total_local_tokens_24h = 0
        total_local_cost_24h = 0.0

        for ev in events_7d:
            ag = ev.get("agent") or "shared"
            bucket = ensure_agent(ag)
            bucket["local_calls_7d"] += 1
            ev_ts = _safe_float(ev.get("timestamp"), 0.0)
            ev_iso = ev.get("iso")
            if ev_iso and (not bucket["last_used_at"] or str(ev_iso) > str(bucket["last_used_at"])):
                bucket["last_used_at"] = ev_iso
            if ev_ts >= cutoff_24h:
                bucket["local_calls_24h"] += 1
                total_local_calls_24h += 1
                toks = int(ev.get("tokens", 0) or 0)
                cost = _safe_float(ev.get("cost_usd"), 0.0)
                lat = int(ev.get("latency_ms", 0) or 0)
                bucket["local_tokens_24h"] += toks
                total_local_tokens_24h += toks
                bucket["local_cost_24h_usd"] = round(bucket["local_cost_24h_usd"] + cost, 6)
                total_local_cost_24h += cost
                if not ev.get("success", True):
                    bucket["local_errors_24h"] += 1
                if lat > 0:
                    bucket["_latencies_24h"].append(lat)

        # Finalize per-agent rows & guard metrics
        agents_list: List[Dict[str, Any]] = []
        for canon, bucket in agents_map.items():
            lats = bucket.pop("_latencies_24h", [])
            bucket["avg_latency_ms_24h"] = int(sum(lats) / len(lats)) if lats else 0
            bucket["tiers"] = sorted(list(bucket["tiers"]))
            if bucket["has_scoped_admin"] or canon == self.PRIMARY_AGENT:
                gm = self.guard.get_abuse_metrics(canon)
                bucket["guard"] = {
                    "keys_created_1h": gm["keys_created_past_hour"],
                    "max_keys_1h": gm["max_keys_per_hour"],
                    "keys_created_24h": gm["keys_created_past_24h"],
                    "max_keys_24h": gm["max_keys_per_day"],
                    "max_budget_per_key_usd": gm["max_budget_per_key_usd"],
                }
            else:
                bucket["guard"] = None
            # Keep non-empty agents + primary controller
            has_activity = (
                canon == self.PRIMARY_AGENT
                or bucket["bot_username"] is not None
                or bucket["active_keys_count"] > 0
                or bucket["subkeys_count"] > 0
                or bucket["allocations_total"] > 0
                or bucket["local_calls_7d"] > 0
            )
            if has_activity:
                agents_list.append(bucket)

        # Sort: primary bot first if active, otherwise by 7d spend + 24h calls + keys
        agents_list.sort(
            key=lambda a: (
                1 if a["agent"] == self.PRIMARY_AGENT else 0,
                a["spend_7d_usd"] + a["local_cost_24h_usd"],
                a["local_calls_24h"],
                a["active_keys_count"] + a["subkeys_count"],
            ),
            reverse=True,
        )

        # Sort keys by 7d spend then period spend
        sanitized_keys.sort(
            key=lambda k: (k["spend_7d_usd"], k["spend_period_usd"]),
            reverse=True,
        )

        # Service & Tunnel Keep-Alive Status
        service_info: Dict[str, Any] = {}
        tunnel_info: Dict[str, Any] = {}
        if include_service:
            try:
                sup_st = ServiceSupervisor().get_status()
                sup_st["uptime_human"] = _format_uptime(sup_st.get("uptime_seconds", 0.0))
                service_info = sup_st
            except Exception as e:
                service_info = {"success": False, "error": str(e)}
            try:
                tunnel_info = CloudflareTunnelManager(self.vault).get_status()
            except Exception as e:
                tunnel_info = {"status": "UNKNOWN", "error": str(e)}

        # Next scheduled report timestamp
        interval_sec = sched["interval_hours"] * 3600.0
        next_report_in_sec = None
        if sched["enabled"]:
            if sched["last_report_at"] > 0:
                next_report_in_sec = max(0, int((sched["last_report_at"] + interval_sec) - now_ts))
            else:
                next_report_in_sec = 0

        return {
            "success": True,
            "timestamp": now_iso,
            "timestamp_human": now_human,
            "account": {
                "status": bal_status.get("status", "UNKNOWN"),
                "badge": bal_status.get("badge", "⚪ UNKNOWN"),
                "usd": usd_now,
                "diem": diem_now,
                "bundled_credits": bundled_now,
                "api_tier": api_tier,
                "access_permitted": access_permitted,
                "next_epoch_begins": next_epoch,
                "low_threshold_usd": low_usd,
                "out_threshold_usd": out_usd,
                "warning": bal_status.get("warning"),
                "recharge_url": bal_status.get("recharge_url", "https://venice.ai/settings/api"),
                "usd_delta_since_last_report": usd_delta,
                "diem_delta_since_last_report": diem_delta,
                "spend_since_last_report_usd": spend_since_last,
            },
            "summary": {
                "active_agents": len(agents_list),
                "total_keys": len(sanitized_keys),
                "remote_keys_synced": remote_synced,
                "total_subkeys": len(subkeys),
                "allocations_count": len(allocations),
                "allocations_claimed": total_alloc_claimed,
                "allocations_remaining": total_alloc_remaining,
                "spend_period_usd": round(total_spend_period, 4),
                "spend_7d_usd": round(total_spend_7d, 4),
                "spend_7d_diem": round(total_spend_7d_diem, 4),
                "local_calls_24h": total_local_calls_24h,
                "local_tokens_24h": total_local_tokens_24h,
                "local_cost_24h_usd": round(total_local_cost_24h, 6),
            },
            "agents": agents_list,
            "top_keys": sanitized_keys[:8],
            "schedule": {
                "enabled": sched["enabled"],
                "interval_hours": sched["interval_hours"],
                "last_report_iso": sched["last_report_iso"],
                "reports_sent_count": sched["reports_sent_count"],
                "next_report_in_seconds": next_report_in_sec,
                "next_report_in_human": _format_uptime(next_report_in_sec) if next_report_in_sec is not None else "Paused",
            },
            "service": service_info,
            "tunnel": tunnel_info,
        }

    def format_telegram_markdown(self, report: Dict[str, Any], scheduled: bool = False) -> str:
        """Renders the agent inference report into clean Telegram Markdown."""
        acc = report.get("account", {})
        summ = report.get("summary", {})
        sched = report.get("schedule", {})
        svc = report.get("service", {})
        tun = report.get("tunnel", {})
        agents = report.get("agents", [])
        top_keys = report.get("top_keys", [])

        mode_tag = "⏰ Scheduled Auto-Report" if scheduled else "⚡ Live Agent Report"
        usd_delta = acc.get("usd_delta_since_last_report")
        if usd_delta is None:
            delta_str = "Baseline snapshot"
        elif usd_delta < 0:
            delta_str = f"-${abs(usd_delta):.4f} USD spent since last report"
        elif usd_delta > 0:
            delta_str = f"+${usd_delta:.4f} USD (recharged)"
        else:
            delta_str = "$0.0000 change since last report"

        lines = [
            f"📈 *VENICE AGENT INFERENCE REPORT* ({mode_tag})",
            f"📅 `{report.get('timestamp_human', '')}`",
            "",
            "💰 *Balance & Inference Health*",
            f"• *Status*: {acc.get('badge', '🟢 HEALTHY')} (`{str(acc.get('api_tier', 'paid')).upper()}`)",
            f"• *USD Balance*: `${acc.get('usd', 0.0):.4f}` ({delta_str})",
            f"• *DIEM Balance*: `{acc.get('diem', 0.0):.2f} DIEM`",
            f"• *7-Day Key Spend*: `${summ.get('spend_7d_usd', 0.0):.4f} USD`"
            + (f" / `{summ.get('spend_7d_diem', 0.0):.2f} DIEM`" if summ.get("spend_7d_diem", 0) > 0 else ""),
            f"• *24h Local Calls*: `{summ.get('local_calls_24h', 0)} reqs` (`{summ.get('local_tokens_24h', 0)} tok`)",
        ]

        if acc.get("warning"):
            lines.append(f"⚠️ *{acc.get('warning')}*")

        lines.append("")
        lines.append(f"🤖 *Agent Usage Breakdown ({len(agents)})*")
        if agents:
            for ag in agents[:8]:
                name = ag.get("agent", "agent")
                bot_u = f" ({ag['bot_username']})" if ag.get("bot_username") else ""
                tiers = ",".join(ag.get("tiers") or []) or "—"
                k_cnt = ag.get("active_keys_count", 0)
                sk_cnt = ag.get("subkeys_count", 0)
                s7d = ag.get("spend_7d_usd", 0.0)
                c24 = ag.get("local_calls_24h", 0)
                tok24 = ag.get("local_tokens_24h", 0)
                clm = ag.get("allocations_claimed", 0)
                tot_al = ag.get("allocations_total", 0)

                lines.append(f"• *{name}*{bot_u} `[Tiers: {tiers}]`")
                detail = f"  Keys: `{k_cnt}`"
                if sk_cnt:
                    detail += f" (+`{sk_cnt}` sub)"
                detail += f" | 7d Spend: `${s7d:.4f}` | 24h: `{c24} req` (`{tok24} tok`)"
                lines.append(detail)

                if tot_al > 0:
                    lines.append(f"  Allocations Claimed: `{clm}/{tot_al}`")
                guard = ag.get("guard")
                if guard:
                    lines.append(
                        f"  Guard Quota: `{guard['keys_created_1h']}/{guard['max_keys_1h']} hr` · "
                        f"`{guard['keys_created_24h']}/{guard['max_keys_24h']} day` (Cap `${guard['max_budget_per_key_usd']:.0f}`)"
                    )
        else:
            lines.append("• No active agents recorded yet.")

        active_top = [k for k in top_keys if k.get("spend_7d_usd", 0) > 0 or k.get("spend_period_usd", 0) > 0]
        if active_top:
            lines.append("")
            lines.append("🔑 *Top Consuming Keys (7d)*")
            for tk in active_top[:5]:
                lim_str = f" / ${tk['limit_usd']:.2f}" if tk.get("limit_usd") else ""
                lines.append(
                    f"• `{tk['masked']}` *{tk['name']}* (`{tk['agent']}`): `${tk['spend_7d_usd']:.4f}`{lim_str}"
                )

        if svc:
            port_ok = "ONLINE ✅" if (svc.get("port_listening") or svc.get("service_active")) else "CHECK ⚠️"
            sup_ok = "ACTIVE ✅" if svc.get("supervisor_running") else ("AUTOSTART ✅" if svc.get("autostart_installed") else "DIRECT ⚪")
            tun_url = tun.get("active_url") or self.vault.get_cloudflare_config().get("domain", "venice.vmu.cash")
            sched_str = (
                f"Every `{sched.get('interval_hours')}h` (next in `{sched.get('next_report_in_human')}`)"
                if sched.get("enabled")
                else "`PAUSED ⏸️`"
            )
            lines.extend([
                "",
                "🛡️ *Online & Keep-Alive Status*",
                f"• *Port {svc.get('port', 8844)}*: `{port_ok}` | *Watchdog*: `{sup_ok}`",
                f"• *Uptime*: `{svc.get('uptime_human', '0s')}` | *Auto-Restarts*: `{svc.get('restarts_count', 0)}`",
                f"• *Endpoint*: `{tun_url}`",
                f"• *Report Cadence*: {sched_str}",
            ])

        return "\n".join(lines)

    def format_cli_text(self, report: Dict[str, Any]) -> str:
        """Renders a clean terminal summary for `python run.py report`."""
        acc = report.get("account", {})
        summ = report.get("summary", {})
        sched = report.get("schedule", {})
        svc = report.get("service", {})

        lines = [
            "=" * 64,
            f"VENICE AGENT INFERENCE & UPTIME REPORT ({report.get('timestamp_human')})",
            "=" * 64,
            f"  Account Health:   {acc.get('badge')} (Tier: {str(acc.get('api_tier', 'paid')).upper()})",
            f"  USD Balance:      ${acc.get('usd', 0.0):.4f} USD",
            f"  DIEM Balance:     {acc.get('diem', 0.0):.2f} DIEM",
            f"  7-Day Key Spend:  ${summ.get('spend_7d_usd', 0.0):.4f} USD",
            f"  24h Local Calls:  {summ.get('local_calls_24h', 0)} requests ({summ.get('local_tokens_24h', 0)} tokens)",
            f"  Provisioned Keys: {summ.get('total_keys', 0)} keys | {summ.get('total_subkeys', 0)} sub-keys | {summ.get('allocations_claimed', 0)}/{summ.get('allocations_count', 0)} allocations",
            "-" * 64,
            "  AGENT BREAKDOWN:",
        ]
        for ag in report.get("agents", []):
            bot_u = f" ({ag['bot_username']})" if ag.get("bot_username") else ""
            lines.append(
                f"    • {ag['agent']}{bot_u}: {ag['active_keys_count']} keys, "
                f"7d=${ag['spend_7d_usd']:.4f}, 24h={ag['local_calls_24h']} calls ({ag['local_tokens_24h']} tok)"
            )
            if ag.get("guard"):
                g = ag["guard"]
                lines.append(
                    f"      Guard Quota: {g['keys_created_1h']}/{g['max_keys_1h']} hr, {g['keys_created_24h']}/{g['max_keys_24h']} 24h"
                )
        if svc:
            lines.extend([
                "-" * 64,
                f"  Keep-Alive:       Port {svc.get('port', 8844)}={'LISTENING' if svc.get('port_listening') else 'CLOSED'} | "
                f"Supervisor={'RUNNING' if svc.get('supervisor_running') else 'STANDBY'} | Uptime={svc.get('uptime_human', '0s')}",
                f"  Auto-Report:      {'ENABLED' if sched.get('enabled') else 'PAUSED'} (every {sched.get('interval_hours')}h)",
            ])
        lines.append("=" * 64)
        return "\n".join(lines)

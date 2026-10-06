"""
Anti-Abuse Safeguards & Rate Limiting Guard for Agent Key Creation.
Enforces strict budget caps, creation quotas, cooldowns, key type restrictions,
and model tier gating for Venice.ai key management by autonomous agents.
"""

import os
import time
import logging
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Set
from datetime import datetime

logger = logging.getLogger("key_guard")


@dataclass
class GuardResult:
    allowed: bool
    reason: Optional[str] = None
    sanitized_budget: float = 1.0
    sanitized_tier: str = "s"
    sanitized_key_type: str = "INFERENCE"
    warning: Optional[str] = None


class KeyCreationGuard:
    # --- Strict Anti-Abuse Policies ---
    MAX_BUDGET_PER_KEY: float = 15.00       # Hard maximum USD budget per key
    DEFAULT_BUDGET: float = 1.00            # Default USD budget if not specified
    MIN_BUDGET: float = 0.05                # Minimum viable USD budget
    REJECT_BUDGET_THRESHOLD: float = 50.00  # Requests exceeding this are rejected outright

    MAX_KEYS_PER_HOUR: int = 5              # Max keys created per 1 hour sliding window
    MAX_KEYS_PER_DAY: int = 15              # Max keys created per 24 hours sliding window
    MIN_COOLDOWN_SECONDS: float = 10.0      # Minimum seconds between consecutive key creations
    MAX_ACTIVE_KEYS_PER_AGENT: int = 50     # Quota cap on total active keys per agent

    ALLOWED_KEY_TYPES: Set[str] = {"INFERENCE"}
    ALLOWED_TIERS: Set[str] = {"xs", "s", "m", "l", "xl"}

    AUTHORIZED_ADMIN_AGENTS: Set[str] = {
        "v3n15pe_bot",
        "v3n15pe",
        "@v3n15pe_bot"
    }

    def __init__(self, vault: Any, cooldown_seconds: Optional[float] = None):
        self.vault = vault
        if cooldown_seconds is not None:
            self.cooldown_seconds = float(cooldown_seconds)
        else:
            env_val = os.environ.get("VENICE_KEY_COOLDOWN_SECONDS")
            self.cooldown_seconds = float(env_val) if env_val is not None else self.MIN_COOLDOWN_SECONDS

    @classmethod
    def normalize_agent_name(cls, agent_name: Optional[str]) -> str:
        """Normalizes agent name for consistent identification."""
        if not agent_name:
            return ""
        name = str(agent_name).strip().lower()
        if name.startswith("@"):
            name = name[1:]
        return name

    def is_agent_authorized_for_admin(self, agent_name: str) -> bool:
        """Checks if the agent has authorization to utilize an admin account."""
        norm = self.normalize_agent_name(agent_name)
        if norm in self.AUTHORIZED_ADMIN_AGENTS:
            return True
        # Check vault's explicitly configured agent_admin_keys
        agent_admin_keys = self.vault.data.get("security", {}).get("agent_admin_keys", {})
        return norm in [self.normalize_agent_name(k) for k in agent_admin_keys.keys()]

    def validate_key_request(
        self,
        caller_agent: str,
        key_type: str = "INFERENCE",
        budget_usd: Optional[float] = None,
        tier: str = "s",
        target_agent: str = "",
        bypass_cooldown: bool = False
    ) -> GuardResult:
        """
        Validates a key creation request against anti-abuse policies:
        - Key Type Restriction: INFERENCE only (ADMIN keys forbidden).
        - Budget Caps: Must be within [MIN_BUDGET, MAX_BUDGET_PER_KEY].
        - Rate Limits: Hourly & Daily creation limits.
        - Cooldown: Enforces waiting period between creations.
        - Active Key Quota: Prevents key flooding.
        - Model Tier Validation: Only permitted model tiers allowed.
        """
        caller_norm = self.normalize_agent_name(caller_agent)
        clean_target = self.normalize_agent_name(target_agent) or caller_norm

        # 1. Key Type Check (Strict Privilege Escalation Prevention)
        clean_ktype = str(key_type or "INFERENCE").strip().upper()
        if clean_ktype not in self.ALLOWED_KEY_TYPES:
            logger.warning(f"Anti-abuse: Blocked agent '{caller_agent}' attempt to create '{clean_ktype}' key.")
            return GuardResult(
                allowed=False,
                reason=(
                    f"Anti-Abuse Safeguard: Agent '{caller_agent}' is strictly prohibited from creating "
                    f"'{clean_ktype}' keys. Autonomous agents are only permitted to issue INFERENCE keys."
                ),
                sanitized_key_type="INFERENCE"
            )

        # 2. Quality Tier Check
        clean_tier = str(tier or "s").strip().lower()
        if clean_tier not in self.ALLOWED_TIERS:
            clean_tier = "s"

        # 3. Budget Validation & Capping
        warning_msg = None
        if budget_usd is None or budget_usd <= 0:
            sanitized_budget = self.DEFAULT_BUDGET
            warning_msg = f"No budget specified; defaulted to ${sanitized_budget:.2f} USD."
        elif budget_usd > self.REJECT_BUDGET_THRESHOLD:
            logger.warning(f"Anti-abuse: Blocked excessive budget request (${budget_usd:.2f}) from '{caller_agent}'.")
            return GuardResult(
                allowed=False,
                reason=(
                    f"Anti-Abuse Safeguard: Requested budget (${budget_usd:.2f} USD) exceeds the maximum limit "
                    f"of ${self.MAX_BUDGET_PER_KEY:.2f} USD. Key creation rejected."
                )
            )
        elif budget_usd > self.MAX_BUDGET_PER_KEY:
            sanitized_budget = self.MAX_BUDGET_PER_KEY
            warning_msg = (
                f"Requested budget (${budget_usd:.2f} USD) was capped to the anti-abuse maximum "
                f"of ${self.MAX_BUDGET_PER_KEY:.2f} USD."
            )
        elif budget_usd < self.MIN_BUDGET:
            return GuardResult(
                allowed=False,
                reason=f"Anti-Abuse Safeguard: Budget must be at least ${self.MIN_BUDGET:.2f} USD."
            )
        else:
            sanitized_budget = round(float(budget_usd), 2)

        # 4. Rate-Limiting & Quota Checks
        history = self.vault.get_key_creation_history(caller_norm)
        now = time.time()
        one_hour_ago = now - 3600.0
        one_day_ago = now - 86400.0

        recent_1h = [h for h in history if h.get("timestamp", 0) >= one_hour_ago]
        recent_24h = [h for h in history if h.get("timestamp", 0) >= one_day_ago]

        # Check cooldown
        if not bypass_cooldown and self.cooldown_seconds > 0 and history:
            last_ts = history[-1].get("timestamp", 0)
            elapsed = now - last_ts
            if elapsed < self.cooldown_seconds:
                remaining = int(self.cooldown_seconds - elapsed) + 1
                logger.warning(f"Anti-abuse: Cooldown active for '{caller_agent}' ({remaining}s remaining).")
                return GuardResult(
                    allowed=False,
                    reason=(
                        f"Anti-Abuse Rate Limit: Rapid key creation detected. Please wait {remaining} seconds "
                        f"before issuing another key."
                    )
                )

        # Check hourly quota
        if len(recent_1h) >= self.MAX_KEYS_PER_HOUR:
            logger.warning(f"Anti-abuse: Hourly quota exceeded for '{caller_agent}' ({len(recent_1h)} keys).")
            return GuardResult(
                allowed=False,
                reason=(
                    f"Anti-Abuse Rate Limit: Hourly creation limit reached ({self.MAX_KEYS_PER_HOUR} keys/hour). "
                    f"Quota resets in {int((recent_1h[0].get('timestamp', now) + 3600 - now) / 60)} minutes."
                )
            )

        # Check daily quota
        if len(recent_24h) >= self.MAX_KEYS_PER_DAY:
            logger.warning(f"Anti-abuse: Daily quota exceeded for '{caller_agent}' ({len(recent_24h)} keys).")
            return GuardResult(
                allowed=False,
                reason=(
                    f"Anti-Abuse Rate Limit: Daily creation limit reached ({self.MAX_KEYS_PER_DAY} keys/24h). "
                    f"Please wait before requesting additional keys."
                )
            )

        # Check active keys quota
        existing_keys = self.vault.get_venice_keys()
        agent_active_keys = [
            k for k in existing_keys
            if self.normalize_agent_name(k.get("agent") or k.get("assigned_agent")) == clean_target
            and k.get("status", "ACTIVE") == "ACTIVE"
        ]
        if len(agent_active_keys) >= self.MAX_ACTIVE_KEYS_PER_AGENT:
            logger.warning(f"Anti-abuse: Active keys quota ({len(agent_active_keys)}) reached for '{clean_target}'.")
            return GuardResult(
                allowed=False,
                reason=(
                    f"Anti-Abuse Quota: Target agent '{clean_target}' has {len(agent_active_keys)} active keys, "
                    f"which exceeds the maximum limit of {self.MAX_ACTIVE_KEYS_PER_AGENT}. "
                    f"Revoke unused keys before provisioning new ones."
                )
            )

        return GuardResult(
            allowed=True,
            sanitized_budget=sanitized_budget,
            sanitized_tier=clean_tier,
            sanitized_key_type="INFERENCE",
            warning=warning_msg
        )

    def record_key_creation(
        self,
        caller_agent: str,
        key_id: str,
        budget_usd: float,
        tier: str,
        target_agent: str = ""
    ) -> None:
        """Records a successful key creation in the audit trail for rate limiting."""
        caller_norm = self.normalize_agent_name(caller_agent)
        entry = {
            "timestamp": time.time(),
            "iso": datetime.utcnow().isoformat() + "Z",
            "key_id": key_id,
            "budget_usd": budget_usd,
            "tier": tier,
            "target_agent": self.normalize_agent_name(target_agent) or caller_norm
        }
        self.vault.record_key_creation(caller_norm, entry)
        logger.info(f"Recorded key creation {key_id} by '{caller_agent}' (${budget_usd:.2f}, tier {tier})")

    def get_abuse_metrics(self, caller_agent: str) -> Dict[str, Any]:
        """Returns rate limiting and usage metrics for an agent."""
        caller_norm = self.normalize_agent_name(caller_agent)
        history = self.vault.get_key_creation_history(caller_norm)
        now = time.time()
        one_hour_ago = now - 3600.0
        one_day_ago = now - 86400.0

        recent_1h = [h for h in history if h.get("timestamp", 0) >= one_hour_ago]
        recent_24h = [h for h in history if h.get("timestamp", 0) >= one_day_ago]

        last_ts = history[-1].get("timestamp", 0) if history else 0
        cooldown_rem = max(0.0, self.cooldown_seconds - (now - last_ts)) if (last_ts and self.cooldown_seconds > 0) else 0.0

        existing_keys = self.vault.get_venice_keys()
        active_keys = [
            k for k in existing_keys
            if self.normalize_agent_name(k.get("agent") or k.get("assigned_agent")) == caller_norm
            and k.get("status", "ACTIVE") == "ACTIVE"
        ]

        return {
            "agent": caller_norm,
            "is_authorized_admin": self.is_agent_authorized_for_admin(caller_norm),
            "keys_created_past_hour": len(recent_1h),
            "max_keys_per_hour": self.MAX_KEYS_PER_HOUR,
            "keys_created_past_24h": len(recent_24h),
            "max_keys_per_day": self.MAX_KEYS_PER_DAY,
            "cooldown_remaining_seconds": round(cooldown_rem, 1),
            "total_active_keys": len(active_keys),
            "max_active_keys": self.MAX_ACTIVE_KEYS_PER_AGENT,
            "max_budget_per_key_usd": self.MAX_BUDGET_PER_KEY,
            "default_budget_usd": self.DEFAULT_BUDGET,
            "allowed_key_types": list(self.ALLOWED_KEY_TYPES)
        }

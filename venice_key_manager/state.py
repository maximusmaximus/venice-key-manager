import json
import logging
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from .config import config
from .models import BackupExport

logger = logging.getLogger(__name__)

DEFAULT_CATEGORIES = ["Default", "Agents", "Production", "Testing", "Telegram", "Research"]


class StateStore:
    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or config.ensure_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.data_dir / "state.json"
        self._categories: List[str] = list(DEFAULT_CATEGORIES)
        self._key_metadata: Dict[str, Dict[str, Any]] = {}
        self._global_threshold: float = config.low_usd_warning_threshold
        self._auth_tokens: Dict[str, Dict[str, Any]] = {}
        self._projects: Dict[str, Dict[str, Any]] = {}
        self._external_keys: Dict[str, Dict[str, Any]] = {}
        self._cloudflare_gateway_url: Optional[str] = getattr(config, "cloudflare_gateway_url", None)
        self._load()

    def _init_default_project(self):
        """Seed default project if none exists."""
        if not self._projects:
            pid = "proj_default"
            now = datetime.utcnow().isoformat() + "Z"
            today = datetime.utcnow().strftime("%Y-%m-%d")
            week = f"{datetime.utcnow().year}-W{datetime.utcnow().isocalendar()[1]}"
            self._projects[pid] = {
                "id": pid,
                "name": "General Fleet & External Services",
                "description": "Default allocation pool for external integrations and connected agents",
                "daily_limit_usd": 5.00,
                "weekly_limit_usd": 25.00,
                "default_sub_key_daily_usd": 0.25,  # Default: 25 cents per project per day
                "max_model_tier": "xl",
                "created_at": now,
                "updated_at": now,
                "status": "active",
                "current_day_spend": 0.0,
                "current_week_spend": 0.0,
                "total_spend": 0.0,
                "total_requests": 0,
                "last_day_reset": today,
                "last_week_reset": week,
            }

    def _load(self):
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._categories = data.get("categories", list(DEFAULT_CATEGORIES))
                    self._key_metadata = data.get("key_metadata", {})
                    self._global_threshold = float(data.get("global_threshold", config.low_usd_warning_threshold))
                    self._auth_tokens = data.get("auth_tokens", {})
                    self._projects = data.get("projects", {})
                    self._external_keys = data.get("external_keys", {})
                    self._cloudflare_gateway_url = data.get("cloudflare_gateway_url", getattr(config, "cloudflare_gateway_url", None))
            except Exception as e:
                logger.error(f"Failed to load state from {self.state_file}: {e}")
        self._init_default_project()

    def save(self):
        try:
            temp_file = self.state_file.with_suffix(".tmp")
            data = {
                "categories": self._categories,
                "key_metadata": self._key_metadata,
                "global_threshold": self._global_threshold,
                "auth_tokens": self._auth_tokens,
                "projects": self._projects,
                "external_keys": self._external_keys,
                "cloudflare_gateway_url": self._cloudflare_gateway_url,
            }
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            temp_file.replace(self.state_file)
        except Exception as e:
            logger.error(f"Failed to save state to {self.state_file}: {e}")

    # --- Categories ---
    def get_categories(self) -> List[str]:
        return list(self._categories)

    def add_category(self, category: str) -> bool:
        category = category.strip()
        if category and category not in self._categories:
            self._categories.append(category)
            self.save()
            return True
        return False

    def remove_category(self, category: str) -> bool:
        category = category.strip()
        if category in self._categories and category != "Default":
            self._categories.remove(category)
            # Reassign keys in this category to Default
            for meta in self._key_metadata.values():
                if meta.get("category") == category:
                    meta["category"] = "Default"
            self.save()
            return True
        return False

    # --- Key Metadata ---
    def get_key_meta(self, key_id: str) -> Dict[str, Any]:
        return self._key_metadata.get(key_id, {
            "category": "Default",
            "custom_threshold": None,
            "notes": ""
        })

    def update_key_meta(
        self,
        key_id: str,
        category: Optional[str] = None,
        custom_threshold: Optional[float] = None,
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        meta = self._key_metadata.setdefault(key_id, {
            "category": "Default",
            "custom_threshold": None,
            "notes": ""
        })
        if category is not None:
            cat = category.strip()
            if cat not in self._categories:
                self.add_category(cat)
            meta["category"] = cat
        if custom_threshold is not None:
            meta["custom_threshold"] = float(custom_threshold) if custom_threshold > 0 else None
        if notes is not None:
            meta["notes"] = notes
        self.save()
        return meta

    def remove_key_meta(self, key_id: str):
        if key_id in self._key_metadata:
            del self._key_metadata[key_id]
            self.save()

    # --- Global Threshold ---
    def get_global_threshold(self) -> float:
        return self._global_threshold

    def set_global_threshold(self, threshold: float):
        if threshold >= 0:
            self._global_threshold = float(threshold)
            self.save()

    # --- Backup Export / Import ---
    def export_backup(self, additional_summary: Optional[Dict[str, Any]] = None) -> BackupExport:
        return BackupExport(
            global_threshold=self._global_threshold,
            categories=self._categories,
            key_metadata=self._key_metadata,
            summary=additional_summary or {}
        )

    def import_backup(self, backup_data: Dict[str, Any]) -> bool:
        try:
            if "categories" in backup_data and isinstance(backup_data["categories"], list):
                for cat in backup_data["categories"]:
                    if isinstance(cat, str) and cat.strip() and cat not in self._categories:
                        self._categories.append(cat.strip())
            if "global_threshold" in backup_data:
                self._global_threshold = float(backup_data["global_threshold"])
            if "key_metadata" in backup_data and isinstance(backup_data["key_metadata"], dict):
                for k, v in backup_data["key_metadata"].items():
                    if isinstance(v, dict):
                        self._key_metadata[k] = v
            self.save()
            return True
        except Exception as e:
            logger.error(f"Failed to import backup: {e}")
            return False

    # --- Auth Tokens (Telegram-generated session keys & batch access codes) ---
    def create_auth_token(
        self,
        created_by: str = "telegram",
        ttl_hours: int = 168,
        prefix: str = "vkm_tg_",
        notes: Optional[str] = None
    ) -> str:
        """Create and persist a cryptographically random access token with an optional prefix."""
        self._load()
        clean_prefix = prefix.strip() if prefix else "vkm_"
        sep = "" if clean_prefix.endswith(("-", "_", ":", ".")) else "_"
        token = f"{clean_prefix}{sep}{secrets.token_hex(20)}"
        now = datetime.utcnow()
        expires_at = (now + timedelta(hours=ttl_hours)).isoformat() + "Z"
        self._auth_tokens[token] = {
            "token": token,
            "prefix": clean_prefix,
            "created_at": now.isoformat() + "Z",
            "expires_at": expires_at,
            "created_by": created_by,
            "notes": notes or "",
            "active": True
        }
        self.save()
        return token

    def create_batch_auth_tokens(
        self,
        prefix: str = "vkm_code",
        count: int = 5,
        created_by: str = "batch",
        ttl_hours: int = 168,
        notes: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Create and persist a batch of cryptographically random access/pairing codes with a custom prefix."""
        self._load()
        now = datetime.utcnow()
        expires_at = (now + timedelta(hours=ttl_hours)).isoformat() + "Z"
        created_tokens = []

        clean_prefix = prefix.strip() if prefix else "vkm_code"
        sep = "" if clean_prefix.endswith(("-", "_", ":", ".")) else "_"
        count = max(1, min(int(count), 100))

        for i in range(1, count + 1):
            hex_part = secrets.token_hex(12)
            if count > 1:
                token = f"{clean_prefix}{sep}{i:02d}_{hex_part}"
            else:
                token = f"{clean_prefix}{sep}{hex_part}"

            meta = {
                "token": token,
                "prefix": clean_prefix,
                "index": i,
                "created_at": now.isoformat() + "Z",
                "expires_at": expires_at,
                "created_by": created_by,
                "notes": notes or f"Batch of {count} ({clean_prefix})",
                "active": True
            }
            self._auth_tokens[token] = meta
            created_tokens.append(dict(meta))

        self.save()
        return created_tokens

    def validate_auth_token(self, token: Optional[str]) -> bool:
        """Check if a token is valid, active, and unexpired."""
        if not token or not isinstance(token, str):
            return False
        token = token.strip()
        if not token:
            return False
        
        # Check static token override from env
        if config.web_auth_token and token == config.web_auth_token:
            return True

        # If not present in memory, reload state from disk (e.g. created by bot/CLI in another process)
        if token not in self._auth_tokens:
            self._load()

        if token not in self._auth_tokens:
            return False

        meta = self._auth_tokens[token]
        if not meta.get("active", True):
            return False

        exp = meta.get("expires_at")
        if exp:
            try:
                exp_clean = exp.rstrip("Z")
                exp_dt = datetime.fromisoformat(exp_clean)
                if datetime.utcnow() > exp_dt:
                    return False
            except Exception:
                pass

        return True

    def revoke_auth_token(self, token: str) -> bool:
        """Deactivate an access token."""
        self._load()
        if token in self._auth_tokens:
            self._auth_tokens[token]["active"] = False
            self.save()
            return True
        return False

    def list_active_tokens(self, prefix: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all valid, unexpired tokens, optionally filtered by prefix."""
        self._load()
        now = datetime.utcnow()
        active = []
        for t, meta in self._auth_tokens.items():
            if not meta.get("active", True):
                continue
            if prefix:
                clean_p = prefix.strip()
                if not (t.startswith(clean_p) or meta.get("prefix") == clean_p):
                    continue
            exp = meta.get("expires_at")
            if exp:
                try:
                    exp_clean = exp.rstrip("Z")
                    if now > datetime.fromisoformat(exp_clean):
                        continue
                except Exception:
                    pass
            active.append(dict(meta))
        return active

    # =========================================================================
    # Cloudflare Gateway Public DNS URL Configuration
    # =========================================================================
    def get_cloudflare_gateway_url(self) -> Optional[str]:
        return self._cloudflare_gateway_url

    def set_cloudflare_gateway_url(self, url: Optional[str]):
        self._cloudflare_gateway_url = url.strip() if (url and url.strip()) else None
        self.save()

    # =========================================================================
    # Projects & Allocations Management
    # =========================================================================
    def create_project(
        self,
        name: str,
        description: str = "",
        daily_limit_usd: float = 1.00,
        weekly_limit_usd: Optional[float] = None,
        default_sub_key_daily_usd: float = 0.25,  # Default: 25 cents per project per day
        max_model_tier: str = "xl"
    ) -> Dict[str, Any]:
        self._load()
        pid = f"proj_{secrets.token_hex(6)}"
        now = datetime.utcnow().isoformat() + "Z"
        today = datetime.utcnow().strftime("%Y-%m-%d")
        week = f"{datetime.utcnow().year}-W{datetime.utcnow().isocalendar()[1]}"

        project = {
            "id": pid,
            "name": name.strip(),
            "description": description.strip(),
            "daily_limit_usd": float(daily_limit_usd),
            "weekly_limit_usd": float(weekly_limit_usd) if weekly_limit_usd is not None else None,
            "default_sub_key_daily_usd": float(default_sub_key_daily_usd),
            "max_model_tier": max_model_tier.lower().strip(),
            "created_at": now,
            "updated_at": now,
            "status": "active",
            "current_day_spend": 0.0,
            "current_week_spend": 0.0,
            "total_spend": 0.0,
            "total_requests": 0,
            "last_day_reset": today,
            "last_week_reset": week,
        }
        self._projects[pid] = project
        self.save()
        return dict(project)

    def list_projects(self) -> List[Dict[str, Any]]:
        self._load()
        now = datetime.utcnow()
        today = now.strftime("%Y-%m-%d")
        week = f"{now.year}-W{now.isocalendar()[1]}"
        changed = False

        res = []
        for p in self._projects.values():
            # Check and reset project windows if rolled over
            if p.get("last_day_reset") != today:
                p["current_day_spend"] = 0.0
                p["last_day_reset"] = today
                changed = True
            if p.get("last_week_reset") != week:
                p["current_week_spend"] = 0.0
                p["last_week_reset"] = week
                changed = True

            # Compute connected keys count
            connected_keys = sum(
                1 for k in self._external_keys.values()
                if k.get("project_id") == p["id"] and k.get("status") == "active"
            )
            p_copy = dict(p)
            p_copy["connected_keys_count"] = connected_keys
            res.append(p_copy)

        if changed:
            self.save()
        return res

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        self._load()
        p = self._projects.get(project_id)
        if not p:
            return None
        # Check reset windows
        now = datetime.utcnow()
        today = now.strftime("%Y-%m-%d")
        week = f"{now.year}-W{now.isocalendar()[1]}"
        if p.get("last_day_reset") != today or p.get("last_week_reset") != week:
            if p.get("last_day_reset") != today:
                p["current_day_spend"] = 0.0
                p["last_day_reset"] = today
            if p.get("last_week_reset") != week:
                p["current_week_spend"] = 0.0
                p["last_week_reset"] = week
            self.save()
        return dict(p)

    def update_project(self, project_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        self._load()
        if project_id not in self._projects:
            return None
        p = self._projects[project_id]
        if "name" in kwargs and kwargs["name"] is not None:
            p["name"] = str(kwargs["name"]).strip()
        if "description" in kwargs and kwargs["description"] is not None:
            p["description"] = str(kwargs["description"]).strip()
        if "daily_limit_usd" in kwargs and kwargs["daily_limit_usd"] is not None:
            p["daily_limit_usd"] = float(kwargs["daily_limit_usd"])
        if "weekly_limit_usd" in kwargs:
            val = kwargs["weekly_limit_usd"]
            p["weekly_limit_usd"] = float(val) if val is not None else None
        if "default_sub_key_daily_usd" in kwargs and kwargs["default_sub_key_daily_usd"] is not None:
            p["default_sub_key_daily_usd"] = float(kwargs["default_sub_key_daily_usd"])
        if "max_model_tier" in kwargs and kwargs["max_model_tier"] is not None:
            p["max_model_tier"] = str(kwargs["max_model_tier"]).lower().strip()
        if "status" in kwargs and kwargs["status"] is not None:
            p["status"] = str(kwargs["status"]).strip()
        p["updated_at"] = datetime.utcnow().isoformat() + "Z"
        self.save()
        return dict(p)

    def delete_project(self, project_id: str) -> bool:
        self._load()
        if project_id in self._projects:
            # Cannot delete default project if it's the only one
            if project_id == "proj_default" and len(self._projects) == 1:
                return False
            del self._projects[project_id]
            # Deactivate associated external keys
            for k in self._external_keys.values():
                if k.get("project_id") == project_id:
                    k["status"] = "revoked"
            self.save()
            return True
        return False

    # =========================================================================
    # External Keys & Sub-Keys Management
    # =========================================================================
    def create_external_key(
        self,
        project_id: str,
        name: str,
        daily_limit_usd: Optional[float] = None,
        weekly_limit_usd: Optional[float] = None,
        limit_period: str = "DAY",
        max_model_tier: str = "xl",
        prefix: str = "vkm_ext_",
        notes: str = "",
        created_by: str = "admin",
        key_type: str = "ADMIN_EXTERNAL",
        parent_key_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._load()
        project = self.get_project(project_id)
        if not project:
            raise ValueError(f"Project with ID '{project_id}' not found.")

        # Default allocation for sub keys or external keys: 25 cents per day unless overridden
        effective_daily = daily_limit_usd
        if effective_daily is None:
            effective_daily = float(project.get("default_sub_key_daily_usd", 0.25))

        kid = f"ext_key_{secrets.token_hex(6)}"
        clean_prefix = prefix.strip() if prefix else "vkm_ext_"
        sep = "" if clean_prefix.endswith(("-", "_", ":", ".")) else "_"
        token = f"{clean_prefix}{sep}{secrets.token_hex(20)}"
        now = datetime.utcnow().isoformat() + "Z"
        today = datetime.utcnow().strftime("%Y-%m-%d")

        key_data = {
            "id": kid,
            "token": token,
            "name": name.strip(),
            "project_id": project_id,
            "project_name": project.get("name", "Project"),
            "key_type": key_type,
            "parent_key_id": parent_key_id,
            "daily_limit_usd": float(effective_daily),
            "weekly_limit_usd": float(weekly_limit_usd) if weekly_limit_usd is not None else None,
            "limit_period": limit_period.upper().strip(),
            "max_model_tier": (max_model_tier or project.get("max_model_tier", "xl")).lower().strip(),
            "created_at": now,
            "created_by": created_by,
            "notes": notes.strip(),
            "status": "active",
            "current_period_spend": 0.0,
            "total_spend": 0.0,
            "total_requests": 0,
            "last_reset_date": today,
        }
        self._external_keys[kid] = key_data
        self.save()
        return dict(key_data)

    def create_sub_key(
        self,
        parent_key_or_token: str,
        name: str,
        amount_usd: Optional[float] = 0.25,  # Default: 25 cents per project per day unless created by admin
        period: str = "DAY",
        max_model_tier: Optional[str] = None,
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """Allow external users to create sub-keys with allocated spend per project per day or week."""
        self._load()
        parent = self.get_external_key(parent_key_or_token)
        if not parent:
            # Check if parent is a pairing token from auth_tokens
            if parent_key_or_token in self._auth_tokens:
                parent = {
                    "id": "pairing_auth",
                    "project_id": "proj_default",
                    "max_model_tier": "xl",
                    "status": "active",
                }
            else:
                raise ValueError("Invalid parent access key or pairing code.")

        if parent.get("status") != "active":
            raise ValueError("Parent key is inactive or revoked.")

        project_id = parent.get("project_id", "proj_default")
        effective_amount = amount_usd if amount_usd is not None and amount_usd > 0 else 0.25
        tier = max_model_tier or parent.get("max_model_tier", "xl")

        return self.create_external_key(
            project_id=project_id,
            name=name,
            daily_limit_usd=effective_amount if period.upper() == "DAY" else effective_amount / 7.0,
            weekly_limit_usd=effective_amount if period.upper() == "WEEK" else effective_amount * 7.0,
            limit_period=period.upper(),
            max_model_tier=tier,
            prefix="vkm_sub_",
            notes=notes or f"Sub-key of {parent.get('name', 'External')}",
            created_by=f"sub_key:{parent.get('id', 'parent')}",
            key_type="SUB_KEY",
            parent_key_id=parent.get("id"),
        )

    def list_external_keys(self, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
        self._load()
        today = datetime.utcnow().strftime("%Y-%m-%d")
        changed = False

        res = []
        for k in self._external_keys.values():
            if project_id and k.get("project_id") != project_id:
                continue
            if k.get("last_reset_date") != today:
                k["current_period_spend"] = 0.0
                k["last_reset_date"] = today
                changed = True
            res.append(dict(k))

        if changed:
            self.save()
        res.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return res

    def get_external_key(self, key_id_or_token: str) -> Optional[Dict[str, Any]]:
        self._load()
        clean = key_id_or_token.strip()
        for k in self._external_keys.values():
            if k.get("id") == clean or k.get("token") == clean:
                today = datetime.utcnow().strftime("%Y-%m-%d")
                if k.get("last_reset_date") != today:
                    k["current_period_spend"] = 0.0
                    k["last_reset_date"] = today
                    self.save()
                return dict(k)
        return None

    def update_external_key(self, key_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        self._load()
        k = self._external_keys.get(key_id)
        if not k:
            return None
        if "name" in kwargs and kwargs["name"] is not None:
            k["name"] = str(kwargs["name"]).strip()
        if "daily_limit_usd" in kwargs and kwargs["daily_limit_usd"] is not None:
            k["daily_limit_usd"] = float(kwargs["daily_limit_usd"])
        if "weekly_limit_usd" in kwargs:
            val = kwargs["weekly_limit_usd"]
            k["weekly_limit_usd"] = float(val) if val is not None else None
        if "max_model_tier" in kwargs and kwargs["max_model_tier"] is not None:
            k["max_model_tier"] = str(kwargs["max_model_tier"]).lower().strip()
        if "status" in kwargs and kwargs["status"] is not None:
            k["status"] = str(kwargs["status"]).strip()
        if "limit_period" in kwargs and kwargs["limit_period"] is not None:
            k["limit_period"] = str(kwargs["limit_period"]).upper().strip()
        self.save()
        return dict(k)

    def revoke_external_key(self, key_id_or_token: str) -> bool:
        self._load()
        clean = key_id_or_token.strip()
        for k in self._external_keys.values():
            if k.get("id") == clean or k.get("token") == clean:
                k["status"] = "revoked"
                self.save()
                return True
        return False

    def validate_external_key(
        self,
        token: str
    ) -> Tuple[bool, Optional[Dict[str, Any]], Optional[Dict[str, Any]], str]:
        """Validate external key or pairing code against project limits and spend ceilings."""
        if not token or not isinstance(token, str):
            return False, None, None, "Missing authentication key or pairing code."

        clean_token = token.strip()
        key_meta = self.get_external_key(clean_token)

        # Allow valid pairing tokens from auth_tokens to access gateway under default project
        if not key_meta:
            if self.validate_auth_token(clean_token):
                default_proj = self.get_project("proj_default") or list(self._projects.values())[0]
                key_meta = {
                    "id": f"paired_{clean_token[:12]}",
                    "token": clean_token,
                    "name": "Paired Device Access",
                    "project_id": default_proj["id"],
                    "project_name": default_proj["name"],
                    "key_type": "PAIRING_CODE",
                    "daily_limit_usd": float(default_proj.get("default_sub_key_daily_usd", 0.25)),
                    "weekly_limit_usd": None,
                    "limit_period": "DAY",
                    "max_model_tier": default_proj.get("max_model_tier", "xl"),
                    "status": "active",
                    "current_period_spend": 0.0,
                    "total_spend": 0.0,
                    "total_requests": 0,
                    "last_reset_date": datetime.utcnow().strftime("%Y-%m-%d"),
                }
            else:
                return False, None, None, "Invalid or expired access key / pairing code."

        if key_meta.get("status") != "active":
            return False, key_meta, None, "This external access key has been paused or revoked."

        project = self.get_project(key_meta.get("project_id", "proj_default"))
        if not project or project.get("status") != "active":
            return False, key_meta, project, "The project associated with this key is inactive or paused."

        # Check spend ceilings
        daily_cap = float(key_meta.get("daily_limit_usd", 0.25))
        key_spent = float(key_meta.get("current_period_spend", 0.0))
        if key_spent >= daily_cap:
            return False, key_meta, project, (
                f"Daily allocation ceiling reached (${daily_cap:.2f} USD limit). "
                "Contact admin to allocate additional inference."
            )

        proj_daily = float(project.get("daily_limit_usd", 5.00))
        proj_spent = float(project.get("current_day_spend", 0.0))
        if proj_spent >= proj_daily:
            return False, key_meta, project, (
                f"Project daily allocation exhausted (${proj_daily:.2f} USD limit for '{project.get('name')}'). "
                "Contact admin to increase project allocation."
            )

        return True, key_meta, project, "OK"

    def record_external_usage(
        self,
        key_id: str,
        project_id: str,
        cost_usd: float,
        tokens: int = 0
    ):
        """Atomically record usage and cost against external key and project."""
        self._load()
        today = datetime.utcnow().strftime("%Y-%m-%d")
        week = f"{datetime.utcnow().year}-W{datetime.utcnow().isocalendar()[1]}"

        # Update Key
        if key_id in self._external_keys:
            k = self._external_keys[key_id]
            if k.get("last_reset_date") != today:
                k["current_period_spend"] = 0.0
                k["last_reset_date"] = today
            k["current_period_spend"] = round(float(k.get("current_period_spend", 0.0)) + cost_usd, 6)
            k["total_spend"] = round(float(k.get("total_spend", 0.0)) + cost_usd, 6)
            k["total_requests"] = int(k.get("total_requests", 0)) + 1

        # Update Project
        if project_id in self._projects:
            p = self._projects[project_id]
            if p.get("last_day_reset") != today:
                p["current_day_spend"] = 0.0
                p["last_day_reset"] = today
            if p.get("last_week_reset") != week:
                p["current_week_spend"] = 0.0
                p["last_week_reset"] = week
            p["current_day_spend"] = round(float(p.get("current_day_spend", 0.0)) + cost_usd, 6)
            p["current_week_spend"] = round(float(p.get("current_week_spend", 0.0)) + cost_usd, 6)
            p["total_spend"] = round(float(p.get("total_spend", 0.0)) + cost_usd, 6)
            p["total_requests"] = int(p.get("total_requests", 0)) + 1

        self.save()


# Global singleton instance
state_store = StateStore()


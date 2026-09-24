import json
import logging
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
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
        self._load()

    def _load(self):
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._categories = data.get("categories", list(DEFAULT_CATEGORIES))
                    self._key_metadata = data.get("key_metadata", {})
                    self._global_threshold = float(data.get("global_threshold", config.low_usd_warning_threshold))
                    self._auth_tokens = data.get("auth_tokens", {})
            except Exception as e:
                logger.error(f"Failed to load state from {self.state_file}: {e}")

    def save(self):
        try:
            temp_file = self.state_file.with_suffix(".tmp")
            data = {
                "categories": self._categories,
                "key_metadata": self._key_metadata,
                "global_threshold": self._global_threshold,
                "auth_tokens": self._auth_tokens,
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

    # --- Auth Tokens (Telegram-generated session keys) ---
    def create_auth_token(self, created_by: str = "telegram", ttl_hours: int = 168) -> str:
        """Create and persist a cryptographically random access token."""
        self._load()
        token = "vkm_tg_" + secrets.token_hex(24)
        now = datetime.utcnow()
        expires_at = (now + timedelta(hours=ttl_hours)).isoformat() + "Z"
        self._auth_tokens[token] = {
            "token": token,
            "created_at": now.isoformat() + "Z",
            "expires_at": expires_at,
            "created_by": created_by,
            "active": True
        }
        self.save()
        return token

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

    def list_active_tokens(self) -> List[Dict[str, Any]]:
        """List all valid, unexpired tokens."""
        self._load()
        now = datetime.utcnow()
        active = []
        for t, meta in self._auth_tokens.items():
            if not meta.get("active", True):
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


# Global singleton instance
state_store = StateStore()

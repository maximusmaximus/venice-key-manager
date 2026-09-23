import json
import logging
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
        self._load()

    def _load(self):
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._categories = data.get("categories", list(DEFAULT_CATEGORIES))
                    self._key_metadata = data.get("key_metadata", {})
                    self._global_threshold = float(data.get("global_threshold", config.low_usd_warning_threshold))
            except Exception as e:
                logger.error(f"Failed to load state from {self.state_file}: {e}")

    def save(self):
        try:
            temp_file = self.state_file.with_suffix(".tmp")
            data = {
                "categories": self._categories,
                "key_metadata": self._key_metadata,
                "global_threshold": self._global_threshold,
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


# Global singleton instance
state_store = StateStore()

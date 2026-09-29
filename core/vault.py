"""
Key Vault & Configuration Store for Venice.ai & Telegram Agent Bots.
Persists managed keys, agent mappings, and configuration to a local JSON vault.
"""

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

VAULT_FILE = Path(__file__).resolve().parent.parent / "venice_vault.json"
DEFAULT_CONFIG_PATH = Path(os.environ.get("AGENT_CONFIG_PATH", "config.yaml"))


class KeyVault:
    def __init__(self, vault_path: Optional[Path] = None):
        self.vault_path = vault_path or VAULT_FILE
        self.data: Dict[str, Any] = self._load()

    def _default_data(self) -> Dict[str, Any]:
        venice_inf_key = os.environ.get("VENICE_INFERENCE_KEY") or os.environ.get("VENICE_API_KEY", "")
        venice_admin_key = os.environ.get("VENICE_ADMIN_KEY", "")
        tg_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        tg_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        config_file = Path(os.environ.get("AGENT_CONFIG_PATH", "config.yaml"))

        # Auto-discover from config file if available
        if config_file.exists():
            try:
                import yaml
                with open(config_file, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                if not venice_inf_key:
                    venice_inf_key = cfg.get("model", {}).get("api_key", "")
                if not tg_bot_token:
                    tg_bot_token = cfg.get("channels", {}).get("telegram", {}).get("bot_token", "")
            except Exception:
                pass

        default_agent_bots = {}
        if tg_bot_token:
            default_agent_bots["primary-agent"] = {
                "agent_name": "primary-agent",
                "bot_token": tg_bot_token,
                "config_path": str(config_file),
                "created_at": datetime.utcnow().isoformat() + "Z",
                "notes": "Primary agent communication bot"
            }

        return {
            "version": "1.0",
            "venice": {
                "base_url": os.environ.get("VENICE_BASE_URL", "https://api.venice.ai/api/v1"),
                "admin_key": venice_admin_key,
                "inference_key": venice_inf_key,
                "default_model": os.environ.get("VENICE_MODEL", "deepseek-v4-flash"),
                "keys": []
            },
            "telegram": {
                "master_bot_token": tg_bot_token,
                "authorized_chat_id": tg_chat_id,
                "mtproto": {
                    "api_id": os.environ.get("TELEGRAM_API_ID", ""),
                    "api_hash": os.environ.get("TELEGRAM_API_HASH", ""),
                    "phone": os.environ.get("TELEGRAM_PHONE", ""),
                    "session_file": "botfather_user.session"
                },
                "agent_bots": default_agent_bots
            },
            "deployments": {
                "primary-agent": {
                    "config_path": str(config_file),
                    "venice_key_field": "model.api_key",
                    "tg_token_field": "channels.telegram.bot_token"
                }
            }
        }

    def _load(self) -> Dict[str, Any]:
        if not self.vault_path.exists():
            defaults = self._default_data()
            self._save(defaults)
            return defaults
        try:
            with open(self.vault_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return self._default_data()

    def _save(self, data: Optional[Dict[str, Any]] = None) -> None:
        if data is not None:
            self.data = data
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.vault_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        shutil.move(str(tmp), str(self.vault_path))

    # --- Venice Key Helpers ---

    def get_venice_admin_key(self) -> str:
        return os.environ.get("VENICE_ADMIN_KEY") or self.data.get("venice", {}).get("admin_key", "")

    def set_venice_admin_key(self, key: str) -> None:
        self.data.setdefault("venice", {})["admin_key"] = key.strip()
        self._save()

    def get_venice_inference_key(self) -> str:
        return os.environ.get("VENICE_INFERENCE_KEY") or os.environ.get("VENICE_API_KEY") or self.data.get("venice", {}).get("inference_key", "")

    def set_venice_inference_key(self, key: str) -> None:
        self.data.setdefault("venice", {})["inference_key"] = key.strip()
        self._save()

    def get_active_venice_key(self) -> str:
        """Returns the admin key if set, otherwise the inference key."""
        return self.get_venice_admin_key() or self.get_venice_inference_key()

    def get_venice_keys(self) -> List[Dict[str, Any]]:
        return self.data.get("venice", {}).get("keys", [])

    def store_venice_key(self, key_info: Dict[str, Any]) -> None:
        keys = self.data.setdefault("venice", {}).setdefault("keys", [])
        key_id = key_info.get("id")
        updated = False
        for i, k in enumerate(keys):
            if k.get("id") == key_id:
                keys[i] = key_info
                updated = True
                break
        if not updated:
            keys.insert(0, key_info)
        self._save()

    def remove_venice_key(self, key_id: str) -> bool:
        keys = self.data.setdefault("venice", {}).setdefault("keys", [])
        initial_len = len(keys)
        self.data["venice"]["keys"] = [k for k in keys if k.get("id") != key_id]
        if len(self.data["venice"]["keys"]) < initial_len:
            self._save()
            return True
        return False

    # --- Telegram Agent Bot Helpers ---

    def get_telegram_master_token(self) -> str:
        return os.environ.get("TELEGRAM_BOT_TOKEN") or self.data.get("telegram", {}).get("master_bot_token", "")

    def get_authorized_chat_id(self) -> str:
        return str(os.environ.get("TELEGRAM_CHAT_ID") or self.data.get("telegram", {}).get("authorized_chat_id", ""))

    def get_agent_bots(self) -> Dict[str, Dict[str, Any]]:
        return self.data.get("telegram", {}).get("agent_bots", {})

    def get_agent_bot(self, agent_name: str) -> Optional[Dict[str, Any]]:
        return self.get_agent_bots().get(agent_name)

    def register_agent_bot(self, agent_name: str, bot_data: Dict[str, Any]) -> None:
        bots = self.data.setdefault("telegram", {}).setdefault("agent_bots", {})
        bot_data["agent_name"] = agent_name
        bot_data.setdefault("updated_at", datetime.utcnow().isoformat() + "Z")
        bots[agent_name] = bot_data
        self._save()

    def remove_agent_bot(self, agent_name: str) -> bool:
        bots = self.data.setdefault("telegram", {}).setdefault("agent_bots", {})
        if agent_name in bots:
            del bots[agent_name]
            self._save()
            return True
        return False

    def get_mtproto_config(self) -> Dict[str, Any]:
        return self.data.get("telegram", {}).get("mtproto", {})

    def set_mtproto_config(self, api_id: str, api_hash: str, phone: str = "") -> None:
        mt = self.data.setdefault("telegram", {}).setdefault("mtproto", {})
        mt["api_id"] = str(api_id)
        mt["api_hash"] = str(api_hash)
        if phone:
            mt["phone"] = phone
        self._save()

    # --- Deployments ---

    def get_deployments(self) -> Dict[str, Dict[str, Any]]:
        return self.data.get("deployments", {})

    def register_deployment(self, name: str, config_path: str, venice_field: str = "", tg_field: str = "") -> None:
        self.data.setdefault("deployments", {})[name] = {
            "config_path": config_path,
            "venice_key_field": venice_field,
            "tg_token_field": tg_field
        }
        self._save()

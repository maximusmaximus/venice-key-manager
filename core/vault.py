"""
Key Vault & Configuration Store for Venice.ai & Telegram Agent Bots.
Persists managed keys, agent mappings, and configuration with automated atomic
persistence, multi-tier secure backup snapshots, and crash-resilient auto-recall.
"""

import json
import logging
import os
import secrets
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger("venice_vault")

# Paths & Directories
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VAULT_FILE = PROJECT_ROOT / "venice_vault.json"
BACKUP_VAULT_FILE = PROJECT_ROOT / "venice_vault.backup.json"
BACKUP_DIR = PROJECT_ROOT / ".vault_backups"

# Resilient user-profile mirrors (survives scratch cleanups, branch changes, and re-clones)
if os.name == "nt":
    _local_appdata = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    USER_PROFILE_BACKUP = Path(_local_appdata) / "venice" / "venice_vault_backup.json"
else:
    USER_PROFILE_BACKUP = Path.home() / ".config" / "venice" / "venice_vault_backup.json"
HOME_VENICE_BACKUP = Path.home() / ".venice" / "venice_vault_backup.json"

DEFAULT_CONFIG_PATH = Path(os.environ.get("AGENT_CONFIG_PATH", "config.yaml"))

# Canonical locations for agent configs and env files
KNOWN_CONFIG_PATHS = [
    Path(r"D:\hermes-music\data\config.yaml"),
    Path(r"C:\Users\maxin\.gemini\antigravity\scratch\a2a-server\config.yaml"),
    Path(r"D:\hermes-music\data\dawagent_config.yaml"),
    Path(r"D:\hermes-music\data\worker_config.yaml"),
    PROJECT_ROOT / "config.yaml",
    Path("config.yaml"),
]

KNOWN_ENV_FILES = [
    Path(r"D:\hermes-music\.env"),
    Path(r"D:\hermes-music\data\.env"),
    PROJECT_ROOT / ".env",
    Path(".env"),
    Path.home() / ".venice" / ".env",
]


def _read_json_file(path: Path) -> Optional[Dict[str, Any]]:
    """Safely reads and parses a JSON file. Returns None if unreadable or invalid."""
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else None
    except Exception as e:
        logger.warning(f"Error reading JSON from {path}: {e}")
        return None


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> bool:
    """
    Atomically writes dictionary to JSON file with forced disk flush (fsync)
    to prevent file corruption during sudden restarts or power loss.
    """
    tmp_path = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Random suffix: pid+timestamp alone collides when two threads save in the same millisecond.
        tmp_name = f".tmp_{os.getpid()}_{secrets.token_hex(6)}.tmp"
        tmp_path = path.parent / tmp_name
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        # On Windows os.replace fails transiently if another process (AV scanner, a reader)
        # holds the destination open. Retry briefly before giving up.
        last_err = None
        for attempt in range(6):
            try:
                os.replace(str(tmp_path), str(path))
                return True
            except PermissionError as pe:
                last_err = pe
                time.sleep(0.05 * (attempt + 1))
        raise last_err  # type: ignore[misc]
    except Exception as e:
        logger.error(f"Failed to atomically write {path}: {e}")
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except Exception:
                pass
        return False


def _has_valuable_keys(data: Dict[str, Any]) -> bool:
    """Checks whether the dictionary contains valuable secret keys."""
    if not isinstance(data, dict):
        return False
    venice = data.get("venice", {})
    tg = data.get("telegram", {})
    subkeys = data.get("subkeys", [])
    allocations = data.get("allocations", [])
    if venice.get("admin_key") or venice.get("inference_key") or venice.get("keys"):
        return True
    if tg.get("master_bot_token") or tg.get("agent_bots"):
        return True
    if subkeys or allocations:
        return True
    return False


def _extract_from_env_file(path: Path) -> Dict[str, str]:
    """Extracts Venice and Telegram variables from a .env file."""
    res = {}
    if not path.exists():
        return res
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k in (
                    "VENICE_API_KEY",
                    "VENICE_INFERENCE_KEY",
                    "VENICE_ADMIN_KEY",
                    "TELEGRAM_BOT_TOKEN",
                    "TELEGRAM_CHAT_ID",
                    "SECURE_PAIRING_CODE",
                    "CLOUDFLARE_DOMAIN"
                ) and v:
                    res[k] = v
    except Exception:
        pass
    return res


def _extract_from_yaml_file(path: Path) -> Dict[str, str]:
    """Extracts model.api_key and channels.telegram.bot_token from a YAML config."""
    res = {}
    if not path.exists():
        return res
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        if isinstance(cfg, dict):
            api_key = cfg.get("model", {}).get("api_key", "")
            bot_token = cfg.get("channels", {}).get("telegram", {}).get("bot_token", "")
            if api_key:
                res["VENICE_INFERENCE_KEY"] = api_key
            if bot_token:
                res["TELEGRAM_BOT_TOKEN"] = bot_token
    except Exception:
        pass
    return res


class KeyVault:
    def __init__(self, vault_path: Optional[Path] = None):
        env_path = os.environ.get("VENICE_VAULT_PATH", "").strip()
        self.vault_path = Path(vault_path) if vault_path else (Path(env_path) if env_path else VAULT_FILE)
        self.backup_path = self.vault_path.with_suffix(".backup.json")
        # Only the canonical production vault mirrors into user-profile stores and the shared
        # snapshot directory. Sandboxed vaults (tests, VENICE_VAULT_PATH) stay self-contained.
        self.is_primary = self.vault_path.resolve() == VAULT_FILE.resolve()
        self.snapshot_dir = BACKUP_DIR if self.is_primary else self.vault_path.parent / ".vault_backups"
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {}
        self._loaded_mtime: Optional[int] = None
        if self.is_primary:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        self.data = self._load()
        self._loaded_mtime = self._disk_mtime()

    # --- Cross-instance freshness ---

    def _disk_mtime(self) -> Optional[int]:
        try:
            return self.vault_path.stat().st_mtime_ns
        except OSError:
            return None

    @property
    def data(self) -> Dict[str, Any]:
        """
        Live vault document. Several KeyVault instances may coexist (Telegram bot, web server,
        MCP server, CLI). Before every access we cheaply stat the file and reload if another
        instance or process wrote it, so nobody saves over newer state with a stale copy.
        """
        mtime = self._disk_mtime()
        if mtime is not None and self._loaded_mtime is not None and mtime != self._loaded_mtime:
            with self._lock:
                disk = _read_json_file(self.vault_path)
                if isinstance(disk, dict) and (_has_valuable_keys(disk) or not _has_valuable_keys(self._data)):
                    self._data = disk
                self._loaded_mtime = mtime
        return self._data

    @data.setter
    def data(self, value: Dict[str, Any]) -> None:
        self._data = value

    def reload(self) -> Dict[str, Any]:
        """Force a reload from disk (no-op if the file is unreadable)."""
        with self._lock:
            disk = _read_json_file(self.vault_path)
            if isinstance(disk, dict):
                self._data = disk
            self._loaded_mtime = self._disk_mtime()
        return self._data

    def _default_data(self) -> Dict[str, Any]:
        """Constructs default vault template and attempts initial auto-discovery."""
        venice_inf_key = os.environ.get("VENICE_INFERENCE_KEY") or os.environ.get("VENICE_API_KEY", "")
        venice_admin_key = os.environ.get("VENICE_ADMIN_KEY", "")
        tg_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        tg_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

        # Auto-discover from known configs and .env files
        for p in KNOWN_CONFIG_PATHS:
            if p.exists():
                extracted = _extract_from_yaml_file(p)
                if not venice_inf_key and "VENICE_INFERENCE_KEY" in extracted:
                    venice_inf_key = extracted["VENICE_INFERENCE_KEY"]
                if not tg_bot_token and "TELEGRAM_BOT_TOKEN" in extracted:
                    tg_bot_token = extracted["TELEGRAM_BOT_TOKEN"]

        for p in KNOWN_ENV_FILES:
            if p.exists():
                extracted = _extract_from_env_file(p)
                if not venice_inf_key and "VENICE_API_KEY" in extracted:
                    venice_inf_key = extracted["VENICE_API_KEY"]
                if not venice_inf_key and "VENICE_INFERENCE_KEY" in extracted:
                    venice_inf_key = extracted["VENICE_INFERENCE_KEY"]
                if not venice_admin_key and "VENICE_ADMIN_KEY" in extracted:
                    venice_admin_key = extracted["VENICE_ADMIN_KEY"]
                if not tg_bot_token and "TELEGRAM_BOT_TOKEN" in extracted:
                    tg_bot_token = extracted["TELEGRAM_BOT_TOKEN"]
                if not tg_chat_id and "TELEGRAM_CHAT_ID" in extracted:
                    tg_chat_id = extracted["TELEGRAM_CHAT_ID"]

        config_file = str(KNOWN_CONFIG_PATHS[0]) if KNOWN_CONFIG_PATHS[0].exists() else "config.yaml"

        default_agent_bots = {}
        if tg_bot_token:
            default_agent_bots["hermes-music"] = {
                "agent_name": "hermes-music",
                "bot_token": tg_bot_token,
                "config_path": config_file,
                "created_at": datetime.utcnow().isoformat() + "Z",
                "notes": "Primary Hermes music & publishing bot"
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
                "authorized_chat_id": tg_chat_id or os.environ.get("TELEGRAM_CHAT_ID", ""),
                "mtproto": {
                    "api_id": os.environ.get("TELEGRAM_API_ID", ""),
                    "api_hash": os.environ.get("TELEGRAM_API_HASH", ""),
                    "phone": os.environ.get("TELEGRAM_PHONE", ""),
                    "session_file": "botfather_user.session"
                },
                "agent_bots": default_agent_bots
            },
            "deployments": {
                "hermes-music": {
                    "config_path": config_file,
                    "venice_key_field": "model.api_key",
                    "tg_token_field": "channels.telegram.bot_token"
                }
            },
            "security": {
                "pairing_code": os.environ.get("SECURE_PAIRING_CODE", "VK-AB67CA09"),
                "require_pairing": True
            },
            "cloudflare": {
                "domain": os.environ.get("CLOUDFLARE_DOMAIN", "venice.vmu.cash")
            },
            "subkeys": []
        }

    def _find_newest_valid_backup(self) -> Optional[Dict[str, Any]]:
        """Scans all backup stores and returns the newest valid snapshot with keys."""
        candidates = []

        # 1. Vault's dedicated backup file
        d_self = _read_json_file(self.backup_path)
        if d_self and _has_valuable_keys(d_self):
            candidates.append((self.backup_path.stat().st_mtime, d_self, self.backup_path))

        # If custom vault path (e.g. unit test), do not mix with production fleet backups
        if not self.is_primary:
            return candidates[0][1] if candidates else None

        # 2. Primary backup file
        d1 = _read_json_file(BACKUP_VAULT_FILE)
        if d1 and _has_valuable_keys(d1):
            candidates.append((BACKUP_VAULT_FILE.stat().st_mtime, d1, BACKUP_VAULT_FILE))

        # 3. User profile backups
        for up in (USER_PROFILE_BACKUP, HOME_VENICE_BACKUP):
            d2 = _read_json_file(up)
            if d2 and _has_valuable_keys(d2):
                candidates.append((up.stat().st_mtime, d2, up))

        # 4. Rolling snapshots in .vault_backups/
        if BACKUP_DIR.exists():
            for snap in sorted(BACKUP_DIR.glob("vault_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
                d3 = _read_json_file(snap)
                if d3 and _has_valuable_keys(d3):
                    candidates.append((snap.stat().st_mtime, d3, snap))
                    break

        if not candidates:
            return None

        # Sort by modification time descending
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    def _auto_recall_missing_fields(self, data: Dict[str, Any]) -> bool:
        """
        Inspects data for missing keys or empty configs and recovers them
        from backup mirrors, known agent YAML configs, and .env files.
        Returns True if any fields were recalled and merged.
        """
        changed = False
        venice = data.setdefault("venice", {})
        telegram = data.setdefault("telegram", {})
        security = data.setdefault("security", {})
        cloudflare = data.setdefault("cloudflare", {})

        # Step A: Check newest valid backup snapshot to fill missing items
        backup_candidate = self._find_newest_valid_backup()
        if backup_candidate:
            b_venice = backup_candidate.get("venice", {})
            b_tg = backup_candidate.get("telegram", {})
            b_sec = backup_candidate.get("security", {})
            b_cf = backup_candidate.get("cloudflare", {})
            b_subkeys = backup_candidate.get("subkeys", [])

            if not venice.get("admin_key") and b_venice.get("admin_key"):
                venice["admin_key"] = b_venice["admin_key"]
                changed = True
                logger.info("Recalled Venice Admin Key from backup store")

            if not venice.get("inference_key") and b_venice.get("inference_key"):
                venice["inference_key"] = b_venice["inference_key"]
                changed = True
                logger.info("Recalled Venice Inference Key from backup store")

            if not venice.get("keys") and b_venice.get("keys"):
                venice["keys"] = b_venice["keys"]
                changed = True
                logger.info(f"Recalled {len(b_venice['keys'])} Venice keys from backup store")

            if not telegram.get("master_bot_token") and b_tg.get("master_bot_token"):
                telegram["master_bot_token"] = b_tg["master_bot_token"]
                changed = True
                logger.info("Recalled Telegram master bot token from backup store")

            if not telegram.get("agent_bots") and b_tg.get("agent_bots"):
                telegram["agent_bots"] = b_tg["agent_bots"]
                changed = True

            if not security.get("pairing_code") and b_sec.get("pairing_code"):
                security["pairing_code"] = b_sec["pairing_code"]
                changed = True

            if not cloudflare.get("domain") and b_cf.get("domain"):
                cloudflare["domain"] = b_cf["domain"]
                changed = True

            if not data.get("subkeys") and b_subkeys:
                data["subkeys"] = b_subkeys
                changed = True
                logger.info(f"Recalled {len(b_subkeys)} agent sub-keys from backup store")

        # Step B: Check known agent YAML configs
        for cp in KNOWN_CONFIG_PATHS:
            if cp.exists():
                cfg = _extract_from_yaml_file(cp)
                if not venice.get("inference_key") and cfg.get("VENICE_INFERENCE_KEY"):
                    venice["inference_key"] = cfg["VENICE_INFERENCE_KEY"]
                    changed = True
                    logger.info(f"Recalled inference key from {cp}")
                if not telegram.get("master_bot_token") and cfg.get("TELEGRAM_BOT_TOKEN"):
                    telegram["master_bot_token"] = cfg["TELEGRAM_BOT_TOKEN"]
                    changed = True
                    logger.info(f"Recalled telegram bot token from {cp}")

        # Step C: Check known .env files
        for ep in KNOWN_ENV_FILES:
            if ep.exists():
                env_vals = _extract_from_env_file(ep)
                if not venice.get("admin_key") and env_vals.get("VENICE_ADMIN_KEY"):
                    venice["admin_key"] = env_vals["VENICE_ADMIN_KEY"]
                    changed = True
                if not venice.get("inference_key") and (env_vals.get("VENICE_INFERENCE_KEY") or env_vals.get("VENICE_API_KEY")):
                    venice["inference_key"] = env_vals.get("VENICE_INFERENCE_KEY") or env_vals.get("VENICE_API_KEY")
                    changed = True
                if not telegram.get("master_bot_token") and env_vals.get("TELEGRAM_BOT_TOKEN"):
                    telegram["master_bot_token"] = env_vals["TELEGRAM_BOT_TOKEN"]
                    changed = True
                if not telegram.get("authorized_chat_id") and env_vals.get("TELEGRAM_CHAT_ID"):
                    telegram["authorized_chat_id"] = env_vals["TELEGRAM_CHAT_ID"]
                    changed = True

        # Step D: Check environment variables
        if not venice.get("admin_key") and os.environ.get("VENICE_ADMIN_KEY"):
            venice["admin_key"] = os.environ["VENICE_ADMIN_KEY"].strip()
            changed = True
        if not venice.get("inference_key") and (os.environ.get("VENICE_INFERENCE_KEY") or os.environ.get("VENICE_API_KEY")):
            venice["inference_key"] = (os.environ.get("VENICE_INFERENCE_KEY") or os.environ.get("VENICE_API_KEY")).strip()
            changed = True
        if not telegram.get("master_bot_token") and os.environ.get("TELEGRAM_BOT_TOKEN"):
            telegram["master_bot_token"] = os.environ["TELEGRAM_BOT_TOKEN"].strip()
            changed = True

        # Ensure defaults for pairing & domain
        if not security.get("pairing_code"):
            security["pairing_code"] = os.environ.get("VENICE_PAIRING_CODE", "VK-AB67CA09")
            changed = True
        if not cloudflare.get("domain"):
            cloudflare["domain"] = os.environ.get("VENICE_CLOUDFLARE_DOMAIN", "venice.vmu.cash")
            changed = True

        # Ensure agent_bots has primary agent if bot_token is known
        bots = telegram.setdefault("agent_bots", {})
        token = telegram.get("master_bot_token", "")
        if token and not bots:
            bot_id_num = None
            if ":" in token:
                try:
                    bot_id_num = int(token.split(":")[0])
                except Exception:
                    pass
            bots["primary-agent"] = {
                "agent_name": "primary-agent",
                "bot_token": token,
                "bot_id": bot_id_num or 0,
                "username": "agent_bot",
                "first_name": "Agent",
                "config_path": str(KNOWN_CONFIG_PATHS[0]) if KNOWN_CONFIG_PATHS else "config.yaml",
                "created_at": datetime.utcnow().isoformat() + "Z",
                "notes": "Primary agent bot"
            }
            changed = True

        return changed

    def _load(self) -> Dict[str, Any]:
        """
        Loads vault from disk with multi-tier fallback recovery.
        Never throws and never silently wipes valuable keys on restart.
        """
        data = None

        if self.vault_path.exists():
            data = _read_json_file(self.vault_path)
            if data is None and self.vault_path.stat().st_size > 0:
                # Primary file exists but is corrupted (e.g. from sudden crash)
                ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                corrupt_backup = self.snapshot_dir / f"corrupt_vault_{ts}.json"
                try:
                    self.snapshot_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(self.vault_path, corrupt_backup)
                    logger.error(f"Primary vault file corrupted. Preserved corrupt copy to {corrupt_backup}")
                except Exception:
                    pass

        # If primary vault failed or is missing, recover from backup stores
        if not data or not _has_valuable_keys(data):
            backup_candidate = self._find_newest_valid_backup()
            if backup_candidate:
                logger.info("Restoring active vault state from newest secure backup...")
                data = backup_candidate

        # If still no data, initialize default structure
        if not data:
            data = self._default_data()

        # Run intelligent auto-recall across all sources
        recalled = self._auto_recall_missing_fields(data)

        # If recovered, missing on disk, or changed, save back to disk and mirrors immediately
        if recalled or not self.vault_path.exists():
            self._data = data
            self._save(force=True, create_snapshot=True)

        return data

    def _rotate_snapshots(self, max_snapshots: int = 20) -> None:
        """Keeps the newest max_snapshots files in the snapshot dir and removes older ones."""
        try:
            if not self.snapshot_dir.exists():
                return
            snaps = sorted(self.snapshot_dir.glob("vault_*.json"), key=lambda p: p.stat().st_mtime)
            while len(snaps) > max_snapshots:
                oldest = snaps.pop(0)
                try:
                    oldest.unlink()
                except Exception:
                    pass
        except Exception:
            pass

    def _save(self, data: Optional[Dict[str, Any]] = None, force: bool = False, create_snapshot: bool = True) -> None:
        """
        Atomically saves vault data to primary file and synchronously mirrors
        to secondary backup and user-profile disaster recovery mirrors.
        """
        with self._lock:
            if data is not None:
                self._data = data
            # Deliberately use self._data (not the reloading property) so pending in-memory
            # mutations are never discarded by a reload right before they are written.
            current = self._data

            # Safety Guard: Never overwrite non-empty keys with empty keys unless force=True
            if not force and not _has_valuable_keys(current):
                disk_data = _read_json_file(self.vault_path) or self._find_newest_valid_backup()
                if disk_data and _has_valuable_keys(disk_data):
                    logger.warning("Safety guard triggered: Refusing to overwrite valuable keys with empty data. Merging...")
                    self._auto_recall_missing_fields(current)

            # 1. Atomic write to primary vault file
            _atomic_write_json(self.vault_path, current)
            self._loaded_mtime = self._disk_mtime()

            # 2. Synchronous mirror to secondary backup file
            _atomic_write_json(self.backup_path, current)

            # 3. Synchronous mirror to user profile disaster recovery mirrors (main vault only)
            if self.is_primary:
                for up in (USER_PROFILE_BACKUP, HOME_VENICE_BACKUP):
                    _atomic_write_json(up, current)

                # 4. Rolling versioned snapshot
                if create_snapshot and _has_valuable_keys(current):
                    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                    snap_path = self.snapshot_dir / f"vault_{ts}.json"
                    _atomic_write_json(snap_path, current)
                    self._rotate_snapshots(max_snapshots=20)

    # --- Backup & Recovery Public APIs ---

    def create_backup(self, label: str = "") -> Dict[str, Any]:
        """
        Explicitly creates a timestamped, labeled backup snapshot across all stores.
        Sandboxed (non-primary) vaults only write inside their own directory.
        """
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        safe_label = "".join(c for c in (label or "").strip() if c.isalnum() or c in "-_")[:40]
        clean_label = f"_{safe_label}" if safe_label else ""
        snap_path = self.snapshot_dir / f"vault_{ts}{clean_label}.json"

        current = self.data
        ok = _atomic_write_json(snap_path, current)
        _atomic_write_json(self.backup_path, current)
        if self.is_primary:
            for up in (USER_PROFILE_BACKUP, HOME_VENICE_BACKUP):
                _atomic_write_json(up, current)

        return {
            "success": ok,
            "path": str(snap_path),
            "filename": snap_path.name,
            "timestamp": ts,
            "size_bytes": snap_path.stat().st_size if snap_path.exists() else 0,
            "has_admin_key": bool(self.get_venice_admin_key()),
            "has_inference_key": bool(self.get_venice_inference_key()),
            "keys_count": len(self.get_venice_keys()),
            "subkeys_count": len(self.get_subkeys())
        }

    def list_backups(self) -> List[Dict[str, Any]]:
        """Lists all available backup snapshots with metadata, sorted newest first."""
        items = []
        seen_paths = set()

        def add_item(p: Path, kind: str):
            if not p.exists() or p in seen_paths:
                return
            seen_paths.add(p)
            d = _read_json_file(p)
            v = d.get("venice", {}) if d else {}
            items.append({
                "filename": p.name,
                "path": str(p),
                "type": kind,
                "modified_at": datetime.utcfromtimestamp(p.stat().st_mtime).isoformat() + "Z",
                "size_bytes": p.stat().st_size,
                "has_admin_key": bool(v.get("admin_key")),
                "has_inference_key": bool(v.get("inference_key")),
                "keys_count": len(v.get("keys", [])),
                "subkeys_count": len(d.get("subkeys", [])) if d else 0
            })

        add_item(self.backup_path, "mirror_local")
        if self.is_primary:
            add_item(USER_PROFILE_BACKUP, "mirror_user_profile")
            add_item(HOME_VENICE_BACKUP, "mirror_home")

        if self.snapshot_dir.exists():
            for sp in sorted(self.snapshot_dir.glob("vault_*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
                add_item(sp, "snapshot")

        items.sort(key=lambda x: x["modified_at"], reverse=True)
        return items

    def resolve_backup_path(self, path_or_name: str) -> Optional[Path]:
        """
        Maps a user-supplied backup path/filename to one of the vault's *known* backup files.
        Prevents restoring from arbitrary filesystem paths supplied over the network.
        """
        if not path_or_name:
            return None
        wanted = str(path_or_name).strip()
        for item in self.list_backups():
            if wanted in (item["path"], item["filename"]):
                return Path(item["path"])
        return None

    def restore_from_file(self, backup_path: Path) -> Dict[str, Any]:
        """
        Restores the active vault from a specific backup JSON file.
        Takes a precautionary pre-restore backup first.
        """
        p = Path(backup_path)
        if not p.exists():
            return {"success": False, "error": f"Backup file not found: {p}"}

        restored_data = _read_json_file(p)
        if not restored_data or not isinstance(restored_data, dict):
            return {"success": False, "error": f"Invalid backup file format: {p}"}

        # Precautionary backup of current state
        self.create_backup(label="pre_restore")

        self.data = restored_data
        self._auto_recall_missing_fields(self.data)
        self._save(force=True, create_snapshot=True)

        return {
            "success": True,
            "restored_from": str(p),
            "keys_count": len(self.get_venice_keys()),
            "subkeys_count": len(self.get_subkeys()),
            "has_admin_key": bool(self.get_venice_admin_key()),
            "has_inference_key": bool(self.get_venice_inference_key())
        }

    def auto_recall(self, sync_venice_remote: bool = True) -> Dict[str, Any]:
        """
        Executes a deep auto-recall scan across backup mirrors, agent configs,
        .env files, and environment variables. If admin_key is configured and
        sync_venice_remote=True, queries Venice API to sync all issued keys.
        """
        prev_admin = bool(self.get_venice_admin_key())
        prev_inf = bool(self.get_venice_inference_key())
        prev_keys = len(self.get_venice_keys())

        recalled = self._auto_recall_missing_fields(self.data)

        remote_synced_count = 0
        admin_key = self.get_venice_admin_key()
        if admin_key and sync_venice_remote:
            try:
                from core.venice_client import VeniceClient
                client = VeniceClient(
                    admin_key=admin_key,
                    base_url=self.data.get("venice", {}).get("base_url", "")
                )
                res = client.list_keys(admin_key=admin_key)
                if res.get("success") and "keys" in res:
                    remote_keys = res["keys"]
                    existing_keys = self.get_venice_keys()
                    merged_dict = {k.get("id"): k for k in existing_keys}
                    for rk in remote_keys:
                        rk_id = rk.get("id")
                        if rk_id:
                            merged_dict[rk_id] = {**merged_dict.get(rk_id, {}), **rk}
                    self.data.setdefault("venice", {})["keys"] = list(merged_dict.values())
                    remote_synced_count = len(remote_keys)
                    recalled = True
                    logger.info(f"Synchronized {remote_synced_count} keys from Venice API")
            except Exception as e:
                logger.warning(f"Remote Venice key sync during recall failed: {e}")

        if recalled:
            self._save(force=True, create_snapshot=True)

        return {
            "success": True,
            "recovered_admin_key": (not prev_admin) and bool(self.get_venice_admin_key()),
            "recovered_inference_key": (not prev_inf) and bool(self.get_venice_inference_key()),
            "remote_keys_synced": remote_synced_count,
            "total_venice_keys": len(self.get_venice_keys()),
            "total_subkeys": len(self.get_subkeys()),
            "has_admin_key": bool(self.get_venice_admin_key()),
            "has_inference_key": bool(self.get_venice_inference_key())
        }

    def get_backup_status(self) -> Dict[str, Any]:
        """Returns the health and status of primary vault and all backup mirrors."""
        snaps = self.list_backups()
        latest_mod = snaps[0]["modified_at"] if snaps else None
        return {
            "success": True,
            "vault_path": str(self.vault_path),
            "vault_exists": self.vault_path.exists(),
            "vault_size_bytes": self.vault_path.stat().st_size if self.vault_path.exists() else 0,
            "backup_mirror_exists": self.backup_path.exists(),
            "user_profile_mirror_exists": (USER_PROFILE_BACKUP.exists() or HOME_VENICE_BACKUP.exists()) if self.is_primary else False,
            "is_primary": self.is_primary,
            "total_snapshots": len([s for s in snaps if s["type"] == "snapshot"]),
            "last_backup_at": latest_mod,
            "has_admin_key": bool(self.get_venice_admin_key()),
            "has_inference_key": bool(self.get_venice_inference_key()),
            "keys_count": len(self.get_venice_keys()),
            "subkeys_count": len(self.get_subkeys()),
            "allocations_count": len(self.data.get("allocations", []))
        }

    # --- Venice Key Helpers ---

    def get_venice_admin_key(self) -> str:
        return os.environ.get("VENICE_ADMIN_KEY") or self.data.get("venice", {}).get("admin_key", "")

    def set_venice_admin_key(self, key: str) -> None:
        self.data.setdefault("venice", {})["admin_key"] = key.strip()
        self._save(force=True, create_snapshot=True)
        # If admin key was set, attempt to auto-sync keys from Venice API
        if key.strip():
            try:
                self.auto_recall(sync_venice_remote=True)
            except Exception:
                pass

    def get_venice_inference_key(self) -> str:
        return os.environ.get("VENICE_INFERENCE_KEY") or os.environ.get("VENICE_API_KEY") or self.data.get("venice", {}).get("inference_key", "")

    def set_venice_inference_key(self, key: str) -> None:
        self.data.setdefault("venice", {})["inference_key"] = key.strip()
        self._save(force=True, create_snapshot=True)

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
        self._save(force=True, create_snapshot=True)

    def remove_venice_key(self, key_id: str) -> bool:
        keys = self.data.setdefault("venice", {}).setdefault("keys", [])
        initial_len = len(keys)
        self.data["venice"]["keys"] = [k for k in keys if k.get("id") != key_id]
        if len(self.data["venice"]["keys"]) < initial_len:
            self._save(force=True, create_snapshot=True)
            return True
        return False

    # --- Telegram Agent Bot Helpers ---

    def get_telegram_master_token(self) -> str:
        return os.environ.get("TELEGRAM_BOT_TOKEN") or self.data.get("telegram", {}).get("master_bot_token", "")

    def set_telegram_master_token(self, token: str) -> None:
        self.data.setdefault("telegram", {})["master_bot_token"] = token
        self._save(force=True, create_snapshot=True)

    def get_authorized_chat_id(self) -> str:
        return str(os.environ.get("TELEGRAM_CHAT_ID") or self.data.get("telegram", {}).get("authorized_chat_id", ""))

    def set_authorized_chat_id(self, chat_id: str) -> None:
        self.data.setdefault("telegram", {})["authorized_chat_id"] = str(chat_id)
        self._save(force=True, create_snapshot=True)

    def get_agent_bots(self) -> Dict[str, Dict[str, Any]]:
        return self.data.get("telegram", {}).get("agent_bots", {})

    def get_agent_bot(self, agent_name: str) -> Optional[Dict[str, Any]]:
        return self.get_agent_bots().get(agent_name)

    def register_agent_bot(self, agent_name: str, bot_data: Dict[str, Any]) -> None:
        bots = self.data.setdefault("telegram", {}).setdefault("agent_bots", {})
        bot_data["agent_name"] = agent_name
        bot_data.setdefault("updated_at", datetime.utcnow().isoformat() + "Z")
        bots[agent_name] = bot_data
        self._save(force=True, create_snapshot=True)

    def remove_agent_bot(self, agent_name: str) -> bool:
        bots = self.data.setdefault("telegram", {}).setdefault("agent_bots", {})
        if agent_name in bots:
            del bots[agent_name]
            self._save(force=True, create_snapshot=True)
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
        self._save(create_snapshot=True)

    # --- Deployments ---

    def get_deployments(self) -> Dict[str, Dict[str, Any]]:
        return self.data.get("deployments", {})

    def register_deployment(self, name: str, config_path: str, venice_field: str = "", tg_field: str = "") -> None:
        self.data.setdefault("deployments", {})[name] = {
            "config_path": config_path,
            "venice_key_field": venice_field,
            "tg_token_field": tg_field
        }
        self._save(create_snapshot=True)

    # --- Security & Secure Pairing Code ---

    def get_pairing_code(self) -> str:
        """Returns the active pairing code, generating one if not set."""
        env_code = os.environ.get("SECURE_PAIRING_CODE")
        if env_code:
            return env_code.strip()
        sec = self.data.setdefault("security", {})
        code = sec.get("pairing_code")
        if not code:
            import secrets
            code = f"VK-{secrets.token_hex(4).upper()}"
            sec["pairing_code"] = code
            sec.setdefault("require_pairing", True)
            self._save(create_snapshot=True)
        return code

    def set_pairing_code(self, code: str) -> None:
        sec = self.data.setdefault("security", {})
        sec["pairing_code"] = code.strip()
        sec.setdefault("require_pairing", True)
        self._save(create_snapshot=True)

    def generate_new_pairing_code(self) -> str:
        """
        Rotates the pairing code. All browsers/clients holding the old code lose access.
        Note: if SECURE_PAIRING_CODE is set in the environment it still takes precedence.
        """
        code = f"VK-{secrets.token_hex(4).upper()}"
        self.set_pairing_code(code)
        return code

    def verify_pairing_code(self, candidate: str) -> bool:
        """Verifies candidate pairing code using constant-time comparison."""
        import hmac
        if not candidate:
            return False
        expected = self.get_pairing_code()
        return hmac.compare_digest(str(candidate).strip().encode("utf-8"), expected.encode("utf-8"))

    def is_pairing_required(self) -> bool:
        return self.data.get("security", {}).get("require_pairing", True)

    # --- Cloudflare DNS & Tunnel Config ---

    def get_cloudflare_config(self) -> Dict[str, Any]:
        return self.data.get("cloudflare", {
            "domain": os.environ.get("CLOUDFLARE_DOMAIN", "venice.vmu.cash"),
            "tunnel_name": os.environ.get("CLOUDFLARE_TUNNEL_NAME", "venice-tunnel"),
            "tunnel_token": os.environ.get("CLOUDFLARE_TUNNEL_TOKEN", "")
        })

    def set_cloudflare_config(self, domain: str, tunnel_name: str = "", tunnel_token: str = "") -> None:
        cf = self.data.setdefault("cloudflare", {})
        if domain:
            cf["domain"] = domain.strip()
        if tunnel_name:
            cf["tunnel_name"] = tunnel_name.strip()
        if tunnel_token:
            cf["tunnel_token"] = tunnel_token.strip()
        self._save(create_snapshot=True)

    # --- Delegated Sub-Keys ---

    def get_subkeys(self) -> List[Dict[str, Any]]:
        return self.data.get("subkeys", [])

    def store_subkey(self, subkey_info: Dict[str, Any]) -> None:
        subkeys = self.data.setdefault("subkeys", [])
        sk_id = subkey_info.get("id")
        updated = False
        for i, sk in enumerate(subkeys):
            if sk.get("id") == sk_id:
                subkeys[i] = subkey_info
                updated = True
                break
        if not updated:
            subkeys.insert(0, subkey_info)
        self._save(force=True, create_snapshot=True)

    def remove_subkey(self, subkey_id: str) -> bool:
        subkeys = self.data.setdefault("subkeys", [])
        initial_len = len(subkeys)
        self.data["subkeys"] = [s for s in subkeys if s.get("id") != subkey_id]
        if len(self.data["subkeys"]) < initial_len:
            self._save(force=True, create_snapshot=True)
            return True
        return False

    # --- Agent Key Allocations (venice.vmu.cash/claim/...) ---

    def mint_allocation(
        self,
        label: str,
        target_agent: str,
        allocated_keys_count: int = 1,
        valid_from: Optional[str] = None,
        valid_until: Optional[str] = None,
        quality_tier: str = "s",
        budget_usd: float = 0.25,
        limit_period: str = "DAY",
        target_node: str = "local",
        api_key: Optional[str] = None,
        raw_metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Mints a secure, claimable key allocation link (https://venice.vmu.cash/claim/<token>)
        for an agent without exposing the raw Venice API key directly.
        """
        alloc_id = f"alloc_{secrets.token_hex(6)}"
        claim_token = f"vclm_{secrets.token_hex(24)}"

        cf_domain = self.get_cloudflare_config().get("domain") or "venice.vmu.cash"
        cf_domain = cf_domain.strip().rstrip("/")
        claim_url = f"https://{cf_domain}/claim/{claim_token}"

        now_str = datetime.utcnow().isoformat() + "Z"
        v_from = valid_from if valid_from else now_str

        chosen_key = (api_key or "").strip()
        if not chosen_key:
            chosen_key = self.get_active_venice_key()

        allocation_record = {
            "id": alloc_id,
            "label": (label or f"Allocation for {target_agent}").strip(),
            "target_agent": (target_agent or "agent").strip(),
            "target_node": (target_node or "local").strip(),
            "claim_token": claim_token,
            "claim_url": claim_url,
            "allocated_keys_count": max(1, int(allocated_keys_count)),
            "claimed_keys_count": 0,
            "valid_from": v_from,
            "valid_until": valid_until if valid_until else None,
            "quality_tier": quality_tier.lower() if quality_tier else "s",
            "budget_usd": float(budget_usd) if budget_usd is not None else 0.25,
            "limit_period": limit_period.upper() if limit_period else "DAY",
            "status": "ACTIVE",
            "created_at": now_str,
            "api_key": chosen_key,
            "apiKey": chosen_key,
            "claims_history": [],
            "raw_metadata": raw_metadata or {}
        }

        allocations = self.data.setdefault("allocations", [])
        allocations.insert(0, allocation_record)
        self._save(force=True, create_snapshot=True)
        return allocation_record

    def _compute_allocation_status(self, alloc: Dict[str, Any]) -> str:
        """Determines real-time dynamic status based on claims and validity window."""
        base_status = alloc.get("status", "ACTIVE")
        if base_status == "REVOKED":
            return "REVOKED"

        allocated = alloc.get("allocated_keys_count", 1)
        claimed = alloc.get("claimed_keys_count", 0)
        if claimed >= allocated:
            return "CLAIMED"

        now = datetime.utcnow()
        dt_from = _parse_iso(alloc.get("valid_from"))
        if dt_from and now < dt_from:
            return "PENDING"

        dt_until = _parse_iso(alloc.get("valid_until"))
        if dt_until and now > dt_until:
            return "EXPIRED"

        return "ACTIVE"

    def get_allocations(self, include_secret: bool = False, target_agent: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lists key allocations, dynamically resolving status and masking secrets if requested."""
        raw_list = self.data.get("allocations", [])
        results = []
        for item in raw_list:
            if target_agent and item.get("target_agent") != target_agent:
                continue
            computed_status = self._compute_allocation_status(item)
            allocated = item.get("allocated_keys_count", 1)
            claimed = item.get("claimed_keys_count", 0)
            remaining = max(0, allocated - claimed)
            can_claim_now = (computed_status == "ACTIVE" and remaining > 0)

            rec = dict(item)
            rec["status"] = computed_status
            rec["remaining_claims"] = remaining
            rec["can_claim_now"] = can_claim_now

            if not include_secret:
                k = rec.get("api_key") or rec.get("apiKey") or ""
                rec["masked_key"] = f"***{k[-4:]}" if len(k) >= 4 else "***"
                rec.pop("api_key", None)
                rec.pop("apiKey", None)

            results.append(rec)
        return results

    def get_allocation(self, alloc_id_or_token: str, include_secret: bool = False) -> Optional[Dict[str, Any]]:
        """Finds allocation by ID, token, or full claim URL."""
        if not alloc_id_or_token:
            return None
        cleaned = alloc_id_or_token.strip().rstrip("/")
        token = cleaned.split("/")[-1] if "/" in cleaned else cleaned

        for item in self.data.get("allocations", []):
            if item.get("id") == token or item.get("claim_token") == token:
                computed_status = self._compute_allocation_status(item)
                allocated = item.get("allocated_keys_count", 1)
                claimed = item.get("claimed_keys_count", 0)
                remaining = max(0, allocated - claimed)
                can_claim_now = (computed_status == "ACTIVE" and remaining > 0)

                rec = dict(item)
                rec["status"] = computed_status
                rec["remaining_claims"] = remaining
                rec["can_claim_now"] = can_claim_now

                if not include_secret:
                    k = rec.get("api_key") or rec.get("apiKey") or ""
                    rec["masked_key"] = f"***{k[-4:]}" if len(k) >= 4 else "***"
                    rec.pop("api_key", None)
                    rec.pop("apiKey", None)

                return rec
        return None

    def inspect_allocation(self, claim_token_or_url: str) -> Dict[str, Any]:
        """
        Public/safe inspection: returns counts, dates, and claiming window
        without revealing the underlying secret Venice API key.
        """
        alloc = self.get_allocation(claim_token_or_url, include_secret=False)
        if not alloc:
            return {
                "success": False,
                "error": "Allocation not found. Please verify the claim link or token."
            }

        return {
            "success": True,
            "id": alloc["id"],
            "label": alloc["label"],
            "target_agent": alloc["target_agent"],
            "target_node": alloc.get("target_node", "local"),
            "claim_token": alloc["claim_token"],
            "claim_url": alloc["claim_url"],
            "allocated_keys_count": alloc["allocated_keys_count"],
            "claimed_keys_count": alloc.get("claimed_keys_count", 0),
            "remaining_claims": alloc["remaining_claims"],
            "valid_from": alloc.get("valid_from"),
            "valid_until": alloc.get("valid_until"),
            "can_claim_now": alloc["can_claim_now"],
            "status": alloc["status"],
            "quality_tier": alloc.get("quality_tier", "s"),
            "budget_usd": alloc.get("budget_usd", 0.25),
            "limit_period": alloc.get("limit_period", "DAY"),
            "created_at": alloc.get("created_at"),
            "total_claims": len(alloc.get("claims_history", []))
        }

    def claim_allocation(self, claim_token_or_url: str, agent_id: str = "", client_info: str = "") -> Dict[str, Any]:
        """
        Processes key claim by an agent. Validates token, claiming schedule,
        and availability. Decrements remaining claims and dispenses the Venice API key.
        """
        if not claim_token_or_url:
            return {"success": False, "error": "Missing claim token or URL."}

        cleaned = claim_token_or_url.strip().rstrip("/")
        token = cleaned.split("/")[-1] if "/" in cleaned else cleaned

        raw_alloc = None
        for item in self.data.get("allocations", []):
            if item.get("id") == token or item.get("claim_token") == token:
                raw_alloc = item
                break

        if not raw_alloc:
            return {"success": False, "error": "Invalid claim token. No matching allocation found."}

        if raw_alloc.get("status") == "REVOKED":
            return {"success": False, "error": "This allocation has been revoked by the administrator."}

        now = datetime.utcnow()
        dt_from = _parse_iso(raw_alloc.get("valid_from"))
        if dt_from and now < dt_from:
            return {
                "success": False,
                "error": f"Allocation is not yet claimable. Claiming opens at {raw_alloc.get('valid_from')}."
            }

        dt_until = _parse_iso(raw_alloc.get("valid_until"))
        if dt_until and now > dt_until:
            raw_alloc["status"] = "EXPIRED"
            self._save(force=True, create_snapshot=True)
            return {
                "success": False,
                "error": f"Allocation expired on {raw_alloc.get('valid_until')}."
            }

        allocated = int(raw_alloc.get("allocated_keys_count", 1))
        claimed = int(raw_alloc.get("claimed_keys_count", 0))
        if claimed >= allocated:
            raw_alloc["status"] = "CLAIMED"
            self._save(force=True, create_snapshot=True)
            return {
                "success": False,
                "error": f"All allocated keys ({allocated}) have already been claimed."
            }

        claimed += 1
        raw_alloc["claimed_keys_count"] = claimed
        if claimed >= allocated:
            raw_alloc["status"] = "CLAIMED"

        now_str = datetime.utcnow().isoformat() + "Z"
        claim_event = {
            "claimed_at": now_str,
            "agent_id": agent_id or raw_alloc.get("target_agent", "agent"),
            "client_info": client_info or "MCP/REST Request"
        }
        raw_alloc.setdefault("claims_history", []).append(claim_event)
        self._save(force=True, create_snapshot=True)

        dispensed_key = raw_alloc.get("api_key") or raw_alloc.get("apiKey") or self.get_active_venice_key()
        return {
            "success": True,
            "api_key": dispensed_key,
            "apiKey": dispensed_key,
            "allocation_id": raw_alloc["id"],
            "label": raw_alloc["label"],
            "target_agent": raw_alloc["target_agent"],
            "target_node": raw_alloc.get("target_node", "local"),
            "quality_tier": raw_alloc.get("quality_tier", "s"),
            "budget_usd": raw_alloc.get("budget_usd", 0.25),
            "limit_period": raw_alloc.get("limit_period", "DAY"),
            "allocated_keys_count": allocated,
            "claimed_keys_count": claimed,
            "remaining_claims": max(0, allocated - claimed),
            "status": raw_alloc["status"],
            "message": f"Successfully claimed key for '{agent_id or raw_alloc['target_agent']}'."
        }

    def revoke_allocation(self, alloc_id_or_token: str) -> bool:
        """Revokes an allocation, rendering it unclaimable."""
        if not alloc_id_or_token:
            return False
        cleaned = alloc_id_or_token.strip().rstrip("/")
        token = cleaned.split("/")[-1] if "/" in cleaned else cleaned

        for item in self.data.get("allocations", []):
            if item.get("id") == token or item.get("claim_token") == token:
                item["status"] = "REVOKED"
                self._save(force=True, create_snapshot=True)
                return True
        return False

    def delete_allocation(self, alloc_id_or_token: str) -> bool:
        """Permanently removes an allocation record."""
        if not alloc_id_or_token:
            return False
        cleaned = alloc_id_or_token.strip().rstrip("/")
        token = cleaned.split("/")[-1] if "/" in cleaned else cleaned

        allocations = self.data.setdefault("allocations", [])
        initial_len = len(allocations)
        self.data["allocations"] = [
            a for a in allocations if a.get("id") != token and a.get("claim_token") != token
        ]
        if len(self.data["allocations"]) < initial_len:
            self._save(force=True, create_snapshot=True)
            return True
        return False

    # --- Balance Alerts & Thresholds ---

    def get_balance_thresholds(self) -> Dict[str, float]:
        """Returns configured USD thresholds for low/out alerts."""
        thresholds = self.data.get("inference_thresholds", {})
        return {
            "low_usd": float(thresholds.get("low_usd", 1.0)),
            "out_usd": float(thresholds.get("out_usd", 0.05))
        }

    def set_balance_thresholds(self, low_usd: float = 1.0, out_usd: float = 0.05) -> Dict[str, float]:
        """Updates and persists USD thresholds for balance alerts."""
        self.data["inference_thresholds"] = {
            "low_usd": float(low_usd),
            "out_usd": float(out_usd)
        }
        self._save(force=True)
        return self.get_balance_thresholds()

    def get_last_balance_alert_state(self) -> Dict[str, Any]:
        """Returns the last alerted balance status and timestamp."""
        return self.data.get("last_balance_alert", {
            "status": "HEALTHY",
            "timestamp": 0.0,
            "usd": 0.0
        })

    def set_last_balance_alert_state(self, status: str, usd: float) -> None:
        """Records the latest alerted status to avoid alert spam across restarts."""
        self.data["last_balance_alert"] = {
            "status": status,
            "timestamp": time.time(),
            "usd": float(usd)
        }
        self._save()


def _parse_iso(iso_str: Optional[str]) -> Optional[datetime]:
    if not iso_str:
        return None
    s = str(iso_str).strip().rstrip("Z")
    if "+" in s:
        s = s.split("+")[0]
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None

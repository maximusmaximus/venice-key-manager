"""
Configuration Deployer for Venice.ai Keys and Telegram Bot Tokens.
Updates target agent configuration files (e.g. config.yaml, .env).
"""

import os
import re
import shutil
import yaml
from pathlib import Path
from typing import Dict, Any, Optional

DEFAULT_CONFIG_PATH = Path(os.environ.get("AGENT_CONFIG_PATH", "config.yaml"))


class ConfigDeployer:
    @staticmethod
    def _create_backup(target_path: Path) -> Path:
        backup = target_path.with_suffix(f"{target_path.suffix}.bak")
        shutil.copy2(target_path, backup)
        return backup

    @classmethod
    def deploy_venice_key(cls, key_string: str, target_path: Optional[Path] = None) -> Dict[str, Any]:
        """
        Deploys a Venice API key into a YAML configuration file under model.api_key.
        """
        path = Path(target_path or DEFAULT_CONFIG_PATH)
        if not path.exists():
            return {"success": False, "error": f"Target file does not exist: {path}"}

        try:
            backup = cls._create_backup(path)

            content = path.read_text(encoding="utf-8")
            pattern = r"(api_key\s*:\s*)([^\r\n]+)"
            if re.search(pattern, content):
                new_content = re.sub(pattern, rf"\g<1>{key_string.strip()}", content, count=1)
                path.write_text(new_content, encoding="utf-8")
            else:
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                data.setdefault("model", {})["api_key"] = key_string.strip()
                with open(path, "w", encoding="utf-8") as f:
                    yaml.safe_dump(data, f, default_flow_style=False)

            return {
                "success": True,
                "target": str(path),
                "backup": str(backup),
                "key_preview": f"{key_string[:12]}...{key_string[-6:]}" if len(key_string) > 20 else key_string
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @classmethod
    def deploy_telegram_token(cls, bot_token: str, target_path: Optional[Path] = None) -> Dict[str, Any]:
        """
        Deploys a Telegram bot token into a YAML configuration file under channels.telegram.bot_token.
        """
        path = Path(target_path or DEFAULT_CONFIG_PATH)
        if not path.exists():
            return {"success": False, "error": f"Target file does not exist: {path}"}

        try:
            backup = cls._create_backup(path)

            content = path.read_text(encoding="utf-8")
            pattern = r"(bot_token\s*:\s*)([^\r\n]+)"
            if re.search(pattern, content):
                new_content = re.sub(pattern, rf"\g<1>{bot_token.strip()}", content, count=1)
                path.write_text(new_content, encoding="utf-8")
            else:
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                channels = data.setdefault("channels", {})
                tg = channels.setdefault("telegram", {})
                tg["bot_token"] = bot_token.strip()
                tg["enabled"] = True
                with open(path, "w", encoding="utf-8") as f:
                    yaml.safe_dump(data, f, default_flow_style=False)

            return {
                "success": True,
                "target": str(path),
                "backup": str(backup),
                "token_preview": f"{bot_token[:10]}...{bot_token[-4:]}" if len(bot_token) > 15 else bot_token
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @classmethod
    def export_env_file(cls, output_path: Path, venice_key: str = "", tg_token: str = "") -> Dict[str, Any]:
        """Exports keys to an environment .env file."""
        lines = []
        if venice_key:
            lines.append(f"VENICE_API_KEY={venice_key.strip()}")
            lines.append(f"VENICE_INFERENCE_KEY={venice_key.strip()}")
        if tg_token:
            lines.append(f"TELEGRAM_BOT_TOKEN={tg_token.strip()}")

        try:
            output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return {"success": True, "path": str(output_path)}
        except Exception as e:
            return {"success": False, "error": str(e)}

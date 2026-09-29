"""
Venice.ai API Client for Key Management, Rate Limits, and Inference.
Complies with official Venice.ai REST API specifications.
"""

import time
import requests
from typing import Dict, Any, List, Optional


class VeniceClient:
    DEFAULT_BASE_URL = "https://api.venice.ai/api/v1"

    def __init__(self, admin_key: str = "", inference_key: str = "", base_url: str = ""):
        self.admin_key = admin_key
        self.inference_key = inference_key
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")

    def _get_headers(self, key: Optional[str] = None, require_admin: bool = False) -> Dict[str, str]:
        selected = key
        if not selected:
            if require_admin:
                selected = self.admin_key
            else:
                selected = self.inference_key or self.admin_key

        if not selected:
            raise ValueError(f"{'Admin' if require_admin else 'Inference or Admin'} API Key is not configured")

        return {
            "Authorization": f"Bearer {selected.strip()}",
            "Content-Type": "application/json"
        }

    # --- Rate Limits & Balances ---

    def get_rate_limits(self, key: Optional[str] = None) -> Dict[str, Any]:
        """
        Retrieves API key rate limits, live USD/DIEM balances, and API tier.
        Supported by both INFERENCE and ADMIN keys.
        """
        url = f"{self.base_url}/api_keys/rate_limits"
        headers = self._get_headers(key=key, require_admin=False)
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                return {
                    "success": True,
                    "balances": data.get("balances", {}),
                    "apiTier": data.get("apiTier", {}),
                    "accessPermitted": data.get("accessPermitted", True),
                    "keyExpiration": data.get("keyExpiration"),
                    "nextEpochBegins": data.get("nextEpochBegins"),
                    "rateLimitsCount": len(data.get("rateLimits", [])),
                    "raw": data
                }
            return {
                "success": False,
                "status_code": resp.status_code,
                "error": resp.text
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # --- API Key Management (ADMIN Key Required) ---

    def list_keys(self, admin_key: Optional[str] = None) -> Dict[str, Any]:
        """
        Lists all active API keys associated with the account.
        Requires ADMIN key.
        """
        url = f"{self.base_url}/api_keys"
        try:
            headers = self._get_headers(key=admin_key, require_admin=True)
        except ValueError as ve:
            return {"success": False, "error": str(ve), "requires_admin": True}

        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                return {"success": True, "keys": data, "count": len(data)}
            elif resp.status_code == 401:
                return {
                    "success": False,
                    "status_code": 401,
                    "error": "Admin API key required. Current key is unauthorized for key management.",
                    "requires_admin": True
                }
            return {"success": False, "status_code": resp.status_code, "error": resp.text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_key(
        self,
        description: str,
        key_type: str = "INFERENCE",
        limit_usd: Optional[float] = None,
        limit_period: str = "MONTH",
        model_privacy: str = "ALL",
        expires_at: Optional[str] = None,
        admin_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Creates a new API key (INFERENCE or ADMIN).
        Requires ADMIN key.
        """
        url = f"{self.base_url}/api_keys"
        try:
            headers = self._get_headers(key=admin_key, require_admin=True)
        except ValueError as ve:
            return {"success": False, "error": str(ve), "requires_admin": True}

        payload: Dict[str, Any] = {
            "apiKeyType": key_type.upper(),
            "description": description,
            "limitPeriod": limit_period,
            "modelPrivacy": model_privacy
        }

        if limit_usd is not None and limit_usd > 0:
            payload["consumptionLimit"] = {"usd": float(limit_usd)}

        if expires_at:
            payload["expiresAt"] = expires_at

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=20)
            if resp.status_code in (200, 201):
                data = resp.json()
                key_obj = data.get("data", data)
                return {"success": True, "key": key_obj}
            return {"success": False, "status_code": resp.status_code, "error": resp.text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_key(self, key_id: str, admin_key: Optional[str] = None) -> Dict[str, Any]:
        """Retrieves details of a specific API key by ID."""
        url = f"{self.base_url}/api_keys/{key_id}"
        try:
            headers = self._get_headers(key=admin_key, require_admin=True)
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                return {"success": True, "key": resp.json().get("data", resp.json())}
            return {"success": False, "status_code": resp.status_code, "error": resp.text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def delete_key(self, key_id: str, admin_key: Optional[str] = None) -> Dict[str, Any]:
        """Deletes/Revokes an API key by ID."""
        url = f"{self.base_url}/api_keys/{key_id}"
        try:
            headers = self._get_headers(key=admin_key, require_admin=True)
            resp = requests.delete(url, headers=headers, timeout=15)
            if resp.status_code in (200, 204):
                return {"success": True, "key_id": key_id, "message": "Key revoked successfully"}
            return {"success": False, "status_code": resp.status_code, "error": resp.text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # --- Models & Light Inference ---

    def list_models(self, key: Optional[str] = None) -> Dict[str, Any]:
        """Retrieves all available Venice models."""
        url = f"{self.base_url}/models"
        headers = self._get_headers(key=key, require_admin=False)
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                models = resp.json().get("data", [])
                model_ids = [m.get("id") for m in models if "id" in m]
                return {"success": True, "models": models, "model_ids": model_ids}
            return {"success": False, "status_code": resp.status_code, "error": resp.text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def test_inference(
        self,
        prompt: str,
        model: str = "deepseek-v4-flash",
        max_tokens: int = 120,
        temperature: float = 0.7,
        key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes a light inference request to verify key functionality,
        latency, and token consumption.
        """
        url = f"{self.base_url}/chat/completions"
        headers = self._get_headers(key=key, require_admin=False)

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature
        }

        start_time = time.time()
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            latency_ms = int((time.time() - start_time) * 1000)

            if resp.status_code == 200:
                data = resp.json()
                choice = data.get("choices", [{}])[0]
                message = choice.get("message", {})
                content = message.get("content") or ""
                reasoning = message.get("reasoning_content") or ""
                usage = data.get("usage", {})
                cost = data.get("cost", {})

                return {
                    "success": True,
                    "model": model,
                    "latency_ms": latency_ms,
                    "content": content,
                    "reasoning": reasoning,
                    "usage": usage,
                    "cost": cost,
                    "finish_reason": choice.get("finish_reason")
                }
            return {
                "success": False,
                "latency_ms": latency_ms,
                "status_code": resp.status_code,
                "error": resp.text
            }
        except Exception as e:
            latency_ms = int((time.time() - start_time) * 1000)
            return {"success": False, "latency_ms": latency_ms, "error": str(e)}

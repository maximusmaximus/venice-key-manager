import time
import logging
from typing import Dict, List, Optional, Any, Tuple
import httpx

from .config import config
from .models import (
    ApiKey,
    ConsumptionLimit,
    KeyCreateRequest,
    KeyCreatedResponse,
    KeyUpdateRequest,
    KeyCycleRequest,
    RateLimitsResponse,
    ModelInfo,
    ModelPricing,
    ModelCapabilities,
    InferenceTestResponse,
)
from .state import state_store

logger = logging.getLogger(__name__)


class VeniceClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self.api_key = api_key or config.venice_api_key
        self.base_url = (base_url or config.venice_base_url).rstrip("/")
        self.timeout = timeout

    def _headers(self, custom_key: Optional[str] = None) -> Dict[str, str]:
        key = custom_key or self.api_key
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "Venice-Key-Manager/1.0",
        }

    # =========================================================================
    # 1. API Keys CRUD
    # =========================================================================

    async def list_keys(self, category_filter: Optional[str] = None) -> List[ApiKey]:
        """Fetch all API keys from Venice and enrich with local metadata."""
        url = f"{self.base_url}/api_keys"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self._headers())
            if resp.status_code != 200:
                logger.error(f"Failed to list keys: HTTP {resp.status_code} - {resp.text}")
                resp.raise_for_status()
            data = resp.json().get("data", [])

        global_thresh = state_store.get_global_threshold()
        keys: List[ApiKey] = []

        for item in data:
            key_obj = ApiKey(**item)
            meta = state_store.get_key_meta(key_obj.id)
            key_obj.category = meta.get("category", "Default")
            key_obj.custom_threshold = meta.get("custom_threshold")

            # Calculate remaining USD in period and low balance flag
            if key_obj.consumptionLimits and key_obj.consumptionLimits.usd is not None:
                limit = float(key_obj.consumptionLimits.usd)
                used = 0.0
                if key_obj.currentPeriodUsage and key_obj.currentPeriodUsage.usd:
                    try:
                        used = float(key_obj.currentPeriodUsage.usd)
                    except ValueError:
                        used = 0.0
                remaining = max(0.0, limit - used)
                key_obj.remaining_usd = round(remaining, 4)

                threshold = key_obj.custom_threshold if key_obj.custom_threshold is not None else global_thresh
                key_obj.is_low_balance = remaining <= threshold
            else:
                key_obj.remaining_usd = None
                key_obj.is_low_balance = False

            if category_filter and category_filter.lower() != "all":
                if key_obj.category.lower() != category_filter.lower():
                    continue

            keys.append(key_obj)

        # Sort by creation date descending
        keys.sort(key=lambda k: k.createdAt or "", reverse=True)
        return keys

    async def get_key(self, key_id: str) -> Optional[ApiKey]:
        """Retrieve a single key by ID."""
        keys = await self.list_keys()
        for k in keys:
            if k.id == key_id:
                return k
        return None

    async def create_key(self, req: KeyCreateRequest) -> KeyCreatedResponse:
        """Create a new Venice API key with spending caps and metadata."""
        url = f"{self.base_url}/api_keys"
        payload: Dict[str, Any] = {
            "apiKeyType": req.apiKeyType,
            "description": req.description,
            "limitPeriod": req.limitPeriod,
        }
        if req.daily_usd is not None and req.daily_usd >= 0:
            payload["consumptionLimit"] = {"usd": float(req.daily_usd)}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=self._headers())
            if resp.status_code not in (200, 201):
                err = f"Venice key creation failed: HTTP {resp.status_code} - {resp.text}"
                logger.error(err)
                raise RuntimeError(err)
            resp_data = resp.json()
            kdata = resp_data.get("data", resp_data)

        new_token = kdata.get("apiKey") or kdata.get("key") or ""
        new_id = kdata.get("id") or kdata.get("keyId") or ""

        # Store category and custom threshold in local state
        state_store.update_key_meta(
            key_id=new_id,
            category=req.category,
            custom_threshold=req.custom_threshold,
        )

        return KeyCreatedResponse(
            apiKey=new_token,
            id=new_id,
            description=req.description,
            apiKeyType=req.apiKeyType,
            consumptionLimits=ConsumptionLimit(usd=req.daily_usd) if req.daily_usd is not None else None,
            limitPeriod=req.limitPeriod,
            category=req.category,
        )

    async def update_key(self, req: KeyUpdateRequest) -> bool:
        """Update spend limits, description, or metadata for an existing key."""
        url = f"{self.base_url}/api_keys"
        payload: Dict[str, Any] = {"id": req.id}

        if req.description is not None:
            payload["description"] = req.description
        if req.limitPeriod is not None:
            payload["limitPeriod"] = req.limitPeriod
        if req.daily_usd is not None:
            payload["consumptionLimit"] = {"usd": float(req.daily_usd)}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.patch(url, json=payload, headers=self._headers())
            if resp.status_code != 200:
                err = f"Venice key update failed: HTTP {resp.status_code} - {resp.text}"
                logger.error(err)
                raise RuntimeError(err)

        state_store.update_key_meta(
            key_id=req.id,
            category=req.category,
            custom_threshold=req.custom_threshold,
        )
        return True

    async def revoke_key(self, key_id: str) -> bool:
        """Revoke / delete an existing Venice API key."""
        url = f"{self.base_url}/api_keys?id={key_id}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.delete(url, headers=self._headers())
            if resp.status_code not in (200, 204):
                logger.warning(f"Venice key revocation returned HTTP {resp.status_code}: {resp.text}")
                return False

        state_store.remove_key_meta(key_id)
        return True

    async def cycle_key(self, req: KeyCycleRequest) -> Tuple[KeyCreatedResponse, bool]:
        """
        Cycle / rotate an API key:
        1. Reads old key specs and local metadata.
        2. Mints a replacement key with matching or updated parameters.
        3. Optionally revokes the old key.
        """
        old_key = await self.get_key(req.id)
        if not old_key:
            raise ValueError(f"Key with ID {req.id} not found.")

        meta = state_store.get_key_meta(req.id)
        category = meta.get("category", "Default")
        custom_threshold = meta.get("custom_threshold")

        description = req.new_description or old_key.description
        daily_usd = req.new_daily_usd
        if daily_usd is None and req.keep_limits and old_key.consumptionLimits:
            daily_usd = old_key.consumptionLimits.usd

        limit_period = old_key.limitPeriod or "EPOCH"
        api_key_type = old_key.apiKeyType or "INFERENCE"

        create_req = KeyCreateRequest(
            description=description,
            apiKeyType=api_key_type,
            daily_usd=daily_usd,
            limitPeriod=limit_period,
            category=category,
            custom_threshold=custom_threshold,
        )

        new_key_resp = await self.create_key(create_req)

        revoked_old = False
        if req.revoke_old:
            revoked_old = await self.revoke_key(req.id)

        return new_key_resp, revoked_old

    # =========================================================================
    # 2. Account Rates, Balances, & Models
    # =========================================================================

    async def get_rate_limits(self) -> RateLimitsResponse:
        """Fetch account balance, RPM/TPM limits, and epoch resets."""
        url = f"{self.base_url}/api_keys/rate_limits"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self._headers())
            if resp.status_code != 200:
                logger.error(f"Failed to fetch rate limits: HTTP {resp.status_code} - {resp.text}")
                resp.raise_for_status()
            data = resp.json().get("data", {})
            return RateLimitsResponse(**data, raw=data)

    async def list_models(self) -> List[ModelInfo]:
        """Fetch available models with capabilities and pricing."""
        url = f"{self.base_url}/models"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self._headers())
            if resp.status_code != 200:
                logger.error(f"Failed to fetch models: HTTP {resp.status_code} - {resp.text}")
                resp.raise_for_status()
            raw_models = resp.json().get("data", [])

        results: List[ModelInfo] = []
        for m in raw_models:
            spec = m.get("model_spec", {})
            pricing_raw = spec.get("pricing", {})
            caps_raw = spec.get("capabilities", {})

            pricing = ModelPricing(
                input=pricing_raw.get("input"),
                output=pricing_raw.get("output"),
                cache_input=pricing_raw.get("cache_input"),
            )

            caps = ModelCapabilities(
                supportsE2EE=bool(caps_raw.get("supportsE2EE")),
                supportsTeeAttestation=bool(caps_raw.get("supportsTeeAttestation")),
                supportsVision=bool(caps_raw.get("supportsVision")),
                supportsFunctionCalling=bool(caps_raw.get("supportsFunctionCalling")),
                supportsReasoning=bool(caps_raw.get("supportsReasoning")),
                supportsWebSearch=bool(caps_raw.get("supportsWebSearch")),
            )

            privacy = spec.get("privacy", "anonymized")
            if caps.supportsE2EE:
                privacy = "e2ee"

            results.append(ModelInfo(
                id=m.get("id", ""),
                name=spec.get("name", m.get("id", "")),
                description=spec.get("description", ""),
                privacy=privacy,
                type=m.get("type", "text"),
                context_length=m.get("context_length", 4096),
                pricing=pricing,
                capabilities=caps,
                offline=bool(spec.get("offline", False)),
            ))

        return results

    # =========================================================================
    # 3. Light Inference Verification
    # =========================================================================

    async def test_inference(
        self,
        prompt: str = "Respond with 'Venice API connection verified successfully' in 5 words.",
        model: str = "deepseek-v4-flash",
        api_key: Optional[str] = None,
    ) -> InferenceTestResponse:
        """Perform a fast, lightweight inference call to verify key functionality and latency."""
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 100,
            "temperature": 0.2,
        }

        start_time = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(url, json=payload, headers=self._headers(custom_key=api_key))
                latency = round((time.perf_counter() - start_time) * 1000, 2)

                if resp.status_code != 200:
                    return InferenceTestResponse(
                        success=False,
                        latency_ms=latency,
                        output="",
                        model=model,
                        error=f"HTTP {resp.status_code}: {resp.text}",
                    )

                data = resp.json()
                content = data["choices"][0]["message"]["content"].strip()
                usage = data.get("usage", {})

                return InferenceTestResponse(
                    success=True,
                    latency_ms=latency,
                    output=content,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                    model=model,
                )
        except Exception as e:
            latency = round((time.perf_counter() - start_time) * 1000, 2)
            return InferenceTestResponse(
                success=False,
                latency_ms=latency,
                output="",
                model=model,
                error=str(e),
            )

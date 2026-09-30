from typing import Dict, List, Optional, Any, Union
from pydantic import BaseModel, Field, field_validator
from datetime import datetime


class ConsumptionLimit(BaseModel):
    usd: Optional[float] = None
    diem: Optional[float] = None
    vcu: Optional[float] = None


class TrailingUsage(BaseModel):
    usd: Optional[str] = "0.0000"
    vcu: Optional[str] = "0.0000"
    diem: Optional[str] = "0.0000"


class PeriodUsage(BaseModel):
    usd: Optional[str] = "0.0000"
    diem: Optional[str] = "0.0000"


class KeyUsage(BaseModel):
    trailingSevenDays: Optional[TrailingUsage] = Field(default_factory=TrailingUsage)


class ApiKey(BaseModel):
    id: str
    apiKeyType: str = "INFERENCE"  # "INFERENCE" | "ADMIN"
    description: str = ""
    last6Chars: Optional[str] = None
    modelPrivacy: Optional[str] = "ALL"
    consumptionLimits: Optional[ConsumptionLimit] = Field(default_factory=ConsumptionLimit)
    limitPeriod: Optional[str] = "EPOCH"  # "EPOCH" | "MONTH" | "LIFETIME"
    createdAt: Optional[str] = None
    expiresAt: Optional[str] = None
    lastUsedAt: Optional[str] = None
    usage: Optional[KeyUsage] = Field(default_factory=KeyUsage)
    currentPeriodUsage: Optional[PeriodUsage] = Field(default_factory=PeriodUsage)
    
    # Client-side / local metadata enrichments
    category: str = "Default"
    custom_threshold: Optional[float] = None
    is_low_balance: bool = False
    remaining_usd: Optional[float] = None


class BalanceInfo(BaseModel):
    USD: float = 0.0
    DIEM: float = 0.0
    BUNDLED_CREDITS: float = 0.0


class ApiTier(BaseModel):
    id: str = "paid"
    isCharged: bool = True


class ModelRateLimit(BaseModel):
    amount: int
    type: str  # "RPM" | "TPM"


class ApiModelRateLimits(BaseModel):
    apiModelId: str
    rateLimits: List[ModelRateLimit] = Field(default_factory=list)


class RateLimitsResponse(BaseModel):
    accessPermitted: bool = False
    apiTier: Optional[ApiTier] = None
    balances: BalanceInfo = Field(default_factory=BalanceInfo)
    keyExpiration: Optional[str] = None
    nextEpochBegins: Optional[str] = None
    rateLimits: List[ApiModelRateLimits] = Field(default_factory=list)
    raw: Optional[Dict[str, Any]] = None


class ModelPricing(BaseModel):
    input: Optional[Dict[str, float]] = None
    output: Optional[Dict[str, float]] = None
    cache_input: Optional[Dict[str, float]] = None


class ModelCapabilities(BaseModel):
    supportsE2EE: bool = False
    supportsTeeAttestation: bool = False
    supportsVision: bool = False
    supportsFunctionCalling: bool = False
    supportsReasoning: bool = False
    supportsWebSearch: bool = False


class ModelInfo(BaseModel):
    id: str
    name: str = ""
    description: str = ""
    privacy: str = "anonymized"  # "anonymized" | "private" | "e2ee"
    type: str = "text"
    context_length: int = 4096
    pricing: Optional[ModelPricing] = None
    capabilities: Optional[ModelCapabilities] = None
    offline: bool = False


class KeyCreateRequest(BaseModel):
    description: str
    apiKeyType: str = "INFERENCE"  # "INFERENCE" | "ADMIN"
    daily_usd: Optional[float] = None
    limitPeriod: str = "EPOCH"  # "EPOCH" | "MONTH" | "LIFETIME"
    category: str = "Default"
    custom_threshold: Optional[float] = None

    @field_validator("description", mode="before")
    def validate_description(cls, v):
        if not v or not str(v).strip():
            raise ValueError("Key name or description is required and cannot be empty.")
        return str(v).strip()


class KeyCreatedResponse(BaseModel):
    apiKey: str
    id: str
    description: str
    apiKeyType: str
    consumptionLimits: Optional[ConsumptionLimit] = None
    limitPeriod: str
    category: str = "Default"
    endpoints: Optional[Dict[str, str]] = None


class KeyUpdateRequest(BaseModel):
    id: str
    description: Optional[str] = None
    daily_usd: Optional[float] = None
    limitPeriod: Optional[str] = None
    category: Optional[str] = None
    custom_threshold: Optional[float] = None


class KeyCycleRequest(BaseModel):
    id: str
    revoke_old: bool = True
    keep_limits: bool = True
    new_daily_usd: Optional[float] = None
    new_description: Optional[str] = None


class InferenceTestRequest(BaseModel):
    prompt: str = "Respond with 'Venice API connection verified successfully' in 5 words."
    model: str = "deepseek-v4-flash"
    api_key: Optional[str] = None  # If None, use master key


class InferenceTestResponse(BaseModel):
    success: bool
    latency_ms: float
    output: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model: str
    cost_usd_est: Optional[float] = None
    error: Optional[str] = None


class BackupExport(BaseModel):
    version: str = "1.0"
    exported_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    global_threshold: float = 0.20
    categories: List[str] = Field(default_factory=lambda: ["Default", "Production", "Agents", "Testing", "Telegram"])
    key_metadata: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    summary: Dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# Batch Operations Models
# =============================================================================

class BatchAuthTokenCreateRequest(BaseModel):
    prefix: str = "vkm_code_"
    count: int = 5
    ttl_hours: int = 168
    notes: Optional[str] = None


class BatchAuthTokenResponse(BaseModel):
    success: bool = True
    count: int
    prefix: str
    tokens: List[Dict[str, Any]] = Field(default_factory=list)


class BatchKeyCreateRequest(BaseModel):
    prefix: str = "agent-"
    count: int = 3
    daily_usd: Optional[float] = 0.50
    limitPeriod: str = "EPOCH"  # "EPOCH" | "MONTH" | "LIFETIME"
    category: str = "Default"
    apiKeyType: str = "INFERENCE"  # "INFERENCE" | "ADMIN"
    custom_threshold: Optional[float] = None


class BatchKeyCreateResponse(BaseModel):
    success: bool = True
    count: int
    prefix: str
    keys: List[KeyCreatedResponse] = Field(default_factory=list)


# =============================================================================
# External Gated Inference & Project Allocation Models
# =============================================================================

MODEL_TIER_ORDER = ["xs", "s", "m", "l", "xl"]

MODEL_TIER_MAPPING: Dict[str, Dict[str, Any]] = {
    "xs": {
        "tier": "xs",
        "label": "Extra Small (Ultra Fast / Low Cost)",
        "description": "Lowest latency, ideal for heartbeat checks, routing, and high-frequency classification",
        "default_model": "llama-3.2-3b",
        "models": ["llama-3.2-3b", "llama-3.2-1b", "qwen-2.5-coder-7b"],
        "cost_per_m_in": 0.05,
        "cost_per_m_out": 0.10,
    },
    "s": {
        "tier": "s",
        "label": "Small (High Speed / Efficient)",
        "description": "Fast generation, high throughput agents, code drafting, and summarization",
        "default_model": "deepseek-v4-flash",
        "models": ["deepseek-v4-flash", "mistral-small", "llama-3.1-8b"],
        "cost_per_m_in": 0.15,
        "cost_per_m_out": 0.60,
    },
    "m": {
        "tier": "m",
        "label": "Medium (Balanced Intelligence & Quality)",
        "description": "Balanced intelligence, strong multi-step tool execution, and complex analysis",
        "default_model": "llama-3.3-70b",
        "models": ["llama-3.3-70b", "qwen-2.5-coder-32b", "mistral-small-24b"],
        "cost_per_m_in": 0.40,
        "cost_per_m_out": 1.20,
    },
    "l": {
        "tier": "l",
        "label": "Large (Deep Reasoning & Complex Coding)",
        "description": "Advanced problem solving, chain-of-thought mathematical reasoning",
        "default_model": "deepseek-r1",
        "models": ["deepseek-r1", "qwen-2.5-72b-instruct"],
        "cost_per_m_in": 1.50,
        "cost_per_m_out": 4.00,
    },
    "xl": {
        "tier": "xl",
        "label": "Extra Large (Flagship 405B+ & Confidential Enclave)",
        "description": "Maximum parameter capability, confidential hardware enclave (E2EE) models",
        "default_model": "llama-3.1-405b",
        "models": ["llama-3.1-405b", "deepseek-r1", "claude-3-5-sonnet"],
        "cost_per_m_in": 3.00,
        "cost_per_m_out": 8.00,
    },
}


def is_tier_allowed(requested_tier: str, max_allowed_tier: str) -> bool:
    """Check if requested model tier is within maximum permitted tier."""
    req = requested_tier.lower().strip()
    max_t = max_allowed_tier.lower().strip()
    if req not in MODEL_TIER_ORDER or max_t not in MODEL_TIER_ORDER:
        return True
    return MODEL_TIER_ORDER.index(req) <= MODEL_TIER_ORDER.index(max_t)


def resolve_model_tier(model_name: str) -> str:
    """Determine the tier of a model or return direct tier if passed."""
    m = (model_name or "").lower().strip()
    if m in MODEL_TIER_ORDER:
        return m
    for tier, data in MODEL_TIER_MAPPING.items():
        if m == data["default_model"].lower() or any(m == alt.lower() for alt in data.get("models", [])):
            return tier
    if "405b" in m or "sonnet" in m or "opus" in m:
        return "xl"
    if "r1" in m or "72b" in m or "reasoning" in m:
        return "l"
    if "70b" in m or "32b" in m:
        return "m"
    if "flash" in m or "small" in m or "14b" in m or "8b" in m:
        return "s"
    if "3b" in m or "1b" in m or "mini" in m:
        return "xs"
    return "m"  # default tier


def get_model_for_tier(tier: str) -> str:
    """Return default Venice model for given size tier."""
    clean_t = tier.lower().strip()
    if clean_t in MODEL_TIER_MAPPING:
        return MODEL_TIER_MAPPING[clean_t]["default_model"]
    return "deepseek-v4-flash"


class ProjectCreateRequest(BaseModel):
    name: str
    description: Optional[str] = ""
    daily_limit_usd: float = 1.00
    weekly_limit_usd: Optional[float] = None
    default_sub_key_daily_usd: float = 0.25  # Default: 25 cents per project per day
    max_model_tier: str = "xl"

    @field_validator("name", mode="before")
    def validate_name(cls, v):
        if not v or not str(v).strip():
            raise ValueError("Project name is required and cannot be empty.")
        return str(v).strip()


class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    daily_limit_usd: Optional[float] = None
    weekly_limit_usd: Optional[float] = None
    default_sub_key_daily_usd: Optional[float] = None
    max_model_tier: Optional[str] = None
    status: Optional[str] = None  # "active" | "paused"


class ExternalKeyCreateRequest(BaseModel):
    project_id: str
    name: str
    daily_limit_usd: Optional[float] = None  # If None, uses project default_sub_key_daily_usd (0.25)
    weekly_limit_usd: Optional[float] = None
    limit_period: str = "DAY"  # "DAY" | "WEEK"
    max_model_tier: str = "xl"
    prefix: str = "vkm_ext_"
    notes: Optional[str] = None

    @field_validator("name", mode="before")
    def validate_name(cls, v):
        if not v or not str(v).strip():
            raise ValueError("External key name is required and cannot be empty.")
        return str(v).strip()


class ExternalKeyUpdateRequest(BaseModel):
    name: Optional[str] = None
    daily_limit_usd: Optional[float] = None
    weekly_limit_usd: Optional[float] = None
    max_model_tier: Optional[str] = None
    status: Optional[str] = None  # "active" | "paused" | "revoked"


class SubKeyCreateRequest(BaseModel):
    name: str
    amount_usd: Optional[float] = 0.25  # Default: 25 cents per project per day unless created by admin
    period: str = "DAY"  # "DAY" | "WEEK"
    max_model_tier: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("name", mode="before")
    def validate_name(cls, v):
        if not v or not str(v).strip():
            raise ValueError("Sub-key name is required and cannot be empty.")
        return str(v).strip()


class GatewayAllocationResponse(BaseModel):
    key_name: str
    project_id: str
    project_name: str
    key_type: str
    daily_limit_usd: float
    spent_today_usd: float
    remaining_today_usd: float
    limit_period: str
    max_model_tier: str
    status: str
    cloudflare_gateway_url: Optional[str] = None
    endpoints: Optional[Dict[str, str]] = None



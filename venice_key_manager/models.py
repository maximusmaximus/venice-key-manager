from typing import Dict, List, Optional, Any, Union
from pydantic import BaseModel, Field
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


class KeyCreatedResponse(BaseModel):
    apiKey: str
    id: str
    description: str
    apiKeyType: str
    consumptionLimits: Optional[ConsumptionLimit] = None
    limitPeriod: str
    category: str = "Default"


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

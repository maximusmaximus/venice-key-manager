"""
Model Quality Tiers for Venice.ai Inference Keys and Agent Allocations.
Provides tier hierarchy: xs < s < m < l < xl, model mappings, and gating checks.
"""

from typing import Dict, Any, List

MODEL_TIER_ORDER: List[str] = ["xs", "s", "m", "l", "xl"]

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
    req = (requested_tier or "m").lower().strip()
    max_t = (max_allowed_tier or "xl").lower().strip()
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
    return "m"


def get_model_for_tier(tier: str) -> str:
    """Return default Venice model for given size tier."""
    clean_t = (tier or "m").lower().strip()
    if clean_t in MODEL_TIER_MAPPING:
        return MODEL_TIER_MAPPING[clean_t]["default_model"]
    return "deepseek-v4-flash"

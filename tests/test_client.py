import pytest
from venice_key_manager.models import (
    ApiKey,
    ConsumptionLimit,
    PeriodUsage,
    KeyUsage,
    TrailingUsage,
    RateLimitsResponse,
    BalanceInfo,
)


def test_api_key_model_parsing():
    raw = {
        "id": "key-uuid-1",
        "apiKeyType": "INFERENCE",
        "description": "test-agent",
        "last6Chars": "ABC123",
        "modelPrivacy": "ALL",
        "consumptionLimits": {"usd": 2.0, "diem": None, "vcu": None},
        "limitPeriod": "EPOCH",
        "createdAt": "2026-09-23T12:00:00Z",
        "currentPeriodUsage": {"usd": "0.5000", "diem": "0.0000"},
        "usage": {
            "trailingSevenDays": {"usd": "1.2000", "vcu": "0.0000", "diem": "0.0000"}
        }
    }
    key = ApiKey(**raw)
    assert key.id == "key-uuid-1"
    assert key.apiKeyType == "INFERENCE"
    assert key.consumptionLimits.usd == 2.0
    assert key.currentPeriodUsage.usd == "0.5000"


def test_rate_limits_response():
    raw = {
        "accessPermitted": True,
        "apiTier": {"id": "paid", "isCharged": True},
        "balances": {"USD": 12.50, "DIEM": 0, "BUNDLED_CREDITS": 0},
        "nextEpochBegins": "2026-09-24T00:00:00.000Z",
        "rateLimits": [
            {
                "apiModelId": "deepseek-v4-flash",
                "rateLimits": [{"amount": 100, "type": "RPM"}]
            }
        ]
    }
    resp = RateLimitsResponse(**raw)
    assert resp.accessPermitted is True
    assert resp.balances.USD == 12.50
    assert len(resp.rateLimits) == 1
    assert resp.rateLimits[0].apiModelId == "deepseek-v4-flash"

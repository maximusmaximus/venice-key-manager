import pytest
from unittest.mock import AsyncMock, MagicMock
from venice_key_manager.report import DailyKeyReport
from venice_key_manager.models import ApiKey, RateLimitsResponse, BalanceInfo


@pytest.fixture
def mock_client():
    client = MagicMock()
    
    mock_balances = BalanceInfo(USD=1.45, DIEM=25.0)
    mock_rates = RateLimitsResponse(
        balances=mock_balances,
        accessPermitted=True,
        nextEpochBegins="2026-09-25T00:00:00Z"
    )
    client.get_rate_limits = AsyncMock(return_value=mock_rates)

    mock_keys = [
        ApiKey(
            id="key_1",
            description="Agent Worker Alpha",
            limitPeriod="EPOCH",
            consumptionLimits={"usd": 1.0},
            currentPeriodUsage={"usd": "0.35"},
            usage={"trailingSevenDays": {"usd": "2.10"}},
            category="Agents"
        ),
        ApiKey(
            id="key_2",
            description="Test Key Beta",
            limitPeriod="EPOCH",
            consumptionLimits={"usd": 0.20},
            currentPeriodUsage={"usd": "0.18"},
            usage={"trailingSevenDays": {"usd": "0.50"}},
            category="Testing"
        ),
    ]
    client.list_keys = AsyncMock(return_value=mock_keys)
    return client


@pytest.mark.asyncio
async def test_generate_report_data(mock_client):
    reporter = DailyKeyReport(client=mock_client)
    data = await reporter.generate_report_data()

    assert data["account"]["balance_usd"] == 1.45
    assert data["summary"]["total_keys"] == 2
    assert data["summary"]["total_spend_today"] == 0.53
    assert data["summary"]["total_spend_7d"] == 2.60
    assert "Agents" in data["categories"]
    assert len(data["top_consumers"]) == 2


@pytest.mark.asyncio
async def test_format_markdown(mock_client):
    reporter = DailyKeyReport(client=mock_client)
    data = await reporter.generate_report_data()
    md = reporter.format_markdown(data)

    assert "VENICE.AI DAILY KEY OPERATIONS & USAGE REPORT" in md
    assert "$1.4500" in md
    assert "Agent Worker Alpha" in md
    assert "Test Key Beta" in md

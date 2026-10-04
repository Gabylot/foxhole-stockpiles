"""Tests for output coordinator module.

This module contains comprehensive tests for the OutputCoordinator which orchestrates
output handling by delegating to configured handlers.
"""

from unittest.mock import patch

import pytest

from foxhole_stockpiles.core.settings.sections.output import (
    ConsoleHandlerSettings,
    DiscordHandlerSettings,
    FileHandlerSettings,
    JsonFormatSettings,
    OutputHandlerConfig,
    OutputSettings,
    ReturnHandlerSettings,
    WebhookHandlerSettings,
)
from foxhole_stockpiles.enums.auth_type import AuthType
from foxhole_stockpiles.enums.stockpile_type import StockpileType
from foxhole_stockpiles.handlers.discord import DiscordOutputHandler
from foxhole_stockpiles.models.stockpile import Stockpile
from foxhole_stockpiles.models.stockpile_item import StockpileItem
from foxhole_stockpiles.services.output_coordinator import OutputCoordinator


@pytest.fixture
def sample_stockpile() -> Stockpile:
    """Create a sample stockpile for testing.

    Returns:
        Stockpile: A configured stockpile instance with test data for output testing.
    """
    items = [
        StockpileItem(quantity=100, code="BasicMaterialsIcon", confidence=0.95),
        StockpileItem(quantity=50, code="PetrolIcon", confidence=0.87),
        StockpileItem(quantity=75, code="DieselIcon", confidence=0.92),
    ]

    return Stockpile(
        name="Test Stockpile",
        type=StockpileType.SEAPORT,
        items=items,
        shard="TEST",
        ingame_timestamp="Day 1,000, 1000 Hours",
        resolution="1920x1080",
    )


class TestOutputCoordinator:
    """Test cases for OutputCoordinator."""

    def test_output_coordinator_initialization(self) -> None:
        """Test output coordinator initialization with handlers."""
        output_settings = OutputSettings(
            handlers=[
                OutputHandlerConfig(
                    name="API Response",
                    format=JsonFormatSettings(),
                    handler=ReturnHandlerSettings(),
                )
            ]
        )
        coordinator = OutputCoordinator(output_settings=output_settings)

        assert isinstance(coordinator, OutputCoordinator)
        assert coordinator.output_settings is not None
        assert len(coordinator.output_settings.handlers) == 1

    @pytest.mark.asyncio
    async def test_handle_output_return(self, sample_stockpile: Stockpile) -> None:
        """Test RETURN handler processing.

        Args:
            sample_stockpile (Stockpile): Sample stockpile data from fixture.
        """
        output_settings = OutputSettings(
            handlers=[
                OutputHandlerConfig(
                    name="API Response",
                    format=JsonFormatSettings(),
                    handler=ReturnHandlerSettings(),
                )
            ]
        )
        coordinator = OutputCoordinator(output_settings=output_settings)

        result = await coordinator.handle_output([sample_stockpile])

        # Should return dict with stockpiles list from the return handler
        assert isinstance(result, dict)
        assert "stockpiles" in result
        assert len(result["stockpiles"]) == 1
        assert result["stockpiles"][0]["name"] == "Test Stockpile"
        assert result["stockpiles"][0]["type"] == "Seaport"
        assert len(result["stockpiles"][0]["items"]) == 3

    @pytest.mark.asyncio
    async def test_handle_output_file(self, sample_stockpile: Stockpile) -> None:
        """Test FILE handler processing.

        Args:
            sample_stockpile (Stockpile): Sample stockpile data from fixture.
        """
        output_settings = OutputSettings(
            handlers=[
                OutputHandlerConfig(
                    name="File Output",
                    format=JsonFormatSettings(),
                    handler=FileHandlerSettings(path="output.json"),
                )
            ]
        )
        coordinator = OutputCoordinator(output_settings=output_settings)

        with patch("foxhole_stockpiles.handlers.file.FileOutputHandler.handle") as mock_handle:
            mock_handle.return_value = None

            result = await coordinator.handle_output([sample_stockpile])

            # No return handler configured, should return None
            assert result is None

    @pytest.mark.asyncio
    async def test_handle_output_multiple_handlers(self, sample_stockpile: Stockpile) -> None:
        """Test multiple handler processing.

        Args:
            sample_stockpile (Stockpile): Sample stockpile data from fixture.
        """
        output_settings = OutputSettings(
            handlers=[
                OutputHandlerConfig(
                    name="API Response",
                    format=JsonFormatSettings(),
                    handler=ReturnHandlerSettings(),
                ),
                OutputHandlerConfig(
                    name="File Backup",
                    format=JsonFormatSettings(),
                    handler=FileHandlerSettings(path="backup.json"),
                ),
            ]
        )
        coordinator = OutputCoordinator(output_settings=output_settings)

        with patch("foxhole_stockpiles.handlers.file.FileOutputHandler.handle") as mock_file_handle:
            mock_file_handle.return_value = None

            result = await coordinator.handle_output([sample_stockpile])

            # Should return result from the return handler
            assert isinstance(result, dict)
            assert "stockpiles" in result
            assert result["stockpiles"][0]["name"] == "Test Stockpile"
            # File handler should also be called
            mock_file_handle.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_output_no_handlers(self, sample_stockpile: Stockpile) -> None:
        """Test with no handlers configured.

        Args:
            sample_stockpile (Stockpile): Sample stockpile data from fixture.
        """
        output_settings = OutputSettings(handlers=[])
        coordinator = OutputCoordinator(output_settings=output_settings)

        result = await coordinator.handle_output([sample_stockpile])

        # No handlers, should return None
        assert result is None

    @pytest.mark.asyncio
    async def test_handle_output_webhook_with_token(self, sample_stockpile: Stockpile) -> None:
        """Test WEBHOOK handler with token passes through response.

        Args:
            sample_stockpile (Stockpile): Sample stockpile data from fixture.
        """
        output_settings = OutputSettings(
            handlers=[
                OutputHandlerConfig(
                    name="Webhook",
                    format=JsonFormatSettings(),
                    handler=WebhookHandlerSettings(
                        url="https://example.com/webhook",
                        auth_type=AuthType.BEARER,
                        token="test-token",
                    ),
                )
            ]
        )
        coordinator = OutputCoordinator(output_settings=output_settings)

        with patch(
            "foxhole_stockpiles.handlers.webhook.WebhookOutputHandler.handle"
        ) as mock_handle:
            mock_handle.return_value = {"status": "success"}

            result = await coordinator.handle_output([sample_stockpile], token="override-token")

            mock_handle.assert_called_once_with(
                stockpiles=[sample_stockpile], token="override-token"
            )
            # Webhook response is now passed through to caller
            assert result == {"status": "success"}

    @pytest.mark.asyncio
    async def test_handle_output_handler_error_continues(self, sample_stockpile: Stockpile) -> None:
        """Test that handler errors don't stop other handlers.

        Args:
            sample_stockpile (Stockpile): Sample stockpile data from fixture.
        """
        output_settings = OutputSettings(
            handlers=[
                OutputHandlerConfig(
                    name="Failing Handler",
                    format=JsonFormatSettings(),
                    handler=ConsoleHandlerSettings(),
                ),
                OutputHandlerConfig(
                    name="API Response",
                    format=JsonFormatSettings(),
                    handler=ReturnHandlerSettings(),
                ),
            ]
        )
        coordinator = OutputCoordinator(output_settings=output_settings)

        with patch(
            "foxhole_stockpiles.handlers.console.ConsoleOutputHandler.handle"
        ) as mock_console:
            mock_console.side_effect = Exception("Handler failed")

            # Should still return result from return handler even though console failed
            result = await coordinator.handle_output([sample_stockpile])

            assert isinstance(result, dict)
            assert "stockpiles" in result
            assert result["stockpiles"][0]["name"] == "Test Stockpile"

    @pytest.mark.asyncio
    async def test_return_handler_takes_precedence(self, sample_stockpile: Stockpile) -> None:
        """Test that first return handler result is used.

        Args:
            sample_stockpile (Stockpile): Sample stockpile data from fixture.
        """
        output_settings = OutputSettings(
            handlers=[
                OutputHandlerConfig(
                    name="First Return",
                    format=JsonFormatSettings(),
                    handler=ReturnHandlerSettings(),
                ),
                OutputHandlerConfig(
                    name="Second Return",
                    format=JsonFormatSettings(),
                    handler=ReturnHandlerSettings(),
                ),
            ]
        )
        coordinator = OutputCoordinator(output_settings=output_settings)

        result = await coordinator.handle_output([sample_stockpile])

        # First return handler result is used
        assert isinstance(result, dict)
        assert "stockpiles" in result
        assert result["stockpiles"][0]["name"] == "Test Stockpile"

    @pytest.mark.asyncio
    async def test_webhook_first_returns_webhook_response(
        self, sample_stockpile: Stockpile
    ) -> None:
        """Test that webhook response is returned when webhook is first.

        When webhook handler is configured before return handler, the webhook's
        response should be passed through to the client.

        Args:
            sample_stockpile (Stockpile): Sample stockpile data from fixture.
        """
        output_settings = OutputSettings(
            handlers=[
                OutputHandlerConfig(
                    name="Webhook First",
                    format=JsonFormatSettings(),
                    handler=WebhookHandlerSettings(
                        url="https://example.com/webhook",
                    ),
                ),
                OutputHandlerConfig(
                    name="Return Handler",
                    format=JsonFormatSettings(),
                    handler=ReturnHandlerSettings(),
                ),
            ]
        )
        coordinator = OutputCoordinator(output_settings=output_settings)

        with patch(
            "foxhole_stockpiles.handlers.webhook.WebhookOutputHandler.handle"
        ) as mock_webhook:
            mock_webhook.return_value = {"webhook": "response", "processed": True}

            result = await coordinator.handle_output([sample_stockpile])

            # Webhook response is used because it's first
            assert result == {"webhook": "response", "processed": True}

    @pytest.mark.asyncio
    async def test_return_first_ignores_webhook_response(self, sample_stockpile: Stockpile) -> None:
        """Test that return handler response is used when it's first.

        When return handler is configured before webhook handler, the return
        handler's response should be passed through (webhook response ignored).

        Args:
            sample_stockpile (Stockpile): Sample stockpile data from fixture.
        """
        output_settings = OutputSettings(
            handlers=[
                OutputHandlerConfig(
                    name="Return Handler",
                    format=JsonFormatSettings(),
                    handler=ReturnHandlerSettings(),
                ),
                OutputHandlerConfig(
                    name="Webhook Second",
                    format=JsonFormatSettings(),
                    handler=WebhookHandlerSettings(
                        url="https://example.com/webhook",
                    ),
                ),
            ]
        )
        coordinator = OutputCoordinator(output_settings=output_settings)

        with patch(
            "foxhole_stockpiles.handlers.webhook.WebhookOutputHandler.handle"
        ) as mock_webhook:
            mock_webhook.return_value = {"webhook": "response"}

            result = await coordinator.handle_output([sample_stockpile])

            # Return handler response is used because it's first
            assert isinstance(result, dict)
            assert "stockpiles" in result
            assert result["stockpiles"][0]["name"] == "Test Stockpile"
            assert "webhook" not in result

    @pytest.mark.asyncio
    async def test_handle_output_multiple_stockpiles(self, sample_stockpile: Stockpile) -> None:
        """Test handling multiple stockpiles at once.

        Args:
            sample_stockpile (Stockpile): Sample stockpile data from fixture.
        """
        # Create a second stockpile
        second_stockpile = Stockpile(
            name="Second Stockpile",
            type=StockpileType.STORAGE_DEPOT,
            items=[
                StockpileItem(quantity=200, code="SulfurIcon", confidence=0.99),
            ],
            shard="TEST",
            resolution="1920x1080",
        )

        output_settings = OutputSettings(
            handlers=[
                OutputHandlerConfig(
                    name="API Response",
                    format=JsonFormatSettings(),
                    handler=ReturnHandlerSettings(),
                )
            ]
        )
        coordinator = OutputCoordinator(output_settings=output_settings)

        result = await coordinator.handle_output([sample_stockpile, second_stockpile])

        # Should return both stockpiles
        assert isinstance(result, dict)
        assert "stockpiles" in result
        assert len(result["stockpiles"]) == 2
        assert result["stockpiles"][0]["name"] == "Test Stockpile"
        assert result["stockpiles"][1]["name"] == "Second Stockpile"


class TestDiscordHandler:
    """Test suite for Discord handler dispatch through the coordinator."""

    def test_creates_discord_handler(self) -> None:
        """A discord handler config produces a DiscordOutputHandler."""
        from foxhole_stockpiles.handlers.discord import DiscordOutputHandler

        settings = OutputSettings(
            handlers=[
                OutputHandlerConfig(
                    name="Discord",
                    handler=DiscordHandlerSettings(url="https://discord.com/api/webhooks/123/tok"),
                )
            ]
        )
        coordinator = OutputCoordinator(settings)
        handler = coordinator._create_handler(settings.handlers[0])
        assert isinstance(handler, DiscordOutputHandler)

    @pytest.mark.asyncio
    async def test_discord_response_is_returned(self, sample_stockpile: Stockpile) -> None:
        """The Discord result is surfaced as the coordinator response."""
        from unittest.mock import AsyncMock

        settings = OutputSettings(
            handlers=[
                OutputHandlerConfig(
                    name="Discord",
                    handler=DiscordHandlerSettings(url="https://discord.com/api/webhooks/123/tok"),
                )
            ]
        )
        coordinator = OutputCoordinator(settings)

        with patch.object(DiscordOutputHandler, "handle", new_callable=AsyncMock) as handle:
            handle.return_value = {"status": "ok", "sent": 1}
            result = await coordinator.handle_output([sample_stockpile])

        assert result == {"status": "ok", "sent": 1}
        handle.assert_awaited_once()

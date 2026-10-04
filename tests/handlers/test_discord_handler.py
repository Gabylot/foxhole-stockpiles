"""Tests for handlers.discord module."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from foxhole_stockpiles.core.settings.sections.output import DiscordHandlerSettings
from foxhole_stockpiles.handlers.discord import DiscordOutputHandler
from foxhole_stockpiles.models.stockpile import Stockpile
from foxhole_stockpiles.models.stockpile_item import StockpileItem

_URL = "https://discord.com/api/webhooks/123/tok"


def _stockpile(name: str = "Depot") -> Stockpile:
    """Build a stockpile for handler tests.

    Args:
        name (str): Stockpile name.

    Returns:
        Stockpile: The constructed stockpile.
    """
    return Stockpile(name=name, items=[StockpileItem(code="A", quantity=5)])


def _ok() -> MagicMock:
    """Build a mock successful response.

    Returns:
        MagicMock: A 204 response mock.
    """
    response = MagicMock()
    response.status_code = 204
    response.raise_for_status.return_value = None
    return response


def _http_error(status: int = 500) -> MagicMock:
    """Build a mock failing response.

    Args:
        status (int): HTTP status code.

    Returns:
        MagicMock: A response mock raising HTTPStatusError.
    """
    response = MagicMock()
    response.status_code = status
    response.text = "nope"
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "failed", request=MagicMock(), response=response
    )
    return response


class TestDiscordOutputHandlerHandle:
    """Test suite for DiscordOutputHandler.handle."""

    @pytest.mark.asyncio
    async def test_posts_image(self) -> None:
        """A stockpile is rendered and posted."""
        handler = DiscordOutputHandler(DiscordHandlerSettings(url=_URL))
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            post.return_value = _ok()
            result = await handler.handle([_stockpile()])

        assert result == {"status": "ok", "sent": 1}
        assert post.await_count == 1

    @pytest.mark.asyncio
    async def test_missing_url(self) -> None:
        """No webhook URL reports a message and posts nothing."""
        handler = DiscordOutputHandler(DiscordHandlerSettings())
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            result = await handler.handle([_stockpile()])

        assert result == {"message": "FS: Discord webhook URL is not set"}
        assert post.await_count == 0

    @pytest.mark.asyncio
    async def test_no_stockpiles(self) -> None:
        """An empty batch reports a message."""
        handler = DiscordOutputHandler(DiscordHandlerSettings(url=_URL))
        result = await handler.handle([])
        assert result == {"message": "No stockpiles to send"}

    @pytest.mark.asyncio
    async def test_http_failure_reported(self) -> None:
        """A failed post surfaces the error and sends nothing."""
        handler = DiscordOutputHandler(DiscordHandlerSettings(url=_URL))
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            post.return_value = _http_error(404)
            result = await handler.handle([_stockpile()])

        assert "404" in result["message"]
        assert result["sent"] == 0

    @pytest.mark.asyncio
    async def test_templates_are_applied(self) -> None:
        """Headline and message templates reach the connector."""
        settings = DiscordHandlerSettings(
            url=_URL,
            headline="{name} ({type})",
            message="Scan of {name}",
        )
        handler = DiscordOutputHandler(settings)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            post.return_value = _ok()
            await handler.handle([_stockpile("Westgate")])

        assert post.await_args.kwargs["data"]["content"] == "Scan of Westgate"

    @pytest.mark.asyncio
    async def test_filename_is_sanitised(self) -> None:
        """Unsafe characters in the stockpile name never reach the filename."""
        handler = DiscordOutputHandler(DiscordHandlerSettings(url=_URL))
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            post.return_value = _ok()
            await handler.handle([_stockpile("Bad/Name:Here")])

        filename = post.await_args.kwargs["files"]["file"][0]
        assert "/" not in filename
        assert filename.endswith(".png")

    @pytest.mark.asyncio
    async def test_batch_sends_all_without_cooldown(self) -> None:
        """With throttling off, every stockpile in a batch is posted."""
        handler = DiscordOutputHandler(DiscordHandlerSettings(url=_URL))
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            post.return_value = _ok()
            result = await handler.handle([_stockpile("A"), _stockpile("B")])

        assert result["sent"] == 2
        assert post.await_count == 2

    @pytest.mark.asyncio
    async def test_batch_stops_after_first_when_throttled(self) -> None:
        """With a cooldown set, a batch only posts the first stockpile."""
        handler = DiscordOutputHandler(DiscordHandlerSettings(url=_URL, min_interval_minutes=15))
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            post.return_value = _ok()
            result = await handler.handle([_stockpile("A"), _stockpile("B"), _stockpile("C")])

        assert result["sent"] == 1
        assert post.await_count == 1


class TestAggregate:
    """Test suite for the aggregate setting."""

    @pytest.mark.asyncio
    async def test_aggregate_posts_one_image(self) -> None:
        """Aggregate mode collapses a batch into a single post."""
        handler = DiscordOutputHandler(DiscordHandlerSettings(url=_URL, aggregate=True))
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            post.return_value = _ok()
            result = await handler.handle([_stockpile("A"), _stockpile("B"), _stockpile("C")])

        assert result["sent"] == 1
        assert post.await_count == 1

    @pytest.mark.asyncio
    async def test_default_is_one_post_per_stockpile(self) -> None:
        """Without the setting, the batch behaviour is unchanged."""
        handler = DiscordOutputHandler(DiscordHandlerSettings(url=_URL))
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            post.return_value = _ok()
            result = await handler.handle([_stockpile("A"), _stockpile("B")])

        assert result["sent"] == 2
        assert post.await_count == 2

    @pytest.mark.asyncio
    async def test_aggregate_sums_quantities(self) -> None:
        """The merged image totals the quantities of every stockpile."""
        handler = DiscordOutputHandler(DiscordHandlerSettings(url=_URL, aggregate=True))
        stockpiles = [
            Stockpile(name="A", items=[StockpileItem(code="Bomb", quantity=10)]),
            Stockpile(name="B", items=[StockpileItem(code="Bomb", quantity=5)]),
        ]
        with (
            patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post,
            patch.object(
                handler._renderer, "render_png", side_effect=lambda sp, headline=None: b"png"
            ) as render,
        ):
            post.return_value = _ok()
            await handler.handle(stockpiles)

        rendered = render.call_args.args[0]
        assert rendered.items == [StockpileItem(code="Bomb", quantity=15)]

    @pytest.mark.asyncio
    async def test_aggregate_single_stockpile_still_posts(self) -> None:
        """A one-stockpile scan still produces its image."""
        handler = DiscordOutputHandler(DiscordHandlerSettings(url=_URL, aggregate=True))
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            post.return_value = _ok()
            result = await handler.handle([_stockpile("A")])

        assert result["sent"] == 1
        assert post.await_count == 1


class TestCooldown:
    """Test suite for the cooldown behaviour."""

    @pytest.mark.asyncio
    async def test_skips_within_window(self) -> None:
        """A recent post blocks the next one."""
        settings = DiscordHandlerSettings(
            url=_URL,
            min_interval_minutes=15,
            last_sent_at=datetime.now(UTC) - timedelta(minutes=2),
        )
        handler = DiscordOutputHandler(settings)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            result = await handler.handle([_stockpile()])

        assert result["status"] == "skipped"
        assert post.await_count == 0

    @pytest.mark.asyncio
    async def test_allows_after_window(self) -> None:
        """Once the window passes, posting resumes."""
        settings = DiscordHandlerSettings(
            url=_URL,
            min_interval_minutes=15,
            last_sent_at=datetime.now(UTC) - timedelta(minutes=30),
        )
        handler = DiscordOutputHandler(settings)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            post.return_value = _ok()
            result = await handler.handle([_stockpile()])

        assert result == {"status": "ok", "sent": 1}
        assert post.await_count == 1

    @pytest.mark.asyncio
    async def test_naive_timestamp_treated_as_utc(self) -> None:
        """A naive timestamp from JSON still throttles instead of erroring."""
        settings = DiscordHandlerSettings(
            url=_URL,
            min_interval_minutes=15,
            last_sent_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1),
        )
        handler = DiscordOutputHandler(settings)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock):
            result = await handler.handle([_stockpile()])

        assert result["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_no_timestamp_posts(self) -> None:
        """Never having posted means no throttling."""
        handler = DiscordOutputHandler(DiscordHandlerSettings(url=_URL, min_interval_minutes=15))
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            post.return_value = _ok()
            result = await handler.handle([_stockpile()])

        assert result["sent"] == 1

    @pytest.mark.asyncio
    async def test_zero_interval_never_throttles(self) -> None:
        """A zero interval disables throttling entirely."""
        settings = DiscordHandlerSettings(
            url=_URL,
            min_interval_minutes=0,
            last_sent_at=datetime.now(UTC),
        )
        handler = DiscordOutputHandler(settings)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            post.return_value = _ok()
            result = await handler.handle([_stockpile()])

        assert result["sent"] == 1

    @pytest.mark.asyncio
    async def test_failure_does_not_arm_cooldown(self) -> None:
        """A failed post leaves the cooldown unrecorded."""
        handler = DiscordOutputHandler(DiscordHandlerSettings(url=_URL, min_interval_minutes=15))
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            post.return_value = _http_error(500)
            result = await handler.handle([_stockpile()])

        assert result["sent"] == 0


class TestCooldownPersistence:
    """Test suite for persisting the cooldown to the config file."""

    @pytest.fixture
    def config_file(self, tmp_path: Path) -> Path:
        """Write a minimal config containing a Discord handler.

        Args:
            tmp_path (Path): pytest-provided temp directory.

        Returns:
            Path: Path to the written config file.
        """
        path = tmp_path / "config.json"
        path.write_text(
            json.dumps(
                {
                    "config_version": 15,
                    "output": {
                        "handlers": [
                            {
                                "name": "Discord",
                                "format": {"type": "json"},
                                "handler": {
                                    "type": "discord",
                                    "url": _URL,
                                    "min_interval_minutes": 15,
                                },
                            }
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )
        return path

    @pytest.mark.asyncio
    async def test_success_persists_timestamp(self, config_file: Path) -> None:
        """A successful post writes last_sent_at into the config."""
        handler = DiscordOutputHandler(
            DiscordHandlerSettings(url=_URL, min_interval_minutes=15),
            config_path=config_file,
        )
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            post.return_value = _ok()
            await handler.handle([_stockpile()])

        saved = json.loads(config_file.read_text(encoding="utf-8"))
        handler_cfg = saved["output"]["handlers"][0]["handler"]
        assert "last_sent_at" in handler_cfg
        # Existing settings are preserved.
        assert handler_cfg["url"] == _URL
        assert handler_cfg["min_interval_minutes"] == 15

    @pytest.mark.asyncio
    async def test_cooldown_survives_restart(self, config_file: Path) -> None:
        """A handler rebuilt from the saved config is still throttled."""
        from foxhole_stockpiles.core.settings.sections.output.handler_config import (
            OutputHandlerConfig,
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            post.return_value = _ok()
            await DiscordOutputHandler(
                DiscordHandlerSettings(url=_URL, min_interval_minutes=15),
                config_path=config_file,
            ).handle([_stockpile()])

        saved = json.loads(config_file.read_text(encoding="utf-8"))
        reloaded = OutputHandlerConfig.model_validate(saved["output"]["handlers"][0])

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            post.return_value = _ok()
            result = await DiscordOutputHandler(reloaded.handler, config_path=config_file).handle(
                [_stockpile()]
            )

        assert result["status"] == "skipped"
        assert post.await_count == 0

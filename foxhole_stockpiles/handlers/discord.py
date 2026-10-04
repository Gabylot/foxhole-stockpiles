"""Discord output handler - renders a stockpile and posts it to a webhook."""

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from foxhole_stockpiles.connectors.discord import DiscordConnector
from foxhole_stockpiles.core.settings.discord_throttle import record_sent
from foxhole_stockpiles.core.settings.sections.output import DiscordHandlerSettings
from foxhole_stockpiles.handlers.base_handler import BaseOutputDestinationHandler
from foxhole_stockpiles.models.stockpile import Stockpile
from foxhole_stockpiles.services.image_renderer import (
    StockpileImageRenderer,
    render_template,
)
from foxhole_stockpiles.services.stockpile_aggregator import aggregate

# Characters that cannot appear in a filename on any supported platform.
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


class DiscordOutputHandler(BaseOutputDestinationHandler):
    """Renders stockpile results as images and posts them to Discord."""

    def __init__(
        self,
        discord_settings: DiscordHandlerSettings,
        config_path: Path | None = None,
    ) -> None:
        """Initialize the Discord output handler.

        Args:
            discord_settings (DiscordHandlerSettings): Discord configuration.
            config_path (Path | None): Config file to persist the cooldown
                timestamp in. Defaults to the platform config location.
        """
        self.logger = logging.getLogger(__name__)
        self._settings = discord_settings
        self._url = discord_settings.url
        self._config_path = config_path
        self._renderer = StockpileImageRenderer(
            font_path=Path(discord_settings.font_path) if discord_settings.font_path else None
        )
        self._connector = DiscordConnector(discord_settings.url)

    def _cooldown_remaining(self, now: datetime) -> timedelta | None:
        """Return how much cooldown is left, or None when a post is allowed.

        Args:
            now (datetime): Current time.

        Returns:
            timedelta | None: Remaining cooldown, or None if throttling is off
                or the window has passed.
        """
        interval = self._settings.min_interval_minutes
        if interval <= 0:
            return None

        last_sent = self._settings.last_sent_at
        if last_sent is None:
            return None

        # Config timestamps round-trip through JSON, so they may be naive.
        if last_sent.tzinfo is None:
            last_sent = last_sent.replace(tzinfo=UTC)

        elapsed = now - last_sent
        cooldown = timedelta(minutes=interval)
        if elapsed >= cooldown:
            return None
        return cooldown - elapsed

    def _interval_after(self, sent_at: datetime) -> timedelta | None:
        """Return the full cooldown window after a send, or None if disabled.

        Args:
            sent_at (datetime): When the post was sent.

        Returns:
            timedelta | None: The configured cooldown, or None when throttling
                is off.
        """
        if self._settings.min_interval_minutes <= 0:
            return None
        return timedelta(minutes=self._settings.min_interval_minutes)

    @staticmethod
    def _skipped(remaining: timedelta) -> dict[str, Any]:
        """Build the response returned when the cooldown blocks a post.

        Args:
            remaining (timedelta): How much cooldown is left.

        Returns:
            dict[str, Any]: A skipped-status response naming the wait.
        """
        minutes = max(1, int(remaining.total_seconds() // 60) + 1)
        return {
            "status": "skipped",
            "message": f"Discord cooldown active, next post in ~{minutes} min",
            "sent": 0,
        }

    @staticmethod
    def _filename(stockpile: Stockpile) -> str:
        """Build a safe attachment filename for a stockpile.

        Args:
            stockpile (Stockpile): The stockpile being posted.

        Returns:
            str: A filename ending in ``.png``.
        """
        raw = stockpile.name or stockpile.type or "stockpile"
        safe = _UNSAFE_FILENAME_CHARS.sub("_", raw).strip("_") or "stockpile"
        return f"{safe[:60]}.png"

    async def handle(self, stockpiles: list[Stockpile], **kwargs: Any) -> dict[str, Any]:
        """Render and post stockpile images to the Discord webhook.

        The first stockpile that posts successfully sets the cooldown for the
        rest of the run, so a batch never floods the channel. A skipped post is
        reported back rather than silently dropped.

        With ``aggregate`` enabled every stockpile is merged into a single
        image first, so the run posts at most one message.

        Args:
            stockpiles (list[Stockpile]): The stockpile data to send.
            **kwargs: Additional parameters (unused).

        Returns:
            dict[str, Any]: ``{"status": "ok", "sent": N}`` when posted, or a
                message describing why nothing was sent.
        """
        if not self._url:
            return {"message": DiscordConnector.DEFAULT_MESSAGE_NO_URL}

        if not stockpiles:
            return {"message": "No stockpiles to send"}

        now = datetime.now(tz=UTC)
        cooldown_until = self._cooldown_remaining(now)
        if cooldown_until is not None:
            return self._skipped(cooldown_until)

        sent = 0
        # The cooldown is enforced again inside this loop: the settings object
        # is only re-read between scans, so a batch must not post every
        # stockpile in one go.
        cooldown_until = None

        # In aggregate mode every stockpile collapses into one image, so the
        # batch is a single post rather than one per stockpile.
        batch: list[Stockpile] = [aggregate(stockpiles)] if self._settings.aggregate else stockpiles

        for stockpile in batch:
            if cooldown_until is not None:
                self.logger.info(
                    "Skipping remaining stockpiles, cooldown still active: %s",
                    self._skipped(cooldown_until)["message"],
                )
                break

            headline = render_template(self._settings.headline, stockpile)
            message = render_template(self._settings.message, stockpile)

            image_data = await asyncio.to_thread(self._renderer.render_png, stockpile, headline)
            result = await self._connector.send_image(
                image_data=image_data,
                filename=self._filename(stockpile),
                content=message,
                username=self._settings.username,
                avatar_url=self._settings.avatar_url,
            )

            if result.get("status") == "ok":
                sent += 1
                # Record on success so a failed post never starts the cooldown.
                sent_at = datetime.now(tz=UTC)
                await asyncio.to_thread(record_sent, self._config_path, self._url, sent_at)
                cooldown_until = self._interval_after(sent_at)
            else:
                self.logger.error(
                    "Discord post failed for %s: %s", stockpile.name or stockpile.type, result
                )
                if sent == 0:
                    return {"message": result.get("message", "Discord post failed"), "sent": 0}

        return {"status": "ok", "sent": sent}

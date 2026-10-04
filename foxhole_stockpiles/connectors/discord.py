"""Discord webhook connector."""

import logging
from typing import Any, Final

import httpx
from httpx import AsyncClient, ConnectTimeout

from foxhole_stockpiles.connectors.webhook import async_retry_on_connect_timeout

# Discord refuses message content longer than this.
MAX_CONTENT_LENGTH: Final[int] = 2000


class DiscordConnector:
    """Posts rendered stockpile images to a Discord webhook."""

    DEFAULT_MESSAGE_NO_URL: Final[str] = "FS: Discord webhook URL is not set"
    DEFAULT_ERROR_PREFIX: Final[str] = "FS: Error sending stockpile image to Discord"

    def __init__(self, url: str | None) -> None:
        """Initialize the Discord connector.

        Args:
            url (str | None): The Discord webhook URL to post to.
        """
        self._url = url
        self._logger = logging.getLogger(__name__)
        self._client = AsyncClient(timeout=30.0)

    @staticmethod
    def _truncate_content(content: str | None) -> str | None:
        """Trim message content to the length Discord accepts.

        Args:
            content (str | None): Raw message content.

        Returns:
            str | None: The content, truncated to the limit, or None if there
                was nothing to send.
        """
        if not content:
            return None
        return content[:MAX_CONTENT_LENGTH]

    @async_retry_on_connect_timeout(max_retries=3, delay=2)
    async def send_image(
        self,
        image_data: bytes,
        filename: str = "stockpile.png",
        content: str | None = None,
        username: str | None = None,
        avatar_url: str | None = None,
    ) -> dict[str, Any]:
        """Post an image to the configured Discord webhook.

        Args:
            image_data (bytes): PNG-encoded image to upload.
            filename (str): Name shown for the uploaded file.
            content (str | None): Optional message text alongside the image.
            username (str | None): Optional webhook display-name override.
            avatar_url (str | None): Optional webhook avatar override.

        Returns:
            dict[str, Any]: ``{"status": "ok"}`` on success, otherwise
                ``{"message": <reason>}`` describing what went wrong.

        Raises:
            ConnectTimeout: If the connection times out after every retry.
        """
        if not self._url:
            self._logger.info("Discord webhook URL is not configured")
            return {"message": self.DEFAULT_MESSAGE_NO_URL}

        if not image_data:
            return {"message": "FS: No image data to send"}

        payload: dict[str, str] = {}
        text = self._truncate_content(content)
        if text:
            payload["content"] = text
        if username:
            payload["username"] = username
        if avatar_url:
            payload["avatar_url"] = avatar_url

        try:
            response = await self._client.post(
                url=self._url,
                data=payload or None,
                files={"file": (filename, image_data, "image/png")},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            detail = e.response.text[:200]
            self._logger.error("Discord rejected the post: HTTP %s", e.response.status_code)
            return {"message": f"HTTP {e.response.status_code} error from Discord: {detail}"}
        except ConnectTimeout:
            raise
        except Exception as e:  # noqa: BLE001 - report any failure as a response message
            error_message = f"{self.DEFAULT_ERROR_PREFIX}: ({type(e).__name__}, {e})"
            self._logger.error(error_message)
            return {"message": error_message}

        return {"status": "ok"}

    async def close(self) -> None:
        """Close the persistent HTTP client.

        Should be called when the connector is no longer needed to free resources.
        """
        if not self._client.is_closed:
            await self._client.aclose()

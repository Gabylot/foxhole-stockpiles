"""Tests for connectors.discord module."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from foxhole_stockpiles.connectors.discord import MAX_CONTENT_LENGTH, DiscordConnector

_URL = "https://discord.com/api/webhooks/123/tok"


def _ok_response() -> MagicMock:
    """Build a mock 204 response.

    Returns:
        MagicMock: A response mock that accepts raise_for_status.
    """
    response = MagicMock()
    response.status_code = 204
    response.raise_for_status.return_value = None
    return response


def _error_response(status: int, text: str = "Unknown Webhook") -> MagicMock:
    """Build a mock response that fails raise_for_status.

    Args:
        status (int): HTTP status code to report.
        text (str): Response body text.

    Returns:
        MagicMock: A response mock raising HTTPStatusError.
    """
    response = MagicMock()
    response.status_code = status
    response.text = text
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "failed", request=MagicMock(), response=response
    )
    return response


class TestDiscordConnectorSendImage:
    """Test suite for DiscordConnector.send_image."""

    @pytest.mark.asyncio
    async def test_send_image_posts_multipart(self) -> None:
        """A successful post uploads the image as a multipart file."""
        connector = DiscordConnector(_URL)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            post.return_value = _ok_response()
            result = await connector.send_image(b"PNGDATA", filename="depot.png")

        assert result == {"status": "ok"}
        assert post.await_count == 1
        kwargs = post.await_args.kwargs
        assert kwargs["url"] == _URL
        assert kwargs["files"]["file"][0] == "depot.png"
        assert kwargs["files"]["file"][1] == b"PNGDATA"
        # No fields were requested, so no form payload is sent.
        assert kwargs["data"] is None

    @pytest.mark.asyncio
    async def test_send_image_includes_optional_fields(self) -> None:
        """Content, username and avatar are forwarded as form fields."""
        connector = DiscordConnector(_URL)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            post.return_value = _ok_response()
            await connector.send_image(
                b"PNG",
                content="hello",
                username="Logi",
                avatar_url="https://cdn.example/a.png",
            )

        data = post.await_args.kwargs["data"]
        assert data["content"] == "hello"
        assert data["username"] == "Logi"
        assert data["avatar_url"] == "https://cdn.example/a.png"

    @pytest.mark.asyncio
    async def test_send_image_truncates_long_content(self) -> None:
        """Over-long content is cut to Discord's limit instead of being rejected."""
        connector = DiscordConnector(_URL)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            post.return_value = _ok_response()
            await connector.send_image(b"PNG", content="x" * (MAX_CONTENT_LENGTH + 500))

        content = post.await_args.kwargs["data"]["content"]
        assert len(content) == MAX_CONTENT_LENGTH

    @pytest.mark.asyncio
    async def test_send_image_without_url(self) -> None:
        """A missing webhook URL is reported without making a request."""
        connector = DiscordConnector(None)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            result = await connector.send_image(b"PNG")

        assert result == {"message": DiscordConnector.DEFAULT_MESSAGE_NO_URL}
        assert post.await_count == 0

    @pytest.mark.asyncio
    async def test_send_image_without_data(self) -> None:
        """Empty image data is reported without making a request."""
        connector = DiscordConnector(_URL)
        result = await connector.send_image(b"")
        assert result == {"message": "FS: No image data to send"}

    @pytest.mark.asyncio
    async def test_send_image_http_error(self) -> None:
        """An HTTP error surfaces the status and body."""
        connector = DiscordConnector(_URL)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            post.return_value = _error_response(404)
            result = await connector.send_image(b"PNG")

        assert "404" in result["message"]
        assert "Unknown Webhook" in result["message"]

    @pytest.mark.asyncio
    async def test_send_image_transport_error(self) -> None:
        """A transport failure is reported as a message, not raised."""
        connector = DiscordConnector(_URL)
        with patch(
            "httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=RuntimeError("boom")
        ):
            result = await connector.send_image(b"PNG")

        assert DiscordConnector.DEFAULT_ERROR_PREFIX in result["message"]

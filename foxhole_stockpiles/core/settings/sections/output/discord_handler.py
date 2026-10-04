"""Discord image output handler settings."""

import re
from datetime import datetime
from typing import ClassVar, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from foxhole_stockpiles.enums.output_handler_type import OutputHandlerType

# Discord webhook URLs look like:
#   https://discord.com/api/webhooks/<webhook_id>/<webhook_token>
# The same path also exists on the canary/ptb domains and the legacy
# discordapp.com host, so any discord sub-domain is accepted.
_DISCORD_WEBHOOK_RE = re.compile(
    r"^https://(?:[a-z0-9-]+\.)*discord(?:app)?\.com/api/webhooks/\S+$",
    re.IGNORECASE,
)

# Upper bound on the cooldown so a typo cannot silently mute the handler for
# years. 24h is generous enough for any real "don't spam the channel" need.
MAX_MIN_INTERVAL_MINUTES = 24 * 60


class DiscordHandlerSettings(BaseModel):
    """Settings for the Discord image output handler.

    Rows in the rendered image show quantities only, since a scan result
    carries no demand data.
    """

    type: OutputHandlerType = Field(default=OutputHandlerType.DISCORD, description="Handler type")
    url: str | None = Field(
        description="Discord webhook URL to post the generated image to",
        default=None,
    )
    username: str | None = Field(
        description="Override for the webhook's display name",
        default=None,
    )
    avatar_url: str | None = Field(
        description="Override for the webhook's avatar image URL",
        default=None,
    )
    headline: str | None = Field(
        description=(
            "Image headline. Supports placeholders: "
            "{name}, {type}, {hex}, {ingame_timestamp}, {timestamp}, "
            "{resolution}, {item_count}, {total_items}"
        ),
        default=None,
    )
    message: str | None = Field(
        description=(
            "Message content posted next to the image. Supports the same "
            "placeholders as the headline."
        ),
        default=None,
    )
    aggregate: bool = Field(
        description=(
            "Combine every stockpile from a scan into one image instead of "
            "posting one image per stockpile. Quantities for the same item "
            "are summed; crated and loose stock stay separate."
        ),
        default=False,
    )
    font_path: str | None = Field(
        description=(
            "Path to a TrueType font file used to draw the image. Falls back to "
            "common system fonts, then Pillow's built-in font."
        ),
        default=None,
    )
    min_interval_minutes: float = Field(
        description=(
            "Minimum number of minutes between posts to this channel, to avoid "
            "spamming it. 0 disables throttling."
        ),
        default=0.0,
        ge=0.0,
    )
    last_sent_at: datetime | None = Field(
        description=(
            "Timestamp of the last successful post. Persisted so the cooldown "
            "survives restarts. Managed automatically; do not set by hand."
        ),
        default=None,
    )

    model_config = ConfigDict(extra="forbid")

    ALLOWED_URL_SCHEMES: ClassVar[frozenset[str]] = frozenset({"http", "https"})

    @field_validator("min_interval_minutes")
    @classmethod
    def reject_non_finite_interval(cls, value: float) -> float:
        """Reject NaN/inf cooldowns, which would silently disable throttling.

        Args:
            value: The configured cooldown in minutes.

        Returns:
            float: The validated cooldown.

        Raises:
            ValueError: If the cooldown is NaN or infinite.
        """
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("min_interval_minutes must be a finite number")
        if value > MAX_MIN_INTERVAL_MINUTES:
            raise ValueError(f"min_interval_minutes must be at most {MAX_MIN_INTERVAL_MINUTES}")
        return value

    @model_validator(mode="after")
    def validate_url(self) -> Self:
        """Validate that the configured URL is a Discord webhook URL.

        Returns:
            Self: The validated instance.

        Raises:
            ValueError: If the URL is not a valid Discord webhook endpoint.
        """
        if self.url is None:
            return self

        scheme = urlsplit(self.url).scheme.lower()
        if scheme not in self.ALLOWED_URL_SCHEMES:
            raise ValueError(
                f"Discord URL scheme '{scheme}' is not allowed; "
                f"must be one of {sorted(self.ALLOWED_URL_SCHEMES)}"
            )
        if not _DISCORD_WEBHOOK_RE.match(self.url):
            raise ValueError(
                "Discord URL must look like https://discord.com/api/webhooks/<id>/<token>"
            )
        return self

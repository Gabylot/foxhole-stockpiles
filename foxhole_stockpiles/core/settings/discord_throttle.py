"""Persistence for the Discord output handler's send timestamp.

The cooldown must survive restarts, and the handler is rebuilt from settings on
every scan, so the "last sent" timestamp is stored in the config file next to the
handler that owns it and rewritten in place after a successful post.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from foxhole_stockpiles.core.settings import get_settings
from foxhole_stockpiles.core.settings.config_path import default_config_path

logger = logging.getLogger(__name__)


def _find_discord_handlers(config: dict[str, Any], url: str) -> list[dict[str, Any]]:
    """Collect the Discord handler entries in a config that match a webhook URL.

    Args:
        config (dict[str, Any]): Parsed config data.
        url (str): The webhook URL to match.

    Returns:
        list[dict[str, Any]]: Mutable handler dicts sharing that URL.
    """
    output = config.get("output")
    if not isinstance(output, dict):
        return []

    handlers = output.get("handlers")
    if not isinstance(handlers, list):
        return []

    matches: list[dict[str, Any]] = []
    for entry in handlers:
        if not isinstance(entry, dict):
            continue
        handler = entry.get("handler")
        if not isinstance(handler, dict):
            continue
        if handler.get("type") == "discord" and handler.get("url") == url:
            matches.append(handler)
    return matches


def record_sent(config_path: Path | None, url: str, sent_at: datetime) -> None:
    """Store the time a Discord webhook was last posted to.

    The config file is rewritten with the new timestamp; all other settings are
    preserved. Failures are logged rather than raised, since a scan should not
    fail just because a cooldown stamp could not be written.

    Args:
        config_path (Path | None): Config file to update. Defaults to the
            platform config location.
        url (str): The webhook URL that was posted to.
        sent_at (datetime): When the post succeeded.
    """
    path = config_path or default_config_path()
    if not path.exists():
        logger.debug("Config file %s not found, skipping throttle timestamp", path)
        return

    try:
        with path.open(encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Could not read config to record Discord send time: %s", e)
        return

    matches = _find_discord_handlers(config, url)
    if not matches:
        logger.debug("No Discord handler for %s found in config", url)
        return

    stamp = sent_at.isoformat()
    for handler in matches:
        handler["last_sent_at"] = stamp

    try:
        # The config can hold a plaintext webhook URL, so keep it owner-only.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        path.chmod(0o600)

        # Make the next settings load observe the new timestamp.
        get_settings.cache_clear()
        logger.debug("Recorded Discord send time %s for %s", stamp, url)
    except OSError as e:
        logger.warning("Could not persist Discord send time: %s", e)

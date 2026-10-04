# Discord Image Output

The **discord** output handler renders each scan as an image and posts it
straight into a Discord channel. Use it when you want a readable picture of a
stockpile in your logistics channel, without wiring up a bot.

> For machine-readable JSON, keep using the [webhook handler](webhooks.md). This
> handler is for humans: it posts a picture.

## Configuration

Add a handler with `"type": "discord"` to `output.handlers`:

```json
{
  "output": {
    "handlers": [
      {
        "name": "Discord Image",
        "format": { "type": "json" },
        "handler": {
          "type": "discord",
          "url": "https://discord.com/api/webhooks/123456789/your-token",
          "headline": "{name} ({type})",
          "message": "Scan of {name} in {hex}",
          "min_interval_minutes": 15
        }
      }
    ]
  }
}
```

To get the webhook URL in Discord: **Server Settings → Integrations → Webhooks →
New Webhook**, then **Copy Webhook URL**.

## Settings

| Setting | Type | Required | Description |
|---------|------|----------|-------------|
| `type` | string | Yes | Must be `"discord"` |
| `url` | string | No | Discord webhook URL. Must be a `discord.com/api/webhooks/...` URL |
| `username` | string\|null | No | Overrides the display name on the webhook's messages |
| `avatar_url` | string\|null | No | Overrides the webhook's avatar image |
| `message` | string\|null | No | Text posted next to the image |
| `headline` | string\|null | No | Title drawn at the top of the image; empty falls back to the stockpile name |
| `aggregate` | bool | No | Combine every stockpile from a scan into one image instead of one image per stockpile. Default `false` |
| `font_path` | string\|null | No | Path to a `.ttf`/`.otf` font used to draw the image |
| `min_interval_minutes` | float | No | Minimum minutes between posts. `0` (default) disables throttling |
| `last_sent_at` | string\|null | No | Managed automatically; do not set by hand |

The `format` block is always forced to `json`, since the handler renders the
image itself and ignores the text format.

## What the image shows

The image has:

- a headline (your `headline` template, or the stockpile name, or its type if
  the stockpile has no name),
- items grouped by catalog category, packed into columns,
- one row per item with its **display name and quantity**,
- a footer with the total item count and the time it was generated.

Quantities are the only numbers shown. A scan result carries no demand data, so
there is nothing to render against a target.

Items whose quantity could not be read show `?` instead of a number and are left
out of the total. Crated stock is named `<item> Crate` and grouped under a
`<category> Crates` heading. Display names come from
the catalog, so configure `database_builder.catalog_file` for readable names —
without it, raw item codes are shown.

No icons are drawn: this project ships no item images, so rows are text only.

## Aggregating stockpiles

By default each stockpile from a scan gets its own image. Set `aggregate` to
`true` to merge them all into a single image instead:

```json
"handler": { "type": "discord", "url": "...", "aggregate": true }
```

A scan of 30 stockpiles then posts one image rather than 30. Items are merged on
their code, so the image has **one row per distinct item** and quantities are
summed — a region holding 400 distinct items produces 400 rows, not 1200.

Two rules keep the totals honest:

- **Crated and loose stock stay separate.** A crated rifle and a loose rifle are
  different goods, so they are never summed into one row. Crated stock is named
  `<item> Crate` and grouped under a `<category> Crates` heading.
- **Unknown quantities stay unknown.** If any contributing stockpile had a
  quantity that could not be read, the merged row shows `?` and is left out of
  the total, rather than publishing a total that silently omits stock.

Because the merged image covers many stockpiles, the placeholders that describe
a *single* stockpile no longer apply: `{name}` becomes `All Stockpiles`, while
`{type}`, `{hex}`, `{shard}`, `{ingame_timestamp}` and `{resolution}` render
empty. `{item_count}` and `{total_items}` remain accurate and cover the merged
view.

## Placeholders

`message` and `headline` both accept these:

| Placeholder | Example |
|-------------|---------|
| `{name}` | Westgate Depot |
| `{type}` | StorageFacility |
| `{hex}` | Westgate |
| `{shard}` | ABLE |
| `{ingame_timestamp}` | Day 1,293, 1906 Hours |
| `{timestamp}` | 2026-01-04 09:00 |
| `{resolution}` | 1920x1080 |
| `{item_count}` | 42 |
| `{total_items}` | 1320 |

Unknown placeholders are left as-is instead of failing the scan, so a typo shows
up as literal text in the channel. An empty or whitespace-only `headline` counts
as unset and falls back to the stockpile name.

## Rate limiting

A capture loop can run every few seconds, which would flood the channel. Set
`min_interval_minutes` to post at most one message per that many minutes:

```json
"handler": { "type": "discord", "url": "...", "min_interval_minutes": 30 }
```

Behaviour:

- Within the window, scans are skipped and nothing is rendered or posted.
- The time of the last successful post is saved as `last_sent_at` in the config
  file, so the cooldown **survives a restart**.
- A failed post does not start the cooldown, so a temporary error is retried on
  the next scan.
- When one scan produces several stockpiles, only the first is posted while the
  cooldown is active.
- `0` (the default) posts every scan.

## Limits

Discord rejects messages with content over 2000 characters, so longer `message`
templates are truncated. Image size is not a concern in practice: the images are
flat-colour PNGs of a few tens of kilobytes.

## Troubleshooting

Enable debug logging to see what the handler is doing:

```json
"logging": {
  "log_level": "DEBUG",
  "loggers": {
    "foxhole_stockpiles.handlers.discord": "DEBUG",
    "foxhole_stockpiles.connectors.discord": "DEBUG"
  }
}
```

**Nothing is posted.** Check the log for `cooldown active` — your
`min_interval_minutes` may simply be suppressing it.

**`HTTP 401` / `403`.** The webhook token in the URL is wrong or was revoked.
Recreate the webhook and update the URL.

**`HTTP 404`.** Discord no longer knows that webhook. This is what you get after
deleting the webhook in Discord.

**The image shows raw codes.** Set `database_builder.catalog_file` so display
names can be resolved.

**Text is tiny or the font is wrong.** Point `font_path` at a `.ttf`/`.otf` file.

## Security considerations

1. A webhook URL is a credential: anyone holding it can post to the channel.
   Treat the config file as a secret (it is written owner-only, `0600`).
2. Prefer a webhook scoped to a single channel over a server-wide one.
3. Delete the webhook in Discord to revoke access instantly.

## See also

- [Configuration](configuration.md) — all configuration options
- [Webhook Integration](webhooks.md) — JSON output to any HTTP endpoint
- [Troubleshooting](troubleshooting.md) — capture, scanning, and output issues

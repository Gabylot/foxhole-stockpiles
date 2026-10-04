"""Render stockpile results as images.

Produces a PNG of a scan that can be posted straight into a chat channel: a dark
canvas with a centred headline, category blocks packed into columns, and one
bordered row per item showing its display name and quantity, closed by a footer
with the item total and the time it was generated.

Rows show quantities only. A scan result carries no demand data, so nothing is
rendered against a target.
"""

import io
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import cast

from PIL import Image, ImageDraw, ImageFont

from foxhole_stockpiles.models.stockpile import Stockpile
from foxhole_stockpiles.models.stockpile_item import StockpileItem

logger = logging.getLogger(__name__)

# Layout constants.
_COLUMN_WIDTH = 400
_COLUMN_SPACING = 100
_HEADLINE_HEIGHT = 60
_EXTRA_HEIGHT_BELOW_HEADLINE = 100
_LINE_HEIGHT = 40
_CATEGORY_NAME_OFFSET = 10
_CATEGORY_PAD = 30
_OVERFLOW_THRESHOLD = 200
_MIN_IMAGE_WIDTH = 600
_IMAGE_PADDING = 20

# Colours: a dark slate canvas with lighter rows, readable in a dark-themed chat.
_BACKGROUND = (17, 24, 39)
_TEXT_COLOR = (255, 255, 255)
_ROW_BORDER = (75, 85, 99)
_ROW_BACKGROUND = (30, 41, 59)
_FOOTER_COLOR = (200, 200, 200)

# Font sizes, in pixels.
_TEXT_FONT_SIZE = 20
_CATEGORY_NAME_FONT_SIZE = 24
_HEADLINE_FONT_SIZE = 24
_FOOTER_FONT_SIZE = 18

# Footer space for the total line, any extra info, and the generation time.
_FOOTER_HEIGHT = 90
_FOOTER_LINE_HEIGHT = 26
_FOOTER_TOP_PADDING = 10

# Tried in order when no font is configured; Pillow's bundled font is the last
# resort so rendering always works.
_FALLBACK_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)

# Shown instead of a number when a quantity is unknown (the model uses -1).
_UNKNOWN_QUANTITY = "?"

# Group for items the catalog has no category for.
_DEFAULT_CATEGORY = "Other"


class _MissingKeyDict(dict[str, str]):
    """Dict that leaves unknown placeholders in place instead of raising."""

    def __missing__(self, key: str) -> str:
        """Return the placeholder unchanged for unknown keys.

        Args:
            key (str): The missing placeholder name.

        Returns:
            str: The original ``{key}`` placeholder text.
        """
        return "{" + key + "}"


def format_placeholder_value(value: object) -> str:
    """Render a placeholder value for display, blanking out missing data.

    Args:
        value: Raw value, possibly None.

    Returns:
        str: The value as text, or an empty string when it is missing.
    """
    return "" if value is None else str(value)


def build_placeholders(stockpile: Stockpile) -> dict[str, str]:
    """Build the placeholder values available to headline and message templates.

    Args:
        stockpile (Stockpile): The stockpile being rendered.

    Returns:
        dict[str, str]: Mapping of placeholder name to its formatted value.
    """
    items = stockpile.items
    # A negative quantity means "unknown", so it must not count toward the total.
    total_items = sum(item.quantity for item in items if item.quantity >= 0)

    return {
        "name": format_placeholder_value(stockpile.name),
        "type": format_placeholder_value(stockpile.type),
        "hex": format_placeholder_value(stockpile.hex),
        "shard": format_placeholder_value(stockpile.shard),
        "ingame_timestamp": format_placeholder_value(stockpile.ingame_timestamp),
        "timestamp": stockpile.timestamp.strftime("%Y-%m-%d %H:%M"),
        "resolution": format_placeholder_value(stockpile.resolution),
        "item_count": str(len(items)),
        "total_items": str(total_items),
    }


def render_template(template: str | None, stockpile: Stockpile) -> str | None:
    """Substitute stockpile placeholders into a user template.

    Unknown placeholders are left untouched rather than raising, so a typo in
    the config degrades to visible-but-harmless text instead of failing a scan.

    Args:
        template (str | None): The raw template, or None.
        stockpile (Stockpile): The stockpile supplying placeholder values.

    Returns:
        str | None: The formatted template, or None if no template was set.
    """
    if template is None:
        return None
    # An empty template is treated as "unset" so a leftover "" in the config
    # falls back to the defaults, matching the GUI's empty-field behaviour.
    if not template.strip():
        return None
    try:
        return template.format_map(_MissingKeyDict(build_placeholders(stockpile)))
    except (ValueError, IndexError):
        # Malformed template braces; show it verbatim rather than failing.
        logger.warning("Could not render template %r", template)
        return template


@dataclass
class _CategoryBlock:
    """One category heading and the item rows that belong to it."""

    name: str
    items: list[StockpileItem] = field(default_factory=list)
    height: int = 0


class StockpileImageRenderer:
    """Renders stockpile results as PNG images.

    Holds no mutable state beyond a font cache, so one instance can be reused
    across scans and across the worker thread the handler renders in.
    """

    def __init__(self, font_path: Path | None = None) -> None:
        """Initialize the renderer.

        Args:
            font_path (Path | None): Optional TrueType font to draw text with.
                When missing or unreadable, a system font is used, falling back
                to Pillow's bundled font.
        """
        self._font_path = font_path
        self._font_cache: dict[int, ImageFont.FreeTypeFont] = {}

    def _font(self, size: int) -> ImageFont.FreeTypeFont:
        """Load and cache a font at the given pixel size.

        Args:
            size (int): Font size in pixels.

        Returns:
            ImageFont.FreeTypeFont: A usable font of the requested size.
        """
        cached = self._font_cache.get(size)
        if cached is not None:
            return cached

        font = self._load_font(size)
        self._font_cache[size] = font
        return font

    def _load_font(self, size: int) -> ImageFont.FreeTypeFont:
        """Resolve a font at the given size from config, system, or Pillow.

        Args:
            size (int): Font size in pixels.

        Returns:
            ImageFont.FreeTypeFont: The first font that loads successfully.
        """
        candidates: list[Path] = []
        if self._font_path is not None:
            candidates.append(self._font_path)
        candidates.extend(Path(path) for path in _FALLBACK_FONTS)

        for candidate in candidates:
            try:
                if candidate.is_file():
                    return ImageFont.truetype(str(candidate), size)
            except OSError as exc:
                logger.warning("Could not load font %s: %s", candidate, exc)

        # Pillow >= 10.1 can scale its built-in font, so text stays legible
        # even when no system fonts are present. Its stub types the result
        # loosely, hence the cast.
        try:
            default = ImageFont.load_default(size=size)
        except TypeError:  # pragma: no cover - only on very old Pillow
            default = ImageFont.load_default()
        return cast(ImageFont.FreeTypeFont, default)

    @staticmethod
    def _truncate(
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont,
        max_width: int,
    ) -> str:
        """Shorten text with an ellipsis until it fits within ``max_width``.

        Args:
            draw (ImageDraw.ImageDraw): Draw context used for measuring.
            text (str): Text to shorten.
            font (ImageFont.FreeTypeFont): Font used for measuring.
            max_width (int): Maximum allowed width in pixels.

        Returns:
            str: The original text, or a shortened version ending in "...".
        """
        if int(draw.textlength(text, font=font)) <= max_width:
            return text

        for length in range(len(text) - 1, 0, -1):
            candidate = text[:length].rstrip() + "..."
            if int(draw.textlength(candidate, font=font)) <= max_width:
                return candidate
        return "..."

    @staticmethod
    def _item_label(item: StockpileItem, display_name: str) -> str:
        """Build the text shown on an item's row.

        Crated stock is named ``"<item> Crate"`` rather than being flagged
        with a marker, so the row name says what the stock actually is.

        Args:
            item (StockpileItem): The item being drawn.
            display_name (str): Its resolved display name.

        Returns:
            str: ``"<name> (<quantity>)"``, using ``?`` for unknown quantities.
        """
        quantity = _UNKNOWN_QUANTITY if item.quantity < 0 else str(item.quantity)
        return f"{display_name} ({quantity})"

    @staticmethod
    def _display_name(item: StockpileItem) -> str:
        """Resolve the name to show for an item, honouring its crate state.

        Args:
            item (StockpileItem): The item being drawn.

        Returns:
            str: The display name, with ``" Crate"`` appended when the item is
                crated and the game allows it.
        """
        from foxhole_stockpiles.services.catalog_service import get_catalog_service

        catalog = get_catalog_service()
        if item.crated:
            # Falls back to the plain name for goods that cannot be crated.
            crated_name = catalog.get_crated_display_name(item.code)
            if crated_name is not None:
                return crated_name
        return catalog.get_display_name(item.code)

    @staticmethod
    def _category_name(item: StockpileItem) -> str:
        """Resolve the category to group an item under.

        A crate is a different good from its contents, so crated and loose stock
        are kept in separate groups.

        Args:
            item (StockpileItem): The item being grouped.

        Returns:
            str: The category label, or ``"Other"`` when the catalog has none.
        """
        from foxhole_stockpiles.services.catalog_service import get_catalog_service

        return get_catalog_service().get_category_for(item.code, crated=item.crated) or (
            _DEFAULT_CATEGORY
        )

    @staticmethod
    def _group_items(stockpile: Stockpile) -> list[_CategoryBlock]:
        """Group a stockpile's items by catalog category.

        Items whose category is unknown land in a single trailing group so the
        output stays deterministic.

        Args:
            stockpile (Stockpile): The stockpile to group.

        Returns:
            list[_CategoryBlock]: Category blocks, ordered by category name.
        """
        from foxhole_stockpiles.services.catalog_service import get_catalog_service

        grouped: dict[str, list[StockpileItem]] = {}
        for item in stockpile.items:
            grouped.setdefault(StockpileImageRenderer._category_name(item), []).append(item)

        return [
            _CategoryBlock(
                name=name,
                items=items,
                # One line per item, plus the heading and its gap.
                height=len(items) * _LINE_HEIGHT + 40 + _CATEGORY_NAME_OFFSET,
            )
            # Ordered by supply, not alphabetically, so related goods read together.
            for name, items in sorted(
                grouped.items(), key=lambda pair: get_catalog_service().category_sort_key(pair[0])
            )
        ]

    @staticmethod
    def _pack_columns(blocks: list[_CategoryBlock]) -> list[list[_CategoryBlock]]:
        """Pack category blocks into columns, stacking wherever they fit.

        A block joins the current column while the result stays within
        ``_OVERFLOW_THRESHOLD`` of the tallest single block; otherwise it starts
        a new column. This keeps short categories stacked instead of giving
        each one a column of its own.

        Args:
            blocks (list[_CategoryBlock]): Category blocks in display order.

        Returns:
            list[list[_CategoryBlock]]: The blocks grouped per column.
        """
        if not blocks:
            return []

        max_block_height = max(block.height for block in blocks)
        columns: list[list[_CategoryBlock]] = [[blocks[0]]]
        column_heights = [blocks[0].height]

        for block in blocks[1:]:
            fits = column_heights[-1] + _CATEGORY_PAD + block.height
            if fits <= max_block_height + _OVERFLOW_THRESHOLD:
                columns[-1].append(block)
                column_heights[-1] += _CATEGORY_PAD + block.height
            else:
                columns.append([block])
                column_heights.append(block.height)

        return columns

    def render(
        self,
        stockpile: Stockpile,
        headline: str | None = None,
    ) -> Image.Image:
        """Render a stockpile to a PIL image.

        Args:
            stockpile (Stockpile): The stockpile to render.
            headline (str | None): Optional headline text. Defaults to the
                stockpile name, falling back to its type.

        Returns:
            Image.Image: The rendered RGB image.
        """
        columns = self._pack_columns(self._group_items(stockpile))

        tallest_block = max((block.height for column in columns for block in column), default=0)
        # A column is as tall as everything stacked in it, not just its last
        # block: summing here keeps the footer clear of the rows below it.
        tallest_column = max(
            (
                sum(block.height for block in column) + _CATEGORY_PAD * (len(column) - 1)
                for column in columns
            ),
            default=0,
        )

        column_count = len(columns)
        image_width = max(
            column_count * _COLUMN_WIDTH + (column_count - 1) * _COLUMN_SPACING + _IMAGE_PADDING,
            _MIN_IMAGE_WIDTH,
        )
        image_height = (
            max(tallest_block, tallest_column)
            + _HEADLINE_HEIGHT
            + _EXTRA_HEIGHT_BELOW_HEADLINE
            + _FOOTER_HEIGHT
        )

        image = Image.new("RGB", (image_width, image_height), _BACKGROUND)
        draw = ImageDraw.Draw(image)

        text_font = self._font(_TEXT_FONT_SIZE)
        category_font = self._font(_CATEGORY_NAME_FONT_SIZE)
        headline_font = self._font(_HEADLINE_FONT_SIZE)

        if headline is None:
            headline = stockpile.name or stockpile.type
        if headline:
            headline_width = int(draw.textlength(headline, font=headline_font))
            draw.text(
                ((image_width - headline_width) / 2, _HEADLINE_HEIGHT - 15),
                headline,
                font=headline_font,
                fill=_TEXT_COLOR,
            )

        x_offset = _IMAGE_PADDING
        for column in columns:
            y_offset = _HEADLINE_HEIGHT + _EXTRA_HEIGHT_BELOW_HEADLINE

            for index, block in enumerate(column):
                if index > 0:
                    y_offset += _CATEGORY_PAD

                # Category heading, centred over its block.
                bar_width = _COLUMN_WIDTH - 20
                name_width = int(draw.textlength(block.name, font=category_font))
                draw.text(
                    (x_offset + (bar_width - name_width) / 2, y_offset),
                    block.name,
                    font=category_font,
                    fill=_TEXT_COLOR,
                )

                row_y = y_offset + _LINE_HEIGHT + _CATEGORY_NAME_OFFSET
                for item in block.items:
                    self._draw_item_row(
                        draw, item=item, x=x_offset, y=row_y, width=bar_width, font=text_font
                    )
                    row_y += _LINE_HEIGHT

                y_offset += block.height

            x_offset += _COLUMN_WIDTH + _COLUMN_SPACING

        self._draw_footer(
            draw,
            stockpile=stockpile,
            image_height=image_height,
            font=self._font(_FOOTER_FONT_SIZE),
        )

        return image

    @staticmethod
    def _draw_item_row(
        draw: ImageDraw.ImageDraw,
        item: StockpileItem,
        x: int,
        y: int,
        width: int,
        font: ImageFont.FreeTypeFont,
    ) -> None:
        """Draw one item as a bordered bar with its name and quantity.

        Args:
            draw (ImageDraw.ImageDraw): Draw context.
            item (StockpileItem): The item to draw.
            x (int): Left edge of the bar.
            y (int): Top edge of the bar.
            width (int): Bar width in pixels.
            font (ImageFont.FreeTypeFont): Font for the label.
        """
        height = _LINE_HEIGHT - 8

        # Dark fill inside a lighter border, so each row reads as a unit.
        draw.rectangle((x, y, x + width, y + height), outline=_ROW_BORDER, width=1)
        draw.rectangle((x + 1, y + 1, x + width - 1, y + height - 1), fill=_ROW_BACKGROUND)

        label = StockpileImageRenderer._item_label(item, StockpileImageRenderer._display_name(item))
        draw.text(
            (x + 10, y + 6),
            StockpileImageRenderer._truncate(draw, label, font, width - 20),
            font=font,
            fill=_TEXT_COLOR,
        )

    def _draw_footer(
        self,
        draw: ImageDraw.ImageDraw,
        stockpile: Stockpile,
        image_height: int,
        font: ImageFont.FreeTypeFont,
    ) -> None:
        """Draw the footer: item total and the render time.

        Args:
            draw (ImageDraw.ImageDraw): Draw context.
            stockpile (Stockpile): The rendered stockpile.
            image_height (int): Canvas height.
            font (ImageFont.FreeTypeFont): Font for the footer text.
        """
        total_items = build_placeholders(stockpile)["total_items"]

        lines = [
            f"Total items: {total_items}",
            f"Generated on {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
        ]

        base_y = image_height - _FOOTER_HEIGHT + _FOOTER_TOP_PADDING
        for index, line in enumerate(lines):
            draw.text(
                (10, base_y + index * _FOOTER_LINE_HEIGHT), line, font=font, fill=_FOOTER_COLOR
            )

    def render_png(
        self,
        stockpile: Stockpile,
        headline: str | None = None,
    ) -> bytes:
        """Render a stockpile and encode it as PNG bytes.

        Args:
            stockpile (Stockpile): The stockpile to render.
            headline (str | None): Optional headline text.

        Returns:
            bytes: PNG-encoded image data.
        """
        buffer = io.BytesIO()
        self.render(stockpile, headline=headline).save(buffer, format="PNG")
        return buffer.getvalue()

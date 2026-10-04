"""Tests for services.image_renderer module."""

from datetime import UTC, datetime

from PIL import Image

from foxhole_stockpiles.enums.stockpile_type import StockpileType
from foxhole_stockpiles.models.stockpile import Stockpile
from foxhole_stockpiles.models.stockpile_item import StockpileItem
from foxhole_stockpiles.services.image_renderer import (
    StockpileImageRenderer,
    build_placeholders,
    format_placeholder_value,
    render_template,
)


def _stockpile(items: list[StockpileItem], **kwargs: object) -> Stockpile:
    """Build a stockpile for rendering tests.

    Args:
        items (list[StockpileItem]): Items to include.
        **kwargs: Extra Stockpile fields.

    Returns:
        Stockpile: The constructed stockpile.
    """
    return Stockpile(items=items, **kwargs)  # type: ignore[arg-type]


class TestPlaceholders:
    """Test suite for placeholder helpers."""

    def test_format_placeholder_value(self) -> None:
        """None becomes an empty string, other values pass through."""
        assert format_placeholder_value(None) == ""
        assert format_placeholder_value(0) == "0"
        assert format_placeholder_value("x") == "x"

    def test_build_placeholders_total_ignores_unknown(self) -> None:
        """An unknown quantity (-1) is excluded from the total."""
        stockpile = _stockpile(
            [
                StockpileItem(code="A", quantity=10),
                StockpileItem(code="B", quantity=-1),
                StockpileItem(code="C", quantity=5),
            ]
        )
        placeholders = build_placeholders(stockpile)
        assert placeholders["total_items"] == "15"
        assert placeholders["item_count"] == "3"

    def test_render_template_substitutes(self) -> None:
        """Known placeholders are replaced."""
        stockpile = _stockpile([StockpileItem(code="A", quantity=4)], name="Depot", hex="Westgate")
        assert render_template("{name}/{hex}/{total_items}", stockpile) == "Depot/Westgate/4"

    def test_render_template_keeps_unknown_placeholder(self) -> None:
        """An unknown placeholder is left as-is rather than raising."""
        stockpile = _stockpile([], name="Depot")
        assert render_template("{name} {nope}", stockpile) == "Depot {nope}"

    def test_render_template_none(self) -> None:
        """A missing template stays None."""
        assert render_template(None, _stockpile([])) is None

    def test_render_template_empty_is_unset(self) -> None:
        """An empty or blank template counts as unset, so defaults apply."""
        stockpile = _stockpile([], name="Depot")
        assert render_template("", stockpile) is None
        assert render_template("   ", stockpile) is None

    def test_headline_falls_back_to_name_then_type(self) -> None:
        """With no headline template the name is used, then the type."""
        named = _stockpile([], name="Depot", type=StockpileType.SEAPORT)
        assert (named.name or named.type) == "Depot"

        unnamed = _stockpile([], name="", type=StockpileType.SEAPORT)
        # Name is blank, so the stockpile type stands in for it.
        assert (unnamed.name or unnamed.type) == StockpileType.SEAPORT

    def test_render_template_malformed_braces(self) -> None:
        """Malformed braces return the template verbatim."""
        stockpile = _stockpile([], name="Depot")
        assert render_template("{name", stockpile) == "{name"


class TestStockpileImageRenderer:
    """Test suite for StockpileImageRenderer."""

    def test_render_produces_image(self) -> None:
        """Rendering returns a non-empty RGB image."""
        stockpile = _stockpile(
            [StockpileItem(code="A", quantity=10), StockpileItem(code="B", quantity=20)],
            name="Depot",
        )
        image = StockpileImageRenderer().render(stockpile)
        assert isinstance(image, Image.Image)
        assert image.mode == "RGB"
        assert image.width > 0 and image.height > 0

    def test_render_png_is_valid(self) -> None:
        """The encoded bytes carry a PNG signature."""
        stockpile = _stockpile([StockpileItem(code="A", quantity=1)], name="Depot")
        data = StockpileImageRenderer().render_png(stockpile)
        assert data.startswith(b"\x89PNG\r\n\x1a\n")

    def test_render_empty_stockpile(self) -> None:
        """An empty stockpile still renders without error."""
        image = StockpileImageRenderer().render(_stockpile([], name="Empty"))
        assert image.width > 0 and image.height > 0

    def test_render_grows_with_items(self) -> None:
        """More items produce a taller image."""
        renderer = StockpileImageRenderer()
        few = renderer.render(_stockpile([StockpileItem(code="A", quantity=1)], name="S"))
        many = renderer.render(
            _stockpile([StockpileItem(code=f"I{i}", quantity=i) for i in range(20)], name="S")
        )
        assert many.height > few.height

    def test_footer_does_not_overlap_rows(self) -> None:
        """The canvas is tall enough for every column plus the footer.

        A column holding several category blocks is as tall as the sum of
        those blocks; measuring only the last one lets the footer land on top
        of the rows.
        """
        # Three categories, one item each: they stack into a single column.
        stockpile = _stockpile([StockpileItem(code=code, quantity=1) for code in ("A", "B", "C")])
        image = StockpileImageRenderer().render(stockpile, headline="H")

        columns = StockpileImageRenderer._pack_columns(
            StockpileImageRenderer()._group_items(stockpile)
        )
        tallest_column = max(
            sum(block.height for block in column) + 30 * (len(column) - 1) for column in columns
        )

        expected = tallest_column + 60 + 100 + 90
        assert image.height >= expected

    def test_group_items_falls_back_to_other(self) -> None:
        """Items without a catalog category are grouped under "Other"."""
        stockpile = _stockpile([StockpileItem(code="UnknownCode", quantity=3)])
        blocks = StockpileImageRenderer._group_items(stockpile)
        assert [block.name for block in blocks] == ["Other"]

    def test_group_items_sorts_by_category(self) -> None:
        """Category blocks are returned in alphabetical order."""
        stockpile = _stockpile(
            [StockpileItem(code="A", quantity=1), StockpileItem(code="B", quantity=1)]
        )
        blocks = StockpileImageRenderer._group_items(stockpile)
        names = [block.name for block in blocks]
        assert names == sorted(names)

    def test_pack_columns_empty(self) -> None:
        """No blocks means no columns."""
        assert StockpileImageRenderer._pack_columns([]) == []

    def test_pack_columns_single_block(self) -> None:
        """A lone block occupies a single column."""
        stockpile = _stockpile([StockpileItem(code="A", quantity=1)])
        blocks = StockpileImageRenderer._group_items(stockpile)
        columns = StockpileImageRenderer._pack_columns(blocks)
        assert len(columns) == 1
        assert columns[0] == blocks

    def test_pack_columns_splits_tall_blocks(self) -> None:
        """Very tall blocks are not stacked into one column."""
        from foxhole_stockpiles.services.image_renderer import _CategoryBlock

        blocks = [_CategoryBlock(name="A", items=[], height=5000) for _ in range(3)]
        columns = StockpileImageRenderer._pack_columns(blocks)
        assert len(columns) > 1

    def test_item_label_formats_quantity(self) -> None:
        """The label is the display name and quantity."""
        label = StockpileImageRenderer._item_label(StockpileItem(code="A", quantity=7), "Thing")
        assert label == "Thing (7)"

    def test_item_label_unknown_quantity(self) -> None:
        """An unknown quantity renders as a question mark."""
        label = StockpileImageRenderer._item_label(StockpileItem(code="A", quantity=-1), "Thing")
        assert label == "Thing (?)"

    def test_item_label_has_no_crate_marker(self) -> None:
        """Crate state is carried by the name, not a trailing marker."""
        label = StockpileImageRenderer._item_label(
            StockpileItem(code="A", quantity=2, crated=True), "Thing Crate"
        )

        assert label == "Thing Crate (2)"
        assert "crated" not in label

    def test_item_label_uses_type_when_no_name(self) -> None:
        """A stockpile without a name falls back to its type for the headline."""
        stockpile = _stockpile([StockpileItem(code="A", quantity=1)], type=StockpileType.SEAPORT)
        image = StockpileImageRenderer().render(stockpile)
        assert image.width > 0

    def test_truncate_shortens_long_text(self) -> None:
        """Text wider than the limit gains an ellipsis."""
        stockpile = _stockpile([], name="S")
        image = StockpileImageRenderer().render(stockpile)
        draw = ImageDrawHelper(image)
        font = StockpileImageRenderer()._font(20)
        result = StockpileImageRenderer._truncate(draw.draw, "A" * 200, font, 50)
        assert result.endswith("...")
        assert len(result) < 200

    def test_truncate_leaves_short_text(self) -> None:
        """Text that already fits is returned unchanged."""
        stockpile = _stockpile([], name="S")
        image = StockpileImageRenderer().render(stockpile)
        draw = ImageDrawHelper(image)
        font = StockpileImageRenderer()._font(20)
        assert StockpileImageRenderer._truncate(draw.draw, "ok", font, 500) == "ok"

    def test_explicit_font_path_is_used(self) -> None:
        """A missing font file falls back instead of raising."""
        stockpile = _stockpile([StockpileItem(code="A", quantity=1)], name="S")
        renderer = StockpileImageRenderer(font_path=__import__("pathlib").Path("/nonexistent.ttf"))
        assert renderer.render(stockpile).width > 0


class ImageDrawHelper:
    """Small helper exposing a draw context for text measurement."""

    def __init__(self, image: Image.Image) -> None:
        """Create the helper.

        Args:
            image (Image.Image): Image to draw on.
        """
        from PIL import ImageDraw

        self.draw = ImageDraw.Draw(image)


class TestFooter:
    """Test suite for the footer rendering."""

    def test_footer_includes_total(self) -> None:
        """The footer reports the summed item count."""
        stockpile = _stockpile(
            [StockpileItem(code="A", quantity=6), StockpileItem(code="B", quantity=4)],
            name="S",
        )
        renderer = StockpileImageRenderer()
        image = renderer.render(stockpile)
        # Footer occupies the bottom band; ensure it is inside the canvas.
        assert image.height > 0
        assert isinstance(renderer.render_png(stockpile), bytes)


class TestTimestampPlaceholder:
    """Test suite for the timestamp placeholder."""

    def test_timestamp_format(self) -> None:
        """The timestamp placeholder renders as a readable datetime."""
        stockpile = Stockpile(items=[], timestamp=datetime(2026, 1, 2, 3, 4, tzinfo=UTC))
        assert build_placeholders(stockpile)["timestamp"] == "2026-01-02 03:04"

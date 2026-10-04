"""Tests for merging several stockpiles into one view."""

from datetime import datetime

from foxhole_stockpiles.enums.stockpile_type import StockpileType
from foxhole_stockpiles.models.stockpile import Stockpile
from foxhole_stockpiles.models.stockpile_item import StockpileItem
from foxhole_stockpiles.services.stockpile_aggregator import (
    AGGREGATE_NAME,
    aggregate,
    merge_items,
)


class TestMergeItems:
    """Merging item rows across stockpiles."""

    def test_sums_quantities_for_same_item(self) -> None:
        """The same item in two stockpiles adds up."""
        merged = merge_items(
            [
                StockpileItem(code="RifleW", quantity=10),
                StockpileItem(code="RifleW", quantity=5),
            ]
        )

        assert merged == [StockpileItem(code="RifleW", quantity=15)]

    def test_crated_and_loose_stay_separate(self) -> None:
        """Crated and loose stock are different goods and must not be summed."""
        merged = merge_items(
            [
                StockpileItem(code="RifleW", quantity=10, crated=True),
                StockpileItem(code="RifleW", quantity=30, crated=False),
            ]
        )

        assert merged == [
            StockpileItem(code="RifleW", quantity=30, crated=False),
            StockpileItem(code="RifleW", quantity=10, crated=True),
        ]

    def test_unknown_quantity_does_not_add_to_total(self) -> None:
        """An unknown quantity stays unknown instead of becoming a number."""
        merged = merge_items(
            [
                StockpileItem(code="Bomb", quantity=-1),
                StockpileItem(code="Bomb", quantity=10),
            ]
        )

        assert merged == [StockpileItem(code="Bomb", quantity=-1)]

    def test_unknown_arriving_last_still_taints_row(self) -> None:
        """A known value cannot rescue a row once an unknown has joined it."""
        merged = merge_items(
            [
                StockpileItem(code="Bomb", quantity=10),
                StockpileItem(code="Bomb", quantity=-1),
            ]
        )

        assert merged == [StockpileItem(code="Bomb", quantity=-1)]

    def test_different_codes_stay_distinct(self) -> None:
        """Different items are not combined."""
        merged = merge_items(
            [
                StockpileItem(code="Bomb", quantity=1),
                StockpileItem(code="RifleW", quantity=2),
            ]
        )

        assert len(merged) == 2

    def test_output_is_sorted_for_stability(self) -> None:
        """Merge order follows the input so renders are reproducible."""
        merged = merge_items(
            [
                StockpileItem(code="Shell", quantity=1),
                StockpileItem(code="Bomb", quantity=2),
                StockpileItem(code="Shell", quantity=3),
            ]
        )

        assert [item.code for item in merged] == ["Bomb", "Shell"]
        assert merged[1].quantity == 4

    def test_empty_input(self) -> None:
        """Merging nothing yields nothing."""
        assert merge_items([]) == []


class TestAggregate:
    """Combining whole stockpiles into one renderable stockpile."""

    def test_empty_input_returns_named_empty_stockpile(self) -> None:
        """Empty input yields an empty stockpile instead of raising."""
        result = aggregate([])

        assert result.name == AGGREGATE_NAME
        assert result.items == []

    def test_combines_items_from_every_stockpile(self) -> None:
        """Items from all stockpiles end up in the merged view."""
        result = aggregate(
            [
                Stockpile(items=[StockpileItem(code="Bomb", quantity=3)]),
                Stockpile(items=[StockpileItem(code="RifleW", quantity=7)]),
            ]
        )

        assert result.items == [
            StockpileItem(code="Bomb", quantity=3),
            StockpileItem(code="RifleW", quantity=7),
        ]

    def test_sums_the_same_item_across_stockpiles(self) -> None:
        """Duplicate items across stockpiles are summed."""
        result = aggregate(
            [
                Stockpile(items=[StockpileItem(code="Bomb", quantity=3)]),
                Stockpile(items=[StockpileItem(code="Bomb", quantity=4)]),
            ]
        )

        assert result.items == [StockpileItem(code="Bomb", quantity=7)]

    def test_carries_no_single_stockpile_identity(self) -> None:
        """Per-stockpile fields are not carried over, since none of them apply."""
        result = aggregate(
            [Stockpile(name="Westgate Depot", hex="Westgate", type=StockpileType.SEAPORT)]
        )

        assert result.name == AGGREGATE_NAME
        assert result.hex is None
        assert result.type == StockpileType.UNDEFINED

    def test_uses_newest_source_timestamp(self) -> None:
        """The merged view is as fresh as its freshest source."""
        early = Stockpile(timestamp=datetime(2024, 1, 1))
        late = Stockpile(timestamp=datetime(2024, 6, 1))

        result = aggregate([early, late])

        assert result.timestamp == late.timestamp

    def test_empty_stockpiles_do_not_break_aggregation(self) -> None:
        """Stockpiles without items are tolerated."""
        result = aggregate([Stockpile(name="Empty"), Stockpile(name="Empty 2")])

        assert result.items == []

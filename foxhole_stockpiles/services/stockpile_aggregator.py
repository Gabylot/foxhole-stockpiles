"""Merge several stockpiles into one combined view.

A scan can return many stockpiles, and posting one image each floods the channel
with near-duplicates. Aggregating merges the items of every stockpile into a
single ``Stockpile`` that the existing renderer draws unchanged.

Two rules keep the merged numbers honest:

* Crated and loose stock stay separate. A crated rifle and a loose rifle are
  different goods, so merging on the item code alone would produce a total that
  means nothing and mislabel the ``(crated)`` marker.
* An unknown quantity never becomes a number. A stockpile whose quantity could
  not be read contributes nothing to a sum, and taints the merged row back to
  unknown so it is drawn as ``?`` and left out of the total.
"""

import logging

from foxhole_stockpiles.enums.stockpile_type import StockpileType
from foxhole_stockpiles.models.stockpile import Stockpile
from foxhole_stockpiles.models.stockpile_item import StockpileItem

logger = logging.getLogger(__name__)

# Headline/name used for the merged stockpile. Individual stockpiles no longer
# apply, so the placeholders that describe a single one are left blank.
AGGREGATE_NAME = "All Stockpiles"


def merge_items(items: list[StockpileItem]) -> list[StockpileItem]:
    """Merge items from several stockpiles into one deduplicated list.

    Items are keyed on ``(code, crated)``. Known quantities are summed; if any
    contributor's quantity is unknown the merged row is marked unknown so the
    renderer draws ``?`` rather than a number built on a guess.

    Args:
        items (list[StockpileItem]): Items across all stockpiles to merge.

    Returns:
        list[StockpileItem]: Merged items, sorted by code then crated.
    """
    quantities: dict[tuple[str, bool], int] = {}

    for item in items:
        key = (item.code, item.crated)
        quantity = quantities.get(key, 0)

        if item.quantity < 0 or quantity < 0:
            # One unknown contributor poisons the row: stay unknown rather than
            # publish a total that silently omits part of the stock.
            quantities[key] = -1
        else:
            quantities[key] = quantity + item.quantity

    # Sorting on (code, crated) puts the loose row first, since False sorts
    # before True; that keeps the crated variant directly under its loose one.
    return [
        StockpileItem(code=code, quantity=quantity, crated=crated)
        for (code, crated), quantity in sorted(quantities.items())
    ]


def aggregate(stockpiles: list[Stockpile]) -> Stockpile:
    """Merge every stockpile into a single combined stockpile.

    Args:
        stockpiles (list[Stockpile]): The stockpiles from one scan.

    Returns:
        Stockpile: One stockpile holding the merged items, ready to render.
        Empty input yields an empty stockpile rather than raising.
    """
    if not stockpiles:
        return Stockpile(name=AGGREGATE_NAME, type=StockpileType.UNDEFINED)

    # The newest source timestamp keeps the footer meaningful.
    timestamp = max(stockpile.timestamp for stockpile in stockpiles)

    logger.debug("Aggregating %d stockpiles into one view", len(stockpiles))

    return Stockpile(
        name=AGGREGATE_NAME,
        type=StockpileType.UNDEFINED,
        timestamp=timestamp,
        items=merge_items([item for stockpile in stockpiles for item in stockpile.items]),
    )

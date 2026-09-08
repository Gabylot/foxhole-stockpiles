"""Parser for refinery queue data inside Foxhole's ``MapData.sav``.

Foxhole persists facility map details (including refinery storage bays) in the
local save file ``<steamid>_MapData.sav`` whenever the world map tooltip for a
facility is displayed. The file is a UE4 ``GVAS`` save, but Foxhole uses a
custom property serialization that differs from stock UE4.24:

- Every property tag is ``Name(FString)``, ``Type(FString)``, ``Size(int64)``,
  ``ArrayIndex(int32)`` followed by a single ``0x00`` flag byte, then the value.
  (StructProperty tags carry no flag byte.)
- ``EnumProperty`` values are preceded by the enum type name as a ``FString``;
  the ``Size`` field only covers the value ``FString``.
- ``ByteProperty`` values backed by an enum are serialized as ``FString``
  (e.g. ``"None"``), despite ``Size == 1``.

Only facilities whose map tooltip was recently opened are stored (in
``RecentMapItemDetails`` / ``InitalMapItemDetails``), so the data is a snapshot
of what the player has seen, not a global dump.

This module does not attempt a full GVAS implementation. Instead it scans the
file for the well-known ``MapDetailRefineryStorage`` element layout and
extracts the squad/personal refinery queue information with strict
verification of the surrounding byte patterns.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

_GVAS_MAGIC = b"GVAS"

# Byte patterns anchored at specific places inside a MapDetailRefineryStorage
# element. Offsets/sizes follow the observations documented in the module
# docstring and were verified against live save files.
_ACCESS_LEVEL_TAG = b"\x0c\x00\x00\x00AccessLevel\x00\x0f\x00\x00\x00StructProperty\x00"
_LEVEL_TAG = b"\x06\x00\x00\x00Level\x00\x0d\x00\x00\x00EnumProperty\x00"
_SQUAD_ID_TAG = b"\x08\x00\x00\x00SquadId\x00\x0c\x00\x00\x00IntProperty\x00"
_INDEX_TAG = b"\x06\x00\x00\x00Index\x00\x0d\x00\x00\x00ByteProperty\x00"
_REFINED_TAG = b"\x08\x00\x00\x00Refined\x00\x0f\x00\x00\x00UInt16Property\x00"
_STORAGE_STRUCT_NAME = b"MapDetailRefineryStorage\x00"

# Patterns for RefineryOrders[] arrays (personal/public orders)
_REFINERY_ORDERS_ARRAY_TAG = b"\x0f\x00\x00\x00RefineryOrders\x00\x0e\x00\x00\x00ArrayProperty\x00"
_REFINERY_ORDER_STRUCT_NAME = b"MapDetailRefineryOrder\x00"
_REFINERY_ORDER_INDEX_TAG = b"\x06\x00\x00\x00Index\x00\x0d\x00\x00\x00ByteProperty\x00"
_REFINERY_ORDER_REFINED_TAG = b"\x08\x00\x00\x00Refined\x00\x0f\x00\x00\x00UInt16Property\x00"
_REFINERY_ORDER_DURATION_TAG = b"\x08\x00\x00\x00Duration\x00\x0f\x00\x00\x00UInt16Property\x00"
_REFINERY_ORDER_SOURCE_TAG = b"\x07\x00\x00\x00Source\x00\x0f\x00\x00\x00UInt16Property\x00"

_ACCESS_LEVEL_ENUM_NAME = "ERefineryOrderAccessLevel"
_MAP_ID_PATTERN = re.compile(rb"EWorldConquestMapId::([A-Za-z0-9]+)\x00")


@dataclass(frozen=True)
class RefineryOrder:
    """A single refinery storage bay order found in a MapData.sav.

    Attributes:
        raw_access_level (str): Raw enum value, e.g.
            ``ERefineryOrderAccessLevel::Squad``.
        access_level (str): Normalized access level, one of ``"squad"``,
            ``"personal"`` or ``"public"``.
        squad_id (int | None): Squad identifier when the order is squad
            accessible, otherwise ``None``.
        index (int): Recipe slot index (0-9) of the refinery bay. The slot
            position matches the in-game recipe list of the facility.
        refined (int): Amount refined so far in this bay.
        map_hint (str | None): ``EWorldConquestMapId::...`` value of the map
            details block the order was found in, when available.
    """

    raw_access_level: str
    access_level: str
    squad_id: int | None
    index: int
    refined: int
    map_hint: str | None = None

    @property
    def is_squad_order(self) -> bool:
        """Whether this order is visible to the whole squad.

        Returns:
            bool: True when the access level is ``"squad"``.
        """
        return self.access_level == "squad"


@dataclass(frozen=True)
class MapDataReport:
    """Result of scanning a MapData.sav file.

    Attributes:
        source (Path): Path of the parsed save file.
        orders (list[RefineryOrder]): All refinery bay orders found.
    """

    source: Path
    orders: list[RefineryOrder]


def _read_fstring(data: bytes, pos: int) -> tuple[str, int]:
    """Read a length-prefixed UE4 FString.

    Args:
        data (bytes): Buffer to read from.
        pos (int): Offset of the length prefix.

    Returns:
        tuple[str, int]: The decoded string and the position after it.

    Raises:
        ValueError: If the string length is invalid for the buffer.
    """
    (length,) = struct.unpack_from("<i", data, pos)
    pos += 4
    if length == 0:
        return "", pos
    if length > 0:
        end = pos + length
        if end > len(data):
            raise ValueError("FString extends past end of buffer")
        return data[pos : end - 1].decode("latin1"), end
    end = pos + (-length) * 2
    if end > len(data):
        raise ValueError("FString extends past end of buffer")
    return data[pos : end - 2].decode("utf-16-le"), end


def _expect(data: bytes, pos: int, pattern: bytes) -> int:
    """Verify that ``pattern`` starts at ``pos`` and return the new position.

    Args:
        data (bytes): Buffer to read from.
        pos (int): Offset where the pattern is expected.
        pattern (bytes): Expected bytes.

    Returns:
        int: Position after the pattern.

    Raises:
        ValueError: If the pattern does not match.
    """
    end = pos + len(pattern)
    if data[pos:end] != pattern:
        raise ValueError(
            f"Unexpected bytes at offset {pos}: expected {pattern!r}, got {data[pos:end]!r}"
        )
    return end


def _parse_access_level(data: bytes, pos: int) -> tuple[str, int | None, int]:
    """Parse the ``AccessLevel`` struct of a refinery storage element.

    ``pos`` must point at the ``AccessLevel`` property tag that directly
    follows the element's struct name and GUID.

    Args:
        data (bytes): Buffer to read from.
        pos (int): Offset of the ``AccessLevel`` property tag.

    Returns:
        tuple[str, int | None, int]: The access level enum value, the squad id
        (``None`` for personal orders) and the position after the struct.

    Raises:
        ValueError: If the expected property patterns are not found.
    """
    level_idx = data.find(_LEVEL_TAG, pos, pos + 120)
    if level_idx < 0:
        raise ValueError("Level property not found after AccessLevel tag")
    cursor = level_idx + len(_LEVEL_TAG) + 8  # skip Size(int64)
    enum_name, cursor = _read_fstring(data, cursor)
    if enum_name != _ACCESS_LEVEL_ENUM_NAME:
        raise ValueError(f"Unexpected enum name {enum_name!r} at offset {cursor}")
    cursor = _expect(data, cursor, b"\x00")  # separator byte
    level, cursor = _read_fstring(data, cursor)

    squad_idx = data.find(_SQUAD_ID_TAG, cursor, cursor + 80)
    if squad_idx < 0:
        raise ValueError("SquadId property not found after Level property")
    value_start = squad_idx + len(_SQUAD_ID_TAG) + 8  # skip Size(int64)
    terminator = data.find(b"\x05\x00\x00\x00" + b"None\x00", value_start, value_start + 24)
    if terminator < 0:
        raise ValueError("SquadId value terminator not found")
    (squad_value,) = struct.unpack_from("<i", data, terminator - 4)
    squad_id = squad_value if squad_value != 0 else None
    return level, squad_id, terminator + 9


def _parse_storage_element(
    data: bytes, tag_pos: int, map_hint: str | None
) -> tuple[RefineryOrder, int]:
    """Parse one ``MapDetailRefineryStorage`` element.

    Args:
        data (bytes): Buffer to read from.
        tag_pos (int): Offset of the element's ``AccessLevel`` property tag
            (i.e. after the array element header and struct GUID).
        map_hint (str | None): Map id of the surrounding details block.

    Returns:
        tuple[RefineryOrder, int]: The parsed order and the position after the
        element.

    Raises:
        ValueError: If the expected property patterns are not found.
    """
    level, squad_id, pos = _parse_access_level(data, tag_pos)

    index_idx = data.find(_INDEX_TAG, pos, pos + 120)
    if index_idx < 0:
        raise ValueError("Index property not found after AccessLevel struct")
    refined_idx = data.find(_REFINED_TAG, index_idx, index_idx + 120)
    if refined_idx < 0:
        raise ValueError("Refined property not found after Index property")
    # The Index byte sits directly before the Refined property name prefix,
    # preceded by a 0x00 flag byte (the Index ByteProperty itself serializes
    # its enum name "None" as an FString right after the tag).
    if data[refined_idx - 2] != 0:
        raise ValueError("Unexpected bytes before Refined property")
    index = data[refined_idx - 1]

    refined_tag_end = refined_idx + len(_REFINED_TAG) + 8  # skip Size(int64)
    if data[refined_tag_end] != 0:
        raise ValueError("Unexpected flag byte before Refined value")
    (refined,) = struct.unpack_from("<H", data, refined_tag_end + 1)
    order = RefineryOrder(
        raw_access_level=level,
        access_level=level.rsplit("::", 1)[-1].lower(),
        squad_id=squad_id,
        index=index,
        refined=refined,
        map_hint=map_hint,
    )
    return order, refined_tag_end + 3


def _is_storage_element_header(data: bytes, tag_pos: int) -> bool:
    """Check whether an ``AccessLevel`` tag belongs to a refinery storage element.

    The element header immediately before the tag must contain the struct
    name ``MapDetailRefineryStorage`` followed by the 16-byte struct GUID
    (array element headers carry one extra filler byte, so a small tolerance
    is applied).

    Args:
        data (bytes): Buffer to read from.
        tag_pos (int): Offset of the ``AccessLevel`` property tag.

    Returns:
        bool: True when the preceding bytes form a valid element header.
    """
    pattern = b"\x19\x00\x00\x00" + _STORAGE_STRUCT_NAME
    window = data[max(0, tag_pos - 60) : tag_pos]
    name_pos = window.find(pattern)
    if name_pos < 0:
        return False
    remaining = len(window) - (name_pos + len(pattern))
    return 16 <= remaining <= 17


def _map_id_before(data: bytes, pos: int) -> str | None:
    """Find the last ``EWorldConquestMapId::X`` string before ``pos``.

    Args:
        data (bytes): Buffer to read from.
        pos (int): Offset to search backwards from.

    Returns:
        str | None: The map id (e.g. ``DeadLandsHex``) or ``None``.
    """
    result: str | None = None
    for match in _MAP_ID_PATTERN.finditer(data, 0, pos):
        result = match.group(1).decode("latin1")
    return result


def parse_map_data(path: Path) -> MapDataReport:
    """Scan a ``MapData.sav`` file for refinery storage orders.

    The refinery storage array stores one element per bay. Only the first
    element repeats the array element header; consecutive order elements
    directly follow the previous element's terminator, so after each header
    match the following elements are chained while the pattern holds.

    Foxhole stores the facility details twice (``InitalMapItemDetails`` and
    ``RecentMapItemDetails``), so the same facility can appear as two chains
    of bays. When two chains in the same map share the access level, squad id
    and at least one recipe slot index, the earlier chain is treated as a
    stale snapshot and dropped in favour of the later one.

    Args:
        path (Path): Path to the ``<steamid>_MapData.sav`` file.

    Returns:
        MapDataReport: The latest refinery bay orders found in the file, with
        stale duplicate snapshots removed.

    Raises:
        ValueError: If the file is not a GVAS save.
    """
    data = path.read_bytes()
    if not data.startswith(_GVAS_MAGIC):
        raise ValueError(f"Not a GVAS save file: {path}")
    chains: list[list[RefineryOrder]] = []
    element_terminator = b"\x05\x00\x00\x00" + b"None\x00"
    for match in re.finditer(re.escape(_ACCESS_LEVEL_TAG), data):
        tag_pos = match.start()
        if not _is_storage_element_header(data, tag_pos):
            continue
        map_hint = _map_id_before(data, tag_pos)
        pos = tag_pos
        chain: list[RefineryOrder] = []
        while True:
            try:
                order, pos = _parse_storage_element(data, pos, map_hint)
            except ValueError:
                break
            chain.append(order)
            if data[pos : pos + len(element_terminator)] != element_terminator:
                break
            pos += len(element_terminator)
            if not data[pos:].startswith(_ACCESS_LEVEL_TAG):
                break
        if chain:
            chains.append(chain)
    return MapDataReport(source=path, orders=_dedupe_chains(chains))


def _dedupe_chains(chains: list[list[RefineryOrder]]) -> list[RefineryOrder]:
    """Drop stale duplicate facility snapshots, keeping the latest chain.

    Two chains describing the same facility share the map hint, access level,
    squad id and at least one recipe slot index. Chains appear in file order
    (initial details before recent details), so the later chain wins.

    Args:
        chains (list[list[RefineryOrder]]): Parsed bay chains in file order.

    Returns:
        list[RefineryOrder]: Orders of the latest chain per facility.
    """
    kept: list[list[RefineryOrder]] = []
    for chain in chains:
        slots = {order.index for order in chain}
        key = (chain[0].map_hint, chain[0].access_level, chain[0].squad_id)
        duplicate = False
        for prev_idx, prev in enumerate(kept):
            prev_key = (prev[0].map_hint, prev[0].access_level, prev[0].squad_id)
            prev_slots = {order.index for order in prev}
            if key == prev_key and slots & prev_slots:
                kept[prev_idx] = chain
                duplicate = True
                break
        if not duplicate:
            kept.append(chain)
    return [order for chain in kept for order in chain]

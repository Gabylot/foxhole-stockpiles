"""Tests for the refinery MapData.sav parser."""

import struct
from pathlib import Path

import pytest

from fs_tools.services.refinery_save_parser import parse_map_data

# ---------------------------------------------------------------------------
# Fixture builders: reproduce the empirically verified byte layout of the
# MapDetailRefineryStorage elements written by Foxhole.
# ---------------------------------------------------------------------------


def _fstring(value: str) -> bytes:
    encoded = value.encode("latin1") + b"\x00"
    return struct.pack("<i", len(encoded)) + encoded


def _tag(name: str, type_name: str, size: int) -> bytes:
    return _fstring(name) + _fstring(type_name) + struct.pack("<i", size) + b"\x00\x00\x00\x00"


def _access_level_struct(level: str, squad_id: int) -> bytes:
    level_value = f"ERefineryOrderAccessLevel::{level}"
    out = _tag("AccessLevel", "StructProperty", 0x99)
    out += _fstring("RefineryOrderAccessLevel")
    out += b"\x00" * 16  # struct GUID
    out += b"\x00"  # trailing struct flag byte
    out += _tag("Level", "EnumProperty", len(level_value) + 1)
    out += _fstring("ERefineryOrderAccessLevel")
    out += b"\x00"  # separator byte
    out += _fstring(level_value)
    out += b"\x05\x00\x00\x00" + b"None\x00"
    out += _tag("SquadId", "IntProperty", 4)
    out += struct.pack("<i", squad_id)
    out += b"\x05\x00\x00\x00" + b"None\x00"
    return out


def _index_property(index: int) -> bytes:
    return (
        _tag("Index", "ByteProperty", 1)
        + _fstring("None")
        + b"\x00"  # flag byte
        + bytes([index])
    )


def _refined_property(refined: int) -> bytes:
    return _tag("Refined", "UInt16Property", 2) + b"\x00" + struct.pack("<H", refined)


def _storage_element(level: str, squad_id: int, index: int, refined: int) -> bytes:
    """One full refinery storage element including the array element header."""
    out = _fstring("RefineryStorages")
    out += _fstring("StructProperty")
    out += struct.pack("<i", 0x99)
    out += b"\x00\x00\x00\x00"
    out += _fstring("MapDetailRefineryStorage")
    out += b"\x00" * 16  # struct GUID
    out += b"\x00"  # array element filler byte
    out += _access_level_struct(level, squad_id)
    out += _index_property(index)
    out += _refined_property(refined)
    out += b"\x05\x00\x00\x00" + b"None\x00"
    return out


def _build_save(elements: bytes, map_id: str = "TerminusHex") -> bytes:
    """Build a minimal fake MapData.sav around the given storage elements."""
    header = b"GVAS" + b"\x00" * 64
    map_hint = _fstring(f"EWorldConquestMapId::{map_id}")
    return header + map_hint + elements


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestParseMapData:
    """Tests for parse_map_data."""

    def test_rejects_non_gvas_file(self, tmp_path: Path) -> None:
        """A file without the GVAS magic raises ValueError."""
        save = tmp_path / "x_MapData.sav"
        save.write_bytes(b"NOTGVAS" + b"\x00" * 32)
        with pytest.raises(ValueError, match="Not a GVAS save file"):
            parse_map_data(save)

    def test_empty_save_yields_no_orders(self, tmp_path: Path) -> None:
        """A GVAS save without storage elements yields no orders."""
        save = tmp_path / "x_MapData.sav"
        save.write_bytes(b"GVAS" + b"\x00" * 64)
        report = parse_map_data(save)
        assert report.orders == []

    def test_single_squad_order(self, tmp_path: Path) -> None:
        """A single squad storage element is parsed completely."""
        save = tmp_path / "x_MapData.sav"
        save.write_bytes(_build_save(_storage_element("Squad", 206, 5, 1128)))
        report = parse_map_data(save)
        assert len(report.orders) == 1
        order = report.orders[0]
        assert order.index == 5
        assert order.refined == 1128
        assert order.squad_id == 206
        assert order.is_squad_order is True
        assert order.access_level == "squad"
        assert order.map_hint == "TerminusHex"

    def test_personal_order_has_no_squad_id(self, tmp_path: Path) -> None:
        """A personal storage element reports no squad id."""
        save = tmp_path / "x_MapData.sav"
        save.write_bytes(_build_save(_storage_element("Personal", 0, 1, 42)))
        report = parse_map_data(save)
        assert len(report.orders) == 1
        order = report.orders[0]
        assert order.squad_id is None
        assert order.is_squad_order is False
        assert order.access_level == "personal"

    def test_chained_elements_are_all_parsed(self, tmp_path: Path) -> None:
        """Consecutive elements without repeated headers are all parsed."""
        terminator = b"\x05\x00\x00\x00" + b"None\x00"
        elements = (
            _storage_element("Squad", 206, 0, 18060)  # already ends with a terminator
            + _access_level_struct("Squad", 206)
            + _index_property(6)
            + _refined_property(0)
            + terminator
            + _access_level_struct("Squad", 206)
            + _index_property(1)
            + _refined_property(5309)
            + terminator
        )
        save = tmp_path / "x_MapData.sav"
        save.write_bytes(_build_save(elements))
        report = parse_map_data(save)
        assert [(o.index, o.refined) for o in report.orders] == [
            (0, 18060),
            (6, 0),
            (1, 5309),
        ]

    def test_invalid_element_is_skipped(self, tmp_path: Path) -> None:
        """A corrupted element does not abort parsing of later elements."""
        good = _storage_element("Squad", 206, 2, 837)
        # Corrupt the index value position of an otherwise valid copy.
        broken = bytearray(_storage_element("Squad", 206, 9, 0))
        broken[-12] = 0x7F  # break the 0x00 flag byte before the Refined value
        save = tmp_path / "x_MapData.sav"
        save.write_bytes(_build_save(bytes(broken) + b"\x00" * 8 + good))
        report = parse_map_data(save)
        assert [(o.index, o.refined) for o in report.orders] == [(2, 837)]

"""Tests for CatalogService."""

import json
from pathlib import Path
from unittest.mock import patch

from foxhole_stockpiles.services.catalog_service import CatalogEntry, CatalogService


class TestCatalogEntry:
    """Tests for CatalogEntry model."""

    def test_catalog_entry_creation(self) -> None:
        """Test CatalogEntry can be created with required fields."""
        entry = CatalogEntry(code="ItemCode1", display_name="Item Name 1")

        assert entry.code == "ItemCode1"
        assert entry.display_name == "Item Name 1"


class TestCatalogService:
    """Tests for CatalogService."""

    def test_init_without_path(self) -> None:
        """Test service can be initialized without catalog path."""
        service = CatalogService(catalog_path=None)

        assert service._catalog_path is None
        assert service._loaded is False
        assert service._catalog == {}

    def test_init_with_path(self, tmp_path: Path) -> None:
        """Test service can be initialized with catalog path."""
        catalog_path = tmp_path / "catalog.json"
        service = CatalogService(catalog_path=catalog_path)

        assert service._catalog_path == catalog_path
        assert service._loaded is False

    def test_load_without_path_returns_codes(self) -> None:
        """Test that lookups return codes when no path is configured."""
        service = CatalogService(catalog_path=None)

        result = service.get_display_name("SomeCode")

        assert result == "SomeCode"
        assert service._loaded is True

    def test_load_with_missing_file(self, tmp_path: Path) -> None:
        """Test that missing catalog file is handled gracefully."""
        catalog_path = tmp_path / "nonexistent.json"
        service = CatalogService(catalog_path=catalog_path)

        result = service.get_display_name("SomeCode")

        assert result == "SomeCode"
        assert service._loaded is True

    def test_load_valid_catalog(self, tmp_path: Path) -> None:
        """Test loading a valid catalog file."""
        catalog_path = tmp_path / "catalog.json"
        catalog_data = [
            {"CodeName": "Item1", "DisplayName": "First Item"},
            {"CodeName": "Item2", "DisplayName": "Second Item"},
            {"CodeName": "Item3"},  # No display name
        ]
        catalog_path.write_text(json.dumps(catalog_data))

        service = CatalogService(catalog_path=catalog_path)

        assert service.get_display_name("Item1") == "First Item"
        assert service.get_display_name("Item2") == "Second Item"
        assert service.get_display_name("Item3") == "Item3"  # Falls back to code
        assert service.get_display_name("Unknown") == "Unknown"  # Not in catalog

    def test_load_invalid_json(self, tmp_path: Path) -> None:
        """Test handling of invalid JSON in catalog file."""
        catalog_path = tmp_path / "catalog.json"
        catalog_path.write_text("{ invalid json }")

        service = CatalogService(catalog_path=catalog_path)

        # Should not raise, should return code
        result = service.get_display_name("SomeCode")

        assert result == "SomeCode"
        assert service._loaded is True

    def test_load_only_once(self, tmp_path: Path) -> None:
        """Test that catalog is loaded only once (lazy loading)."""
        catalog_path = tmp_path / "catalog.json"
        catalog_data = [{"CodeName": "Item1", "DisplayName": "First Item"}]
        catalog_path.write_text(json.dumps(catalog_data))

        service = CatalogService(catalog_path=catalog_path)

        # First call loads
        assert service._loaded is False
        _ = service.get_display_name("Item1")
        assert service._loaded is True

        # Modify file (shouldn't affect loaded data)
        catalog_data[0]["DisplayName"] = "Modified"
        catalog_path.write_text(json.dumps(catalog_data))

        # Should still return original value
        assert service.get_display_name("Item1") == "First Item"

    def test_catalog_entries_without_codename_skipped(self, tmp_path: Path) -> None:
        """Test that entries without CodeName are skipped."""
        catalog_path = tmp_path / "catalog.json"
        catalog_data = [
            {"CodeName": "Item1", "DisplayName": "First Item"},
            {"DisplayName": "No Code Item"},  # No CodeName
            {"CodeName": "", "DisplayName": "Empty Code"},  # Empty CodeName
            {"CodeName": "Item2", "DisplayName": "Second Item"},
        ]
        catalog_path.write_text(json.dumps(catalog_data))

        service = CatalogService(catalog_path=catalog_path)

        # Only items with non-empty CodeName should be loaded
        assert service.get_display_name("Item1") == "First Item"
        assert service.get_display_name("Item2") == "Second Item"
        assert service.get_display_name("") == ""


class TestCatalogCategories:
    """Tests for category resolution, including uncategorised items."""

    def test_strips_unreal_enum_prefix(self, tmp_path: Path) -> None:
        """Test the Unreal enum prefix and CamelCase are made readable."""
        catalog_path = tmp_path / "catalog.json"
        catalog_path.write_text(
            json.dumps([{"CodeName": "Cloth", "ItemCategory": "EItemCategory::Supplies"}])
        )
        service = CatalogService(catalog_path=catalog_path)

        # The game's "Supplies" category is grouped under "Resource".
        assert service.get_category("Cloth") == "Resource"

    def test_crated_stock_gets_its_own_category(self, tmp_path: Path) -> None:
        """Test crated and loose stock are grouped separately.

        A crate is a different good from its contents, so "Vehicle" and
        "Vehicle Crates" are distinct groups.
        """
        catalog_path = tmp_path / "catalog.json"
        catalog_path.write_text(
            json.dumps(
                [{"CodeName": "TruckC", "VehicleDynamicData": {"ResourcesPerBuildCycle": 1}}]
            )
        )
        service = CatalogService(catalog_path=catalog_path)

        assert service.get_category("TruckC") == "Vehicle"
        assert service.get_category_for("TruckC", crated=False) == "Vehicle"
        assert service.get_category_for("TruckC", crated=True) == "Vehicle Crates"

    def test_crated_name_gets_crate_suffix(self, tmp_path: Path) -> None:
        """Test a cratable item's crated name gains the "Crate" suffix."""
        catalog_path = tmp_path / "catalog.json"
        catalog_path.write_text(
            json.dumps(
                [
                    {
                        "CodeName": "RifleC",
                        "DisplayName": "Argenti r.II Rifle",
                        "ItemCategory": "EItemCategory::SmallArms",
                        "ItemProfileData": {"bIsCratable": True},
                    }
                ]
            )
        )
        service = CatalogService(catalog_path=catalog_path)

        assert service.get_display_name("RifleC") == "Argenti r.II Rifle"
        assert service.get_crated_display_name("RifleC") == "Argenti r.II Rifle Crate"

    def test_non_cratable_item_has_no_crated_name(self, tmp_path: Path) -> None:
        """Test a good that cannot be crated reports no crated name."""
        catalog_path = tmp_path / "catalog.json"
        catalog_path.write_text(
            json.dumps(
                [
                    {
                        "CodeName": "Cloth",
                        "DisplayName": "Basic Materials",
                        "ItemCategory": "Supplies",
                    }
                ]
            )
        )
        service = CatalogService(catalog_path=catalog_path)

        assert service.get_crated_display_name("Cloth") is None

    def test_category_sort_key_follows_web_app_order(self) -> None:
        """Test categories sort in supply order, not alphabetically.

        Small arms and munitions come first, vehicles and shippables last, with
        each crate group directly before its loose counterpart.
        """
        order = [
            "Small Arms Crates",
            "Heavy Arms Crates",
            "Heavy Ammo Crates",
            "Utility Crates",
            "Medical Crates",
            "Resource Crates",
            "Uniform Crates",
            "Vehicle Crates",
            "Vehicle",
            "Shippables Crates",
            "Shippables",
        ]
        keys = [CatalogService.category_sort_key(name) for name in order]

        assert keys == sorted(keys)

    def test_unknown_category_sorts_last(self) -> None:
        """Test an unrecognised category is placed after the known ones."""
        assert CatalogService.category_sort_key("Widgets") > CatalogService.category_sort_key(
            "Shippables"
        )

    def test_group_items_uses_web_app_category_order(self, tmp_path: Path) -> None:
        """Test rendered blocks follow the catalog's category order."""
        import foxhole_stockpiles.services.image_renderer as renderer_module
        from foxhole_stockpiles.services.catalog_service import CatalogService

        catalog_path = tmp_path / "catalog.json"
        catalog_path.write_text(
            json.dumps(
                [
                    {
                        "CodeName": "Bandages",
                        "ItemCategory": "EItemCategory::Medical",
                        "ItemProfileData": {"bIsCratable": True},
                    },
                    {
                        "CodeName": "TruckC",
                        "VehicleDynamicData": {"ResourcesPerBuildCycle": 1},
                    },
                ]
            )
        )
        service = CatalogService(catalog_path=catalog_path)

        from foxhole_stockpiles.models.stockpile import Stockpile
        from foxhole_stockpiles.models.stockpile_item import StockpileItem

        stockpile = Stockpile(
            items=[
                StockpileItem(code="TruckC", quantity=1),
                StockpileItem(code="Bandages", quantity=2, crated=True),
            ]
        )

        with patch(
            "foxhole_stockpiles.services.catalog_service.get_catalog_service",
            return_value=service,
        ):
            blocks = renderer_module.StockpileImageRenderer._group_items(stockpile)

        # Medical Crates sorts before Vehicle Crates even though "Vehicle"
        # comes after "Medical" alphabetically in both forms.
        assert [block.name for block in blocks] == ["Medical Crates", "Vehicle"]

    def test_crated_name_is_not_doubled(self, tmp_path: Path) -> None:
        """Test a name already ending in "Crate" is not suffixed twice."""
        catalog_path = tmp_path / "catalog.json"
        catalog_path.write_text(
            json.dumps(
                [
                    {
                        "CodeName": "Bandages",
                        "DisplayName": "Bandages Crate",
                        "ItemCategory": "EItemCategory::Medical",
                        "ItemProfileData": {"bIsCratable": True},
                    }
                ]
            )
        )
        service = CatalogService(catalog_path=catalog_path)

        assert service.get_crated_display_name("Bandages") == "Bandages Crate"

    def test_category_falls_back_to_dynamic_data(self, tmp_path: Path) -> None:
        """Test items with no ItemCategory are grouped by their dynamic data.

        Vehicles and structures carry no ItemCategory, so grouping falls back to
        the dynamic-data blocks that identify them.
        """
        entries = [
            {"CodeName": "TruckC", "VehicleDynamicData": {"ResourcesPerBuildCycle": 1}},
            {"CodeName": "EmplacedAAC", "StructureDynamicData": {"Health": 100}},
            {"CodeName": "ConcreteMixer", "StructureDynamicData": {"Health": 100}},
            {"CodeName": "Mystery"},
        ]
        catalog_path = tmp_path / "catalog.json"
        catalog_path.write_text(json.dumps(entries))
        service = CatalogService(catalog_path=catalog_path)

        assert service.get_category("TruckC") == "Vehicle"
        assert service.get_category("EmplacedAAC") == "Shippables"
        assert service.get_category("ConcreteMixer") == "Shippables"
        # Anything else falls back to resources.
        assert service.get_category("Mystery") == "Resource"

    def test_every_entry_gets_a_category(self, tmp_path: Path) -> None:
        """Test no catalog entry is left uncategorised.

        Without a fallback the renderer puts uncategorised items into a single
        catch-all group, which is what this guards against.
        """
        catalog_path = tmp_path / "catalog.json"
        catalog_path.write_text(
            json.dumps(
                [
                    {"CodeName": "Cloth", "ItemCategory": "EItemCategory::Supplies"},
                    {"CodeName": "TruckC"},
                    {"CodeName": "ShipPart1"},
                    {"CodeName": "Unknown"},
                ]
            )
        )
        service = CatalogService(catalog_path=catalog_path)

        for code in ("Cloth", "TruckC", "ShipPart1", "Unknown"):
            assert service.get_category(code) is not None

    def test_known_category_wins_over_fallback(self, tmp_path: Path) -> None:
        """Test a real ItemCategory is not overridden by the dynamic data."""
        catalog_path = tmp_path / "catalog.json"
        catalog_path.write_text(
            json.dumps(
                [
                    {
                        "CodeName": "Cloth",
                        "ItemCategory": "EItemCategory::Supplies",
                        "VehicleDynamicData": {"ResourcesPerBuildCycle": 1},
                    }
                ]
            )
        )
        service = CatalogService(catalog_path=catalog_path)

        assert service.get_category("Cloth") == "Resource"

    def test_crate_categories_collapse_onto_their_family(self, tmp_path: Path) -> None:
        """Test the base categories of crate variants are still resolvable.

        A crate variant has its own ItemCategory, but it must resolve to the same
        family as the loose item so the two groups stay adjacent.
        """
        catalog_path = tmp_path / "catalog.json"
        catalog_path.write_text(
            json.dumps(
                [
                    {"CodeName": "TruckC", "VehicleDynamicData": {"ResourcesPerBuildCycle": 1}},
                    {"CodeName": "TruckCCrate", "ItemCategory": "EItemCategory::VehicleCrates"},
                    {"CodeName": "ShipPart1", "StructureDynamicData": {"Health": 100}},
                    {
                        "CodeName": "ShipPart1Crate",
                        "ItemCategory": "EItemCategory::ShippableCrates",
                    },
                ]
            )
        )
        service = CatalogService(catalog_path=catalog_path)

        assert service.get_category("TruckC") == service.get_category("TruckCCrate")
        assert service.get_category("ShipPart1") == service.get_category("ShipPart1Crate")

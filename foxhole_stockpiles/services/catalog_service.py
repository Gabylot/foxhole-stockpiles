"""Catalog service for loading and querying item catalog data."""

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Unreal enums are stored as "EItemCategory::Supplies"; only the label is useful.
_ENUM_PREFIX = "::"

# Items the game leaves without an ItemCategory are still grouped: anything
# carrying VehicleDynamicData is a vehicle, anything carrying
# StructureDynamicData is a structure, and whatever is left is a consumable.
# Without this they all collapse into one catch-all group.
_SUPPLIES_CATEGORY = "Resource"
_STRUCTURES_CATEGORY = "Shippables"
_VEHICLES_CATEGORY = "Vehicle"

# ItemCategory enum values mapped to the base category names used for grouping.
# The crate variants stay separate from their base item, because a crate is a
# different good: a crate of rifles is not the same thing as loose rifles.
_CATEGORY_LABELS = {
    "Small Arms": "Small Arms",
    "Heavy Arms": "Heavy Arms",
    "Heavy Ammo": "Heavy Ammo",
    "Utility": "Utility",
    "Medical": "Medical",
    "Supplies": "Resource",
    "Uniforms": "Uniform",
    "Vehicle Crates": "Vehicle",
    "Vehicles": "Vehicle",
    "Shippable Crates": "Shippables",
    "Shippables": "Shippables",
}

# Suffix appended to a category name when the stock is in a crate.
_CRATE_CATEGORY_SUFFIX = " Crates"

# Suffix appended to an item's name when it is held in a crate.
_CRATE_NAME_SUFFIX = " Crate"

# Display order for rendered category blocks. This is a supply order rather than
# an alphabetical one, so related goods read together: light arms and munitions
# first, vehicles and shippables last.
_CATEGORY_ORDER = (
    "Small Arms",
    "Heavy Arms",
    "Heavy Ammo",
    "Utility",
    "Medical",
    "Resource",
    "Uniform",
    "Vehicle",
    "Shippables",
)


def _strip_enum_prefix(value: Any) -> str | None:
    """Turn an Unreal enum value into a readable label.

    Args:
        value: Raw catalog value, e.g. ``"EItemCategory::SmallArms"`` or ``None``.

    Returns:
        str | None: ``"Small Arms"``, or ``None`` when there is no category.
    """
    if not value or not isinstance(value, str):
        return None
    label = value.split(_ENUM_PREFIX, 1)[-1].strip()
    if not label:
        return None
    # "SmallArms" -> "Small Arms"
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", label)
    return spaced[:1].upper() + spaced[1:]


def _fallback_category(item: dict[str, Any]) -> str:
    """Derive a category for items the game leaves without an ItemCategory.

    Vehicles and structures carry no ``ItemCategory``, so grouping uses the
    dynamic-data blocks that identify them.

    Args:
        item: One raw catalog entry.

    Returns:
        str: The category the item belongs to.
    """
    if item.get("VehicleDynamicData"):
        return _VEHICLES_CATEGORY
    if item.get("StructureDynamicData"):
        return _STRUCTURES_CATEGORY
    return _SUPPLIES_CATEGORY


def _is_cratable(item: dict[str, Any]) -> bool:
    """Report whether the game lets this item be stored in a crate.

    Args:
        item: One raw catalog entry.

    Returns:
        bool: True when the item can be crated.
    """
    # Facility variants are never crated.
    if item.get("VehicleBuildType") == "EVehicleBuildType::VehicleFacility":
        return False

    profile = item.get("ItemProfileData") or {}
    if profile.get("bIsCratable"):
        return True

    shippable = item.get("ShippableInfo")
    if isinstance(shippable, dict) and shippable.get("bAllowPackagingToCrate"):
        return True

    # Most vehicles declare their build categories instead.
    return bool(item.get("ProductionCategories"))


class CatalogEntry(BaseModel):
    """Catalog entry with item code and display name."""

    code: str
    display_name: str
    category: str | None = None
    cratable: bool = False


class CatalogService:
    """Service for loading and querying item catalog data.

    Provides code-to-display-name lookups for the web interface.
    """

    def __init__(self, catalog_path: Path | None = None) -> None:
        """Initialize the catalog service.

        Args:
            catalog_path: Path to the catalog.json file. If None, lookups will return codes.
        """
        self._catalog_path = catalog_path
        self._catalog: dict[str, CatalogEntry] = {}
        self._loaded = False

    def _load(self) -> None:
        """Load catalog from file. Called lazily on first access."""
        if self._loaded:
            return

        self._loaded = True

        if not self._catalog_path:
            logger.debug("No catalog path configured, display names will use codes")
            return

        if not self._catalog_path.exists():
            logger.warning("Catalog file not found at %s", self._catalog_path)
            return

        try:
            with self._catalog_path.open(encoding="utf-8") as f:
                catalog_data = json.load(f)

            for item in catalog_data:
                code = item.get("CodeName")
                display_name = item.get("DisplayName", code)
                if code:
                    # Vehicles and structures carry no ItemCategory; derive
                    # theirs from the dynamic-data blocks so they do not all
                    # land in one catch-all group.
                    label = _strip_enum_prefix(item.get("ItemCategory"))
                    category = _CATEGORY_LABELS.get(label or "", label) or _fallback_category(item)
                    self._catalog[code] = CatalogEntry(
                        code=code,
                        display_name=display_name,
                        category=category,
                        cratable=_is_cratable(item),
                    )

            logger.info("Loaded %d items from catalog", len(self._catalog))

        except json.JSONDecodeError as e:
            logger.error("Failed to parse catalog file %s: %s", self._catalog_path, e)
        except Exception as e:  # noqa: BLE001 - keep the app usable with an empty catalog
            logger.error("Failed to load catalog: %s", e)

    def get_display_name(self, code: str) -> str:
        """Get display name for an item code.

        Args:
            code: The item code to look up.

        Returns:
            The display name if found, otherwise returns the code itself.
        """
        self._load()
        entry = self._catalog.get(code)
        return entry.display_name if entry else code

    def get_category(self, code: str) -> str | None:
        """Get the item's category label, used to group rows in rendered output.

        Args:
            code: The item code to look up.

        Returns:
            str | None: A readable base category label (e.g. ``"Small Arms"``),
                or None when the item is unknown or has no category.
        """
        self._load()
        entry = self._catalog.get(code)
        return entry.category if entry else None

    def get_category_for(self, code: str, *, crated: bool = False) -> str | None:
        """Get the category label to render an item under.

        Crated and loose stock are grouped separately, since a crate is a
        different good from its contents.

        Args:
            code: The item code to look up.
            crated: Whether the stock is held in a crate.

        Returns:
            str | None: The category label, or None when the item is unknown.
        """
        category = self.get_category(code)
        if category is None or not crated:
            return category
        return f"{category}{_CRATE_CATEGORY_SUFFIX}"

    @staticmethod
    def category_sort_key(name: str) -> tuple[int, int, str]:
        """Build the sort key that orders the rendered category blocks.

        Loose and crated groups of the same family stay adjacent, with the
        crated group first, so the two read together.

        Args:
            name: A category label, e.g. ``"Vehicle Crates"``.

        Returns:
            tuple[int, int, str]: Family rank, loose flag, then the label so
                unknown categories sort last but deterministically.
        """
        if name.endswith(_CRATE_CATEGORY_SUFFIX):
            base = name[: -len(_CRATE_CATEGORY_SUFFIX)]
            # Crated groups sort ahead of loose ones within a family: the web
            # app orders "Vehicle Crates" (id 8) ahead of "Vehicle" (id 9).
            is_loose = 0
        else:
            base = name
            is_loose = 1

        try:
            rank = _CATEGORY_ORDER.index(base)
        except ValueError:
            # Unrecognised categories go after the known ones.
            rank = len(_CATEGORY_ORDER)
        return (rank, is_loose, name)

    def get_crated_display_name(self, code: str) -> str | None:
        """Get the display name for an item held inside a crate.

        A crate is stored as its own row named ``"<item> Crate"``, sharing
        the base item's code, so the crated and loose forms read the same way.

        Args:
            code: The item code to look up.

        Returns:
            str | None: The crated display name, or None when the item is not a
                cratable good.
        """
        self._load()
        entry = self._catalog.get(code)
        if entry is None or not entry.cratable:
            return None
        name = entry.display_name
        # Avoid "X Crate Crate" if the name already carries the suffix.
        return name if name.endswith(_CRATE_NAME_SUFFIX) else f"{name}{_CRATE_NAME_SUFFIX}"


@lru_cache
def get_catalog_service() -> CatalogService:
    """Get a process-wide cached catalog service built from current settings.

    Returns:
        CatalogService: A catalog service using the configured catalog file.
    """
    from foxhole_stockpiles.core.settings import get_settings

    catalog_path = get_settings().database_builder.catalog_file
    return CatalogService(catalog_path=catalog_path)

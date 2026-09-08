"""Read refinery queue data from Foxhole's MapData.sav file."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from foxhole_stockpiles.enums.stockpile_type import StockpileType
from foxhole_stockpiles.handlers.stockpile_json import stockpiles_to_json_payload
from foxhole_stockpiles.models.stockpile import Stockpile
from foxhole_stockpiles.models.stockpile_item import StockpileItem
from fs_tools.services.refinery_save_parser import MapDataReport, RefineryOrder, parse_map_data

_DEFAULT_SAVE_GAMES_DIR = Path.home() / "AppData" / "Local" / "Foxhole" / "Saved" / "SaveGames"
_RECIPE_FILE_NAME = "refinery_recipes.json"


def _find_newest_save(save_dir: Path) -> Path:
    """Return the newest ``*_MapData.sav`` file in a SaveGames directory.

    Args:
        save_dir (Path): Directory containing the save files.

    Returns:
        Path: Path to the most recently modified map data save.

    Raises:
        typer.Exit: If no save file exists in the directory.
    """
    candidates = sorted(
        save_dir.glob("*_MapData.sav"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not candidates:
        typer.echo(f"No *_MapData.sav files found in {save_dir}", err=True)
        raise typer.Exit(code=1)
    return candidates[0]


def _find_recipe_file() -> Path:
    """Locate the bundled ``refinery_recipes.json``.

    The mapping lives alongside this command module so it stays in version
    control (the repository ``data/`` directory is gitignored).

    Returns:
        Path: Path to the mapping file.

    Raises:
        typer.Exit: If the file cannot be located.
    """
    module_dir = Path(__file__).resolve().parent
    bundled = module_dir / _RECIPE_FILE_NAME
    if bundled.is_file():
        return bundled
    typer.echo(
        f"Error: recipe mapping {_RECIPE_FILE_NAME} not found next to the sav_reader module:"
        f" {module_dir}",
        err=True,
    )
    raise typer.Exit(code=1)


def _load_recipes(recipes: Path | None) -> dict[int, dict[str, str]]:
    """Load the recipe slot mapping.

    Args:
        recipes (Path | None): Explicit mapping file path, or ``None`` to use
            the bundled ``refinery_recipes.json`` next to this module.

    Returns:
        dict[int, dict[str, str]]: Mapping of recipe slot index to
        ``code_name`` and ``display_name``.

    Raises:
        typer.Exit: If the mapping file cannot be read or parsed.
    """
    path = recipes if recipes is not None else _find_recipe_file()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        typer.echo(f"Error: cannot read recipe mapping {path}: {exc}", err=True)
        raise typer.Exit(code=1) from None
    except json.JSONDecodeError as exc:
        typer.echo(f"Error: invalid JSON in recipe mapping {path}: {exc}", err=True)
        raise typer.Exit(code=1) from None
    return {int(index): entry for index, entry in payload.get("recipes", {}).items()}


def _resolve_save_file(save_file: Path) -> Path:
    """Resolve the save file argument to an existing ``_MapData.sav`` file.

    Args:
        save_file (Path): Explicit save file or a SaveGames directory.

    Returns:
        Path: Path to the save file to parse.

    Raises:
        typer.Exit: If the path does not exist or no save file can be found.
    """
    if save_file.is_file():
        return save_file
    if save_file.is_dir():
        return _find_newest_save(save_file)
    typer.echo(f"Error: save file not found: {save_file}", err=True)
    raise typer.Exit(code=1)


def _format_stockpile(stockpile: Stockpile) -> str:
    """Format one refinery stockpile as a human readable line.

    Args:
        stockpile (Stockpile): Converted refinery stockpile.

    Returns:
        str: Formatted line.
    """
    item = stockpile.items[0] if stockpile.items else None
    item_label = f"{item.code} x{item.quantity}" if item else "empty"
    owner = f"squad {stockpile.squad_id}" if stockpile.squad_id is not None else "personal"
    reserve_tag = " [RESERVE]" if stockpile.is_reserve else ""
    hex_label = stockpile.hex or "?"
    return (
        f"  [{hex_label}] {stockpile.name}:{reserve_tag}"
        f" {item_label} ({stockpile.access_level}, {owner})"
    )


def _order_to_stockpile(
    order: RefineryOrder, recipes: dict[int, dict[str, str]]
) -> Stockpile:
    """Map one refinery order onto the shared ``Stockpile`` model.

    The refinery queue data is shaped to match the existing storage output so
    it can flow through the same webhook/handlers unchanged. Each order becomes
    a ``Stockpile`` of type ``Refinery`` whose single item is the recipe being
    produced. Squad-shared orders are flagged as reserve stockpiles
    (``is_reserve = True``) and carry their ``access_level`` and ``squad_id``.

    Args:
        order (RefineryOrder): Parsed order.
        recipes (dict[int, dict[str, str]]): Recipe slot mapping.

    Returns:
        Stockpile: The equivalent stockpile-ready representation.
    """
    recipe = recipes.get(order.index, {})
    code = recipe.get("code_name", "Unknown")
    display = recipe.get("display_name", f"slot {order.index}")
    is_squad = order.is_squad_order
    return Stockpile(
        name=display,
        type=StockpileType.REFINERY,
        hex=order.map_hint,
        is_reserve=is_squad,
        access_level=order.access_level,
        squad_id=order.squad_id,
        items=[StockpileItem(code=code, quantity=order.refined, crated=False)],
    )


def orders_to_stockpiles(
    orders: list[RefineryOrder], recipes: dict[int, dict[str, str]]
) -> list[Stockpile]:
    """Convert all refinery orders to ``Stockpile`` models.

    Args:
        orders (list[RefineryOrder]): Parsed orders (may include duplicates
            from the initial + recent details blocks).
        recipes (dict[int, dict[str, str]]): Recipe slot mapping.

    Returns:
        list[Stockpile]: One ``Stockpile`` per unique order.
    """
    result: list[Stockpile] = []
    seen: set[tuple[object, ...]] = set()
    for order in orders:
        key = (order.index, order.refined, order.squad_id, order.access_level)
        if key in seen:
            continue
        seen.add(key)
        result.append(_order_to_stockpile(order, recipes))
    return result


async def run(
    save_file: Path | None = None,
    output: Path | None = None,
    recipes: Path | None = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Read refinery queue data from a MapData.sav file.

    Args:
        save_file (Path | None): Path to a ``<steamid>_MapData.sav`` file or a
            SaveGames directory. Defaults to the newest file in the local
            Foxhole SaveGames directory.
        output (Path | None): Optional JSON output file. When omitted the
            result is printed to the console.
        recipes (Path | None): Optional recipe mapping JSON (defaults to the
            bundled ``refinery_recipes.json`` next to this module).
        verbose (bool): Enable verbose logging. Defaults to False.
        quiet (bool): Suppress all output except errors. Defaults to False.
    """
    del verbose, quiet  # parsing is silent; kept for CLI option symmetry
    resolved = _resolve_save_file(save_file if save_file is not None else _DEFAULT_SAVE_GAMES_DIR)
    report: MapDataReport = parse_map_data(resolved)
    recipe_map = _load_recipes(recipes)
    stockpiles: list[Stockpile] = orders_to_stockpiles(report.orders, recipe_map)

    if output is not None:
        payload = stockpiles_to_json_payload(stockpiles)
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        typer.echo(f"Wrote {len(stockpiles)} refinery stockpiles to {output}")
        return

    typer.echo(f"Source: {report.source}")
    if not stockpiles:
        typer.echo("No refinery orders found (open facility tooltips on the map to cache them).")
        return
    typer.echo(f"Refinery orders found: {len(stockpiles)}")
    for stockpile in stockpiles:
        typer.echo(_format_stockpile(stockpile))

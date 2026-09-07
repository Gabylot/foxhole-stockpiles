"""Read refinery queue data from Foxhole's MapData.sav file."""

from __future__ import annotations

import json
from pathlib import Path

import typer

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


def _format_order(order: RefineryOrder, recipes: dict[int, dict[str, str]]) -> str:
    """Format one order as a human readable line.

    Args:
        order (RefineryOrder): Parsed order.
        recipes (dict[int, dict[str, str]]): Recipe slot mapping.

    Returns:
        str: Formatted line.
    """
    recipe = recipes.get(order.index)
    item = recipe["display_name"] if recipe else f"Unknown slot {order.index}"
    code = recipe["code_name"] if recipe else "?"
    owner = f"squad {order.squad_id}" if order.squad_id is not None else "personal"
    map_hint = order.map_hint or "?"
    return (
        f"  [{map_hint}] slot {order.index} ({code} / {item}):"
        f" {order.refined} refined ({owner})"
    )


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

    if output is not None:
        payload = {
            "source": str(report.source),
            "orders": [
                {
                    "index": order.index,
                    "refined": order.refined,
                    "squad_id": order.squad_id,
                    "access_level": order.access_level,
                    "map_hint": order.map_hint,
                    "code_name": recipe_map.get(order.index, {}).get("code_name"),
                    "display_name": recipe_map.get(order.index, {}).get("display_name"),
                }
                for order in report.orders
            ],
        }
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        typer.echo(f"Wrote {len(report.orders)} orders to {output}")
        return

    typer.echo(f"Source: {report.source}")
    if not report.orders:
        typer.echo("No refinery orders found (open facility tooltips on the map to cache them).")
        return
    typer.echo(f"Refinery orders found: {len(report.orders)}")
    seen: set[tuple[object, ...]] = set()
    for order in report.orders:
        key = (order.index, order.refined, order.squad_id, order.access_level)
        if key in seen:
            continue
        seen.add(key)
        typer.echo(_format_order(order, recipe_map))

# Read Sav

This directory contains the tool for reading squad refinery queue data directly
from Foxhole's `MapData.sav` save file.

## Overview

The `fs-tools read-sav` tool parses the local Foxhole save file
(`<steamid>_MapData.sav`) and extracts the refinery storage bay orders it
contains — including squad-shared queues — without requiring the game to run.

## Background

Foxhole persists facility map details (including refinery storage bays) in the
local save file whenever a facility tooltip is displayed on the world map.
Each storage bay stores:

- The access level (`Personal` or `Squad`) and the owning squad id
- The recipe slot index (`Index`, 0-9) — the position of the recipe in the
  facility's in-game production list
- The refinement progress (`Refined`)

Every order carries an `access_level` derived from the game's
`ERefineryOrderAccessLevel` enum: `"squad"` when the bay is shared with the
whole squad, `"personal"` when it is refined for an individual player, or
`"public"` when it is shared with everyone.

Only facilities whose tooltips were recently opened are cached in the save,
and only bays with an assigned order are serialized.

The `Index` is *not* a global item id: it is translated through
`fs_tools/commands/sav_reader/refinery_recipes.json`, which lists the refinery
recipe slots in in-game UI order. This mapping must be re-verified after game
updates that touch the refinery.

## Usage

```bash
# Read the newest MapData.sav from the local Foxhole SaveGames directory
fs-tools read-sav

# Read a specific save file and print a table
fs-tools read-sav --save-file "C:\...\Saved\SaveGames\76561198103523496_MapData.sav"

# Write the result as JSON
fs-tools read-sav --save-file "C:\...\SaveGames" --output refinery.json

# Use a custom recipe mapping
fs-tools read-sav --recipes my_recipes.json
```

## Output format

The command emits the same `{"stockpiles": [...]}` payload as the other
stockpile commands, so refinery queues can flow through the same webhook and
handlers unchanged. Each order becomes a `Stockpile` of type `Refinery` whose
single item is the recipe being produced. Squad-shared orders are flagged with
`is_reserve: true`.

JSON fields per stockpile:

| Field | Type | Description |
|---|---|---|
| `name` | string | Resolved recipe display name (e.g. `Basic Materials`) |
| `type` | string | Always `Refinery` |
| `hex` | string | Hex region name (e.g. `TerminusHex`) |
| `is_reserve` | boolean | `true` for squad-shared queues |
| `access_level` | string | `"squad"`, `"personal"` or `"public"` |
| `squad_id` | int \| null | Owning squad id for squad queues, else `null` |
| `items` | array | Single item with `code`, `quantity` (= refined amount), `crated` |
| `timestamp` | string | When the stockpile was parsed |

## Example output

Console:

```
Source: C:\Users\...\76561198103523496_MapData.sav
Refinery orders found: 9
  [TerminusHex] Basic Materials: [RESERVE] Cloth x18060 (squad, squad 206)
  [TerminusHex] Diesel: [RESERVE] Diesel x5309 (squad, squad 206)
  [TerminusHex] Explosive Materials: [RESERVE] Explosive x837 (squad, squad 206)
  ...
```

JSON (`--output refinery.json`):

```json
{
  "stockpiles": [
    {
      "name": "Basic Materials",
      "type": "Refinery",
      "hex": "TerminusHex",
      "is_reserve": true,
      "access_level": "squad",
      "squad_id": 206,
      "items": [
        {
          "code": "Cloth",
          "quantity": 18060,
          "crated": false
        }
      ],
      "timestamp": "2026-09-08T18:07:04"
    }
  ]
}
```

## Limitations

- The save only contains data for facilities whose map tooltips were recently
  viewed by this player.
- The same order may be listed twice (initial + recent details blocks).
- The recipe slot mapping is hard-coded UI order and may change with game
  updates.

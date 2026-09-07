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

## Example output

```
Source: C:\Users\...\76561198103523496_MapData.sav
Refinery orders found: 14
  [TerminusHex] slot 0 (Cloth / Basic Materials): 18060 refined (squad 206)
  [TerminusHex] slot 1 (Diesel / Diesel): 5309 refined (squad 206)
  [TerminusHex] slot 2 (Explosive / Explosive Materials): 837 refined (squad 206)
  ...
```

## Limitations

- The save only contains data for facilities whose map tooltips were recently
  viewed by this player.
- The same order may be listed twice (initial + recent details blocks).
- The recipe slot mapping is hard-coded UI order and may change with game
  updates.

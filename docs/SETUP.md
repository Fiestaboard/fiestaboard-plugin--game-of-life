# Game of Life Setup Guide

Run Conway's Game of Life on your Vestaboard — one generation per refresh.

## Overview

The Game of Life plugin simulates a colony of cells directly on your board. No
account, API key, or network access is required; everything is computed
locally.

**Prerequisites:** none.

## Quick Setup

### 1. Enable the Plugin

In the FiestaBoard web UI, go to **Integrations**, find **Game of Life**, and
click **Enable**.

### 2. Configure

Click **Configure** and pick:

- **Cell Color** — Tile color for live cells. Choose `rainbow` to color each
  cell by age instead (newborn white, then red → violet as it survives).
- **Wrap Edges** — Leave **on** so the board behaves like a torus and gliders
  wrap around instead of dying at the edge. Turn it **off** for a hard-walled
  board, where patterns tend to settle faster.
- **Seed Pattern** — `random` for a fresh random colony each time, or one of
  the classic patterns: `glider`, `r_pentomino`, `lwss`, `gliders`.
- **Initial Density** — How full a random seed starts (0.1–0.6). Around `0.3`
  gives the longest-lived colonies; higher values die back quickly.
- **Reseed After Stable** — How many frozen or blinking generations to allow
  before starting a new colony. Default `3`.
- **Max Generations** — Hard cap before a reseed, so a long-running colony
  still gets refreshed. `0` means unlimited.
- **Refresh Interval** — Seconds between generations (minimum 10).

### 3. Add a Template

Go to **Pages**, create a page using the Game of Life plugin, and put the board
variable on the first line with **wrap** enabled:

```
{{game_of_life.game_of_life}}
```

Leave lines 2–6 empty — the variable expands to the whole board.

### 4. View Your Board

Each refresh advances the colony one generation.

---

## Mixing in the Counters

If you would rather keep a text page and just watch the numbers, the plugin
also exposes:

```
LIFE  GEN {{game_of_life.generation}}
POP {{game_of_life.population}}
SEED {{game_of_life.seed_pattern}}
```

## Tips

- **The board is noisy.** A generation can flip most of the tiles at once. Use
  a refresh interval of 60 seconds or more, and prefer the `glider` or `lwss`
  seeds if you want a quieter board — they only move a handful of tiles.
- **Colonies get boring.** Random seeds usually settle into still lifes and
  blinkers within 50–150 generations; that is what **Reseed After Stable** is
  for. Lower it to `1` or `2` for a more restless board.
- **Note boards are small.** A 3x15 grid gives cells very little room, so
  colonies settle or die fast. Lower **Reseed After Stable** and keep
  **Wrap Edges** on.
- **Rainbow mode shows motion.** Moving patterns stay white because their cells
  are constantly reborn; anything that has been sitting still drifts up the
  palette.

## Troubleshooting

**The board keeps restarting.** That is the reseed logic. Raise
**Reseed After Stable** and **Max Generations** if you want a colony to run
longer, even when it has stopped changing.

**Everything dies immediately.** Random seeds below `0.15` density rarely
survive. Raise **Initial Density** toward `0.3`, or pick a pattern seed.

**The pattern never moves.** With **Wrap Edges** off, a glider that reaches an
edge collapses into a still life. Turn wrapping back on.

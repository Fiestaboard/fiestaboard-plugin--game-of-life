# Game of Life Plugin

Conway's Game of Life, running live on your board. Every refresh advances the
colony one generation: live cells are a colored tile, dead cells are black.
When the colony dies out, settles into a still life or a period-2 oscillator,
or hits the generation cap, the board reseeds itself so it never sits frozen.

No API, no key — the simulation runs entirely on your FiestaBoard.

**→ [Setup Guide](./docs/SETUP.md)** — Configuration instructions

## How It Works

Each `fetch_data` call:

1. Applies the standard B3/S23 rules to every cell. With **Wrap Edges** on
   (the default) the grid is a torus, so gliders that leave one edge re-enter
   on the opposite side.
2. Ages every survivor by one generation (used by the `rainbow` color mode).
3. Renders the grid to board tiles and hashes it.
4. Reseeds if the population hit zero, if the grid has been unchanged or
   oscillating with period 2 for **Reseed After Stable** generations, or if
   **Max Generations** has been reached.

The grid is sized from the board it is rendering on (`self.board`), so a
Flagship runs 6x22 and a Note runs 3x15. Changing boards reseeds.

## Template Variables

```
{{game_of_life.game_of_life}}        # full board frame (use on line 1 with wrap)
{{game_of_life.generation}}          # generations since the last seed
{{game_of_life.population}}          # live cells
{{game_of_life.board_rows}}          # rows in the simulated grid
{{game_of_life.board_cols}}          # columns in the simulated grid
{{game_of_life.is_stable}}           # true once the grid stops changing
{{game_of_life.seed_pattern}}        # the configured seed pattern
```

`game_of_life_array` is also exposed: the same frame as a 2-D list of board
character codes, for templates or plugins that want the raw tiles.

## Example Template

Full-screen art — put the frame on line 1 with wrapping on and leave the rest
of the lines empty:

```
{{game_of_life.game_of_life}}
```

## Settings

| Setting                | Default  | Description                                                                                     |
| ---------------------- | -------- | ----------------------------------------------------------------------------------------------- |
| `enabled`              | `false`  | Enable the plugin                                                                                 |
| `cell_color`           | `green`  | `red`, `orange`, `yellow`, `green`, `blue`, `violet`, `white`, or `rainbow` (color by cell age)   |
| `wrap_edges`           | `true`   | Treat the board as a torus                                                                        |
| `seed_pattern`         | `random` | `random`, `glider`, `r_pentomino`, `lwss`, or `gliders`                                           |
| `initial_density`      | `0.3`    | Fraction of cells alive in a random seed (0.1–0.6)                                                |
| `reseed_after_stable`  | `3`      | Reseed after this many unchanged / period-2 generations                                           |
| `max_generations`      | `200`    | Reseed after this many generations; `0` = unlimited                                               |
| `refresh_seconds`      | `60`     | Seconds between generations (minimum 10)                                                          |

### Seed Patterns

| Pattern       | Size  | Behaviour                                                        |
| ------------- | ----- | ---------------------------------------------------------------- |
| `random`      | —     | Random fill at `initial_density`; usually the liveliest option    |
| `glider`      | 3x3   | A single glider walking diagonally across the board               |
| `r_pentomino` | 3x3   | Classic methuselah — chaotic for a long run                       |
| `lwss`        | 4x5   | Lightweight spaceship travelling sideways                         |
| `gliders`     | 3x3 each | A row of gliders, one every 7 columns (3 on Flagship, 2 on Note) |

Larger classics such as the pulsar (13x13) and the Gosper glider gun (36x9)
do not fit on either board, so they are not offered.

### Rainbow Mode

With `cell_color` set to `rainbow`, each cell is colored by how long it has
survived: newborns are white, then red, orange, yellow, green, blue, violet,
cycling back to red. Gliders and spaceships stay mostly white (their cells are
constantly reborn), while still lifes drift through the palette.

## Board Noise

Every generation flips a lot of tiles, so the physical board is loud at short
refresh intervals. `refresh_seconds` of 60 or more is a good default; pattern
seeds such as `glider` change far fewer tiles per generation than `random`.

## Author

FiestaBoard Team

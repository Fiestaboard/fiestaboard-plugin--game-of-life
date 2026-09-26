"""Game of Life plugin for FiestaBoard.

Runs Conway's Game of Life on the board grid.  Every ``fetch_data`` call
advances the simulation by one generation and renders live cells as a
coloured tile and dead cells as black.  When the colony dies out, settles
into a still life / period-2 oscillator, or reaches ``max_generations``,
the board is reseeded so it never sits frozen.

Every board gets its own colony.  The registry holds one plugin instance per
id and renders it for every board the user owns -- a Flagship, a Note, a note
array of any size from 15x3 to 120x24 -- plus boardless reads from
``GET /plugins/{id}/data``.  Simulation state is therefore keyed by board
geometry, never held in a single slot, so no board's grid can answer for
another's.
"""

import logging
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from src.plugins.base import PluginBase, PluginResult
from src.board_chars import BoardChars

logger = logging.getLogger(__name__)

# Board dimensions assumed when no board context is bound (``self.board`` is
# None on legacy paths, unit tests, and the boardless GET /plugins/{id}/data
# read). The platform's own default board is a Flagship, so these are the
# only dimension literals in the file and they are not a layout decision.
DEFAULT_ROWS = 6
DEFAULT_COLS = 22

#: Colony key used when no board is bound. Deliberately *not* ``(6, 22)``: a
#: boardless read is sized like a Flagship but it is not one, and sharing a
#: real Flagship's key let every GET /plugins/{id}/data poll advance that
#: board's simulation behind its back -- the board then showed every other
#: generation.
UNBOUND_KEY = "_unbound"

CELL_COLORS = {
    "red": BoardChars.RED,
    "orange": BoardChars.ORANGE,
    "yellow": BoardChars.YELLOW,
    "green": BoardChars.GREEN,
    "blue": BoardChars.BLUE,
    "violet": BoardChars.VIOLET,
    "white": BoardChars.WHITE,
}

# Rainbow mode: a newborn cell is white, then it walks this palette as it ages
RAINBOW_PALETTE = [
    BoardChars.RED,
    BoardChars.ORANGE,
    BoardChars.YELLOW,
    BoardChars.GREEN,
    BoardChars.BLUE,
    BoardChars.VIOLET,
]

# Seed patterns as (row, col) offsets from the shape's own top-left corner.
#
# Nothing is excluded here for being "too big". Whether a pulsar (13x13) or a
# Gosper glider gun (9x36) fits is a property of the board being seeded, not
# of the pattern, and a 120x24 note array has room for both -- so the decision
# is made in _fit_pattern() with the board's dimensions in hand.
PATTERNS: Dict[str, List[Tuple[int, int]]] = {
    "glider": [(0, 1), (1, 2), (2, 0), (2, 1), (2, 2)],
    "r_pentomino": [(0, 1), (0, 2), (1, 0), (1, 1), (2, 1)],
    "lwss": [
        (0, 1), (0, 4),
        (1, 0),
        (2, 0), (2, 4),
        (3, 0), (3, 1), (3, 2), (3, 3),
    ],
    # Period-3 oscillator, 48 cells in a 13x13 box.
    "pulsar": [
        (0, 2), (0, 3), (0, 4), (0, 8), (0, 9), (0, 10),
        (2, 0), (2, 5), (2, 7), (2, 12),
        (3, 0), (3, 5), (3, 7), (3, 12),
        (4, 0), (4, 5), (4, 7), (4, 12),
        (5, 2), (5, 3), (5, 4), (5, 8), (5, 9), (5, 10),
        (7, 2), (7, 3), (7, 4), (7, 8), (7, 9), (7, 10),
        (8, 0), (8, 5), (8, 7), (8, 12),
        (9, 0), (9, 5), (9, 7), (9, 12),
        (10, 0), (10, 5), (10, 7), (10, 12),
        (12, 2), (12, 3), (12, 4), (12, 8), (12, 9), (12, 10),
    ],
    # Emits a glider every 30 generations. 36 cells in a 9x36 box.
    "gosper_glider_gun": [
        (0, 24),
        (1, 22), (1, 24),
        (2, 12), (2, 13), (2, 20), (2, 21), (2, 34), (2, 35),
        (3, 11), (3, 15), (3, 20), (3, 21), (3, 34), (3, 35),
        (4, 0), (4, 1), (4, 10), (4, 16), (4, 20), (4, 21),
        (5, 0), (5, 1), (5, 10), (5, 14), (5, 16), (5, 17), (5, 22), (5, 24),
        (6, 10), (6, 16), (6, 24),
        (7, 11), (7, 15),
        (8, 12), (8, 13),
    ],
}

#: Bounding box (height, width) of each pattern, derived rather than retyped.
PATTERN_SIZES: Dict[str, Tuple[int, int]] = {
    name: (max(r for r, _ in cells) + 1, max(c for _, c in cells) + 1)
    for name, cells in PATTERNS.items()
}

# When a pattern does not fit the board, seed the nearest smaller relative
# instead of clipping it. A clipped shape is not the shape: an LWSS with its
# bottom row cut off (which is what a 3-row Note did to it) is a 5-cell
# fragment that is not a spaceship and does not travel.
PATTERN_FALLBACKS: Dict[str, Tuple[str, ...]] = {
    "gosper_glider_gun": ("pulsar", "lwss", "glider"),
    "pulsar": ("lwss", "glider"),
    "lwss": ("glider",),
}

# ``gliders`` predates tiling, when single patterns were placed once and only
# this option repeated them. Every pattern tiles now, so it is kept as an
# alias so existing configurations keep working.
PATTERN_ALIASES = {"gliders": "glider"}

# Dead cells left between tiled copies. Two is the principled value, not a
# taste call: a Life cell sees one ring of neighbours, so a two-cell gap is the
# narrowest separation at which no copy can see another at generation zero.
# Being a constant, seed density is the same on a Note as on a 120-column
# array; the *number* of copies is what the board decides. (The old
# GLIDER_SPACING = 7 was a stride tuned to land exactly three gliders on 22
# columns, and tiled no rows at all.)
PATTERN_GAP = 2

SEED_PATTERNS = ["random"] + list(PATTERNS) + list(PATTERN_ALIASES)

DEFAULT_CELL_COLOR = "green"
DEFAULT_WRAP_EDGES = True
DEFAULT_INITIAL_DENSITY = 0.3
MIN_INITIAL_DENSITY = 0.1
MAX_INITIAL_DENSITY = 0.6
DEFAULT_SEED_PATTERN = "random"
DEFAULT_RESEED_AFTER_STABLE = 3
DEFAULT_MAX_GENERATIONS = 200
DEFAULT_REFRESH_SECONDS = 60
MIN_REFRESH_SECONDS = 10

HISTORY_SIZE = 4  # recent grid hashes kept for stability detection

Grid = List[List[int]]  # 0 = dead, n > 0 = alive for n generations
#: How a colony is addressed: a bound board's ``(rows, cols)``, or
#: :data:`UNBOUND_KEY` when there is no board.
ColonyKey = Union[Tuple[int, int], str]


@dataclass
class Colony:
    """One board's running simulation.

    Held in :attr:`GameOfLifePlugin._colonies` under a ``(rows, cols)`` key.
    Everything that advances with the simulation lives here so that adding a
    board cannot disturb another board's run.
    """

    grid: Grid
    generation: int = 0
    history: List[int] = field(default_factory=list)
    stable_count: int = 0


class GameOfLifePlugin(PluginBase):
    """Conway's Game of Life, one generation per refresh, per board."""

    def __init__(self, manifest: Dict[str, Any]):
        super().__init__(manifest)
        # One colony per board geometry, never one for the plugin.
        #
        # This is the whole bug that was here: the simulation used to live in a
        # single un-keyed slot, guarded by a size check that "handled" a
        # mismatch by reseeding. Because the registry renders one instance for
        # every board, and GET /plugins/{id}/data renders it with no board at
        # all, any board that is not 22x6 was reseeded on every other call and
        # pinned at generation 0 forever -- and two boards on one account
        # pinned each other the same way. Keying by geometry isolates them
        # instead. The key space is bounded (Flagship, Note, and the note-array
        # shapes up to 8x8), so this cannot grow without limit.
        self._colonies: Dict[ColonyKey, Colony] = {}

    @property
    def plugin_id(self) -> str:
        return "game_of_life"

    # ------------------------------------------------------------------ #
    # Configuration
    # ------------------------------------------------------------------ #

    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        errors = []

        cell_color = config.get("cell_color", DEFAULT_CELL_COLOR)
        if cell_color != "rainbow" and cell_color not in CELL_COLORS:
            errors.append(
                f"Invalid cell_color '{cell_color}'. "
                f"Must be one of: {', '.join(CELL_COLORS)}, rainbow"
            )

        seed_pattern = config.get("seed_pattern", DEFAULT_SEED_PATTERN)
        if seed_pattern not in SEED_PATTERNS:
            errors.append(
                f"Invalid seed_pattern '{seed_pattern}'. "
                f"Must be one of: {', '.join(SEED_PATTERNS)}"
            )

        density = config.get("initial_density", DEFAULT_INITIAL_DENSITY)
        if (
            isinstance(density, bool)
            or not isinstance(density, (int, float))
            or not MIN_INITIAL_DENSITY <= density <= MAX_INITIAL_DENSITY
        ):
            errors.append(
                f"initial_density must be a number between "
                f"{MIN_INITIAL_DENSITY} and {MAX_INITIAL_DENSITY}"
            )

        wrap_edges = config.get("wrap_edges", DEFAULT_WRAP_EDGES)
        if not isinstance(wrap_edges, bool):
            errors.append("wrap_edges must be true or false")

        reseed = config.get("reseed_after_stable", DEFAULT_RESEED_AFTER_STABLE)
        if isinstance(reseed, bool) or not isinstance(reseed, int) or reseed < 1:
            errors.append("reseed_after_stable must be a positive integer")

        max_gen = config.get("max_generations", DEFAULT_MAX_GENERATIONS)
        if isinstance(max_gen, bool) or not isinstance(max_gen, int) or max_gen < 0:
            errors.append("max_generations must be an integer >= 0 (0 = unlimited)")

        refresh = config.get("refresh_seconds", DEFAULT_REFRESH_SECONDS)
        if isinstance(refresh, bool) or not isinstance(refresh, int) or refresh < MIN_REFRESH_SECONDS:
            errors.append(f"refresh_seconds must be an integer >= {MIN_REFRESH_SECONDS}")

        return errors

    # ------------------------------------------------------------------ #
    # Data fetch (the main entry-point)
    # ------------------------------------------------------------------ #

    def fetch_data(self) -> PluginResult:
        """Advance this board's simulation one generation and render it."""
        try:
            rows, cols = self._dims()
            wrap = bool(self.config.get("wrap_edges", DEFAULT_WRAP_EDGES))
            seed_pattern = self.config.get("seed_pattern", DEFAULT_SEED_PATTERN)
            reseed_after = int(self.config.get("reseed_after_stable", DEFAULT_RESEED_AFTER_STABLE))
            max_generations = int(self.config.get("max_generations", DEFAULT_MAX_GENERATIONS))

            key = self._colony_key()
            colony = self._colonies.get(key)
            if colony is None:
                # First frame this board has ever been asked for.
                colony = self._start_colony(key, rows, cols, seed_pattern)
            else:
                colony.grid = self._step(colony.grid, wrap)
                colony.generation += 1
                self._record_history(colony)

                if (
                    self._population(colony.grid) == 0
                    or colony.stable_count >= reseed_after
                    or (max_generations > 0 and colony.generation >= max_generations)
                ):
                    colony = self._start_colony(key, rows, cols, seed_pattern)

            board = self._render(colony.grid)
            lines = self._board_to_lines(board)

            data = {
                "game_of_life": "\n".join(lines),
                "game_of_life_array": board,
                "generation": colony.generation,
                "population": self._population(colony.grid),
                "board_rows": rows,
                "board_cols": cols,
                "is_stable": colony.stable_count > 0,
                "seed_pattern": seed_pattern,
            }
            # The frame *is* the whole board, so publish it as formatted_lines
            # too: that is the live path src/displays/service.py renders from,
            # and it makes the board rendering readable through
            # GET /plugins/{id}/data instead of only through a template.
            return PluginResult(available=True, data=data, formatted_lines=lines)

        except Exception as e:
            logger.exception("Error generating Game of Life frame")
            return PluginResult(available=False, error=str(e))

    def get_formatted_display(self) -> Optional[List[str]]:
        """Default page: this board's current frame, one line per row.

        Reads the colony for the geometry being rendered, seeding it if this
        board shape has not been seen yet. It deliberately does not render
        whatever grid happens to be in hand: after a 120x24 array render, that
        would put 24 rows of 120 markers onto a Flagship.
        """
        rows, cols = self._dims()
        key = self._colony_key()
        colony = self._colonies.get(key)
        if colony is None:
            colony = self._start_colony(
                key, rows, cols, self.config.get("seed_pattern", DEFAULT_SEED_PATTERN)
            )
        return self._board_to_lines(self._render(colony.grid))

    def cleanup(self) -> None:
        """Drop every board's simulation when the plugin is disabled."""
        self._colonies.clear()

    # ------------------------------------------------------------------ #
    # Simulation
    # ------------------------------------------------------------------ #

    def _bound(self) -> Optional[Any]:
        """The bound board, or None when it carries no usable dimensions."""
        board = self.board
        if board is not None and getattr(board, "rows", None) and getattr(board, "cols", None):
            return board
        return None

    def _dims(self) -> Tuple[int, int]:
        """Rows and columns to render at. No board bound means assume a Flagship."""
        board = self._bound()
        if board is None:
            return DEFAULT_ROWS, DEFAULT_COLS
        return board.rows, board.cols

    def _colony_key(self) -> ColonyKey:
        """Which colony the board currently bound owns."""
        board = self._bound()
        if board is None:
            return UNBOUND_KEY
        return (board.rows, board.cols)

    def current_colony(self) -> Optional[Colony]:
        """The colony for the board currently bound, if it has been seeded."""
        return self._colonies.get(self._colony_key())

    def colony_for(self, rows: int, cols: int) -> Optional[Colony]:
        """This plugin's colony for a ``rows`` x ``cols`` board, if it has one."""
        return self._colonies.get((rows, cols))

    def _start_colony(self, key: ColonyKey, rows: int, cols: int, seed_pattern: str) -> Colony:
        """Seed a fresh colony sized ``rows`` x ``cols`` and store it under *key*."""
        colony = self._seed(rows, cols, seed_pattern)
        self._colonies[key] = colony
        return colony

    def _seed(self, rows: int, cols: int, seed_pattern: str) -> Colony:
        """Build a fresh colony sized to the board, counters at zero."""
        grid: Grid = [[0] * cols for _ in range(rows)]

        if seed_pattern == "random":
            density = float(self.config.get("initial_density", DEFAULT_INITIAL_DENSITY))
            for r in range(rows):
                for c in range(cols):
                    if random.random() < density:
                        grid[r][c] = 1
        else:
            for r, c in self._pattern_cells(seed_pattern, rows, cols):
                grid[r][c] = 1

        return Colony(grid=grid, generation=0, history=[self._grid_hash(grid)], stable_count=0)

    @classmethod
    def _fit_pattern(cls, seed_pattern: str, rows: int, cols: int) -> str:
        """The pattern actually seeded for *seed_pattern* on this board.

        Walks :data:`PATTERN_FALLBACKS` to the first relative whose bounding
        box fits, so a pattern is either placed whole or substituted -- never
        clipped. ``glider`` is 3x3 and fits the smallest board the platform
        can produce (a single Note, 3x15), so the walk always terminates.
        """
        name = PATTERN_ALIASES.get(seed_pattern, seed_pattern)
        for candidate in (name, *PATTERN_FALLBACKS.get(name, ())):
            height, width = PATTERN_SIZES[candidate]
            if height <= rows and width <= cols:
                return candidate
        return "glider"

    @staticmethod
    def _tile_positions(extent: int, size: int, gap: int) -> List[int]:
        """Start offsets for copies of a *size*-long shape along *extent*.

        The count comes from the board rather than a constant: as many copies
        as fit with *gap* dead cells between them, each centred in its own
        equal share of the extent. Density is therefore constant across board
        sizes, and no unused margin is left at the far edge the way a fixed
        stride leaves one.
        """
        count = max(1, extent // (size + gap))
        stride = extent // count
        offset = max(0, (stride - size) // 2)
        return [i * stride + offset for i in range(count)]

    @classmethod
    def _pattern_cells(cls, seed_pattern: str, rows: int, cols: int) -> List[Tuple[int, int]]:
        """Absolute live-cell positions for a named seed pattern.

        The pattern is tiled across **both** axes, so the live-cell count
        scales with ``rows * cols``. Placing a single fixed shape put 5 live
        cells in the 2,880 of a 120x24 array -- a board that reads as blank --
        and tiling only along columns left every copy in rows 0-2 however tall
        the board was.
        """
        name = cls._fit_pattern(seed_pattern, rows, cols)
        cells = PATTERNS[name]
        height, width = PATTERN_SIZES[name]

        out: List[Tuple[int, int]] = []
        for row0 in cls._tile_positions(rows, height, PATTERN_GAP):
            for col0 in cls._tile_positions(cols, width, PATTERN_GAP):
                out.extend((r + row0, c + col0) for r, c in cells)
        # _fit_pattern guarantees the shape fits every board the platform can
        # produce (the smallest is a single Note, 3x15), so this filter is a
        # guard against a degenerate board, not the clipping it replaced --
        # which is why the substitution happens up there and not here.
        return [(r, c) for r, c in out if 0 <= r < rows and 0 <= c < cols]

    @staticmethod
    def _count_neighbors(grid: Grid, r: int, c: int, wrap: bool) -> int:
        rows, cols = len(grid), len(grid[0])
        count = 0
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                nr, nc = r + dr, c + dc
                if wrap:
                    nr, nc = nr % rows, nc % cols
                elif not (0 <= nr < rows and 0 <= nc < cols):
                    continue
                if grid[nr][nc] > 0:
                    count += 1
        return count

    @classmethod
    def _step(cls, grid: Grid, wrap: bool) -> Grid:
        """One Game of Life generation. Survivors age by one; births start at 1."""
        rows, cols = len(grid), len(grid[0])
        new: Grid = [[0] * cols for _ in range(rows)]
        for r in range(rows):
            for c in range(cols):
                n = cls._count_neighbors(grid, r, c, wrap)
                if grid[r][c] > 0 and n in (2, 3):
                    new[r][c] = grid[r][c] + 1
                elif grid[r][c] == 0 and n == 3:
                    new[r][c] = 1
        return new

    @staticmethod
    def _population(grid: Grid) -> int:
        return sum(1 for row in grid for cell in row if cell > 0)

    @staticmethod
    def _grid_hash(grid: Grid) -> int:
        """Hash of which cells are alive (ages ignored)."""
        return hash(tuple(tuple(cell > 0 for cell in row) for row in grid))

    @classmethod
    def _record_history(cls, colony: Colony) -> None:
        """Track the new grid; bump the stable counter on a still life or period-2 loop."""
        h = cls._grid_hash(colony.grid)
        recent = colony.history[-2:]
        if h in recent:
            colony.stable_count += 1
        else:
            colony.stable_count = 0
        colony.history.append(h)
        colony.history[:] = colony.history[-HISTORY_SIZE:]

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #

    def _render(self, grid: Grid) -> List[List[int]]:
        """Map the age grid to board character codes."""
        cell_color = self.config.get("cell_color", DEFAULT_CELL_COLOR)
        fixed = CELL_COLORS.get(cell_color, BoardChars.GREEN)
        board = []
        for row in grid:
            line = []
            for age in row:
                if age == 0:
                    line.append(BoardChars.BLACK)
                elif cell_color == "rainbow":
                    line.append(self._rainbow_color(age))
                else:
                    line.append(fixed)
            board.append(line)
        return board

    @staticmethod
    def _rainbow_color(age: int) -> int:
        if age <= 1:
            return BoardChars.WHITE
        return RAINBOW_PALETTE[(age - 2) % len(RAINBOW_PALETTE)]

    def _board_to_lines(self, board: List[List[int]]) -> List[str]:
        """One color-marker string per board row. Each marker is one tile."""
        color_map = {
            BoardChars.RED: "{red}",
            BoardChars.ORANGE: "{orange}",
            BoardChars.YELLOW: "{yellow}",
            BoardChars.GREEN: "{green}",
            BoardChars.BLUE: "{blue}",
            BoardChars.VIOLET: "{violet}",
            BoardChars.WHITE: "{white}",
            BoardChars.BLACK: "{black}",
        }
        return ["".join(color_map.get(code, " ") for code in row) for row in board]


# Export the plugin class
Plugin = GameOfLifePlugin

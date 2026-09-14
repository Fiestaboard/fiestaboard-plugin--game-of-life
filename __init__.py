"""Game of Life plugin for FiestaBoard.

Runs Conway's Game of Life on the board grid.  Every ``fetch_data`` call
advances the simulation by one generation and renders live cells as a
coloured tile and dead cells as black.  When the colony dies out, settles
into a still life / period-2 oscillator, or reaches ``max_generations``,
the board is reseeded so it never sits frozen.
"""

import logging
import random
from typing import Any, Dict, List, Optional, Tuple

from src.plugins.base import PluginBase, PluginResult
from src.board_chars import BoardChars

logger = logging.getLogger(__name__)

# Default board dimensions (Flagship) when no board context is bound
DEFAULT_ROWS = 6
DEFAULT_COLS = 22

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

# Seed patterns as (row, col) offsets. All fit inside a 6-row Flagship and a
# 3-row Note. Larger classics (pulsar 13x13, Gosper glider gun 36x9) do not
# fit on either board, so they are not offered.
PATTERNS: Dict[str, List[Tuple[int, int]]] = {
    "glider": [(0, 1), (1, 2), (2, 0), (2, 1), (2, 2)],
    "r_pentomino": [(0, 1), (0, 2), (1, 0), (1, 1), (2, 1)],
    "lwss": [
        (0, 1), (0, 4),
        (1, 0),
        (2, 0), (2, 4),
        (3, 0), (3, 1), (3, 2), (3, 3),
    ],
}
GLIDER_SPACING = 7  # columns between gliders in the "gliders" seed
SEED_PATTERNS = ["random"] + list(PATTERNS) + ["gliders"]

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


class GameOfLifePlugin(PluginBase):
    """Conway's Game of Life, one generation per refresh."""

    def __init__(self, manifest: Dict[str, Any]):
        super().__init__(manifest)
        self._grid: Optional[Grid] = None
        self._generation = 0
        self._history: List[int] = []
        self._stable_count = 0

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
        """Advance the simulation one generation and render it."""
        try:
            rows, cols = self._dims()
            wrap = bool(self.config.get("wrap_edges", DEFAULT_WRAP_EDGES))
            seed_pattern = self.config.get("seed_pattern", DEFAULT_SEED_PATTERN)
            reseed_after = int(self.config.get("reseed_after_stable", DEFAULT_RESEED_AFTER_STABLE))
            max_generations = int(self.config.get("max_generations", DEFAULT_MAX_GENERATIONS))

            grid_mismatch = (
                self._grid is None
                or len(self._grid) != rows
                or len(self._grid[0]) != cols
            )
            if grid_mismatch:
                self._seed(rows, cols, seed_pattern)
            else:
                self._grid = self._step(self._grid, wrap)
                self._generation += 1
                self._record_history()

                population = self._population(self._grid)
                if (
                    population == 0
                    or self._stable_count >= reseed_after
                    or (max_generations > 0 and self._generation >= max_generations)
                ):
                    self._seed(rows, cols, seed_pattern)

            board = self._render(self._grid)

            data = {
                "game_of_life": self._board_to_string(board),
                "game_of_life_array": board,
                "generation": self._generation,
                "population": self._population(self._grid),
                "board_rows": rows,
                "board_cols": cols,
                "is_stable": self._stable_count > 0,
                "seed_pattern": seed_pattern,
            }
            return PluginResult(available=True, data=data)

        except Exception as e:
            logger.exception("Error generating Game of Life frame")
            return PluginResult(available=False, error=str(e))

    def get_formatted_display(self) -> Optional[List[str]]:
        """Default page: the current board frame, one line per row."""
        if self._grid is None:
            rows, cols = self._dims()
            self._seed(rows, cols, self.config.get("seed_pattern", DEFAULT_SEED_PATTERN))
        return self._board_to_string(self._render(self._grid)).split("\n")

    def cleanup(self) -> None:
        """Reset simulation state when the plugin is disabled."""
        self._grid = None
        self._generation = 0
        self._history = []
        self._stable_count = 0

    # ------------------------------------------------------------------ #
    # Simulation
    # ------------------------------------------------------------------ #

    def _dims(self) -> Tuple[int, int]:
        board = self.board
        if board is not None and getattr(board, "rows", None) and getattr(board, "cols", None):
            return board.rows, board.cols
        return DEFAULT_ROWS, DEFAULT_COLS

    def _seed(self, rows: int, cols: int, seed_pattern: str) -> None:
        """Start a fresh colony and reset counters."""
        grid: Grid = [[0] * cols for _ in range(rows)]

        if seed_pattern == "random":
            density = float(self.config.get("initial_density", DEFAULT_INITIAL_DENSITY))
            for r in range(rows):
                for c in range(cols):
                    if random.random() < density:
                        grid[r][c] = 1
        else:
            for r, c in self._pattern_cells(seed_pattern, rows, cols):
                if 0 <= r < rows and 0 <= c < cols:
                    grid[r][c] = 1

        self._grid = grid
        self._generation = 0
        self._history = [self._grid_hash(grid)]
        self._stable_count = 0

    @staticmethod
    def _pattern_cells(seed_pattern: str, rows: int, cols: int) -> List[Tuple[int, int]]:
        """Absolute cell positions for a named seed pattern.

        Single patterns are centred on the board. ``gliders`` lines up as
        many gliders as fit, one every ``GLIDER_SPACING`` columns, near the
        top-left so they have room to travel.
        """
        if seed_pattern == "gliders":
            glider = PATTERNS["glider"]
            cells = []
            col = 1
            while col + 3 <= cols:
                cells.extend((r, c + col) for r, c in glider)
                col += GLIDER_SPACING
            return cells

        cells = PATTERNS[seed_pattern]
        height = max(r for r, _ in cells) + 1
        width = max(c for _, c in cells) + 1
        row0 = max(0, (rows - height) // 2)
        col0 = max(0, (cols - width) // 2)
        return [(r + row0, c + col0) for r, c in cells]

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

    def _record_history(self) -> None:
        """Track the new grid; bump the stable counter on a still life or period-2 loop."""
        h = self._grid_hash(self._grid)
        recent = self._history[-2:]
        if h in recent:
            self._stable_count += 1
        else:
            self._stable_count = 0
        self._history.append(h)
        self._history = self._history[-HISTORY_SIZE:]

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

    def _board_to_string(self, board: List[List[int]]) -> str:
        """Convert board array to the color-marker string format."""
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

        lines = []
        for row in board:
            lines.append("".join(color_map.get(code, " ") for code in row))
        return "\n".join(lines)


# Export the plugin class
Plugin = GameOfLifePlugin

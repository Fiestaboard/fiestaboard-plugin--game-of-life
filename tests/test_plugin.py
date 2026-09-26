"""Tests for the game_of_life plugin."""

import json
import random
from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest

from src.board_chars import BoardChars
from src.devices import BoardContext
from src.plugins.geometry_conformance import assert_board_conformance
from src.plugins.manifest import PluginManifest
from plugins.game_of_life import (
    DEFAULT_COLS,
    DEFAULT_ROWS,
    PATTERN_SIZES,
    GameOfLifePlugin,
    PATTERNS,
    RAINBOW_PALETTE,
    SEED_PATTERNS,
)

MANIFEST = json.loads((Path(__file__).parent.parent / "manifest.json").read_text())

# The conformance suite reads top-level ``max_lengths``, which core builds by
# merging each variable's own ``max_length`` in. Going through core's loader
# rather than restating the numbers keeps one source of truth in the manifest.
CORE_MANIFEST = PluginManifest.from_dict(MANIFEST).to_dict()

BLINKER = [(2, 10), (2, 11), (2, 12)]
BLOCK = [(2, 10), (2, 11), (3, 10), (3, 11)]


def make_plugin(**config) -> GameOfLifePlugin:
    plugin = GameOfLifePlugin(MANIFEST)
    plugin.config = config
    return plugin


def colony(plugin, rows=None, cols=None):
    """The plugin's colony for one board.

    Simulation state is keyed by geometry, so a test that drives the
    simulation has to say which board it is driving. With no rows/cols this
    is the colony for whatever board is bound right now -- which, in an
    unbound test, is the boardless colony, not a Flagship's.
    """
    if rows is None:
        return plugin.current_colony()
    return plugin.colony_for(rows, cols)


def set_grid(plugin, cells, rows=DEFAULT_ROWS, cols=DEFAULT_COLS):
    """Replace the bound board's grid with *cells* and reset its history."""
    c = plugin.current_colony()
    c.grid = grid_from_cells(cells, rows, cols)
    c.history = [plugin._grid_hash(c.grid)]
    c.stable_count = 0
    return c


def grid_from_cells(cells, rows=DEFAULT_ROWS, cols=DEFAULT_COLS):
    grid = [[0] * cols for _ in range(rows)]
    for r, c in cells:
        grid[r][c] = 1
    return grid


def live_cells(grid):
    return {(r, c) for r, row in enumerate(grid) for c, v in enumerate(row) if v > 0}


def count_tiles(line: str) -> int:
    """Each {marker} is one tile; anything else is one tile per character."""
    tiles = 0
    i = 0
    while i < len(line):
        if line[i] == "{":
            i = line.index("}", i) + 1
        else:
            i += 1
        tiles += 1
    return tiles


class TestBasics:
    def test_plugin_id(self):
        assert make_plugin().plugin_id == "game_of_life"

    def test_fetch_data_returns_every_declared_variable(self):
        result = make_plugin().fetch_data()
        assert result.available is True
        assert result.error is None
        for var in MANIFEST["variables"]["simple"]:
            assert var in result.data, f"Missing variable: {var}"

    def test_first_fetch_is_generation_zero(self):
        plugin = make_plugin()
        assert plugin.fetch_data().data["generation"] == 0
        assert plugin.fetch_data().data["generation"] == 1

    def test_board_shape_defaults_to_flagship(self):
        result = make_plugin().fetch_data()
        board = result.data["game_of_life_array"]
        assert len(board) == DEFAULT_ROWS
        assert all(len(row) == DEFAULT_COLS for row in board)
        assert result.data["board_rows"] == DEFAULT_ROWS
        assert result.data["board_cols"] == DEFAULT_COLS

    def test_board_string_has_six_lines_of_22_tiles(self):
        lines = make_plugin().fetch_data().data["game_of_life"].split("\n")
        assert len(lines) == DEFAULT_ROWS
        assert all(count_tiles(line) == DEFAULT_COLS for line in lines)

    def test_board_codes_are_black_or_cell_color(self):
        random.seed(1)
        board = make_plugin(cell_color="blue").fetch_data().data["game_of_life_array"]
        codes = {code for row in board for code in row}
        assert codes <= {BoardChars.BLACK, BoardChars.BLUE}
        assert BoardChars.BLUE in codes

    def test_population_matches_live_cells(self):
        plugin = make_plugin()
        result = plugin.fetch_data()
        assert result.data["population"] == len(live_cells(colony(plugin).grid))

    def test_seed_pattern_is_reported(self):
        assert make_plugin(seed_pattern="glider").fetch_data().data["seed_pattern"] == "glider"

    def test_fetch_data_never_raises(self):
        plugin = make_plugin()
        with patch.object(plugin, "_step", side_effect=RuntimeError("boom")):
            plugin.fetch_data()  # seeds
            result = plugin.fetch_data()  # steps -> raises
        assert result.available is False
        assert "boom" in result.error

    def test_get_formatted_display_shape(self):
        lines = make_plugin().get_formatted_display()
        assert len(lines) <= 6
        assert all(count_tiles(line) <= 22 for line in lines)

    def test_cleanup_drops_every_board_colony(self):
        plugin = make_plugin()
        for _ in range(3):
            plugin.fetch_data()
        note = BoardContext(device_type="note", rows=3, cols=15)
        with plugin._bound_board(note):
            plugin.fetch_data()
        assert colony(plugin) is not None            # the boardless colony
        assert colony(plugin, 3, 15) is not None     # the Note's
        plugin.cleanup()
        assert plugin._colonies == {}
        assert colony(plugin) is None
        assert colony(plugin, 3, 15) is None


class TestValidateConfig:
    def test_defaults_are_valid(self):
        assert make_plugin().validate_config({}) == []

    def test_full_valid_config(self):
        errors = make_plugin().validate_config({
            "enabled": True,
            "cell_color": "rainbow",
            "wrap_edges": False,
            "initial_density": 0.5,
            "seed_pattern": "lwss",
            "reseed_after_stable": 2,
            "max_generations": 0,
            "refresh_seconds": 30,
        })
        assert errors == []

    @pytest.mark.parametrize("config, field", [
        ({"cell_color": "pink"}, "cell_color"),
        ({"seed_pattern": "diehard"}, "seed_pattern"),
        ({"initial_density": 0.9}, "initial_density"),
        ({"initial_density": "lots"}, "initial_density"),
        ({"wrap_edges": "yes"}, "wrap_edges"),
        ({"reseed_after_stable": 0}, "reseed_after_stable"),
        ({"max_generations": -1}, "max_generations"),
        ({"refresh_seconds": 5}, "refresh_seconds"),
    ])
    def test_invalid_values(self, config, field):
        errors = make_plugin().validate_config(config)
        assert len(errors) == 1
        assert field in errors[0]

    def test_every_seed_pattern_runs(self):
        for pattern in SEED_PATTERNS:
            plugin = make_plugin(seed_pattern=pattern)
            result = plugin.fetch_data()
            assert result.available, pattern
            assert result.data["population"] > 0, pattern


class TestRules:
    def test_neighbor_count_with_wrap(self):
        grid = grid_from_cells([(0, 0), (5, 21), (0, 21), (5, 0)])
        # Corner (0,0): the other three corners are its diagonal neighbours on a torus
        assert GameOfLifePlugin._count_neighbors(grid, 0, 0, wrap=True) == 3

    def test_neighbor_count_without_wrap(self):
        grid = grid_from_cells([(0, 0), (5, 21), (0, 21), (5, 0), (0, 1)])
        assert GameOfLifePlugin._count_neighbors(grid, 0, 0, wrap=False) == 1

    def test_blinker_oscillates_with_period_2(self):
        start = grid_from_cells(BLINKER)
        gen1 = GameOfLifePlugin._step(start, wrap=True)
        gen2 = GameOfLifePlugin._step(gen1, wrap=True)
        assert live_cells(gen1) == {(1, 11), (2, 11), (3, 11)}
        assert live_cells(gen2) == set(BLINKER)

    def test_block_is_stable(self):
        start = grid_from_cells(BLOCK)
        assert live_cells(GameOfLifePlugin._step(start, wrap=True)) == set(BLOCK)

    def test_glider_moves_one_diagonal_after_4_generations(self):
        glider = {(r + 1, c + 18) for r, c in PATTERNS["glider"]}  # near the right edge
        grid = grid_from_cells(glider)
        for _ in range(4):
            grid = GameOfLifePlugin._step(grid, wrap=True)
        expected = {((r + 1) % DEFAULT_ROWS, (c + 1) % DEFAULT_COLS) for r, c in glider}
        assert live_cells(grid) == expected

    def test_survivors_age_and_births_start_at_one(self):
        grid = grid_from_cells(BLOCK)
        aged = GameOfLifePlugin._step(GameOfLifePlugin._step(grid, True), True)
        assert all(aged[r][c] == 3 for r, c in BLOCK)
        born = GameOfLifePlugin._step(grid_from_cells(BLINKER), True)
        assert born[1][11] == 1 and born[3][11] == 1
        assert born[2][11] == 2


class TestReseeding:
    def test_reseed_on_extinction(self):
        plugin = make_plugin(seed_pattern="random", initial_density=0.3)
        plugin.fetch_data()
        c = set_grid(plugin, [(2, 10)])  # a lone cell dies next step
        c.generation = 40
        random.seed(3)
        result = plugin.fetch_data()
        assert result.data["population"] > 0
        assert result.data["generation"] == 0

    def test_reseed_after_stable_generations(self):
        plugin = make_plugin(reseed_after_stable=3)
        plugin.fetch_data()
        set_grid(plugin, BLOCK).generation = 10

        gens = [plugin.fetch_data().data for _ in range(3)]
        assert [g["is_stable"] for g in gens[:2]] == [True, True]
        assert [g["generation"] for g in gens[:2]] == [11, 12]
        # Third stable generation hits the threshold and reseeds
        assert gens[2]["generation"] == 0
        assert gens[2]["is_stable"] is False

    def test_period_2_oscillator_counts_as_stable(self):
        plugin = make_plugin(reseed_after_stable=2)
        plugin.fetch_data()
        set_grid(plugin, BLINKER)

        first = plugin.fetch_data().data
        assert first["is_stable"] is False  # gen 1 differs from the seed
        second = plugin.fetch_data().data
        assert second["is_stable"] is True  # gen 2 == gen 0
        third = plugin.fetch_data().data
        assert third["generation"] == 0  # reseeded

    def test_reseed_after_max_generations(self):
        random.seed(7)
        plugin = make_plugin(max_generations=5, reseed_after_stable=50, initial_density=0.4)
        generations = [plugin.fetch_data().data["generation"] for _ in range(7)]
        assert generations[:5] == [0, 1, 2, 3, 4]
        assert 0 in generations[5:]

    def test_max_generations_zero_is_unlimited(self):
        plugin = make_plugin(max_generations=0, reseed_after_stable=100)
        plugin.fetch_data()
        set_grid(plugin, BLINKER)
        for _ in range(30):
            result = plugin.fetch_data()
        assert result.data["generation"] == 30

    def test_history_is_bounded(self):
        plugin = make_plugin(reseed_after_stable=100)
        for _ in range(20):
            plugin.fetch_data()
        assert len(colony(plugin).history) <= 4


class TestColors:
    def test_rainbow_ages_walk_the_palette(self):
        plugin = make_plugin(cell_color="rainbow")
        grid = grid_from_cells(BLOCK)
        # Ages 1..8 across the block's four cells plus a few extra cells
        grid[2][10] = 1
        grid[2][11] = 2
        grid[3][10] = 3
        grid[3][11] = 8
        board = plugin._render(grid)
        assert board[2][10] == BoardChars.WHITE
        assert board[2][11] == RAINBOW_PALETTE[0]  # red
        assert board[3][10] == RAINBOW_PALETTE[1]  # orange
        assert board[3][11] == RAINBOW_PALETTE[0]  # wraps around after violet
        assert board[0][0] == BoardChars.BLACK

    def test_rainbow_newborns_are_white_after_step(self):
        plugin = make_plugin(cell_color="rainbow")
        plugin.fetch_data()
        set_grid(plugin, BLINKER)
        board = plugin.fetch_data().data["game_of_life_array"]
        assert board[1][11] == BoardChars.WHITE
        assert board[2][11] == RAINBOW_PALETTE[0]

    def test_fixed_color_marker_in_string(self):
        plugin = make_plugin(cell_color="violet")
        plugin.fetch_data()
        set_grid(plugin, BLOCK)
        text = plugin.fetch_data().data["game_of_life"]
        assert "{violet}" in text
        assert "{black}" in text
        assert "{green}" not in text


class TestEveryBoardGeometry:
    """The plugin is rendered on every shape the platform supports.

    A note array is not simply "a bigger Flagship": 1-wide x 4-tall is 15x12,
    narrower than a Flagship and twice as tall, and 8-wide x 1-tall is 120x3,
    wider but shorter. Both are in this matrix on purpose.
    """

    SHAPES = [
        ("flagship", 6, 22),
        ("note", 3, 15),
        ("note_array", 12, 30),   # 2 wide x 4 tall -- a 65" FiestaPanel
        ("note_array", 12, 15),   # 1 wide x 4 tall -- narrower than a Flagship
        ("note_array", 3, 120),   # 8 wide x 1 tall -- wider but shorter
        ("note_array", 24, 120),  # 8 wide x 8 tall -- the largest array
    ]

    @pytest.mark.parametrize("device_type,rows,cols", SHAPES)
    def test_frame_is_exactly_the_board(self, device_type, rows, cols):
        board = BoardContext(device_type=device_type, rows=rows, cols=cols)
        plugin = make_plugin()
        with plugin._bound_board(board):
            result = plugin.fetch_data()
        grid = result.data["game_of_life_array"]
        assert len(grid) == rows
        assert all(len(row) == cols for row in grid)
        assert result.data["board_rows"] == rows
        assert result.data["board_cols"] == cols

        lines = result.data["game_of_life"].split("\n")
        assert len(lines) == rows
        assert all(count_tiles(line) == cols for line in lines)
        # formatted_lines is the live whole-board path (displays/service.py).
        assert result.formatted_lines == lines

    @pytest.mark.parametrize("device_type,rows,cols", SHAPES)
    def test_get_formatted_display_uses_this_board_not_the_last_one(self, device_type, rows, cols):
        """The dead hook must size from the bound board, not from whatever
        grid is already in hand: after a 120x24 render it used to return 24
        rows of 120 markers onto a Flagship."""
        plugin = make_plugin()
        big = BoardContext(device_type="note_array", rows=24, cols=120)
        plugin.get_data(big)

        board = BoardContext(device_type=device_type, rows=rows, cols=cols)
        with plugin._bound_board(board):
            lines = plugin.get_formatted_display()
        assert len(lines) == rows
        assert all(count_tiles(line) == cols for line in lines)

    @pytest.mark.parametrize("device_type,rows,cols", SHAPES)
    def test_every_seed_pattern_fills_every_board(self, device_type, rows, cols):
        board = BoardContext(device_type=device_type, rows=rows, cols=cols)
        for pattern in SEED_PATTERNS:
            plugin = make_plugin(seed_pattern=pattern)
            with plugin._bound_board(board):
                result = plugin.fetch_data()
            assert result.available, (pattern, device_type)
            # Live cells scale with the board: a single fixed shape put 5
            # cells in the 2,880 of a max array, which reads as blank.
            assert result.data["population"] >= rows * cols * 0.03, (
                f"{pattern} on {rows}x{cols} seeded only "
                f"{result.data['population']} of {rows * cols} cells"
            )

    def test_unbound_board_defaults_to_flagship(self):
        result = make_plugin().get_data(None)
        assert result.available
        assert (result.data["board_rows"], result.data["board_cols"]) == (DEFAULT_ROWS, DEFAULT_COLS)


class TestPerBoardState:
    """One plugin instance, many boards. State must be keyed by geometry."""

    FLAGSHIP = BoardContext(device_type="flagship", rows=6, cols=22)
    NOTE = BoardContext(device_type="note", rows=3, cols=15)
    PANEL = BoardContext(device_type="note_array", rows=12, cols=30)

    def _advance(self, plugin, board, times):
        seen = []
        for _ in range(times):
            with plugin._bound_board(board):
                seen.append(plugin.fetch_data().data["generation"])
        return seen

    def test_each_board_advances_its_own_generations(self):
        plugin = make_plugin(max_generations=0, reseed_after_stable=999)
        assert self._advance(plugin, self.FLAGSHIP, 4) == [0, 1, 2, 3]
        # A second board starts its own colony rather than resetting the first.
        assert self._advance(plugin, self.NOTE, 3) == [0, 1, 2]
        assert self._advance(plugin, self.FLAGSHIP, 2) == [4, 5]
        assert self._advance(plugin, self.PANEL, 2) == [0, 1]
        assert self._advance(plugin, self.NOTE, 1) == [3]

    def test_interleaved_boards_still_advance(self):
        """Two boards on one account used to pin each other at generation 0:
        the single un-keyed state slot mismatched on size every call and the
        mismatch guard reseeded instead of isolating."""
        plugin = make_plugin(max_generations=0, reseed_after_stable=999)
        flagship, note = [], []
        for _ in range(5):
            flagship.extend(self._advance(plugin, self.FLAGSHIP, 1))
            note.extend(self._advance(plugin, self.NOTE, 1))
        assert flagship == [0, 1, 2, 3, 4]
        assert note == [0, 1, 2, 3, 4]

    def test_boardless_read_does_not_disturb_a_board(self):
        """GET /plugins/{id}/data fetches with board=None. That read must not
        steal or reset a real board's generations."""
        plugin = make_plugin(max_generations=0, reseed_after_stable=999)
        for board in (self.FLAGSHIP, self.NOTE, self.PANEL):
            plugin.cleanup()
            seen = []
            for _ in range(5):
                seen.extend(self._advance(plugin, board, 1))
                plugin.fetch_data()  # unbound: the boardless read
            assert seen == [0, 1, 2, 3, 4], f"{board.rows}x{board.cols} saw {seen}"

    def test_boardless_colony_is_not_the_flagship_colony(self):
        """A boardless read renders at Flagship size but is its own board:
        sharing the key made every poll advance the real Flagship."""
        plugin = make_plugin(max_generations=0, reseed_after_stable=999)
        plugin.get_data(None)
        plugin.get_data(self.FLAGSHIP)
        with plugin._bound_board(None):
            unbound = plugin.current_colony()
        assert unbound is not None
        assert plugin.colony_for(6, 22) is not None
        assert unbound is not plugin.colony_for(6, 22)

    def test_colonies_are_keyed_by_geometry_not_device_type(self):
        """Two note arrays of different sizes are different boards."""
        plugin = make_plugin()
        wide = BoardContext(device_type="note_array", rows=3, cols=120)
        tall = BoardContext(device_type="note_array", rows=24, cols=15)
        plugin.get_data(wide)
        plugin.get_data(tall)
        assert plugin.colony_for(3, 120) is not None
        assert plugin.colony_for(24, 15) is not None
        assert plugin.colony_for(3, 120) is not plugin.colony_for(24, 15)
        assert len(plugin.colony_for(3, 120).grid) == 3
        assert len(plugin.colony_for(24, 15).grid) == 24


class TestSeedPlacement:
    def test_tile_positions_scale_with_the_board_and_stay_in_bounds(self):
        for extent in range(3, 121):
            for size in (3, 4, 5, 9, 13):
                if size > extent:
                    continue
                starts = GameOfLifePlugin._tile_positions(extent, size, 2)
                assert starts == sorted(starts)
                assert starts[0] >= 0
                assert starts[-1] + size <= extent, (extent, size, starts)

    def test_more_columns_means_more_copies(self):
        counts = [len(GameOfLifePlugin._tile_positions(cols, 3, 2)) for cols in (15, 22, 30, 60, 120)]
        assert counts == sorted(counts)
        assert counts[-1] > counts[0]

    def test_patterns_tile_on_the_row_axis_too(self):
        """Tiling only along columns left every copy in rows 0-2 however tall
        the board was: nine of twelve rows on a 30x12 panel seeded empty."""
        cells = GameOfLifePlugin._pattern_cells("glider", rows=12, cols=30)
        assert len({r for r, _ in cells}) > 3
        assert max(r for r, _ in cells) > 3

    @pytest.mark.parametrize("pattern", [p for p in SEED_PATTERNS if p != "random"])
    @pytest.mark.parametrize("rows,cols", [(6, 22), (3, 15), (12, 30), (12, 15), (3, 120), (24, 120)])
    def test_pattern_cells_are_always_in_bounds(self, pattern, rows, cols):
        cells = GameOfLifePlugin._pattern_cells(pattern, rows, cols)
        assert cells
        assert all(0 <= r < rows and 0 <= c < cols for r, c in cells)

    @pytest.mark.parametrize("pattern", [p for p in SEED_PATTERNS if p != "random"])
    @pytest.mark.parametrize("rows,cols", [(6, 22), (3, 15), (12, 30), (12, 15), (3, 120), (24, 120)])
    def test_substituted_pattern_is_always_placed_whole(self, pattern, rows, cols):
        """A clipped shape is not the shape. An LWSS is 4 rows tall, so on a
        3-row Note its whole bottom row used to be dropped, leaving a 5-cell
        fragment that is not a spaceship. Substitute, never clip."""
        fitted = GameOfLifePlugin._fit_pattern(pattern, rows, cols)
        height, width = PATTERN_SIZES[fitted]
        assert height <= rows and width <= cols
        cells = GameOfLifePlugin._pattern_cells(pattern, rows, cols)
        # Every copy carries the full cell count of the fitted pattern.
        assert len(cells) % len(PATTERNS[fitted]) == 0
        assert len(cells) >= len(PATTERNS[fitted])

    def test_lwss_is_substituted_on_a_three_row_note_not_clipped(self):
        assert GameOfLifePlugin._fit_pattern("lwss", rows=3, cols=15) == "glider"
        cells = GameOfLifePlugin._pattern_cells("lwss", rows=3, cols=15)
        assert len(cells) % len(PATTERNS["glider"]) == 0

    def test_large_patterns_are_gated_on_the_board_not_excluded(self):
        """A 120x24 array has room for a pulsar and a Gosper gun; a Flagship
        does not. The decision belongs at seed time, with the board in hand."""
        assert GameOfLifePlugin._fit_pattern("pulsar", 24, 120) == "pulsar"
        assert GameOfLifePlugin._fit_pattern("gosper_glider_gun", 24, 120) == "gosper_glider_gun"
        assert GameOfLifePlugin._fit_pattern("pulsar", 6, 22) == "lwss"
        assert GameOfLifePlugin._fit_pattern("gosper_glider_gun", 3, 15) == "glider"

    def test_pulsar_is_a_period_three_oscillator(self):
        rows = cols = 21
        grid = grid_from_cells([(r + 4, c + 4) for r, c in PATTERNS["pulsar"]], rows, cols)
        assert len(PATTERNS["pulsar"]) == 48
        start = live_cells(grid)
        g = grid
        for _ in range(3):
            g = GameOfLifePlugin._step(g, wrap=False)
        assert live_cells(g) == start

    def test_gosper_gun_emits_gliders(self):
        grid = grid_from_cells([(r + 1, c + 1) for r, c in PATTERNS["gosper_glider_gun"]], 30, 60)
        assert len(PATTERNS["gosper_glider_gun"]) == 36
        g = grid
        for _ in range(40):
            g = GameOfLifePlugin._step(g, wrap=False)
        # The gun is period-30 and keeps its own 36 cells; anything above that
        # is emitted gliders travelling away.
        assert len(live_cells(g)) > 36

    def test_degenerate_board_smaller_than_any_pattern_does_not_crash(self):
        """No real board is this small (the smallest is a Note, 3x15), but the
        seeder must not raise if one ever is."""
        assert GameOfLifePlugin._fit_pattern("lwss", rows=1, cols=1) == "glider"
        cells = GameOfLifePlugin._pattern_cells("lwss", rows=1, cols=1)
        assert all(0 <= r < 1 and 0 <= c < 1 for r, c in cells)

    def test_gliders_is_an_alias_for_glider(self):
        assert (GameOfLifePlugin._pattern_cells("gliders", 6, 22)
                == GameOfLifePlugin._pattern_cells("glider", 6, 22))


class TestBoardConformance:
    def test_renders_on_every_board_shape(self):
        assert_board_conformance(
            make_plugin,
            manifest=CORE_MANIFEST,
            strict_growth=True,
            require_note_array_preview=True,
        )

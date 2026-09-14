"""Tests for the game_of_life plugin."""

import json
import random
from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest

from src.board_chars import BoardChars
from src.devices import BoardContext
from plugins.game_of_life import (
    DEFAULT_COLS,
    DEFAULT_ROWS,
    GameOfLifePlugin,
    PATTERNS,
    RAINBOW_PALETTE,
    SEED_PATTERNS,
)

MANIFEST = json.loads((Path(__file__).parent.parent / "manifest.json").read_text())

BLINKER = [(2, 10), (2, 11), (2, 12)]
BLOCK = [(2, 10), (2, 11), (3, 10), (3, 11)]


def make_plugin(**config) -> GameOfLifePlugin:
    plugin = GameOfLifePlugin(MANIFEST)
    plugin.config = config
    return plugin


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
        assert result.data["population"] == len(live_cells(plugin._grid))

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

    def test_cleanup_resets_state(self):
        plugin = make_plugin()
        for _ in range(3):
            plugin.fetch_data()
        assert plugin._grid is not None
        plugin.cleanup()
        assert plugin._grid is None
        assert plugin._generation == 0
        assert plugin._history == []
        assert plugin._stable_count == 0


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
        ({"seed_pattern": "pulsar"}, "seed_pattern"),
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
        plugin._grid = grid_from_cells([(2, 10)])  # a lone cell dies next step
        plugin._generation = 40
        random.seed(3)
        result = plugin.fetch_data()
        assert result.data["population"] > 0
        assert result.data["generation"] == 0

    def test_reseed_after_stable_generations(self):
        plugin = make_plugin(reseed_after_stable=3)
        plugin.fetch_data()
        plugin._grid = grid_from_cells(BLOCK)
        plugin._history = [plugin._grid_hash(plugin._grid)]
        plugin._stable_count = 0
        plugin._generation = 10

        gens = [plugin.fetch_data().data for _ in range(3)]
        assert [g["is_stable"] for g in gens[:2]] == [True, True]
        assert [g["generation"] for g in gens[:2]] == [11, 12]
        # Third stable generation hits the threshold and reseeds
        assert gens[2]["generation"] == 0
        assert gens[2]["is_stable"] is False

    def test_period_2_oscillator_counts_as_stable(self):
        plugin = make_plugin(reseed_after_stable=2)
        plugin.fetch_data()
        plugin._grid = grid_from_cells(BLINKER)
        plugin._history = [plugin._grid_hash(plugin._grid)]
        plugin._stable_count = 0

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
        plugin._grid = grid_from_cells(BLINKER)
        plugin._history = [plugin._grid_hash(plugin._grid)]
        for _ in range(30):
            result = plugin.fetch_data()
        assert result.data["generation"] == 30

    def test_history_is_bounded(self):
        plugin = make_plugin(reseed_after_stable=100)
        for _ in range(20):
            plugin.fetch_data()
        assert len(plugin._history) <= 4


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
        plugin._grid = grid_from_cells(BLINKER)
        board = plugin.fetch_data().data["game_of_life_array"]
        assert board[1][11] == BoardChars.WHITE
        assert board[2][11] == RAINBOW_PALETTE[0]

    def test_fixed_color_marker_in_string(self):
        plugin = make_plugin(cell_color="violet")
        plugin.fetch_data()
        plugin._grid = grid_from_cells(BLOCK)
        text = plugin.fetch_data().data["game_of_life"]
        assert "{violet}" in text
        assert "{black}" in text
        assert "{green}" not in text


class TestNoteBoard:
    def test_note_board_dimensions_are_used(self):
        note = BoardContext(device_type="note", rows=3, cols=15)
        plugin = make_plugin(seed_pattern="gliders")
        with patch.object(GameOfLifePlugin, "board", new_callable=PropertyMock, return_value=note):
            result = plugin.fetch_data()
            board = result.data["game_of_life_array"]
            assert len(board) == 3
            assert all(len(row) == 15 for row in board)
            assert result.data["board_rows"] == 3
            assert result.data["board_cols"] == 15
            assert len(result.data["game_of_life"].split("\n")) == 3
            # Two gliders fit on a Note (three on a Flagship)
            assert result.data["population"] == 10
            assert plugin.fetch_data().available

    def test_board_change_reseeds(self):
        plugin = make_plugin()
        plugin.fetch_data()
        plugin.fetch_data()
        assert plugin._generation == 1
        note = BoardContext(device_type="note", rows=3, cols=15)
        with patch.object(GameOfLifePlugin, "board", new_callable=PropertyMock, return_value=note):
            result = plugin.fetch_data()
        assert result.data["generation"] == 0
        assert len(result.data["game_of_life_array"]) == 3

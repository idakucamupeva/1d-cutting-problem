"""Kerf accounting and cut geometry on a single board."""

from app.optimizer.types import NEW, BoardPlan

KERF = 4


def board(*pieces: int, length: int = 13_000) -> BoardPlan:
    return BoardPlan(source_kind=NEW, source_length_mm=length, pieces=list(pieces))


class TestFeasibility:
    def test_exact_fit_needs_only_n_minus_1_cuts(self):
        # 3 pieces + 2 kerfs exactly fill the board: no trailing cut needed.
        b = board(4_000, 4_000, 4_992, length=13_000)  # 12992 + 2*4 = 13000
        assert b.is_feasible(KERF)
        assert b.leftover_mm(KERF) == 0
        assert b.cuts_count(KERF) == 2

    def test_one_mm_over_is_infeasible(self):
        b = board(4_000, 4_000, 4_993, length=13_000)  # 12993 + 8 = 13001
        assert not b.is_feasible(KERF)

    def test_single_piece_equal_to_stock_needs_no_cut(self):
        b = board(13_000)
        assert b.is_feasible(KERF)
        assert b.leftover_mm(KERF) == 0
        assert b.cuts_count(KERF) == 0

    def test_kerf_is_counted_between_pieces(self):
        # 2 pieces of 6500 fit only in 13008, not 13000.
        assert not board(6_500, 6_500, length=13_000).is_feasible(KERF)
        assert board(6_500, 6_500, length=13_008).is_feasible(KERF)


class TestLeftover:
    def test_leftover_loses_trailing_kerf(self):
        # 4000 from 13000: one cut frees the piece, kerf comes off the rest.
        b = board(4_000)
        assert b.leftover_mm(KERF) == 13_000 - 4_000 - KERF
        assert b.cuts_count(KERF) == 1

    def test_sliver_smaller_than_kerf_becomes_dust(self):
        # rest after last piece is 2mm (< kerf): the trailing cut eats it.
        b = board(12_998)
        assert b.is_feasible(KERF)
        assert b.leftover_mm(KERF) == 0
        assert b.cuts_count(KERF) == 1

    def test_fits_accounts_for_added_kerf(self):
        b = board(4_000)
        assert b.fits(8_992, KERF)  # 4000+8992+4 = 12996 <= 13000
        assert b.fits(8_996, KERF)  # exactly 13000
        assert not b.fits(8_997, KERF)

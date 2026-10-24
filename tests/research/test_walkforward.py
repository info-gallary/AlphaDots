"""Walk-forward splits: no test day is ever visible to the training that precedes it."""

from __future__ import annotations

import numpy as np
import pytest

from alphadots_research.walkforward import choose, walk_forward


def test_every_fold_leaves_the_purge_and_embargo_gap_and_skips_the_holdout() -> None:
    plan = walk_forward(756, first_train=252, test_size=63, holdout=126, purge=1, embargo=5)
    assert plan.folds, "at least one fold"
    for fold in plan.folds:
        # Last training decision uses the return to close train.stop; test starts 6 days on.
        assert fold.test.start - fold.train.stop == 6
        assert fold.test.stop <= plan.holdout.start
    starts = [fold.test.start for fold in plan.folds]
    stops = [fold.test.stop for fold in plan.folds]
    assert starts[1:] == stops[:-1], "test blocks are contiguous, so the track has no gaps"
    assert plan.holdout.stop == 755, "the last close has no forward return to trade"
    assert plan.holdout.start - plan.holdout_train.stop == 6


def test_a_window_too_short_for_training_is_refused() -> None:
    with pytest.raises(ValueError, match="not enough days"):
        walk_forward(300, first_train=252, holdout=126)


def test_choose_picks_the_best_training_ic_and_breaks_ties_toward_the_smaller_value() -> None:
    ics = {
        0.5: np.array([0.01, 0.01, 0.50]),
        1.0: np.array([0.03, 0.03, -0.50]),
        2.0: np.array([0.03, 0.03, 0.00]),
    }
    assert choose(ics, range(0, 2)) == 1.0, "tied with 2.0 on the training days: smaller wins"
    assert choose(ics, range(0, 3)) == 0.5, "the third day only counts when it is training"

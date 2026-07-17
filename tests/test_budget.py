"""Tests for agent.budget: limits, spending and the is_last heuristic."""

from types import SimpleNamespace

from agent.budget import Budget


def receipt(input_tokens: int, output_tokens: int) -> SimpleNamespace:
    """A minimal LLM receipt, shaped as Budget.spend() reads it."""
    return SimpleNamespace(input_tokens=input_tokens,
                           output_tokens=output_tokens)


def test_fresh_budget_allows_and_is_not_last():
    """Nothing spent yet: room to run, no reason to wrap up."""
    budget = Budget(10, 6_000, 1_500, 120.0)
    assert budget.allows()
    assert not budget.is_last()


def test_is_last_by_token_growth():
    """The 1.5x growth margin: is_last fires when the next call,
    assumed bigger than the previous, would not fit."""
    budget = Budget(10, 6_000, 1_500, 120.0)
    budget.spend(receipt(600, 80))
    assert not budget.is_last()      # 5400 left, next ~900
    budget.spend(receipt(1_200, 80))
    assert not budget.is_last()      # 4200 left, next ~1800
    budget.spend(receipt(1_900, 80))
    assert budget.is_last()          # 2300 left, next ~2850


def test_is_last_by_iteration_count():
    """The iteration before the cap is announced as the last one."""
    budget = Budget(3, 10**6, 10**6, 60.0)
    budget.spend(receipt(1, 1))
    budget.spend(receipt(1, 1))
    assert budget.is_last()
    budget.spend(receipt(1, 1))
    assert not budget.allows()


def test_single_iteration_budget_is_last_immediately():
    """With max_iterations=1 the very first call is the last."""
    assert Budget(1, 10**6, 10**6, 60.0).is_last()


def test_output_tokens_block():
    """Blowing the output limit stops the run."""
    budget = Budget(10, 10**6, 100, 60.0)
    budget.spend(receipt(1, 200))
    assert not budget.allows()


def test_time_blocks():
    """A zero-second budget allows nothing."""
    assert not Budget(10, 10**6, 10**6, 0.0).allows()


def test_input_blocks_predictively():
    """The next call costs at least what the last one did: when even
    that minimum does not fit, the call is not made. The final totals
    must stay within the limits, so overshooting loses the task."""
    budget = Budget(10, 6_000, 10**6, 60.0)
    budget.spend(receipt(2_000, 1))
    assert budget.allows()       # 2000 spent + 2000 next = 4000, fits
    budget.spend(receipt(2_500, 1))
    assert not budget.allows()   # 4500 spent + 2500 next would be 7000


def test_input_predictive_edge_exactly_fits():
    """spent + last == max is still affordable (the limit is <=)."""
    budget = Budget(10, 1_000, 10**6, 60.0)
    budget.spend(receipt(500, 1))
    assert budget.allows()


def test_time_blocks_predictively():
    """An iteration as long as the last one must still fit the clock."""
    budget = Budget(10, 10**6, 10**6, 60.0)
    budget.last_iter_seconds = 120.0  # simulate one very slow lap
    assert not budget.allows()


def test_remaining_output_shrinks_as_spent():
    """remaining_output() is the server-side cap for the next call."""
    budget = Budget(10, 10**6, 1_500, 60.0)
    assert budget.remaining_output() == 1_500
    budget.spend(receipt(1, 400))
    assert budget.remaining_output() == 1_100

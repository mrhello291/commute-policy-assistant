import pytest

from app.llm.budget import BudgetExhausted, CallBudget, Deadline


def test_budget_allows_exactly_max_calls():
    budget = CallBudget(max_calls=2)
    budget.spend()
    budget.spend()
    assert budget.used == 2 and budget.remaining == 0
    with pytest.raises(BudgetExhausted):
        budget.spend()
    assert budget.used == 2


def test_budget_needs_at_least_one_call():
    with pytest.raises(ValueError):
        CallBudget(0)


def test_deadline_counts_down_with_the_clock():
    now = [100.0]
    deadline = Deadline(3.0, clock=lambda: now[0])
    assert deadline.remaining() == 3.0
    now[0] = 102.5
    assert deadline.remaining() == pytest.approx(0.5)
    now[0] = 104.0
    assert deadline.remaining() == 0.0 and deadline.expired()

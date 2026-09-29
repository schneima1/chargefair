"""Tests for the allocation methods (no Flask application needed)."""
from __future__ import annotations

import random
import time

import pytest

from chargefair.allocation import (
    METHODS,
    AllocationProblem,
    AllocationUser,
    SolverUnavailable,
    available_methods,
    get_method,
    guaranteed_then_extras,
    run,
)

SOLVER_METHODS = [key for key, spec in METHODS.items() if spec.uses_solver]
ALL_METHODS = list(METHODS)
# Methods that promise the guarantee (the deliberately naive baselines do not).
FAIR_METHODS = [key for key, spec in METHODS.items() if key not in ("fcfs", "lottery")]


def make_problem(users: int = 12, slots: int = 4, capacity: int = 2,
                 wishes: int = 3, desired: int = 1, guarantee: int = 1,
                 max_per_user: int = 2) -> AllocationProblem:
    rng = random.Random(7)
    grid = [(day, window) for day in range(5) for window in range(4)]
    slot_table = {key: capacity for key in rng.sample(grid, slots)}
    people = []
    for index in range(users):
        available = rng.sample(list(slot_table), min(wishes, len(slot_table)))
        people.append(
            AllocationUser(
                index=index,
                name=f"Person {index}",
                priorities=available,
                desired_count=desired,
                request_time=float(index),
            )
        )
    return AllocationProblem(
        slots=slot_table,
        users=people,
        max_slots_per_user=max_per_user,
        guaranteed_per_user=guarantee,
    )


# ---------------------------------------------------------------------------
# Capacity and limits
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method", ALL_METHODS)
def test_capacity_is_never_exceeded(method: str) -> None:
    problem = make_problem()
    assignment = run(method, problem, seed=42, time_limit=5, workers=2)
    counts: dict[tuple[int, int], int] = {}
    for user in problem.users:
        for key in assignment.slots_for(user.index):
            counts[key] = counts.get(key, 0) + 1
    for key, used in counts.items():
        assert used <= problem.slots[key], f"{method} overbooked slot {key}"


@pytest.mark.parametrize("method", ALL_METHODS)
def test_users_only_get_slots_they_asked_for(method: str) -> None:
    problem = make_problem()
    assignment = run(method, problem, seed=1, time_limit=5, workers=2)
    for user in problem.users:
        for key in assignment.slots_for(user.index):
            assert key in user.priorities


@pytest.mark.parametrize("method", ALL_METHODS)
def test_weekly_limit_is_respected(method: str) -> None:
    problem = make_problem(users=30, slots=6, capacity=2, wishes=4, desired=2)
    assignment = run(method, problem, seed=3, time_limit=8, workers=2)
    for user in problem.users:
        assert len(assignment.slots_for(user.index)) <= problem.max_slots_per_user


@pytest.mark.parametrize("method", SOLVER_METHODS)
def test_solver_methods_supply_at_least_as_many_users_as_fcfs(method: str) -> None:
    """The optimising methods must not be worse than the naive baseline."""
    problem = make_problem(users=24, slots=8, capacity=1, wishes=3)
    baseline = run("fcfs", problem, seed=5, time_limit=5, workers=2)
    result = run(method, problem, seed=5, time_limit=10, workers=4)
    supplied_baseline = sum(1 for u in problem.users if baseline.slots_for(u.index))
    supplied_result = sum(1 for u in problem.users if result.slots_for(u.index))
    assert supplied_result >= supplied_baseline


@pytest.mark.parametrize("method", SOLVER_METHODS)
def test_solver_methods_are_deterministic_for_a_fixed_seed(method: str) -> None:
    problem = make_problem()
    first = run(method, problem, seed=99, time_limit=15, workers=1)
    second = run(method, problem, seed=99, time_limit=15, workers=1)
    assert first.user_slots == second.user_slots


# ---------------------------------------------------------------------------
# The guarantee: one slot for everybody before anybody gets a second one
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method", FAIR_METHODS)
def test_guarantee_is_met_when_capacity_allows_it(method: str) -> None:
    """25 applicants, 26 free slots -> everybody gets at least one slot."""
    problem = make_problem(users=25, slots=13, capacity=2, wishes=4, desired=3,
                           guarantee=1, max_per_user=3)
    assert problem.total_capacity == 26
    assignment = run(method, problem, seed=17, time_limit=10, workers=2)
    for user in problem.users:
        assert assignment.count_for(user.index) >= 1, f"{method} left {user.name} out"


@pytest.mark.parametrize("method", FAIR_METHODS)
def test_nobody_gets_a_second_slot_while_somebody_is_left_out(method: str) -> None:
    """20 applicants compete for 10 slots: ten get one, nobody gets two."""
    problem = make_problem(users=20, slots=5, capacity=2, wishes=4, desired=3,
                           guarantee=1, max_per_user=3)
    assert problem.total_capacity == 10
    assignment = run(method, problem, seed=23, time_limit=10, workers=2)
    counts = [assignment.count_for(user.index) for user in problem.users]
    assert sum(counts) <= problem.total_capacity
    assert max(counts) == 1, f"{method} handed out an extra slot: {counts}"
    assert sum(1 for count in counts if count == 1) == problem.total_capacity


@pytest.mark.parametrize("method", FAIR_METHODS)
def test_extras_only_after_everybody_is_served(method: str) -> None:
    """40 applicants, 45 slots: all get one, only 5 second slots are handed out."""
    problem = make_problem(users=40, slots=9, capacity=5, wishes=5, desired=3,
                           guarantee=1, max_per_user=3)
    assert problem.total_capacity == 45
    assignment = run(method, problem, seed=29, time_limit=15, workers=2)
    counts = [assignment.count_for(user.index) for user in problem.users]
    assert min(counts) >= 1, "the guarantee must not be traded for extras"
    assert sum(counts) == problem.total_capacity
    assert sum(1 for count in counts if count >= 2) == 5
    assert max(counts) == 2, "a third slot would be unfair while others wait"


def test_second_slot_comes_before_a_third_one() -> None:
    """Round based extras: everybody gets a second slot before a third one."""
    keys = [(0, index) for index in range(8)]
    problem = AllocationProblem(
        slots={key: 1 for key in keys},
        users=[
            AllocationUser(index=i, name=f"P{i}", priorities=list(keys),
                           desired_count=3, request_time=float(i))
            for i in range(5)
        ],
        max_slots_per_user=3,
        guaranteed_per_user=1,
    )
    assignment = guaranteed_then_extras(problem)
    counts = sorted((assignment.count_for(user.index) for user in problem.users), reverse=True)
    # 8 slots for 5 people: first round everybody, second round three of them.
    assert counts == [2, 2, 2, 1, 1]


def test_overlapping_wishes_still_keep_the_guarantee() -> None:
    """A plain greedy pass fails here; the matching must not."""
    slot_a, slot_b = (0, 0), (0, 1)
    problem = AllocationProblem(
        slots={slot_a: 1, slot_b: 1},
        users=[
            # Top priority applicant and the only one who can use both slots.
            AllocationUser(1, "flexibel", [slot_a, slot_b], 1),
            # Can only ever use slot_a.
            AllocationUser(2, "festgelegt", [slot_a], 1),
        ],
        max_slots_per_user=1,
        guaranteed_per_user=1,
    )
    assignment = guaranteed_then_extras(problem)
    assert assignment.count_for(1) == 1
    assert assignment.count_for(2) == 1
    assert assignment.info["guarantee_fulfilled"] is True


def test_guarantee_helper_reports_demand_and_feasibility() -> None:
    problem = make_problem(users=10, slots=3, capacity=2, wishes=3, desired=2)
    assert problem.guaranteed_demand == 10
    assert problem.total_capacity == 6
    assert problem.guarantee_is_feasible is False


# ---------------------------------------------------------------------------
# Everything but the time of submission is ignored
# ---------------------------------------------------------------------------

def test_fcfs_prefers_early_requests() -> None:
    key_a, key_b = (0, 0), (0, 1)
    problem = AllocationProblem(
        slots={key_a: 1, key_b: 1},
        users=[
            AllocationUser(1, "spät", [key_a, key_b], 1, request_time=99.0),
            AllocationUser(2, "früh", [key_a], 1, request_time=1.0),
        ],
        max_slots_per_user=1,
    )
    assignment = run("fcfs", problem, seed=1)
    assert assignment.slots_for(2) == [key_a]


@pytest.mark.parametrize("method", FAIR_METHODS)
def test_equal_applicants_are_decided_by_draw_not_by_identity(method: str) -> None:
    """Nobody has a structural advantage: only the seed decides.

    Both applicants ask for the same single slot and are otherwise identical.
    Across several seeds each of them must win at least once, which rules out a
    hidden preference for a user id or any other personal attribute.
    """
    key = (0, 0)

    def winner_for(seed: int) -> int:
        problem = AllocationProblem(
            slots={key: 1},
            users=[
                AllocationUser(1, "A", [key], 1),
                AllocationUser(2, "B", [key], 1),
            ],
            max_slots_per_user=2,
            guaranteed_per_user=1,
        )
        assignment = run(method, problem, seed=seed, time_limit=5, workers=1)
        return 1 if assignment.slots_for(1) else 2

    winners = {winner_for(seed) for seed in range(20)}
    assert winners == {1, 2}, f"{method} always favours the same applicant"


@pytest.mark.parametrize("method", FAIR_METHODS)
def test_request_time_does_not_influence_the_outcome(method: str) -> None:
    """Filing a wish early must not be an advantage outside of FCFS."""
    key = (0, 0)

    def allocate(request_times: tuple[float, float]) -> dict:
        problem = AllocationProblem(
            slots={key: 1},
            users=[
                AllocationUser(1, "A", [key], 1, request_time=request_times[0]),
                AllocationUser(2, "B", [key], 1, request_time=request_times[1]),
            ],
            max_slots_per_user=2,
            guaranteed_per_user=1,
        )
        return run(method, problem, seed=5, time_limit=5, workers=1).user_slots

    early_first = allocate((1.0, 500.0))
    early_second = allocate((500.0, 1.0))
    assert early_first == early_second, f"{method} rewards early submission"


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------

def test_empty_problem_returns_empty_assignment() -> None:
    problem = AllocationProblem(slots={}, users=[], max_slots_per_user=1)
    for method in ALL_METHODS:
        assignment = run(method, problem, seed=1, time_limit=2, workers=1)
        assert assignment.user_slots == {}
        assert assignment.unassigned == []


def test_fallback_keeps_the_guarantee_when_the_solver_fails(monkeypatch) -> None:
    """A failing solver must degrade to the deterministic method, not crash."""
    import chargefair.allocation as allocation

    def boom(*_args, **_kwargs):
        raise SolverUnavailable("simulated solver failure")

    monkeypatch.setattr(allocation, "_lexicographic_solver", boom)
    monkeypatch.setattr(allocation, "_rank_based_solver", boom)

    problem = make_problem(users=20, slots=6, capacity=2, wishes=4, desired=2)
    for method in ("lexicographic", "rank_based"):
        assignment = run(method, problem, seed=4, time_limit=5, workers=1)
        assert assignment.info["fallback"] is True
        # 12 slots for 20 applicants: the slots must still be spread over 12
        # different people instead of giving some of them a second slot.
        assert assignment.info["guaranteed_ok"] == problem.total_capacity
        assert assignment.info["extras"] == 0
        assert sum(assignment.count_for(u.index) for u in problem.users) == problem.total_capacity


def test_low_thread_count_still_produces_a_result_quickly() -> None:
    """Regression: single threaded runs used to hit the time limit (UNKNOWN)."""
    problem = make_problem(users=30, slots=6, capacity=2, wishes=4, desired=2)
    started = time.perf_counter()
    assignment = run("lexicographic", problem, seed=3, time_limit=10, workers=1)
    elapsed = time.perf_counter() - started
    # 12 slots for 30 applicants: everybody who can be served must be served.
    assert assignment.info["guaranteed_ok"] == problem.total_capacity
    assert assignment.info.get("fallback") is None
    assert elapsed < 8.0, f"single threaded allocation took {elapsed:.1f}s"


def test_result_summary_counts_guarantee_and_extras() -> None:
    problem = make_problem(users=10, slots=6, capacity=2, wishes=5, desired=2,
                           guarantee=1, max_per_user=2)
    assignment = run("guaranteed", problem, seed=1)
    info = assignment.info
    assert info["applicants"] == 10
    assert info["capacity"] == 12
    assert info["supplied"] == 10
    assert info["guaranteed_ok"] == 10
    assert info["extras"] == 2
    assert info["guarantee_fulfilled"] is True


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registry_default_is_first_and_lexicographic() -> None:
    methods = available_methods()
    assert methods[0].key == "lexicographic"
    assert get_method(None).key == "lexicographic"
    assert get_method("does-not-exist").key == "lexicographic"


def test_all_expected_methods_are_registered() -> None:
    assert set(METHODS) == {"lexicographic", "rank_based", "guaranteed", "lottery", "fcfs"}


def test_every_method_is_documented_for_the_ui() -> None:
    for spec in available_methods():
        assert spec.label and spec.summary and spec.details
        assert spec.pros and spec.cons

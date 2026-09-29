"""Allocation methods for charging slots.

Ported from the research scripts in the repository root (``methods.py``) and
adapted to the web application: the methods operate on *requests* that employees
filed for an allocation round instead of synthetic availability sets.

Allocation contract
-------------------
Every method honours the same promise:

1. **Guarantee** - each applicant receives ``guaranteed_per_user`` charging
   slots (default: one per week) as long as capacity allows. Nobody is served
   twice while somebody else is left without a slot.
2. **Extras** - only after every applicant has their guaranteed slot the
   remaining capacity is handed out to those who asked for more
   (``desired_count``), round by round: everybody gets a second slot before
   anybody receives a third one.
3. **Preference** - among equally served applicants the better wish ranking
   wins.

There is deliberately **no scoring of people**: nobody is preferred for having
waited longer, for having fewer slots in the past or for any other personal
history. If two applicants compete for the same scarce slot and cannot be
told apart by the rules above, a random draw decides - reproducible because
the random generator is seeded with the week.

Implemented methods
-------------------
* ``lexicographic``  - CP-SAT, default: guarantee, then extras, then wish
                       quality, then a random tie-break
* ``rank_based``     - CP-SAT: guarantee/coverage, then minimise the squared
                       wish rank (nobody should end up with a bad slot)
* ``guaranteed``     - deterministic two-phase greedy without a solver; also
                       the fallback when the solver cannot deliver a result
* ``lottery``        - random draw per slot
* ``fcfs``           - first come, first served (naive baseline: whoever
                       submits first wins)

Solver based methods are lexicographic: each objective level is optimised and
then frozen before the next level is optimised. A deterministic random
tie-break keeps results reproducible without systematically favouring low user
ids.
"""
from __future__ import annotations

import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from ortools.sat.python import cp_model

SlotKey = tuple[int, int]  # (weekday index, window index)

# Safety net so a running web request never blocks for minutes.
WALL_TIME_FACTOR = 4.0
WALL_TIME_MINIMUM = 5.0


class SolverUnavailable(RuntimeError):
    """Raised when CP-SAT cannot produce a usable solution."""


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class AllocationUser:
    """A single applicant inside an allocation run.

    ``priorities`` is ordered: the first entry is the most wanted slot.
    ``desired_count`` is the *total* number of slots the person would like to
    have, including the guaranteed one.
    """

    index: int
    name: str
    priorities: list[SlotKey] = field(default_factory=list)
    desired_count: int = 1
    request_time: float = 0.0  # only used by the FCFS baseline

    @property
    def available(self) -> list[SlotKey]:
        return list(self.priorities)

    @property
    def rank(self) -> dict[SlotKey, int]:
        return {key: position + 1 for position, key in enumerate(self.priorities)}


@dataclass
class AllocationProblem:
    """Everything an allocation method needs to know."""

    slots: dict[SlotKey, int]  # free capacity per slot
    users: list[AllocationUser]
    max_slots_per_user: int = 2
    guaranteed_per_user: int = 1

    @property
    def total_capacity(self) -> int:
        return sum(self.slots.values())

    @property
    def requested(self) -> int:
        return sum(self.limit_for(user) for user in self.users)

    @property
    def guaranteed_demand(self) -> int:
        """Capacity needed to give every applicant the guaranteed slot."""
        return sum(1 for user in self.users if self.limit_for(user) >= 1)

    @property
    def guarantee_is_feasible(self) -> bool:
        return self.guaranteed_demand <= self.total_capacity

    def limit_for(self, user: AllocationUser) -> int:
        """Upper bound of slots this applicant may receive."""
        return max(0, min(user.desired_count, self.max_slots_per_user))

    def guarantee_for(self, user: AllocationUser) -> int:
        """Number of slots this applicant is guaranteed (capacity permitting)."""
        return max(0, min(self.guaranteed_per_user, self.limit_for(user)))


@dataclass
class Assignment:
    """Result of an allocation method."""

    name: str
    user_slots: dict[int, list[SlotKey]] = field(default_factory=dict)
    info: dict = field(default_factory=dict)
    unassigned: list[str] = field(default_factory=list)

    def slots_for(self, user_index: int) -> list[SlotKey]:
        return self.user_slots.get(user_index, [])

    def count_for(self, user_index: int) -> int:
        return len(self.user_slots.get(user_index, []))


@dataclass
class MethodSpec:
    """Metadata used by the admin UI to describe a method."""

    key: str
    label: str
    summary: str
    details: str
    pros: tuple[str, ...]
    cons: tuple[str, ...]
    uses_solver: bool
    func: Callable


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _finish(name: str, result: dict[int, list[SlotKey]], users: list[AllocationUser],
            info: dict | None = None) -> Assignment:
    cleaned = {user.index: sorted(result.get(user.index, [])) for user in users}
    unassigned = [user.name for user in users if not cleaned[user.index]]
    return Assignment(name=name, user_slots=cleaned, info=info or {}, unassigned=unassigned)


def _summarise(assignment: Assignment, problem: AllocationProblem) -> None:
    """Add the counters that the admin UI displays."""
    guaranteed_ok = 0
    extras = 0
    for user in problem.users:
        count = assignment.count_for(user.index)
        if count >= max(1, problem.guarantee_for(user)):
            guaranteed_ok += 1
        extras += max(0, count - max(1, problem.guarantee_for(user)))
    assignment.info.update(
        {
            "applicants": len(problem.users),
            "supplied": sum(1 for user in problem.users if assignment.slots_for(user.index)),
            "assigned": sum(assignment.count_for(user.index) for user in problem.users),
            "guaranteed_ok": guaranteed_ok,
            "extras": extras,
            "capacity": problem.total_capacity,
            "guarantee_fulfilled": guaranteed_ok == len(problem.users),
        }
    )


def _new_solver(seed: int, time_limit: float, workers: int) -> cp_model.CpSolver:
    """Create a solver that is fast *and* reproducible.

    ``linearization_level = 2`` enables the full LP relaxation. Without it,
    runs with few threads burn hundreds of thousands of conflicts on these
    models instead of solving them in milliseconds.
    """
    solver = cp_model.CpSolver()
    solver.parameters.random_seed = seed
    solver.parameters.num_search_workers = max(1, workers)
    solver.parameters.linearization_level = 2
    # Deterministic time keeps the search reproducible across machines; the
    # wall clock limit is only a safety net.
    solver.parameters.max_deterministic_time = max(1.0, time_limit)
    solver.parameters.max_time_in_seconds = max(WALL_TIME_MINIMUM, time_limit * WALL_TIME_FACTOR)
    return solver


def _add_capacity_constraints(model: cp_model.CpModel, problem: AllocationProblem,
                              x: dict) -> None:
    for key, capacity in problem.slots.items():
        applicants = [x[(user.index, key)] for user in problem.users if key in user.available]
        if applicants:
            model.Add(sum(applicants) <= max(0, capacity))


def _add_user_vars(model: cp_model.CpModel, problem: AllocationProblem,
                   x: dict) -> tuple[dict, dict, dict]:
    """Create the per-user coverage indicator variables (>=1, >=2, >=3 slots)."""
    y1, y2, y3 = {}, {}, {}
    for user in problem.users:
        limit = problem.limit_for(user)
        total = sum(x[(user.index, key)] for key in user.available)
        y1[user.index] = model.NewBoolVar(f"y1_{user.index}")
        y2[user.index] = model.NewBoolVar(f"y2_{user.index}")
        y3[user.index] = model.NewBoolVar(f"y3_{user.index}")
        model.Add(total <= limit)
        model.Add(y1[user.index] <= total)
        model.Add(total >= 2 * y2[user.index])
        model.Add(total >= 3 * y3[user.index])
        model.Add(y2[user.index] <= y1[user.index])
        model.Add(y3[user.index] <= y2[user.index])
    return y1, y2, y3


def _snapshot(x: dict, solver: cp_model.CpSolver) -> dict:
    return {key: solver.Value(var) for key, var in x.items()}


def _from_values(values: dict, users: list[AllocationUser]) -> dict[int, list[SlotKey]]:
    result: dict[int, list[SlotKey]] = {user.index: [] for user in users}
    for (user_index, key), taken in values.items():
        if taken:
            result[user_index].append(key)
    return result


def _optimise_level(model: cp_model.CpModel, solver: cp_model.CpSolver, objective,
                    deadline: float, maximise: bool = True) -> int:
    """Solve one objective level within the remaining time budget."""
    remaining = max(0.5, deadline - time.perf_counter())
    solver.parameters.max_time_in_seconds = max(
        WALL_TIME_MINIMUM, min(solver.parameters.max_time_in_seconds, remaining * WALL_TIME_FACTOR)
    )
    solver.parameters.max_deterministic_time = max(
        0.5, min(solver.parameters.max_deterministic_time, remaining)
    )
    if maximise:
        model.Maximize(objective)
    else:
        model.Minimize(objective)
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise SolverUnavailable(f"solver status: {solver.StatusName(status)}")
    return round(solver.ObjectiveValue())


def _solve_levels(model: cp_model.CpModel, solver: cp_model.CpSolver, x: dict,
                  levels: list, deadline: float) -> tuple[dict, dict]:
    """Run the lexicographic chain, freezing every level before the next one.

    If a later level fails, the best solution found so far is returned instead
    of discarding the whole run.
    """
    values: dict | None = None
    info: dict = {"levels": [], "status": solver.StatusName(cp_model.OPTIMAL)}
    for name, objective, maximise in levels:
        if objective is None:
            continue
        try:
            value = _optimise_level(model, solver, objective, deadline, maximise)
        except SolverUnavailable as exc:
            info["stopped_at"] = name
            info["stop_reason"] = str(exc)
            info["status"] = "partial"
            break
        model.Add(objective == value)
        values = _snapshot(x, solver)
        info[name] = value
        info["levels"].append(name)
    if values is None:
        raise SolverUnavailable(info.get("stop_reason", "no objective level solved"))
    return values, info


def _random_tie_break(model: cp_model.CpModel, solver: cp_model.CpSolver, x: dict,
                      problem: AllocationProblem, rng: random.Random, values: dict,
                      deadline: float) -> tuple[dict, str]:
    terms = [
        rng.randrange(1_000_003) * var
        for key, var in x.items()
    ]
    if not terms:
        return values, "empty"
    try:
        _optimise_level(model, solver, sum(terms), deadline, maximise=True)
        return _snapshot(x, solver), solver.StatusName(cp_model.OPTIMAL)
    except SolverUnavailable:
        # The previous level is already optimal - keep its solution.
        return values, "kept"


# ---------------------------------------------------------------------------
# Deterministic method: guarantee first, extras second
# ---------------------------------------------------------------------------

class _FlowNetwork:
    """Minimal Dinic max-flow used by the deterministic allocation.

    A greedy pass is *not* enough for the guarantee: with overlapping wishes it
    can leave an applicant without a slot even though free capacity exists.
    Only a maximum matching rules that out, so the deterministic method
    computes one.
    """

    def __init__(self, size: int) -> None:
        self.graph: list[list[list[int]]] = [[] for _ in range(size)]

    def add_edge(self, source: int, target: int, capacity: int) -> list[int]:
        forward = [target, capacity, len(self.graph[target])]
        backward = [source, 0, len(self.graph[source])]
        self.graph[source].append(forward)
        self.graph[target].append(backward)
        return forward

    def increase(self, edge: list[int], delta: int) -> None:
        edge[1] += delta

    def max_flow(self, source: int, sink: int) -> int:
        total = 0
        while True:
            level = [-1] * len(self.graph)
            level[source] = 0
            queue = deque([source])
            while queue:
                node = queue.popleft()
                for target, capacity, _ in self.graph[node]:
                    if capacity > 0 and level[target] < 0:
                        level[target] = level[node] + 1
                        queue.append(target)
            if level[sink] < 0:
                return total
            iterator = [0] * len(self.graph)
            while True:
                pushed = self._augment(source, sink, 1 << 30, level, iterator)
                if not pushed:
                    break
                total += pushed

    def _augment(self, node: int, sink: int, limit: int, level: list,
                 iterator: list) -> int:
        if node == sink:
            return limit
        while iterator[node] < len(self.graph[node]):
            edge = self.graph[node][iterator[node]]
            target, capacity, reverse = edge
            if capacity > 0 and level[target] == level[node] + 1:
                pushed = self._augment(target, sink, min(limit, capacity), level, iterator)
                if pushed:
                    edge[1] -= pushed
                    self.graph[target][reverse][1] += pushed
                    return pushed
            iterator[node] += 1
        return 0


def guaranteed_then_extras(problem: AllocationProblem, rng: random.Random | None = None,
                           time_limit: float = 10.0, workers: int = 1) -> Assignment:
    """Allocation in flow rounds - the guarantee is structural.

    Round 1 limits every applicant to one slot and computes a maximum matching,
    which maximises the number of served people. The following rounds raise the
    per-person limit by exactly one slot at a time and keep augmenting the same
    flow: because a person can grow by at most one slot per round, everybody
    who wants a second slot is considered before anybody can receive a third
    one. An augmenting path never removes a slot from somebody without giving
    them another one in the same step, so extras can only appear once the
    coverage of round 1 is secured - extras therefore never take a slot away
    from a person who has none.

    Applicants are processed in a **shuffled order** (seeded, therefore
    reproducible): no personal history influences the result, so a scarce slot
    is decided by drawing lots rather than by who waited longest.

    Finally a preference pass swaps slots for better ranked wishes wherever
    spare capacity allows, which can only improve the result.
    """
    started = time.perf_counter()
    users = list(problem.users)
    slots = list(problem.slots)
    if not users or not slots:
        assignment = _finish("Garantieverfahren", {}, users, {"status": "empty"})
        _summarise(assignment, problem)
        return assignment

    rng = rng or random.Random()
    ordered = sorted(users, key=lambda u: u.index)
    rng.shuffle(ordered)

    source = 0
    user_node = {user.index: 1 + position for position, user in enumerate(ordered)}
    slot_node = {key: 1 + len(ordered) + position for position, key in enumerate(slots)}
    sink = 1 + len(ordered) + len(slots)

    network = _FlowNetwork(sink + 1)
    source_edges: dict[int, list[int]] = {}
    for user in ordered:
        source_edges[user.index] = network.add_edge(source, user_node[user.index], 0)
    # Wishes are added in ranking order so the search prefers the best wishes.
    slot_edges: list[tuple[int, tuple[int, int], list[int]]] = []
    for user in ordered:
        for key in user.priorities:
            if key in slot_node:
                edge = network.add_edge(user_node[user.index], slot_node[key], 1)
                slot_edges.append((user.index, key, edge))
    for key in slots:
        network.add_edge(slot_node[key], sink, max(0, problem.slots[key]))

    # Round 1 is the guarantee; every further round hands out one more slot per
    # person. Within a round nobody can grow by more than one slot, so the
    # extras are spread as widely as the capacity allows.
    max_limit = max((problem.limit_for(user) for user in ordered), default=0)
    for _level in range(1, max_limit + 1):
        for user in ordered:
            if problem.limit_for(user) >= _level:
                network.increase(source_edges[user.index], 1)
        network.max_flow(source, sink)

    result: dict[int, list[SlotKey]] = {user.index: [] for user in users}
    for user_index, key, edge in slot_edges:
        if edge[1] == 0:  # capacity 1 used up -> this wish was granted
            result[user_index].append(key)

    _improve_preferences(problem, result, ordered)

    assignment = _finish("Garantieverfahren", result, users, {
        "status": "deterministic",
        "wall_time": round(time.perf_counter() - started, 4),
    })
    _summarise(assignment, problem)
    return assignment


def _improve_preferences(problem: AllocationProblem,
                         result: dict[int, list[SlotKey]],
                         ordered: list[AllocationUser]) -> None:
    """Swap granted slots for better ranked wishes while capacity allows.

    The number of slots per person and per slot stays the same, so neither the
    guarantee nor the capacity limits are affected.
    """
    used: dict[SlotKey, int] = {}
    for keys in result.values():
        for key in keys:
            used[key] = used.get(key, 0) + 1

    for user in ordered:
        mine = result.get(user.index, [])
        if not mine:
            continue
        for key in user.priorities:
            if key in mine:
                break  # wishes are ranked: everything before this is granted
            if used.get(key, 0) >= problem.slots.get(key, 0):
                continue
            worst = max(mine, key=lambda granted: user.priorities.index(granted))
            if user.priorities.index(worst) > user.priorities.index(key):
                mine.remove(worst)
                used[worst] -= 1
                mine.append(key)
                used[key] = used.get(key, 0) + 1
                break


# ---------------------------------------------------------------------------
# Method 1: first come, first served
# ---------------------------------------------------------------------------

def fcfs(problem: AllocationProblem, rng: random.Random | None = None,
         time_limit: float = 10.0, workers: int = 1) -> Assignment:
    """Serve applicants in the order in which they filed their wishes.

    Kept as a comparison baseline: this is exactly the "whoever clicks first
    wins" behaviour that round based allocation is meant to remove.
    """
    started = time.perf_counter()
    remaining = dict(problem.slots)
    counts = {user.index: 0 for user in problem.users}
    result: dict[int, list[SlotKey]] = {user.index: [] for user in problem.users}

    for user in sorted(problem.users, key=lambda u: (u.request_time, u.index)):
        limit = problem.limit_for(user)
        for key in user.priorities:
            if counts[user.index] >= limit:
                break
            if remaining.get(key, 0) <= 0:
                continue
            result[user.index].append(key)
            remaining[key] -= 1
            counts[user.index] += 1

    assignment = _finish("First Come, First Served", result, problem.users, {
        "status": "deterministic",
        "wall_time": round(time.perf_counter() - started, 4),
    })
    _summarise(assignment, problem)
    return assignment


# ---------------------------------------------------------------------------
# Method 2: lottery
# ---------------------------------------------------------------------------

def lottery(problem: AllocationProblem, rng: random.Random | None = None,
            time_limit: float = 10.0, workers: int = 1) -> Assignment:
    """Draw winners randomly for every single slot."""
    rng = rng or random.Random()
    started = time.perf_counter()
    remaining = dict(problem.slots)
    counts = {user.index: 0 for user in problem.users}
    result: dict[int, list[SlotKey]] = {user.index: [] for user in problem.users}

    keys = list(remaining)
    rng.shuffle(keys)
    for key in keys:
        applicants = [
            user for user in problem.users
            if key in user.available and counts[user.index] < problem.limit_for(user)
        ]
        rng.shuffle(applicants)
        for user in applicants[: max(0, remaining.get(key, 0))]:
            result[user.index].append(key)
            counts[user.index] += 1

    assignment = _finish("Losverfahren", result, problem.users, {
        "status": "random",
        "wall_time": round(time.perf_counter() - started, 4),
    })
    _summarise(assignment, problem)
    return assignment


# ---------------------------------------------------------------------------
# Method 3: rank based optimisation
# ---------------------------------------------------------------------------

def rank_based(problem: AllocationProblem, rng: random.Random | None = None,
               time_limit: float = 20.0, workers: int = 8) -> Assignment:
    """Guarantee/coverage first, then minimise the squared wish rank.

    A second wish weighs four times as much as the first one, so nobody ends up
    with a very poor slot while somebody else gets their favourite.
    """
    rng = rng or random.Random()
    try:
        return _rank_based_solver(problem, rng, time_limit, workers)
    except SolverUnavailable as exc:
        return _fallback(problem, rng, "Rang-Optimierung", exc)


def _rank_based_solver(problem: AllocationProblem, rng: random.Random,
                       time_limit: float, workers: int) -> Assignment:
    started = time.perf_counter()
    deadline = started + max(1.0, time_limit)
    model = cp_model.CpModel()
    x = {
        (user.index, key): model.NewBoolVar(f"x_{user.index}_{key[0]}_{key[1]}")
        for user in problem.users
        for key in user.available
    }
    _add_capacity_constraints(model, problem, x)
    y1, y2, y3 = _add_user_vars(model, problem, x)
    solver = _new_solver(rng.randrange(2 ** 31), time_limit, workers)

    ranks = {user.index: user.rank for user in problem.users}
    cost = sum(
        (ranks[user.index][key] ** 2) * x[(user.index, key)]
        for user in problem.users
        for key in user.available
    )
    second_terms = [y2[u.index] for u in problem.users if problem.limit_for(u) >= 2]
    third_terms = [y3[u.index] for u in problem.users if problem.limit_for(u) >= 3]

    # Level 1 maximises coverage, which realises the guarantee. Levels 2 and 3
    # count how many people may receive extras - both are frozen before the
    # rank cost is optimised, so extras can never push somebody out of their
    # guaranteed slot.
    levels = [
        ("supplied", sum(y1.values()), True),
        ("second", sum(second_terms) if second_terms else None, True),
        ("third", sum(third_terms) if third_terms else None, True),
        ("rank_cost", cost, False),
    ]
    values, info = _solve_levels(model, solver, x, levels, deadline)
    values, tie = _random_tie_break(model, solver, x, problem, rng, values, deadline)

    assignment = _finish("Rang-Optimierung", _from_values(values, problem.users), problem.users, {
        **info,
        "tie_break": tie,
        "wall_time": round(time.perf_counter() - started, 2),
    })
    _summarise(assignment, problem)
    return assignment


# ---------------------------------------------------------------------------
# Method 4: lexicographic optimisation (default)
# ---------------------------------------------------------------------------

def lexicographic(problem: AllocationProblem, rng: random.Random | None = None,
                  time_limit: float = 20.0, workers: int = 8) -> Assignment:
    """The default method.

    Objective order (each level is frozen before the next one is optimised):

    1. ``supplied`` - the number of applicants who receive at least one slot.
       Maximising this realises the guarantee.
    2. ``seconds`` - applicants with a second slot. Only reached once coverage
       is maximal, so extras require spare capacity.
    3. ``utility`` - quality of the granted wishes (favourite wish = 5 points).
    4. random tie-break, so no applicant is systematically preferred.
    """
    rng = rng or random.Random()
    try:
        return _lexicographic_solver(problem, rng, time_limit, workers)
    except SolverUnavailable as exc:
        return _fallback(problem, rng, "Lexikografisch", exc)


def _lexicographic_solver(problem: AllocationProblem, rng: random.Random,
                          time_limit: float, workers: int) -> Assignment:
    started = time.perf_counter()
    deadline = started + max(1.0, time_limit)
    model = cp_model.CpModel()
    x = {
        (user.index, key): model.NewBoolVar(f"x_{user.index}_{key[0]}_{key[1]}")
        for user in problem.users
        for key in user.available
    }
    _add_capacity_constraints(model, problem, x)
    y1, y2, y3 = _add_user_vars(model, problem, x)
    solver = _new_solver(rng.randrange(2 ** 31), time_limit, workers)

    second_terms = [y2[u.index] for u in problem.users if problem.limit_for(u) >= 2]
    utility_terms = [
        max(1, 5 - position) * x[(user.index, key)]
        for user in problem.users
        for position, key in enumerate(user.priorities)
    ]

    levels = [
        ("supplied", sum(y1.values()), True),
        ("seconds", sum(second_terms) if second_terms else None, True),
        ("utility", sum(utility_terms) if utility_terms else None, True),
    ]
    values, info = _solve_levels(model, solver, x, levels, deadline)
    values, tie = _random_tie_break(model, solver, x, problem, rng, values, deadline)

    assignment = _finish("Lexikografisch", _from_values(values, problem.users), problem.users, {
        **info,
        "tie_break": tie,
        "wall_time": round(time.perf_counter() - started, 2),
    })
    _summarise(assignment, problem)
    return assignment


def _fallback(problem: AllocationProblem, rng: random.Random, label: str,
              exc: Exception) -> Assignment:
    """Used when the solver cannot deliver: keep the promise, lose the polish."""
    assignment = guaranteed_then_extras(problem, rng)
    assignment.name = label
    assignment.info["fallback"] = True
    assignment.info["fallback_reason"] = str(exc)
    return assignment


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

METHODS: dict[str, MethodSpec] = {
    "lexicographic": MethodSpec(
        key="lexicographic",
        label="Lexikografisch (empfohlen)",
        summary="Garantierte Grundversorgung, danach Zusatzwünsche, danach die besten Zeiten.",
        details=(
            "Der Solver optimiert die Ziele streng nacheinander. Zuerst bekommt "
            "jede Person ihre garantierte Ladezeit – das Ziel ist die maximale "
            "Anzahl versorgter Personen. Dieses Ergebnis wird festgeschrieben, "
            "erst danach werden zusätzlich gewünschte Ladezeiten verteilt und zum "
            "Schluss entscheidet der Zufall über gleichwertige Lösungen. "
            "Niemand erhält eine zweite Ladezeit, solange jemand anders noch keine "
            "hat. Es gibt keine Bevorzugung einzelner Personen: Wer einen knappen "
            "Platz bekommt, entscheidet bei sonst gleichen Bedingungen das Los."
        ),
        pros=(
            "Garantiert die Grundversorgung, solange Kapazität vorhanden ist",
            "Verteilt Zusatzwünsche erst, wenn alle versorgt sind",
            "Keine Punktesysteme, keine persönliche Bevorzugung",
        ),
        cons=(
            "Berechnung dauert wenige Sekunden",
            "Ergebnis ist nur mit gleichem Zufallsstart exakt reproduzierbar",
        ),
        uses_solver=True,
        func=lexicographic,
    ),
    "rank_based": MethodSpec(
        key="rank_based",
        label="Rang-Optimierung",
        summary="Erst Grundversorgung, dann möglichst gute Wunschränge für alle.",
        details=(
            "Wie die lexikografische Methode: Die Versorgung (mindestens ein Platz "
            "für alle) steht im Vordergrund, danach wird die Summe der quadrierten "
            "Wunschränge minimiert. Ein zweiter Wunsch wiegt also viermal so schwer "
            "wie der erste – dadurch bekommt niemand einen sehr schlechten Platz, "
            "während andere ihren Lieblingsplatz erhalten. Auch hier entscheidet "
            "bei Gleichstand der Zufall, nicht die Person."
        ),
        pros=(
            "Sehr ausgewogene Ergebnisse, keine extremen Benachteiligungen",
            "Einfach zu erklären: 'niemand bekommt den schlechtesten Platz'",
        ),
        cons=(
            "Begünstigt strukturell Personen mit vielen Wunschzeiten",
            "Benötigt einen Solver-Lauf",
        ),
        uses_solver=True,
        func=rank_based,
    ),
    "guaranteed": MethodSpec(
        key="guaranteed",
        label="Garantieverfahren (sofort)",
        summary="Zweistufig: erst eine Ladezeit für alle, dann Zusatzwünsche in Runden.",
        details=(
            "Kein Solver, sondern eine feste Regel: In der ersten Stufe erhält "
            "jede Person ihre garantierte Ladezeit – berechnet als maximale "
            "Zuordnung, damit wirklich jede Person versorgt wird, deren Wünsche "
            "mit der freien Kapazität erfüllbar sind. In der zweiten Stufe werden "
            "verbleibende Plätze zusätzlich verteilt, wobei eine Person nie "
            "unter ihre Grundversorgung fallen kann. Zum Schluss werden Termine "
            "gegen besser bewertete Wünsche getauscht, solange Plätze frei sind. "
            "Wer einen knappen Platz erhält, entscheidet das Los – das Ergebnis "
            "ist mit gleichem Startwert identisch und damit überprüfbar."
        ),
        pros=(
            "Sofort berechnet, kein Solver nötig",
            "Wiederholbar und leicht nachvollziehbar",
            "Erzwingt die Grundversorgung mathematisch (maximale Zuordnung)",
        ),
        cons=(
            "Optimiert Wunschzeiten nur nachgelagert",
            "Bei stark unterschiedlichen Wünschen weniger Treffer als beim Solver",
        ),
        uses_solver=False,
        func=guaranteed_then_extras,
    ),
    "lottery": MethodSpec(
        key="lottery",
        label="Losverfahren",
        summary="Pro Slot werden die Gewinner zufällig gezogen.",
        details=(
            "Für jeden Slot werden die Bewerbungen gesammelt und die verfügbaren "
            "Plätze per Zufall vergeben. Es gibt keinen Zeitvorteil, aber auch "
            "keine Steuerung: Die garantierte Grundversorgung ist nicht "
            "sichergestellt, und Wunschprioritäten bleiben unberücksichtigt."
        ),
        pros=(
            "Extrem einfach zu erklären und zu prüfen",
            "Kein Zeitdruck beim Abgeben der Wünsche",
        ),
        cons=(
            "Keine Garantie auf eine Mindestversorgung",
            "Wunschprioritäten bleiben unberücksichtigt",
        ),
        uses_solver=False,
        func=lottery,
    ),    "fcfs": MethodSpec(
        key="fcfs",
        label="First Come, First Served",
        summary="Wer zuerst anfragt, bekommt den Platz.",
        details=(
            "Die Reihenfolge des Eingangs entscheidet. Diese Methode ist nur als "
            "Vergleichsmaßstab sinnvoll: Sie erzeugt genau den Ansturm kurz nach "
            "Öffnung der Vergaberunde, den das System eigentlich verhindern soll, "
            "und kennt keine Grundversorgung."
        ),
        pros=(
            "Sehr leicht verständlich",
            "Keine Rechenzeit nötig",
        ),
        cons=(
            "Bevorzugt dauerhaft schnelle und ständig anwesende Personen",
            "Bestraft Schichtdienst, Urlaub und Homeoffice",
        ),
        uses_solver=False,
        func=fcfs,
    ),
}

DEFAULT_METHOD = "lexicographic"


def available_methods() -> list[MethodSpec]:
    """Return all methods, default method first."""
    ordered = [METHODS[DEFAULT_METHOD]]
    ordered.extend(spec for key, spec in METHODS.items() if key != DEFAULT_METHOD)
    return ordered


def get_method(key: str | None) -> MethodSpec:
    return METHODS.get(key or DEFAULT_METHOD, METHODS[DEFAULT_METHOD])


def run(method_key: str, problem: AllocationProblem, seed: int = 42,
        time_limit: float = 20.0, workers: int = 8) -> Assignment:
    """Execute a method by key using a reproducible random seed."""
    spec = get_method(method_key)
    rng = random.Random(seed)
    assignment = spec.func(problem, rng, time_limit, workers)
    assignment.info.setdefault("method", spec.key)
    assignment.info.setdefault("method_label", spec.label)
    _summarise(assignment, problem)
    return assignment

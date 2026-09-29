from __future__ import annotations

import time
from dataclasses import dataclass, field

from ortools.sat.python import cp_model


@dataclass
class Assignment:
    name: str
    slots: dict
    info: dict = field(default_factory=dict)


def _new_solver(seed: int, time_limit: float) -> cp_model.CpSolver:
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.random_seed = seed
    solver.parameters.num_search_workers = 8
    return solver


def _x_vars(model: cp_model.CpModel, users, slot_list) -> dict:
    x = {}
    for user in users:
        for slot_index in user.available_slots:
            x[(user.index, slot_index)] = model.NewBoolVar(f"x_{user.index}_{slot_index}")
    for slot in slot_list:
        model.Add(
            sum(x[(user.index, slot.index)] for user in users if slot.index in user.available)
            <= slot.capacity
        )
    return x


def _solution(x: dict, solver: cp_model.CpSolver, users) -> dict:
    result = {user.index: [] for user in users}
    for (user_index, slot_index), var in x.items():
        if solver.Value(var):
            result[user_index].append(slot_index)
    for user_index in result:
        result[user_index].sort()
    return result


def _check(solver: cp_model.CpSolver, status: int) -> None:
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError(f"Solver-Status: {solver.StatusName(status)}")


def _optimise(model: cp_model.CpModel, solver: cp_model.CpSolver, objective) -> float:
    model.Maximize(objective)
    status = solver.Solve(model)
    _check(solver, status)
    return solver.ObjectiveValue()


def _coverage_vars(model: cp_model.CpModel, users, x: dict) -> tuple:
    y1, y2, y3 = {}, {}, {}
    for user in users:
        total = sum(x[(user.index, slot)] for slot in user.available_slots)
        y1[user.index] = model.NewBoolVar(f"y1_{user.index}")
        y2[user.index] = model.NewBoolVar(f"y2_{user.index}")
        y3[user.index] = model.NewBoolVar(f"y3_{user.index}")
        model.Add(total <= user.desired_slots)
        model.Add(y1[user.index] <= total)
        model.Add(total >= 2 * y2[user.index])
        model.Add(total >= 3 * y3[user.index])
        model.Add(y2[user.index] <= y1[user.index])
        model.Add(y3[user.index] <= y2[user.index])
    return y1, y2, y3


def _fix_coverage(model: cp_model.CpModel, solver: cp_model.CpSolver, users, y1, y2, y3) -> list:
    levels = []
    coverage = round(_optimise(model, solver, sum(y1.values())))
    levels.append(coverage)
    model.Add(sum(y1.values()) == coverage)

    second_terms = [y2[user.index] for user in users if user.desired_slots >= 2]
    second = round(_optimise(model, solver, sum(second_terms))) if second_terms else 0
    levels.append(second)
    if second_terms:
        model.Add(sum(second_terms) == second)

    third_terms = [y3[user.index] for user in users if user.desired_slots >= 3]
    third = round(_optimise(model, solver, sum(third_terms))) if third_terms else 0
    levels.append(third)
    if third_terms:
        model.Add(sum(third_terms) == third)

    return levels


def fcfs(users, slot_list, rng) -> Assignment:
    counts = {user.index: 0 for user in users}
    result = {user.index: [] for user in users}
    for slot in slot_list:
        applicants = [
            user
            for user in users
            if slot.index in user.available and counts[user.index] < user.desired_slots
        ]
        applicants.sort(key=lambda user: (user.request_time, user.index))
        for user in applicants[: slot.capacity]:
            result[user.index].append(slot.index)
            counts[user.index] += 1
    return Assignment("First Come, First Served", result)


def lottery(users, slot_list, rng) -> Assignment:
    counts = {user.index: 0 for user in users}
    result = {user.index: [] for user in users}
    order = list(slot_list)
    rng.shuffle(order)
    for slot in order:
        applicants = [
            user
            for user in users
            if slot.index in user.available and counts[user.index] < user.desired_slots
        ]
        winners = rng.sample(applicants, min(slot.capacity, len(applicants)))
        for user in winners:
            result[user.index].append(slot.index)
            counts[user.index] += 1
    for user_index in result:
        result[user_index].sort()
    return Assignment("Losverfahren (pro Slot)", result)


def rank_based(users, slot_list, rng, time_limit: float = 30.0) -> Assignment:
    seed = rng.randrange(2**31)
    model = cp_model.CpModel()
    x = _x_vars(model, users, slot_list)
    y1, y2, y3 = _coverage_vars(model, users, x)
    solver = _new_solver(seed, time_limit)
    started = time.perf_counter()
    levels = _fix_coverage(model, solver, users, y1, y2, y3)

    cost = sum(
        (user.rank[slot] ** 2) * x[(user.index, slot)]
        for user in users
        for slot in user.available_slots
    )
    model.Minimize(cost)
    status = solver.Solve(model)
    _check(solver, status)
    rank_cost = round(solver.ObjectiveValue())
    model.Add(cost == rank_cost)

    noise = {key: rng.randrange(1_000_003) for key in x}
    model.Maximize(sum(noise[key] * var for key, var in x.items()))
    status = solver.Solve(model)
    _check(solver, status)
    elapsed = time.perf_counter() - started

    info = {
        "coverage": levels[0],
        "second": levels[1],
        "third": levels[2],
        "rank_cost": rank_cost,
        "wall_time": elapsed,
        "status": solver.StatusName(status),
    }
    return Assignment("Rang-Optimierung", _solution(x, solver, users), info)


def lexicographic(users, slot_list, rng, time_limit: float = 30.0) -> Assignment:
    seed = rng.randrange(2**31)
    model = cp_model.CpModel()
    x = _x_vars(model, users, slot_list)
    y1, y2, y3 = _coverage_vars(model, users, x)
    solver = _new_solver(seed, time_limit)
    started = time.perf_counter()
    levels = _fix_coverage(model, solver, users, y1, y2, y3)

    preference = sum(
        user.utility[slot] * x[(user.index, slot)]
        for user in users
        for slot in user.available_slots
    )
    utility_level = round(_optimise(model, solver, preference))
    model.Add(preference == utility_level)

    noise = {key: rng.randrange(1_000_003) for key in x}
    model.Maximize(sum(noise[key] * var for key, var in x.items()))
    status = solver.Solve(model)
    _check(solver, status)
    elapsed = time.perf_counter() - started

    info = {
        "coverage": levels[0],
        "second": levels[1],
        "third": levels[2],
        "utility": utility_level,
        "wall_time": elapsed,
        "status": solver.StatusName(status),
    }
    return Assignment("Lexikografisch + Zufalls-Tie-Break", _solution(x, solver, users), info)

"""Ad-hoc solver benchmark: 80 users, both CP-SAT methods.

Runs standalone (pipe into a container): ``python - < bench_solver_80.py``
"""
from __future__ import annotations

import resource
import time

from chargefair.allocation import (
    METHODS,
    AllocationProblem,
    AllocationUser,
    run,
)

USERS = 80
DAYS = 5
WINDOWS = 8          # 5 x 8 = 40 candidate slots per week
CAPACITY = 3         # charging points per slot -> 120 total capacity
WISHES = 6           # wishes filed per user
DESIRED = 2          # would like 2 slots per week
GUARANTEE = 1
MAX_PER_USER = 3


def build_problem() -> AllocationProblem:
    import random

    rng = random.Random(7)
    grid = [(day, window) for day in range(DAYS) for window in range(WINDOWS)]
    slot_table = {key: CAPACITY for key in grid}
    users = []
    for index in range(USERS):
        priorities = rng.sample(list(slot_table), WISHES)
        users.append(
            AllocationUser(
                index=index,
                name=f"Person {index}",
                priorities=priorities,
                desired_count=DESIRED,
                request_time=float(index),
            )
        )
    return AllocationProblem(
        slots=slot_table,
        users=users,
        max_slots_per_user=MAX_PER_USER,
        guaranteed_per_user=GUARANTEE,
    )


def peak_rss_mib() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def main() -> None:
    problem = build_problem()
    print(f"users={USERS} slots={len(problem.slots)} "
          f"capacity={problem.total_capacity} "
          f"requested={problem.requested} "
          f"guarantee_feasible={problem.guarantee_is_feasible}")
    print(f"baseline RSS: {peak_rss_mib():.1f} MiB\n")

    for key, spec in METHODS.items():
        if not spec.uses_solver:
            continue
        start = time.perf_counter()
        assignment = run(key, problem, seed=42, time_limit=20.0, workers=8)
        elapsed = time.perf_counter() - start
        served = sum(1 for u in problem.users
                     if assignment.slots_for(u.index))
        print(f"{key:16s} time={elapsed:6.2f}s  "
              f"peak_rss={peak_rss_mib():7.1f} MiB  "
              f"served_users={served}/{USERS}  "
              f"assigned={sum(len(assignment.slots_for(u.index)) for u in problem.users)}")

    # Non-solver baseline for comparison
    start = time.perf_counter()
    assignment = run("guaranteed", problem, seed=42, time_limit=20.0, workers=8)
    elapsed = time.perf_counter() - start
    served = sum(1 for u in problem.users if assignment.slots_for(u.index))
    print(f"{'guaranteed':16s} time={elapsed:6.2f}s  "
          f"peak_rss={peak_rss_mib():7.1f} MiB  "
          f"served_users={served}/{USERS}")
    print(f"\nprocess peak RSS: {peak_rss_mib():.1f} MiB")


if __name__ == "__main__":
    main()

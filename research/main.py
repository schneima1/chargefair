"""Pre-study entry point: simulate users and compare the allocation methods.

    python main.py --users 70 --seed 42

Matplotlib is only imported when figures are actually requested, so the
comparison also runs in a slim environment (``--no-figures``).
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

from data import build_slots, generate_users
from methods import fcfs, lexicographic, lottery, rank_based
from report import (
    evaluation_lines,
    print_comparison,
    print_detail,
    print_overview,
    ranking_lines,
    write_csv,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Vergabe von 7 Ladepunkten an 70 Nutzer")
    parser.add_argument("--seed", type=int, default=42, help="Zufallsseed fuer Reproduzierbarkeit")
    parser.add_argument("--users", type=int, default=70, help="Anzahl Mitarbeiter")
    parser.add_argument("--time-limit", type=float, default=30.0, help="Solver-Zeitlimit in Sekunden")
    parser.add_argument("--out", type=Path, default=Path("out"), help="Ausgabeverzeichnis")
    parser.add_argument("--no-figures", action="store_true", help="Keine Grafiken erzeugen")
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    rng = random.Random(args.seed)

    slot_list = build_slots()
    users = generate_users(args.users, slot_list, rng)

    assignments = [
        fcfs(users, slot_list, rng),
        lottery(users, slot_list, rng),
        rank_based(users, slot_list, rng, args.time_limit),
        lexicographic(users, slot_list, rng, args.time_limit),
    ]

    print_overview(slot_list, users)
    print_comparison(assignments, users, slot_list)

    printed = []
    printed.extend(evaluation_lines(assignments, users, slot_list))
    ranking, best = ranking_lines(assignments, users, slot_list)
    printed.extend(ranking)
    for line in printed:
        print(line)

    print_detail(assignments[-1], users, slot_list)
    write_csv(assignments, users, slot_list, args.out)

    figure_dir = args.out / "figures"
    (args.out).mkdir(parents=True, exist_ok=True)
    (args.out / "auswertung.txt").write_text(
        "\n".join(printed) + "\n", encoding="utf-8"
    )
    print(f"Textauswertung: {args.out / 'auswertung.txt'}")
    print(f"CSV-Export: {args.out / 'assignments.csv'} und {args.out / 'summary.csv'}")

    if not args.no_figures:
        try:
            from visuals import save_comparison_figure, save_method_figure
        except ImportError:
            print(
                "Hinweis: matplotlib ist nicht installiert – keine Grafiken. "
                "Installation: pip install matplotlib",
            )
        else:
            paths = [
                save_method_figure(assignment, users, slot_list, figure_dir)
                for assignment in assignments
            ]
            paths.append(save_comparison_figure(assignments, users, slot_list, figure_dir))
            print("Grafiken:")
            for path in paths:
                print(f"  {path}")
    print(f"Beste Methode: {best}")


if __name__ == "__main__":
    main()

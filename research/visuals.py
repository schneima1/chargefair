from __future__ import annotations

import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np

from data import DAY_NAMES, WINDOWS
from report import evaluate, text_evaluation

BLUE = "#1565c0"
GREEN = "#2e7d32"
ORANGE = "#ef6c00"
RED = "#c62828"
GREY = "#607d8b"
WINDOW_LABELS = [f"{start:02d}-{end:02d}" for start, end in WINDOWS]


def save_method_figure(assignment, users, slot_list, out_dir: Path) -> Path:
    figure, axes = plt.subplots(2, 3, figsize=(19, 10))
    _plot_slots_per_user(axes[0][0], assignment, users)
    _plot_heatmap(axes[0][1], assignment, users, slot_list)
    _plot_stars(axes[0][2], assignment, users)
    _plot_ranks(axes[1][0], assignment, users)
    _plot_key_metrics(axes[1][1], assignment, users, slot_list)
    _plot_problem_categories(axes[1][2], assignment, users, slot_list)
    figure.suptitle(f"Verfahren: {assignment.name}", fontsize=16, fontweight="bold")
    figure.tight_layout(rect=(0.0, 0.0, 1.0, 0.95))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_slug(assignment.name)}.png"
    figure.savefig(path, dpi=110)
    plt.close(figure)
    return path


def save_comparison_figure(assignments, users, slot_list, out_dir: Path) -> Path:
    names = [assignment.name for assignment in assignments]
    short = [_short(name) for name in names]
    metrics = [evaluate(assignment, users, slot_list) for assignment in assignments]
    evaluations = [text_evaluation(assignment, users, slot_list) for assignment in assignments]
    positions = np.arange(len(assignments))
    figure, axes = plt.subplots(2, 2, figsize=(17, 11))

    _grouped_bars(
        axes[0][0],
        positions,
        short,
        [
            ("mind. 1 Slot", [m["covered"] for m in metrics], GREEN),
            ("Bedarf voll gedeckt", [m["demand_met"] for m in metrics], BLUE),
            ("unter Bedarf", [m["under"] for m in metrics], ORANGE),
        ],
        "Versorgung und Bedarfsdeckung",
        ylabel="Anzahl Nutzer",
        total=len(users),
    )

    _bars(
        axes[0][1],
        positions,
        [m["utilisation"] * 100 for m in metrics],
        short,
        "Auslastung der 7 Ladeplaetze",
        ylabel="Auslastung in %",
        color=GREEN,
        ylim=(0, 108),
        value_format="{:.0f} %",
    )

    _grouped_bars(
        axes[1][0],
        positions,
        short,
        [
            ("ohne Ladeslot", [len(e["without_slot"]) for e in evaluations], RED),
            ("ohne geeigneten Slot", [len(e["without_good"]) for e in evaluations], ORANGE),
            ("ohne Top-2-Wunsch", [len(e["without_top"]) for e in evaluations], GREY),
        ],
        "Unversorgte / unzufriedene Nutzer (kleiner ist besser)",
        ylabel="Anzahl Nutzer",
    )

    _bars(
        axes[1][1],
        positions,
        [m["mean_utility"] for m in metrics],
        short,
        "Durchschnittlicher Nutzen (Balken) und Oe Rang (gestrichelt)",
        ylabel="Sterne (1-5)",
        color=BLUE,
        ylim=(0, 5.4),
        value_format="{:.2f}",
    )
    twin = axes[1][1].twinx()
    twin.plot(positions, [m["mean_rank"] for m in metrics], "o--", color=RED)
    twin.set_ylabel("durchschnittlicher Rang", color=RED)
    twin.tick_params(axis="y", labelcolor=RED)

    figure.suptitle("Verfahrensvergleich", fontsize=16, fontweight="bold")
    figure.tight_layout(rect=(0.0, 0.0, 1.0, 0.95))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "vergleich.png"
    figure.savefig(path, dpi=110)
    plt.close(figure)
    return path


def _plot_slots_per_user(axis, assignment, users) -> None:
    counts = [0, 0, 0, 0]
    for user in users:
        counts[len(assignment.slots[user.index])] += 1
    colors = [RED, ORANGE, BLUE, GREEN]
    bars = axis.bar(["0", "1", "2", "3"], counts, color=colors)
    axis.set_title("Verteilung: Slots je Nutzer")
    axis.set_xlabel("erhaltene Slots")
    axis.set_ylabel("Anzahl Nutzer")
    axis.set_ylim(0, max(counts) * 1.25 + 1)
    for bar, count in zip(bars, counts):
        axis.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5, str(count), ha="center")


def _plot_heatmap(axis, assignment, users, slot_list) -> None:
    grid = np.zeros((len(DAY_NAMES), len(WINDOWS)))
    for user in users:
        for slot_index in assignment.slots[user.index]:
            slot = slot_list[slot_index]
            column = (slot.start - WINDOWS[0][0]) // 3
            grid[slot.day][column] += 1
    capacity = slot_list[0].capacity
    image = axis.imshow(grid, cmap="YlGnBu", vmin=0, vmax=capacity, aspect="auto")
    axis.set_xticks(range(len(WINDOWS)), WINDOW_LABELS)
    axis.set_yticks(range(len(DAY_NAMES)), DAY_NAMES)
    axis.set_title(f"Wochenbelegung (max. {capacity} je Feld)")
    for day in range(len(DAY_NAMES)):
        for column in range(len(WINDOWS)):
            value = int(grid[day][column])
            axis.text(
                column,
                day,
                str(value),
                ha="center",
                va="center",
                color="white" if value > capacity * 0.6 else "black",
            )
    axis.figure.colorbar(image, ax=axis, fraction=0.046, pad=0.04)


def _plot_stars(axis, assignment, users) -> None:
    counts = {stars: 0 for stars in range(1, 6)}
    for user in users:
        for slot_index in assignment.slots[user.index]:
            counts[user.utility[slot_index]] += 1
    bars = axis.bar([str(stars) for stars in range(1, 6)], list(counts.values()), color=BLUE)
    axis.set_title("Sterne der zugeteilten Slots")
    axis.set_xlabel("Praefenz (Sterne)")
    axis.set_ylabel("Anzahl Zuteilungen")
    axis.set_ylim(0, max(counts.values()) * 1.25 + 1)
    for bar, count in zip(bars, counts.values()):
        axis.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5, str(count), ha="center")


def _plot_ranks(axis, assignment, users) -> None:
    max_rank = max((max(user.rank.values()) for user in users), default=1)
    counts = {rank: 0 for rank in range(1, max_rank + 1)}
    for user in users:
        for slot_index in assignment.slots[user.index]:
            counts[user.rank[slot_index]] += 1
    axis.bar([str(rank) for rank in counts], list(counts.values()), color=GREY)
    axis.set_title("Rang der zugeteilten Slots")
    axis.set_xlabel("Rang (1 = Lieblingsslot)")
    axis.set_ylabel("Anzahl Zuteilungen")


def _plot_key_metrics(axis, assignment, users, slot_list) -> None:
    m = evaluate(assignment, users, slot_list)
    values = [
        m["covered"] / m["total_users"] * 100,
        m["demand_met"] / m["total_users"] * 100,
        m["utilisation"] * 100,
        m["top2"] * 100,
    ]
    labels = ["versorgt", "Bedarf gedeckt", "Auslastung", "Top-2-Wunsch"]
    bars = axis.bar(labels, values, color=[GREEN, BLUE, ORANGE, GREY])
    axis.set_title("Kennzahlen")
    axis.set_ylabel("in %")
    axis.set_ylim(0, 112)
    for bar, value in zip(bars, values):
        axis.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1.5, f"{value:.0f}", ha="center")


def _plot_problem_categories(axis, assignment, users, slot_list) -> None:
    e = text_evaluation(assignment, users, slot_list)
    labels = ["ohne\nLadeslot", "ohne guten\nSlot", "ohne Top-2\nWunsch", "unter\nBedarf", "ueber\nBedarf"]
    values = [
        len(e["without_slot"]),
        len(e["without_good"]),
        len(e["without_top"]),
        len(e["under"]),
        len(e["over"]),
    ]
    bars = axis.bar(labels, values, color=[RED, ORANGE, GREY, BLUE, GREEN])
    axis.set_title("Problemfaelle (kleiner ist besser)")
    axis.set_ylabel("Anzahl Nutzer")
    axis.set_ylim(0, max(values + [1]) * 1.25 + 1)
    for bar, value in zip(bars, values):
        axis.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5, str(value), ha="center")


def _bars(axis, positions, values, labels, title, ylabel, color, ylim, value_format) -> None:
    bars = axis.bar(positions, values, color=color)
    axis.set_xticks(positions, labels, rotation=15, ha="right")
    axis.set_title(title)
    axis.set_ylabel(ylabel)
    axis.set_ylim(*ylim)
    for bar, value in zip(bars, values):
        axis.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + (ylim[1] - ylim[0]) * 0.02,
            value_format.format(value),
            ha="center",
        )


def _grouped_bars(axis, positions, labels, series, title, ylabel, total=None) -> None:
    width = 0.8 / len(series)
    for offset, (label, values, color) in enumerate(series):
        bars = axis.bar(positions + offset * width - 0.4 + width / 2, values, width, label=label, color=color)
        for bar, value in zip(bars, values):
            axis.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.4, str(value), ha="center", fontsize=8)
    axis.set_xticks(positions, labels, rotation=15, ha="right")
    axis.set_title(title)
    axis.set_ylabel(ylabel)
    axis.legend()
    if total:
        axis.set_ylim(0, total * 1.25)


def _short(name: str) -> str:
    mapping = {
        "First Come, First Served": "FCFS",
        "Losverfahren (pro Slot)": "Lotterie",
        "Rang-Optimierung": "Rang-Opt.",
        "Lexikografisch + Zufalls-Tie-Break": "Lexikografisch",
    }
    return mapping.get(name, name)


def _slug(name: str) -> str:
    slug = name.lower()
    for source, target in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        slug = slug.replace(source, target)
    slug = re.sub(r"[^a-z0-9]+", "_", slug).strip("_")
    return slug

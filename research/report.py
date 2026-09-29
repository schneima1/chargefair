from __future__ import annotations

import csv
from pathlib import Path


def evaluate(assignment, users, slot_list) -> dict:
    total_capacity = sum(slot.capacity for slot in slot_list)
    counts = {user.index: len(assignment.slots[user.index]) for user in users}
    assigned = sum(counts.values())
    covered = sum(1 for count in counts.values() if count >= 1)
    covered2 = sum(1 for count in counts.values() if count >= 2)
    covered3 = sum(1 for count in counts.values() if count >= 3)
    pairs = [
        (user, slot_index)
        for user in users
        for slot_index in assignment.slots[user.index]
    ]
    mean_utility = (
        sum(user.utility[slot] for user, slot in pairs) / len(pairs) if pairs else 0.0
    )
    mean_rank = (
        sum(user.rank[slot] for user, slot in pairs) / len(pairs) if pairs else 0.0
    )
    top1 = (
        sum(1 for user, slot in pairs if user.rank[slot] == 1) / len(pairs) if pairs else 0.0
    )
    top2 = (
        sum(1 for user, slot in pairs if user.rank[slot] <= 2) / len(pairs) if pairs else 0.0
    )
    worst_rank_sum = max(
        (sum(user.rank[slot] ** 2 for slot in assignment.slots[user.index]) for user in users),
        default=0,
    )
    demand_met = sum(1 for user in users if counts[user.index] == user.desired_slots)
    under = sum(1 for user in users if 0 < counts[user.index] < user.desired_slots)
    over = sum(1 for user in users if counts[user.index] > user.desired_slots)
    return {
        "total_users": len(users),
        "total_capacity": total_capacity,
        "assigned": assigned,
        "covered": covered,
        "covered2": covered2,
        "covered3": covered3,
        "demand_met": demand_met,
        "under": under,
        "over": over,
        "unassigned": [user.name for user in users if counts[user.index] == 0],
        "utilisation": assigned / total_capacity if total_capacity else 0.0,
        "mean_utility": mean_utility,
        "mean_rank": mean_rank,
        "top1": top1,
        "top2": top2,
        "worst_rank_sum": worst_rank_sum,
        "mean_slots": assigned / covered if covered else 0.0,
    }


def print_overview(slot_list, users) -> None:
    total = sum(slot.capacity for slot in slot_list)
    print("=" * 78)
    print("LADEPLATZVERGABE - WOCHENPLAN")
    print("=" * 78)
    print(
        f"Ladeplaetze: {slot_list[0].capacity}   Slots/Woche: {len(slot_list)}   "
        f"Kapazitaet/Woche: {total} Ladevorgaenge"
    )
    print(f"Mitarbeiter: {len(users)}   Slots je Person: 1 bis 3")
    demand = sum(user.desired_slots for user in users)
    print(f"Gewuenschte Ladevorgaenge (Energiebedarf): {demand}")
    print()
    print(f"{'Slot':<22}{'Bewerber':>9}{'Plätze':>8}{'Nachfrage':>11}")
    print("-" * 50)
    for slot in slot_list:
        applicants = sum(1 for user in users if slot.index in user.available)
        print(
            f"{slot.label:<22}{applicants:>9}{slot.capacity:>8}"
            f"{applicants / slot.capacity:>10.2f}x"
        )
    print()


def print_comparison(assignments, users, slot_list) -> None:
    print("=" * 78)
    print("VERFAHRENSVERGLEICH")
    print("=" * 78)
    metrics = [(assignment.name, evaluate(assignment, users, slot_list)) for assignment in assignments]
    width = max(len(name) for name, _ in metrics) + 2
    label_width = 34
    header = f"{'Kennzahl':<{label_width}}" + "".join(
        f"{name:>{width}}" for name, _ in metrics
    )
    print(header)
    print("-" * len(header))
    rows = [
        ("Nutzer mit mind. 1 Slot", lambda m: f"{m['covered']}/{m['total_users']}"),
        ("Bedarf voll gedeckt", lambda m: f"{m['demand_met']}/{m['total_users']}"),
        ("Nutzer unter Bedarf", lambda m: str(m["under"])),
        ("Nutzer ueber Bedarf", lambda m: str(m["over"])),
        ("Nutzer ohne Slot", lambda m: str(len(m["unassigned"]))),
        ("Belegte Ladevorgaenge", lambda m: f"{m['assigned']}/{m['total_capacity']}"),
        ("Auslastung", lambda m: f"{m['utilisation'] * 100:.1f} %"),
        ("Ø Slots je versorgtem Nutzer", lambda m: f"{m['mean_slots']:.2f}"),
        ("Ø Nutzen (1-5)", lambda m: f"{m['mean_utility']:.2f}"),
        ("Ø Rang", lambda m: f"{m['mean_rank']:.2f}"),
        ("Top-Wunsch getroffen", lambda m: f"{m['top1'] * 100:.1f} %"),
        ("Top-2-Wunsch getroffen", lambda m: f"{m['top2'] * 100:.1f} %"),
        ("Schlechteste Rangsumme^2", lambda m: str(m["worst_rank_sum"])),
    ]
    for label, formatter in rows:
        print(f"{label:<{label_width}}" + "".join(f"{formatter(m):>{width}}" for _, m in metrics))
    print()
    for assignment in assignments:
        print(f"  {assignment.name}: {_method_description(assignment.name)}")
        if assignment.info.get("wall_time"):
            print(f"      Solverzeit: {assignment.info['wall_time']:.2f} s")
    print()
    unassigned = metrics[-1][1]["unassigned"]
    if unassigned:
        names = ", ".join(unassigned)
        print(f"Ohne Slot (letztes Verfahren): {names}")
        print()


def print_detail(assignment, users, slot_list) -> None:
    labels = {slot.index: slot.label for slot in slot_list}
    print("=" * 78)
    print(f"DETAILPLAN - {assignment.name}")
    print("=" * 78)
    print(
        f"{'Mitarbeiter':<22}{'Fahrzeug':<26}{'Muster':<22}{'Bed.':>5}  Slots"
    )
    print("-" * 90)
    for user in users:
        slots = ", ".join(
            f"{labels[slot]} ({user.utility[slot]}*)" for slot in assignment.slots[user.index]
        )
        slots = slots if slots else "-"
        print(
            f"{user.name:<22}{user.car:<26}{user.pattern:<22}"
            f"{user.desired_slots:>5}  {slots}"
        )
    print()


def write_csv(assignments, users, slot_list, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    labels = {slot.index: slot.label for slot in slot_list}
    with (out_dir / "assignments.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["Verfahren", "Mitarbeiter", "Fahrzeug", "Slot", "Sterne", "Rang"]
        )
        for assignment in assignments:
            for user in users:
                for slot in assignment.slots[user.index]:
                    writer.writerow(
                        [
                            assignment.name,
                            user.name,
                            user.car,
                            labels[slot],
                            user.utility[slot],
                            user.rank[slot],
                        ]
                    )
    with (out_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "Verfahren",
                "versorgt",
                "mind2",
                "mind3",
                "belegt",
                "kapazitaet",
                "auslastung",
                "oe_nutzen",
                "oe_rang",
                "top1_quote",
                "top2_quote",
            ]
        )
        for assignment in assignments:
            m = evaluate(assignment, users, slot_list)
            writer.writerow(
                [
                    assignment.name,
                    m["covered"],
                    m["covered2"],
                    m["covered3"],
                    m["assigned"],
                    m["total_capacity"],
                    f"{m['utilisation']:.4f}",
                    f"{m['mean_utility']:.4f}",
                    f"{m['mean_rank']:.4f}",
                    f"{m['top1']:.4f}",
                    f"{m['top2']:.4f}",
                ]
            )


def text_evaluation(assignment, users, slot_list) -> dict:
    counts = {user.index: len(assignment.slots[user.index]) for user in users}
    without_slot = [user.name for user in users if counts[user.index] == 0]
    without_good = [
        user.name
        for user in users
        if not any(user.utility[slot] >= 3 for slot in assignment.slots[user.index])
    ]
    without_top = [
        user.name
        for user in users
        if not any(user.rank[slot] <= 2 for slot in assignment.slots[user.index])
    ]
    under = [
        user.name
        for user in users
        if 0 < counts[user.index] < user.desired_slots
    ]
    over = [user.name for user in users if counts[user.index] > user.desired_slots]
    exact = [user.name for user in users if counts[user.index] == user.desired_slots]
    return {
        "without_slot": without_slot,
        "without_good": without_good,
        "without_top": without_top,
        "under": under,
        "over": over,
        "exact": exact,
    }


def evaluation_lines(assignments, users, slot_list) -> list:
    lines = ["", "=" * 78, "KURZAUSWERTUNG JE VERFAHREN", "=" * 78]
    for assignment in assignments:
        m = evaluate(assignment, users, slot_list)
        e = text_evaluation(assignment, users, slot_list)
        lines.append("")
        lines.append(assignment.name)
        lines.append(
            f"  Versorgt: {m['covered']}/{m['total_users']} "
            f"({m['covered'] / m['total_users'] * 100:.0f} %)"
            f" | Auslastung: {m['utilisation'] * 100:.0f} %"
            f" | Oe Nutzen: {m['mean_utility']:.2f}/5"
            f" | Oe Rang: {m['mean_rank']:.1f}"
        )
        lines.append(_names_line("  Ohne Ladeslot", e["without_slot"]))
        lines.append(_names_line("  Ohne geeigneten Slot (>=3 Sterne)", e["without_good"]))
        lines.append(_names_line("  Ohne Top-2-Wunsch", e["without_top"]))
        lines.append(
            f"  Bedarf exakt gedeckt: {len(e['exact'])}/{m['total_users']}"
            f" | unterversorgt: {len(e['under'])} | ueberversorgt: {len(e['over'])}"
        )
        lines.append(f"  Zustand: {_verdict(e, m)}")
    return lines


def ranking_lines(assignments, users, slot_list):
    scored = []
    for assignment in assignments:
        m = evaluate(assignment, users, slot_list)
        key = (m["covered"], m["demand_met"], round(m["mean_utility"], 4), m["assigned"])
        scored.append((assignment.name, key, m))
    scored.sort(key=lambda item: item[1], reverse=True)
    lines = [
        "",
        "=" * 78,
        "BESTIMMUNG DER BESTEN METHODE",
        "=" * 78,
        "Jeder Nutzer gibt seinen Bedarf von 1 bis 3 Slots an und erhaelt nie mehr als diesen.",
        "Kriterien streng der Reihe nach (lexikografisch):",
        "  1. moeglichst viele Nutzer mit mindestens 1 Slot",
        "  2. danach moeglichst viele Nutzer mit vollstaendig gedecktem Bedarf",
        "  3. danach hoechster durchschnittlicher Nutzen (1-5 Sterne)",
        "  4. danach hoechste Auslastung der Ladeplaetze",
        "",
    ]
    for rank, (name, key, m) in enumerate(scored, 1):
        lines.append(
            f"  {rank}. {name:<37}"
            f" versorgt {key[0]:>2} | Bedarf gedeckt {key[1]:>2} | "
            f"Oe Nutzen {key[2]:.2f} | belegt {key[3]:>3}"
        )
    best = scored[0]
    lines.append("")
    lines.append(f"  => Beste Methode nach diesen Kriterien: {best[0]}")
    lines.append(
        f"     Begruendung: kein anderes Verfahren erreicht {best[1][0]} versorgte Nutzer "
        f"bei {best[1][1]} voll gedecktem Bedarf mit Ø Nutzen {best[1][2]:.2f}."
    )
    lines.append(
        "     Da die Obergrenze je Nutzer dem angegebenen Bedarf entspricht, kann "
        "niemand mehr Slots erhalten als benoetigt."
    )
    return lines, best[0]


def _names_line(label: str, names: list) -> str:
    if not names:
        return f"{label}: 0"
    shown = ", ".join(names[:8])
    suffix = f" ... (+{len(names) - 8} weitere)" if len(names) > 8 else ""
    return f"{label}: {len(names)} -> {shown}{suffix}"


def _verdict(evaluation: dict, metrics: dict) -> str:
    if evaluation["without_slot"]:
        return "kritisch - Nutzer bleiben voellig ohne Ladeslot"
    if evaluation["without_good"]:
        return "verbesserungswuerdig - Nutzer ohne wirklich passenden Slot"
    if evaluation["under"]:
        return "akzeptabel - alle versorgt, aber teils unter dem Bedarf"
    return "gut - alle versorgt und Bedarf gedeckt"


def _method_description(name: str) -> str:
    descriptions = {
        "First Come, First Served": "Reihenfolge der Anfragen entscheidet, keine globale Optimierung",
        "Losverfahren (pro Slot)": "Unabhaengige Ziehung je Slot, keine Koordination zwischen Slots",
        "Rang-Optimierung": "Versorgung -> Bedarf decken -> danach min. Summe der quadrierten Raenge",
        "Lexikografisch + Zufalls-Tie-Break": (
            "Versorgung -> Bedarf decken -> max. Praeferenzen -> Losentscheid"
        ),
    }
    return descriptions.get(name, "")

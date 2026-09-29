from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

DAY_NAMES = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag")
WINDOWS = ((6, 9), (9, 12), (12, 15), (15, 18))
CHARGERS = 7
MAX_SLOTS_PER_USER = 3
MIN_OVERLAP_HOURS = 1.5


@dataclass(frozen=True)
class Slot:
    index: int
    day: int
    start: int
    end: int
    capacity: int = CHARGERS

    @property
    def label(self) -> str:
        return f"{DAY_NAMES[self.day]} {self.start:02d}:00-{self.end:02d}:00"


@dataclass
class User:
    index: int
    name: str
    car: str
    battery_kwh: float
    ac_kw: float
    consumption: float
    commute_km: float
    pattern: str
    chronotype: str
    weekly_km: float
    desired_slots: int
    presence: tuple
    request_time: float
    available: frozenset = frozenset()
    utility: dict = field(default_factory=dict)
    rank: dict = field(default_factory=dict)

    @property
    def available_slots(self) -> list:
        return sorted(self.available)


CARS = (
    ("Tesla Model 3 RWD", 60.0, 11.0),
    ("Tesla Model 3 Long Range", 75.0, 11.0),
    ("Tesla Model Y Long Range", 75.0, 11.0),
    ("Tesla Model S", 95.0, 22.0),
    ("VW ID.3 Pro", 58.0, 11.0),
    ("VW ID.3 Pro S", 77.0, 11.0),
    ("VW ID.4 Pro", 77.0, 11.0),
    ("VW ID.5 Pro", 77.0, 11.0),
    ("VW ID.7 Pro", 77.0, 11.0),
    ("VW ID. Buzz", 77.0, 11.0),
    ("Skoda Enyaq 60", 58.0, 11.0),
    ("Skoda Enyaq 85", 77.0, 11.0),
    ("Cupra Born 58", 58.0, 11.0),
    ("Cupra Born 77", 77.0, 11.0),
    ("Audi Q4 45 e-tron", 77.0, 11.0),
    ("Audi Q8 55 e-tron", 106.0, 22.0),
    ("BMW i4 eDrive40", 81.0, 11.0),
    ("BMW iX3", 74.0, 11.0),
    ("BMW iX xDrive50", 105.0, 22.0),
    ("Mercedes EQA 250+", 70.5, 11.0),
    ("Mercedes EQB 300", 66.5, 11.0),
    ("Mercedes EQE 350", 90.6, 22.0),
    ("Hyundai Ioniq 5", 72.6, 11.0),
    ("Hyundai Ioniq 6", 77.4, 11.0),
    ("Kia EV6", 77.4, 11.0),
    ("Kia Niro EV", 64.8, 11.0),
    ("Renault Megane E-Tech", 60.0, 22.0),
    ("Renault Zoe", 52.0, 22.0),
    ("Peugeot e-208", 50.0, 11.0),
    ("Peugeot e-2008", 50.0, 11.0),
    ("Opel Corsa Electric", 50.0, 11.0),
    ("Opel Mokka Electric", 50.0, 11.0),
    ("Fiat 500e", 42.0, 11.0),
    ("Mini Cooper SE", 32.6, 11.0),
    ("Ford Mustang Mach-E", 75.0, 11.0),
    ("Volvo XC40 Recharge", 78.0, 11.0),
    ("Volvo C40 Recharge", 78.0, 11.0),
    ("Polestar 2", 82.0, 11.0),
    ("Smart #1", 66.0, 22.0),
    ("BYD Atto 3", 60.5, 11.0),
    ("BYD Seal", 82.5, 11.0),
    ("NIO ET5", 75.0, 11.0),
    ("MG4 Electric", 64.0, 11.0),
    ("MG ZS EV", 72.6, 11.0),
    ("Dacia Spring", 26.8, 7.4),
    ("Mazda MX-30", 35.5, 11.0),
    ("Porsche Taycan", 89.0, 22.0),
    ("Toyota bZ4X", 71.4, 11.0),
    ("Subaru Solterra", 71.4, 11.0),
    ("Nissan Leaf e+", 59.0, 6.6),
)

FIRST_NAMES = (
    "Anna", "Lukas", "Sophie", "Maximilian", "Marie", "Felix", "Laura", "Jonas",
    "Julia", "David", "Lea", "Philipp", "Sarah", "Moritz", "Nina", "Tobias",
    "Katharina", "Sebastian", "Hannah", "Alexander", "Mia", "Paul", "Emma", "Leon",
    "Lena", "Ben", "Clara", "Finn", "Johanna", "Niklas", "Leonie", "Jan",
    "Amelie", "Fabian", "Charlotte", "Simon", "Franziska", "Christian", "Vanessa", "Martin",
    "Sabine", "Andreas", "Petra", "Stefan", "Monika", "Thomas", "Ute", "Michael",
    "Kerstin", "Jürgen", "Birgit", "Ralf", "Anja", "Dirk", "Sonja", "Markus",
    "Ulrike", "Thorsten", "Heike", "Oliver", "Meike", "Ingo", "Tanja", "Hendrik",
    "Katrin", "Sven", "Miriam", "Arne", "Bettina", "Nico", "Pia", "Jannik",
)

LAST_NAMES = (
    "Müller", "Schmidt", "Schneider", "Fischer", "Weber", "Meyer", "Wagner", "Becker",
    "Schulz", "Hoffmann", "Schäfer", "Koch", "Bauer", "Richter", "Klein", "Wolf",
    "Schröder", "Neumann", "Schwarz", "Zimmermann", "Braun", "Krüger", "Hofmann", "Hartmann",
    "Lange", "Schmitt", "Werner", "Schmitz", "Krause", "Meier", "Lehmann", "Schmid",
    "Schulze", "Maier", "Köhler", "Herrmann", "König", "Walter", "Mayer", "Huber",
    "Kaiser", "Fuchs", "Peters", "Lang", "Scholz", "Möller", "Weiß", "Jung",
    "Hahn", "Schubert", "Vogel", "Friedrich", "Keller", "Günther", "Frank", "Berger",
    "Winkler", "Roth", "Beck", "Lorenz", "Baumann", "Franke", "Albrecht", "Schuster",
    "Simon", "Ludwig", "Böhm", "Winter", "Kraus", "Martin", "Schumacher", "Krämer",
)

PATTERN_NAMES = (
    "Vollzeit Büro",
    "Vollzeit Frühdienst",
    "Vollzeit Spätdienst",
    "Hybrid (3 Bürotage)",
    "Teilzeit (3 Tage)",
    "Teilzeit (2 Tage)",
    "Außendienst flexibel",
    "Werkstudent/in",
)

CHRONO_ORDER = {
    "Frühaufsteher": (0, 1, 2, 3),
    "Standard": (1, 2, 0, 3),
    "Abendmensch": (2, 3, 1, 0),
}
DAY_FACTOR = (-0.2, 0.0, 0.0, 0.0, -0.4)
POSITION_FACTOR = (1.5, 0.5, -0.5, -1.5)


def build_slots() -> list:
    slots = []
    index = 0
    for day in range(len(DAY_NAMES)):
        for start, end in WINDOWS:
            slots.append(Slot(index=index, day=day, start=start, end=end))
            index += 1
    return slots


def generate_users(count: int, slot_list: list, rng: random.Random) -> list:
    names = _unique_names(count, rng)
    users = []
    for index in range(count):
        car, battery, ac_kw = CARS[rng.randrange(len(CARS))]
        pattern = PATTERN_NAMES[rng.randrange(len(PATTERN_NAMES))]
        chronotype = rng.choices(
            ("Frühaufsteher", "Standard", "Abendmensch"), weights=(3, 5, 2)
        )[0]
        presence = _presence(pattern, rng)
        available = _availability(presence, slot_list)
        utility = _utility(presence, available, slot_list, chronotype, rng)
        rank = _rank(utility)
        commute_km = round(rng.uniform(8.0, 65.0), 1)
        consumption = round(rng.uniform(15.0, 23.0), 1)
        days_present = sum(1 for window in presence if window is not None)
        weekly_km = round(commute_km * days_present + rng.uniform(10.0, 60.0), 1)
        desired = _desired_slots(weekly_km, consumption, ac_kw)
        request_time = round(rng.uniform(0.0, 240.0), 2)
        users.append(
            User(
                index=index,
                name=names[index],
                car=car,
                battery_kwh=battery,
                ac_kw=ac_kw,
                consumption=consumption,
                commute_km=commute_km,
                pattern=pattern,
                chronotype=chronotype,
                weekly_km=weekly_km,
                desired_slots=desired,
                presence=presence,
                request_time=request_time,
                available=available,
                utility=utility,
                rank=rank,
            )
        )
    return users


def _unique_names(count: int, rng: random.Random) -> list:
    seen = set()
    names = []
    while len(names) < count:
        name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        if name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _window(start: float, end: float, jitter: float, rng: random.Random) -> tuple:
    return (
        round(start + rng.uniform(-jitter, jitter), 2),
        round(end + rng.uniform(-jitter, jitter), 2),
    )


def _presence(pattern: str, rng: random.Random) -> tuple:
    jitter = 0.5
    days = [None] * len(DAY_NAMES)
    if pattern == "Vollzeit Büro":
        for day in range(5):
            days[day] = _window(7.75, 16.75, jitter, rng)
    elif pattern == "Vollzeit Frühdienst":
        for day in range(5):
            days[day] = _window(6.0, 14.5, jitter, rng)
    elif pattern == "Vollzeit Spätdienst":
        for day in range(5):
            days[day] = _window(9.5, 18.0, jitter, rng)
    elif pattern == "Hybrid (3 Bürotage)":
        for day in rng.sample(range(5), 3):
            days[day] = _window(8.0, 17.0, jitter, rng)
    elif pattern == "Teilzeit (3 Tage)":
        for day in rng.sample(range(5), 3):
            days[day] = _window(8.0, 14.5, jitter, rng)
    elif pattern == "Teilzeit (2 Tage)":
        for day in rng.sample(range(5), 2):
            days[day] = _window(8.0, 16.5, jitter, rng)
    elif pattern == "Außendienst flexibel":
        absent = rng.randrange(5)
        for day in range(5):
            start = rng.uniform(6.5, 10.5)
            days[day] = _window(start, start + rng.uniform(6.0, 9.0), 0.25, rng)
        days[absent] = None
    elif pattern == "Werkstudent/in":
        for day in rng.sample(range(5), rng.choice((2, 3))):
            days[day] = _window(9.0, 15.0, jitter, rng)
    return tuple(days)


def _availability(presence: tuple, slot_list: list) -> frozenset:
    available = set()
    for slot in slot_list:
        window = presence[slot.day]
        if window is None:
            continue
        overlap = min(slot.end, window[1]) - max(slot.start, window[0])
        if overlap >= MIN_OVERLAP_HOURS:
            available.add(slot.index)
    return frozenset(available)


def _utility(presence, available, slot_list, chronotype, rng) -> dict:
    order = CHRONO_ORDER[chronotype]
    utility = {}
    for slot in slot_list:
        if slot.index not in available:
            continue
        window_index = (slot.start - WINDOWS[0][0]) // 3
        position = order.index(window_index)
        score = (
            3.2
            + POSITION_FACTOR[position]
            + DAY_FACTOR[slot.day]
            + rng.uniform(-0.9, 0.9)
        )
        utility[slot.index] = int(max(1, min(5, round(score))))
    return utility


def _rank(utility: dict) -> dict:
    ordered = sorted(utility, key=lambda slot: (-utility[slot], slot))
    return {slot: position + 1 for position, slot in enumerate(ordered)}


def _desired_slots(weekly_km: float, consumption: float, ac_kw: float) -> int:
    energy = weekly_km * consumption / 100.0
    slot_energy = ac_kw * 3.0 * 0.9
    return int(min(MAX_SLOTS_PER_USER, max(1, math.ceil(energy / slot_energy))))

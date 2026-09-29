"""Mathematical documentation of the allocation methods.

Every method is described in the same formal language so the descriptions can
be compared and verified:

* **Input** - the data an allocation run works on
* **Variables** - the decisions the method makes
* **Objective** - what is maximised or minimised, in the order it is applied
* **Constraints** - what must hold for every solution
* **Properties** - what can be proven about the result

The text is rendered on ``/verfahren/<key>``; formulas use plain HTML markup so
they stay readable without MathJax or an internet connection. ``latex`` holds
the same statement for copy & paste into a paper or a slide.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .allocation import METHODS


@dataclass
class MethodMath:
    """Formal description of one allocation method."""

    key: str
    notation: list = field(default_factory=list)      # (symbol, meaning)
    variables: list = field(default_factory=list)     # (formula, explanation)
    objective: list = field(default_factory=list)     # (formula, explanation, latex)
    constraints: list = field(default_factory=list)   # (formula, explanation)
    algorithm: list = field(default_factory=list)     # ordered pseudo-code steps
    properties: list = field(default_factory=list)
    complexity: str = ""
    latex: str = ""


SETS = [
    ("U", "Menge der Antragstellenden (Nutzer) in dieser Runde"),
    ("S", "Menge der Zeitfenster (Wochentag × Ladefenster), die Kapazität haben"),
    ("T", "Menge aller Zeitfenster der Woche (auch der belegten)"),
    ("P_u", "geordnete Wunschliste von u ∈ U, bester Wunsch zuerst"),
    ("c_s", "freie Kapazität von Zeitfenster s ∈ S (Anzahl Ladepunkte)"),
    ("w_u", "gewünschte Anzahl Ladezeiten von u ∈ U (1 … max. pro Woche)"),
    ("g", "garantierte Ladezeiten je Person und Woche (Standard: 1)"),
    ("m", "Obergrenze je Person und Woche (Standard: 3)"),
    ("rank_u(s)", "Position von s in P_u; 1 = Lieblingszeit, ∞ wenn nicht gewünscht"),
    ("ℓ_u", "Obergrenze für u: ℓ_u = min(w_u + bereits zugeteilt, m)"),
]

COMMON_CONSTRAINTS = [
    ("∑_{u ∈ U} x_{u,s} ≤ c_s  für alle s ∈ S",
     "Pro Zeitfenster werden höchstens so viele Personen zugeteilt, wie Ladepunkte frei sind. "
     "Diese Nebenbedingung gilt in jedem Verfahren – sie ist die physische Grenze."),
    ("x_{u,s} = 0  für alle s ∉ P_u",
     "Niemand bekommt einen Termin, den er oder sie nicht gewünscht hat."),
    ("∑_{s ∈ S} x_{u,s} ≤ ℓ_u  für alle u ∈ U",
     "Wochenobergrenze je Person, inklusive bereits bestehender Buchungen."),
]


def _lexicographic_math() -> MethodMath:
    return MethodMath(
        key="lexicographic",
        notation=SETS,
        variables=[
            ("x_{u,s} ∈ {0,1}",
             "1, wenn Person u den Slot s erhält, sonst 0."),
            ("y^{(1)}_u ∈ {0,1}",
             "1, wenn u mindestens eine Ladezeit erhält."),
            ("y^{(2)}_u ∈ {0,1}",
             "1, wenn u mindestens zwei Ladezeiten erhält (nur falls ℓ_u ≥ 2)."),
            ("y^{(3)}_u ∈ {0,1}",
             "1, wenn u mindestens drei Ladezeiten erhält (nur falls ℓ_u ≥ 3)."),
        ],
        objective=[
            ("max  f₁ = ∑_{u ∈ U} y^{(1)}_u",
             "Stufe 1 – Grundversorgung: Die Anzahl der versorgten Personen wird maximiert. "
             "Das Realisiert die Garantie, denn niemand kann besser gestellt werden, solange "
             "eine andere Person leer ausgeht."),
            ("max  f₂ = ∑_{u ∈ U} y^{(2)}_u",
             "Stufe 2 – Zusatzwünsche: Nachdem f₁ festgeschrieben ist, wird die Anzahl der "
             "Personen mit zweiter Ladezeit maximiert. Da f₁ konstant bleibt, kann dies nur "
             "über freie Restkapazität geschehen."),
            ("max  f₃ = ∑_{u ∈ U} ∑_{s ∈ P_u} max(1, 6 − rank_u(s)) · x_{u,s}",
             "Stufe 3 – Wunschqualität: Der Lieblingswunsch zählt 5 Punkte, der zweite 4 usw. "
             "Erst nachdem Versorgung und Zusatzverteilung optimal sind, wird die Qualität "
             "verbessert."),
            ("max  f₄ = ∑_{(u,s)} r_{u,s} · x_{u,s}",
             "Stufe 4 – Zufall: r_{u,s} sind ganzzahlige Zufallszahlen aus einem Zufallsgenerator "
             "mit festem Startwert (Seed = Ordinalzahl der Woche). Sie entscheiden über "
             "gleichwertige Lösungen und verhindern eine systematische Bevorzugung."),
        ],
        constraints=COMMON_CONSTRAINTS + [
            ("y^{(1)}_u ≤ ∑_{s ∈ P_u} x_{u,s},   ∑_{s ∈ P_u} x_{u,s} ≥ 2 y^{(2)}_u,   "
             "∑_{s ∈ P_u} x_{u,s} ≥ 3 y^{(3)}_u",
             "Verknüpfung der Indikatoren mit den Zuordnungen."),
            ("y^{(2)}_u ≤ y^{(1)}_u,   y^{(3)}_u ≤ y^{(2)}_u",
             "Monotonie: Wer drei Ladezeiten hat, hat auch eine zweite und eine erste."),
        ],
        algorithm=[
            "Löse Stufe 1 (max f₁) und schreibe das Optimum f₁* als Gleichung fest: ∑ y⁽¹⁾ = f₁*.",
            "Löse Stufe 2 (max f₂), fixiere f₂*.",
            "Löse Stufe 3 (max f₃), fixiere f₃*.",
            "Löse Stufe 4 (max f₄) als Tie-Break – die vorherigen Optima bleiben garantiert erhalten.",
            "Übernimm x aus dem letzten Modell und erzeuge daraus die Buchungen.",
        ],
        properties=[
            "Die Grundversorgung ist optimal: Es gibt keine Lösung, in der mehr Personen "
            "mindestens eine Ladezeit erhalten (Maximalität von f₁).",
            "Zusatzwünsche können die Grundversorgung nicht verschlechtern, weil f₁ vor f₂ "
            "festgeschrieben wird.",
            "Gleichstand wird ausgelost, nicht nach Person entschieden (f₄).",
            "Bei festem Startwert ist das Ergebnis reproduzierbar: gleicher Seed → gleiche Zuordnung.",
        ],
        complexity=(
            "NP-schwer als Ganzes (verallgemeinertes Zuordnungsproblem). CP-SAT löst die "
            "Instanzen dieser Größenordnung (≤ 85 Personen, 20 Zeitfenster) in Millisekunden – "
            "gemessen: 20–60 ms. Es wird zusätzlich ein Zeitlimit gesetzt, damit die "
            "Antwortzeit unabhängig von der Eingabe begrenzt bleibt."
        ),
        latex=r"""\begin{align*}
\text{Gegeben:}\quad & U,\; S,\; P_u \subseteq S,\; c_s \in \mathbb{N},\; w_u \in \mathbb{N},\; g,\; m \\[2pt]
\text{Variablen:}\quad & x_{u,s},\, y^{(k)}_u \in \{0,1\} \\[4pt]
\text{(1)}\quad & \max\; f_1 = \sum_{u \in U} y^{(1)}_u \\
\text{(2)}\quad & \max\; f_2 = \sum_{u \in U} y^{(2)}_u
      \quad \text{s.t. } f_1 = f_1^{*} \\
\text{(3)}\quad & \max\; f_3 = \sum_{u \in U} \sum_{s \in P_u} \max(1,\, 6 - \mathrm{rank}_u(s))\, x_{u,s}
      \quad \text{s.t. } f_2 = f_2^{*} \\
\text{(4)}\quad & \max\; f_4 = \sum_{(u,s)} r_{u,s}\, x_{u,s}
      \quad \text{s.t. } f_3 = f_3^{*} \\[4pt]
\text{u.d.N.}\quad
 & \sum_{u \in U} x_{u,s} \le c_s && \forall s \in S \\
 & \sum_{s \in S} x_{u,s} \le \ell_u && \forall u \in U \\
 & x_{u,s} = 0 && \forall u \in U,\, s \notin P_u \\
 & y^{(1)}_u \le \textstyle\sum_{s} x_{u,s},\quad
   \sum_{s} x_{u,s} \ge 2 y^{(2)}_u,\quad
   \sum_{s} x_{u,s} \ge 3 y^{(3)}_u && \forall u \in U \\
 & y^{(2)}_u \le y^{(1)}_u,\quad y^{(3)}_u \le y^{(2)}_u && \forall u \in U
\end{align*}""",
    )


def _rank_based_math() -> MethodMath:
    return MethodMath(
        key="rank_based",
        notation=SETS,
        variables=[
            ("x_{u,s} ∈ {0,1}", "1, wenn Person u den Slot s erhält."),
            ("y^{(1)}_u, y^{(2)}_u, y^{(3)}_u ∈ {0,1}", "Versorgungsindikatoren wie beim lexikografischen Verfahren."),
        ],
        objective=[
            ("max  f₁ = ∑_{u ∈ U} y^{(1)}_u",
             "Stufe 1 – Grundversorgung wie oben."),
            ("max  f₂ = ∑_{u ∈ U} y^{(2)}_u",
             "Stufe 2 – Anzahl der Personen mit zweiter Ladezeit."),
            ("min  f₃ = ∑_{(u,s)} rank_u(s)² · x_{u,s}",
             "Stufe 3 – Quadratische Wunschkosten. Der zweite Wunsch wiegt viermal so schwer "
             "wie der erste, der dritte neunmal. Dadurch werden sehr schlechte Zuordnungen "
             "stärker vermieden als viele kleine Verbesserungen."),
        ],
        constraints=COMMON_CONSTRAINTS + [
            ("y^{(1)} ≤ ∑_s x_{u,s},   ∑_s x_{u,s} ≥ 2 y^{(2)}_u",
             "Verknüpfung der Indikatoren."),
            ("rank_u(s) wird durch |P_u| nach oben beschränkt",
             "Nicht gewünschte Slots haben unendliche Kosten und werden nie gewählt."),
        ],
        algorithm=[
            "Maximiere die Anzahl versorgter Personen (f₁) und fixiere das Optimum.",
            "Maximiere die Anzahl zweiter Ladezeiten (f₂) und fixiere das Optimum.",
            "Minimiere die Summe der quadrierten Wunschränge (f₃).",
            "Tie-Break per Zufall mit festem Startwert.",
        ],
        properties=[
            "Auch hier ist die Grundversorgung optimal (f₁ maximal).",
            "Die Quadrierung wirkt ausgleichend: Ein Rang-5-Wunsch kostet 25, zwei Rang-2-Wünsche "
            "kosten zusammen 8 – das Verfahren bevorzugt also gleichmäßige Lösungen.",
            "Kein Personenbezug: Es zählt ausschließlich die Position in der eigenen Wunschliste.",
        ],
        complexity=(
            "Wie beim lexikografischen Verfahren NP-schwer; die quadratische Zielfunktion wird "
            "von CP-SAT linearisiert (eine Binärvariable je Rangstufe). Laufzeit im Test: "
            "wenige Millisekunden."
        ),
        latex=r"""\begin{align*}
\text{(1)}\quad & \max\; f_1 = \sum_{u \in U} y^{(1)}_u \\
\text{(2)}\quad & \max\; f_2 = \sum_{u \in U} y^{(2)}_u \quad \text{s.t. } f_1 = f_1^{*} \\
\text{(3)}\quad & \min\; f_3 = \sum_{u \in U} \sum_{s \in P_u} \mathrm{rank}_u(s)^2 \, x_{u,s}
      \quad \text{s.t. } f_2 = f_2^{*} \\[4pt]
\text{u.d.N.}\quad
 & \sum_{u} x_{u,s} \le c_s && \forall s \in S \\
 & \sum_{s} x_{u,s} \le \ell_u && \forall u \in U
\end{align*}""",
    )


def _guaranteed_math() -> MethodMath:
    return MethodMath(
        key="guaranteed",
        notation=SETS + [
            ("G", "Bipartiter Graph: Knoten = Personen ∪ Zeitfenster, Kanten = Wünsche"),
            ("f", "Flusswert des Maximum-Flow-Problems"),
            ("k", "Runde der Zusatzverteilung, k = 1 … max_u ℓ_u"),
        ],
        variables=[
            ("x_{u,s} ∈ {0,1}", "Kante (u,s) wird genutzt – entspricht einer Zuordnung."),
            ("f", "Gesamtzahl zugeteilter Ladezeiten (Summe der Flüsse zur Senke)."),
        ],
        objective=[
            ("max  f",
             "Maximaler Fluss im Netzwerk. In Runde 1 ist jeder Personen-Knoten auf Kapazität "
             "1 begrenzt, also entspricht f der Anzahl versorgter Personen – das Maximum davon "
             "ist eine maximale Zuordnung (Satz von König/Ford-Fulkerson)."),
            ("max  f  (Runde k mit Kapazität k je Person)",
             "Ab Runde 2 wird die Quellkapazität jeder Person um genau 1 erhöht. Damit wächst "
             "f höchstens um 1 pro Person und Runde: Alle, die eine zweite Ladezeit möchten, "
             "werden vor jeder dritten berücksichtigt."),
        ],
        constraints=[
            ("0 ≤ x_{u,s} ≤ 1",
             "Eine Kante wird höchstens einmal genutzt."),
            ("∑_{s} x_{u,s} ≤ k   (in Runde k)",
             "Personenkapazität, rundenweise um eins erhöht."),
            ("∑_{u} x_{u,s} ≤ c_s",
             "Slotkapazität – dieselbe physische Grenze wie bei den anderen Verfahren."),
            ("Flusserhaltung: Zufluss = Abfluss an jedem inneren Knoten",
             "Standardbedingung des Max-Flow-Problems; formalisiert die Zuordnung."),
        ],
        algorithm=[
            "Baue das Netzwerk: Quelle → Person u (Kapazität 0), u → s für jedes s ∈ P_u "
            "(Kapazität 1), s → Senke (Kapazität c_s).",
            "Runde k = 1: Setze die Quellkapazität jeder Person auf 1 und berechne den "
            "maximalen Fluss (Dinic-Algorithmus). Ergebnis: maximale Zuordnung.",
            "Runde k = 2 … max ℓ_u: Erhöhe die Quellkapazität jeder Person mit ℓ_u ≥ k um 1 "
            "und berechne erneut den maximalen Fluss auf demselben Netzwerk.",
            "Verbesserungsschritt: Tausche belegte Slots gegen besser bewertete Wünsche, "
            "solange freie Kapazität vorhanden ist (Anzahl je Person bleibt gleich).",
            "Ziehe die genutzten Kanten als Zuordnung heraus.",
        ],
        properties=[
            "Runde 1 liefert ein Maximum-Matching: Die Anzahl versorgter Personen ist maximal "
            "(Satz von Berge: Ein Matching ist maximal, wenn kein augmentierender Pfad existiert).",
            "Eine Augmentierung entzieht niemandem einen Slot, ohne ihm im selben Schritt einen "
            "anderen zu geben – die Versorgung aus Runde 1 bleibt also erhalten.",
            "Das Ergebnis ist deterministisch (bis auf die Startreihenfolge, die aus dem Seed "
            "abgeleitet wird) und ohne Solver in O(|U| · |S|²) berechenbar.",
            "Keine Gewichtung nach Person: Die Reihenfolge entsteht durch Ziehen, die Fairness "
            "folgt allein aus Kapazität und Rundenzahl.",
        ],
        complexity=(
            "Dinic: O(V² · E) mit V = |U| + |S| + 2 und E = |Wünsche| + |U| + |S|. Bei "
            "85 Personen und 20 Zeitfenstern sind das wenige zehntausend Operationen – "
            "gemessen unter 10 ms, ohne Solver."
        ),
        latex=r"""\begin{align*}
\text{Netzwerk:}\quad
 & N = (V, E), \quad V = \{q\} \cup U \cup S \cup \{z\} \\
 & E = \{(q,u)\} \cup \{(u,s) : s \in P_u\} \cup \{(s,z)\} \\[2pt]
\text{Kapazitäten:}\quad
 & \mathrm{cap}(q,u) = k, \quad \mathrm{cap}(u,s) = 1, \quad \mathrm{cap}(s,z) = c_s \\[4pt]
\text{Runde } k:\quad & \max\; f_k = \sum_{(s,z) \in E} f(s,z)
   \quad \text{mit } \mathrm{cap}(q,u) = \min(k, \ell_u) \\[4pt]
\text{Ergebnis:}\quad & x_{u,s} = f(u,s) \in \{0,1\}, \quad
   f_1 = \text{maximale Zuordnung} \\[2pt]
\text{Verbesserung:}\quad
 & \text{Tausche } (u,s) \to (u,s') \text{ falls } \mathrm{rank}_u(s') < \mathrm{rank}_u(s)
   \text{ und } \sum_u x_{u,s'} < c_{s'}
\end{align*}""",
    )


def _lottery_math() -> MethodMath:
    return MethodMath(
        key="lottery",
        notation=SETS,
        variables=[
            ("x_{u,s} ∈ {0,1}", "1, wenn u in der Ziehung für s gewinnt."),
            ("σ_s", "zufällige Permutation der Bewerbermenge von s."),
        ],
        objective=[
            ("—",
             "Keine Zielfunktion. Für jedes Zeitfenster werden die Gewinner unabhängig gezogen; "
             "es wird nichts optimiert."),
        ],
        constraints=[
            ("∑_{u} x_{u,s} ≤ c_s",
             "Slotkapazität – die einzige Nebenbedingung."),
        ],
        algorithm=[
            "Für jedes Zeitfenster s: Sammle alle Personen mit s ∈ P_u und freiem Kontingent.",
            "Ziehe gleichverteilt c_s Gewinner (Fisher-Yates-Permutation, erste c_s Einträge).",
            "Wiederhole für alle Zeitfenster in zufälliger Reihenfolge.",
        ],
        properties=[
            "Keine Garantie: Die Wahrscheinlichkeit, leer auszugehen, ist für alle gleich groß, "
            "aber die Varianz über mehrere Wochen ist hoch.",
            "Wunschprioritäten werden nicht ausgewertet: Alle Wünsche sind gleich viel wert.",
            "Reproduzierbar bei festem Startwert, sonst nicht.",
            "Vorteil: maximal einfach zu erklären und zu prüfen – deshalb als Vergleichsmaßstab enthalten.",
        ],
        complexity="O(|S| · |U|) – linear, keine Optimierung.",
        latex=r"""\begin{align*}
\text{Gegeben:}\quad & c_s,\; P_u \\[2pt]
\text{Variablen:}\quad & x_{u,s} \in \{0,1\}, \quad
   \sigma_s \sim \mathrm{Unif}(\{u : s \in P_u\}) \\[4pt]
\text{Ziehung:}\quad
 & x_{u,s} = 1 \iff u \in \{\sigma_s(1), \dots, \sigma_s(c_s)\} \\[4pt]
\text{u.d.N.}\quad
 & \sum_u x_{u,s} \le c_s && \forall s \in S
\end{align*}""",
    )


def _fcfs_math() -> MethodMath:
    return MethodMath(
        key="fcfs",
        notation=SETS + [
            ("a_u", "Zeitpunkt, zu dem u den Wunsch abgegeben hat (Anmeldezeit)"),
            ("≺", "Sortierung nach a_u aufsteigend"),
        ],
        variables=[
            ("x_{u,s} ∈ {0,1}", "1, wenn u den Slot s erhält."),
            ("q_s", "Zustand der Restkapazität von s während des Durchlaufs."),
        ],
        objective=[
            ("—",
             "Keine Zielfunktion, sondern eine feste Abarbeitungsreihenfolge. Diese Methode "
             "ist bewusst nicht gerecht – sie dient als Referenz."),
        ],
        constraints=[
            ("∑_{u} x_{u,s} ≤ c_s",
             "Slotkapazität."),
            ("∑_{s} x_{u,s} ≤ ℓ_u",
             "Wochenobergrenze."),
        ],
        algorithm=[
            "Sortiere alle Personen aufsteigend nach Anmeldezeit a_u.",
            "Gehe die Personen in dieser Reihenfolge durch.",
            "Vergib an u den besten noch freien Wunsch aus P_u, solange das Kontingent ℓ_u "
            "nicht erschöpft ist.",
        ],
        properties=[
            "Wer zuerst klickt, gewinnt: Der Anmeldezeitpunkt ist das einzige Kriterium.",
            "Bevorzugt dauerhaft Personen, die sofort nach Öffnung verfügbar sind, und "
            "benachteiligt Schichtdienst, Urlaub und Homeoffice.",
            "Keine Grundversorgung, keine Fairness – deshalb nur als Vergleichsmaßstab.",
            "Vollständig deterministisch.",
        ],
        complexity="O(|U| log |U| + |U| · |S|) – Sortieren plus linearer Durchlauf.",
        latex=r"""\begin{align*}
\text{Sortierung:}\quad
 & u_1 \preceq u_2 \iff a_{u_1} \le a_{u_2} \\[4pt]
\text{Zuweisung:}\quad
 & x_{u_i, s} = 1 \iff s = \arg\min_{s' \in P_{u_i}} \mathrm{rank}_{u_i}(s')
   \text{ mit } \textstyle\sum_{u} x_{u,s'} < c_{s'} \\[4pt]
\text{u.d.N.}\quad
 & \sum_u x_{u,s} \le c_s, \quad \sum_s x_{u,s} \le \ell_u
\end{align*}""",
    )


_BUILDERS = {
    "lexicographic": _lexicographic_math,
    "rank_based": _rank_based_math,
    "guaranteed": _guaranteed_math,
    "lottery": _lottery_math,
    "fcfs": _fcfs_math,
}


def method_math(key: str) -> MethodMath | None:
    builder = _BUILDERS.get(key)
    return builder() if builder else None


def all_methods_math() -> list[MethodMath]:
    """All documented methods, default method first."""
    from .allocation import available_methods

    result = []
    for spec in available_methods():
        math_doc = method_math(spec.key)
        if math_doc:
            result.append(math_doc)
    return result


def method_with_spec(key: str):
    """Return ``(spec, math_doc)`` for one method key."""
    spec = METHODS.get(key)
    if spec is None:
        return None, None
    return spec, method_math(key)

# Vortests / Vorstudie zur Vergabe

Hier liegen die **Simulations- und Auswertungsskripte**, mit denen die
Vergabeverfahren vor der Umsetzung der Webanwendung untersucht wurden.

Diese Skripte sind **nicht Teil der laufenden Anwendung**. Sie dienen dazu,
Verfahren ohne echte Nutzungsdaten zu vergleichen – etwa für eine Präsentation
oder um eine Entscheidung zu begründen.

| Datei | Zweck |
| --- | --- |
| `data.py` | erzeugt synthetische Mitarbeitende (Fahrzeuge, Arbeitsmodelle, Anwesenheiten, Wunschzeiten) |
| `methods.py` | die Vergabeverfahren (FCFS, Los, Rang-Optimierung, lexikografisch) |
| `report.py` | Kennzahlen, Tabellen, Textauswertung |
| `visuals.py` | Grafiken (Heatmaps, Balkendiagramme) |
| `main.py` | Einstiegspunkt: alles zusammen ausführen |
| `bench_solver_80.py` | Lasttest der echten Verfahren bei Zielgröße (80 Personen) |
| `out/` | Ausgabeverzeichnis (CSV, Grafiken, `auswertung.txt`) |

## Lasttest bei Zielgröße

`bench_solver_80.py` prüft die **portierten** Verfahren aus
`chargefair/allocation.py` unter realen Bedingungen: 80 Personen, 40 Zeitfenster
(5 Tage × 8 Fenster), 3 Ladepunkte je Fenster, 6 Wünsche je Person, gewünscht sind
2 Ladezeiten. Gemessen wird Laufzeit, Speicherbedarf und die Zahl der versorgten
Personen.

```bash
# im laufenden Container (nutzt das installierte Paket)
docker compose exec -T web python - < research/bench_solver_80.py
```

Ergebnis auf dieser Installation:

| Verfahren | Laufzeit | Versorgte Personen | Verteilte Slots | Spitzen-RSS |
| --- | ---: | ---: | ---: | ---: |
| `lexicographic` | 0,43 s | **80 / 80** | 120 von 120 | 159 MiB |
| `rank_based` | 1,19 s | **80 / 80** | 120 von 120 | 174 MiB |
| `guaranteed` | 0,00 s | **80 / 80** | 120 von 120 | 174 MiB |

Die Baseline vor dem Lauf lag bei 117 MiB – der Solver benötigt also rund 40 MiB
zusätzlich. Wichtig für den Betrieb: Bei der Zielgröße von 85 Mitarbeitenden
versorgt **jedes** Verfahren alle Personen, und die Rechenzeit bleibt deutlich
unter einer Sekunde. Das Zeitlimit (`SOLVER_TIME_LIMIT`, Standard 20 s) greift in
dieser Größenordnung nie.

Für noch größere Instanzen lohnt es sich, `workers` (Standard 8) nicht zu
reduzieren: Ohne ausreichend Threads nutzt CP-SAT die LP-Relaxierung schwächer
und braucht deutlich länger – siehe `linearization_level` in
`chargefair/allocation.py`.

## Ausführen

```bash
pip install -r ../requirements-dev.txt   # Flask-App + matplotlib
cd research
python main.py --users 70 --seed 42
```

Optionen:

```bash
python main.py --help
python main.py --users 85 --seed 7 --time-limit 60
python main.py --no-figures           # ohne Grafiken
```

Ergebnisse landen in `out/`.

> **Hinweis:** Die Verfahren wurden für die Anwendung nach
> `chargefair/allocation.py` portiert und dort auf echte Buchungswünsche
> umgestellt. Die Webanwendung nutzt **ausschließlich** die Portierung; die
> Skripte hier arbeiten mit synthetischen Daten und dienen dem Vergleich.

## Was die Vorstudie gezeigt hat

Die Skripte haben den Vergleich der Verfahren ermöglicht, der in der
Anwendung zur Auswahl steht:

- **First Come, First Served** bevorzugt dauerhaft schnelle Personen und
  bestraft Schichtdienst, Urlaub und Homeoffice.
- **Losverfahren** garantiert keine Mindestversorgung.
- **Rang-Optimierung** verteilt die Wunschränge ausgewogen, kennt aber keine
  Grundversorgung.
- **Lexikografisch** stellt zuerst die Versorgung aller sicher und optimiert
  erst danach die Wunscherfüllung – deshalb ist es das Standardverfahren.

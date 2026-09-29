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
| `out/` | Ausgabeverzeichnis (CSV, Grafiken, `auswertung.txt`) |

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

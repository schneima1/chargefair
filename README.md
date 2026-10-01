# ⚡ ChargeFair

**Faire Ladeplatzvergabe für Unternehmen mit begrenzter Ladeinfrastruktur.**

Mitarbeitende können Ladezeiten anfragen, regelmäßige Termine hinterlegen, einzelne
Termine freigeben, Plätze tauschen und frei werdende Zeiten kurzfristig übernehmen.
Ein Regelwerk mit garantierter Grundversorgung sorgt dafür, dass Ladegelegenheiten
langfristig möglichst gleichmäßig verteilt werden – statt „wer zuerst klickt,
gewinnt“.

---

## Inhalt

- [Kernidee](#kernidee)
- [Funktionsumfang](#funktionsumfang)
- [Vergabeverfahren](#vergabeverfahren)
- [Vergabephasen](#vergabephasen)
- [Benachrichtigungen](#benachrichtigungen)
- [Belegung und Kontakt](#belegung-und-kontakt)
- [Feedback und Board](#feedback-und-board)
- [Webanwendung starten](#webanwendung-starten)
- [Konfiguration](#konfiguration)
- [Zeitraster und Infrastruktur](#zeitraster-und-infrastruktur)
- [Tests](#tests)
- [Projektstruktur](#projektstruktur)
- [Sicherheit](#sicherheit)
- [DSGVO und Datenschutz](#dsgvo-und-datenschutz)
- [Betriebshinweise](#betriebshinweise)
- [Vortests](#vortests)

---

## Kernidee

Bei 8 Ladepunkten, 4 Zeitfenstern pro Werktag und 5 Arbeitstagen entstehen
**160 Ladegelegenheiten pro Woche**. Bei 85 Fahrzeugen sind das rechnerisch rund
1,9 Ladezeiten pro Person und Woche.

Das eigentliche Problem ist also nicht die Kapazität, sondern die **Verteilung**.
Deshalb arbeitet ChargeFair nicht mit einer Buchungsseite, die um 08:00 Uhr geöffnet
wird, sondern mit **Vergaberunden**:

1. Alle geben bis zu einer Frist ihre Wunschzeiten mit Priorität ab.
2. Der Zeitpunkt der Abgabe spielt keine Rolle.
3. Das System verteilt die Plätze nach den Regeln des gewählten Verfahrens.
4. Alle Beteiligten erhalten eine E-Mail mit dem Ergebnis.

Drei Ebenen sichern die Verteilung:

| Ebene | Regel |
| --- | --- |
| 1 – Grundversorgung | Jede Person erhält ihre garantierte Ladezeit (Standard: 1 pro Woche). |
| 2 – Zusatzwünsche | Weitere Ladezeiten, sobald alle versorgt sind – erst die zweite für alle, dann die dritte. |
| 3 – Restkapazität | Frei gebliebene Plätze können zusätzlich direkt gebucht werden. |

> **Es gibt kein Punktesystem.** Niemand wird bevorzugt, weil er oder sie länger
> gewartet hat oder in der Vergangenheit seltener laden konnte. Über knappe Plätze
> entscheidet innerhalb der Regeln der **Zufall** – mit dem Startwert der Woche
> gezogen und damit wiederholbar.

---

## Funktionsumfang

**Mitarbeitende**

- Anmeldung mit Firmen-E-Mail, E-Mail-Bestätigung, Passwort-Reset
- Dashboard mit nächstem Ladeplatz und Wochenübersicht
- Wochenplan mit Farbcodierung (eigene Buchung / frei / freigegeben / belegt)
- Wunschabgabe mit bis zu fünf Prioritäten pro Vergabephase
- Angabe, **wie oft pro Woche** geladen werden soll (z. B. 3×) – der garantierte
  Platz bleibt unabhängig davon erhalten
- Direktbuchung freier Zeitfenster (kurzfristig und nach abgeschlossener Vergabe)
- Regelmäßige Ladezeiten: jede Woche, gerade oder ungerade Kalenderwochen, mit Enddatum
- Freigeben einzelner Termine (Urlaub, Homeoffice)
- **Belegungsübersicht** mit Fahrzeug und Modell aller Kolleginnen und Kollegen
- **Interesse an einem belegten Slot** hinterlegen – Benachrichtigung per E-Mail
- **Kolleginnen und Kollegen direkt anschreiben**, ohne dass E-Mail-Adressen
  ausgetauscht werden
- Warteliste mit automatischem Angebot und Frist
- Tauschen von Ladezeiten
- **Board** für kurzfristige Absprachen und Themen abseits des Ladens
- **Feedback-Formular** für Verbesserungsvorschläge (auch anonym), mit Status
  und Rückmeldung der Administration
- Erinnerungen vor Beginn und Ende der Ladezeit – mit **Ein-Klick-Link zum Freigeben**
- Nachträgliche Bestätigung „Ich habe geladen“ (Transparenz statt Sanktion)

**Administration**

- Vergabe-, Buchungs- und Nutzungskennzahlen
- **Auswahl des Vergabeverfahrens** – global, pro Phase und pro Woche
- **Mathematische Beschreibung** jedes Verfahrens mit Variablen, Zielfunktionen,
  Nebenbedingungen und LaTeX-Vorlage
- Verfahrensvergleich: alle Verfahren auf dieselben Wünsche, ohne das echte
  Ergebnis zu verändern
- **Vergabephasen** über mehrere Wochen (Standard: 4) anlegen, einladen,
  erinnern und in einem Schritt verteilen
- Benutzerverwaltung, Rollen, Deaktivierung, Anonymisierung
- Ladeinfrastruktur verwalten (Ladesäulen, Ladepunkte, Stellplätze)
- Verteilungsanalyse mit Verteilungsklassen und Balkendiagramm
- Slot-Interesse einsehen, Feedback beantworten, E-Mail-Queue verwalten
- Zeitraster und Regeln konfigurieren
- Registrierungscodes erzeugen und deaktivieren
- Audit-Log aller administrativen Aktionen

---

## Vergabeverfahren

Alle Verfahren sind in `chargefair/allocation.py` implementiert und über die
Administration auswählbar. **Standard ist das lexikografische Verfahren.**

| Schlüssel | Bezeichnung | Prinzip |
| --- | --- | --- |
| `lexicographic` | **Lexikografisch (empfohlen)** | Garantie → Versorgung → Zusatzwünsche → Wunschqualität → Zufall |
| `rank_based` | Rang-Optimierung | Garantie/Versorgung → Minimierung der quadrierten Wunschränge |
| `guaranteed` | Garantieverfahren (sofort) | Max-Flow-Zuordnung ohne Solver, in Runden: erst 2. Slots für alle, dann 3. |
| `lottery` | Losverfahren | Pro Zeitfenster werden Gewinner zufällig gezogen |
| `fcfs` | First Come, First Served | Reihenfolge des Eingangs – bewusst als Vergleichsmaßstab enthalten |

### Der Vergabe-Vertrag: Anspruch und Zusatzwünsche

Alle Verfahren – außer den beiden bewusst naiven Vergleichsverfahren `lottery`
und `fcfs` – halten dieselbe Zusage ein:

1. **Garantie.** Jede Person erhält ihre garantierte Ladezeit (Standard: 1 pro
   Woche), solange die Kapazität reicht. Niemand bekommt eine zweite Ladezeit,
   solange eine andere Person noch keine hat.
2. **Zusatzwünsche.** Erst wenn alle versorgt sind, werden verbleibende Plätze
   an die verteilt, die mehr möchten – in Runden: erst die zweite Ladezeit für
   alle, die eine möchten, dann die dritte. Die Vergabe ist stufenweise
   umgesetzt (CP-SAT-Zielebene bzw. Aufstocken der Flow-Kapazität um eins pro
   Runde), damit sich Zusatzwünsche nie zu Lasten der Grundversorgung
   auswirken.
3. **Gleichstand.** Wenn zwei Personen mit gleichwertigen Wünschen um denselben
   knappen Platz konkurrieren, entscheidet der Zufall. Es gibt keine Bevorzugung
   nach Wartezeit, früheren Ladezeiten oder sonstigen persönlichen Merkmalen.

Die Solver-Verfahren nutzen **OR-Tools CP-SAT** und arbeiten lexikografisch: Jedes
Ziel wird optimiert und anschließend festgeschrieben, bevor das nächste Ziel
optimiert wird. Ein Zufalls-Tie-Break mit festem Startwert entscheidet über
gleichwertige Lösungen – das verhindert, dass niedrige Nutzer-IDs oder andere
persönliche Merkmale systematisch bevorzugt werden.

Für die Reproduzierbarkeit nutzt der Solver `max_deterministic_time` (gleiches
Ergebnis auf jeder Maschine) und `linearization_level = 2`. Ohne die volle
LP-Relaxierung verbrauchen Läufe mit wenigen Threads auf diesen Modellen
hunderttausende Konflikte statt sie in Millisekunden zu lösen. Liefert der Solver
trotzdem kein Ergebnis, greift automatisch das deterministische Garantieverfahren –
die Vergabe schlägt nie fehl.

Alle Verfahren wurden aus den Vortest-Skripten in `research/` übernommen und für
den Web-Betrieb auf echte Wünsche statt synthetischer Verfügbarkeiten umgestellt.
`tests/test_allocation.py` prüft unter anderem, dass Kapazitäten und Wochenlimits
nie überschritten werden, dass niemand einen zweiten Slot erhält, solange jemand
leer ausgeht, dass Zusatzwünsche erst nach der Grundversorgung verteilt werden,
dass der Zeitpunkt der Wunschabgabe keinen Einfluss hat und dass die optimierenden
Verfahren nie schlechter versorgen als FCFS.

### Mathematische Beschreibung

Jedes Verfahren ist unter **`/verfahren/<key>`** formal dokumentiert – einheitlich
aufgebaut mit Eingangsgrößen, Entscheidungsvariablen, Zielfunktionen in der
Reihenfolge ihrer Anwendung, Nebenbedingungen, Ablauf, Eigenschaften und einer
LaTeX-Vorlage zum Kopieren. Übersicht: **`/verfahren`**.

Die harten Nebenbedingungen sind für alle Verfahren identisch:

$$\sum_{u \in U} x_{u,s} \leq c_s \quad \forall s \in S
\qquad\qquad
\sum_{s \in S} x_{u,s} \leq \ell_u \quad \forall u \in U$$

$c_s$ ist die freie Kapazität eines Zeitfensters (Anzahl Ladepunkte), $\ell_u$ die
Wochenobergrenze einer Person. Die Verfahren unterscheiden sich nur in der
Zielfunktion bzw. der Abarbeitungsreihenfolge.

---

## Vergabephasen

Der Regelbetrieb läuft über **Phasen** statt über einzelne Wochen: Eine Phase
umfasst standardmäßig **vier Wochen**. Mitarbeitende geben ihre Wünsche einmal für
den gesamten Zeitraum ab, die Vergabe läuft anschließend automatisch für jede
Woche – bei einer Phase entstehen also vier Allocation-Runs.

| Schritt | Aktion im System |
| --- | --- |
| 1 | Administration legt unter *Vergaberunden* eine Phase an (Startwoche, Wochen, Verfahren, Frist) |
| 2 | „Einladungen versenden“ informiert alle aktiven Mitarbeitenden per E-Mail mit Frist und Kontingent |
| 3 | Mitarbeitende tragen Wünsche je Woche ein (oder nutzen die Wunschabgabe für einzelne Wochen) |
| 4 | Automatische Erinnerung `phase.reminder_days` Tage vor Fristablauf – wer noch nichts eingetragen hat, wird gesondert erinnert |
| 5 | „Vergabe für alle Wochen starten“ verteilt jede Woche einzeln und verschickt die Ergebnismails |
| 6 | Optional: einzelne Wochen erneut verteilen (das ersetzt die Ergebnisse der jeweiligen Woche) |

Warum pro Woche einzeln? Kapazität, Anwesenheit und Wünsche unterscheiden sich je
Woche. Eine gemeinsame Optimierung über vier Wochen könnte Plätze vergeben, die in
einer bestimmten Woche gar nicht frei sind – die garantierte Ladezeit gilt deshalb
bewusst **pro Woche**, genau wie es den Mitarbeitenden beschrieben wird.

Einzelne Runden lassen sich weiterhin anlegen (Sonderfälle, kurzfristige
Sonderregelungen).

---

## Benachrichtigungen

Alle Nachrichten laufen über die **E-Mail-Queue**: Der Web-Request legt die
Nachricht nur ab, der Worker versendet sie. Der Webserver wartet nie auf SMTP.

| Anlass | Empfänger | Besonderheit |
| --- | --- | --- |
| Registrierung, Passwort-Reset | Person | Token-Links mit Ablaufzeit |
| Phaseneröffnung | alle Aktiven | Zeitraum, Frist, Kontingent, „wie oft laden“ |
| Frist-Erinnerung | alle Aktiven | getrennter Text, je nachdem ob schon Wünsche vorliegen |
| Vergabeergebnis | Teilnehmende | zugeteilte Slots, nicht erfüllte Wünsche, Erklärung Anspruch/Zusatz |
| Vergabe-Digest | Administration | Kennzahlen der Runde bzw. Phase |
| Freigabe bestätigt | Person | nennt die Anzahl der informierten Interessenten |
| **Slot frei geworden** | alle Interessierten | **Ein-Klick-Link zur Übernahme** |
| Übernahme | beide Personen | Bestätigung und Information |
| Erinnerung vor Ladebeginn | Person | **Ein-Klick-Link zum Freigeben**, falls etwas dazwischenkommt |
| Erinnerung vor Ladeende | Person | Hinweis zum Umparken |
| Nicht genutzte Ladezeiten | Person | freundlicher Hinweis, keine Sanktion |
| Kontaktnachricht | Empfängerin/Empfänger | Absenderadresse wird bewusst nicht mitgesendet |
| Neuer Verbesserungsvorschlag | Administration | Titel und Text |
| Rückmeldung zum Vorschlag | Verfasserin/Verfasser | Antwort der Administration |

### Ein-Klick-Links

Die Links in Erinnerungs- und Interessensmails funktionieren **ohne Anmeldung**.
Sie sind mit `SECRET_KEY` signiert (`itsdangerous`) und laufen ab:

| Link | Aktion | Gültigkeit |
| --- | --- | --- |
| `/freigeben/<token>` | eigene Ladezeit freigeben | 12 Stunden |
| `/slot/<token>` | frei gewordenen Slot übernehmen | 6 Stunden |

Der Token enthält nur Aktion, Buchungs-ID und Nutzer-ID – es wird nichts in der
Datenbank gespeichert. Der Server prüft zusätzlich, ob die Buchung wirklich noch
der Person gehört bzw. noch frei ist. Ein `SECRET_KEY`-Wechsel entwertet alle
offenen Links, was nach einem Vorfall genau richtig ist.

---

## Belegung und Kontakt

Die Seite **`/uebersicht`** zeigt für die gewählte Woche, wer wann an welchem
Ladepunkt lädt – mit Kurzname, Fahrzeugmodell und Fahrzeugtyp (BEV/PHEV).

Bewusst **nicht** enthalten:

- **Kennzeichen** – nicht nötig für die Abstimmung, daher nicht sichtbar
- **Telefonnummer und E-Mail-Adresse** – stattdessen der Button „Nachricht“

Über `/kontakt/<id>` schreibt man einer Person über das System. Die Nachricht wird
per E-Mail zugestellt, aber **ohne Absenderadresse**; im Text steht nur der Name.
So kann jede Person selbst entscheiden, ob und wie sie antwortet. Der Nachrichten-
inhalt wird nicht gespeichert, nur der Vorgang im Audit-Log.

Wer eine belegte Ladezeit übernehmen möchte, merkt sein **Interesse** direkt in der
Zelle vor. Wird der Termin freigegeben, erhalten alle Interessierten automatisch
eine Mail mit Übernahme-Link.

---

## Feedback und Board

Zwei getrennte Orte für zwei unterschiedliche Anliegen:

| | **Board** (`/board`) | **Feedback** (`/feedback`) |
| --- | --- | --- |
| Zweck | kurzfristige Absprachen, allgemeine Themen | Verbesserungsvorschläge zum System |
| Themen | Ladeplatz, Allgemein, Frage | Verbesserungsvorschlag |
| Anonym möglich | nein | **ja** |
| Status-Workflow | offen / geschlossen | offen → in Prüfung → umgesetzt / abgelehnt |
| Antwort der Administration | Kommentar | feste Rückmeldung + E-Mail an die Verfassenden |

Beide Bereiche nutzen dieselbe Technik (Beiträge mit Kommentaren, schließen,
löschen), sind aber getrennt gelistet: Verbesserungsvorschläge tauchen nicht im
allgemeinen Board auf und umgekehrt. Bei jedem neuen Vorschlag erhält die
Administration eine Benachrichtigung; bei jeder Statusänderung die Verfasserin oder
der Verfasser.

---

## Webanwendung starten

### Variante A: Docker (empfohlen)

Voraussetzung: Docker Desktop läuft (unter Windows mit aktivierter WSL-Integration)
oder Docker Engine mit Compose v2.

```bash
cd ladeplatzvergabe
cp .env.example .env            # nur beim ersten Mal
# SECRET_KEY in .env ersetzen:
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
docker compose up -d --build
```

Danach im Browser öffnen: **<http://localhost:8000>**

Beim ersten Start werden automatisch Datenbank, Ladeinfrastruktur, Administrator,
Demo-Mitarbeitende und eine Demo-Historie angelegt. Ein zweiter Start ist
idempotent – es wird nichts doppelt erzeugt.

```bash
docker compose logs -f web     # mitlesen, zeigt auch den Registrierungscode
docker compose down            # stoppen (Daten bleiben erhalten)
docker compose down -v         # stoppen und alle Daten löschen
```

| Dienst | Aufgabe |
| --- | --- |
| `web` | Gunicorn mit der Flask-Anwendung, Port 8000 |
| `worker` | E-Mail-Queue, Erinnerungen, abgelaufene Wartelisten-Angebote, Buchungsabschluss |

Beide teilen sich das Volume `chargefair-data` (Datenbank und Ausgabe der
Test-E-Mails).

### Variante B: Lokal ohne Docker

Am einfachsten mit dem mitgelieferten Skript – es legt beim ersten Aufruf ein
virtuelles Environment an, installiert die Abhängigkeiten und erzeugt die `.env`:

```bash
cd ladeplatzvergabe
./start-local.sh
```

Danach im Browser öffnen: **<http://127.0.0.1:8000>**

Alternativ von Hand:

```bash
cd ladeplatzvergabe
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # Wert in .env eintragen

python run.py
```

`run.py` legt fehlende Tabellen und Demo-Daten selbst an und startet den
Entwicklungsserver.

> Läuft `python run.py` mit `ModuleNotFoundError` ab, ist das virtuelle Environment
> nicht aktiv oder die Abhängigkeiten fehlen – `source .venv/bin/activate` und
> `pip install -r requirements.txt` ausführen.

### Zugangsdaten

| Rolle | E-Mail | Passwort |
| --- | --- | --- |
| Administration | `admin@example.com` | `ChargeFair!2026` |
| Mitarbeitende (Demo) | z. B. `anna.mueller@example.com` | `Demo!2026` |

Der gültige **Registrierungscode** steht in der Administration unter
*Registrierungscodes* und wird beim Start protokolliert:

```bash
docker compose logs web | grep -i registrierungscode
```

> **Wichtig:** Vor einem echten Einsatz `ADMIN_PASSWORD`, `DEMO_PASSWORD`,
> `SECRET_KEY` ändern und `SEED_DEMO=0` setzen.

### Nützliche Verwaltungsbefehle

```bash
docker compose exec web flask --app chargefair.app:create_app open-round
docker compose exec web flask --app chargefair.app:create_app allocate --method rank_based
docker compose exec web flask --app chargefair.app:create_app dispatch-mail
docker compose exec web flask --app chargefair.app:create_app seed-history --weeks 12
docker compose run --rm test            # Testsuite im Container
```

Die Anwendung ist unter <http://localhost:8000> erreichbar; der Port lässt sich
über `CHARGEFAIR_PORT` in der `.env` ändern.

---

## Lokaler Betrieb ohne Docker (Details)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
export SECRET_KEY="lokal-nur-zum-testen"
flask --app chargefair.app:create_app init-db
flask --app chargefair.app:create_app run --debug --port 8000
```

Alternativ mit dem mitgelieferten Starter:

```bash
python run.py
```

---

## Konfiguration

Die Konfiguration erfolgt über Umgebungsvariablen (`.env`, `docker-compose.yml`
oder klassische Umgebungsvariablen). Die vollständige Liste steht in
`.env.example`.

| Variable | Standard | Bedeutung |
| --- | --- | --- |
| `SECRET_KEY` | – | Signaturschlüssel für Sessions (zwingend ändern) |
| `BASE_URL` | – | Basis-URL für Links in E-Mails |
| `ALLOCATION_METHOD` | `lexicographic` | Startverfahren, danach über die Administration änderbar |
| `GUARANTEED_SLOTS_PER_WEEK` | `1` | Garantierte Ladezeit je Person und Woche |
| `MAX_SLOTS_PER_WEEK` | `3` | Obergrenze je Person und Woche inkl. Zusatzwünschen |
| `SOLVER_TIME_LIMIT` | `20` | Zeitlimit je Solver-Lauf in Sekunden |
| `SOLVER_WORKERS` | `8` | Parallele Suchthreads von CP-SAT |
| `REGISTRATION_REQUIRES_TOKEN` | `1` | Registrierung nur mit Code |
| `ALLOWED_EMAIL_DOMAINS` | leer | Erlaubte Domains, z. B. `firma.de,example.com` |
| `REQUIRE_EMAIL_VERIFICATION` | `1` | E-Mail-Bestätigung erforderlich |
| `MAIL_DRY_RUN` | `1` | `1` = Mails nur in `data/mail_outbox` schreiben |
| `MAIL_BATCH_SIZE` | `50` | Nachrichten je Worker-Durchlauf |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` | leer | SMTP-Zugang |
| `SMTP_TLS` | `1` | `1` = STARTTLS (Port 587), `0` = implizites TLS (Port 465) |
| `SESSION_COOKIE_SECURE` | `0` | Bei HTTPS auf `1` setzen |
| `GUNICORN_WORKERS` / `GUNICORN_THREADS` | `2` / `4` | Webserver-Parallelität |

Zur Laufzeit änderbar (Administration → *Einstellungen*): Vergabeverfahren,
Zeitraster, Betriebstage, garantierte und maximale Ladezeiten pro Woche,
Reaktionszeit der Warteliste, Zeitraum der Verteilungsstatistik, Erinnerungen.

Phasendauer und Erinnerungsvorläufe stehen in der `.env`
(`PHASE_WEEKS`, `PHASE_REMINDER_DAYS`, `SLOT_REMINDER_MINUTES`) und werden beim
ersten Start in die Datenbank übernommen.

---

## Zeitraster und Infrastruktur

- **8 Ladepunkte**, Typ 2, in 4 Ladesäulen mit je 2 Punkten
- **4 Zeitfenster pro Werktag**: 06:00–09:00, 09:30–12:30, 13:00–16:00, 16:30–20:00
- 30 Minuten Puffer zwischen den Fenstern zum Umparken
- **14 Stellplätze**, davon 8 einem Ladepunkt fest zugeordnet
- Wochenkapazität: 8 × 4 × 5 = **160 Ladegelegenheiten**

### BEV und PHEV teilen sich die Infrastruktur

Es gibt **keine Trennung nach Fahrzeugtyp**:

- Alle 8 Ladepunkte stehen BEV und PHEV gleichermaßen zur Verfügung.
- Es gilt **ein** gemeinsames Zeitraster – keine kürzeren oder reservierten
  Zeitfenster für einzelne Fahrzeugarten.
- Die Fahrzeugart (BEV / PHEV) wird ausschließlich als Profilinformation
  gespeichert und in Übersichten angezeigt.

Das reduziert Komplexität und vermeidet eine künstliche Zwei-Klassen-Regelung.
Wird eine Differenzierung fachlich gewünscht, kann sie über die Priorisierung in
`chargefair/allocation.py` (Gewichtung je Nutzer) ergänzt werden, ohne das
Datenmodell zu ändern.

---

## E-Mail-Versand

Benachrichtigungen werden nie synchron im Web-Request versendet. Der Ablauf:

```text
Flask  ->  email_queue (SQLite)  ->  Worker  ->  SMTP
```

Der Worker versendet **einen ganzen Stapel über eine einzige SMTP-Verbindung**
statt pro Nachricht eine neue aufzubauen. Während einer Phaseneröffnung oder
Vergabe entstehen leicht mehrere hundert Mails – eine Verbindung je Mail wäre
langsam und würde vom Provider schnell gedrosselt.

Fehlgeschlagene Nachrichten werden mit exponentiellem Backoff wiederholt
(`MAIL_MAX_ATTEMPTS`, Standard 5) und bleiben danach in der Administration unter
*E-Mail-Queue* sichtbar, wo sie sich manuell erneut einreihen lassen.

Im Testmodus (`MAIL_DRY_RUN=1`) schreibt der Worker die Nachrichten als Textdateien
nach `data/mail_outbox/` – ideal zur Kontrolle von Formulierungen und Links.
Für den Echtbetrieb:

```dotenv
MAIL_DRY_RUN=0
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=...
SMTP_PASSWORD=...
```

Folgende Nachrichten werden erzeugt: Registrierungsbestätigung, Willkommen,
Passwort-Reset, Runden-Öffnung, Vergabeergebnis, Buchungsbestätigung, Freigabe,
Wartelisten-Angebot und -Ablauf, Übernahme, Tauschangebot und -annahme,
Erinnerungen (30 Minuten vor Beginn, 15 Minuten vor Ende), Hinweis bei nicht
genutzten Ladezeiten sowie ein Digest an die Administration.

---

## Tests

```bash
docker compose run --rm test
```

- `tests/test_allocation.py` – Kapazitäten, Wochenlimits, Wunschtreue,
  Determinismus bei festem Seed, Garantie unter Knappheit, Zusatzwünsche erst
  nach der Grundversorgung, Losentscheid bei Gleichstand, kein Einfluss des
  Abgabezeitpunkts, Solver-Fallback
- `tests/test_app.py` – Registrierung mit Code, Login-Sperre, Rollen, Seiten,
  Methodenwechsel im Adminbereich, vollständiger Durchlauf von der Wunschabgabe
  bis zur Buchung, Verfahrensvergleich, Anspruch vs. Zusatzwünsche
- `tests/test_features.py` – Phasen über vier Wochen inkl. Einladung und Frist-
  erinnerung, Slot-Interesse mit Benachrichtigung, Ein-Klick-Links (Freigabe,
  Übernahme, manipulierte und abgelaufene Token), Kontaktformular ohne
  Adressweitergabe, Belegungsübersicht ohne Kennzeichen, Feedback mit Status und
  Rückmeldung, Board-Themen, mathematische Beschreibungen

---

## Projektstruktur

```text
chargefair/
├── __init__.py
├── app.py                    # Application Factory, Jinja-Helfer, CLI
├── config.py                 # Konfiguration über Umgebungsvariablen
├── extensions.py             # SQLAlchemy, LoginManager, CSRF
├── models.py                 # Datenmodell
├── slots.py                  # Zeitraster und Wochenarithmetik
├── allocation.py             # Die Vergabeverfahren + Registry
├── method_docs.py            # Mathematische Beschreibung der Verfahren
├── allocation_service.py     # Phasen, Runden, Buchungen, Warteliste, Interesse, Tausch
├── services.py               # Einstellungen, Statistik, E-Mail-Queue, Audit
├── notifications.py          # Deutsche Benachrichtigungstexte
├── tokens.py                 # Signierte Ein-Klick-Links für E-Mails
├── seed.py                   # Infrastruktur, Admin, Demo-Daten
├── mail_worker.py            # eigenständiger Worker-Prozess
├── blueprints/
│   ├── auth.py               # Registrierung, Login, Profil
│   ├── main.py               # Mitarbeiterbereich
│   └── admin.py              # Administration
├── templates/                # Jinja2-Templates (deutsch)
└── static/css/app.css        # Oberflächendesign

research/                     # Vortests zur Verfahrensauswahl (nicht Teil der App)docker/entrypoint.sh          # Bootstrap und Start
docker-compose.yml            # web + worker
Dockerfile
run.py                        # lokaler Start ohne Docker
start-local.sh                # venv + Abhängigkeiten + Start in einem Schritt
tests/
data/                         # SQLite-Datenbank und Mail-Ausgabe (Volume)
```

> **Schema-Angleichung:** Beim Start gleicht `init_db()` eine bestehende SQLite-Datei
> automatisch an die Modelle an – fehlende Tabellen und Spalten werden ergänzt,
> entfernte Spalten aus einer festen Liste gelöscht. Dadurch ist kein manueller
> Migrationsschritt nötig; bestehende Daten bleiben erhalten. Nach dem Protokoll
> `[schema] angepasst: …` lohnt trotzdem ein Blick in `docker compose logs web`.

---

## Sicherheit

Umgesetzt:

- Passwörter ausschließlich als Hash (Werkzeug, PBKDF2-SHA256 mit Salt) –
  auch für die Administration nicht lesbar
- CSRF-Schutz für alle Formulare (Flask-WTF)
- Session-Cookies `HttpOnly` und `SameSite=Lax`, optional `Secure`
- Brute-Force-Schutz: Konto wird nach mehreren Fehlversuchen temporär gesperrt
- Passwort-Reset mit einmaligem Token und Ablaufzeit
- E-Mail-Verifikation mit Token und Ablaufzeit
- Kein Konten-Enumerieren bei „Passwort vergessen“
- Passwortregeln (Mindestlänge, Buchstabe, Ziffer)
- Rollen und Rechte (`user` / `admin`) mit serverseitiger Prüfung
- Parametrisierte Queries über SQLAlchemy
- Automatisches Escaping in Jinja2 gegen XSS
- Audit-Log für administrative und sicherheitsrelevante Aktionen
- Container läuft als unprivilegierter Benutzer
- Healthcheck-Endpunkt `/healthz`

Für den Produktivbetrieb zusätzlich empfohlen: HTTPS mit Reverse Proxy, `SECRET_KEY`
aus einem Secret Store, `SESSION_COOKIE_SECURE=1`, regelmäßige Sicherung des
Volumes, restriktive `ALLOWED_EMAIL_DOMAINS`.

---

## DSGVO und Datenschutz

**Datenminimierung.** Gespeichert werden Name, Firmen-E-Mail, optional
Telefonnummer und Abteilung sowie Fahrzeugdaten (Typ, Hersteller, Modell,
Kennzeichen, Ladeleistung). Nicht gespeichert werden Privatadresse, Geburtsdatum
oder Standortdaten.

**Telefonnummer ist optional.** Sie wird nur angezeigt, wenn die Person sie selbst
im Profil einträgt – transparent erklärt an der Eingabestelle.

**Löschkonzept.** Mitarbeitende können ihr Konto selbst löschen, die Administration
kann es anonymisieren:

```text
Konto deaktivieren -> zukünftige Buchungen freigeben/entfernen
                   -> personenbezogene Daten anonymisieren
                   -> anonyme Statistik bleibt erhalten
```

**Rechenschaftspflicht.** Das Audit-Log dokumentiert Vergabeentscheidungen und
administrative Eingriffe nachvollziehbar.

**Keine personenbezogenen Daten in Logs.** Das Audit-Log enthält die handelnde
Kennung, jedoch keine Passwörter und keine Daten Dritter.

Vor einem Echtbetrieb sollte das Konzept mit dem Datenschutzbeauftragten
abgestimmt werden.

---

## Betriebshinweise

**Datenbank.** SQLite genügt für diese Größenordnung deutlich. Über
`DATABASE_URL` ist ein späterer Wechsel auf PostgreSQL ohne Codeänderung möglich:

```dotenv
DATABASE_URL=postgresql+psycopg://user:passwort@db:5432/chargefair
```

**Abwesenheit eines Schedulers.** Der Worker-Prozess übernimmt alle periodischen
Aufgaben (Erinnerungen, abgelaufene Wartelisten-Angebote, Abschluss vergangener
Buchungen). Im Webserver läuft zusätzlich ein leichter Dispatcher, damit im
Entwicklungsbetrieb Nachrichten sofort ankommen.

**Skalierung.** Bei mehreren Web-Prozessen den Worker als eigenen Dienst betreiben
(im Compose-Setup bereits vorgesehen) und `MAIL_WORKER_IN_PROCESS=0` setzen.

**Backups.** Regelmäßig `data/chargefair.db` sichern – alle fachlichen Daten liegen
dort.

---

## Vortests

Im Ordner **`research/`** liegen die Simulations- und Auswertungsskripte, mit denen
die Vergabeverfahren vor der Umsetzung untersucht wurden (`data.py`, `methods.py`,
`report.py`, `visuals.py`, `main.py`). Sie sind **nicht Teil der Webanwendung** und
werden nicht in das Docker-Image kopiert.

```bash
pip install -r requirements-dev.txt
cd research
python main.py --users 70 --seed 42
```

Die Skripte erzeugen synthetische Nutzergruppen, wenden alle Vergabeverfahren an
und erstellen Kennzahlen, CSV-Exporte und Grafiken in `research/out/` – nützlich,
um Verfahren ohne echte Daten zu vergleichen oder eine Entscheidung zu belegen.

`research/bench_solver_80.py` ist ein Lasttest der portierten Verfahren bei
Zielgröße (80 Personen, 40 Zeitfenster, 3 Ladepunkte je Fenster). Gemessenes
Ergebnis: **alle 80 Personen werden versorgt**, der Solver braucht 0,43 s
(`lexicographic`) bzw. 1,19 s (`rank_based`) und rund 40 MiB zusätzlichen Speicher.

```bash
docker compose exec -T web python - < research/bench_solver_80.py
```

Die Verfahren selbst wurden nach `chargefair/allocation.py` portiert und dort auf
echte Buchungswünsche umgestellt; die Webanwendung nutzt ausschließlich diese
Portierung. Details stehen in `research/README.md`.

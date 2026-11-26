# TANSS-Triage

Transkribiert Sprachnachrichten aus TANSS-Tickets mit lokalem Whisper und
bereitet Starface-Voicemail-Tickets automatisch auf: Transkript als
Kommentar, Betreff und Beschreibung per LLM, Firma und Melder per
Rufnummernvergleich. Außerdem markiert es **potenzielle Ticket-Duplikate**
beim selben Kunden.

Version: siehe [VERSION](VERSION) - Änderungen: [CHANGELOG.md](CHANGELOG.md)

## Was passiert

Das Starface-Telefoniesystem legt für Sprachnachrichten per E-Mail
TANSS-Tickets an ("Sie haben eine Sprachnachricht von ... erhalten") mit der
Aufnahme als WAV-Anhang. TANSS-Triage läuft dauerhaft auf dem TANSS-Server
und **pollt die TANSS-Datenbank** im konfigurierbaren Intervall (Default
jede Minute) nach neuen Sprachaufnahmen: neue Mail-Anhänge
(`mails_attachments`, erfasst auch Voicemails an bestehende Tickets) und
neue Ticket-Dokumente (`bug_files`), jeweils über einen persistierten
ID-Cursor - ein Neustart holt verpasste Zeit dadurch automatisch nach.
(Die TANSS-Webhook-Regeln waren für diesen Anwendungsfall zu
eingeschränkt und wurden in 0.5.0 durch das Polling ersetzt.)

Für jedes gefundene Ticket:

1. **Alle Tickets mit Audio-Anhang** (wav, mp3, m4a, ogg, opus, flac, aac,
   wma): Anhang holen, mit faster-whisper (Default `large-v3`, CPU/int8)
   vollständig transkribieren, Transkript als internen Kommentar ins Ticket
   schreiben. Audio wird an zwei Stellen gesucht: bei den Ticket-Dokumenten
   und - der Starface-Normalfall - bei den **Anhängen der Mails** des
   Tickets. Mail-Anhänge liegen im Storage des TANSS-Servers
   (`[storage] mail_attachments_dir`) und werden direkt vom Dateisystem
   gelesen; ohne DB-Zugriff dient die API als Ausweichweg.
2. **Nur Starface-Tickets** (erkannt am Mail-Absender
   `starface@tele-x.abasoft-gmbh.de` in der Ticket-Historie) zusätzlich:
   - **Rufnummern-Zuordnung** über `POST /api/v1/phoneCalls/identify`:
     eindeutige Firma wird zugewiesen; ist die Nummer die eindeutige
     Durchwahl genau eines Ansprechpartners (und nicht die Firmenzentrale),
     wird er als Melder eingetragen. Arbeitet der Ansprechpartner in
     mehreren Firmen, passiert **nichts** (Prüfung über die Tabelle
     `mitarbeiter_firmen`, read-only via `~/.my.cnf`). Kann die Firma nicht
     ermittelt werden, passiert ebenfalls nichts.
   - **LLM-Aufbereitung** (nur wenn konfiguriert): Betreff und strukturierte
     Beschreibung aus dem Transkript; kennt das LLM die Ansprechpartnerliste
     der Firma, darf es den Melder vorschlagen. Der Betreff folgt dem Stil
     der Hotline-Tickets (Telegrammstil ohne Floskeln, Hausabkürzungen wie
     KIM, ePA, KT, HZV, Rückrufhinweis "RR" samt genannter Nummer, maximal
     100 Zeichen - die Feldlänge von `bug.ueberschrift`); weitere Kürzel
     kommen über `[llm] subject_abbreviations_extra` dazu. Eine
     Sprachnachricht ohne Inhalt bekommt den Betreff "Sprachnachricht ohne
     Inhalt" ohne LLM-Aufruf. Modell ist Claude Sonnet 5.5; die Antwort
     erzwingt die API per JSON-Schema (Structured Outputs), lehnt Claude ab,
     rechnet serverseitig ein Ersatzmodell weiter (`[llm] fallbacks`).
   - **Abteilung**: jedes Starface-Ticket landet bei der HLE, solange es
     noch keiner Abteilung zugewiesen ist (`[departments]`).
   - **Praxisausfall** (erkennt das LLM: Server weg, Praxis kann nicht
     arbeiten): Betreff "SERVERAUSFALL !! ...", Fälligkeit und Deadline
     sofort, Tag DRINGEND (`[outage]`).
   - **Überschreib-Schutz**: Betreff wird nur ersetzt, solange er noch mit
     "Sie haben eine Sprachnachricht" beginnt; die Beschreibung nur, solange
     die Starface-Boilerplate ("WARNUNG: EXTERNE NACHRICHT" / "STARFACE")
     noch darin steht. Von Kolleg*innen angepasste Tickets bleiben unberührt.

Nicht-Starface-Tickets mit Audio-Anhang bekommen nur den
Transkript-Kommentar; Betreff, Beschreibung und Zuordnung bleiben unberührt.

Doppelte Verarbeitung verhindert die SQLite-State-Datei (`state.db`), die
sich jede verarbeitete Audio-Quelle merkt - sie darf deshalb nicht
gelöscht werden, sonst würden bereits kommentierte Tickets bei einem
erneuten Anstoß noch einmal kommentiert. (Kommentar-Marker aus Versionen
bis 0.3.1 werden in der Ticket-Historie weiterhin erkannt.)

## Duplikat-Erkennung

Kunden melden denselben Vorfall oft mehrfach - als Sprachnachricht, als
Mail und als Anruf. TANSS-Triage prüft deshalb **jedes Ticket, sobald es
erstmals bei einer Kundenfirma liegt**, gegen die **offenen Tickets
desselben Kunden** (`[duplicates]`, braucht ein LLM - ohne ruht die
Erkennung).

Der Poller erkennt den Moment im selben Takt über zwei weitere Cursor:
neue Tickets, die schon bei einer Firma liegen (von Kolleg*innen direkt
beim Kunden angelegt, Tabelle `bug`), und Firmenzuweisungen aus dem
TANSS-Änderungsprotokoll (`entity_changes`, Feld `companyId` - so kommen
Mail-Tickets, die mit Firma 0 entstehen, und die von TANSS-Triage selbst
zugeordneten Starface-Tickets). Geprüft wird in drei Stufen:

0. **Ohne LLM-Aufruf**, beides gilt direkt als Duplikat:
   - Das neue Ticket **nennt die Nummer** eines offenen Tickets desselben
     Kunden, im Betreff oder im (zitierten) Text - typisch für Antworten
     und Nachfragen ("[abasoft-Ticket #257317]", "Ticketnummer 251579").
     Es zählt nur eine für sich stehende Zahl ab fünf Stellen, kein Teil
     einer längeren Ruf- oder Seriennummer.
   - **Exakt gleicher Betreff** (Leerraum und Groß-/Kleinschreibung egal) -
     typisch bei mehreren Anrufen, für die das LLM denselben Betreff
     vergibt, und bei Monitoring-Meldungen. Genannt wird nur das
     **älteste** offene Ticket mit diesem Betreff - bei einer Serie das,
     in das zusammengeführt wird -, die Begründung nennt die Größe der
     Serie ("Es ist das 5. offene Ticket mit diesem Betreff seit ...").

   Genannte Tickets stehen im Kommentar vor dem mit gleichem Betreff.
1. **Sichtung**: Das LLM bekommt das neue Ticket und die Liste der offenen
   Tickets (Betreff, Anlagedatum, Status, Beschreibungsanfang, dazu der
   Anfang der jüngsten weiteren Nachricht der Praxis) und nennt sichere
   Duplikate und unklare Kandidaten, jeweils mit Begründung.
2. **Nachprüfung** nur der unklaren: Beschreibung, Nachrichten der Praxis
   und Leistungen entscheiden - bei vielen jeweils die ersten und die
   letzten paar -, wieder mit Begründung. Vorab eingetragene Termine
   (Leistungen mit Datum in der Zukunft) kommen gesondert als "geplant"
   dazu, höchstens zwei, damit sie die letzten echten Notizen nicht
   verdrängen. Nicht mitgeschickt werden Leistungen ohne eigene Aussage:
   die automatischen Protokolle verschickter Mails ("[Automatische
   Protokollierung des Ticket-E-Mail-Versandes]" - die Mail liegt ohnehin
   am Ticket), Systemeinträge ("[System] ...") und reine Vermerke
   "Automatische Rückantwort".

**Nachrichten der Praxis** sind weitere Mails und Anrufe zum Ticket -
ungeduldige Nachfragen und wiederholte Anrufe sind typische Duplikate:

- **Eingehende Mails** am Ticket, auch Kontaktformulare. Nicht dabei: die
  eigenen Adressen (`own_senders`, Default `@abasoft.de` -
  Eingangsbestätigungen, interne Weiterleitungen), Starface-Mails (ihr
  Inhalt steckt im Transkript), unsere ausgehenden Mails und die Mail, aus
  der das Ticket entstand (sie steht als Beschreibung da). Zitierte
  Verläufe unter einer Antwort werden abgeschnitten. Das Datum kommt aus
  dem Date-Header (`mails_headers`), die Reihenfolge aus der Mail-ID.
- **Sprachnachrichten**: unsere Transkript-Kommentare (`posting`, Titel
  "Transkript: ..."), mit Datum. Andere Kommentare bleiben draußen - vor
  allem unsere eigenen Duplikat-Hinweise. Beim Zusammenführen wandern die
  Kommentare ins Zielticket mit; ruft eine Praxis mehrfach an, trägt das
  Zielticket alle Transkripte.

**Passwörter werden geschwärzt**, bevor irgendein Text an das LLM geht -
hier wie bei der Starface-Aufbereitung (`[masking]`, `masking.py`).
Tickettexte enthalten mitunter Zugangsdaten im Klartext (Postfach- und
KIM-Passwörter in Mails, PINs in Notizen). Geschwärzt wird:

- das Wort nach einem Schlüsselwort ("Passwort:", "Kennwort =", "PW:",
  "PIN", "PUK", "Zugangsdaten") - mit Doppelpunkt immer, sonst nur, wenn
  es eine Ziffer oder ein Sonderzeichen enthält ("PIN 1234" ja, "PIN
  gesperrt" nein); das Schlüsselwort darf am Ende der Zeile davor stehen;
- passwortartige Wörter: 8 bis 40 Zeichen, ein Teil zwischen den Trennern
  (`- _ . / : , @ \ ( ) [ ]`) mit Kleinbuchstaben, Ziffern und
  Großbuchstaben oder Sonderzeichen und mindestens vier Wechseln der
  Zeichenart ("Sommer2024!", "kT9mP2qX");
- Base64-Zeilen (Rauschen aus Mails).

Nie geschwärzt werden URLs, E-Mail-Adressen, IP-Adressen, Zahlen, Datum
und Telefonnummern, Pfade, Markup, Produktnamen aus einem Buchstaben- und
einem Ziffernblock ("Eternus02", "Orga930M", "Befund2PACS"), fremde
Ticketnummern ("Ticket#259570") und Mengen ("2xLenovo"); weitere Wörter
über `keep_words`. Nicht erkannt werden Wort plus Ziffern ohne
Schlüsselwort ("Sommer2024"), Passphrasen aus Kleinbuchstaben und
gesprochene Passwörter. In TANSS selbst wird nichts geändert.

Bei inhaltsarmen neuen Tickets (nur Rückrufbitte, ein Stichwort wie
"Drucker", leere Beschreibung) verlangen beide LLM-Stufen einen konkreten
Bezug - dieselbe Person oder Rückrufnummer, dasselbe Gerät, einen Verweis
auf das frühere Anliegen. Dasselbe Thema allein reicht dort nicht.

Bei Verdacht bekommt das **neue** Ticket den Tag "Prüfen! pot. Duplikat"
und einen internen Kommentar. Der Kommentartitel nennt das erste
Duplikat, der Text dessen Betreff und darunter die Begründung;
weitere Treffer (insgesamt höchstens drei) folgen nach einer Trennlinie:

```
Titel: Potenzielles Duplikat von Ticket #1234
Text:  "Titel des anderen Tickets"

       Begründung (ein bis drei Sätze vom LLM)

       ----------------------------------------------------------------------------------------------------
       Potenzielles Duplikat von Ticket #2345
       "Titel des anderen möglichen Duplikat-Tickets"

       Begründung
```

Treffer ohne LLM tragen eine feste Begründung ("Das neue Ticket nennt die
Ticketnummer #1234." bzw. "Beide Tickets tragen denselben Betreff."). Im
Log steht je Treffer eine Zeile mit der Begründung.

Was man wissen sollte:

- Jedes Ticket wird **einmal** geprüft (State-Schlüssel `dup:<Ticket>`);
  ein späterer Firmenwechsel löst keine zweite Prüfung aus.
- Tickets der eigenen Firma (`exclude_company_ids`, Default ABASTU) werden
  nicht geprüft. Wandert ein solches Ticket später zu einem Kunden, wird
  es dann geprüft.
- Trägt ein Ticket noch den generischen Starface-Betreff, **wartet die
  Prüfung auf die Transkription** und läuft direkt im Anschluss.
  "Sprachnachricht ohne Inhalt" wird nicht geprüft und nur dann als
  Vergleich herangezogen, wenn ein späterer Anruf am selben Ticket Inhalt
  hatte.
- Hat der Kunde kein weiteres offenes Ticket, gibt es keinen LLM-Aufruf.
- Scheitert eine Prüfung (LLM oder Datenbank nicht erreichbar), wird sie
  nach zehn Minuten wiederholt, insgesamt höchstens dreimal.
- **Der API-Benutzer braucht das Recht "Ticket-System: Tickets anzeigen
  (alle Abteilungen)"** - anders als bei den Voicemail-Tickets liegen die
  geprüften Tickets fast immer schon bei einer Abteilung. Ohne das Recht
  verweigert TANSS das Ticket mit HTTP 403
  (`FORBIDDEN_NO_ENTITY_ACCESS`); die Prüfung endet dann vor dem ersten
  LLM-Aufruf mit einer Warnung im Log und wird nicht wiederholt. Nach dem
  Setzen des Rechts lässt sich ein solches Ticket mit `--duplicate <ID>`
  nachprüfen.
- Beim ersten Start mit dieser Funktion werden nur die Cursor gesetzt -
  Alttickets bleiben unberührt.
- Grenzen: Wiederkehrende Automatik-Mails mit identischem Betreff gelten
  als Duplikat, solange das Vorgänger-Ticket offen ist; Zweigstellen
  eines Kunden werden nicht übergreifend verglichen.

## Einrichtung

### 1. Abhängigkeiten

Python >= 3.10. Kein systemweites ffmpeg nötig (faster-whisper dekodiert
über PyAV). Die Abhängigkeiten stehen in der `pyproject.toml`;
`pip install .` installiert sie zusammen mit dem Programm:

```bash
python3 -m venv .venv
.venv/bin/pip install .
```

Unter Windows (Entwicklung): `py -3.14 -m venv .venv` und
`.venv\Scripts\pip install -e .[dev]`.

### 2. Konfiguration

```bash
cp .env.example .env            # Zugangsdaten eintragen, chmod 600
cp config.example.toml config.toml   # Verhalten anpassen (optional)
```

- `.env`: `TANSS_BASE_URL`, `TANSS_USERNAME`, `TANSS_PASSWORD`; optional
  `ANTHROPIC_API_KEY` (Claude) bzw. `LLM_API_KEY` (lokaler Endpunkt).
- **Wichtig:** Das Programm sucht `.env`, `config.toml` und `state.db` im
  Projektstamm - das ist das Arbeitsverzeichnis beim Start (systemd setzt
  es über `WorkingDirectory`; beim manuellen Start vorher `cd` in den
  Projektordner) oder, wenn gesetzt, `TANSS_TRIAGE_HOME`.
- `config.toml`: alle Werte haben Defaults, die Vorlage dokumentiert sie.
  LLM-Provider: `anthropic` (Default, sobald `ANTHROPIC_API_KEY` gesetzt
  ist), `openai_compatible` (lokales Modell, `base_url` z. B.
  `http://localhost:11434/v1` für Ollama) oder `none` (kein Rewrite,
  Transkript und Zuordnung laufen trotzdem).
- Der TANSS-API-Benutzer braucht Rechte auf Tickets (lesen/ändern),
  Historie, Dokumente, Kommentare und Rufnummern-Identifikation, dazu
  "Ticket-System: Abteilung zuweisen", "Ticket-System: Fälligkeit
  bearbeiten" und "Ticket-System: Deadline bearbeiten" (Praxisausfall) sowie
  "Ticket-System: Tickets anzeigen (alle Abteilungen)" - sonst bleiben ihm
  Tickets verborgen, die schon einer Abteilung zugewiesen sind.
- Für den Mehrfach-Firmen-Check liest das Programm `~/.my.cnf` (dieselbe
  Datei wie der MCP-Server, User `tanssuser`, read-only). Fehlt sie, werden
  Ansprechpartner-Treffer sicherheitshalber nicht zugeordnet. Denselben
  Zugang nutzt die Duplikat-Erkennung (Tabellen `bug`, `bug_status`,
  `entity_changes`, `leistungen`); für Tag und Kommentar reichen die
  Rechte, die der API-Benutzer für die Transkript-Kommentare und den
  Tag DRINGEND schon hat.

Beim ersten Lauf lädt faster-whisper das Modell (`large-v3` ~= 3 GB) in den
Hugging-Face-Cache (`download_root` in der config.toml verlegt ihn).

### 3. Dienst (Linux, systemd)

```bash
sudo cp deploy/tanss-triage.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tanss-triage
journalctl -u tanss-triage -f
```

Ausrollen von der Windows-Entwicklung aus: `.\deploy.ps1 -Server <host>`
(kopiert Quellen nach `/home/tanss/listings/TANSS-Triage`, lässt `.env`,
`config.toml` und `state.db` auf dem Server unangetastet).

## Bedienung

```bash
python -m tanss_triage                     # Dauerbetrieb: Polling + Worker
python -m tanss_triage --ticket 250312 --dry-run   # ein Ticket testweise (schreibt nichts)
python -m tanss_triage --ticket 250312    # ein Ticket scharf nachziehen
python -m tanss_triage --duplicate 260148 --dry-run   # Duplikatprüfung testweise (schreibt nichts)
python -m tanss_triage --duplicate 260148  # Duplikatprüfung scharf, auch wenn schon geprüft
python -m tanss_triage --identify 07432994360      # Rufnummernauflösung ansehen
python -m tanss_triage --mail 356324       # Mail samt Anhängen zeigen (Diagnose)
```

**Empfohlener Ersttest** auf dem Server: ein bekanntes Voicemail-Ticket mit
`--ticket <id> --dry-run` durchspielen und das geplante Ergebnis im Log
ansehen, dann ohne `--dry-run` scharf schalten. Für die Duplikat-Erkennung
genauso: `--duplicate <id> --dry-run` mit einem Ticket, zu dem beim selben
Kunden ein offenes Geschwisterticket existiert - das Log zeigt die
Entscheidung und den Kommentartext (Details der Sichtung mit
`[logging] level = "DEBUG"`).

Downtime holt der Dienst selbst nach: die Poll-Cursor stehen in der
State-DB, nach einem Neustart wird ab dem letzten Stand weitergemacht.
Nur beim allerersten Start (ohne State-DB) greift stattdessen der
konfigurierte Rückblick (`initial_lookback_minutes`); die Cursor der
Duplikat-Erkennung werden beim ersten Lauf nur gesetzt.

## Entwicklung

```bash
.venv/bin/python -m pytest        # Tests, ohne Netz und ohne Modelle
```

Struktur: `poller.py` fragt die Datenbank nach neuen Sprachaufnahmen und
nach Tickets, die erstmals bei einer Firma liegen, und füllt eine Queue
mit Paaren (Handler, Ticket-ID); `worker.py` arbeitet sie sequenziell ab
(Whisper lastet die CPU allein aus, und die Duplikatprüfung soll den
fertigen Betreff sehen),
`processor.py` orchestriert die Audio-Verarbeitung pro Ticket,
`duplicates.py` die Duplikatprüfung, `tanss_client.py` kapselt die
REST-API (Login-Eigenheiten: Header `apiToken`, 2-Minuten-Idle-Timeout,
automatischer Re-Login), `db.py` die lesenden Datenbankabfragen,
`assigner.py` enthält die Zuordnungsregeln, `triage.py`/`llm.py` den
LLM-Schritt. Weitere Trigger docken am Poller an, weitere Aktionen als
Handler in `cli.serve`.

Versionierung: SemVer in [VERSION](VERSION), Änderungen in
[CHANGELOG.md](CHANGELOG.md) nachführen.

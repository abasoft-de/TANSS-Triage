# Changelog

Alle nennenswerten Änderungen an TANSS-Triage stehen in dieser Datei.

Das Format folgt [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
die Versionierung folgt [SemVer](https://semver.org/lang/de/) (x.y.z).

## [0.6.2] - 2026-09-22

### Hinzugefügt

- Als letzte Zeile zu jedem verarbeiteten Ticket steht der aufrufbare
  TANSS-Link im Log
  (`.../index.php?section=bug&sub=view&neueFirma=<firmenID>&bugID=<id>`,
  Format aus der Wissensbasis - `neueFirma` wechselt zugleich den
  angezeigten Kunden). Liegt die API hinter `/backend`, fällt das Präfix
  für den Oberflächen-Link weg.

### Behoben

- **Zuweisung wurde nicht gesetzt**: Der Dienst trug Firma und Auftraggeber
  ein, ließ die Ticket-Zuweisung (`bug.linkTypID`/`linkID`, in der API
  `linkTypeId`/`linkId`) aber auf 0 stehen - Kolleg*innen mussten sie von
  Hand nachziehen. Sie zeigt jetzt auf den Ansprechpartner (Typ 3), wenn
  ein Melder ermittelt wurde, sonst auf die Firma (Typ 2). Eine bereits
  gesetzte Zuweisung bleibt unangetastet. (Belegt an den Bestandsdaten:
  bei Typ 2 ist `linkID` ausnahmslos die firmenID, bei Typ 3 ausnahmslos
  ein Ansprechpartner der Ticketfirma.)

## [0.6.1] - 2026-09-21

### Behoben

- **Betreffänderung galt als Fehler, obwohl sie ankam**: Ändert das PUT
  auf `/api/v1/tickets/{id}` den Betreff, antwortet TANSS mit
  `HTTP 400 RUNTIME_EXCEPTION` - die Änderung wird aber gespeichert
  (Betreff, Text, Firma, Melder stehen im Ticket, die Historie
  protokolliert sie). Seit 0.6.0 setzt der Dienst den Betreff, damit lief
  jedes Voicemail-Ticket in einen ERROR samt Traceback, obwohl die Arbeit
  getan war (Tickets 254701 und 254708 am 21.09.2026). Nach einem
  fehlgeschlagenen Update wird das Ticket jetzt erneut gelesen: stimmen
  Betreff, Text, Firma und Melder, bleibt im Log nur die gewohnte
  Erfolgsmeldung "Ticket <id> aktualisiert (...)" - die irreführende
  Fehlermeldung wird zurückgehalten (sie steht auf DEBUG, falls sie doch
  einmal gebraucht wird). Fehlt etwas, bleibt es ein Fehler mit Traceback.
  Die Ursache liegt in TANSS - dort mit der traceId aus der Antwort zu
  klären.

## [0.6.0] - 2026-09-21

### Geändert

- **Betreff im Stil der Hotline**: Die LLM-Anweisung für den Ticketbetreff
  ist aus den bestehenden HLE-/HLT-Betreffen abgeleitet (Stichprobe: 8.890
  Betreffe aus 2026, darunter 1.227 Voicemail-Tickets, deren Starface-
  Betreff eine Kollegin ersetzt hat). Verlangt werden jetzt Telegrammstil
  ohne Floskeln und Satzpunkt (Ziel 30-90 Zeichen), die Hausabkürzungen
  (EVA, KIM, ePA, eRP, KT, HZV, PAL, RR ... - 36 % dieser Betreffe enden
  auf einen RR-Hinweis, nur 0,9 % schreiben "Rückruf" aus), höchstens drei
  Anliegen mit " + " verbunden, Zusätze mit " -> " angehängt sowie ein
  Rückrufhinweis mit der im Gespräch genannten Nummer und Uhrzeit.
- Der Nachname des Anrufers darf im Rückrufteil stehen ("... -> RR Frau
  Meier 07141 141210"), nicht am Betreffanfang - 17 % der von Hand
  vergebenen Betreffe nennen die Person, weil bei einer Sammelnummer sonst
  unklar ist, wen man zurückruft. Praxis- und Firmennamen bleiben draußen
  (die Firma hängt am Ticket), und die übermittelte Anrufernummer gehört
  ausdrücklich nicht in den Betreff - nur eine, die der Anrufer nennt.
- Sprachnachrichten ohne Inhalt (Piepton gehört, aufgelegt) bekommen ohne
  LLM-Aufruf den Betreff "Sprachnachricht ohne Inhalt" - so heißen solche
  Tickets auch von Hand.

- **Klartext statt IDs im Log**: Die Meldung nach einem Ticket-Update nennt
  jetzt die KUBEZ der Firma und den Namen des Melders ("Firma RAUMAR,
  Melder Hr. Dr. med. Sascha Orlik") statt der Datensatz-IDs; ohne
  Datenbank bleibt es bei "Firma #2249". Das gilt auch für die
  Dry-run-Ausgabe (die zusätzlich den geplanten Betreff zeigt), die Warnung
  über eine nicht ladbare Ansprechpartnerliste und die Begründung
  "<Name> arbeitet in mehreren Firmen", die im Kommentar landet.

### Hinzugefügt

- `[llm] subject_abbreviations_extra` in der config.toml: ergänzt die
  eingebaute Abkürzungsliste (ersetzt sie nicht), damit neue Kürzel ohne
  Deployment dazukommen.

### Behoben

- Ein zu langer LLM-Betreff wurde bei 200 Zeichen gekappt und ungeprüft
  geschrieben, obwohl `bug.ueberschrift` ein `varchar(100)` ist. Gekürzt
  wird jetzt auf 100 Zeichen an der Wortgrenze (mit Auslassungszeichen);
  Zeilenumbrüche im Betreff fallen weg.

## [0.5.0] - 2026-09-16

### Geändert

- **Webhook durch Polling ersetzt**: Die TANSS-Event-Regeln sind für
  diesen Anwendungsfall zu eingeschränkt (Trigger per API nicht setzbar,
  Regel feuerte nicht). Der Dienst pollt jetzt die TANSS-Datenbank im
  konfigurierbaren Intervall (`[polling] interval_seconds`, Default 60)
  nach neuen Sprachaufnahmen: neue Mail-Anhänge (`mails_attachments`) und
  neue Ticket-Dokumente (`bug_files`), jeweils über einen in der State-DB
  persistierten ID-Cursor. Ein Neustart holt verpasste Zeit dadurch
  automatisch nach; beim allerersten Start greift
  `initial_lookback_minutes` (Default 60).

### Entfernt

- Webhook-Server, `[webhook]`-Konfiguration sowie die CLI-Kommandos
  `--register-webhook` und `--list-webhooks`.

## [0.4.1] - 2026-09-15

### Geändert

- Ohne LLM (oder wenn der LLM-Lauf scheitert) wird die Ticketbeschreibung
  eines Starface-Tickets durch das reine Transkript ersetzt, solange noch
  die Starface-Boilerplate darin steht. Der Betreff bleibt ohne LLM
  unangetastet; manuell ersetzte Beschreibungen ebenfalls.

## [0.4.0] - 2026-09-15

### Behoben

- **Rufnummern-Zuordnung funktioniert jetzt real**: `POST
  /api/v1/phoneCalls/identify` antwortet - anders als dokumentiert - nicht
  mit `fromPhoneNrInfos`/`foundType`/`items`, sondern flach mit
  `fromCompanyId`/`fromEmployeeId`/`numberIdentifyState`. Die Auswertung
  versteht jetzt beide Formen. Da die flache Antwort weder
  Zentrale-vs-Durchwahl noch Eindeutigkeit hergibt, klärt das eine
  Read-only-DB-Prüfung (Ziffernvergleich gegen die Nummernfelder von
  `firmen` und `mitarbeiter`): Melder wird nur gesetzt, wenn die Nummer
  nicht die Firmenzentrale ist und genau einem aktiven Mitarbeiter
  gehört; ohne DB wird nur die Firma zugewiesen.

## [0.3.4] - 2026-09-15

### Geändert

- Rufnummern-Zuordnung fragt zuerst die nationale Schreibweise an (der
  Normalfall) und nur bei Fehlschlag die gelieferte 0049-Form - spart
  pro Voicemail einen API-Aufruf.

## [0.3.3] - 2026-09-15

### Behoben

- Rufnummern-Zuordnung fand national gepflegte Nummern nicht: Starface
  liefert international (0049702451366), TANSS speichert national
  (07024/51366), und der identify-Endpunkt gleicht das 0049-Präfix nicht
  selbst an. Findet die gelieferte Nummer nichts, wird jetzt zusätzlich
  die auf 0... normalisierte Schreibweise probiert (0049/+49 -> 0;
  ausländische Nummern bleiben unverändert).

## [0.3.2] - 2026-09-15

### Geändert

- Kommentar-Titel ist wieder "Transkript: <Dateiname>".
- Der Idempotenz-Marker steht nicht mehr im Kommentar (Vorgabe). Gegen
  Doppelverarbeitung schützt damit allein die State-DB; Marker aus
  älteren Versionen werden in der Historie weiterhin erkannt.

## [0.3.1] - 2026-09-15

### Geändert

- Die Zeile "Datei: ... | Audio: ... | Anrufer: ..." entfällt im Kommentar;
  er besteht jetzt aus Zuordnungszeile, Transkript und Marker.

## [0.3.0] - 2026-09-15

### Geändert

- **Neues Kommentarformat** (Vorgabe): Titel "Automatisch erzeugtes
  Transkript"; erste Zeile `Zuordnung: <KUBEZ> | <Anrede> <Titel> <Vorname>
  <Nachname> (<Rolle>)` (Bestandteile entfallen, wenn in TANSS nicht
  gepflegt; ohne Personenzuordnung nur die KUBEZ, ohne Firmenzuordnung der
  Klartext-Grund), dann das Transkript, dann die Zeile
  `Datei: ... | Audio: ... | Anrufer: ...`, zuletzt der Idempotenz-Marker.
  KUBEZ und Personenbeschriftung kommen aus der Datenbank
  (firmen.displayID, mitarbeiter + anrede + mitarbeiter_titel + funktion);
  ohne DB greifen die Namen aus der identify-Antwort.
  Der Block "Automatische Triage" mit Ticket-Änderungen entfällt.

### Behoben

- Mehrfach-Firmen-Check fragte die falsche Spalte ab (`maID` statt
  `mitarbeiterID` in `mitarbeiter_firmen`) und lief dadurch immer in den
  Fail-safe - Ansprechpartner wurden nie als Melder gesetzt.

## [0.2.2] - 2026-09-15

### Geändert

- Die harmlose Hugging-Face-Warnung "set a HF_TOKEN" (anonymer
  Modell-Download, nur Rate-Limit-Hinweis) wird nicht mehr geloggt.

## [0.2.1] - 2026-09-15

### Behoben

- Anrufernummer-Extraktion: Starface nennt die Nummer im Betreff teils
  doppelt ("von 0157... 0157... in ..."); die beiden Nummern verschmolzen
  zu einer Doppelnummer, an der auch die Rufnummern-Zuordnung scheiterte.
  Jetzt zählt die letzte zusammenhängende Ziffernfolge.

## [0.2.0] - 2026-09-15

### Hinzugefügt

- **Mail-Anhänge werden verarbeitet**: Starface hängt die Voicemail an die
  eingehende Mail, nicht ans Ticket - `GET /tickets/{id}/documents` sah sie
  daher nie (in der Datenbank liegt sie in `mails_attachments`, nicht in
  `bug_files`). Audio wird jetzt aus zwei Quellen gesammelt: Ticket-Dokumente
  und die Anhänge aller Mails der Ticket-Historie. Mail-Anhänge werden
  bevorzugt direkt aus dem Storage des TANSS-Servers gelesen
  (`[storage] mail_attachments_dir`, Default
  `~/app/storage/dokumente/attachments`, Ablage
  `<verzeichnis>/<filenameDB>`); ist die DB nicht verfügbar, wird die API
  befragt (`GET /api/v1/mails/{id}`).
- Diagnose-Kommando `--mail <id>`: zeigt eine Mail samt Anhängen aus API
  und Datenbank.

### Geändert

- State-Schlüssel sind jetzt Texte (`doc:<id>` bzw. `mail:<id>:<datei>`),
  weil Mail-Anhänge keine eigene numerische ID haben; eine vorhandene
  State-DB wird beim Start automatisch migriert. Die Kommentar-Marker
  tragen dieselben Schlüssel.
- "Ticket hat keine Audio-Anhänge" wird jetzt auf INFO geloggt, damit ein
  Lauf mit `--ticket` nicht mehr wortlos endet.

### Behoben

- Webhook-Server liest den Request-Body jetzt auch bei abgelehnten
  Anfragen ein, statt Clients mit einem Verbindungsabbruch stehenzulassen.

## [0.1.3] - 2026-09-15

### Behoben

- Login scheiterte mit HTTP 400 "Bad request", wenn die REST-API der
  Installation hinter dem Präfix `/backend` liegt (der nackte Pfad
  `/api/v1/login` landet dann im PHP-Frontend). Der Client probiert jetzt
  beide Varianten, merkt sich die funktionierende Basis-URL und empfiehlt
  im Log, `TANSS_BASE_URL` entsprechend zu setzen. Ein 403 (falsche
  Zugangsdaten) bricht die Suche sofort ab.

## [0.1.2] - 2026-09-15

### Behoben

- `.env`, `config.toml` und `VERSION` wurden nach einer nicht-editierbaren
  Installation (`pip install .`, wie sie deploy.ps1 auf dem Server macht)
  nicht gefunden: der Projektstamm wurde relativ zur Moduldatei berechnet
  und zeigte damit in die site-packages des venv. Der Stamm wird jetzt über
  `TANSS_TRIAGE_HOME`, den Quellbaum (editierbare Installation) oder das
  Arbeitsverzeichnis bestimmt - systemd setzt `WorkingDirectory` passend,
  ein manueller Start braucht vorher `cd` in den Projektordner.
- Selbsttest in deploy.ps1 läuft jetzt im Zielverzeichnis (zeigte sonst
  Version 0.0.0).

## [0.1.1] - 2026-09-10

### Geändert

- LLM-Prompt: der Betreff beschreibt nur noch das Anliegen - keine
  Anrufer- oder Praxisnamen mehr im Betreff.
- Durchgängig echte deutsche Umlaute in Kommentaren, Prompts, Meldungen
  und Dokumentation (statt ae/oe/ue), damit auch das LLM Umlaute
  zurückliefert.
- Testfunktionen einheitlich englisch benannt.

## [0.1.0] - 2026-09-10

### Hinzugefügt

- Webhook-Server für TANSS-Event-Regeln (`TICKET_CREATED` / `TICKET_EMAIL_RECEIVED`);
  kein Polling, Verarbeitung ausschließlich ereignisgesteuert.
- Vollständige Transkription von Audio-Anhängen (wav, mp3, m4a, ogg, opus,
  flac, aac, wma) mit lokalem Whisper (faster-whisper, Default `large-v3`).
- Transkript wird als (interner) Kommentar in das Ticket geschrieben - bei
  allen Tickets mit Audio-Anhang.
- Starface-Erkennung über den Mail-Absender der Ticket-Historie; nur bei
  Starface-Tickets werden Betreff und Beschreibung neu gesetzt, und nur,
  solange dort noch der generische Starface-Text steht (Überschreib-Schutz
  für manuell angepasste Tickets).
- LLM-Aufbereitung (Betreff, strukturierte Beschreibung, Melder-Vorschlag)
  über die Claude-API bei hinterlegtem `ANTHROPIC_API_KEY`; alternativ
  lokal gehostetes Modell über einen OpenAI-kompatiblen Endpunkt
  (Ollama, vLLM, LM Studio). Ohne LLM-Konfiguration entfällt dieser Schritt.
- Automatische Zuordnung von Starface-Tickets per Rufnummernvergleich
  (`POST /api/v1/phoneCalls/identify`): Firma zuweisen; bei eindeutiger
  Durchwahl zusätzlich den Ansprechpartner als Melder. Mitarbeiter, die in
  mehreren Firmen arbeiten, werden nicht zugeordnet (Read-only-Prüfung auf
  `mitarbeiter_firmen` über `~/.my.cnf`).
- Idempotenz über SQLite-State und Marker-Kommentar (übersteht State-Verlust).
- CLI: `serve` (Default), `--ticket <id>`, `--identify <nummer>`,
  `--register-webhook <url>`, `--list-webhooks`, `--dry-run`, `--config`.
- Deployment: `deploy.ps1` (scp/ssh nach `/home/tanss/listings/TANSS-Triage`),
  systemd-Unit `deploy/tanss-triage.service`.

# Changelog

Alle nennenswerten Änderungen an TANSS-Triage stehen in dieser Datei.

Das Format folgt [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
die Versionierung folgt [SemVer](https://semver.org/lang/de/) (x.y.z).

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

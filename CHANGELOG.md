# Changelog

Alle nennenswerten Änderungen an TANSS-Triage stehen in dieser Datei.

Das Format folgt [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
die Versionierung folgt [SemVer](https://semver.org/lang/de/) (x.y.z).

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

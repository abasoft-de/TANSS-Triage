# Changelog

Alle nennenswerten Änderungen an TANSS-Triage stehen in dieser Datei.

Das Format folgt [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
die Versionierung folgt [SemVer](https://semver.org/lang/de/) (x.y.z).

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

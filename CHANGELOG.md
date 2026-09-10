# Changelog

Alle nennenswerten Aenderungen an TANSS-Triage stehen in dieser Datei.

Das Format folgt [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
die Versionierung folgt [SemVer](https://semver.org/lang/de/) (x.y.z).

## [0.1.0] - 2026-09-10

### Hinzugefuegt

- Webhook-Server fuer TANSS-Event-Regeln (`TICKET_CREATED` / `TICKET_EMAIL_RECEIVED`);
  kein Polling, Verarbeitung ausschliesslich ereignisgesteuert.
- Vollstaendige Transkription von Audio-Anhaengen (wav, mp3, m4a, ogg, opus,
  flac, aac, wma) mit lokalem Whisper (faster-whisper, Default `large-v3`).
- Transkript wird als (interner) Kommentar in das Ticket geschrieben - bei
  allen Tickets mit Audio-Anhang.
- Starface-Erkennung ueber den Mail-Absender der Ticket-Historie; nur bei
  Starface-Tickets werden Betreff und Beschreibung neu gesetzt, und nur,
  solange dort noch der generische Starface-Text steht (Ueberschreib-Schutz
  fuer manuell angepasste Tickets).
- LLM-Aufbereitung (Betreff, strukturierte Beschreibung, Melder-Vorschlag)
  ueber die Claude-API bei hinterlegtem `ANTHROPIC_API_KEY`; alternativ
  lokal gehostetes Modell ueber einen OpenAI-kompatiblen Endpunkt
  (Ollama, vLLM, LM Studio). Ohne LLM-Konfiguration entfaellt dieser Schritt.
- Automatische Zuordnung von Starface-Tickets per Rufnummernvergleich
  (`POST /api/v1/phoneCalls/identify`): Firma zuweisen; bei eindeutiger
  Durchwahl zusaetzlich den Ansprechpartner als Melder. Mitarbeiter, die in
  mehreren Firmen arbeiten, werden nicht zugeordnet (Read-only-Pruefung auf
  `mitarbeiter_firmen` ueber `~/.my.cnf`).
- Idempotenz ueber SQLite-State und Marker-Kommentar (uebersteht State-Verlust).
- CLI: `serve` (Default), `--ticket <id>`, `--identify <nummer>`,
  `--register-webhook <url>`, `--list-webhooks`, `--dry-run`, `--config`.
- Deployment: `deploy.ps1` (scp/ssh nach `/home/tanss/listings/TANSS-Triage`),
  systemd-Unit `deploy/tanss-triage.service`.

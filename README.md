# TANSS-Triage

Transkribiert Sprachnachrichten aus TANSS-Tickets mit lokalem Whisper und
bereitet Starface-Voicemail-Tickets automatisch auf: Transkript als
Kommentar, Betreff und Beschreibung per LLM, Firma und Melder per
Rufnummernvergleich.

Version: siehe [VERSION](VERSION) - Änderungen: [CHANGELOG.md](CHANGELOG.md)

## Was passiert

Das Starface-Telefoniesystem legt für Sprachnachrichten per E-Mail
TANSS-Tickets an ("Sie haben eine Sprachnachricht von ... erhalten") mit der
Aufnahme als WAV-Anhang. TANSS-Triage läuft dauerhaft auf dem TANSS-Server
und wird **ausschließlich per Webhook** (TANSS-Event-Regeln) über neue
Tickets benachrichtigt - es pollt nicht.

Für jedes gemeldete Ticket:

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
     der Firma, darf es den Melder vorschlagen.
   - **Überschreib-Schutz**: Betreff wird nur ersetzt, solange er noch mit
     "Sie haben eine Sprachnachricht" beginnt; die Beschreibung nur, solange
     die Starface-Boilerplate ("WARNUNG: EXTERNE NACHRICHT" / "STARFACE")
     noch darin steht. Von Kolleg*innen angepasste Tickets bleiben unberührt.

Nicht-Starface-Tickets mit Audio-Anhang bekommen nur den
Transkript-Kommentar; Betreff, Beschreibung und Zuordnung bleiben unberührt.

Doppelte Verarbeitung wird zweifach verhindert: eine SQLite-State-Datei
merkt sich verarbeitete Dokument-IDs, und jeder Kommentar trägt einen
Marker `[TANSS-Triage vX.Y.Z | doc:<id>]`, der auch nach Verlust der
State-Datei erkannt wird.

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
  Historie, Dokumente, Kommentare, Rufnummern-Identifikation und - für
  `--register-webhook` - Event-Regeln.
- Für den Mehrfach-Firmen-Check liest das Programm `~/.my.cnf` (dieselbe
  Datei wie der MCP-Server, User `tanssuser`, read-only). Fehlt sie, werden
  Ansprechpartner-Treffer sicherheitshalber nicht zugeordnet.

Beim ersten Lauf lädt faster-whisper das Modell (`large-v3` ~= 3 GB) in den
Hugging-Face-Cache (`download_root` in der config.toml verlegt ihn).

### 3. Webhook in TANSS anlegen

```bash
.venv/bin/python -m tanss_triage --register-webhook "http://127.0.0.1:8763/webhook"
```

(bzw. `.../webhook/<secret>`, wenn in der config.toml ein Secret gesetzt
ist). Danach in der TANSS-Oberfläche prüfen, dass die Regel bei
**Ticket erstellt** bzw. **E-Mail empfangen** feuert; `--list-webhooks`
zeigt die vorhandenen Regeln. Scheitert die Anlage per API an Rechten,
die Regel manuell in TANSS anlegen (Aktion: WebHook, URL wie oben,
Methode POST).

### 4. Dienst (Linux, systemd)

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
python -m tanss_triage                     # Dauerbetrieb: Webhook-Server + Worker
python -m tanss_triage --ticket 250312 --dry-run   # ein Ticket testweise (schreibt nichts)
python -m tanss_triage --ticket 250312    # ein Ticket scharf nachziehen
python -m tanss_triage --identify 07432994360      # Rufnummernauflösung ansehen
python -m tanss_triage --mail 356324       # Mail samt Anhängen zeigen (Diagnose)
python -m tanss_triage --list-webhooks    # Event-Regeln anzeigen
```

`GET http://127.0.0.1:8763/health` antwortet mit Name und Version
(für Monitoring).

**Empfohlener Ersttest** auf dem Server: ein bekanntes Voicemail-Ticket mit
`--ticket <id> --dry-run` durchspielen und das geplante Ergebnis im Log
ansehen, dann ohne `--dry-run` scharf schalten.

Da bewusst nicht gepollt wird, werden Tickets aus einer Downtime nicht
automatisch nachgeholt - einzelne Tickets lassen sich mit `--ticket <id>`
nachziehen.

## Entwicklung

```bash
.venv/bin/python -m pytest        # Tests, ohne Netz und ohne Modelle
```

Struktur: `webhook_server.py` nimmt Events an und füllt eine Queue,
`worker.py` arbeitet sie sequenziell ab (Whisper lastet die CPU allein aus),
`processor.py` orchestriert pro Ticket, `tanss_client.py` kapselt die
REST-API (Login-Eigenheiten: Header `apiToken`, 2-Minuten-Idle-Timeout,
automatischer Re-Login), `assigner.py` enthält die Zuordnungsregeln,
`triage.py`/`llm.py` den LLM-Schritt. Erweiterungen (weitere Trigger,
weitere Aktionen) docken am Processor an.

Versionierung: SemVer in [VERSION](VERSION), Änderungen in
[CHANGELOG.md](CHANGELOG.md) nachführen.

# Changelog

Alle nennenswerten Änderungen an TANSS-Triage stehen in dieser Datei.

Das Format folgt [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
die Versionierung folgt [SemVer](https://semver.org/lang/de/) (x.y.z).

## [0.10.0] - 2026-11-26

### Hinzugefügt

- **Nachrichten der Praxis** in der Duplikatprüfung: weitere eingehende
  Mails am Ticket (auch Kontaktformulare) und unsere Transkripte weiterer
  Sprachnachrichten. Ungeduldige Nachfragen ("warten immer noch auf ...")
  und wiederholte Anrufe stehen oft nur dort.
  - Sichtung: je Ticket der Anfang der jüngsten weiteren Mail bzw.
    Sprachnachricht. Nachprüfung: die ersten und letzten Nachrichten wie
    bei den Leistungen.
  - Nicht dabei: eigene Absender (neu: `[duplicates] own_senders`,
    Default `@abasoft.de` - Eingangsbestätigungen), Starface-Mails,
    ausgehende Mails und die Mail, aus der das Ticket entstand. Zitierte
    Verläufe unter Antworten werden abgeschnitten. Das Mail-Datum kommt
    aus dem Date-Header (`mails_headers`), die Tabelle `mails` hat keins.
  - Transkripte kommen aus den Ticket-Kommentaren (`posting`, Typ 3,
    Titel "Transkript: ..."); andere Kommentare, vor allem die eigenen
    Duplikat-Hinweise, bleiben draußen.
  - Ein Ticket "Sprachnachricht ohne Inhalt" zählt als Vergleich, wenn ein
    späterer Anruf am selben Ticket Inhalt hatte.
- **Passwörter werden geschwärzt**, bevor ein Text ans LLM geht
  (`masking.py`, `[masking]`): Duplikat-Prompts und Transkripte für die
  Starface-Aufbereitung. Drei Regeln - das Wort nach "Passwort:", "PIN
  1234" usw.; passwortartige Wörter (Klein- und Großbuchstaben bzw.
  Sonderzeichen und Ziffern, mindestens vier Wechsel der Zeichenart, z. B.
  "Sommer2024!"); Base64-Zeilen. URLs, E-Mail-Adressen, IP-Adressen,
  Zahlen, Pfade, Markup, Produktnamen wie "Eternus02" oder "Orga930M",
  fremde Ticketnummern und Mengen wie "2xLenovo" bleiben stehen;
  `keep_words` nimmt weitere aus. Erprobt an gut 12.000 echten
  Tickettexten: geschwärzt wird in knapp 2 % der Texte, im Messlauf in 7
  von 186 Prompts - alle zu Recht.
- **Geplante Termine**: Leistungen mit Datum in der Zukunft gehen
  gesondert als "geplant" in die Nachprüfung (höchstens zwei, die nächsten
  zuerst). Die ersten und letzten Leistungen wählt sie nur noch aus den
  vergangenen - vorab eingetragene Termine verdrängen den aktuellen Stand
  nicht mehr.

### Geändert

- **Serien mit gleichem Betreff** (Monitoring, wiederholte Mails): Der
  Hinweis nennt nur noch das älteste offene Ticket mit diesem Betreff -
  das, in das zusammengeführt wird - und die Größe der Serie, statt der
  drei jüngsten.
- **Strenger bei inhaltsarmen neuen Tickets** (nur Rückrufbitte, ein
  Stichwort wie "Drucker", leere Beschreibung): Beide LLM-Stufen melden
  ein Duplikat nur noch mit konkretem Bezug - dieselbe Person oder
  Rückrufnummer, dasselbe Gerät, ein Verweis auf das frühere Anliegen.
- **Duplikat-Hinweise mit Begründung**: Jeder Treffer trägt im Kommentar
  unter dem Betreff eine Begründung; weitere Treffer folgen nach einer
  Trennlinie (Vorlage: Ticket 260976). Bisher begründete nur die
  Nachprüfung, und auch das nur im Log.
- Hinter dem Betreff steht die Ticketnummer nicht mehr - sie steht schon
  in der Überschrift darüber.
- Die Nachprüfung schickt keine Leistungen ohne eigene Aussage mehr ans
  LLM: Protokolle verschickter Mails ("[Automatische Protokollierung des
  Ticket-E-Mail-Versandes]", die Mail liegt ohnehin am Ticket),
  Systemeinträge ("[System] ...") und reine Vermerke "Automatische
  Rückantwort". Sie werden vor der Auswahl der ersten und letzten
  Leistungen aussortiert und belegen keinen Platz mehr.
  - Die Sichtung (Stufe 1) liefert dafür ein neues Pflichtfeld
    `begruendung` je genanntem Ticket; die Nachprüfung schreibt ein bis
    drei Sätze statt einem.
  - Treffer ohne LLM bekommen eine feste Begründung (genannte
    Ticketnummer bzw. gleicher Betreff).
  - Im Log steht je Treffer eine Zeile `Duplikat von #… - <Begründung>`,
    gleich in welcher Stufe er entstand.

## [0.9.0] - 2026-11-16

### Hinzugefügt

- **Erkennung potenzieller Ticket-Duplikate** (`duplicates.py`,
  `[duplicates]`): Ein Ticket wird geprüft, sobald es erstmals bei einer
  Kundenfirma liegt - gegen die offenen Tickets desselben Kunden. Bei
  Verdacht bekommt das neue Ticket den Tag "Prüfen! pot. Duplikat"
  (`tag_id`, Default 181) und einen internen Kommentar mit dem Titel
  `Potenzielles Duplikat von Ticket #1234` und dem Text
  `"Titel des anderen Tickets" #1234`; weitere Treffer folgen im Text
  jeweils nach einer Leerzeile mit derselben Überschrift (höchstens
  `max_matches` Treffer, Default 3).
  - Drei Stufen: ohne LLM-Aufruf, wenn das neue Ticket die Nummer eines
    offenen Tickets desselben Kunden nennt (Betreff oder Text, für sich
    stehende Zahl ab fünf Stellen) oder exakt denselben Betreff trägt;
    Sichtung von Betreffs und Beschreibungsanfängen per LLM; Nachprüfung nur der
    unklaren Kandidaten mit Beschreibung und Leistungen (bis
    `services_head` + `services_tail` alle, sonst die ersten und die
    letzten). Antworten per JSON-Schema, nur vorgelegte Ticketnummern
    zählen.
  - Trigger im bestehenden Poll-Takt über zwei neue Cursor: neue Tickets
    mit Firma (`bug`) und Firmenzuweisungen aus dem Änderungsprotokoll
    (`entity_changes`, Feld `companyId`). Der erste Lauf setzt nur die
    Cursor - ein Deployment markiert keine Alttickets.
  - Jedes Ticket wird einmal geprüft (State `dup:<Ticket>`). Tickets der
    eigenen Firma (`exclude_company_ids`, Default ABASTU) und Tickets ohne
    Firma bleiben offen und werden geprüft, sobald sie bei einem Kunden
    liegen. Bei noch generischem Starface-Betreff wartet die Prüfung auf
    die Transkription und läuft direkt danach.
  - Fehlgeschlagene Prüfungen (LLM, Datenbank) werden nach zehn Minuten
    wiederholt, höchstens dreimal.
  - Verweigert TANSS dem API-Benutzer das Ticket (HTTP 403, etwa weil ihm
    das Recht "Tickets anzeigen (alle Abteilungen)" fehlt und das Ticket
    bei einer Abteilung liegt), endet die Prüfung vor dem ersten LLM-Aufruf
    mit einer Warnung und wird nicht wiederholt.
  - Ohne LLM ruht die Erkennung.
- Einzellauf `--duplicate <ID>` (mit `--dry-run` kombinierbar) für Test
  und Diagnose; prüft auch ein schon geprüftes Ticket.

### Geändert

- Die Worker-Queue trägt Paare (Handler, Ticket-ID); Audio-Verarbeitung und
  Duplikatprüfung teilen sich die Queue und laufen in ihrer Reihenfolge.
- Poller: Scheitert eine einzelne Abfrage, werden die Funde der übrigen
  trotzdem eingereiht. Bisher gingen sie verloren, obwohl deren Cursor
  schon weitergerückt waren.
- Die Startzeile im Log nennt den Zustand der Duplikat-Erkennung.

## [0.8.0] - 2026-09-29

### Geändert

- **Modell Claude Sonnet 5.5** (`claude-sonnet-5-5`) statt Sonnet 5 - gleicher
  Preis, gleicher Prompt.
- **Structured Outputs**: Die Claude-API bekommt das Antwortformat als
  JSON-Schema (`output_config.format`, `triage.TRIAGE_SCHEMA`) und kann nur
  noch gültiges JSON mit genau den Feldern betreff, beschreibung, melder_id
  und praxisausfall liefern - kein Code-Zaun, kein Erklärtext, kein
  fehlendes Feld. Der tolerante Parser bleibt für lokale Modelle
  (`openai_compatible`), denen kein Schema mitgegeben wird.
- `[llm] max_tokens` von 1500 auf 16000: Das Denken des Modells zählt mit
  hinein, eine abgeschnittene Antwort wäre kein gültiges JSON. Es ist nur
  eine Obergrenze, abgerechnet wird der tatsächliche Verbrauch. **Steht in
  der config.toml auf dem Server noch `max_tokens = 1500`, den Eintrag
  anheben oder löschen.**
- Abhängigkeit `anthropic>=1.4,<2` (vorher `>=0.40`), damit `deploy.ps1`
  das SDK auf dem Server auf einen Stand mit Structured Outputs und
  Fallbacks hebt.

### Hinzugefügt

- **Ersatzmodell bei Ablehnung**: `[llm] fallbacks = "default"` - lehnt
  Claude eine Anfrage ab, rechnet serverseitig das von Anthropic nach
  Ablehnungsgrund gewählte Modell weiter (Beta
  `server-side-fallback-2026-07-01`). `""` schaltet es ab.
- Eine Ablehnung (`stop_reason` refusal, samt Kategorie) oder eine bei
  `max_tokens` abgeschnittene Antwort landet als eigene Warnung im Log; das
  Ticket bleibt dann unverändert.

## [0.7.0] - 2026-09-23

### Hinzugefügt

- **Feste Abteilung HLE**: Jedes Starface-Ticket wird der HLE zugewiesen
  (`assignedToDepartmentId`, `[departments] id`, Default 8), solange es
  noch keiner Abteilung zugewiesen ist - auch ohne LLM. Die HLE ist gut
  besetzt und verteilt selbst weiter. (Eine LLM-gestützte Zuordnung auf
  HLE/HLT/Vertrieb war entworfen und getestet, ist aber auf Vorgabe
  wieder entfallen.)
- **Praxisausfall**: Kann die Praxis insgesamt nicht arbeiten (Server weg,
  EVA startet nirgends, "Praxis steht still"), setzt das LLM das neue
  Feld `praxisausfall`. Das Programm stellt dem Betreff "SERVERAUSFALL !! "
  voran (innerhalb der 100 Zeichen), setzt Fälligkeit und Deadline auf
  jetzt und hängt den Tag DRINGEND an (`POST /api/v1/tags/assignment`,
  vorhandene Tags bleiben). Ein manuell geänderter Betreff bleibt auch hier
  stehen; die Abteilung bleibt HLE. Nur ein echtes JSON-`true` zählt, der
  Prompt verlangt im Zweifel `false`; ein indirekt ausgesprochener
  Stillstand ("brauchen Hilfe, damit wir arbeiten können") zählt. Probelauf
  mit Sonnet 5 gegen alle 71 Voicemails seit 15.09. und danach gegen die 22
  Grenzfälle darunter: die drei echten Totalausfälle erkannt, kein
  Fehlalarm - auch nicht bei einzelnem Arbeitsplatz, Homeoffice,
  Kartenleser oder Internet allein. Abschaltbar über `[outage] enabled`,
  der Tag über
  `[outage] tag_id = 0`. Der API-Benutzer braucht zusätzlich das Recht
  "Ticket-System: Deadline bearbeiten".

### Geändert

- Betreff-Regel ergänzt: Nennt der Anrufer nur Namen, Praxis oder
  Rückrufnummer, ist das eine Rückrufbitte ("Rückruf erbeten -> RR ..."),
  keine leere Nachricht.

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

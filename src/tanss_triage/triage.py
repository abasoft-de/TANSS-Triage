"""
Usage: from tanss_triage.triage import run_triage, parse_triage_json

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: triage.py
Beschreibung: Baut aus dem Transkript einer Sprachnachricht per LLM einen
              ordentlichen Ticketbetreff und eine strukturierte Beschreibung.
              Der Betreff folgt dem Stil, in dem die Hotline ihre Tickets
              benennt (Telegrammstil, Hausabkürzungen, Rückrufhinweis) -
              abgeleitet aus den bestehenden HLE-/HLT-Betreffen.
              Bekommt das LLM zusätzlich die Ansprechpartnerliste der schon
              identifizierten Firma, darf es den Melder vorschlagen (nur IDs
              aus der Liste werden akzeptiert). Außerdem kennzeichnet es
              Praxisausfälle (Server weg, Praxis kann nicht arbeiten). Die
              Antwort ist striktes JSON; was nicht parsebar ist, führt zu
              "kein Update" statt zu einem kaputten Ticket.
Letzte Änderung: 2026-09-23
"""

import json
import logging
import re
from dataclasses import dataclass

from .llm import LlmError

LOG = logging.getLogger("tanss_triage.triage")

# Harte Grenze: bug.ueberschrift ist varchar(100). Kein einziger der
# bestehenden HLE-/HLT-Betreffe ist länger, der Schnitt liegt bei 55 Zeichen.
MAX_SUBJECT_LENGTH = 100

# Betreff und Beschreibung, wenn nichts gesprochen wurde (Piepton, aufgelegt).
# So benennen die Kolleg*innen solche Tickets auch von Hand.
EMPTY_TRANSCRIPT_SUBJECT = "Sprachnachricht ohne Inhalt"
EMPTY_TRANSCRIPT_CONTENT = ("Sprachnachricht ohne verständlichen Inhalt - "
                            "es wurde nichts gesprochen.")

# Hausabkürzungen für den Betreff: Whisper schreibt aus, was gesprochen wird
# ("die Kartenlesegeräte lesen keine Gesundheitskarten"), die Hotline notiert
# kurz ("KT liest keine eGKs"). Die Liste wächst mit der Zeit (eAU, ePA und
# Hybrid-DRG kamen alle erst dazu) - ergänzen lässt sie sich über
# [llm] subject_abbreviations_extra in der config.toml, ohne Deployment.
DEFAULT_ABBREVIATIONS = [
    "EVA", "EVABOX", "KIM", "ePA", "eRP", "eAU", "eGK", "KVK",
    "KT (Kartenterminal)", "KL (Kartenleser)", "SMC-KT", "HBA", "TI",
    "AP (Arbeitsplatz)", "HO (Homeoffice)", "HZV", "PAL", "PSÄ",
    "RE (Rechnung)", "DaSi (Datensicherung)",
    "MDB (Medikamentendatenbank)", "OT (Online-Terminkalender)",
    "FiBu", "GOÄ", "LANR", "ÜW (Überweisung)", "RR (Rückruf)",
]

SYSTEM_PROMPT_TEMPLATE = """\
Du bereitest Voicemail-Tickets in einem IT-Ticketsystem (TANSS) auf. Die
Anrufer sind Kunden eines IT-Systemhauses für Arztpraxen (Software EVA).
Du bekommst das automatische Transkript einer Sprachnachricht (Whisper,
Telefonqualität - rechne mit Erkennungsfehlern bei Namen und Fachbegriffen)
sowie Metadaten zum Anruf.

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt, ohne Erklärtext davor oder
danach, mit genau diesen Feldern:

{
  "betreff": "Ticketbetreff nach den Betreff-Regeln unten",
  "beschreibung": "strukturierte Zusammenfassung: 1-3 Sätze Anliegen, danach falls vorhanden Zeilen wie 'Anrufer: ...', 'Rückruf unter: ...', 'Dringlichkeit: ...'. Der Name des Anrufers gehört hierher, auch wenn er im Betreff entfällt. Schreibe 'Rückruf unter: ...' nur hinein, wenn der Anrufer selbst die Nummer nennt. Nur Informationen aus Transkript/Metadaten, nichts erfinden. Füge zwischen Anliegen und den Zeilen danach eine Leerzeile ein, sofern du Zeilen danach schreibst.",
  "melder_id": null,
  "praxisausfall": true oder false (Regel unten)
}

Betreff-Regeln - so benennt die Hotline ihre Tickets:

- Telegrammstil ohne Einleitung, Ziel 30 bis 90 Zeichen, nie mehr als 100.
  Gegenstand plus Problem oder Infinitiv: "KIM und ePA gehen nicht",
  "Nadeldrucker druckt nicht", "Ziffer 86901 freischalten".
- Kein Satzpunkt, keine Floskeln ("Sprachnachricht von", "Anruf wegen"),
  keine Praxis- oder Firmennamen - die Firma hängt am Ticket.
- Nutze die Hausabkürzungen, auch wenn das Transkript sie ausspricht:
  %(kuerzel)s.
  Fachbegriffe, die du nicht sicher zuordnest, lässt du so stehen, wie sie
  im Transkript fallen.
- Höchstens drei Anliegen, verbunden mit " + ". Einen Zusatzhinweis hängst
  du mit " -> " an.
- Bittet der Anrufer um Rückruf, endet der Betreff mit "RR" - samt der
  Nummer und dem Zeitfenster, die er nennt:
  "... -> RR 07141 141210 ab 14 Uhr". Die übermittelte Anrufernummer aus
  den Metadaten gehört NICHT in den Betreff, nur eine im Gespräch genannte.
- Den Nachnamen des Anrufers nur im Rückrufteil nennen
  ("... -> RR Frau Meier 07141 141210"), nie am Betreffanfang.
- Dringlichkeit nur übernehmen, wenn der Anrufer sie ausspricht
  ("dringend", "Praxis steht still").
- Nennt der Anrufer nur Namen, Praxis oder Rückrufnummer, ist das eine
  Rückrufbitte ("Rückruf erbeten -> RR ..."), keine leere Nachricht.
- Wurde nichts gesagt oder ist nichts verständlich, lautet der Betreff
  genau "%(leer)s".

Wenn eine Liste möglicher Ansprechpartner mitgegeben wird und du den Anrufer
darin sicher wiedererkennst (Name im Transkript passt eindeutig), setze
"melder_id" auf dessen id (Zahl). Im Zweifel null - eine falsche Zuordnung
ist schlimmer als keine.

Praxisausfall - gesondert kennzeichnen:
- "praxisausfall": true, wenn die Praxis insgesamt nicht arbeiten kann:
  Server ausgefallen oder nicht erreichbar, EVA startet an keinem
  Arbeitsplatz, "Praxis steht still", "nichts geht mehr", "wir können
  nicht arbeiten" - auch indirekt gesagt ("brauchen dringend Hilfe,
  damit wir arbeiten können").
- false bei allem, was nur einen Teil betrifft: ein einzelner
  Arbeitsplatz oder ein Gerät, Internet oder TI allein (KIM, eRP,
  Kartenlesen), solange in EVA weitergearbeitet werden kann. Im Zweifel
  false - ein Fehlalarm macht ein Ticket unnötig dringend.
- Den Betreff formulierst du wie immer; die Kennzeichnung als
  Serverausfall setzt das Programm selbst davor.\
"""


@dataclass
class TriageResult:
    betreff: str
    beschreibung: str
    melder_id: int | None
    praxisausfall: bool = False


def build_system_prompt(extra_abbreviations=None):
    """Setzt den Systemprompt samt Abkürzungsliste zusammen.

    Zusätzliche Kürzel aus der config.toml werden angehängt, nicht ersetzt -
    wer ein neues Kürzel braucht, schreibt eine Zeile und nicht die ganze
    Liste.
    """
    kuerzel = list(DEFAULT_ABBREVIATIONS)
    for item in (extra_abbreviations or []):
        item = str(item).strip()
        if item and item not in kuerzel:
            kuerzel.append(item)
    return SYSTEM_PROMPT_TEMPLATE % {"kuerzel": ", ".join(kuerzel),
                                     "leer": EMPTY_TRANSCRIPT_SUBJECT}


def build_user_prompt(transcript, caller_number="", voicemail_box="",
                      contacts=None):
    """Baut die Nutzer-Nachricht für das LLM zusammen."""
    lines = []
    if caller_number:
        # Ausdrücklich "übermittelt": diese Nummer gehört nicht in den
        # Betreff, sie steht ohnehin am Ticket.
        lines.append("Übermittelte Anrufernummer: %s" % caller_number)
    if voicemail_box:
        lines.append("Voicemail-Box: %s" % voicemail_box)
    if contacts:
        lines.append("")
        lines.append("Mögliche Ansprechpartner (id: Name):")
        for contact in contacts:
            lines.append("  %s: %s" % (contact.get("id"),
                                       contact.get("name", "").strip()))
    lines.append("")
    lines.append("Transkript der Sprachnachricht:")
    lines.append(transcript.strip() or "(leer - keine Sprache erkannt)")
    return "\n".join(lines)


def shorten_subject(text, limit=MAX_SUBJECT_LENGTH):
    """Kürzt den Betreff auf die Feldlänge von bug.ueberschrift.

    Geschnitten wird an der Wortgrenze und mit Auslassungszeichen beendet -
    ein hart abgeschnittenes Wort sieht in der Ticketliste aus wie ein
    Tippfehler. Zeilenumbrüche und Mehrfach-Leerzeichen fallen weg, weil ein
    Betreff einzeilig ist.
    """
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 2].rstrip()
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:.+/-<>") + " …"


def parse_triage_json(text):
    """Zieht das JSON-Objekt aus der LLM-Antwort und prüft die Felder.

    Modelle verpacken JSON gern in Code-Zäune oder hängen Sätze an -
    darum wird das erste {...}-Paar gesucht statt blind json.loads zu rufen.
    Liefert None, wenn nichts Brauchbares zu holen ist.
    """
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    betreff = str(data.get("betreff") or "").strip()
    beschreibung = str(data.get("beschreibung") or "").strip()
    if not betreff or not beschreibung:
        return None
    shortened = shorten_subject(betreff)
    if shortened != betreff:
        LOG.info("Betreff des LLM war %d Zeichen lang und wurde gekürzt: %s",
                 len(betreff), shortened)
    melder_id = data.get("melder_id")
    if not isinstance(melder_id, int) or melder_id <= 0:
        melder_id = None
    # Nur ein echtes true zählt - "ja", 1 oder "true" als Text machen kein
    # Ticket dringend.
    praxisausfall = data.get("praxisausfall") is True
    return TriageResult(betreff=shortened, beschreibung=beschreibung,
                        melder_id=melder_id, praxisausfall=praxisausfall)


def run_triage(llm, transcript, caller_number="", voicemail_box="",
               contacts=None, extra_abbreviations=None):
    """LLM-Aufruf plus Parsen; None bei Fehler oder unbrauchbarer Antwort.

    Ein leeres Transkript (Piepton gehört, aufgelegt) braucht kein LLM -
    dafür steht der feste Betreff bereit. melder_id wird gegen die
    Kandidatenliste geprüft: das LLM darf nur IDs vorschlagen, die es auch
    angeboten bekam.
    """
    if llm is None:
        return None
    if not (transcript or "").strip():
        LOG.info("Transkript ist leer - kein LLM-Aufruf, Betreff \"%s\".",
                 EMPTY_TRANSCRIPT_SUBJECT)
        return TriageResult(betreff=EMPTY_TRANSCRIPT_SUBJECT,
                            beschreibung=EMPTY_TRANSCRIPT_CONTENT,
                            melder_id=None)
    user = build_user_prompt(transcript, caller_number, voicemail_box,
                             contacts)
    try:
        answer = llm.complete(build_system_prompt(extra_abbreviations), user)
    except LlmError as error:
        LOG.warning("LLM-Aufruf fehlgeschlagen: %s", error)
        return None
    result = parse_triage_json(answer)
    if result is None:
        LOG.warning("LLM-Antwort nicht als JSON parsebar: %.200s", answer)
        return None
    if result.melder_id is not None:
        known = {contact.get("id") for contact in (contacts or [])}
        if result.melder_id not in known:
            LOG.info("LLM schlug melder_id %s vor, die nicht in der "
                     "Kandidatenliste steht - verworfen.", result.melder_id)
            result.melder_id = None
    return result

"""
Usage: from tanss_triage.triage import run_triage, parse_triage_json

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: triage.py
Beschreibung: Baut aus dem Transkript einer Sprachnachricht per LLM einen
              ordentlichen Ticketbetreff und eine strukturierte Beschreibung.
              Bekommt das LLM zusätzlich die Ansprechpartnerliste der schon
              identifizierten Firma, darf es den Melder vorschlagen (nur IDs
              aus der Liste werden akzeptiert). Die Antwort ist striktes JSON;
              was nicht parsebar ist, führt zu "kein Update" statt zu einem
              kaputten Ticket.
Letzte Änderung: 2026-09-10
"""

import json
import logging
import re
from dataclasses import dataclass

from .llm import LlmError

LOG = logging.getLogger("tanss_triage.triage")

SYSTEM_PROMPT = """\
Du bereitest Voicemail-Tickets in einem IT-Ticketsystem (TANSS) auf. Die
Anrufer sind Kunden eines IT-Systemhauses für Arztpraxen (Software EVA).
Du bekommst das automatische Transkript einer Sprachnachricht (Whisper,
Telefonqualität - rechne mit Erkennungsfehlern bei Namen und Fachbegriffen)
sowie Metadaten zum Anruf.

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt, ohne Erklärtext davor oder
danach, mit genau diesen Feldern:

{
  "betreff": "prägnanter Ticketbetreff, max. 80 Zeichen, deutsch. Beschreibe nur das Anliegen - keine Anrufernamen, keine Praxisnamen, keine Floskeln wie 'Sprachnachricht von'.",
  "beschreibung": "strukturierte Zusammenfassung: 1-3 Sätze Anliegen, danach falls vorhanden Zeilen wie 'Anrufer: ...', 'Rückruf unter: ...', 'Dringlichkeit: ...'. Nur Informationen aus Transkript/Metadaten, nichts erfinden.",
  "melder_id": null
}

Wenn eine Liste möglicher Ansprechpartner mitgegeben wird und du den Anrufer
darin sicher wiedererkennst (Name im Transkript passt eindeutig), setze
"melder_id" auf dessen id (Zahl). Im Zweifel null - eine falsche Zuordnung
ist schlimmer als keine.\
"""


@dataclass
class TriageResult:
    betreff: str
    beschreibung: str
    melder_id: int | None


def build_user_prompt(transcript, caller_number="", voicemail_box="",
                      contacts=None):
    """Baut die Nutzer-Nachricht für das LLM zusammen."""
    lines = []
    if caller_number:
        lines.append("Anrufernummer: %s" % caller_number)
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


def parse_triage_json(text):
    """Zieht das JSON-Objekt aus der LLM-Antwort und prüft die Felder.

    Modelle verpacken JSON gern in ```-Zäune oder hängen Sätze an -
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
    melder_id = data.get("melder_id")
    if not isinstance(melder_id, int) or melder_id <= 0:
        melder_id = None
    return TriageResult(betreff=betreff[:200], beschreibung=beschreibung,
                        melder_id=melder_id)


def run_triage(llm, transcript, caller_number="", voicemail_box="",
               contacts=None):
    """LLM-Aufruf plus Parsen; None bei Fehler oder unbrauchbarer Antwort.

    melder_id wird gegen die Kandidatenliste geprüft - das LLM darf nur
    IDs vorschlagen, die es auch angeboten bekam.
    """
    if llm is None:
        return None
    user = build_user_prompt(transcript, caller_number, voicemail_box,
                             contacts)
    try:
        answer = llm.complete(SYSTEM_PROMPT, user)
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

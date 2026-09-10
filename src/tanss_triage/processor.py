"""
Usage: from tanss_triage.processor import Processor

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: processor.py
Beschreibung: Verarbeitet ein einzelnes Ticket: Audio-Anhaenge finden,
              herunterladen, mit Whisper transkribieren, Transkript als
              Kommentar anlegen. Ist es ein Starface-Voicemail-Ticket, kommen
              Rufnummern-Zuordnung (Firma/Melder) und - falls ein LLM
              konfiguriert ist - Betreff und Beschreibung dazu. Der
              Ueberschreib-Schutz sorgt dafuer, dass manuell angepasste
              Tickets nicht angefasst werden: der Betreff wird nur ersetzt,
              solange er noch generisch ist, die Beschreibung nur, solange
              die Starface-Boilerplate darin steht.
Letzte Aenderung: 2026-09-10
"""

import logging
import os
import re
import tempfile

from . import __version__
from .assigner import Assignment, decide_assignment
from .triage import run_triage

LOG = logging.getLogger("tanss_triage.processor")

MARKER_PREFIX = "[TANSS-Triage"


def comment_marker(document_id):
    """Marker im Kommentar; macht Verarbeitung auch ohne State-DB erkennbar."""
    return "%s v%s | doc:%d]" % (MARKER_PREFIX, __version__, document_id)


def find_audio_documents(documents, extensions):
    """Filtert die Ticket-Dokumente auf Sprachaufnahmen.

    Es zaehlt die Dateiendung ODER ein audio/*-MIME-Typ - das Starface-Mail
    haengt neben der WAV auch Inline-Bilder an (SF_M_IMG_0), die hier
    rausfallen.
    """
    wanted = {extension.lower().lstrip(".") for extension in extensions}
    hits = []
    for document in documents or []:
        name = (document.get("fileName") or "")
        extension = os.path.splitext(name)[1].lower().lstrip(".")
        mime = (document.get("mimeType") or "").lower()
        if extension in wanted or mime.startswith("audio"):
            hits.append(document)
    return hits


def find_starface_mail(history, sender_patterns):
    """Sucht in der Ticket-Historie die eingehende Starface-Mail."""
    patterns = [pattern.lower() for pattern in sender_patterns if pattern]
    for mail in (history or {}).get("mails") or []:
        if not mail.get("inbound"):
            continue
        sender = (mail.get("senderEMail") or "").lower()
        if any(pattern in sender for pattern in patterns):
            return mail
    return None


def extract_caller_info(mail):
    """Zieht Anrufernummer und Voicemail-Box aus der Starface-Mail.

    Betreff: "Sie haben eine Sprachnachricht von <Name?> <Nummer> in <Box>
    erhalten" - vor der Nummer kann ein aufgeloester Name samt Klammern
    stehen, darum gilt: die letzte lange Ziffernfolge vor " in " ist die
    Nummer. Faellt der Betreff aus, hilft der Mailtext ("Sprachmitteilung
    von <Nummer>", "Voicemail-Box <Box> der STARFACE").
    """
    subject = (mail or {}).get("subject") or ""
    body = (mail or {}).get("bodyPlain") or ""

    number = ""
    box = ""
    match = re.search(r"von\s+(.*?)\s+in\s+(.+?)\s+erhalten", subject)
    if match:
        numbers = re.findall(r"\+?\d[\d\s/().-]{5,}\d", match.group(1))
        if numbers:
            number = re.sub(r"[^\d+]", "", numbers[-1])
        box = match.group(2).strip()
    if not number:
        match = re.search(r"Sprachmitteilung von\s*(\+?[\d\s/().-]{6,})",
                          body)
        if match:
            number = re.sub(r"[^\d+]", "", match.group(1))
    if not box:
        match = re.search(r"Voicemail-Box\s+(.+?)\s+der", body)
        if match:
            box = match.group(1).strip()
    return number, box


def should_replace_title(title, pattern):
    """Betreff nur ersetzen, solange er noch generisch ist."""
    if not pattern:
        return True
    return bool(re.search(pattern, title or ""))


def should_replace_content(content, patterns):
    """Beschreibung nur ersetzen, solange die Boilerplate noch drinsteht."""
    if not patterns:
        return True
    haystack = (content or "").lower()
    return any(pattern.lower() in haystack for pattern in patterns)


def history_has_marker(history, document_id):
    """Safety-Net gegen verlorene State-DB: steht unser Marker schon im Ticket?"""
    needle = "| doc:%d]" % document_id
    for comment in (history or {}).get("comments") or []:
        text = (comment.get("content") or "") + (comment.get("title") or "")
        if MARKER_PREFIX in text and needle in text:
            return True
    return False


def format_duration(seconds):
    seconds = int(round(seconds or 0))
    return "%d:%02d" % divmod(seconds, 60)


class Processor:
    """Verdrahtet Client, Whisper, LLM und State fuer je ein Ticket."""

    def __init__(self, cfg, client, transcriber, llm, state,
                 is_multi_company):
        self.cfg = cfg
        self.client = client
        self.transcriber = transcriber
        self.llm = llm
        self.state = state
        self.is_multi_company = is_multi_company

    # -- Hauptablauf

    def process_ticket(self, ticket_id):
        """Verarbeitet ein Ticket vollstaendig; unkritische Fehler landen im Log."""
        documents = self.client.get_documents(ticket_id)
        audio_documents = find_audio_documents(documents,
                                               self.cfg.audio.extensions)
        if not audio_documents:
            LOG.debug("Ticket %d hat keine Audio-Anhaenge.", ticket_id)
            return

        history = self.client.get_ticket_history(ticket_id)
        starface_mail = find_starface_mail(history,
                                           self.cfg.starface.sender_patterns)
        caller_number, voicemail_box = extract_caller_info(starface_mail)

        transcripts = []
        for document in audio_documents:
            document_id = document.get("id")
            if self.state.is_done(document_id):
                LOG.debug("Dokument %s schon verarbeitet (State).", document_id)
                continue
            if history_has_marker(history, document_id):
                LOG.info("Dokument %s traegt schon einen Marker-Kommentar - "
                         "wird nur im State nachgetragen.", document_id)
                self.state.mark_done(document_id, ticket_id, "marker gefunden")
                continue
            try:
                transcript = self._transcribe_document(ticket_id, document)
            except Exception as error:
                retry = self.state.mark_failed(document_id, ticket_id,
                                               str(error))
                LOG.exception("Dokument %s von Ticket %d fehlgeschlagen%s.",
                              document_id, ticket_id,
                              "" if retry else " (endgueltig aufgegeben)")
                continue
            transcripts.append((document, transcript))

        if not transcripts:
            return

        # Zuordnung und LLM laufen einmal pro Ticket, nicht pro Datei.
        assignment = Assignment()
        if (starface_mail and caller_number
                and self.cfg.assignment.enabled):
            assignment = self._assign(caller_number)

        triage_result = None
        if starface_mail and self.llm is not None:
            contacts = []
            if assignment.company_id:
                try:
                    contacts = [
                        {"id": employee.get("id"),
                         "name": employee.get("name")
                                 or ("%s %s" % (employee.get("firstName", ""),
                                                employee.get("lastName", ""))).strip()}
                        for employee in self.client.get_company_employees(
                            assignment.company_id)]
                except Exception as error:
                    LOG.warning("Ansprechpartnerliste fuer Firma %s nicht "
                                "ladbar: %s", assignment.company_id, error)
            combined = "\n\n".join(transcript.text
                                   for _, transcript in transcripts)
            triage_result = run_triage(self.llm, combined, caller_number,
                                       voicemail_box, contacts)

        ticket = self.client.get_ticket(ticket_id)
        update, update_notes = self._build_update(ticket, bool(starface_mail),
                                                  assignment, triage_result)

        for document, transcript in transcripts:
            comment_title, comment_body = self._build_comment(
                document, transcript, starface_mail, caller_number,
                assignment, update_notes, ticket)
            if self.cfg.dry_run:
                LOG.info("[dry-run] Kommentar an Ticket %d:\n%s\n%s",
                         ticket_id, comment_title, comment_body)
            else:
                self.client.post_comment(ticket_id, comment_title,
                                         comment_body,
                                         internal=self.cfg.comment.internal)
                self.state.mark_done(document.get("id"), ticket_id,
                                     "transkribiert")
            LOG.info("Ticket %d: %s transkribiert (%s Audio).", ticket_id,
                     document.get("fileName"),
                     format_duration(transcript.duration))

        if update:
            if self.cfg.dry_run:
                LOG.info("[dry-run] Ticket-Update %d: %s", ticket_id,
                         {key: update[key] for key in
                          ("title", "companyId", "remitterId")
                          if key in update})
            else:
                self.client.update_ticket(ticket_id, update)
                LOG.info("Ticket %d aktualisiert (%s).", ticket_id,
                         ", ".join(update_notes))

    # -- Teilschritte

    def _transcribe_document(self, ticket_id, document):
        """Laedt ein Dokument in ein Temp-Verzeichnis und transkribiert es."""
        name = document.get("fileName") or ("dokument-%s" % document.get("id"))
        with tempfile.TemporaryDirectory(prefix="tanss-triage-") as directory:
            path = os.path.join(directory, os.path.basename(name))
            size = self.client.download_document(ticket_id,
                                                 document.get("id"), path)
            LOG.info("Ticket %d: %s heruntergeladen (%d Bytes), "
                     "transkribiere ...", ticket_id, name, size)
            return self.transcriber.transcribe(path)

    def _assign(self, caller_number):
        try:
            identified = self.client.identify_phone_number(caller_number)
        except Exception as error:
            LOG.warning("Rufnummern-Identifikation fuer %s fehlgeschlagen: %s",
                        caller_number, error)
            return Assignment(note="Rufnummern-Identifikation fehlgeschlagen.")
        return decide_assignment(
            identified, self.is_multi_company,
            assign_remitter=self.cfg.assignment.assign_remitter)

    def _build_update(self, ticket, is_starface, assignment, triage_result):
        """Baut das PUT-Objekt; None, wenn nichts zu aendern ist.

        Nur Starface-Tickets werden angefasst - und auch dort gilt der
        Ueberschreib-Schutz fuer Betreff und Beschreibung.
        """
        if not is_starface:
            return None, []
        update = dict(ticket)
        notes = []

        if triage_result:
            if should_replace_title(ticket.get("title"),
                                    self.cfg.starface.generic_title_pattern):
                update["title"] = triage_result.betreff
                notes.append("Betreff gesetzt")
            else:
                LOG.info("Betreff von Ticket %s wurde schon manuell "
                         "angepasst - bleibt.", ticket.get("id"))
            if should_replace_content(
                    ticket.get("content"),
                    self.cfg.starface.generic_content_patterns):
                update["content"] = triage_result.beschreibung
                notes.append("Beschreibung gesetzt")
            else:
                LOG.info("Beschreibung von Ticket %s ohne Starface-"
                         "Boilerplate - bleibt.", ticket.get("id"))

        if assignment.company_id:
            update["companyId"] = assignment.company_id
            notes.append("Firma %d" % assignment.company_id)
        remitter = assignment.remitter_id
        if (remitter is None and triage_result
                and triage_result.melder_id
                and self.cfg.assignment.assign_remitter):
            remitter = triage_result.melder_id
            # Der LLM-Vorschlag kommt aus der Ansprechpartnerliste der schon
            # zugeordneten Firma - ohne Firma gibt es keine Liste und damit
            # auch keinen Vorschlag.
        if remitter:
            update["remitterId"] = remitter
            notes.append("Melder %d" % remitter)

        return (update, notes) if notes else (None, [])

    def _build_comment(self, document, transcript, starface_mail,
                       caller_number, assignment, update_notes, ticket):
        """Formuliert den Ticket-Kommentar zu einer transkribierten Datei."""
        title = "Transkript: %s" % (document.get("fileName") or "Sprachaufnahme")
        lines = [comment_marker(document.get("id"))]
        meta = ["Datei: %s" % (document.get("fileName") or "?"),
                "Audio: %s min" % format_duration(transcript.duration)]
        if caller_number:
            meta.append("Anrufer: %s" % caller_number)
        lines.append(" | ".join(meta))
        lines.append("")
        lines.append(transcript.text or "(keine Sprache erkannt)")
        if starface_mail:
            lines.append("")
            lines.append("--- Automatische Triage ---")
            if assignment.note:
                lines.append("Zuordnung: %s" % assignment.note)
            if update_notes:
                lines.append("Ticket-Aenderungen: %s" % ", ".join(update_notes))
                if any(note == "Betreff gesetzt" for note in update_notes):
                    lines.append("Urspruenglicher Betreff: %s"
                                 % (ticket.get("title") or ""))
        return title, "\n".join(lines)

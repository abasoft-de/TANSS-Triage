"""
Usage: from tanss_triage.processor import Processor

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: processor.py
Beschreibung: Verarbeitet ein einzelnes Ticket: Audio-Anhänge finden,
              herunterladen, mit Whisper transkribieren, Transkript als
              Kommentar anlegen. Audio kann an zwei Stellen hängen: als
              Ticket-Dokument und - der Starface-Normalfall - als Anhang
              der eingegangenen Mail (in der Datenbank mails_attachments,
              nicht bug_files). Ist es ein Starface-Voicemail-Ticket, kommen
              Rufnummern-Zuordnung (Firma/Melder) und - falls ein LLM
              konfiguriert ist - Betreff und Beschreibung dazu. Der
              Überschreib-Schutz sorgt dafür, dass manuell angepasste
              Tickets nicht angefasst werden: der Betreff wird nur ersetzt,
              solange er noch generisch ist, die Beschreibung nur, solange
              die Starface-Boilerplate darin steht.
Letzte Änderung: 2026-09-15
"""

import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass

from . import __version__
from .assigner import Assignment, decide_assignment
from .tanss_client import TanssApiError
from .triage import run_triage, shorten_subject

LOG = logging.getLogger("tanss_triage.processor")

MARKER_PREFIX = "[TANSS-Triage"

# Ticket-Zuweisung (bug.linkTypID/linkID): auf welches Objekt das Ticket
# zeigt. 2 = Firma (linkID ist die firmenID), 3 = Ansprechpartner (linkID
# ist die mitarbeiterID, immer einer der Ticketfirma).
LINK_TYPE_COMPANY = 2
LINK_TYPE_EMPLOYEE = 3


@dataclass
class AudioSource:
    """Eine transkribierbare Datei: woher sie kommt und wie man sie lädt."""
    key: str            # eindeutig, z. B. "doc:55" oder "mail:356324:x.wav"
    file_name: str
    download: object    # callable(target_path) -> Größe in Bytes


def comment_marker(item_key):
    """Marker im Kommentar; macht Verarbeitung auch ohne State-DB erkennbar."""
    return "%s v%s | %s]" % (MARKER_PREFIX, __version__, item_key)


def is_audio_file(file_name, mime_type, extensions):
    wanted = {extension.lower().lstrip(".") for extension in extensions}
    extension = os.path.splitext(file_name or "")[1].lower().lstrip(".")
    return extension in wanted or (mime_type or "").lower().startswith("audio")


def find_audio_documents(documents, extensions):
    """Filtert die Ticket-Dokumente auf Sprachaufnahmen.

    Es zählt die Dateiendung ODER ein audio/*-MIME-Typ - das Starface-Mail
    hängt neben der WAV auch Inline-Bilder an (SF_M_IMG_0), die hier
    rausfallen.
    """
    return [document for document in documents or []
            if is_audio_file(document.get("fileName"),
                             document.get("mimeType"), extensions)]


def mail_attachment_name(attachment):
    """Dateiname eines Mail-Anhangs, tolerant gegen Feldnamens-Varianten."""
    for field in ("filename", "fileName", "name"):
        value = attachment.get(field)
        if isinstance(value, str) and value:
            return value
    return ""


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


def extract_phone_number(text):
    """Letzte Rufnummer in einem Text.

    Zuerst zusammenhängende Ziffernfolgen: Starface nennt die Nummer im
    Betreff teils doppelt ("von 0157... 0157... in ...") - ein Muster, das
    Leerzeichen in der Nummer erlaubt, würde beide zu einer verschmelzen.
    Erst wenn nichts Zusammenhängendes da ist, sind Trennzeichen (ohne
    Leerzeichen) erlaubt, etwa 06154/6006-0.
    """
    contiguous = re.findall(r"\+?\d{6,}", text or "")
    if contiguous:
        return contiguous[-1]
    grouped = re.findall(r"\+?\d[\d/().-]{4,}\d", text or "")
    if grouped:
        return re.sub(r"[^\d+]", "", grouped[-1])
    return ""


def normalize_phone_number(number, country_code="49"):
    """Bringt 0049... / +49... auf die nationale Schreibweise 0...

    Starface liefert Anrufernummern international (0049702451366), TANSS
    pflegt sie überwiegend national (07024/51366) - und der
    identify-Endpunkt normalisiert das 0049-Präfix nicht selbst.
    Ausländische Nummern (anderes Präfix) bleiben unverändert.
    """
    if not number:
        return number
    for prefix in ("00" + country_code, "+" + country_code):
        if number.startswith(prefix) and len(number) > len(prefix):
            return "0" + number[len(prefix):]
    return number


def extract_caller_info(mail):
    """Zieht Anrufernummer und Voicemail-Box aus der Starface-Mail.

    Betreff: "Sie haben eine Sprachnachricht von <Name?> <Nummer> in <Box>
    erhalten" - vor der Nummer kann ein aufgelöster Name samt Klammern
    stehen, darum gilt: die letzte Rufnummer vor " in " zählt. Fällt der
    Betreff aus, hilft der Mailtext ("Sprachmitteilung von <Nummer>",
    "Voicemail-Box <Box> der STARFACE").
    """
    subject = (mail or {}).get("subject") or ""
    body = (mail or {}).get("bodyPlain") or ""

    number = ""
    box = ""
    match = re.search(r"von\s+(.*?)\s+in\s+(.+?)\s+erhalten", subject)
    if match:
        number = extract_phone_number(match.group(1))
        box = match.group(2).strip()
    if not number:
        match = re.search(r"Sprachmitteilung von([^\n]*)", body)
        if match:
            number = extract_phone_number(match.group(1))
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


def history_has_marker(history, item_key):
    """Safety-Net gegen verlorene State-DB: steht unser Marker schon im Ticket?"""
    needle = "| %s]" % item_key
    for comment in (history or {}).get("comments") or []:
        text = (comment.get("content") or "") + (comment.get("title") or "")
        if MARKER_PREFIX in text and needle in text:
            return True
    return False


def format_duration(seconds):
    seconds = int(round(seconds or 0))
    return "%d:%02d" % divmod(seconds, 60)


def ticket_url(base_url, ticket_id, company_id=0):
    """Aufrufbarer TANSS-Link zum Ticket.

    Format aus der Wissensbasis: neben der bugID gehört die firmenID als
    neueFirma in den Link - TANSS wechselt damit gleichzeitig den
    angezeigten Kunden, sodass man von dort weiter recherchieren kann.
    Die API kann hinter /backend liegen, die Oberfläche nicht.
    """
    base = (base_url or "").rstrip("/")
    if base.endswith("/backend"):
        base = base[:-len("/backend")]
    return ("%s/index.php?section=bug&sub=view&neueFirma=%d&bugID=%d"
            % (base, int(company_id or 0), int(ticket_id)))


class Processor:
    """Verdrahtet Client, Whisper, LLM und State für je ein Ticket."""

    def __init__(self, cfg, client, transcriber, llm, state,
                 is_multi_company, mail_attachments_lookup=None,
                 labels_lookup=None, phone_roles_lookup=None):
        self.cfg = cfg
        self.client = client
        self.transcriber = transcriber
        self.llm = llm
        self.state = state
        self.is_multi_company = is_multi_company
        # mail_id -> Liste aus mails_attachments oder None (DB nicht da)
        self.mail_attachments_lookup = (mail_attachments_lookup
                                        or (lambda mail_id: None))
        # (company_id, employee_id) -> (kubez, person) für die
        # Zuordnungszeile; None-Werte lösen die Fallbacks aus.
        self.labels_lookup = (labels_lookup
                              or (lambda company_id, employee_id: (None, None)))
        # (nummer, company_id) -> (ist_firmennummer, [mitarbeiter_ids])
        # oder None; klärt Zentrale-vs-Durchwahl für die flache
        # identify-Antwort.
        self.phone_roles_lookup = (phone_roles_lookup
                                   or (lambda number, company_id: None))

    # -- Hauptablauf

    def process_ticket(self, ticket_id):
        """Verarbeitet ein Ticket vollständig; unkritische Fehler landen im Log."""
        history = self.client.get_ticket_history(ticket_id)
        sources = self._collect_audio_sources(ticket_id, history)
        if not sources:
            LOG.info("Ticket %d hat keine Audio-Anhänge (weder als Dokument "
                     "noch an einer Mail).", ticket_id)
            return

        starface_mail = find_starface_mail(history,
                                           self.cfg.starface.sender_patterns)
        caller_number, voicemail_box = extract_caller_info(starface_mail)

        transcripts = []
        for source in sources:
            if self.state.is_done(source.key):
                LOG.debug("%s schon verarbeitet (State).", source.key)
                continue
            if history_has_marker(history, source.key):
                LOG.info("%s trägt schon einen Marker-Kommentar - wird nur "
                         "im State nachgetragen.", source.key)
                self.state.mark_done(source.key, ticket_id, "Marker gefunden")
                continue
            try:
                transcript = self._transcribe_source(ticket_id, source)
            except Exception as error:
                retry = self.state.mark_failed(source.key, ticket_id,
                                               str(error))
                LOG.exception("%s von Ticket %d fehlgeschlagen%s.",
                              source.key, ticket_id,
                              "" if retry else " (endgültig aufgegeben)")
                continue
            transcripts.append((source, transcript))

        if not transcripts:
            LOG.info("Ticket %d: nichts zu tun (alles schon verarbeitet "
                     "oder fehlgeschlagen).", ticket_id)
            return

        # Zuordnung und LLM laufen einmal pro Ticket, nicht pro Datei.
        assignment = Assignment()
        if (starface_mail and caller_number
                and self.cfg.assignment.enabled):
            assignment = self._assign(caller_number)

        combined_transcript = "\n\n".join(
            transcript.text for _, transcript in transcripts
            if transcript.text).strip()

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
                    LOG.warning("Ansprechpartnerliste für Firma %s nicht "
                                "ladbar: %s",
                                self._company_label(assignment.company_id,
                                                    assignment), error)
            triage_result = run_triage(
                self.llm, combined_transcript, caller_number, voicemail_box,
                contacts,
                extra_abbreviations=self.cfg.llm.subject_abbreviations_extra)

        ticket = self.client.get_ticket(ticket_id)
        update, update_notes = self._build_update(ticket, bool(starface_mail),
                                                  assignment, triage_result,
                                                  combined_transcript)

        for source, transcript in transcripts:
            comment_title, comment_body = self._build_comment(
                source, transcript, starface_mail, assignment)
            if self.cfg.dry_run:
                LOG.info("[dry-run] Kommentar an Ticket %d:\n%s\n%s",
                         ticket_id, comment_title, comment_body)
            else:
                self.client.post_comment(ticket_id, comment_title,
                                         comment_body,
                                         internal=self.cfg.comment.internal)
                self.state.mark_done(source.key, ticket_id, "transkribiert")
            LOG.info("Ticket %d: %s transkribiert (%s Audio).", ticket_id,
                     source.file_name, format_duration(transcript.duration))

        if update:
            if self.cfg.dry_run:
                LOG.info("[dry-run] Ticket-Update %d: %s", ticket_id,
                         ", ".join(update_notes))
                if "title" in update:
                    LOG.info("[dry-run] Neuer Betreff: %s", update["title"])
            else:
                self._apply_update(ticket_id, update, update_notes)
        if (self._is_outage(triage_result, bool(starface_mail))
                and self.cfg.outage.tag_id):
            self._tag_urgent(ticket_id)

        # Letzte Zeile zum Ticket: der Link, damit man aus dem Journal
        # heraus direkt hinspringen kann.
        LOG.info("Ticket %d: %s", ticket_id,
                 ticket_url(self.cfg.tanss.base_url, ticket_id,
                            (update or {}).get("companyId")
                            or ticket.get("companyId")))

    # -- Teilschritte

    def _apply_update(self, ticket_id, update, notes):
        """Schreibt das Ticket-Update; nach einem Fehler wird nachgelesen.

        TANSS (Stand 2026-09) quittiert ein PUT, das den Betreff ändert,
        mit HTTP 400 RUNTIME_EXCEPTION - gespeichert wird die Änderung
        trotzdem, samt Historieneinträgen. Statt ein erledigtes Update als
        Fehler zu melden, wird das Ticket deshalb noch einmal gelesen:
        stehen die gewünschten Werte drin, gilt das Update als geglückt und
        die falsche Fehlermeldung bleibt draußen (sie steht nur auf DEBUG,
        damit sie bei Bedarf nachvollziehbar ist). Sonst fliegt der Fehler
        weiter.
        """
        try:
            self.client.update_ticket(ticket_id, update)
        except TanssApiError as error:
            if not self._update_took_effect(ticket_id, update):
                raise
            LOG.debug("Ticket %d: TANSS quittierte das wirksame Update mit "
                      "einem Fehler: %s", ticket_id, error)
        LOG.info("Ticket %d aktualisiert (%s).", ticket_id, ", ".join(notes))

    def _update_took_effect(self, ticket_id, update):
        """Stehen die geschriebenen Werte im Ticket? Nur die, die zählen."""
        try:
            fresh = self.client.get_ticket(ticket_id)
        except Exception as error:
            LOG.info("Ticket %d nach dem Fehler nicht erneut lesbar: %s",
                     ticket_id, error)
            return False

        def normalized(value):
            return " ".join(str(value or "").split())

        for key in ("title", "content"):
            if key in update and normalized(fresh.get(key)) != normalized(
                    update[key]):
                return False
        for key in ("companyId", "remitterId", "linkTypeId", "linkId",
                    "assignedToDepartmentId", "dueDate", "deadlineDate"):
            if key in update and fresh.get(key) != update[key]:
                return False
        return True

    def _collect_audio_sources(self, ticket_id, history):
        """Sammelt alle Audio-Dateien eines Tickets.

        Zwei Quellen: die Ticket-Dokumente und die Anhänge der Mails aus
        der Historie - Starface hängt die WAV an die Mail, nicht ans
        Ticket, darum reicht der Dokumente-Endpunkt allein nicht.
        """
        sources = []
        for document in find_audio_documents(
                self.client.get_documents(ticket_id),
                self.cfg.audio.extensions):
            sources.append(AudioSource(
                key="doc:%d" % document.get("id"),
                file_name=document.get("fileName") or "?",
                download=lambda path, d=document: self.client.download_document(
                    ticket_id, d.get("id"), path)))

        for mail in (history or {}).get("mails") or []:
            mail_id = mail.get("id")
            if not mail_id:
                continue
            sources.extend(self._mail_audio_sources(ticket_id, mail_id))
        return sources

    def _mail_audio_sources(self, ticket_id, mail_id):
        """Audio-Anhänge einer Mail.

        Bevorzugt über die Datenbank (mails_attachments) plus direkten
        Dateizugriff im Storage des TANSS-Servers - das ist der verlässliche
        Weg, denn die Ablage ist <verzeichnis>/<filenameDB>. Nur wenn die
        DB nicht verfügbar ist, wird die API befragt.
        """
        rows = self.mail_attachments_lookup(mail_id)
        if rows is not None:
            return self._sources_from_storage(mail_id, rows)

        try:
            detail = self.client.get_mail(mail_id)
        except Exception as error:
            LOG.warning("Mail %s von Ticket %d nicht ladbar: %s",
                        mail_id, ticket_id, error)
            return []
        sources = []
        for attachment in detail.get("attachments") or []:
            name = mail_attachment_name(attachment)
            mime = attachment.get("mimeType") or attachment.get("type")
            if not is_audio_file(name, mime, self.cfg.audio.extensions):
                continue
            sources.append(AudioSource(
                key="mail:%d:%s" % (mail_id, name),
                file_name=name,
                download=lambda path, m=mail_id, a=attachment:
                    self.client.download_mail_attachment(m, a, path)))
        return sources

    def _sources_from_storage(self, mail_id, rows):
        """Baut Audio-Quellen aus mails_attachments-Zeilen (Dateisystem)."""
        storage_dir = self.cfg.storage.resolved_dir()
        sources = []
        for row in rows:
            name = row.get("filename") or ""
            if not is_audio_file(name, None, self.cfg.audio.extensions):
                continue
            stored = os.path.join(storage_dir, row.get("verzeichnis") or "",
                                  row.get("filenameDB") or "")

            def copy_from_storage(path, stored=stored):
                if not storage_dir:
                    raise RuntimeError("[storage] mail_attachments_dir ist "
                                       "leer - kein Dateizugriff möglich.")
                if not os.path.isfile(stored):
                    raise FileNotFoundError(
                        "Mail-Anhang nicht im Storage: %s" % stored)
                shutil.copyfile(stored, path)
                return os.path.getsize(path)

            sources.append(AudioSource(
                key="mail:%d:%s" % (mail_id, name),
                file_name=name,
                download=copy_from_storage))
        return sources

    def _transcribe_source(self, ticket_id, source):
        """Lädt eine Audio-Quelle in ein Temp-Verzeichnis und transkribiert sie."""
        with tempfile.TemporaryDirectory(prefix="tanss-triage-") as directory:
            path = os.path.join(directory,
                                os.path.basename(source.file_name or "audio"))
            size = source.download(path)
            LOG.info("Ticket %d: %s heruntergeladen (%d Bytes), "
                     "transkribiere ...", ticket_id, source.file_name, size)
            return self.transcriber.transcribe(path)

    def _assign(self, caller_number):
        """Rufnummern-Zuordnung; probiert nationale UND internationale Form.

        Zuerst die auf 0... normalisierte Schreibweise - das ist der
        Normalfall, denn Starface liefert 0049..., TANSS pflegt national
        und identify gleicht das Präfix nicht selbst an. Nur wenn dabei
        nichts gefunden wird, noch die Nummer wie geliefert.
        """
        normalized = normalize_phone_number(caller_number)
        candidates = [normalized]
        if caller_number != normalized:
            candidates.append(caller_number)

        assignment = Assignment(note="Rufnummern-Identifikation "
                                     "fehlgeschlagen.")
        for candidate in candidates:
            try:
                identified = self.client.identify_phone_number(candidate)
            except Exception as error:
                LOG.warning("Rufnummern-Identifikation für %s "
                            "fehlgeschlagen: %s", candidate, error)
                continue
            assignment = decide_assignment(
                identified, self.is_multi_company,
                assign_remitter=self.cfg.assignment.assign_remitter,
                phone_roles=lambda company_id, number=candidate:
                    self.phone_roles_lookup(number, company_id),
                employee_label=lambda employee_id:
                    self.labels_lookup(None, employee_id)[1])
            if assignment.has_change:
                if candidate != normalized:
                    LOG.info("Rufnummer %s erst in der gelieferten "
                             "Schreibweise %s gefunden.", normalized,
                             candidate)
                break
        return assignment

    def _build_update(self, ticket, is_starface, assignment, triage_result,
                      transcript_text=""):
        """Baut das PUT-Objekt; None, wenn nichts zu ändern ist.

        Nur Starface-Tickets werden angefasst - und auch dort gilt der
        Überschreib-Schutz für Betreff und Beschreibung. Ohne LLM wird die
        Boilerplate-Beschreibung durch das nackte Transkript ersetzt.
        """
        if not is_starface:
            return None, []
        update = dict(ticket)
        notes = []

        content_replaceable = should_replace_content(
            ticket.get("content"), self.cfg.starface.generic_content_patterns)
        if triage_result:
            if should_replace_title(ticket.get("title"),
                                    self.cfg.starface.generic_title_pattern):
                update["title"] = self._title_for(triage_result)
                notes.append("Betreff gesetzt")
            else:
                LOG.info("Betreff von Ticket %s wurde schon manuell "
                         "angepasst - bleibt.", ticket.get("id"))
            if content_replaceable:
                update["content"] = triage_result.beschreibung
                notes.append("Beschreibung gesetzt")
            else:
                LOG.info("Beschreibung von Ticket %s ohne Starface-"
                         "Boilerplate - bleibt.", ticket.get("id"))
        elif transcript_text and content_replaceable:
            # Kein (erfolgreicher) LLM-Lauf: statt der Starface-Boilerplate
            # soll wenigstens das Transkript in der Beschreibung stehen.
            update["content"] = transcript_text
            notes.append("Beschreibung durch Transkript ersetzt")

        if assignment.company_id:
            update["companyId"] = assignment.company_id
            notes.append("Firma %s" % self._company_label(assignment.company_id,
                                                          assignment))
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
            notes.append("Melder %s" % self._person_label(remitter,
                                                          assignment))

        # Die Zuweisung (linkTypeId/linkId) gehört zur Zuordnung dazu; ohne
        # sie steht das Ticket zwar bei der Firma, ist aber keinem Objekt
        # zugewiesen. Kennen wir den Ansprechpartner, zeigt sie auf ihn,
        # sonst auf die Firma. Eine vorhandene Zuweisung bleibt unangetastet -
        # die hat jemand von Hand gewählt.
        if not ticket.get("linkTypeId"):
            if remitter:
                update["linkTypeId"] = LINK_TYPE_EMPLOYEE
                update["linkId"] = remitter
                notes.append("Zuweisung Ansprechpartner")
            elif assignment.company_id:
                update["linkTypeId"] = LINK_TYPE_COMPANY
                update["linkId"] = assignment.company_id
                notes.append("Zuweisung Firma")

        self._set_department(ticket, update, notes)
        if self._is_outage(triage_result, is_starface):
            # Fälligkeit und Deadline "jetzt": das Ticket steht sofort oben
            # in jeder Fälligkeitsansicht.
            now = int(time.time())
            update["dueDate"] = now
            update["deadlineDate"] = now
            note = "Praxisausfall: Fälligkeit und Deadline jetzt"
            if self.cfg.outage.tag_id:
                note += ", Tag %s" % (self.cfg.outage.tag_name
                                      or "#%d" % self.cfg.outage.tag_id)
            notes.append(note)

        return (update, notes) if notes else (None, [])

    def _is_outage(self, triage_result, is_starface):
        """Praxisausfall laut LLM - und die Sonderbehandlung ist an."""
        return bool(is_starface and triage_result
                    and triage_result.praxisausfall
                    and self.cfg.outage.enabled)

    def _title_for(self, triage_result):
        """Neuer Betreff; bei Praxisausfall mit Präfix, in der Feldlänge."""
        if self._is_outage(triage_result, True):
            return shorten_subject(self.cfg.outage.title_prefix
                                   + triage_result.betreff)
        return triage_result.betreff

    def _set_department(self, ticket, update, notes):
        """Weist das Ticket der HLE zu, solange es keine Abteilung hat.

        Alle Starface-Tickets landen bei der HLE (Vorgabe) - sie ist gut
        besetzt und verteilt selbst weiter. Eine schon zugewiesene Abteilung
        hat jemand von Hand gewählt; die bleibt.
        """
        department_id = self.cfg.departments.id
        if not self.cfg.departments.enabled or not department_id:
            return
        if ticket.get("assignedToDepartmentId"):
            return
        update["assignedToDepartmentId"] = department_id
        notes.append("Abteilung %s" % (self.cfg.departments.name
                                      or "#%d" % department_id))

    def _tag_urgent(self, ticket_id):
        """Hängt den Praxisausfall-Tag an; vorhandene Tags bleiben stehen.

        Ein Fehler hier ist kein Grund, die Verarbeitung abzubrechen - der
        Rest des Tickets ist dann schon geschrieben.
        """
        tag_id = self.cfg.outage.tag_id
        if self.cfg.dry_run:
            LOG.info("[dry-run] Ticket %d: Tag %d würde gesetzt.",
                     ticket_id, tag_id)
            return
        try:
            present = {tag.get("id") for tag in
                       self.client.get_ticket_tags(ticket_id) or []}
            if tag_id in present:
                return
            self.client.add_ticket_tag(ticket_id, tag_id)
        except Exception as error:
            LOG.warning("Ticket %d: Tag %d ließ sich nicht setzen: %s",
                        ticket_id, tag_id, error)

    def _company_label(self, company_id, assignment=None):
        """KUBEZ der Firma für Log und Notizen; notfalls die nackte ID.

        Eine Firmen-ID sagt niemandem etwas, der ins Journal schaut - die
        Kurzbezeichnung (firmen.displayID) dagegen sofort.
        """
        if not company_id:
            return ""
        kubez, _ = self.labels_lookup(company_id, None)
        return (kubez or (assignment.company_label if assignment else "")
                or "#%s" % company_id)

    def _person_label(self, employee_id, assignment=None):
        """Name des Ansprechpartners für Log und Notizen; notfalls die ID."""
        if not employee_id:
            return ""
        _, person = self.labels_lookup(None, employee_id)
        if not person and assignment and assignment.remitter_id == employee_id:
            person = assignment.remitter_label
        return person or "#%s" % employee_id

    def _assignment_line(self, assignment):
        """Die Zuordnungszeile des Kommentars.

        Firma gefunden: "Zuordnung: <KUBEZ>", mit Melder zusätzlich
        " | Anrede Titel Vorname Nachname (Rolle)". Keine Firma: der
        Klartext-Grund aus der Zuordnungsentscheidung.
        """
        if assignment.company_id is None:
            return "Zuordnung: %s" % (assignment.note
                                      or "keine automatische Zuordnung.")
        kubez, person = self.labels_lookup(assignment.company_id,
                                           assignment.remitter_id)
        kubez = (kubez or assignment.company_label
                 or "Firma #%d" % assignment.company_id)
        if assignment.remitter_id:
            person = person or assignment.remitter_label
            if person:
                return "Zuordnung: %s | %s" % (kubez, person)
        return "Zuordnung: %s" % kubez

    def _build_comment(self, source, transcript, starface_mail, assignment):
        """Formuliert den Ticket-Kommentar zu einer transkribierten Datei.

        Bewusst ohne Marker (Vorgabe): gegen Doppelverarbeitung schützt
        allein die State-DB; Marker aus älteren Versionen werden beim
        Lesen der Historie weiterhin erkannt.
        """
        title = "Transkript: %s" % (source.file_name or "Sprachaufnahme")
        lines = []
        if starface_mail:
            lines.append(self._assignment_line(assignment))
            lines.append("")
        lines.append(transcript.text or "(keine Sprache erkannt)")
        return title, "\n".join(lines)

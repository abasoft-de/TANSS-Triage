"""
Usage: from tanss_triage.poller import Poller

Autor: SO, (c) abasoft GmbH 2026-09-16
Datei: poller.py
Beschreibung: Findet neue Tickets mit Sprachaufnahmen durch minütliches
              Pollen der TANSS-Datenbank (die Webhook-Regeln von TANSS sind
              für diesen Anwendungsfall zu eingeschränkt). Zwei Quellen mit
              je einem persistierten ID-Cursor: neue Mail-Anhänge
              (mails_attachments, erfasst auch Voicemails an bestehende
              Tickets) und neue Ticket-Dokumente (bug_files). Weil die
              Cursor in der State-DB liegen, holt ein Neustart verpasste
              Zeit automatisch nach; beim allerersten Start greift ein
              konfigurierbarer Rückblick.
Letzte Änderung: 2026-09-16
"""

import logging

from . import db
from .processor import is_audio_file

LOG = logging.getLogger("tanss_triage.poller")

CURSOR_MAILS = "mail_attachments_max_id"
CURSOR_FILES = "bug_files_max_id"


class Poller:
    def __init__(self, cfg, state, enqueue):
        """enqueue ist queue.put des Workers (nimmt Ticket-IDs)."""
        self.cfg = cfg
        self.state = state
        self.enqueue = enqueue
        self._db_was_down = False

    def tick(self):
        """Ein Poll-Zyklus; Fehler beenden nie den Betrieb."""
        tickets = set()
        ok = self._poll_mail_attachments(tickets)
        ok = self._poll_bug_files(tickets) and ok

        if not ok:
            if not self._db_was_down:
                LOG.warning("TANSS-Datenbank nicht erreichbar - Polling "
                            "setzt aus, bis sie wieder da ist.")
                self._db_was_down = True
            return
        if self._db_was_down:
            LOG.info("TANSS-Datenbank wieder erreichbar - Polling läuft.")
            self._db_was_down = False

        for ticket_id in sorted(tickets):
            LOG.info("Polling: neues Audio in Ticket %d.", ticket_id)
            self.enqueue(ticket_id)

    # -- Quellen

    def _poll_mail_attachments(self, tickets):
        """Neue Mail-Anhänge seit dem letzten Cursor-Stand."""
        max_id = db.max_mail_attachment_id(self.cfg.db)
        if max_id is None:
            return False
        cursor = self.state.get_cursor(CURSOR_MAILS)
        if cursor is None:
            # Allererster Lauf: kurzer Rückblick statt Komplett-Historie.
            rows = db.backlog_audio_tickets(
                self.cfg.db, self.cfg.polling.initial_lookback_minutes,
                self.cfg.audio.extensions)
            if rows is None:
                return False
            tickets.update(row["ticket_id"] for row in rows)
        elif max_id > cursor:
            rows = db.audio_mail_tickets(self.cfg.db, cursor, max_id,
                                         self.cfg.audio.extensions)
            if rows is None:
                return False
            tickets.update(row["ticket_id"] for row in rows)
        self.state.set_cursor(CURSOR_MAILS, max_id)
        return True

    def _poll_bug_files(self, tickets):
        """Neue Ticket-Dokumente seit dem letzten Cursor-Stand."""
        max_id = db.max_bug_file_id(self.cfg.db)
        if max_id is None:
            return False
        cursor = self.state.get_cursor(CURSOR_FILES)
        if cursor is None:
            # Ticket-Dokumente mit Audio gab es historisch nicht - der
            # erste Lauf setzt nur den Cursor.
            self.state.set_cursor(CURSOR_FILES, max_id)
            return True
        if max_id > cursor:
            rows = db.bug_files_between(self.cfg.db, cursor, max_id)
            if rows is None:
                return False
            for row in rows:
                if is_audio_file(row.get("file_name"), row.get("mime_type"),
                                 self.cfg.audio.extensions):
                    tickets.add(row["ticket_id"])
        self.state.set_cursor(CURSOR_FILES, max_id)
        return True

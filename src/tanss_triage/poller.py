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
              Für die Duplikat-Erkennung kommen zwei weitere Quellen dazu:
              neue Tickets, die schon bei einer Firma liegen (bug), und
              Firmenzuweisungen aus dem Änderungsprotokoll
              (entity_changes) - hier setzt der erste Lauf nur die Cursor,
              damit ein Deployment keine Alttickets markiert.
Letzte Änderung: 2026-11-16
"""

import logging

from . import db
from .duplicates import KEY_PREFIX as DUPLICATE_KEY_PREFIX
from .processor import is_audio_file

LOG = logging.getLogger("tanss_triage.poller")

CURSOR_MAILS = "mail_attachments_max_id"
CURSOR_FILES = "bug_files_max_id"
CURSOR_TICKETS = "bug_max_id"
CURSOR_CHANGES = "entity_changes_max_id"

# Wartezeit, bevor eine fehlgeschlagene Duplikatprüfung erneut eingereiht
# wird - ein LLM-Ausfall dauert selten nur eine Minute.
DUPLICATE_RETRY_SECONDS = 600


class Poller:
    def __init__(self, cfg, state, enqueue, enqueue_duplicate=None):
        """enqueue reiht ein Ticket zur Audio-Verarbeitung ein,
        enqueue_duplicate zur Duplikatprüfung (None = Erkennung ruht);
        beide nehmen Ticket-IDs."""
        self.cfg = cfg
        self.state = state
        self.enqueue = enqueue
        self.enqueue_duplicate = enqueue_duplicate
        self._db_was_down = False

    @property
    def _duplicates_active(self):
        return bool(self.enqueue_duplicate is not None
                    and self.cfg.duplicates.enabled)

    def tick(self):
        """Ein Poll-Zyklus; Fehler beenden nie den Betrieb."""
        tickets = set()
        duplicate_tickets = set()
        ok = self._poll_mail_attachments(tickets)
        ok = self._poll_bug_files(tickets) and ok
        if self._duplicates_active:
            ok = self._poll_new_tickets(duplicate_tickets) and ok
            ok = self._poll_company_changes(duplicate_tickets) and ok
            duplicate_tickets.update(self.state.failed_tickets(
                DUPLICATE_KEY_PREFIX, DUPLICATE_RETRY_SECONDS))

        if not ok:
            if not self._db_was_down:
                LOG.warning("TANSS-Datenbank nicht (vollständig) erreichbar "
                            "- betroffene Abfragen setzen aus, bis sie "
                            "wieder da ist.")
                self._db_was_down = True
        elif self._db_was_down:
            LOG.info("TANSS-Datenbank wieder erreichbar - Polling läuft.")
            self._db_was_down = False

        # Eingereiht wird, was gefunden wurde - auch wenn eine andere
        # Abfrage scheiterte: ihr Cursor ist stehen geblieben, die der
        # geglückten sind schon weiter.
        for ticket_id in sorted(tickets):
            LOG.info("Polling: neues Audio in Ticket %d.", ticket_id)
            self.enqueue(ticket_id)
        # Audio zuerst: die Duplikatprüfung soll den fertigen Betreff sehen.
        for ticket_id in sorted(duplicate_tickets):
            LOG.debug("Polling: Duplikatprüfung für Ticket %d.", ticket_id)
            self.enqueue_duplicate(ticket_id)

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

    def _poll_new_tickets(self, tickets):
        """Neue Tickets, die schon bei einer Firma liegen (Duplikatprüfung)."""
        max_id = db.max_bug_id(self.cfg.db)
        if max_id is None:
            return False
        cursor = self.state.get_cursor(CURSOR_TICKETS)
        if cursor is not None and max_id > cursor:
            rows = db.company_tickets_between(self.cfg.db, cursor, max_id)
            if rows is None:
                return False
            tickets.update(row["ticket_id"] for row in rows)
        self.state.set_cursor(CURSOR_TICKETS, max_id)
        return True

    def _poll_company_changes(self, tickets):
        """Tickets, denen laut Änderungsprotokoll eine Firma zugewiesen wurde."""
        max_id = db.max_entity_change_id(self.cfg.db)
        if max_id is None:
            return False
        cursor = self.state.get_cursor(CURSOR_CHANGES)
        if cursor is not None and max_id > cursor:
            rows = db.company_changes_between(self.cfg.db, cursor, max_id)
            if rows is None:
                return False
            tickets.update(row["ticket_id"] for row in rows)
        self.state.set_cursor(CURSOR_CHANGES, max_id)
        return True

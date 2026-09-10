"""
Usage: from tanss_triage.state import State

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: state.py
Beschreibung: Merkt sich in einer SQLite-Datei, welche Ticket-Dokumente schon
              verarbeitet wurden, damit doppelte Webhooks (TICKET_CREATED und
              EMAIL_RECEIVED feuern für dasselbe Ticket) und Neustarts keine
              doppelten Kommentare erzeugen. Fehlversuche werden gezählt,
              damit eine kaputte Datei nicht endlos wiederholt wird.
Letzte Änderung: 2026-09-10
"""

import sqlite3
import threading
import time

MAX_ATTEMPTS = 3


class State:
    """SQLite-Zustand; ein Lock genügt, es schreibt nur der Worker."""

    def __init__(self, db_path):
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(db_path, check_same_thread=False)
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                document_id  INTEGER PRIMARY KEY,
                ticket_id    INTEGER NOT NULL,
                processed_at INTEGER,
                status       TEXT NOT NULL DEFAULT 'pending',
                attempts     INTEGER NOT NULL DEFAULT 0,
                detail       TEXT
            )""")
        self._connection.commit()

    def is_done(self, document_id):
        with self._lock:
            row = self._connection.execute(
                "SELECT status FROM documents WHERE document_id = ?",
                (document_id,)).fetchone()
        return bool(row) and row[0] == "done"

    def attempts(self, document_id):
        with self._lock:
            row = self._connection.execute(
                "SELECT attempts FROM documents WHERE document_id = ?",
                (document_id,)).fetchone()
        return row[0] if row else 0

    def mark_done(self, document_id, ticket_id, detail=""):
        self._write(document_id, ticket_id, "done", detail)

    def mark_failed(self, document_id, ticket_id, detail=""):
        """Zählt einen Fehlversuch; liefert True, solange Wiederholen erlaubt ist."""
        with self._lock:
            self._connection.execute("""
                INSERT INTO documents (document_id, ticket_id, processed_at,
                                       status, attempts, detail)
                VALUES (?, ?, ?, 'failed', 1, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    processed_at = excluded.processed_at,
                    status = 'failed',
                    attempts = documents.attempts + 1,
                    detail = excluded.detail
                """, (document_id, ticket_id, int(time.time()), detail[:500]))
            self._connection.commit()
            row = self._connection.execute(
                "SELECT attempts FROM documents WHERE document_id = ?",
                (document_id,)).fetchone()
        return row[0] < MAX_ATTEMPTS

    def _write(self, document_id, ticket_id, status, detail):
        with self._lock:
            self._connection.execute("""
                INSERT INTO documents (document_id, ticket_id, processed_at,
                                       status, attempts, detail)
                VALUES (?, ?, ?, ?, 0, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    processed_at = excluded.processed_at,
                    status = excluded.status,
                    detail = excluded.detail
                """, (document_id, ticket_id, int(time.time()), status,
                      detail[:500]))
            self._connection.commit()

    def close(self):
        with self._lock:
            self._connection.close()

"""
Usage: from tanss_triage.state import State

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: state.py
Beschreibung: Merkt sich in einer SQLite-Datei, welche Audio-Quellen schon
              verarbeitet wurden, damit doppelte Webhooks (TICKET_CREATED und
              EMAIL_RECEIVED feuern für dasselbe Ticket) und Neustarts keine
              doppelten Kommentare erzeugen. Schlüssel sind Texte wie
              "doc:55" (Ticket-Dokument) oder "mail:356324:voicemail.wav"
              (Mail-Anhang, der keine eigene numerische ID hat).
              Fehlversuche werden gezählt, damit eine kaputte Datei nicht
              endlos wiederholt wird.
Letzte Änderung: 2026-09-15
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
            CREATE TABLE IF NOT EXISTS processed (
                item_key     TEXT PRIMARY KEY,
                ticket_id    INTEGER NOT NULL,
                processed_at INTEGER,
                status       TEXT NOT NULL DEFAULT 'pending',
                attempts     INTEGER NOT NULL DEFAULT 0,
                detail       TEXT
            )""")
        self._migrate_legacy()
        self._connection.commit()

    def _migrate_legacy(self):
        """Übernimmt Zeilen aus der alten Tabelle documents (bis 0.1.3).

        Dort waren die Schlüssel numerische Dokument-IDs; sie werden als
        "doc:<id>" weitergeführt, danach fällt die alte Tabelle weg.
        """
        row = self._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'documents'").fetchone()
        if not row:
            return
        self._connection.execute("""
            INSERT OR IGNORE INTO processed
            SELECT 'doc:' || document_id, ticket_id, processed_at,
                   status, attempts, detail
            FROM documents""")
        self._connection.execute("DROP TABLE documents")

    def is_done(self, item_key):
        with self._lock:
            row = self._connection.execute(
                "SELECT status FROM processed WHERE item_key = ?",
                (str(item_key),)).fetchone()
        return bool(row) and row[0] == "done"

    def attempts(self, item_key):
        with self._lock:
            row = self._connection.execute(
                "SELECT attempts FROM processed WHERE item_key = ?",
                (str(item_key),)).fetchone()
        return row[0] if row else 0

    def mark_done(self, item_key, ticket_id, detail=""):
        self._write(item_key, ticket_id, "done", detail)

    def mark_failed(self, item_key, ticket_id, detail=""):
        """Zählt einen Fehlversuch; liefert True, solange Wiederholen erlaubt ist."""
        with self._lock:
            self._connection.execute("""
                INSERT INTO processed (item_key, ticket_id, processed_at,
                                       status, attempts, detail)
                VALUES (?, ?, ?, 'failed', 1, ?)
                ON CONFLICT(item_key) DO UPDATE SET
                    processed_at = excluded.processed_at,
                    status = 'failed',
                    attempts = processed.attempts + 1,
                    detail = excluded.detail
                """, (str(item_key), ticket_id, int(time.time()),
                      detail[:500]))
            self._connection.commit()
            row = self._connection.execute(
                "SELECT attempts FROM processed WHERE item_key = ?",
                (str(item_key),)).fetchone()
        return row[0] < MAX_ATTEMPTS

    def _write(self, item_key, ticket_id, status, detail):
        with self._lock:
            self._connection.execute("""
                INSERT INTO processed (item_key, ticket_id, processed_at,
                                       status, attempts, detail)
                VALUES (?, ?, ?, ?, 0, ?)
                ON CONFLICT(item_key) DO UPDATE SET
                    processed_at = excluded.processed_at,
                    status = excluded.status,
                    detail = excluded.detail
                """, (str(item_key), ticket_id, int(time.time()), status,
                      detail[:500]))
            self._connection.commit()

    def close(self):
        with self._lock:
            self._connection.close()

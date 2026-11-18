"""
Usage: pytest tests/test_worker.py

Autor: SO, (c) abasoft GmbH 2026-11-16
Datei: test_worker.py
Beschreibung: Prüft den Worker: Queue-Einträge sind Paare (Handler,
              Ticket-ID), sie laufen in der Reihenfolge der Queue, und ein
              Fehler in einem Handler beendet den Worker nicht.
Letzte Änderung: 2026-11-16
"""

from tanss_triage.worker import Worker, new_queue


def test_worker_dispatches_handlers_in_order_and_survives_errors():
    seen = []

    def audio(ticket_id):
        seen.append(("audio", ticket_id))

    def broken(ticket_id):
        raise RuntimeError("API weg")

    def duplicate(ticket_id):
        seen.append(("duplicate", ticket_id))

    work_queue = new_queue()
    work_queue.put((audio, 1))
    work_queue.put((broken, 2))
    work_queue.put((duplicate, 1))
    worker = Worker(work_queue)
    worker.start()
    worker.stop(timeout=5)
    assert seen == [("audio", 1), ("duplicate", 1)]

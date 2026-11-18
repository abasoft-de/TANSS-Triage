"""
Usage: from tanss_triage.worker import Worker

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: worker.py
Beschreibung: Arbeitet die vom Poller gefüllte Queue sequenziell ab. Jeder
              Eintrag ist ein Paar (Handler, Ticket-ID) - so teilen sich
              Audio-Verarbeitung und Duplikatprüfung eine Queue, und die
              Reihenfolge bleibt erhalten (erst transkribieren, dann auf
              Duplikate prüfen).
              Sequenziell mit Absicht: Whisper large-v3 lastet die CPU allein
              aus, und zwei parallele Transkriptionen wären zusammen
              langsamer als nacheinander. Ein Fehler bei einem Ticket beendet
              den Worker nicht - das nächste soll weiter verarbeitet werden.
Letzte Änderung: 2026-11-16
"""

import logging
import queue as queue_module
import threading

LOG = logging.getLogger("tanss_triage.worker")

STOP = object()          # Sentinel in der Queue beendet den Worker


class Worker:
    def __init__(self, work_queue):
        self._queue = work_queue
        self._thread = threading.Thread(target=self._run, name="worker",
                                        daemon=True)

    def start(self):
        self._thread.start()

    def stop(self, timeout=30):
        """Bittet den Worker zu enden und wartet auf das laufende Ticket."""
        self._queue.put(STOP)
        self._thread.join(timeout=timeout)

    def _run(self):
        LOG.info("Worker gestartet.")
        while True:
            item = self._queue.get()
            if item is STOP:
                break
            handler, ticket_id = item
            try:
                handler(ticket_id)
            except Exception:
                # process_ticket fängt Dokumentfehler selbst; hier landen
                # nur noch Ausfälle davor (Ticket nicht lesbar, API weg).
                LOG.exception("Ticket %s konnte nicht verarbeitet werden.",
                              ticket_id)
            finally:
                self._queue.task_done()
        LOG.info("Worker beendet.")


def new_queue():
    return queue_module.Queue()

"""
Usage: from tanss_triage.worker import Worker

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: worker.py
Beschreibung: Arbeitet die vom Webhook-Server gefüllte Queue sequenziell ab.
              Sequenziell mit Absicht: Whisper large-v3 lastet die CPU allein
              aus, und zwei parallele Transkriptionen wären zusammen
              langsamer als nacheinander. Ein Fehler bei einem Ticket beendet
              den Worker nicht - der nächste Webhook soll weiter verarbeitet
              werden.
Letzte Änderung: 2026-09-10
"""

import logging
import queue as queue_module
import threading

LOG = logging.getLogger("tanss_triage.worker")

STOP = object()          # Sentinel in der Queue beendet den Worker


class Worker:
    def __init__(self, work_queue, processor):
        self._queue = work_queue
        self._processor = processor
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
            try:
                self._processor.process_ticket(item)
            except Exception:
                # process_ticket fängt Dokumentfehler selbst; hier landen
                # nur noch Ausfälle davor (Ticket nicht lesbar, API weg).
                LOG.exception("Ticket %s konnte nicht verarbeitet werden.",
                              item)
            finally:
                self._queue.task_done()
        LOG.info("Worker beendet.")


def new_queue():
    return queue_module.Queue()

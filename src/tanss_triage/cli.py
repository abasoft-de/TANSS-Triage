"""
Usage: python -m tanss_triage                        # Dauerbetrieb (Polling)
       python -m tanss_triage --ticket 250312 --dry-run
       python -m tanss_triage --duplicate 260148 --dry-run
       python -m tanss_triage --identify 07432994360
       python -m tanss_triage --mail 356324

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: cli.py
Beschreibung: Kommandozeile von TANSS-Triage. Ohne Optionen startet der
              Dauerbetrieb: ein Poller fragt die TANSS-Datenbank im
              konfigurierten Intervall nach neuen Sprachaufnahmen und nach
              Tickets, die erstmals bei einer Firma liegen
              (Duplikat-Erkennung), und ein Worker arbeitet beides
              sequenziell ab; sauberes Ende bei SIGTERM/SIGINT (systemd).
              Die übrigen Kommandos sind Einzelläufe für Test und Diagnose.
Letzte Änderung: 2026-11-16
"""

import argparse
import json
import logging
import signal
import sys
import threading

from . import __version__
from .assigner import multi_company_checker
from .config import load_config
from .db import (fetch_assignment_labels, fetch_mail_attachments,
                 fetch_ticket, open_company_tickets, phone_number_roles,
                 ticket_messages, ticket_services)
from .duplicates import DuplicateChecker
from .llm import build_llm
from .logging_setup import setup_logging
from .masking import configure as configure_masking
from .poller import Poller
from .processor import Processor
from .state import State
from .tanss_client import TanssClient
from .transcriber import Transcriber
from .worker import Worker, new_queue

LOG = logging.getLogger("tanss_triage.cli")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="tanss-triage",
        description="Transkribiert Sprachnachrichten aus TANSS-Tickets und "
                    "bereitet Starface-Tickets auf.")
    parser.add_argument("--version", action="version",
                        version="tanss-triage %s" % __version__)
    parser.add_argument("--config", metavar="PFAD",
                        help="Pfad zur config.toml (Default: Projektstamm)")
    parser.add_argument("--dry-run", action="store_true",
                        help="nichts in TANSS schreiben, nur loggen")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--ticket", type=int, metavar="ID",
                      help="nur dieses Ticket verarbeiten und beenden")
    mode.add_argument("--duplicate", type=int, metavar="ID",
                      help="nur dieses Ticket auf Duplikate prüfen und "
                           "beenden (auch wenn es schon geprüft wurde)")
    mode.add_argument("--identify", metavar="NUMMER",
                      help="Rufnummer auflösen und die rohe Antwort zeigen")
    mode.add_argument("--mail", type=int, metavar="ID",
                      help="Mail samt Anhängen zeigen (API und DB, zur "
                           "Diagnose)")
    return parser


def build_processor(cfg, client, state, llm):
    return Processor(
        cfg=cfg,
        client=client,
        transcriber=Transcriber(cfg.whisper),
        llm=llm,
        state=state,
        is_multi_company=multi_company_checker(cfg.db),
        mail_attachments_lookup=lambda mail_id: fetch_mail_attachments(
            cfg.db, mail_id),
        labels_lookup=lambda company_id, employee_id: fetch_assignment_labels(
            cfg.db, company_id, employee_id),
        phone_roles_lookup=lambda number, company_id: phone_number_roles(
            cfg.db, number, company_id))


def build_duplicate_checker(cfg, client, state, llm):
    return DuplicateChecker(
        cfg=cfg,
        client=client,
        llm=llm,
        state=state,
        ticket_lookup=lambda ticket_id: fetch_ticket(cfg.db, ticket_id),
        candidates_lookup=lambda company_id, exclude_id: open_company_tickets(
            cfg.db, company_id, exclude_id, cfg.duplicates.max_candidates),
        services_lookup=lambda ticket_ids: ticket_services(cfg.db,
                                                           ticket_ids),
        messages_lookup=lambda ticket_ids: ticket_messages(cfg.db,
                                                           ticket_ids))


def serve(cfg, client):
    """Dauerbetrieb: Poll-Schleife + Worker, Ende per Signal."""
    state = State(cfg.state.db_path)
    llm = build_llm(cfg.llm)
    processor = build_processor(cfg, client, state, llm)
    checker = build_duplicate_checker(cfg, client, state, llm)
    if cfg.duplicates.enabled and not checker.active:
        LOG.info("Duplikat-Erkennung ruht - sie braucht ein LLM.")

    def handle_audio(ticket_id):
        """Audio verarbeiten, danach dasselbe Ticket auf Duplikate prüfen.

        Die Prüfung direkt im Anschluss holt nach, was zurückgestellt
        wurde, solange das Ticket noch den generischen Starface-Betreff
        trug; für schon geprüfte oder firmenlose Tickets ist sie ein
        kurzer Blick in State bzw. Datenbank.
        """
        try:
            processor.process_ticket(ticket_id)
        except Exception:
            # process_ticket fängt Dokumentfehler selbst; hier landen nur
            # noch Ausfälle davor (Ticket nicht lesbar, API weg).
            LOG.exception("Ticket %s konnte nicht verarbeitet werden.",
                          ticket_id)
        checker.check_ticket(ticket_id)

    work_queue = new_queue()
    worker = Worker(work_queue)
    worker.start()
    poller = Poller(
        cfg, state,
        enqueue=lambda ticket_id: work_queue.put((handle_audio, ticket_id)),
        enqueue_duplicate=(
            (lambda ticket_id: work_queue.put((checker.check_ticket,
                                               ticket_id)))
            if checker.active else None))

    stop_event = threading.Event()

    def handle_signal(signum, _frame):
        LOG.info("Signal %s - fahre herunter.", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    LOG.info("tanss-triage %s bereit (Polling alle %ds, LLM: %s, "
             "Whisper: %s, Duplikat-Erkennung: %s%s).", __version__,
             cfg.polling.interval_seconds, cfg.llm.resolved_provider(),
             cfg.whisper.model, "an" if checker.active else "aus",
             ", dry-run" if cfg.dry_run else "")
    while not stop_event.is_set():
        try:
            poller.tick()
        except Exception:
            LOG.exception("Poll-Zyklus fehlgeschlagen - nächster Versuch "
                          "im Intervall.")
        stop_event.wait(cfg.polling.interval_seconds)

    worker.stop()
    return 0


def main(argv=None):
    arguments = build_parser().parse_args(argv)
    cfg = load_config(config_path=arguments.config)
    cfg.dry_run = bool(arguments.dry_run)
    setup_logging(cfg.logging)
    configure_masking(cfg.masking.enabled, cfg.masking.keep_words)

    problems = cfg.check()
    if problems:
        for problem in problems:
            print("KONFIGURATION:", problem, file=sys.stderr)
        return 2

    client = TanssClient(cfg.tanss.base_url, cfg.tanss.username,
                         cfg.tanss.password, timeout=cfg.tanss.timeout)

    if arguments.identify:
        result = client.identify_phone_number(arguments.identify)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if arguments.mail:
        mail = client.get_mail(arguments.mail)
        for field in ("body", "bodyHtml", "bodyPlain", "originalText"):
            if isinstance(mail.get(field), str) and len(mail[field]) > 300:
                mail[field] = mail[field][:300] + " ...[gekürzt]"
        print("--- API (GET /api/v1/mails/%d) ---" % arguments.mail)
        print(json.dumps(mail, indent=2, ensure_ascii=False))
        print("\n--- DB (mails_attachments) ---")
        rows = fetch_mail_attachments(cfg.db, arguments.mail)
        print(json.dumps(rows, indent=2, ensure_ascii=False)
              if rows is not None else "(DB nicht verfügbar)")
        return 0

    if arguments.ticket:
        state = State(cfg.state.db_path)
        build_processor(cfg, client, state,
                        build_llm(cfg.llm)).process_ticket(arguments.ticket)
        return 0

    if arguments.duplicate:
        state = State(cfg.state.db_path)
        checker = build_duplicate_checker(cfg, client, state,
                                          build_llm(cfg.llm))
        if not checker.active:
            print("Die Duplikat-Erkennung ist aus: [duplicates] enabled "
                  "ist false oder es ist kein LLM konfiguriert.",
                  file=sys.stderr)
            return 2
        # force: der Einzellauf prüft auch ein schon geprüftes Ticket.
        checker.check_ticket(arguments.duplicate, force=True)
        return 0

    return serve(cfg, client)


if __name__ == "__main__":
    sys.exit(main())

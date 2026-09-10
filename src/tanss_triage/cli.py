"""
Usage: python -m tanss_triage [--once-Optionen]
       python -m tanss_triage                        # Webhook-Server (Betrieb)
       python -m tanss_triage --ticket 250312 --dry-run
       python -m tanss_triage --identify 07432994360
       python -m tanss_triage --register-webhook http://127.0.0.1:8763/webhook
       python -m tanss_triage --list-webhooks

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: cli.py
Beschreibung: Kommandozeile von TANSS-Triage. Ohne Optionen startet der
              Dauerbetrieb: Webhook-Server plus Worker, sauberes Ende bei
              SIGTERM/SIGINT (systemd). Die uebrigen Kommandos sind
              Einzellaeufe fuer Test, Diagnose und Einrichtung.
Letzte Aenderung: 2026-09-10
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
from .llm import build_llm
from .logging_setup import setup_logging
from .processor import Processor
from .state import State
from .tanss_client import TanssClient
from .transcriber import Transcriber
from .webhook_server import start_webhook_server
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
    mode.add_argument("--identify", metavar="NUMMER",
                      help="Rufnummer aufloesen und die rohe Antwort zeigen")
    mode.add_argument("--register-webhook", metavar="URL",
                      help="Event-Regel mit WEBHOOK-Aktion in TANSS anlegen")
    mode.add_argument("--list-webhooks", action="store_true",
                      help="vorhandene Event-Regeln anzeigen")
    return parser


def build_processor(cfg, client):
    return Processor(
        cfg=cfg,
        client=client,
        transcriber=Transcriber(cfg.whisper),
        llm=build_llm(cfg.llm),
        state=State(cfg.state.db_path),
        is_multi_company=multi_company_checker(cfg.db))


def serve(cfg, client):
    """Dauerbetrieb: Webhook-Server + Worker, Ende per Signal."""
    work_queue = new_queue()
    worker = Worker(work_queue, build_processor(cfg, client))
    worker.start()
    server = start_webhook_server(cfg.webhook, work_queue)

    stop_event = threading.Event()

    def handle_signal(signum, _frame):
        LOG.info("Signal %s - fahre herunter.", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    LOG.info("tanss-triage %s bereit (LLM: %s, Whisper: %s%s).",
             __version__, cfg.llm.resolved_provider(), cfg.whisper.model,
             ", dry-run" if cfg.dry_run else "")
    stop_event.wait()

    server.shutdown()
    worker.stop()
    return 0


def main(argv=None):
    arguments = build_parser().parse_args(argv)
    cfg = load_config(config_path=arguments.config)
    cfg.dry_run = bool(arguments.dry_run)
    setup_logging(cfg.logging)

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

    if arguments.list_webhooks:
        for rule in client.list_event_rules():
            print(json.dumps(rule, indent=2, ensure_ascii=False))
        return 0

    if arguments.register_webhook:
        rule = client.create_event_rule(
            "TANSS-Triage Voicemail-Transkription",
            arguments.register_webhook)
        print("Regel angelegt:")
        print(json.dumps(rule, indent=2, ensure_ascii=False))
        print("\nWichtig: In der TANSS-Oberflaeche pruefen, dass die Regel "
              "auf die Trigger TICKET_CREATED bzw. TICKET_EMAIL_RECEIVED "
              "reagiert.")
        return 0

    if arguments.ticket:
        build_processor(cfg, client).process_ticket(arguments.ticket)
        return 0

    return serve(cfg, client)


if __name__ == "__main__":
    sys.exit(main())

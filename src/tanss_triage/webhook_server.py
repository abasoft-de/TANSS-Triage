"""
Usage: from tanss_triage.webhook_server import start_webhook_server

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: webhook_server.py
Beschreibung: Nimmt die Webhooks der TANSS-Event-Regeln entgegen. Bewusst nur
              Standardbibliothek (ThreadingHTTPServer): ein Endpunkt, localhost,
              keine weitere Abhaengigkeit. Der Handler antwortet sofort und
              legt nur die Ticket-ID in die Queue - transkribiert wird im
              Worker, denn Whisper braucht Minuten und TANSS soll auf seinen
              Webhook nicht warten. TANSS signiert Webhooks nicht, darum
              steckt ein optionales Shared Secret im Pfad.
Letzte Aenderung: 2026-09-10
"""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__

LOG = logging.getLogger("tanss_triage.webhook")

MAX_BODY = 1024 * 1024        # TnsTanssEvent ist klein; alles darueber ist Unfug


def extract_ticket_id(payload):
    """Zieht die Ticket-ID aus einem TnsTanssEvent.

    linkId traegt sie bei linkType TICKET; zur Sicherheit zaehlt auch
    content.ticket.id. Alles andere (etwa Events anderer Objektarten,
    falls die Regel breiter feuert) wird ignoriert.
    """
    if not isinstance(payload, dict):
        return None
    link_type = payload.get("linkType")
    if link_type in (None, "TICKET"):
        link_id = payload.get("linkId")
        if isinstance(link_id, int) and link_id > 0:
            return link_id
    ticket = ((payload.get("content") or {}).get("ticket") or {})
    ticket_id = ticket.get("id")
    if isinstance(ticket_id, int) and ticket_id > 0:
        return ticket_id
    return None


class _Handler(BaseHTTPRequestHandler):
    # Klassenweite Verdrahtung, gesetzt von start_webhook_server().
    queue = None
    expected_path = "/webhook"

    def do_POST(self):                                   # noqa: N802 (stdlib-API)
        if self.path.rstrip("/") != self.expected_path:
            self._answer(404, "unknown path")
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            self._answer(400, "bad length")
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._answer(400, "bad json")
            return
        ticket_id = extract_ticket_id(payload)
        if ticket_id is None:
            # Kein Ticket-Event - ok melden, sonst deaktiviert womoeglich
            # jemand die Regel wegen Fehlerquoten.
            LOG.debug("Webhook ohne Ticket-ID ignoriert: %.200s", payload)
            self._answer(200, "ignored")
            return
        trigger = payload.get("triggerType", "")
        LOG.info("Webhook: Ticket %d (%s)", ticket_id, trigger or "ohne Trigger")
        self.queue.put(ticket_id)
        self._answer(200, "queued")

    def do_GET(self):                                    # noqa: N802
        if self.path.rstrip("/") == "/health":
            self._answer(200, "tanss-triage %s" % __version__)
        else:
            self._answer(404, "unknown path")

    def _answer(self, status, text):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        """Zugriffs-Logging laeuft ueber unser Logging, nicht ueber stderr."""
        LOG.debug("%s - %s", self.address_string(), fmt % args)


def start_webhook_server(cfg, queue):
    """Startet den HTTP-Server im Hintergrund-Thread und liefert ihn zurueck.

    cfg ist eine WebhookConfig; der Pfad ist /webhook oder - mit Secret -
    /webhook/<secret>.
    """
    handler = type("Handler", (_Handler,), {
        "queue": queue,
        "expected_path": ("/webhook/" + cfg.secret) if cfg.secret
                         else "/webhook",
    })
    server = ThreadingHTTPServer((cfg.listen_host, cfg.listen_port), handler)
    thread = threading.Thread(target=server.serve_forever,
                              name="webhook-server", daemon=True)
    thread.start()
    LOG.info("Webhook-Server lauscht auf http://%s:%d%s",
             cfg.listen_host, cfg.listen_port, handler.expected_path)
    return server

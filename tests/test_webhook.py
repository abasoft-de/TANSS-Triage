"""
Usage: pytest tests/test_webhook.py

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: test_webhook.py
Beschreibung: Prueft den Webhook-Server gegen echte HTTP-Requests auf einem
              Ephemeral-Port sowie das Herausziehen der Ticket-ID aus dem
              TnsTanssEvent.
Letzte Aenderung: 2026-09-10
"""

import json
import queue
import urllib.request
import urllib.error

import pytest

from tanss_triage.config import WebhookConfig
from tanss_triage.webhook_server import extract_ticket_id, start_webhook_server

EVENT = {"linkType": "TICKET", "linkId": 253724,
         "triggerType": "TICKET_CREATED",
         "content": {"ticket": {"id": 253724, "title": "Sprachnachricht"}}}


def test_ticket_id_aus_linkid():
    assert extract_ticket_id(EVENT) == 253724


def test_ticket_id_aus_content_wenn_linkid_fehlt():
    assert extract_ticket_id(
        {"content": {"ticket": {"id": 99}}}) == 99


def test_kein_ticket_event():
    assert extract_ticket_id({"linkType": "PC", "linkId": 5}) is None
    assert extract_ticket_id({}) is None
    assert extract_ticket_id("unsinn") is None


@pytest.fixture()
def server_und_queue():
    work_queue = queue.Queue()
    cfg = WebhookConfig(listen_host="127.0.0.1", listen_port=0,
                        secret="s3cret")
    server = start_webhook_server(cfg, work_queue)
    yield server, work_queue
    server.shutdown()


def _post(url, payload):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data,
                                     headers={"Content-Type":
                                              "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def test_webhook_landet_in_queue(server_und_queue):
    server, work_queue = server_und_queue
    port = server.server_address[1]
    status = _post("http://127.0.0.1:%d/webhook/s3cret" % port, EVENT)
    assert status == 200
    assert work_queue.get(timeout=5) == 253724


def test_falsches_secret_wird_abgewiesen(server_und_queue):
    server, work_queue = server_und_queue
    port = server.server_address[1]
    status = _post("http://127.0.0.1:%d/webhook/falsch" % port, EVENT)
    assert status == 404
    assert work_queue.empty()


def test_health_endpunkt(server_und_queue):
    server, _ = server_und_queue
    port = server.server_address[1]
    with urllib.request.urlopen(
            "http://127.0.0.1:%d/health" % port, timeout=5) as response:
        assert response.status == 200
        assert b"tanss-triage" in response.read()

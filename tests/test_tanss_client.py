"""
Usage: pytest tests/test_tanss_client.py

Autor: SO, (c) abasoft GmbH 2026-09-15
Datei: test_tanss_client.py
Beschreibung: Prüft den Login des REST-Clients: Erkennung des
              /backend-Präfixes, sofortiger Abbruch bei falschen
              Zugangsdaten und Re-Login bei abgelaufenem Token.
Letzte Änderung: 2026-09-15
"""

import pytest

from tanss_triage.tanss_client import TanssApiError, TanssClient


class _FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or str(payload)
        self.content = b"x"

    def json(self):
        return self._payload


LOGIN_OK = _FakeResponse(200, {"content": {"apiKey": "Bearer abc",
                                           "employeeId": 1}})
LOGIN_BAD_REQUEST = _FakeResponse(400, text='{"message":"Bad request"}')
LOGIN_DENIED = _FakeResponse(
    403, {"meta": {"text": "Unsuccessful login attempt"}})


class _FakeSession:
    """Beantwortet Requests anhand einer URL->Antwort-Tabelle."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url))
        return self.routes[url]

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        return self.routes[url]


def _client(routes):
    client = TanssClient("https://tanss.example.de", "api", "pw")
    client._session = _FakeSession(routes)
    return client


def test_login_falls_back_to_backend_prefix():
    client = _client({
        "https://tanss.example.de/api/v1/login": LOGIN_BAD_REQUEST,
        "https://tanss.example.de/backend/api/v1/login": LOGIN_OK,
    })
    client._login()
    assert client.base_url == "https://tanss.example.de/backend"
    assert client._api_key == "Bearer abc"


def test_login_direct_path_wins():
    client = _client({"https://tanss.example.de/api/v1/login": LOGIN_OK})
    client._login()
    assert client.base_url == "https://tanss.example.de"


def test_wrong_credentials_stop_probing():
    session_routes = {
        "https://tanss.example.de/api/v1/login": LOGIN_DENIED,
        # /backend darf gar nicht mehr probiert werden
    }
    client = _client(session_routes)
    with pytest.raises(TanssApiError) as error:
        client._login()
    assert error.value.status == 403
    assert client._session.calls == [
        ("POST", "https://tanss.example.de/api/v1/login")]


def test_backend_url_is_not_doubled():
    client = TanssClient("https://tanss.example.de/backend", "api", "pw")
    client._session = _FakeSession({
        "https://tanss.example.de/backend/api/v1/login": LOGIN_BAD_REQUEST,
    })
    with pytest.raises(TanssApiError):
        client._login()
    assert client._session.calls == [
        ("POST", "https://tanss.example.de/backend/api/v1/login")]

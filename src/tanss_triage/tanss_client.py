"""
Usage: from tanss_triage.tanss_client import TanssClient

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: tanss_client.py
Beschreibung: Dünner Client für die TANSS-REST-API (https://api-doc.tanss.de).
              Besonderheiten der API: der Login liefert einen JWT, der als
              wörtlicher Header "apiToken: Bearer <jwt>" gesendet wird (nicht
              Authorization), er lebt 4 Stunden und verfällt zusätzlich nach
              ~2 Minuten Leerlauf. Der Client loggt sich darum bei 401/403
              einmal automatisch neu ein und wiederholt den Aufruf.
Letzte Änderung: 2026-09-10
"""

import logging
import threading

import requests

LOG = logging.getLogger("tanss_triage.tanss")

# Felder, die PUT /tickets/{id} laut API-Beispiel entgegennimmt. Der GET
# liefert daneben etliche berechnete/readonly-Felder - die werden vor dem
# Update herausgefiltert, statt sie dem Server zurückzuwerfen.
TICKET_WRITE_FIELDS = (
    "companyId", "remitterId", "title", "content", "extTicketId",
    "assignedToEmployeeId", "assignedToDepartmentId", "statusId", "typeId",
    "linkTypeId", "linkId", "deadlineDate", "project", "projectId", "repair",
    "dueDate", "attention", "orderById", "installationFee",
    "installationFeeDriveMode", "installationFeeAmount", "separateBilling",
    "billingToCompanyId", "deliveryAddressOnPdf", "serviceCapAmount",
    "relationshipLinkTypeId", "relationshipLinkId", "resubmissionDate",
    "estimatedMinutes", "localTicketAdminFlag", "localTicketAdminEmployeeId",
    "phaseId", "resubmissionText", "resubmissionMode", "orderNumber",
    "reminder", "reminderInterval", "reminderIntervalHours", "clearanceMode",
)


class TanssApiError(RuntimeError):
    """Antwort der TANSS-API war kein Erfolg."""

    def __init__(self, message, status=None, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload


class TanssClient:
    """Sitzung gegen eine TANSS-Instanz, thread-sicher genug für einen Worker.

    Ein Lock schützt den Login, damit Webhook- und Worker-Thread sich nicht
    gegenseitig frische Tokens wegwerfen.
    """

    def __init__(self, base_url, username, password, timeout=30.0):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self._session = requests.Session()
        self._api_key = ""
        self._login_lock = threading.Lock()

    # -- Anmeldung

    def _login(self):
        """Holt ein frisches Token. Wirft TanssApiError bei Ablehnung.

        Je nach Installation liegt die REST-API nicht direkt unter
        /api/v1, sondern hinter dem Backend-Präfix /backend/api/v1 -
        der nackte Pfad landet dann im PHP-Frontend, das mit einem
        generischen 400 "Bad request" antwortet. Darum werden beide
        Varianten probiert und die funktionierende Basis-URL behalten.
        Ein 403 dagegen kommt von der echten API (falsche Zugangsdaten)
        und beendet die Suche sofort.
        """
        candidates = [self.base_url]
        if not self.base_url.endswith("/backend"):
            candidates.append(self.base_url + "/backend")
        last_error = None
        for base in candidates:
            response = self._session.post(
                base + "/api/v1/login",
                json={"username": self.username, "password": self.password},
                timeout=self.timeout)
            if response.status_code == 200:
                content = response.json().get("content") or {}
                api_key = content.get("apiKey", "")
                if not api_key:
                    raise TanssApiError("Login-Antwort ohne apiKey.")
                if base != self.base_url:
                    LOG.info("API-Endpunkt liegt unter %s - am besten "
                             "TANSS_BASE_URL in der .env darauf setzen.", base)
                    self.base_url = base
                self._api_key = api_key    # enthält bereits "Bearer "
                LOG.info("An TANSS angemeldet (employeeId %s).",
                         content.get("employeeId"))
                return
            last_error = TanssApiError(
                "Login an %s fehlgeschlagen (HTTP %d): %s"
                % (base, response.status_code, response.text[:300]),
                status=response.status_code)
            if response.status_code == 403:
                # Echte API-Antwort: Zugangsdaten falsch, kein Pfadproblem.
                break
        raise last_error

    def _request(self, method, path, *, json_body=None, params=None,
                 _retry=True):
        """Ein API-Aufruf inklusive automatischem Re-Login bei 401/403.

        403 kann auch ein echter Rechtefehler sein - der einmalige zweite
        Versuch nach frischem Login schadet dann nicht, und der Fehlertext
        des zweiten Versuchs sagt, woran es wirklich liegt.
        """
        if not self._api_key:
            with self._login_lock:
                if not self._api_key:
                    self._login()
        response = self._session.request(
            method, self.base_url + path,
            headers={"apiToken": self._api_key},
            json=json_body, params=params, timeout=self.timeout)
        if response.status_code in (401, 403) and _retry:
            with self._login_lock:
                self._login()
            return self._request(method, path, json_body=json_body,
                                 params=params, _retry=False)
        if response.status_code >= 400:
            raise TanssApiError(
                "%s %s -> HTTP %d: %s"
                % (method, path, response.status_code, response.text[:500]),
                status=response.status_code)
        if not response.content:
            return {}
        return response.json()

    @staticmethod
    def _content(payload):
        return (payload or {}).get("content")

    # -- Tickets

    def get_ticket(self, ticket_id):
        return self._content(
            self._request("GET", "/api/v1/tickets/%d" % ticket_id)) or {}

    def get_ticket_history(self, ticket_id):
        """Kommentare, Leistungen und Mails eines Tickets."""
        return self._content(
            self._request("GET", "/api/v1/tickets/history/%d" % ticket_id)) or {}

    def update_ticket(self, ticket_id, ticket):
        """PUT mit auf die beschreibbaren Felder gefiltertem Ticket-Objekt."""
        body = {key: ticket[key] for key in TICKET_WRITE_FIELDS
                if key in ticket}
        return self._content(self._request(
            "PUT", "/api/v1/tickets/%d" % ticket_id,
            json_body=body, params={"remitterCheck": "false"}))

    def post_comment(self, ticket_id, title, content, internal=True):
        return self._content(self._request(
            "POST", "/api/v1/tickets/%d/comments" % ticket_id,
            json_body={"title": title, "content": content,
                       "internal": bool(internal)}))

    # -- Dokumente

    def get_documents(self, ticket_id):
        return self._content(self._request(
            "GET", "/api/v1/tickets/%d/documents" % ticket_id)) or []

    def download_document(self, ticket_id, document_id, target_path):
        """Lädt ein Ticket-Dokument in eine Datei.

        Die API liefert erst einen einmaligen Download-Link (15 Minuten
        gültig, genau ein Abruf) - darum wird hier sofort heruntergeladen
        und nie eine URL nach außen gereicht.
        """
        pass_response = self._content(self._request(
            "GET", "/api/v1/tickets/%d/documents/%d"
            % (ticket_id, document_id))) or {}
        url = pass_response.get("url", "")
        if not url:
            raise TanssApiError("Kein Download-Link für Dokument %d."
                                % document_id)
        if url.startswith("/"):
            url = self.base_url + url
        response = self._session.get(url, headers={"apiToken": self._api_key},
                                     timeout=max(self.timeout, 120),
                                     stream=True)
        if response.status_code >= 400:
            raise TanssApiError("Download von Dokument %d -> HTTP %d"
                                % (document_id, response.status_code),
                                status=response.status_code)
        size = 0
        with open(target_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=65536):
                handle.write(chunk)
                size += len(chunk)
        return size

    # -- Identifikation und Stammdaten

    def identify_phone_number(self, number):
        """Lässt TANSS eine Rufnummer auflösen (Firmen- und Mitarbeiternummern)."""
        return self._content(self._request(
            "POST", "/api/v1/phoneCalls/identify",
            json_body={"fromPhoneNumber": number})) or {}

    def get_company_employees(self, company_id):
        return self._content(self._request(
            "GET", "/api/v1/companies/%d/employees" % company_id)) or []

    # -- Event-Regeln (Webhooks)

    def list_event_rules(self):
        """Alle Event-Regeln (Filter leer = alles, was der Nutzer sehen darf)."""
        return self._content(self._request(
            "PUT", "/api/v1/tanssEvents/rules",
            json_body={"linkIds": []})) or []

    def create_event_rule(self, name, url, method="POST"):
        return self._content(self._request(
            "POST", "/api/v1/tanssEvents/rules",
            json_body={
                "name": name,
                "active": True,
                "actions": [{"actionType": "WEBHOOK",
                             "params": {"url": url, "method": method}}],
            }))

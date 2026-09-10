"""
Usage: from tanss_triage.assigner import decide_assignment

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: assigner.py
Beschreibung: Entscheidet anhand der TANSS-Rufnummernauflösung
              (POST /api/v1/phoneCalls/identify), welcher Firma ein
              Starface-Ticket zugewiesen wird und ob ein Ansprechpartner als
              Melder gesetzt werden darf. Die Regeln stammen aus der Vorgabe:
              Firma nur bei eindeutigem Treffer; Melder nur bei genau einem
              exakten Mitarbeiter-Treffer, dessen Nummer nicht die
              Firmenzentrale ist; Mitarbeiter, die in mehreren Firmen
              arbeiten, werden gar nicht zugeordnet. Im Zweifel passiert
              nichts - eine falsche Zuordnung ist schlimmer als keine.
Letzte Änderung: 2026-09-10
"""

import logging
from dataclasses import dataclass

LOG = logging.getLogger("tanss_triage.assigner")


@dataclass
class Assignment:
    company_id: int | None = None
    remitter_id: int | None = None
    note: str = ""

    @property
    def has_change(self):
        return self.company_id is not None or self.remitter_id is not None


def multi_company_checker(db_cfg):
    """Baut die Prüfung 'arbeitet Mitarbeiter X in mehreren Firmen?'.

    Die REST-API stellt die Mehrfach-Anstellung nicht bereit, darum ein
    Read-only-Blick in die TANSS-Datenbank (mitarbeiter_firmen), mit den
    Zugangsdaten aus ~/.my.cnf - derselben Datei, die schon der MCP-Server
    benutzt. Liefert eine Funktion employee_id -> True/False/None
    (None = Prüfung nicht möglich, der Aufrufer entscheidet fail-safe).
    """
    defaults_file = db_cfg.resolved_defaults_file()

    def check(employee_id):
        if not defaults_file:
            return None
        try:
            import pymysql
            connection = pymysql.connect(
                read_default_file=defaults_file,
                host=db_cfg.host, database=db_cfg.database,
                connect_timeout=5)
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT COUNT(DISTINCT firmenID) FROM "
                        "mitarbeiter_firmen WHERE maID = %s",
                        (employee_id,))
                    (count,) = cursor.fetchone()
            finally:
                connection.close()
            return count > 1
        except Exception as error:
            LOG.warning("Mehrfach-Firmen-Check für Mitarbeiter %s nicht "
                        "möglich: %s", employee_id, error)
            return None

    return check


def decide_assignment(identify_content, is_multi_company,
                      assign_remitter=True):
    """Wendet die Zuordnungsregeln auf eine identify-Antwort an.

    identify_content ist der content der API-Antwort (TnsPhoneCall),
    is_multi_company eine Funktion employee_id -> True/False/None.
    """
    infos = (identify_content or {}).get("fromPhoneNrInfos") or {}
    found_type = infos.get("foundType") or "NONE"
    items = infos.get("items") or []
    if not items and infos.get("result"):
        items = [infos["result"]]

    if found_type in ("NONE", "TOO_SHORT"):
        return Assignment(note="Rufnummer in TANSS nicht bekannt - "
                               "keine automatische Zuordnung.")

    companies = [item for item in items if item.get("type") == "COMPANY"]
    employees = [item for item in items if item.get("type") == "EMPLOYEE"]
    inactive = [item for item in items
                if item.get("type") == "EMPLOYEE_INACTIVE"]

    # -- Mitarbeiter-Treffer: der einzige Weg zu einem Melder
    if len(employees) == 1:
        employee = employees[0]
        employee_id = employee.get("id")
        company_id = employee.get("companyId")
        exact = (employee.get("charsLeftOut") or 0) == 0
        # Firmennummer beim Mitarbeiter hinterlegt? Dann matcht dieselbe
        # Nummer auch eine Firma exakt - das ist die Zentrale, kein
        # persönlicher Anschluss.
        company_exact = any((item.get("charsLeftOut") or 0) == 0
                            for item in companies)

        multi = is_multi_company(employee_id)
        if multi is True:
            return Assignment(note="%s arbeitet in mehreren Firmen - "
                                   "keine automatische Zuordnung."
                                   % (employee.get("name") or
                                      "Mitarbeiter %s" % employee_id))
        if multi is None:
            return Assignment(note="Mehrfach-Firmen-Prüfung nicht möglich "
                                   "(DB nicht erreichbar) - sicherheitshalber "
                                   "keine automatische Zuordnung.")

        if exact and not company_exact and assign_remitter:
            return Assignment(
                company_id=company_id, remitter_id=employee_id,
                note="Rufnummer eindeutig: %s (Firma %s)."
                     % (employee.get("name") or employee_id, company_id))
        if company_id:
            return Assignment(
                company_id=company_id,
                note="Rufnummer gehört zur Firma von %s - Firma zugewiesen, "
                     "Melder offen (Nummer nicht eindeutig persönlich)."
                     % (employee.get("name") or employee_id))

    if len(employees) > 1:
        company_ids = {employee.get("companyId") for employee in employees}
        company_ids.discard(None)
        if len(company_ids) == 1:
            return Assignment(
                company_id=company_ids.pop(),
                note="Mehrere Ansprechpartner mit dieser Nummer - Firma "
                     "zugewiesen, Melder offen.")
        return Assignment(note="Rufnummer passt zu Ansprechpartnern "
                               "verschiedener Firmen - keine automatische "
                               "Zuordnung.")

    # -- Reine Firmen-Treffer (auch: nur inaktive Mitarbeiter)
    candidates = {item.get("id") for item in companies}
    candidates.discard(None)
    if not candidates and inactive:
        candidates = {item.get("companyId") for item in inactive}
        candidates.discard(None)
    if len(candidates) == 1:
        return Assignment(company_id=candidates.pop(),
                          note="Rufnummer eindeutig einer Firma zugeordnet.")
    if len(candidates) > 1:
        return Assignment(note="Rufnummer passt zu mehreren Firmen - "
                               "keine automatische Zuordnung.")
    return Assignment(note="Rufnummer in TANSS nicht bekannt - "
                           "keine automatische Zuordnung.")

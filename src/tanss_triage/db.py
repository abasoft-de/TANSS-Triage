"""
Usage: from tanss_triage.db import fetch_mail_attachments

Autor: SO, (c) abasoft GmbH 2026-09-15
Datei: db.py
Beschreibung: Read-only-Blicke in die TANSS-MariaDB (Zugangsdaten aus
              ~/.my.cnf, derselben Datei wie beim MCP-Server) für das, was
              die REST-API nicht hergibt. Hier: die Anhänge einer Mail aus
              mails_attachments, denn Starface hängt die Voicemail an die
              Mail, nicht ans Ticket - und die Dateien liegen als
              <verzeichnis>/<filenameDB> im Storage des TANSS-Servers,
              auf dem dieses Programm läuft.
Letzte Änderung: 2026-09-15
"""

import logging
import os
import re

LOG = logging.getLogger("tanss_triage.db")


def _query(db_cfg, sql, params):
    """Eine lesende Abfrage; None, wenn die Datenbank nicht verfügbar ist."""
    defaults_file = db_cfg.resolved_defaults_file()
    if not defaults_file or not os.path.isfile(defaults_file):
        return None
    try:
        import pymysql
        import pymysql.cursors
        connection = pymysql.connect(
            read_default_file=defaults_file,
            host=db_cfg.host, database=db_cfg.database,
            connect_timeout=5,
            cursorclass=pymysql.cursors.DictCursor)
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return list(cursor.fetchall())
        finally:
            connection.close()
    except Exception as error:
        LOG.warning("DB-Abfrage nicht möglich (%s): %.120s", error, sql)
        return None


def fetch_mail_attachments(db_cfg, mail_id):
    """Liest die Anhänge einer Mail aus mails_attachments.

    Liefert eine Liste von Dicts (filename, filenameDB, verzeichnis,
    extension) - oder None, wenn die Datenbank nicht verfügbar ist; der
    Aufrufer weicht dann auf die API aus.
    """
    return _query(db_cfg,
                  "SELECT filename, filenameDB, verzeichnis, extension "
                  "FROM mails_attachments WHERE mailID = %s", (mail_id,))


def format_person(row):
    """Baut die Personenzeile: Anrede Titel Vorname Nachname (Rolle).

    Jeder Bestandteil kann in TANSS fehlen und wird dann weggelassen;
    Vor- und/oder Nachname sind laut Datenpflege immer da.
    """
    parts = [row.get(field) for field in
             ("anrede", "titel", "vorname", "nachname")]
    label = " ".join(part.strip() for part in parts
                     if isinstance(part, str) and part.strip())
    funktion = (row.get("funktion") or "").strip()
    if funktion:
        label = "%s (%s)" % (label, funktion) if label else funktion
    return label or None


def phone_number_roles(db_cfg, number, company_id):
    """Prüft, wie eine (nationale) Rufnummer in TANSS hinterlegt ist.

    Die identify-Antwort sagt nur, WEN TANSS gefunden hat - nicht, ob die
    Nummer die Firmenzentrale ist oder ein eindeutiger persönlicher
    Anschluss. Das klärt dieser Blick in die Datenbank: verglichen werden
    reine Ziffernfolgen, damit 07024/51366 und 0702451366 gleich sind.

    Liefert (ist_firmennummer, [IDs aktiver Mitarbeiter mit genau dieser
    Nummer]) - oder None, wenn die Datenbank nicht verfügbar ist.
    """
    digits = re.sub(r"\D", "", number or "")
    if not digits:
        return (False, [])
    rows = _query(db_cfg, """
        SELECT COUNT(*) AS n FROM firmen
        WHERE ID = %s AND %s IN (
            REGEXP_REPLACE(CONCAT(COALESCE(vorwahl, ''), telefon),
                           '[^0-9]', ''),
            REGEXP_REPLACE(telnr, '[^0-9]', ''),
            REGEXP_REPLACE(telefax, '[^0-9]', ''),
            REGEXP_REPLACE(faxnr, '[^0-9]', ''))
        """, (company_id, digits))
    if rows is None:
        return None
    is_company_number = rows[0]["n"] > 0
    rows = _query(db_cfg, """
        SELECT ID FROM mitarbeiter
        WHERE aktiv = 'Y' AND %s IN (
            REGEXP_REPLACE(tel, '[^0-9]', ''),
            REGEXP_REPLACE(tel2, '[^0-9]', ''),
            REGEXP_REPLACE(mobil, '[^0-9]', ''),
            REGEXP_REPLACE(mobil2, '[^0-9]', ''),
            REGEXP_REPLACE(`private`, '[^0-9]', ''),
            REGEXP_REPLACE(persFaxNr, '[^0-9]', ''))
        """, (digits,))
    if rows is None:
        return None
    return is_company_number, [row["ID"] for row in rows]


def fetch_assignment_labels(db_cfg, company_id=None, employee_id=None):
    """Anzeige-Beschriftungen für die Zuordnungszeile des Kommentars.

    Liefert (kubez, person): die Kurzbezeichnung der Firma
    (firmen.displayID) und die formatierte Person. Beides best-effort -
    None, wenn nicht ermittelbar; der Aufrufer hat Fallbacks.
    """
    kubez = None
    person = None
    if company_id:
        rows = _query(db_cfg,
                      "SELECT displayID FROM firmen WHERE ID = %s",
                      (company_id,))
        if rows:
            kubez = (rows[0].get("displayID") or "").strip() or None
    if employee_id:
        rows = _query(db_cfg, """
            SELECT a.kurz AS anrede, t.name AS titel,
                   m.vorname, m.nachname, m.funktion
            FROM mitarbeiter m
            LEFT JOIN anrede a ON a.ID = m.anredeID
            LEFT JOIN mitarbeiter_titel t ON t.ID = m.titelID
            WHERE m.ID = %s""", (employee_id,))
        if rows:
            person = format_person(rows[0])
    return kubez, person

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

LOG = logging.getLogger("tanss_triage.db")


def fetch_mail_attachments(db_cfg, mail_id):
    """Liest die Anhänge einer Mail aus mails_attachments.

    Liefert eine Liste von Dicts (filename, filenameDB, verzeichnis,
    extension) - oder None, wenn die Datenbank nicht verfügbar ist; der
    Aufrufer weicht dann auf die API aus.
    """
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
                cursor.execute(
                    "SELECT filename, filenameDB, verzeichnis, extension "
                    "FROM mails_attachments WHERE mailID = %s", (mail_id,))
                return list(cursor.fetchall())
        finally:
            connection.close()
    except Exception as error:
        LOG.warning("Anhänge von Mail %s nicht aus der DB lesbar (%s) - "
                    "weiche auf die API aus.", mail_id, error)
        return None

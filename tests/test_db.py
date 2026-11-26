"""
Usage: pytest tests/test_db.py

Autor: SO, (c) abasoft GmbH 2026-11-26
Datei: test_db.py
Beschreibung: Prüft die Nachbearbeitung der Datenbankabfragen ohne
              Datenbank: das Mail-Datum aus dem Date-Header und die
              Zeilen von ticket_messages (Abfrage gefälscht).
Letzte Änderung: 2026-11-26
"""

from tanss_triage import db
from tanss_triage.config import Config


def test_mail_header_timestamp_parses_rfc2822():
    # 25.11.2026 09:25:29 MEZ = 08:25:29 UTC
    assert db.mail_header_timestamp("Wed, 25 Nov 2026 09:25:29 +0100") \
        == 1795595129
    assert db.mail_header_timestamp(None) == 0
    assert db.mail_header_timestamp("") == 0
    assert db.mail_header_timestamp("kein Datum") == 0


def test_ticket_messages_turns_date_header_into_date(monkeypatch):
    captured = {}

    def fake_query(db_cfg, sql, params):
        captured["sql"], captured["params"] = sql, params
        return [
            {"ticket_id": 7, "kind": "mail", "id": 1, "date": 0,
             "sender": "praxis@example.de", "text": "Hallo",
             "date_header": "Wed, 25 Nov 2026 09:25:29 +0100"},
            {"ticket_id": 7, "kind": "voicemail", "id": 2,
             "date": 1795600000, "sender": "", "text": "Transkript",
             "date_header": None},
        ]

    monkeypatch.setattr(db, "_query", fake_query)
    rows = db.ticket_messages(Config().db, [7, 8])
    assert [(row["kind"], row["date"]) for row in rows] == [
        ("mail", 1795595129), ("voicemail", 1795600000)]
    assert all("date_header" not in row for row in rows)
    # beide Teile der UNION bekommen die Ticket-IDs, der Titel-Filter
    # reist als Parameter
    assert captured["params"].count(7) == 2
    assert "Transkript:%" in captured["params"]
    assert db.ticket_messages(Config().db, []) == []

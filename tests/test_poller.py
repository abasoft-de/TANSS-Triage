"""
Usage: pytest tests/test_poller.py

Autor: SO, (c) abasoft GmbH 2026-09-16
Datei: test_poller.py
Beschreibung: Prüft den DB-Poller: Cursor-Fortschreibung, Erstlauf mit
              Rückblick, Audio-Filter für bug_files und das Aussetzen bei
              nicht erreichbarer Datenbank (Cursor bleibt dann stehen).
              Dazu die Quellen der Duplikat-Erkennung: neue Tickets mit
              Firma, Firmenzuweisungen und fehlgeschlagene Prüfungen.
Letzte Änderung: 2026-11-16
"""

from tanss_triage import poller as poller_module
from tanss_triage.config import Config
from tanss_triage.poller import (CURSOR_CHANGES, CURSOR_FILES, CURSOR_MAILS,
                                 CURSOR_TICKETS, Poller)
from tanss_triage.state import State


def _setup(tmp_path, monkeypatch, *, max_mail=100, max_file=50,
           mail_rows=None, backlog_rows=None, file_rows=None):
    """Baut Poller mit gefakten db-Funktionen; liefert (poller, state, queue)."""
    monkeypatch.setattr(poller_module.db, "max_mail_attachment_id",
                        lambda cfg: max_mail)
    monkeypatch.setattr(poller_module.db, "audio_mail_tickets",
                        lambda cfg, lo, hi, ext: mail_rows)
    monkeypatch.setattr(poller_module.db, "backlog_audio_tickets",
                        lambda cfg, minutes, ext: backlog_rows)
    monkeypatch.setattr(poller_module.db, "max_bug_file_id",
                        lambda cfg: max_file)
    monkeypatch.setattr(poller_module.db, "bug_files_between",
                        lambda cfg, lo, hi: file_rows)
    cfg = Config()
    cfg.state.db_path = str(tmp_path / "state.db")
    state = State(cfg.state.db_path)
    queued = []
    return Poller(cfg, state, queued.append), state, queued


def test_first_run_uses_backlog_and_sets_cursors(tmp_path, monkeypatch):
    poller, state, queued = _setup(
        tmp_path, monkeypatch,
        backlog_rows=[{"ticket_id": 254125}, {"ticket_id": 254128}])
    poller.tick()
    assert sorted(queued) == [254125, 254128]
    assert state.get_cursor(CURSOR_MAILS) == 100
    assert state.get_cursor(CURSOR_FILES) == 50


def test_new_mail_attachments_are_enqueued(tmp_path, monkeypatch):
    poller, state, queued = _setup(
        tmp_path, monkeypatch,
        mail_rows=[{"ticket_id": 254130}])
    state.set_cursor(CURSOR_MAILS, 90)
    state.set_cursor(CURSOR_FILES, 50)
    poller.tick()
    assert queued == [254130]
    assert state.get_cursor(CURSOR_MAILS) == 100


def test_no_news_no_queries(tmp_path, monkeypatch):
    poller, state, queued = _setup(tmp_path, monkeypatch, mail_rows=None)
    state.set_cursor(CURSOR_MAILS, 100)     # schon auf max
    state.set_cursor(CURSOR_FILES, 50)
    poller.tick()                            # audio_mail_tickets wird nicht
    assert queued == []                      # gebraucht (None stoert nicht)


def test_bug_files_filtered_for_audio(tmp_path, monkeypatch):
    poller, state, queued = _setup(
        tmp_path, monkeypatch,
        file_rows=[
            {"id": 51, "ticket_id": 111, "file_name": "diktat.mp3",
             "mime_type": ""},
            {"id": 52, "ticket_id": 222, "file_name": "brief.pdf",
             "mime_type": "application/pdf"},
        ])
    state.set_cursor(CURSOR_MAILS, 100)
    state.set_cursor(CURSOR_FILES, 40)
    poller.tick()
    assert queued == [111]
    assert state.get_cursor(CURSOR_FILES) == 50


def test_db_down_keeps_cursor(tmp_path, monkeypatch):
    poller, state, queued = _setup(tmp_path, monkeypatch, max_mail=None,
                                   max_file=None)
    state.set_cursor(CURSOR_MAILS, 90)
    poller.tick()
    assert queued == []
    assert state.get_cursor(CURSOR_MAILS) == 90     # nichts verschoben


def test_failed_window_query_does_not_advance_cursor(tmp_path, monkeypatch):
    # max_id da, aber die Fensterabfrage scheitert -> Cursor bleibt stehen,
    # damit der naechste Zyklus dieselben Zeilen noch einmal sieht
    poller, state, queued = _setup(tmp_path, monkeypatch, mail_rows=None)
    state.set_cursor(CURSOR_MAILS, 90)
    state.set_cursor(CURSOR_FILES, 50)
    poller.tick()
    assert queued == []
    assert state.get_cursor(CURSOR_MAILS) == 90


# -- Duplikat-Erkennung ------------------------------------------------------

def _setup_duplicates(tmp_path, monkeypatch, *, max_ticket=260150,
                      max_change=54600, ticket_rows=None, change_rows=None,
                      **audio):
    """Wie _setup, dazu die Quellen der Duplikat-Erkennung; liefert
    (poller, state, queued, duplicates). calls zählt die Fensterabfragen."""
    poller, state, queued = _setup(tmp_path, monkeypatch, **audio)
    calls = []

    def tickets_between(cfg, lo, hi):
        calls.append(("tickets", lo, hi))
        return ticket_rows

    def changes_between(cfg, lo, hi):
        calls.append(("changes", lo, hi))
        return change_rows

    monkeypatch.setattr(poller_module.db, "max_bug_id",
                        lambda cfg: max_ticket)
    monkeypatch.setattr(poller_module.db, "company_tickets_between",
                        tickets_between)
    monkeypatch.setattr(poller_module.db, "max_entity_change_id",
                        lambda cfg: max_change)
    monkeypatch.setattr(poller_module.db, "company_changes_between",
                        changes_between)
    duplicates = []
    poller.enqueue_duplicate = duplicates.append
    poller.calls = calls
    # Audio-Cursor stehen schon auf max - diese Tests schauen nur auf die
    # Duplikat-Quellen
    state.set_cursor(CURSOR_MAILS, 100)
    state.set_cursor(CURSOR_FILES, 50)
    return poller, state, queued, duplicates


def test_duplicate_first_run_only_sets_cursors(tmp_path, monkeypatch):
    poller, state, _, duplicates = _setup_duplicates(
        tmp_path, monkeypatch,
        ticket_rows=[{"ticket_id": 1}], change_rows=[{"ticket_id": 2}])
    poller.tick()
    assert duplicates == []                 # kein Rückblick auf Alttickets
    assert poller.calls == []
    assert state.get_cursor(CURSOR_TICKETS) == 260150
    assert state.get_cursor(CURSOR_CHANGES) == 54600


def test_new_company_tickets_and_assignments_are_enqueued(tmp_path,
                                                          monkeypatch):
    poller, state, queued, duplicates = _setup_duplicates(
        tmp_path, monkeypatch,
        ticket_rows=[{"ticket_id": 260148}, {"ticket_id": 260147}],
        change_rows=[{"ticket_id": 260144}, {"ticket_id": 260148}])
    state.set_cursor(CURSOR_TICKETS, 260140)
    state.set_cursor(CURSOR_CHANGES, 54500)
    poller.tick()
    assert duplicates == [260144, 260147, 260148]    # jedes Ticket einmal
    assert queued == []
    assert poller.calls == [("tickets", 260140, 260150),
                            ("changes", 54500, 54600)]
    assert state.get_cursor(CURSOR_TICKETS) == 260150
    assert state.get_cursor(CURSOR_CHANGES) == 54600


def test_duplicate_sources_rest_when_disabled(tmp_path, monkeypatch):
    poller, state, _, duplicates = _setup_duplicates(
        tmp_path, monkeypatch, ticket_rows=[{"ticket_id": 260148}])
    state.set_cursor(CURSOR_TICKETS, 260140)
    poller.cfg.duplicates.enabled = False
    poller.tick()
    assert duplicates == [] and poller.calls == []
    assert state.get_cursor(CURSOR_TICKETS) == 260140


def test_failed_duplicate_checks_are_enqueued_again(tmp_path, monkeypatch):
    poller, state, _, duplicates = _setup_duplicates(tmp_path, monkeypatch)
    state.set_cursor(CURSOR_TICKETS, 260150)
    state.set_cursor(CURSOR_CHANGES, 54600)
    state.mark_failed("dup:260130", 260130, "LLM weg")
    poller.tick()
    assert duplicates == []                 # Wartezeit noch nicht um
    monkeypatch.setattr(poller_module, "DUPLICATE_RETRY_SECONDS", 0)
    poller.tick()
    assert duplicates == [260130]


def test_failed_duplicate_query_keeps_cursor_but_audio_is_enqueued(
        tmp_path, monkeypatch):
    # entity_changes nicht lesbar: der Cursor bleibt stehen - das neue
    # Audio aus derselben Runde darf deswegen nicht verloren gehen
    poller, state, queued, duplicates = _setup_duplicates(
        tmp_path, monkeypatch, max_change=None,
        ticket_rows=[{"ticket_id": 260148}],
        mail_rows=[{"ticket_id": 254130}])
    state.set_cursor(CURSOR_MAILS, 90)
    state.set_cursor(CURSOR_TICKETS, 260140)
    state.set_cursor(CURSOR_CHANGES, 54500)
    poller.tick()
    assert queued == [254130]
    assert duplicates == [260148]
    assert state.get_cursor(CURSOR_CHANGES) == 54500
    assert state.get_cursor(CURSOR_TICKETS) == 260150

"""
Usage: pytest tests/test_departments.py

Autor: SO, (c) abasoft GmbH 2026-09-23
Datei: test_departments.py
Beschreibung: Prüft die feste Abteilung (jedes Starface-Ticket landet bei der
              HLE, solange es keine Abteilung hat) und den Praxisausfall -
              vom Parsen der LLM-Antwort bis zum Ticket-Update: Betreff-
              Präfix, Fälligkeit und Deadline jetzt, Tag DRINGEND.
Letzte Änderung: 2026-09-23
"""

import json
import time

from tanss_triage.triage import (MAX_SUBJECT_LENGTH, build_system_prompt,
                                 parse_triage_json, run_triage)

from test_processor import (AUDIO_DOCUMENTS, IDENTIFY_EMPLOYEE,
                            STARFACE_HISTORY, STARFACE_TICKET, FakeClient,
                            _config, _processor)


def _answer(**fields):
    data = {"betreff": "Server nicht erreichbar",
            "beschreibung": "Nichts geht mehr.", "melder_id": None}
    data.update(fields)
    return json.dumps(data, ensure_ascii=False)


class _Llm:
    def __init__(self, answer):
        self.answer = answer

    def complete(self, system, user):
        return self.answer


class _TagClient(FakeClient):
    """FakeClient mit Tags und einem Ticket, das Updates übernimmt."""

    def __init__(self, *args, tags=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tags = list(tags or [])
        self.added_tags = []

    def update_ticket(self, ticket_id, ticket):
        super().update_ticket(ticket_id, ticket)
        self.ticket = dict(ticket)

    def get_ticket_tags(self, ticket_id):
        return [{"id": tag_id} for tag_id in self.tags]

    def add_ticket_tag(self, ticket_id, tag_id):
        self.added_tags.append(tag_id)
        self.tags.append(tag_id)


def _run(tmp_path, answer=None, ticket=None, tags=None, cfg=None):
    client = _TagClient(AUDIO_DOCUMENTS, STARFACE_HISTORY,
                        dict(ticket or STARFACE_TICKET),
                        identify=IDENTIFY_EMPLOYEE, tags=tags)
    llm = _Llm(answer) if answer else None
    processor = _processor(cfg or _config(tmp_path), client, llm=llm)
    processor.process_ticket(4711)
    return client


# -- Parsen ------------------------------------------------------------------

def test_outage_is_parsed():
    assert parse_triage_json(_answer(praxisausfall=True)).praxisausfall


def test_only_real_true_counts_as_outage():
    for value in ("true", "ja", 1, None):
        assert parse_triage_json(
            _answer(praxisausfall=value)).praxisausfall is False


def test_empty_transcript_is_no_outage():
    assert run_triage(_Llm(_answer(praxisausfall=True)), "  ") \
        .praxisausfall is False


def test_prompt_has_outage_rule_but_no_department_rules():
    prompt = build_system_prompt()
    assert '"praxisausfall"' in prompt
    assert "Praxisausfall - gesondert kennzeichnen" in prompt
    assert "Abteilung" not in prompt
    assert '"abteilung"' not in prompt
    assert "%(" not in prompt              # alle Platzhalter ersetzt


# -- Abteilung ---------------------------------------------------------------

def test_every_starface_ticket_goes_to_hle(tmp_path):
    client = _run(tmp_path, _answer())
    _, update = client.updates[-1]
    assert update["assignedToDepartmentId"] == 8


def test_hle_is_set_without_llm_too(tmp_path):
    client = _run(tmp_path)
    _, update = client.updates[-1]
    assert update["assignedToDepartmentId"] == 8


def test_existing_department_is_kept(tmp_path):
    ticket = dict(STARFACE_TICKET, assignedToDepartmentId=4)
    client = _run(tmp_path, _answer(), ticket=ticket)
    _, update = client.updates[-1]
    assert update["assignedToDepartmentId"] == 4


def test_department_can_be_switched_off(tmp_path):
    cfg = _config(tmp_path)
    cfg.departments.enabled = False
    client = _run(tmp_path, _answer(), cfg=cfg)
    _, update = client.updates[-1]
    assert not update.get("assignedToDepartmentId")


def test_comment_has_no_department_line(tmp_path):
    client = _run(tmp_path, _answer(praxisausfall=True))
    assert "Abteilung" not in client.comments[0][2]


# -- Praxisausfall -----------------------------------------------------------

def test_outage_sets_prefix_and_dates_but_stays_at_hle(tmp_path):
    before = int(time.time())
    client = _run(tmp_path, _answer(praxisausfall=True))
    _, update = client.updates[-1]
    assert update["title"] == "SERVERAUSFALL !! Server nicht erreichbar"
    assert before <= update["dueDate"] <= int(time.time())
    assert update["deadlineDate"] == update["dueDate"]
    assert update["assignedToDepartmentId"] == 8
    assert client.added_tags == [53]        # DRINGEND


def test_normal_ticket_gets_no_dates(tmp_path):
    client = _run(tmp_path, _answer(praxisausfall=False))
    _, update = client.updates[-1]
    assert "deadlineDate" not in update
    assert not update["title"].startswith("SERVERAUSFALL")


def test_outage_title_stays_within_field_length(tmp_path):
    client = _run(tmp_path, _answer(betreff="Wort " * 25, praxisausfall=True))
    _, update = client.updates[-1]
    assert update["title"].startswith("SERVERAUSFALL !! ")
    assert len(update["title"]) <= MAX_SUBJECT_LENGTH


def test_outage_keeps_manually_changed_title(tmp_path):
    ticket = dict(STARFACE_TICKET, title="Server weg - bin dran")
    client = _run(tmp_path, _answer(praxisausfall=True), ticket=ticket)
    _, update = client.updates[-1]
    assert update["title"] == "Server weg - bin dran"
    assert update["deadlineDate"]           # dringend bleibt es trotzdem


def test_outage_tag_can_be_switched_off(tmp_path):
    cfg = _config(tmp_path)
    cfg.outage.tag_id = 0
    client = _run(tmp_path, _answer(praxisausfall=True), cfg=cfg)
    assert client.added_tags == []
    _, update = client.updates[-1]
    assert update["deadlineDate"]           # der Rest bleibt


def test_tag_is_not_added_twice(tmp_path):
    client = _run(tmp_path, _answer(praxisausfall=True), tags=[53, 72])
    assert client.added_tags == []


def test_outage_handling_can_be_switched_off(tmp_path):
    cfg = _config(tmp_path)
    cfg.outage.enabled = False
    client = _run(tmp_path, _answer(praxisausfall=True), cfg=cfg)
    _, update = client.updates[-1]
    assert not update["title"].startswith("SERVERAUSFALL")
    assert "deadlineDate" not in update
    assert client.added_tags == []


def test_dry_run_writes_nothing(tmp_path):
    cfg = _config(tmp_path)
    cfg.dry_run = True
    client = _run(tmp_path, _answer(praxisausfall=True), cfg=cfg)
    assert client.updates == []
    assert client.added_tags == []

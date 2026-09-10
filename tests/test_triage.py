"""
Usage: pytest tests/test_triage.py

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: test_triage.py
Beschreibung: Prüft das robuste Parsen der LLM-Antwort und die Absicherung
              des Melder-Vorschlags gegen die Kandidatenliste.
Letzte Änderung: 2026-09-10
"""

from tanss_triage.triage import parse_triage_json, run_triage


def test_parses_plain_json():
    result = parse_triage_json(
        '{"betreff": "Fax defekt", "beschreibung": "Fax geht nicht.", '
        '"melder_id": 42}')
    assert result.betreff == "Fax defekt"
    assert result.melder_id == 42


def test_parses_json_inside_code_fence():
    text = "Hier das Ergebnis:\n```json\n{\"betreff\": \"B\", " \
           "\"beschreibung\": \"D\", \"melder_id\": null}\n```\nGruß"
    result = parse_triage_json(text)
    assert result.betreff == "B"
    assert result.melder_id is None


def test_unusable_answer_returns_none():
    assert parse_triage_json("") is None
    assert parse_triage_json("kein json hier") is None
    assert parse_triage_json('{"betreff": "", "beschreibung": "x"}') is None
    assert parse_triage_json('{"betreff": "x"}') is None
    assert parse_triage_json("{kaputt: ja}") is None


def test_melder_id_must_be_positive_int():
    result = parse_triage_json(
        '{"betreff": "B", "beschreibung": "D", "melder_id": "42"}')
    assert result.melder_id is None
    result = parse_triage_json(
        '{"betreff": "B", "beschreibung": "D", "melder_id": -1}')
    assert result.melder_id is None


class _FakeLlm:
    def __init__(self, answer):
        self.answer = answer
        self.seen = None

    def complete(self, system, user):
        self.seen = (system, user)
        return self.answer


def test_run_triage_rejects_unknown_melder_id():
    llm = _FakeLlm('{"betreff": "B", "beschreibung": "D", "melder_id": 99}')
    result = run_triage(llm, "Hallo", contacts=[{"id": 1, "name": "A"}])
    assert result is not None
    assert result.melder_id is None       # 99 stand nicht in der Liste


def test_run_triage_accepts_known_melder_id():
    llm = _FakeLlm('{"betreff": "B", "beschreibung": "D", "melder_id": 7}')
    result = run_triage(llm, "Hallo", caller_number="0712345",
                        contacts=[{"id": 7, "name": "Frau Duft"}])
    assert result.melder_id == 7
    system, user = llm.seen
    assert "0712345" in user
    assert "Frau Duft" in user


def test_run_triage_without_llm():
    assert run_triage(None, "Hallo") is None

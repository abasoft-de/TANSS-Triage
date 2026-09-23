"""
Usage: pytest tests/test_triage.py

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: test_triage.py
Beschreibung: Prüft das robuste Parsen der LLM-Antwort, die Absicherung
              des Melder-Vorschlags gegen die Kandidatenliste, die Kürzung
              des Betreffs auf die Feldlänge und den Betreff-Teil des
              Systemprompts (Hausabkürzungen, leeres Transkript).
Letzte Änderung: 2026-09-21
"""

from tanss_triage.triage import (EMPTY_TRANSCRIPT_SUBJECT,
                                 MAX_SUBJECT_LENGTH, build_system_prompt,
                                 parse_triage_json, run_triage,
                                 shorten_subject)


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


def test_short_subject_stays_untouched():
    subject = "KT liest keine eGKs -> RR 07141 141210"
    assert shorten_subject(subject) == subject


def test_subject_is_cut_at_word_boundary():
    subject = ("Kartenterminal liest keine Gesundheitskarten mehr und "
               "zusätzlich gehen KIM und ePA nicht, Praxis steht still")
    short = shorten_subject(subject)
    assert len(short) <= MAX_SUBJECT_LENGTH
    assert short.endswith(" …")
    assert subject.startswith(short[:-2])      # nur hinten gekürzt
    assert not short[:-2].endswith(" ")        # kein halbes Wort, kein Rest


def test_subject_becomes_single_line():
    assert shorten_subject("KIM  geht\nnicht") == "KIM geht nicht"


def test_parse_enforces_field_length():
    long_subject = "Wort " * 40
    result = parse_triage_json(
        '{"betreff": "%s", "beschreibung": "D"}' % long_subject)
    assert len(result.betreff) <= MAX_SUBJECT_LENGTH


def test_system_prompt_carries_house_style():
    prompt = build_system_prompt()
    for expected in ("RR (Rückruf)", "KT (Kartenterminal)", "ePA",
                     "07141 141210", EMPTY_TRANSCRIPT_SUBJECT):
        assert expected in prompt
    # Die übermittelte Anrufernummer gehört ausdrücklich nicht in den Betreff
    assert "NICHT in den Betreff" in prompt


def test_system_prompt_appends_extra_abbreviations():
    prompt = build_system_prompt(["Hybrid-DRG", "  ", "ePA"])
    kuerzel = next(line for line in prompt.splitlines()
                   if line.strip().startswith("EVA, EVABOX"))
    assert "Hybrid-DRG" in kuerzel
    assert kuerzel.count("ePA,") == 1       # Dubletten fallen weg


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


def test_run_triage_passes_extra_abbreviations():
    llm = _FakeLlm('{"betreff": "B", "beschreibung": "D"}')
    run_triage(llm, "Hallo", extra_abbreviations=["Hybrid-DRG"])
    system, _ = llm.seen
    assert "Hybrid-DRG" in system


def test_run_triage_shortcuts_empty_transcript():
    llm = _FakeLlm('{"betreff": "erfunden", "beschreibung": "erfunden"}')
    result = run_triage(llm, "   \n ")
    assert result.betreff == EMPTY_TRANSCRIPT_SUBJECT
    assert llm.seen is None               # kein LLM-Aufruf für Stille


def test_run_triage_without_llm():
    assert run_triage(None, "Hallo") is None
    assert run_triage(None, "") is None

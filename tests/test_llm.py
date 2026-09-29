"""
Usage: pytest tests/test_llm.py

Autor: SO, (c) abasoft GmbH 2026-09-29
Datei: test_llm.py
Beschreibung: Prüft den Claude-Aufruf ohne Netz: das JSON-Schema geht als
              output_config.format mit, das Ersatzmodell läuft über den
              Beta-Aufruf (abschaltbar), und eine Ablehnung oder eine bei
              max_tokens abgeschnittene Antwort wird zum LlmError statt zu
              einem halben Ticket-Update.
Letzte Änderung: 2026-09-29
"""

from types import SimpleNamespace

import pytest

from tanss_triage.config import LlmConfig
from tanss_triage.llm import FALLBACK_BETA, AnthropicLlm, LlmError
from tanss_triage.triage import TRIAGE_SCHEMA, run_triage

ANSWER = ('{"betreff": "KIM geht nicht", "beschreibung": "KIM streikt.", '
          '"melder_id": null, "praxisausfall": false}')


def _response(stop_reason="end_turn", text=ANSWER, category=None):
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category=category) if category else None,
        content=[SimpleNamespace(type="thinking", thinking=""),
                 SimpleNamespace(type="text", text=text)])


class _Messages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class _Client:
    def __init__(self, response):
        self.messages = _Messages(response)
        self.beta = SimpleNamespace(messages=_Messages(response))


def _llm(response=None, **overrides):
    cfg = LlmConfig(api_key="test", **overrides)
    llm = AnthropicLlm.__new__(AnthropicLlm)   # ohne echtes SDK-Objekt
    llm._cfg = cfg
    llm._client = _Client(response or _response())
    return llm


def test_defaults_are_sonnet_5_5_with_fallback():
    cfg = LlmConfig()
    assert cfg.model == "claude-sonnet-5-5"
    assert cfg.fallbacks == "default"


def test_schema_is_sent_as_output_format():
    llm = _llm()
    assert llm.complete("S", "U", schema=TRIAGE_SCHEMA) == ANSWER
    call = llm._client.beta.messages.calls[-1]
    assert call["output_config"] == {
        "format": {"type": "json_schema", "schema": TRIAGE_SCHEMA}}
    assert call["model"] == "claude-sonnet-5-5"


def test_fallback_goes_through_beta_call():
    llm = _llm()
    llm.complete("S", "U")
    call = llm._client.beta.messages.calls[-1]
    assert call["betas"] == [FALLBACK_BETA]
    assert call["fallbacks"] == "default"
    assert not llm._client.messages.calls


def test_fallback_can_be_switched_off():
    llm = _llm(fallbacks="")
    llm.complete("S", "U", schema=TRIAGE_SCHEMA)
    call = llm._client.messages.calls[-1]
    assert "fallbacks" not in call and "betas" not in call
    assert "output_config" in call
    assert not llm._client.beta.messages.calls


def test_without_schema_no_output_format():
    llm = _llm(fallbacks="")
    llm.complete("S", "U")
    assert "output_config" not in llm._client.messages.calls[-1]


def test_refusal_raises_llm_error():
    llm = _llm(_response("refusal", text="", category="general_harms"))
    with pytest.raises(LlmError, match="general_harms"):
        llm.complete("S", "U", schema=TRIAGE_SCHEMA)


def test_truncated_answer_raises_llm_error():
    llm = _llm(_response("max_tokens", text='{"betreff": "KIM'))
    with pytest.raises(LlmError, match="max_tokens"):
        llm.complete("S", "U", schema=TRIAGE_SCHEMA)


def test_refusal_means_no_triage_result():
    llm = _llm(_response("refusal", text=""))
    assert run_triage(llm, "Hallo, KIM geht nicht.") is None


def test_run_triage_sends_schema():
    llm = _llm()
    result = run_triage(llm, "Hallo, KIM geht nicht.")
    assert result.betreff == "KIM geht nicht"
    call = llm._client.beta.messages.calls[-1]
    assert call["output_config"]["format"]["schema"] is TRIAGE_SCHEMA


def test_schema_matches_parser_fields():
    # Jedes Objekt braucht additionalProperties false, alle Felder sind
    # Pflicht - so verlangt es die API für Structured Outputs.
    assert TRIAGE_SCHEMA["additionalProperties"] is False
    assert set(TRIAGE_SCHEMA["required"]) == set(TRIAGE_SCHEMA["properties"])
    assert set(TRIAGE_SCHEMA["properties"]) == {
        "betreff", "beschreibung", "melder_id", "praxisausfall"}

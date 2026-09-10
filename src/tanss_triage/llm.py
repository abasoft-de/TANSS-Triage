"""
Usage: from tanss_triage.llm import build_llm

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: llm.py
Beschreibung: Abstraktion ueber das Sprachmodell. Zwei Provider: die
              Claude-API (anthropic-SDK, braucht ANTHROPIC_API_KEY) und ein
              OpenAI-kompatibler Endpunkt fuer lokal gehostete Modelle
              (Ollama, vLLM, LM Studio - ueber schlichtes requests, damit
              keine weitere Abhaengigkeit noetig ist). build_llm() liefert
              None, wenn kein Provider konfiguriert ist - der Aufrufer laesst
              den LLM-Schritt dann einfach aus.
Letzte Aenderung: 2026-09-10
"""

import logging

import requests

LOG = logging.getLogger("tanss_triage.llm")


class LlmError(RuntimeError):
    """Der LLM-Aufruf ist fehlgeschlagen (Netz, Quota, Serverfehler)."""


class AnthropicLlm:
    """Claude ueber die offizielle API."""

    def __init__(self, cfg):
        import anthropic                      # erst hier, damit "none" und
        self._client = anthropic.Anthropic(   # openai_compatible das SDK
            api_key=cfg.api_key)              # nicht brauchen
        self._cfg = cfg

    def complete(self, system, user):
        try:
            response = self._client.messages.create(
                model=self._cfg.model,
                max_tokens=self._cfg.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                timeout=self._cfg.timeout)
        except Exception as error:            # SDK wirft eigene Hierarchie
            raise LlmError("Claude-Aufruf fehlgeschlagen: %s" % error) from error
        parts = [block.text for block in response.content
                 if getattr(block, "type", "") == "text"]
        return "".join(parts).strip()


class OpenAiCompatibleLlm:
    """Lokales Modell hinter einem OpenAI-kompatiblen /chat/completions."""

    def __init__(self, cfg):
        self._cfg = cfg
        self._url = cfg.base_url.rstrip("/") + "/chat/completions"

    def complete(self, system, user):
        headers = {"Content-Type": "application/json"}
        if self._cfg.api_key:
            headers["Authorization"] = "Bearer " + self._cfg.api_key
        try:
            response = requests.post(
                self._url, headers=headers, timeout=self._cfg.timeout,
                json={
                    "model": self._cfg.model,
                    "max_tokens": self._cfg.max_tokens,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                })
        except requests.RequestException as error:
            raise LlmError("LLM-Endpunkt nicht erreichbar: %s" % error) from error
        if response.status_code >= 400:
            raise LlmError("LLM-Endpunkt antwortet HTTP %d: %s"
                           % (response.status_code, response.text[:300]))
        try:
            return response.json()["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, ValueError) as error:
            raise LlmError("Unerwartete LLM-Antwortstruktur.") from error


def build_llm(cfg):
    """Liefert den passenden Client oder None (= LLM-Schritt entfaellt)."""
    provider = cfg.resolved_provider()
    if provider == "anthropic":
        return AnthropicLlm(cfg)
    if provider == "openai_compatible":
        return OpenAiCompatibleLlm(cfg)
    LOG.info("Kein LLM konfiguriert - Betreff/Beschreibung bleiben "
             "unangetastet, es gibt nur Transkript und Rufnummern-Zuordnung.")
    return None

"""
Usage: from tanss_triage.llm import build_llm

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: llm.py
Beschreibung: Abstraktion über das Sprachmodell. Zwei Provider: die
              Claude-API (anthropic-SDK, braucht ANTHROPIC_API_KEY) und ein
              OpenAI-kompatibler Endpunkt für lokal gehostete Modelle
              (Ollama, vLLM, LM Studio - über schlichtes requests, damit
              keine weitere Abhängigkeit nötig ist). build_llm() liefert
              None, wenn kein Provider konfiguriert ist - der Aufrufer lässt
              den LLM-Schritt dann einfach aus.
              Mit JSON-Schema erzwingt die Claude-API eine Antwort, die genau
              diesem Schema folgt (Structured Outputs, output_config.format).
              Lehnt das Modell ab, springt serverseitig ein Ersatzmodell ein
              ([llm] fallbacks).
Letzte Änderung: 2026-09-29
"""

import logging

import requests

LOG = logging.getLogger("tanss_triage.llm")


class LlmError(RuntimeError):
    """Der LLM-Aufruf ist fehlgeschlagen (Netz, Quota, Serverfehler)."""


# Beta-Header für fallbacks = "default" (Ersatzmodell nach Ablehnungsgrund).
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicLlm:
    """Claude über die offizielle API."""

    def __init__(self, cfg):
        import anthropic                      # erst hier, damit "none" und
        self._client = anthropic.Anthropic(   # openai_compatible das SDK
            api_key=cfg.api_key)              # nicht brauchen
        self._cfg = cfg

    def complete(self, system, user, schema=None):
        """Antworttext; mit schema garantiert gültiges JSON nach Schema."""
        request = {
            "model": self._cfg.model,
            "max_tokens": self._cfg.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "timeout": self._cfg.timeout,
        }
        if schema:
            request["output_config"] = {
                "format": {"type": "json_schema", "schema": schema}}
        try:
            if self._cfg.fallbacks:
                response = self._client.beta.messages.create(
                    betas=[FALLBACK_BETA], fallbacks=self._cfg.fallbacks,
                    **request)
            else:
                response = self._client.messages.create(**request)
        except Exception as error:            # SDK wirft eigene Hierarchie
            raise LlmError("Claude-Aufruf fehlgeschlagen: %s" % error) from error
        # Bei Ablehnung oder abgeschnittener Antwort gilt das Schema nicht -
        # dann lieber kein Update als ein halbes.
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise LlmError("Claude hat die Anfrage abgelehnt (Kategorie %s)."
                           % (getattr(details, "category", None) or "unbekannt"))
        if response.stop_reason == "max_tokens":
            raise LlmError("Antwort bei max_tokens = %d abgeschnitten - "
                           "[llm] max_tokens erhöhen." % self._cfg.max_tokens)
        parts = [block.text for block in response.content
                 if getattr(block, "type", "") == "text"]
        return "".join(parts).strip()


class OpenAiCompatibleLlm:
    """Lokales Modell hinter einem OpenAI-kompatiblen /chat/completions."""

    def __init__(self, cfg):
        self._cfg = cfg
        self._url = cfg.base_url.rstrip("/") + "/chat/completions"

    def complete(self, system, user, schema=None):
        # schema wird nicht mitgeschickt: lokale Endpunkte unterstützen
        # Structured Outputs sehr unterschiedlich. Der Prompt verlangt JSON,
        # parse_triage_json fängt den Rest ab.
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
    """Liefert den passenden Client oder None (= LLM-Schritt entfällt)."""
    provider = cfg.resolved_provider()
    if provider == "anthropic":
        return AnthropicLlm(cfg)
    if provider == "openai_compatible":
        return OpenAiCompatibleLlm(cfg)
    LOG.info("Kein LLM konfiguriert - Betreff/Beschreibung bleiben "
             "unangetastet, es gibt nur Transkript und Rufnummern-Zuordnung.")
    return None

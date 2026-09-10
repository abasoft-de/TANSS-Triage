"""
Usage: from tanss_triage.transcriber import Transcriber

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: transcriber.py
Beschreibung: Wrapper um faster-whisper. Das Modell wird erst beim ersten
              Transkript geladen (large-v3 sind ~3 GB Download plus etliche
              Sekunden Ladezeit) und dann behalten - der Worker arbeitet
              sequenziell, mehr als eine Instanz braucht niemand.
              faster-whisper dekodiert über PyAV, ein systemweites ffmpeg
              ist nicht nötig; damit sind wav, mp3, m4a, ogg usw. abgedeckt.
Letzte Änderung: 2026-09-10
"""

import logging
from dataclasses import dataclass

LOG = logging.getLogger("tanss_triage.whisper")


@dataclass
class Transcript:
    text: str
    duration: float          # Sekunden Audiomaterial
    language: str


class Transcriber:
    def __init__(self, cfg):
        """cfg ist eine WhisperConfig (siehe config.py)."""
        self._cfg = cfg
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel   # Import kostet Zeit
            LOG.info("Lade Whisper-Modell %s (device=%s, compute=%s) ...",
                     self._cfg.model, self._cfg.device, self._cfg.compute_type)
            self._model = WhisperModel(
                self._cfg.model,
                device=self._cfg.device,
                compute_type=self._cfg.compute_type,
                download_root=self._cfg.download_root or None)
            LOG.info("Whisper-Modell geladen.")
        return self._model

    def transcribe(self, path):
        """Transkribiert eine Audiodatei vollständig.

        Die Segmente kommen als Generator - erst das Ausiterieren
        transkribiert wirklich. VAD filtert Stille am Anfang/Ende der
        Voicemail (Ansageton, Aufleger), verkürzt also nur, was ohnehin
        keine Sprache ist.
        """
        model = self._ensure_model()
        segments, info = model.transcribe(
            path,
            language=self._cfg.language or None,
            vad_filter=True)
        parts = [segment.text.strip() for segment in segments]
        text = " ".join(part for part in parts if part).strip()
        return Transcript(text=text,
                          duration=float(info.duration or 0.0),
                          language=info.language or self._cfg.language)

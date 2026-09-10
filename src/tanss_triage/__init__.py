"""
Usage: import tanss_triage

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: __init__.py
Beschreibung: TANSS-Triage - transkribiert Sprachnachrichten aus TANSS-Tickets
              (lokales Whisper) und bereitet Starface-Tickets per LLM auf.
              Die Versionsnummer kommt aus der VERSION-Datei im Projektstamm,
              damit sie auch ohne Python lesbar ist (cat VERSION) und ein
              Deploy sie als gewöhnliche Datei mitnimmt.
Letzte Änderung: 2026-09-10
"""

import os

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


def _read_version():
    """Programmversion aus der VERSION-Datei im Projektstamm.

    Fehlt sie, ist das ein unvollständiges Deploy - 0.0.0 macht das
    sichtbar, ohne den Start zu verhindern.
    """
    try:
        with open(os.path.join(_BASE_DIR, "VERSION"), "r",
                  encoding="utf-8") as handle:
            first = handle.readline().strip()
            return first or "0.0.0"
    except OSError:
        return "0.0.0"


__version__ = _read_version()

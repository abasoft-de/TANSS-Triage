"""
Usage: import tanss_triage

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: __init__.py
Beschreibung: TANSS-Triage - transkribiert Sprachnachrichten aus TANSS-Tickets
              (lokales Whisper) und bereitet Starface-Tickets per LLM auf.
              Hier wird der Projektstamm bestimmt (dort liegen .env,
              config.toml, VERSION und state.db) und daraus die Version
              gelesen - als Textdatei, damit sie auch ohne Python lesbar ist
              (cat VERSION) und ein Deploy sie als gewöhnliche Datei mitnimmt.
Letzte Änderung: 2026-09-15
"""

import os


def _find_base_dir(environ=None):
    """Bestimmt den Projektstamm (.env, config.toml, VERSION, state.db).

    Drei Kandidaten in dieser Reihenfolge:
    1. TANSS_TRIAGE_HOME - der explizite Weg, wenn das Programm aus einem
       fremden Arbeitsverzeichnis laufen soll.
    2. Der Quellbaum: bei einer editierbaren Installation (pip install -e .)
       liegt dieses Modul unter <projekt>/src/tanss_triage/, drei Ebenen
       über der VERSION-Datei.
    3. Das Arbeitsverzeichnis: nach "pip install ." liegt das Modul in
       site-packages und sagt nichts über den Projektort - systemd setzt
       WorkingDirectory auf den Projektordner, und der manuelle Start
       macht vorher cd dorthin.
    """
    environ = os.environ if environ is None else environ
    explicit = environ.get("TANSS_TRIAGE_HOME", "").strip()
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    source_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    if os.path.isfile(os.path.join(source_root, "VERSION")):
        return source_root
    return os.getcwd()


BASE_DIR = _find_base_dir()


def _read_version():
    """Programmversion aus der VERSION-Datei im Projektstamm.

    Fehlt sie, läuft das Programm nicht im Projektordner (oder das Deploy
    ist unvollständig) - 0.0.0 macht das sichtbar, ohne den Start zu
    verhindern.
    """
    try:
        with open(os.path.join(BASE_DIR, "VERSION"), "r",
                  encoding="utf-8") as handle:
            first = handle.readline().strip()
            return first or "0.0.0"
    except OSError:
        return "0.0.0"


__version__ = _read_version()

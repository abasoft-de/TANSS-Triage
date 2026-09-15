"""
Usage: from tanss_triage.logging_setup import setup_logging

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: logging_setup.py
Beschreibung: Logging auf die Konsole (unter systemd landet das im Journal)
              und optional in eine rotierende Datei. Bewusst schlicht - wer
              mehr braucht, hängt sich an das Standard-Logging.
Letzte Änderung: 2026-09-10
"""

import logging
import logging.handlers


def setup_logging(cfg):
    """cfg ist eine LoggingConfig (level, file)."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, (cfg.level or "INFO").upper(),
                          logging.INFO))
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    if cfg.file:
        rotating = logging.handlers.RotatingFileHandler(
            cfg.file, maxBytes=10 * 1024 * 1024, backupCount=3,
            encoding="utf-8")
        rotating.setFormatter(formatter)
        root.addHandler(rotating)

    # Fremdbibliotheken sollen nicht jedes HTTP-Detail ins Log kippen.
    for noisy in ("urllib3", "httpx", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # huggingface_hub warnt bei jedem anonymen Hub-Kontakt vor Rate-Limits
    # ("set a HF_TOKEN") - für den einmaligen Modell-Download belanglos.
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)

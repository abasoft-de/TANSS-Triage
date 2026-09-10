"""
Usage: python -m tanss_triage

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: __main__.py
Beschreibung: Einstieg fuer python -m tanss_triage; reicht an cli.main durch.
Letzte Aenderung: 2026-09-10
"""

import sys

from .cli import main

sys.exit(main())

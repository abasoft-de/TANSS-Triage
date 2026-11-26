"""
Usage: pytest tests/test_masking.py

Autor: SO, (c) abasoft GmbH 2026-11-26
Datei: test_masking.py
Beschreibung: Prüft das Schwärzen von Passwörtern vor dem LLM-Aufruf:
              Schlüsselwort-Regel, passwortartige Wörter, Base64, die nie
              geschwärzten Strukturen (URL, E-Mail, IP, Datum, Produktnamen)
              und die Einstellungen aus [masking]. Alle Beispiel-Passwörter
              sind erfunden.
Letzte Änderung: 2026-11-26
"""

import pytest

from tanss_triage import masking
from tanss_triage.masking import MASK, mask_secrets


@pytest.fixture(autouse=True)
def default_settings():
    masking.configure()
    yield
    masking.configure()


@pytest.mark.parametrize("text, expected", [
    # Schlüsselwort mit Doppelpunkt: das nächste Wort, egal wie es aussieht
    ("Passwort: sonnenblume", "Passwort: " + MASK),
    ("Das Kennwort = Ostern", "Das Kennwort = " + MASK),
    ("PW:geheim42 bitte ändern", "PW:" + MASK + " bitte ändern"),
    # über den Zeilenumbruch hinweg
    ("Zugangsdaten für das Postfach\nPasswort:\n\nFlieder77!",
     "Zugangsdaten für das Postfach\nPasswort:\n\n" + MASK),
    # ohne Doppelpunkt nur Wörter mit Ziffer oder Sonderzeichen
    ("Die PIN 4711 ist gesperrt", "Die PIN " + MASK + " ist gesperrt"),
    ("PIN gesperrt, bitte PUK schicken", "PIN gesperrt, bitte PUK schicken"),
    ("Passwort vergessen", "Passwort vergessen"),
])
def test_keyword_rule(text, expected):
    assert mask_secrets(text) == expected


@pytest.mark.parametrize("word", [
    "Sommer2024!", "kT9mP2qX", "Kwerl+Tamon731", "Rx#7vLp2",
])
def test_password_like_words_are_masked(word):
    assert mask_secrets("Neues Postfach %s eingerichtet." % word) == \
        "Neues Postfach %s eingerichtet." % MASK


def test_surrounding_punctuation_stays():
    assert mask_secrets("(kT9mP2qX), danke") == "(%s), danke" % MASK


@pytest.mark.parametrize("text", [
    "Eternus02 meldet BATTERY#0/1 EXPIRATION",          # Produktname
    "GA-Build411 und MR-1-Build490 installiert",          # Trenner
    "SUSEServer1.0 läuft, Port1,Port4 frei",
    "siehe https://wa.me/+499342962180?text=Hallo123",    # URL
    "Mail an Praxis.Mueller2024@example.de",              # E-Mail
    "Server 192.168.38.13:8080 und 2a01:4f8:c2c:ce57::1", # IP-Adressen
    "Termin 02.12.2026 10:00, Tel. +49(0)7141/121210",    # Datum, Telefon
    "Pfad C:\\EVA\\Befunde2026 und \\\\srv01\\Daten",      # Pfade
    "Ticket #260140, Fehler 0xc0000225",
    'charset="utf-8" <tel:+49-9342-962130>',             # Markup
    "Weinhold, Renate *27.03.62",
    "Wir brauchen dringend einen Rückruf zur Röntgenanlage.",
    # Fehltreffer aus dem Test mit echten Tickets (26.11.2026)
    "Befund2PACS, Orga930M und iPhone15Pro",              # Produktnamen
    "[Ticket#2026112210000121] und Ticket#259570, iQ#10003453",
    "2xeForms+ 2xLenovo Idea Tab",                         # Mengen
    "Werte 53(Fallakte) und 0653123(Ziel-HbA1c,",          # Klammern
])
def test_ordinary_text_stays(text):
    assert mask_secrets(text) == text


def test_base64_lines_are_masked():
    line = "MzgzRDk8L0NvbXB1dGVyPg0KICAgIDxPYmplY3RTaXplPjIzNjA1MzI8L09iamVjdFNp"
    assert mask_secrets("Anhang:\n%s\nEnde" % line) == \
        "Anhang:\n%s\nEnde" % MASK


def test_whitespace_and_lines_are_kept():
    text = "Zeile 1\n\n  eingerückt  kT9mP2qX\tTab\n"
    assert mask_secrets(text) == "Zeile 1\n\n  eingerückt  %s\tTab\n" % MASK


def test_keep_words_and_disabling():
    text = "Backup Veeam_WinSRV2K19 fehlgeschlagen"
    assert mask_secrets(text) == "Backup %s fehlgeschlagen" % MASK
    masking.configure(keep_words=["Veeam_WinSRV2K19"])
    assert mask_secrets(text) == text
    masking.configure(enabled=False)
    assert mask_secrets("Passwort: Flieder77!") == "Passwort: Flieder77!"


def test_empty_input():
    assert mask_secrets("") == ""
    assert mask_secrets(None) is None


def test_prompts_are_masked():
    from tanss_triage.duplicates import (build_detail_prompt,
                                         build_shortlist_prompt)
    from tanss_triage.triage import build_system_prompt, run_triage

    ticket = {"id": 1, "title": "Postfach eingerichtet", "created": 0,
              "content": "Benutzer: praxis\nPasswort: Flieder77!"}
    candidate = {"id": 2, "title": "Zugang kT9mP2qX", "created": 0,
                 "content": "", "status": "neu"}
    for prompt in (build_shortlist_prompt(ticket, [candidate]),
                   build_detail_prompt(ticket, [candidate], {}, 3, 3)):
        assert "Flieder77!" not in prompt and "kT9mP2qX" not in prompt
        assert MASK in prompt

    class RecordingLlm:
        def complete(self, system, user, schema=None):
            self.user = user
            return '{"betreff": "x", "beschreibung": "y", "melder_id": null}'

    llm = RecordingLlm()
    run_triage(llm, "Mein Passwort ist geheim, die PIN 4711.")
    assert "4711" not in llm.user and MASK in llm.user
    assert MASK in build_system_prompt()

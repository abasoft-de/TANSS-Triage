"""
Usage: from tanss_triage.masking import mask_secrets

Autor: SO, (c) abasoft GmbH 2026-11-26
Datei: masking.py
Beschreibung: Schwärzt Passwörter und ähnliche Geheimnisse, bevor ein Text an
              das LLM geht. Tickettexte enthalten mitunter Zugangsdaten im
              Klartext (Postfach-Passwörter in Mails, PINs in Notizen) - die
              gehören nicht in einen API-Aufruf. Drei Regeln, erprobt an gut
              12.000 echten Tickettexten (Stand 26.11.2026):
              1. Schlüsselwort davor ("Passwort:", "PIN 1234"): mit Doppel-
                 punkt oder "=" das nächste Wort, sonst nur eins mit Ziffer
                 oder Sonderzeichen ("PIN gesperrt" bleibt).
              2. Passwortartige Wörter: ein Teil zwischen den Trennern
                 - _ . / : , @ \\ hat Kleinbuchstaben, Ziffern und Groß-
                 buchstaben oder Sonderzeichen und wechselt mindestens
                 viermal die Zeichenart ("Sommer2024!", "kT9mP2qX"). Produkt-
                 namen wie "Eternus02", "Orga930M" oder "GA-Build411",
                 fremde Ticketnummern ("Ticket#259570") und Mengen
                 ("2xLenovo") bleiben stehen.
              3. Base64-Zeilen - kein Geheimnis, aber Rauschen.
              Bekannte Strukturen (URL, E-Mail, IP, Zahlen und Datum,
              Pfade, Ticketnummern, Markup) werden nie geschwärzt. Was nicht
              erkannt wird: Wort plus Ziffern ohne Schlüsselwort
              ("Sommer2024" sieht aus wie "Eternus02"), Passphrasen aus
              Kleinbuchstaben, gesprochene Passwörter.
Letzte Änderung: 2026-11-26
"""

import re

MASK = "[geschwärzt]"

# Einstellungen aus [masking]; configure() setzt sie beim Start.
_settings = {"enabled": True, "keep_words": frozenset()}

KEYWORD = re.compile(
    r"(?i)(?:^|\s)(passw(?:or)?[dt]|kennwort|pw|pwd|pin|puk|"
    r"zugangsdaten|zugangscode)\s*([:=])?\s*$")
# Schlüsselwort und Geheimnis in einem Wort: "PW:geheim42", "Passwort=..."
KEYWORD_INLINE = re.compile(
    r"(?i)^(passw(?:or)?[dt]|kennwort|pw|pwd|pin|puk|zugangscode)[:=](.+)$")

SAFE = (
    re.compile(r"(?i)^(https?|ftp)://|^www\."),              # URL
    re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$"),              # E-Mail
    re.compile(r"(?i)^cid:"),                                 # Bildverweis
    re.compile(r"=[0-9A-F]{2}"),                              # Quoted-Printable
    re.compile(r"^\d{1,3}(\.\d{1,3}){3}(:\d+)?(/\d+)?$"),     # IP-Adresse
    re.compile(r"^[\d.,:;/+\-()x%€$]+$"),                      # Zahl, Datum, Tel.
    re.compile(r"^[A-Za-z]:\\|^\\\\"),                        # Pfade
    re.compile(r"^#\d+$"),                                    # Ticketnummer
    # Fremde Ticketnummern: "Ticket#259570", "[Ticket#2026112210000121]",
    # "iQ#10003453"
    re.compile(r"^\[?[A-Za-z]{1,12}#\d+\]?$"),
    # Ein Buchstaben-, ein Ziffern-, höchstens noch ein Buchstabenblock:
    # Gerätemodelle und Produktnamen ("Orga930M", "Befund2PACS",
    # "iPhone15Pro", "Eternus02")
    re.compile(r"^[A-Za-z]+\d+[A-Za-z]*$"),
    # Mengenangaben: "2xLenovo", "2xeForms+"
    re.compile(r"^\d+x[A-Za-z][A-Za-z-]*\+?$"),
)
MARKUP = re.compile(r"[<>\"]|&\w+;|^&")
# Trenner, an denen ein Wort für Regel 2 in Teile zerfällt - Klammern
# gehören dazu ("53(Fallakte)", Laborwerte wie "HbA1c(Ziel)")
SEPARATORS = re.compile(r"[-_./:,@\\()\[\]]+")
BASE64_LINE = re.compile(r"^[A-Za-z0-9+/=]{60,}$")
# Satzzeichen um ein Wort herum, die stehen bleiben - ohne "!" und "?":
# die gehören in Passwörtern oft dazu ("Sommer2024!")
EDGE = ".,;:()[]{}\"'<>“”„"
# Nach einem Schlüsselwort sind nur diese Strukturen ausgenommen - eine
# Zahl nach "PIN" ist gerade das Geheimnis
KEYWORD_SAFE = SAFE[:3]


def configure(enabled=True, keep_words=()):
    """Übernimmt [masking] aus der Konfiguration (einmal beim Start)."""
    _settings["enabled"] = bool(enabled)
    _settings["keep_words"] = frozenset(word for word in keep_words or ()
                                        if word)


def _char_class(char):
    if char.isupper():
        return "A"
    if char.islower():
        return "a"
    if char.isdigit():
        return "9"
    if char in "-_.":
        return None          # Trenner zählen nicht als Sonderzeichen
    return "+"


def _runs(part):
    """Anzahl der Abschnitte gleicher Zeichenart."""
    count, last = 0, None
    for char in part:
        kind = _char_class(char)
        if kind != last:
            if kind is not None:
                count += 1
            last = kind
    return count


def _part_secret(part):
    if len(part) < 6:
        return False
    kinds = {_char_class(char) for char in part}
    return ("a" in kinds and "9" in kinds and ("A" in kinds or "+" in kinds)
            and _runs(part) >= 4)


def _is_safe(word):
    return any(rule.search(word) for rule in SAFE)


def looks_secret(word):
    """Regel 2: passwortartiges Wort (8-40 Zeichen)."""
    if not 8 <= len(word) <= 40 or _is_safe(word) or MARKUP.search(word):
        return False
    return any(_part_secret(part) for part in SEPARATORS.split(word))


def _keyword_secret(word, keyword):
    """Regel 1: Wort nach einem Schlüsselwort."""
    if len(word) < 4 or any(rule.search(word) for rule in KEYWORD_SAFE):
        return False
    if keyword.group(2):                 # "Passwort:" / "PIN ="
        return True
    return bool(re.search(r"\d|[^\w\s\-.]", word))


def mask_secrets(text):
    """Ersetzt Geheimnisse durch [geschwärzt]; Zeilen und Leerraum bleiben.

    Das Schlüsselwort darf auch am Ende der vorigen Zeile stehen
    ("Passwort:" und darunter das Passwort).
    """
    if not text or not _settings["enabled"]:
        return text
    keep = _settings["keep_words"]
    pieces = []
    position = 0
    previous = []                        # die letzten Wörter, roh
    for match in re.finditer(r"\S+", text):
        raw = match.group(0)
        word = raw.strip(EDGE)
        replacement = None
        if word and word not in keep:
            inline = KEYWORD_INLINE.match(word)
            keyword = KEYWORD.search(" ".join(previous[-2:]) + " ")
            if inline and len(inline.group(2)) >= 4:
                replacement = raw.replace(
                    word, "%s%s" % (word[:len(word) - len(inline.group(2))],
                                    MASK), 1)
            elif ((keyword and _keyword_secret(word, keyword))
                  or BASE64_LINE.match(word) or looks_secret(word)):
                replacement = raw.replace(word, MASK, 1)
        pieces.append(text[position:match.start()])
        pieces.append(replacement if replacement is not None else raw)
        position = match.end()
        previous.append(raw)
    pieces.append(text[position:])
    return "".join(pieces)

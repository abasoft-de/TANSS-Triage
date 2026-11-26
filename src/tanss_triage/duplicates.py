"""
Usage: from tanss_triage.duplicates import DuplicateChecker

Autor: SO, (c) abasoft GmbH 2026-11-16
Datei: duplicates.py
Beschreibung: Erkennt potenzielle Ticket-Duplikate. Geprüft wird ein Ticket,
              sobald es erstmals bei einer Kundenfirma liegt - gegen die
              offenen Tickets desselben Kunden, in drei Stufen: (0) ganz ohne
              LLM: das neue Ticket nennt die Nummer eines offenen Tickets
              oder trägt exakt denselben Betreff; (1) das LLM sichtet Betreffs
              und Beschreibungsanfänge und nennt sichere Duplikate und
              unklare Kandidaten; (2) nur für die unklaren bekommt es
              Beschreibung, Nachrichten der Praxis (weitere Mails,
              Transkripte weiterer Anrufe) und Leistungen (bei vielen die
              ersten und letzten paar, geplante Termine gesondert) und
              entscheidet. Bei Verdacht bekommt das neue
              Ticket den Tag "Prüfen! pot. Duplikat" und einen Kommentar
              mit Verweis auf das andere Ticket. Der State-Schlüssel
              "dup:<Ticket>" sorgt dafür, dass jedes Ticket genau einmal
              geprüft wird; solange es keine prüfbare Firma oder noch den
              generischen Starface-Betreff hat, bleibt die Prüfung offen.
              Jeder Treffer trägt eine Begründung - vom LLM oder, ohne
              LLM, die Regel, die gegriffen hat.
Letzte Änderung: 2026-11-26
"""

import logging
import re
import time

from .llm import LlmError
from .masking import MASK, mask_secrets
from .processor import ticket_url
from .tanss_client import TanssApiError
from .triage import EMPTY_TRANSCRIPT_SUBJECT, extract_json_object

LOG = logging.getLogger("tanss_triage.duplicates")

KEY_PREFIX = "dup:"

# Kommentartitel (für das erste Duplikat) und Überschrift jedes weiteren.
COMMENT_HEADING = "Potenzielles Duplikat von Ticket #%d"
# Trennlinie zwischen den Duplikaten im Kommentartext.
COMMENT_SEPARATOR = "-" * 100

# Begründungen der Treffer ohne LLM (Stufe 0).
REASON_MENTIONED = "Das neue Ticket nennt die Ticketnummer #%d."
REASON_SAME_TITLE = "Beide Tickets tragen denselben Betreff."

# State-Vermerk, wenn TANSS dem API-Benutzer das Ticket verweigert (403).
NO_ACCESS_DETAIL = "kein Zugriff auf das Ticket (403)"

# Zeichenobergrenzen in den Prompts: genug zum Wiedererkennen eines
# Anliegens, ohne dass eine weitergeleitete Mail-Kette den Aufruf sprengt.
CONTENT_LIMIT = 1500            # Beschreibung des neuen Tickets
CANDIDATE_EXCERPT_LIMIT = 600   # Beschreibungsanriss je Kandidat (Stufe 1)
DETAIL_CONTENT_LIMIT = 2000     # Beschreibung je Ticket in Stufe 2
SERVICE_TEXT_LIMIT = 600        # je Leistung in Stufe 2

# Kürzere Zahlen im Tickettext gelten nie als Ticketnummer - Hausnummern,
# Uhrzeiten und Ziffern der Abrechnung sind zu häufig.
MIN_TICKET_NUMBER_DIGITS = 5

# Leistungen, die nicht ins Prompt gehen, weil sie nichts Eigenes sagen:
# - "[Automatische Protokollierung des Ticket-E-Mail-Versandes]": jede Mail,
#   die wir aus dem Ticket verschicken, steht zusätzlich als Leistung da
#   (5.939 in 90 Tagen, Stand 26.11.2026) - die Mail selbst liegt am
#   Ticket, und zitierte Eingangsbestätigungen ("Ihr Ticket wurde
#   automatisch erfasst") stecken auch darin.
# - "[System] ...": Statusänderungen der Firma, Sperren/Entsperren.
SERVICE_IGNORE_PREFIXES = ("[Automatische Protokollierung", "[System]")
# Leistungen, deren ganzer Text nur vermerkt, dass eine automatische Antwort
# kam (Kolleg*innen tragen das so ein) - Vergleich ohne Schreibung.
SERVICE_IGNORE_TEXTS = ("automatische rückantwort",)

# Zeilen, die der Mailserver jeder eingehenden Mail voranstellt und die
# nichts über das Anliegen sagen.
BOILERPLATE_PREFIXES = ("WARNUNG: EXTERNE NACHRICHT",)
# Kopfzeilen des Kontaktformulars, die in der Anzeige wegfallen (siehe
# compact_form); "Nachricht:" allein auf der Zeile kommt dazu.
FORM_HEADER_PREFIXES = ("Praxis: Kundenkürzel", "Kubez aus System:",
                        "EIO aus System:", "Datum:")

# Leistungen mit Datum in der Zukunft sind vorab eingetragene Termine. Sie
# kommen gesondert ins Prompt, höchstens so viele (die nächsten zuerst) -
# sonst verdrängten sie als "letzte" Leistungen den aktuellen Stand.
MAX_PLANNED_SERVICES = 2

# -- Nachrichten der Praxis: eingehende Mails und unsere Transkripte
MESSAGE_TEXT_LIMIT = 600        # je Nachricht in Stufe 2
MESSAGE_EXCERPT_LIMIT = 250     # jüngste weitere Nachricht in Stufe 1
# Eine Mail, deren Anfang (so viele Zeichen) in der Beschreibung steht, ist
# die, aus der das Ticket entstand - die steht schon als Beschreibung da.
ORIGIN_MATCH_CHARS = 150
# Ein Transkript, das so kurz nach der Ticketanlage entstand, gehört zur
# Sprachnachricht, aus der das Ticket entstand (Starface legt das Ticket an,
# TANSS-Triage transkribiert binnen Minuten).
VOICEMAIL_ORIGIN_SECONDS = 15 * 60
# Unsere Transkript-Kommentare (processor._build_comment): Titel
# "Transkript: <Datei>", erste Zeile "Zuordnung: ...", leere Aufnahmen
# "(keine Sprache erkannt)". Ältere Versionen setzten eine Markerzeile.
TRANSCRIPT_TITLE_PREFIX = "Transkript:"
TRANSCRIPT_SKIP_PREFIXES = ("Zuordnung:", "[TANSS-Triage")
EMPTY_TRANSCRIPT_TEXT = "(keine Sprache erkannt)"
# Beginn eines Zitats in Antwortmails - was darunter steht, ist meist unsere
# eigene Mail oder ein älterer Teil des Verlaufs.
QUOTE_START = re.compile(
    r"^(-{2,}\s*(Ursprüngliche Nachricht|Original Message)"
    r"|Am .{4,80} schrieb .{0,80}:$|On .{4,80} wrote:$|>)", re.IGNORECASE)
# Bleibt nach dem Abschneiden des Zitats weniger übrig, war es eine
# Weiterleitung - dann zählt der ganze Text.
MIN_REPLY_CHARS = 40

DEFINITION = """\
Du prüfst in einem IT-Ticketsystem (TANSS), ob ein neues Ticket ein Duplikat
eines schon offenen Tickets desselben Kunden ist. Die Kunden sind
Arztpraxen, betreut von einem IT-Systemhaus (Praxissoftware EVA). Dasselbe
Anliegen kommt oft mehrfach an: als Sprachnachricht, als E-Mail und als
Anruf, auch von verschiedenen Personen derselben Praxis.

Ein Duplikat liegt vor, wenn beide Tickets dasselbe Anliegen bzw. denselben
Vorfall betreffen - auch wenn sie ihn mit anderen Worten, über einen anderen
Kanal oder von einer anderen Person melden. Kein Duplikat ist ein Ticket,
das nur dasselbe Thema, Programm oder Gerät berührt, aber ein anderes
Problem oder einen anderen Auftrag beschreibt (zwei verschiedene Drucker,
zwei verschiedene Abrechnungsfragen, ein neuer Auftrag neben einem
laufenden).

Neue Tickets ohne konkretes Anliegen - nur eine Rückrufbitte, ein
Stichwort wie "Drucker", "ePA" oder "KIM Fehler", eine leere oder
nichtssagende Beschreibung - sind nur dann ein Duplikat, wenn ein konkreter
Bezug besteht: dieselbe Person oder Rückrufnummer, dasselbe Gerät oder
derselbe Arbeitsplatz, ein ausdrücklicher Verweis auf das frühere Anliegen
("habe schon angerufen", "warten immer noch") oder eine Nachfrage zum
erkennbar selben Thema. Dasselbe Thema allein reicht bei solchen Tickets
nicht.

Zu einem Ticket können weitere Nachrichten der Praxis gehören: Mails, die
später eingingen, und Transkripte weiterer Sprachnachrichten. Ungeduldige
Nachfragen und wiederholte Anrufe zum selben Anliegen sind typische
Duplikate - und dort steht das Anliegen oft in den eigenen Worten der
Praxis.

Betreffs stehen oft im Telegrammstil mit Hausabkürzungen (KIM, ePA, eRP,
eAU, KT = Kartenterminal, AP = Arbeitsplatz, DaSi = Datensicherung,
RR = Rückruf). Beschreibungen aus E-Mails enthalten Formularköpfe (Praxis,
Name, Telefon, Datum), Grußformeln und Signaturen - die sagen nichts über
das Anliegen, Name und Telefon aber darüber, wer schreibt. Wörter, die wie
Passwörter oder Zugangsdaten aussehen, sind durch """ + MASK + """ ersetzt -
erwähne sie nicht.

Jedes genannte Ticket bekommt eine "begruendung": ein bis drei Sätze für
die Kolleg*innen, die den Hinweis im Ticket prüfen - was beide Tickets
verbindet und, wenn du nicht sicher bist, was dagegen spricht. Sachlich,
ohne Anrede, ohne die Ticketnummern zu wiederholen."""

SHORTLIST_SYSTEM_PROMPT = DEFINITION + """

Du bekommst das neue Ticket (Betreff und Beschreibung) und die Liste der
offenen Tickets des Kunden: Nummer, Anlagedatum, Status, Betreff, der
Anfang der Beschreibung und - falls vorhanden - der Anfang der jüngsten
weiteren Nachricht der Praxis.

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt, ohne Erklärtext davor oder
danach:

{"kandidaten": [{"ticket_id": 1234, "einschaetzung": "duplikat",
                 "begruendung": "ein bis drei Sätze"}]}

- "duplikat": Betreff und Beschreibungsanfang genügen, um zu sagen, dass es
  sehr wahrscheinlich dasselbe Anliegen ist. Die Begründung steht so im
  Hinweis am Ticket.
- "unklar": Es könnte dasselbe Anliegen sein, lässt sich so aber nicht
  entscheiden. Für diese Tickets bekommst du anschließend die vollständige
  Beschreibung, die Nachrichten der Praxis und die Leistungen; die
  Begründung sagt hier kurz, was für und was gegen dasselbe Anliegen
  spricht.
- Tickets, die erkennbar etwas anderes betreffen, lässt du weg. Die meisten
  neuen Tickets sind keine Duplikate - eine leere Liste ist die häufigste
  richtige Antwort.
- Nenne nur Ticketnummern aus der Liste, die wahrscheinlichsten zuerst."""

VERDICT_SYSTEM_PROMPT = DEFINITION + """

Du bekommst das neue Ticket und einige offene Tickets desselben Kunden, bei
denen der Betreff allein nicht reichte - jeweils mit Beschreibung, den
Nachrichten der Praxis (weitere Mails, Transkripte von Sprachnachrichten,
jeweils mit Datum) und den Leistungen (den Arbeitsnotizen der
Techniker; bei vielen nur die ersten und die letzten, dazu vorab
eingetragene Termine als "geplant"). Entscheide für jedes, ob das neue
Ticket dasselbe Anliegen meldet.

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt, ohne Erklärtext davor oder
danach:

{"duplikate": [{"ticket_id": 1234, "begruendung": "ein bis drei Sätze"}]}

- Nimm ein Ticket auf, wenn es wahrscheinlich dasselbe Anliegen ist; ein
  Mensch prüft den Hinweis nach. Bloße Themenverwandtschaft reicht nicht.
- Kein Duplikat dabei: eine leere Liste.
- Nenne nur Ticketnummern aus den vorgelegten Tickets, die
  wahrscheinlichsten zuerst."""

# Antwortformate für Structured Outputs (siehe triage.TRIAGE_SCHEMA).
SHORTLIST_SCHEMA = {
    "type": "object",
    "properties": {
        "kandidaten": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticket_id": {"type": "integer"},
                    "einschaetzung": {"type": "string",
                                      "enum": ["duplikat", "unklar"]},
                    "begruendung": {"type": "string"},
                },
                "required": ["ticket_id", "einschaetzung", "begruendung"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["kandidaten"],
    "additionalProperties": False,
}

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "duplikate": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticket_id": {"type": "integer"},
                    "begruendung": {"type": "string"},
                },
                "required": ["ticket_id", "begruendung"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["duplikate"],
    "additionalProperties": False,
}


def item_key(ticket_id):
    """State-Schlüssel der Duplikatprüfung eines Tickets."""
    return "%s%d" % (KEY_PREFIX, int(ticket_id))


def normalize_title(title):
    """Betreff für den Vergleich: Leerraum zusammengezogen, Schreibung egal."""
    return " ".join((title or "").split()).casefold()


def exact_title_matches(title, candidates):
    """Stufe 0: Kandidaten mit exakt gleichem Betreff, Reihenfolge bleibt.

    Passiert vor allem bei mehreren Anrufen zum selben Vorfall, für die
    das LLM denselben Betreff vergibt - dafür braucht es keinen Aufruf.
    """
    wanted = normalize_title(title)
    if not wanted:
        return []
    return [candidate for candidate in candidates
            if normalize_title(candidate.get("title")) == wanted]


def mentioned_ticket_matches(ticket, candidates):
    """Stufe 0: Kandidaten, deren Ticketnummer das neue Ticket selbst nennt.

    Antworten auf Ticket-Mails und Nachfragen tragen die Nummer im Betreff
    oder im zitierten Text ("[abasoft-Ticket #257317]", "Ticketnummer
    251579"). Gesucht wird jede für sich stehende Ziffernfolge, die genau
    die Nummer eines offenen Tickets desselben Kunden ist - Teile längerer
    Zahlen (Rufnummern, Seriennummern) zählen nicht. Reihenfolge wie in
    der Kandidatenliste.
    """
    text = "%s\n%s" % (ticket.get("title") or "", ticket.get("content") or "")
    mentioned = {int(number) for number in re.findall(
        r"(?<!\d)\d{%d,}(?!\d)" % MIN_TICKET_NUMBER_DIGITS, text)}
    return [candidate for candidate in candidates
            if candidate.get("id") in mentioned]


def is_informative_service(text):
    """Sagt die Leistung etwas über das Anliegen? Leere, automatische
    Mail-Protokolle, Systemeinträge und reine Rückantwort-Vermerke nicht."""
    text = (text or "").strip()
    if not text:
        return False
    if text.startswith(SERVICE_IGNORE_PREFIXES):
        return False
    return " ".join(text.split()).casefold() not in SERVICE_IGNORE_TEXTS


def series_reason(same_title):
    """Begründung für einen Treffer mit gleichem Betreff.

    Gibt es mehrere offene Tickets mit diesem Betreff (Monitoring-Meldungen,
    wiederholte Mails), nennt sie die Größe der Serie und ihren Beginn.
    """
    if len(same_title) < 2:
        return REASON_SAME_TITLE
    oldest = min(same_title, key=_age_key)
    return "%s Es ist das %d. offene Ticket mit diesem Betreff seit %s." % (
        REASON_SAME_TITLE, len(same_title) + 1,
        format_timestamp(oldest.get("created")))


def _age_key(ticket):
    """Sortierschlüssel: ältestes Ticket zuerst."""
    return (ticket.get("created") or 0, ticket.get("id") or 0)


def strip_html(text):
    """Grobes HTML zu Text - nur für Mails ohne Klartextteil."""
    if "<" not in (text or ""):
        return text or ""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return (text.replace("&nbsp;", " ").replace("&amp;", "&")
            .replace("&lt;", "<").replace("&gt;", ">"))


def clean_mail_text(text):
    """Mailtext ohne HTML und ohne zitierten Verlauf darunter.

    Antworten zitieren meist unsere eigene Mail; was darunter steht, sagt
    über das neue Anliegen nichts. Bleibt nach dem Schnitt fast nichts
    übrig, war es eine Weiterleitung - dann zählt der ganze Text.
    """
    lines = strip_html(text).splitlines()
    for index, line in enumerate(lines):
        if QUOTE_START.match(line.strip()):
            reply = "\n".join(lines[:index])
            if len(clip_text(reply, 10 ** 6)) >= MIN_REPLY_CHARS:
                return reply
            break
    return "\n".join(lines)


def clean_transcript_text(text):
    """Transkript ohne unsere Zuordnungs- und Markerzeilen; "" wenn leer."""
    lines = [line for line in (text or "").splitlines()
             if not line.strip().startswith(TRANSCRIPT_SKIP_PREFIXES)]
    text = "\n".join(lines).strip()
    return "" if text == EMPTY_TRANSCRIPT_TEXT else text


def _normalized(text):
    return " ".join(clip_text(text, 10 ** 6).split()).casefold()


def group_messages(rows, tickets_by_id, own_senders=(), starface_senders=()):
    """Ordnet die Nachrichten der Praxis den Tickets zu.

    rows kommt aus db.ticket_messages: eingehende Mails (kind "mail",
    Reihenfolge über die Mail-ID, Datum aus dem Date-Header oder 0) und
    unsere Transkript-Kommentare (kind "voicemail", mit Datum). Liefert je
    Ticket {"mails": [...], "voicemails": [...]}; jede Nachricht hat
    text, date, sender und origin - True, wenn das Ticket aus ihr
    entstand (Mail: steht als Beschreibung da; Sprachnachricht: kam
    binnen Minuten nach der Anlage).

    Weg fallen Mails von eigenen Adressen (Eingangsbestätigungen,
    interne Weiterleitungen) und von Starface - deren Inhalt steckt im
    Transkript -, leere Mails und leere Aufnahmen.
    """
    own = [pattern.lower() for pattern in own_senders or () if pattern]
    starface = [pattern.lower() for pattern in starface_senders or ()
                if pattern]
    grouped = {}
    # Mails nach ID (Eingang beim Server; der Date-Header ist die Uhr des
    # Absenders), Transkripte nach Datum.
    for row in sorted(rows or [], key=lambda row: (
            row.get("kind") != "mail",
            row.get("id") or 0 if row.get("kind") == "mail"
            else row.get("date") or 0)):
        ticket_id = row.get("ticket_id")
        ticket = tickets_by_id.get(ticket_id)
        if ticket is None:
            continue
        entry = grouped.setdefault(ticket_id, {"mails": [], "voicemails": []})
        if row.get("kind") == "mail":
            sender = (row.get("sender") or "").lower()
            if any(pattern in sender for pattern in own + starface):
                continue
            text = clean_mail_text(row.get("text"))
            normalized = _normalized(text)
            if not normalized:
                continue
            origin = normalized[:ORIGIN_MATCH_CHARS] in _normalized(
                ticket.get("content"))
            entry["mails"].append({"text": text,
                                   "date": row.get("date") or 0,
                                   "sender": row.get("sender") or "",
                                   "origin": origin})
        else:
            text = clean_transcript_text(row.get("text"))
            if not text:
                continue
            date = row.get("date") or 0
            created = ticket.get("created") or 0
            origin = bool(created and 0 <= date - created
                          < VOICEMAIL_ORIGIN_SECONDS)
            entry["voicemails"].append({"text": text, "date": date,
                                        "sender": "", "origin": origin})
    return grouped


def _further(messages, kind):
    """Die Nachrichten einer Art, aus denen das Ticket nicht entstand."""
    return [message for message in (messages or {}).get(kind) or []
            if not message.get("origin")]


def has_voicemail_content(messages):
    """Trägt das Ticket mindestens ein Transkript mit Inhalt?"""
    return bool((messages or {}).get("voicemails"))


def compact_form(text):
    """Kürzt den Kopf von Kontaktformular-Mails für die Anzeige.

    Die Formularmails beginnen mit "Praxis: Kundenkürzel ...", "Kubez aus
    System", "EIO aus System", "Datum" und "Nachricht:" - im 250-Zeichen-
    Anriss blieb vom Anliegen sonst kaum etwas. Name, Telefon und E-Mail
    bleiben: sie zeigen, wer schreibt. Nur für die Anzeige - die Erkennung
    der Ursprungsmail vergleicht den ungekürzten Text.
    """
    lines = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(FORM_HEADER_PREFIXES) or stripped == "Nachricht:":
            continue
        lines.append(line)
    return "\n".join(lines)


def _one_line(text, limit):
    return " ".join(clip_text(compact_form(text), limit).split())


def clip_text(text, limit):
    """Kürzt einen Text fürs Prompt.

    Leerzeilen, Einrückungen und der Warnhinweis des Mailservers fallen
    weg - der steht bei jedem zweiten Ticket vorn und würde sonst den
    halben Beschreibungsanriss belegen.
    """
    lines = [" ".join(line.split()) for line in (text or "").splitlines()]
    text = "\n".join(line for line in lines
                     if line and not line.startswith(BOILERPLATE_PREFIXES))
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " …[gekürzt]"


def format_timestamp(timestamp):
    """Unix-Zeitstempel als Datum mit Uhrzeit; leer bei 0."""
    if not timestamp:
        return ""
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(int(timestamp)))


def select_services(services, head, tail):
    """Wählt die Leistungen fürs Prompt: (erste, Anzahl ausgelassen, letzte).

    Bis head + tail Stück kommen alle mit; darüber nur die ersten head und
    die letzten tail - der Anfang zeigt das gemeldete Problem, das Ende den
    aktuellen Stand.
    """
    services = list(services or [])
    head = max(0, int(head))
    tail = max(0, int(tail))
    if len(services) <= head + tail:
        return services, 0, []
    return (services[:head], len(services) - head - tail,
            services[len(services) - tail:])


def _when(date):
    """" am <Datum>" für eine Nachricht - leer, wenn sie keins hat."""
    return " am %s" % format_timestamp(date) if date else ""


def _further_message_lines(messages, indent):
    """Stufe 1: die jüngste weitere Mail und Sprachnachricht, je eine Zeile."""
    lines = []
    mails = _further(messages, "mails")
    if mails:
        lines.append("%s+ %d weitere Mail%s der Praxis, zuletzt%s: %s" % (
            indent, len(mails), "" if len(mails) == 1 else "s",
            _when(mails[-1]["date"]),
            _one_line(mails[-1]["text"], MESSAGE_EXCERPT_LIMIT)))
    voicemails = _further(messages, "voicemails")
    if voicemails:
        lines.append("%s+ %d weitere Sprachnachricht%s, zuletzt am %s: %s" % (
            indent, len(voicemails), "" if len(voicemails) == 1 else "en",
            format_timestamp(voicemails[-1]["date"]),
            _one_line(voicemails[-1]["text"], MESSAGE_EXCERPT_LIMIT)))
    return lines


def build_shortlist_prompt(ticket, candidates, messages_by_ticket=None):
    """Nutzer-Nachricht der Stufe 1: neues Ticket plus Kandidatenliste."""
    messages_by_ticket = messages_by_ticket or {}
    lines = ["Neues Ticket #%d (angelegt %s)"
             % (ticket.get("id"), format_timestamp(ticket.get("created"))),
             "Betreff: %s" % (ticket.get("title") or "").strip(),
             "Beschreibung:",
             clip_text(compact_form(ticket.get("content")), CONTENT_LIMIT)
             or "(leer)"]
    lines.extend(_further_message_lines(
        messages_by_ticket.get(ticket.get("id")), ""))
    lines.extend(["", "Offene Tickets desselben Kunden:"])
    for candidate in candidates:
        lines.append("#%d | %s | %s | %s" % (
            candidate.get("id"), format_timestamp(candidate.get("created")),
            candidate.get("status") or "?",
            (candidate.get("title") or "").strip()))
        excerpt = _one_line(candidate.get("content"), CANDIDATE_EXCERPT_LIMIT)
        if excerpt:
            lines.append("    %s" % excerpt)
        lines.extend(_further_message_lines(
            messages_by_ticket.get(candidate.get("id")), "    "))
    return mask_secrets("\n".join(lines))


def split_services(services, now):
    """Teilt Leistungen in vergangene und geplante (Datum nach now)."""
    past, planned = [], []
    for row in services or []:
        (planned if now and (row.get("date") or 0) > now
         else past).append(row)
    planned.sort(key=lambda row: row.get("date") or 0)
    return past, planned[:MAX_PLANNED_SERVICES]


def _entry_text(text, limit):
    return " | ".join(clip_text(text, limit).splitlines()) or "(ohne Text)"


def _add_selection(lines, rows, head, tail, describe, what):
    """Hängt die ersten und letzten Einträge an, mit Auslassungszeile."""
    first, skipped, last = select_services(rows, head, tail)
    lines.extend(describe(row) for row in first)
    if skipped:
        lines.append("- … %d weitere %s ausgelassen …" % (skipped, what))
    lines.extend(describe(row) for row in last)


def _detail_block(heading, ticket, services, head, tail, messages=None,
                  now=None):
    """Ein Ticket mit Beschreibung, Nachrichten und Leistungen (Stufe 2).

    Die Mail, aus der das Ticket entstand, fehlt bei den Nachrichten - sie
    steht als Beschreibung da. Die erste Sprachnachricht bleibt drin: die
    Beschreibung ist nur ihre Zusammenfassung.
    """
    lines = [heading,
             "Betreff: %s" % (ticket.get("title") or "").strip(),
             "Beschreibung:",
             clip_text(compact_form(ticket.get("content")),
                       DETAIL_CONTENT_LIMIT)
             or "(leer)"]
    mails = _further(messages, "mails")
    if mails:
        lines.append("Weitere Mails der Praxis:")
        _add_selection(lines, mails, head, tail,
                       lambda mail: "- %s%s: %s" % (
                           "%s " % format_timestamp(mail["date"])
                           if mail["date"] else "",
                           "von %s" % (mail["sender"] or "?"),
                           _entry_text(compact_form(mail["text"]),
                                       MESSAGE_TEXT_LIMIT)),
                       "Mails")
    voicemails = (messages or {}).get("voicemails") or []
    if voicemails:
        lines.append("Sprachnachrichten (Transkripte):")
        _add_selection(lines, voicemails, head, tail,
                       lambda voicemail: "- %s: %s" % (
                           format_timestamp(voicemail["date"]),
                           _entry_text(voicemail["text"],
                                       MESSAGE_TEXT_LIMIT)),
                       "Sprachnachrichten")

    def describe_service(row):
        return "- %s: %s" % (format_timestamp(row.get("date")),
                             _entry_text(row.get("text"), SERVICE_TEXT_LIMIT))

    past, planned = split_services(services, now)
    if past:
        lines.append("Leistungen:")
        _add_selection(lines, past, head, tail, describe_service,
                       "Leistungen")
    else:
        lines.append("Leistungen: keine")
    if planned:
        lines.append("Geplante Termine:")
        lines.extend("- geplant: %s" % describe_service(row)[2:]
                     for row in planned)
    return lines


def build_detail_prompt(ticket, candidates, services_by_ticket, head, tail,
                        messages_by_ticket=None, now=None):
    """Nutzer-Nachricht der Stufe 2: Beschreibungen, Nachrichten, Leistungen."""
    messages_by_ticket = messages_by_ticket or {}
    lines = _detail_block(
        "Neues Ticket #%d (angelegt %s)"
        % (ticket.get("id"), format_timestamp(ticket.get("created"))),
        ticket, services_by_ticket.get(ticket.get("id")), head, tail,
        messages_by_ticket.get(ticket.get("id")), now)
    for candidate in candidates:
        lines.append("")
        lines.extend(_detail_block(
            "Offenes Ticket #%d (angelegt %s, Status %s)"
            % (candidate.get("id"),
               format_timestamp(candidate.get("created")),
               candidate.get("status") or "?"),
            candidate, services_by_ticket.get(candidate.get("id")),
            head, tail, messages_by_ticket.get(candidate.get("id")), now))
    return mask_secrets("\n".join(lines))


def _reason(entry):
    """Die Begründung eines Antworteintrags, bereinigt; leer, wenn keine."""
    return " ".join(str(entry.get("begruendung") or "").split())


def parse_shortlist(text, known_ids):
    """Liest die Antwort der Stufe 1.

    Liefert (sichere, unklare) - beides Listen von (Ticket-ID,
    Begründung). Nur IDs aus der vorgelegten Liste zählen, jede einmal.
    None, wenn die Antwort nicht dem Format folgt.
    """
    data = extract_json_object(text)
    if data is None or not isinstance(data.get("kandidaten"), list):
        return None
    sure, unclear, seen = [], [], set()
    for entry in data["kandidaten"]:
        if not isinstance(entry, dict):
            continue
        ticket_id = entry.get("ticket_id")
        if isinstance(ticket_id, bool) or not isinstance(ticket_id, int):
            continue
        if ticket_id not in known_ids:
            LOG.info("LLM nannte Ticket %s, das nicht in der Kandidatenliste "
                     "steht - verworfen.", ticket_id)
            continue
        if ticket_id in seen:
            continue
        if entry.get("einschaetzung") == "duplikat":
            sure.append((ticket_id, _reason(entry)))
        elif entry.get("einschaetzung") == "unklar":
            unclear.append((ticket_id, _reason(entry)))
        else:
            continue
        seen.add(ticket_id)
    return sure, unclear


def parse_verdict(text, known_ids):
    """Liest die Antwort der Stufe 2: Liste (Ticket-ID, Begründung).

    None, wenn die Antwort nicht dem Format folgt.
    """
    data = extract_json_object(text)
    if data is None or not isinstance(data.get("duplikate"), list):
        return None
    confirmed = []
    for entry in data["duplikate"]:
        if not isinstance(entry, dict):
            continue
        ticket_id = entry.get("ticket_id")
        if isinstance(ticket_id, bool) or not isinstance(ticket_id, int):
            continue
        if ticket_id not in known_ids:
            LOG.info("LLM nannte Ticket %s, das nicht vorgelegt wurde - "
                     "verworfen.", ticket_id)
            continue
        if any(ticket_id == known for known, _ in confirmed):
            continue
        confirmed.append((ticket_id, _reason(entry)))
    return confirmed


def build_comment(matches):
    """Titel und Text des Hinweis-Kommentars.

    matches ist eine Liste von (Kandidat, Begründung). Der Kommentartitel
    nennt das erste Duplikat, der Text beginnt mit dessen Betreff (die
    Nummer steht schon in der Überschrift), nach einer Leerzeile folgt die
    Begründung. Jedes weitere Duplikat folgt nach einer Leerzeile und
    einer Trennlinie, mit der Überschrift als Textzeile (Vorlage: Ticket
    260976):

        Titel: Potenzielles Duplikat von Ticket #1234
        "Betreff des anderen Tickets"

        Begründung ...

        ------------------------------------------------------------ ...
        Potenzielles Duplikat von Ticket #2345
        "Betreff des zweiten Tickets"

        Begründung ...
    """
    blocks = []
    for index, (match, reason) in enumerate(matches):
        lines = []
        if index:
            lines.append(COMMENT_HEADING % match.get("id"))
        lines.append("\"%s\"" % " ".join((match.get("title") or "").split()))
        if reason:
            lines.extend(["", reason])
        blocks.append("\n".join(lines))
    title = COMMENT_HEADING % matches[0][0].get("id")
    return title, ("\n\n%s\n" % COMMENT_SEPARATOR).join(blocks)


class DuplicateChecker:
    """Prüft je ein Ticket gegen die offenen Tickets seines Kunden."""

    def __init__(self, cfg, client, llm, state, ticket_lookup,
                 candidates_lookup, services_lookup, messages_lookup=None,
                 clock=time.time):
        self.cfg = cfg
        self.client = client
        self.llm = llm
        self.state = state
        # ticket_id -> Dict aus db.fetch_ticket ({} = gibt es nicht,
        # None = DB nicht da)
        self.ticket_lookup = ticket_lookup
        # (company_id, exclude_id) -> Liste aus db.open_company_tickets
        self.candidates_lookup = candidates_lookup
        # [ticket_ids] -> Zeilen aus db.ticket_services
        self.services_lookup = services_lookup
        # [ticket_ids] -> Zeilen aus db.ticket_messages (Mails, Transkripte)
        self.messages_lookup = messages_lookup or (lambda ticket_ids: [])
        # Uhr für "geplant" (Leistungen in der Zukunft); im Test austauschbar
        self.clock = clock

    @property
    def active(self):
        """Eingeschaltet und ein LLM da - sonst ruht die Erkennung."""
        return bool(self.cfg.duplicates.enabled and self.llm is not None)

    def check_ticket(self, ticket_id, force=False):
        """Prüft ein Ticket, höchstens einmal; wirft nie.

        force übergeht den State (Einzellauf zur Diagnose). Ein Fehler
        (LLM, Datenbank, API) wird gezählt - der Poller reiht das Ticket
        dann später erneut ein.
        """
        if not self.active:
            return
        key = item_key(ticket_id)
        if not force and self.state.is_done(key):
            return
        try:
            detail = self._check(ticket_id)
        except Exception as error:
            if self.cfg.dry_run:
                LOG.exception("[dry-run] Duplikatprüfung von Ticket %d "
                              "fehlgeschlagen.", ticket_id)
                return
            retry = self.state.mark_failed(key, ticket_id, str(error))
            LOG.warning("Duplikatprüfung von Ticket %d fehlgeschlagen%s: %s",
                        ticket_id,
                        "" if retry else " (endgültig aufgegeben)", error)
            return
        if self.cfg.dry_run:
            return
        if detail is not None:
            self.state.mark_done(key, ticket_id, detail)
        elif self.state.attempts(key):
            # Zurückgestellt nach einem früheren Fehlversuch: der Eintrag
            # muss weg, sonst reiht der Poller das Ticket endlos neu ein.
            self.state.forget(key)

    # -- Ablauf

    def _check(self, ticket_id):
        """Die eigentliche Prüfung.

        Liefert den Vermerk für den State - oder None, wenn die Prüfung
        offen bleibt (noch keine prüfbare Firma, Transkription steht aus)
        und ein späterer Anstoß sie nachholen soll.
        """
        settings = self.cfg.duplicates
        ticket = self.ticket_lookup(ticket_id)
        if ticket is None:
            raise RuntimeError("TANSS-Datenbank nicht verfügbar.")
        if not ticket or ticket.get("deleted"):
            return "Ticket gelöscht"

        company_id = ticket.get("company_id") or 0
        if not company_id:
            LOG.debug("Ticket %d liegt noch bei keiner Firma - "
                      "Duplikatprüfung wartet.", ticket_id)
            return None
        if company_id in (settings.exclude_company_ids or []):
            LOG.debug("Ticket %d liegt bei der ausgenommenen Firma %d - "
                      "keine Duplikatprüfung.", ticket_id, company_id)
            return None

        title = ticket.get("title") or ""
        empty_subject = normalize_title(EMPTY_TRANSCRIPT_SUBJECT)
        if normalize_title(title) == empty_subject:
            return "nicht prüfbar (%s)" % EMPTY_TRANSCRIPT_SUBJECT
        generic = self.cfg.starface.generic_title_pattern
        if generic and re.search(generic, title):
            LOG.info("Ticket %d trägt noch den generischen Starface-Betreff "
                     "- Duplikatprüfung wartet auf die Transkription.",
                     ticket_id)
            return None

        candidates = self.candidates_lookup(company_id, ticket_id)
        if candidates is None:
            raise RuntimeError("TANSS-Datenbank nicht verfügbar.")
        # Noch nicht transkribierte Voicemails taugen nicht als Vergleich.
        candidates = [candidate for candidate in candidates
                      if not (generic and re.search(
                          generic, candidate.get("title") or ""))]
        messages = self._messages(ticket, candidates)
        # Leere Sprachnachrichten auch nicht - es sei denn, ein späterer
        # Anruf am selben Ticket hatte Inhalt.
        candidates = [
            candidate for candidate in candidates
            if normalize_title(candidate.get("title")) != empty_subject
            or has_voicemail_content(messages.get(candidate.get("id")))]
        if not candidates:
            LOG.info("Ticket %d: kein Duplikat (keine weiteren offenen "
                     "Tickets beim Kunden).", ticket_id)
            return "kein Duplikat (keine offenen Tickets)"

        # Erst jetzt, wo es etwas zu vergleichen gibt: darf der API-Benutzer
        # das Ticket überhaupt anfassen? Sonst wären die LLM-Aufrufe
        # umsonst, weil der Kommentar am Ende abgelehnt wird.
        if not self._has_access(ticket_id):
            return NO_ACCESS_DETAIL

        limit = max(1, int(settings.max_matches))
        # Stufe 0, ohne LLM: zuerst die ausdrücklich genannten Tickets
        # (das stärkste Signal), dann die mit gleichem Betreff - davon nur
        # das älteste: bei einer Serie (Monitoring, wiederholte Mails) ist
        # das das Ticket, in das zusammengeführt wird.
        mentioned = mentioned_ticket_matches(ticket, candidates)
        same_title = exact_title_matches(title, candidates)
        matches = [(candidate, REASON_MENTIONED % candidate.get("id"))
                   for candidate in mentioned]
        if same_title:
            oldest = min(same_title, key=_age_key)
            if oldest not in mentioned:
                matches.append((oldest, series_reason(same_title)))
        matches = matches[:limit]
        stage = ", ".join(label for label, hits in (
            ("Ticketnummer genannt", mentioned),
            ("gleicher Betreff", same_title)) if hits)
        if not matches:
            matches = self._ask_llm(ticket, candidates, messages)[:limit]
            stage = "LLM"
        if not matches:
            LOG.info("Ticket %d: kein Duplikat unter %d offenen Tickets.",
                     ticket_id, len(candidates))
            return "kein Duplikat"

        try:
            self._report(ticket, matches, stage)
        except TanssApiError as error:
            if error.status != 403:
                raise
            # Lesen ging, Schreiben nicht: auch das ändert kein neuer
            # Versuch, nur ein Recht in TANSS.
            self._warn_no_access(ticket_id, error)
            return NO_ACCESS_DETAIL
        return "Duplikat von %s (%s)" % (
            ", ".join("#%d" % match.get("id") for match, _ in matches), stage)

    def _has_access(self, ticket_id):
        """Prüft über die API, ob der Benutzer das Ticket sehen darf.

        TANSS verweigert Tickets fremder Abteilungen mit 403
        (FORBIDDEN_NO_ENTITY_ACCESS), solange dem API-Benutzer das Recht
        "Tickets anzeigen (alle Abteilungen)" fehlt. Das ist kein
        vorübergehender Fehler - das Ticket gilt dann als erledigt, statt
        dreimal vergeblich wiederholt zu werden. Andere API-Fehler fliegen
        weiter und werden wie üblich gezählt.
        """
        try:
            self.client.get_ticket(ticket_id)
        except TanssApiError as error:
            if error.status != 403:
                raise
            self._warn_no_access(ticket_id, error)
            return False
        return True

    def _warn_no_access(self, ticket_id, error):
        LOG.warning("Ticket %d: keine Duplikatprüfung - der API-Benutzer hat "
                    "keinen Zugriff auf das Ticket (%s). Ihm fehlt vermutlich "
                    "das Recht \"Ticket-System: Tickets anzeigen (alle "
                    "Abteilungen)\".", ticket_id, error)

    def _messages(self, ticket, candidates):
        """Nachrichten der Praxis für das neue Ticket und die Kandidaten."""
        tickets_by_id = {item.get("id"): item for item in [ticket] + candidates}
        rows = self.messages_lookup(list(tickets_by_id))
        if rows is None:
            raise RuntimeError("TANSS-Datenbank nicht verfügbar.")
        return group_messages(rows, tickets_by_id,
                              self.cfg.duplicates.own_senders,
                              self.cfg.starface.sender_patterns)

    def _ask_llm(self, ticket, candidates, messages=None):
        """Stufe 1 und - nur für unklare Kandidaten - Stufe 2.

        Liefert eine Liste von (Kandidat, Begründung): erst die sicheren
        Treffer der Sichtung, dann die in der Nachprüfung bestätigten.
        """
        settings = self.cfg.duplicates
        by_id = {candidate.get("id"): candidate for candidate in candidates}

        answer = self.llm.complete(
            SHORTLIST_SYSTEM_PROMPT,
            build_shortlist_prompt(ticket, candidates, messages),
            schema=SHORTLIST_SCHEMA)
        shortlist = parse_shortlist(answer, by_id)
        if shortlist is None:
            raise LlmError("Antwort der Duplikat-Sichtung nicht lesbar: "
                           "%.200s" % answer)
        sure, unclear = shortlist
        unclear = unclear[:max(0, int(settings.max_detail_candidates))]
        LOG.debug("Ticket %d: Sichtung von %d Kandidaten - sicher %s, "
                  "unklar %s.", ticket.get("id"), len(candidates), sure,
                  unclear)

        confirmed = []
        if unclear and len(sure) < max(1, int(settings.max_matches)):
            confirmed = self._verify(ticket, [by_id[ticket_id]
                                              for ticket_id, _ in unclear],
                                     messages)
        return [(by_id[ticket_id], reason)
                for ticket_id, reason in sure + confirmed]

    def _verify(self, ticket, candidates, messages=None):
        """Stufe 2: Beschreibung, Nachrichten und Leistungen entscheiden."""
        settings = self.cfg.duplicates
        ticket_ids = [ticket.get("id")] + [candidate.get("id")
                                           for candidate in candidates]
        rows = self.services_lookup(ticket_ids)
        if rows is None:
            raise RuntimeError("TANSS-Datenbank nicht verfügbar.")
        # Aussortiert wird vor der Auswahl der ersten und letzten paar,
        # damit kein Mail-Protokoll einer Technikernotiz den Platz nimmt.
        services_by_ticket = {}
        for row in rows:
            if is_informative_service(row.get("text")):
                services_by_ticket.setdefault(row.get("ticket_id"),
                                              []).append(row)

        answer = self.llm.complete(
            VERDICT_SYSTEM_PROMPT,
            build_detail_prompt(ticket, candidates, services_by_ticket,
                                settings.services_head,
                                settings.services_tail, messages,
                                now=self.clock()),
            schema=VERDICT_SCHEMA)
        verdict = parse_verdict(
            answer, {candidate.get("id") for candidate in candidates})
        if verdict is None:
            raise LlmError("Antwort der Duplikat-Nachprüfung nicht lesbar: "
                           "%.200s" % answer)
        return verdict

    def _report(self, ticket, matches, stage):
        """Schreibt Kommentar und Tag ans neue Ticket.

        matches ist eine Liste von (Kandidat, Begründung); jede Begründung
        steht im Kommentar und - je Treffer eine Zeile - im Log.
        """
        ticket_id = ticket.get("id")
        settings = self.cfg.duplicates
        title, body = build_comment(matches)
        if self.cfg.dry_run:
            LOG.info("[dry-run] Ticket %d: potenzielles Duplikat (%s). "
                     "Kommentar:\n%s\n%s", ticket_id, stage, title, body)
            if settings.tag_id:
                LOG.info("[dry-run] Ticket %d: Tag %s würde gesetzt.",
                         ticket_id,
                         settings.tag_name or "#%d" % settings.tag_id)
            return
        self.client.post_comment(ticket_id, title, body,
                                 internal=self.cfg.comment.internal)
        if settings.tag_id:
            self._tag(ticket_id, settings.tag_id)
        for match, reason in matches:
            LOG.info("Ticket %d: Duplikat von #%d - %s", ticket_id,
                     match.get("id"), reason or "ohne Begründung")
        LOG.info("Ticket %d: potenzielles Duplikat von %s (%s) - %s",
                 ticket_id,
                 ", ".join("#%d" % match.get("id") for match, _ in matches),
                 stage,
                 ticket_url(self.cfg.tanss.base_url, ticket_id,
                            ticket.get("company_id")))

    def _tag(self, ticket_id, tag_id):
        """Hängt den Duplikat-Tag an; vorhandene Tags bleiben stehen.

        Ein Fehler hier bricht nichts ab - der Kommentar steht dann schon
        im Ticket und trägt den Hinweis allein.
        """
        try:
            present = {tag.get("id") for tag in
                       self.client.get_ticket_tags(ticket_id) or []}
            if tag_id in present:
                return
            self.client.add_ticket_tag(ticket_id, tag_id)
        except Exception as error:
            LOG.warning("Ticket %d: Tag %d ließ sich nicht setzen: %s",
                        ticket_id, tag_id, error)

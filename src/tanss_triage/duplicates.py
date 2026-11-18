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
              Beschreibung und Leistungen (bei vielen die ersten und
              letzten paar) und entscheidet. Bei Verdacht bekommt das neue
              Ticket den Tag "Prüfen! pot. Duplikat" und einen Kommentar
              mit Verweis auf das andere Ticket. Der State-Schlüssel
              "dup:<Ticket>" sorgt dafür, dass jedes Ticket genau einmal
              geprüft wird; solange es keine prüfbare Firma oder noch den
              generischen Starface-Betreff hat, bleibt die Prüfung offen.
Letzte Änderung: 2026-11-16
"""

import logging
import re
import time

from .llm import LlmError
from .processor import ticket_url
from .tanss_client import TanssApiError
from .triage import EMPTY_TRANSCRIPT_SUBJECT, extract_json_object

LOG = logging.getLogger("tanss_triage.duplicates")

KEY_PREFIX = "dup:"

# Kommentartitel (für das erste Duplikat) und Überschrift jedes weiteren.
COMMENT_HEADING = "Potenzielles Duplikat von Ticket #%d"

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

# Zeilen, die der Mailserver jeder eingehenden Mail voranstellt und die
# nichts über das Anliegen sagen.
BOILERPLATE_PREFIXES = ("WARNUNG: EXTERNE NACHRICHT",)

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

Betreffs stehen oft im Telegrammstil mit Hausabkürzungen (KIM, ePA, eRP,
eAU, KT = Kartenterminal, AP = Arbeitsplatz, DaSi = Datensicherung,
RR = Rückruf). Beschreibungen aus E-Mails enthalten Formularköpfe (Praxis,
Name, Telefon, Datum), Grußformeln und Signaturen - die sagen nichts über
das Anliegen."""

SHORTLIST_SYSTEM_PROMPT = DEFINITION + """

Du bekommst das neue Ticket (Betreff und Beschreibung) und die Liste der
offenen Tickets des Kunden: Nummer, Anlagedatum, Status, Betreff und der
Anfang der Beschreibung.

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt, ohne Erklärtext davor oder
danach:

{"kandidaten": [{"ticket_id": 1234, "einschaetzung": "duplikat"}]}

- "duplikat": Betreff und Beschreibungsanfang genügen, um zu sagen, dass es
  sehr wahrscheinlich dasselbe Anliegen ist.
- "unklar": Es könnte dasselbe Anliegen sein, lässt sich so aber nicht
  entscheiden. Für diese Tickets bekommst du anschließend die vollständige
  Beschreibung und die Leistungen.
- Tickets, die erkennbar etwas anderes betreffen, lässt du weg. Die meisten
  neuen Tickets sind keine Duplikate - eine leere Liste ist die häufigste
  richtige Antwort.
- Nenne nur Ticketnummern aus der Liste, die wahrscheinlichsten zuerst."""

VERDICT_SYSTEM_PROMPT = DEFINITION + """

Du bekommst das neue Ticket und einige offene Tickets desselben Kunden, bei
denen der Betreff allein nicht reichte - jeweils mit Beschreibung und den
Leistungen (den Arbeitsnotizen der Techniker; bei vielen nur die ersten und
die letzten). Entscheide für jedes, ob das neue Ticket dasselbe Anliegen
meldet.

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt, ohne Erklärtext davor oder
danach:

{"duplikate": [{"ticket_id": 1234, "begruendung": "ein kurzer Satz"}]}

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
                },
                "required": ["ticket_id", "einschaetzung"],
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


def build_shortlist_prompt(ticket, candidates):
    """Nutzer-Nachricht der Stufe 1: neues Ticket plus Kandidatenliste."""
    lines = ["Neues Ticket #%d (angelegt %s)"
             % (ticket.get("id"), format_timestamp(ticket.get("created"))),
             "Betreff: %s" % (ticket.get("title") or "").strip(),
             "Beschreibung:",
             clip_text(ticket.get("content"), CONTENT_LIMIT) or "(leer)",
             "",
             "Offene Tickets desselben Kunden:"]
    for candidate in candidates:
        lines.append("#%d | %s | %s | %s" % (
            candidate.get("id"), format_timestamp(candidate.get("created")),
            candidate.get("status") or "?",
            (candidate.get("title") or "").strip()))
        excerpt = " ".join(clip_text(candidate.get("content"),
                                     CANDIDATE_EXCERPT_LIMIT).split())
        if excerpt:
            lines.append("    %s" % excerpt)
    return "\n".join(lines)


def _detail_block(heading, ticket, services, head, tail):
    """Ein Ticket mit Beschreibung und Leistungen für Stufe 2."""
    lines = [heading,
             "Betreff: %s" % (ticket.get("title") or "").strip(),
             "Beschreibung:",
             clip_text(ticket.get("content"), DETAIL_CONTENT_LIMIT)
             or "(leer)"]
    first, skipped, last = select_services(services, head, tail)
    if not first and not last:
        lines.append("Leistungen: keine")
        return lines
    lines.append("Leistungen:")

    def add(rows):
        for row in rows:
            lines.append("- %s: %s" % (
                format_timestamp(row.get("date")),
                " | ".join(clip_text(row.get("text"),
                                     SERVICE_TEXT_LIMIT).splitlines())
                or "(ohne Text)"))

    add(first)
    if skipped:
        lines.append("- … %d weitere Leistungen ausgelassen …" % skipped)
    add(last)
    return lines


def build_detail_prompt(ticket, candidates, services_by_ticket, head, tail):
    """Nutzer-Nachricht der Stufe 2: Beschreibungen und Leistungen."""
    lines = _detail_block(
        "Neues Ticket #%d (angelegt %s)"
        % (ticket.get("id"), format_timestamp(ticket.get("created"))),
        ticket, services_by_ticket.get(ticket.get("id")), head, tail)
    for candidate in candidates:
        lines.append("")
        lines.extend(_detail_block(
            "Offenes Ticket #%d (angelegt %s, Status %s)"
            % (candidate.get("id"),
               format_timestamp(candidate.get("created")),
               candidate.get("status") or "?"),
            candidate, services_by_ticket.get(candidate.get("id")),
            head, tail))
    return "\n".join(lines)


def parse_shortlist(text, known_ids):
    """Liest die Antwort der Stufe 1: (sichere IDs, unklare IDs).

    Nur IDs aus der vorgelegten Liste zählen, jede einmal. None, wenn die
    Antwort nicht dem Format folgt.
    """
    data = extract_json_object(text)
    if data is None or not isinstance(data.get("kandidaten"), list):
        return None
    sure, unclear = [], []
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
        if ticket_id in sure or ticket_id in unclear:
            continue
        if entry.get("einschaetzung") == "duplikat":
            sure.append(ticket_id)
        elif entry.get("einschaetzung") == "unklar":
            unclear.append(ticket_id)
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
        confirmed.append((ticket_id,
                          str(entry.get("begruendung") or "").strip()))
    return confirmed


def build_comment(matches):
    """Titel und Text des Hinweis-Kommentars.

    Der Kommentartitel nennt das erste Duplikat, der Text beginnt mit
    dessen Betreff und Nummer. Jedes weitere Duplikat folgt nach einer
    Leerzeile mit derselben Überschrift als Textzeile:

        Titel: Potenzielles Duplikat von Ticket #1234
        "Betreff des anderen Tickets" #1234

        Potenzielles Duplikat von Ticket #2345
        "Betreff des zweiten Tickets" #2345
    """
    headings = [COMMENT_HEADING % match.get("id") for match in matches]
    blocks = ["\"%s\" #%d" % (" ".join((match.get("title") or "").split()),
                              match.get("id")) for match in matches]
    for index in range(1, len(blocks)):
        blocks[index] = "%s\n%s" % (headings[index], blocks[index])
    return headings[0], "\n\n".join(blocks)


class DuplicateChecker:
    """Prüft je ein Ticket gegen die offenen Tickets seines Kunden."""

    def __init__(self, cfg, client, llm, state, ticket_lookup,
                 candidates_lookup, services_lookup):
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
        # Tickets ohne Aussage taugen nicht als Vergleich: leere
        # Sprachnachrichten und noch nicht transkribierte Voicemails.
        candidates = [
            candidate for candidate in candidates
            if normalize_title(candidate.get("title")) != empty_subject
            and not (generic and re.search(generic,
                                           candidate.get("title") or ""))]
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
        # (das stärkste Signal), dann die mit gleichem Betreff.
        mentioned = mentioned_ticket_matches(ticket, candidates)
        same_title = [candidate for candidate
                      in exact_title_matches(title, candidates)
                      if candidate not in mentioned]
        matches = (mentioned + same_title)[:limit]
        stage = ", ".join(label for label, hits in (
            ("Ticketnummer genannt", mentioned),
            ("gleicher Betreff", same_title)) if hits)
        if not matches:
            matches = self._ask_llm(ticket, candidates)[:limit]
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
            ", ".join("#%d" % match.get("id") for match in matches), stage)

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

    def _ask_llm(self, ticket, candidates):
        """Stufe 1 und - nur für unklare Kandidaten - Stufe 2."""
        settings = self.cfg.duplicates
        by_id = {candidate.get("id"): candidate for candidate in candidates}

        answer = self.llm.complete(
            SHORTLIST_SYSTEM_PROMPT,
            build_shortlist_prompt(ticket, candidates),
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
                                              for ticket_id in unclear])
        return [by_id[ticket_id] for ticket_id in sure + confirmed]

    def _verify(self, ticket, candidates):
        """Stufe 2: Beschreibung und Leistungen entscheiden lassen."""
        settings = self.cfg.duplicates
        ticket_ids = [ticket.get("id")] + [candidate.get("id")
                                           for candidate in candidates]
        rows = self.services_lookup(ticket_ids)
        if rows is None:
            raise RuntimeError("TANSS-Datenbank nicht verfügbar.")
        services_by_ticket = {}
        for row in rows:
            if (row.get("text") or "").strip():
                services_by_ticket.setdefault(row.get("ticket_id"),
                                              []).append(row)

        answer = self.llm.complete(
            VERDICT_SYSTEM_PROMPT,
            build_detail_prompt(ticket, candidates, services_by_ticket,
                                settings.services_head,
                                settings.services_tail),
            schema=VERDICT_SCHEMA)
        verdict = parse_verdict(
            answer, {candidate.get("id") for candidate in candidates})
        if verdict is None:
            raise LlmError("Antwort der Duplikat-Nachprüfung nicht lesbar: "
                           "%.200s" % answer)
        for candidate_id, reason in verdict:
            LOG.info("Ticket %d: Duplikat von #%d laut Nachprüfung - %s",
                     ticket.get("id"), candidate_id, reason or "ohne "
                     "Begründung")
        return [candidate_id for candidate_id, _ in verdict]

    def _report(self, ticket, matches, stage):
        """Schreibt Kommentar und Tag ans neue Ticket."""
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
        LOG.info("Ticket %d: potenzielles Duplikat von %s (%s) - %s",
                 ticket_id,
                 ", ".join("#%d" % match.get("id") for match in matches),
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

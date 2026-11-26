"""
Usage: pytest tests/test_duplicates.py

Autor: SO, (c) abasoft GmbH 2026-11-16
Datei: test_duplicates.py
Beschreibung: Prüft die Duplikat-Erkennung: Betreffvergleich (Stufe 0),
              Prompt-Aufbau, Parser der LLM-Antworten, Auswahl der
              Leistungen und den Gesamtablauf mit gemocktem TANSS-Client,
              LLM und Datenbank-Lookups - samt der Fälle, in denen die
              Prüfung offen bleibt (keine Firma, ausgenommene Firma,
              generischer Starface-Betreff).
Letzte Änderung: 2026-11-26
"""

import json
import time

from tanss_triage.config import Config
from tanss_triage.duplicates import (
    SHORTLIST_SCHEMA, SHORTLIST_SYSTEM_PROMPT, VERDICT_SCHEMA,
    VERDICT_SYSTEM_PROMPT, DuplicateChecker, build_comment,
    build_detail_prompt, build_shortlist_prompt, clean_mail_text,
    clean_transcript_text, clip_text, exact_title_matches, group_messages,
    is_informative_service, item_key, mentioned_ticket_matches,
    normalize_title, parse_shortlist, parse_verdict, select_services,
    series_reason, split_services)
from tanss_triage.llm import LlmError
from tanss_triage.state import State
from tanss_triage.tanss_client import TanssApiError

STARFACE_SUBJECT = ("Sie haben eine Sprachnachricht von  00497432994360 "
                    "in Zentrale Überlauf erhalten")

# Fester "Jetzt"-Zeitpunkt: 26.11.2026 12:00 Ortszeit - über mktime, damit
# die erwarteten Datumsangaben in jeder Zeitzone stimmen.
NOW = int(time.mktime((2026, 11, 26, 12, 0, 0, 0, 0, -1)))
HOUR = 3600

NEW_TICKET = {"id": 260148, "company_id": 554, "deleted": 0,
              "title": "KIM und ePA gehen nicht -> RR 07141 121210",
              "content": "Seit heute früh gehen KIM und ePA nicht.",
              "created": 0}

CANDIDATES = [
    {"id": 260140, "company_id": 554, "title": "ePA gestört",
     "content": "ePA lädt keine Dokumente.", "created": 0,
     "status": "gesichtet"},
    {"id": 260120, "company_id": 554, "title": "Nadeldrucker druckt nicht",
     "content": "Drucker am Empfang.", "created": 0, "status": "neu"},
    {"id": 260101, "company_id": 554, "title": "Konnektor-Zertifikate",
     "content": "Zertifikat läuft ab.", "created": 0,
     "status": "in Bearbeitung"},
]


# -- Hilfsfunktionen ---------------------------------------------------------

def test_title_normalization_ignores_case_and_whitespace():
    assert normalize_title("  KIM  geht\tnicht ") == normalize_title(
        "kim geht nicht")
    assert normalize_title(None) == ""


def test_exact_title_matches_keep_candidate_order():
    candidates = [{"id": 3, "title": "ePA  gestört"},
                  {"id": 2, "title": "Drucker"},
                  {"id": 1, "title": "EPA gestört"}]
    hits = exact_title_matches("ePA gestört", candidates)
    assert [hit["id"] for hit in hits] == [3, 1]
    assert exact_title_matches("", candidates) == []


def test_mentioned_ticket_numbers_match_candidates():
    candidates = [{"id": 257317, "title": "ePA für Hinrichs Kunden"},
                  {"id": 251579, "title": "Neukunde"},
                  {"id": 121210, "title": "Altticket"},
                  {"id": 99614, "title": "Umstellung Zweigpraxis"}]

    def hits(title, content=""):
        return [hit["id"] for hit in mentioned_ticket_matches(
            {"title": title, "content": content}, candidates)]

    assert hits("[abasoft-Ticket #257317] epa in KRB Nachfrage") == [257317]
    assert hits("Kundennummer 10696, Ticketnummer 251579") == [251579]
    # auch im (zitierten) Text, mehrere Nennungen in Kandidaten-Reihenfolge
    assert hits("Re: Nachfrage",
                "wie in Ticket 99614 und\n> Ticket: 257317 besprochen"
                ) == [257317, 99614]
    # Teile längerer Zahlen zählen nicht: Rufnummer, Seriennummer
    assert hits("RR unter 07141121210", "Serial No: 4612573170") == []
    # eine für sich stehende Zahl aber schon - auch als Rufnummernteil
    assert hits("RR 07141 121210") == [121210]
    assert hits("Ziffer 86901 freischalten") == []
    assert hits("", "") == []


def test_clip_text_drops_blank_lines_and_marks_cut():
    assert clip_text("  a   b \n\n\n c ", 100) == "a b\nc"
    clipped = clip_text("x" * 50, 10)
    assert clipped.startswith("x" * 10) and clipped.endswith("[gekürzt]")
    assert clip_text(None, 10) == ""
    # der Warnhinweis des Mailservers sagt nichts über das Anliegen
    assert clip_text("WARNUNG: EXTERNE NACHRICHT - Bitte seien Sie "
                     "vorsichtig!\nHallo,\nder Drucker druckt nicht.",
                     100) == "Hallo,\nder Drucker druckt nicht."


def test_uninformative_services_are_recognized():
    assert not is_informative_service(
        "[Automatische Protokollierung des Ticket-E-Mail-Versandes]\n"
        "Empfänger: praxis@example.de\n---\n[abasoft-Ticket #260442] Re: "
        "Ihr Ticket wurde automatisch erfasst")
    assert not is_informative_service(
        "[System] Status der Firma geändert - Aktiv > Inaktiv")
    assert not is_informative_service("Automatische Rückantwort\r\n")
    assert not is_informative_service("   ")
    assert not is_informative_service(None)
    # normale Notizen bleiben, auch wenn das Wort vorkommt
    assert is_informative_service("Automatische Rückantwort, Praxis ist "
                                  "bis 1.12. im Urlaub")
    assert is_informative_service("Anruf von Fr. Schlappbach\n- Drucker")


def test_select_services_all_when_few():
    services = [{"text": str(index)} for index in range(6)]
    first, skipped, last = select_services(services, 3, 3)
    assert (len(first), skipped, last) == (6, 0, [])


def test_select_services_head_and_tail_when_many():
    services = [{"text": str(index)} for index in range(10)]
    first, skipped, last = select_services(services, 3, 2)
    assert [row["text"] for row in first] == ["0", "1", "2"]
    assert skipped == 5
    assert [row["text"] for row in last] == ["8", "9"]
    # tail = 0 darf nicht die ganze Liste liefern (Slice-Falle [-0:])
    first, skipped, last = select_services(services, 2, 0)
    assert (len(first), skipped, last) == (2, 8, [])


def test_shortlist_prompt_lists_ticket_and_candidates():
    prompt = build_shortlist_prompt(NEW_TICKET, CANDIDATES)
    assert "Neues Ticket #260148" in prompt
    assert "Betreff: KIM und ePA gehen nicht" in prompt
    assert "#260140 |" in prompt and "| gesichtet | ePA gestört" in prompt
    assert "ePA lädt keine Dokumente." in prompt
    assert prompt.index("#260140") < prompt.index("#260120")


def test_detail_prompt_shows_services_with_gap():
    services = {260140: [{"ticket_id": 260140, "date": 0,
                          "text": "Leistung %d" % index}
                         for index in range(8)]}
    prompt = build_detail_prompt(NEW_TICKET, CANDIDATES[:1], services, 2, 2)
    assert "Offenes Ticket #260140" in prompt
    assert "Leistung 0" in prompt and "Leistung 7" in prompt
    assert "Leistung 3" not in prompt
    assert "4 weitere Leistungen ausgelassen" in prompt
    # das neue Ticket hat keine Leistungen
    assert "Leistungen: keine" in prompt


def test_planned_services_are_split_off_and_capped():
    services = [{"date": NOW - 2 * HOUR, "text": "gestern"},
                {"date": NOW + 72 * HOUR, "text": "Termin 3"},
                {"date": NOW + 24 * HOUR, "text": "Termin 1"},
                {"date": NOW + 48 * HOUR, "text": "Termin 2"},
                {"date": 0, "text": "ohne Datum"}]
    past, planned = split_services(services, NOW)
    assert [row["text"] for row in past] == ["gestern", "ohne Datum"]
    assert [row["text"] for row in planned] == ["Termin 1", "Termin 2"]
    # ohne "jetzt" ist nichts geplant
    assert split_services(services, None)[1] == []


def test_planned_services_do_not_crowd_out_latest_notes():
    services = {260140: [{"ticket_id": 260140, "date": NOW - (10 - i) * HOUR,
                          "text": "Notiz %d" % i} for i in range(8)]
                + [{"ticket_id": 260140, "date": NOW + 24 * HOUR,
                    "text": "Termin Einrichtung KT"}]}
    prompt = build_detail_prompt(NEW_TICKET, CANDIDATES[:1], services, 2, 2,
                                 now=NOW)
    assert "Notiz 7" in prompt and "Notiz 6" in prompt   # die letzten zwei
    assert "4 weitere Leistungen ausgelassen" in prompt
    assert "Geplante Termine:\n- geplant: 27.11.2026 12:00: " \
           "Termin Einrichtung KT" in prompt


def test_series_reason_names_size_and_start():
    single = [{"id": 1, "created": NOW}]
    assert series_reason(single) == "Beide Tickets tragen denselben Betreff."
    series = [{"id": 3, "created": NOW}, {"id": 1, "created": NOW - HOUR},
              {"id": 2, "created": NOW - 1800}]
    assert series_reason(series) == (
        "Beide Tickets tragen denselben Betreff. Es ist das 4. offene "
        "Ticket mit diesem Betreff seit 26.11.2026 11:00.")


def test_stricter_rule_for_vague_tickets_is_in_both_prompts():
    for prompt in (SHORTLIST_SYSTEM_PROMPT, VERDICT_SYSTEM_PROMPT):
        assert "ohne konkretes Anliegen" in prompt
        assert "Dasselbe Thema allein reicht" in prompt
        assert "Nachrichten der Praxis" in prompt


# -- Nachrichten der Praxis --------------------------------------------------

def test_clean_mail_text_drops_quoted_history():
    reply = ("Guten Tag, der Drucker druckt leider immer noch nicht, bitte "
             "um Rückruf.\n\n-----Ursprüngliche Nachricht-----\nVon: "
             "support@abasoft.de\nWir haben den Treiber neu installiert.")
    assert clean_mail_text(reply).strip().endswith("bitte um Rückruf.")
    # Weiterleitung: über dem Zitat steht fast nichts - dann alles behalten
    forward = "FYI\n\nAm 25.11.2026 um 09:00 schrieb Praxis Müller:\n> KIM " \
              "geht nicht"
    assert "KIM geht nicht" in clean_mail_text(forward)
    # HTML ohne Klartextteil
    assert clean_mail_text("<p>Hallo&nbsp;Team</p><p>ePA geht nicht</p>") \
        .split() == ["Hallo", "Team", "ePA", "geht", "nicht"]


def test_clean_transcript_text_drops_our_lines():
    assert clean_transcript_text(
        "Zuordnung: SAIHIL | Herr Dr. Said (Arzt)\n\nWir brauchen einen "
        "Techniker.") == "Wir brauchen einen Techniker."
    assert clean_transcript_text("Zuordnung: X\n\n(keine Sprache erkannt)") \
        == ""
    assert clean_transcript_text(
        "[TANSS-Triage v0.2.0 | doc:5]\nHallo") == "Hallo"


def _mail(ticket_id, mail_id, text, sender="praxis@example.de"):
    return {"ticket_id": ticket_id, "kind": "mail", "id": mail_id, "date": 0,
            "sender": sender, "text": text}


def _voicemail(ticket_id, comment_id, date, text):
    return {"ticket_id": ticket_id, "kind": "voicemail", "id": comment_id,
            "date": date, "sender": "",
            "text": "Zuordnung: KUBEZ\n\n" + text}


FORM_MAIL = ("WARNUNG: EXTERNE NACHRICHT - Bitte seien Sie vorsichtig!\n"
             "Praxis: Kundenkürzel LIEBRE\nName: Frau R.\nNachricht:\n"
             "Laborwerte werden nicht übernommen, Beispiel Pat 16201.")


def test_group_messages_filters_and_marks_origin():
    ticket = {"id": 1, "created": NOW, "content": FORM_MAIL}
    rows = [
        _mail(1, 10, FORM_MAIL, "kunde@tele-x.abasoft-gmbh.de"),  # Ursprung
        _mail(1, 12, "Wir warten immer noch auf Rückmeldung (Labor)."),
        _mail(1, 11, "Ihr Ticket wurde automatisch erfasst ...",
              "support@abasoft.de"),                               # eigene
        _mail(1, 13, "Sie haben eine Sprachnachricht ...",
              "starface@tele-x.abasoft-gmbh.de"),                  # Starface
        _mail(1, 14, "   "),                                       # leer
        _voicemail(1, 20, NOW + 120, "Erster Anruf zum Labor."),   # Ursprung
        _voicemail(1, 21, NOW + 2 * HOUR, "(keine Sprache erkannt)"),
        _voicemail(1, 22, NOW + 3 * HOUR, "Zweiter Anruf, Labor geht nicht."),
        _voicemail(9, 30, NOW, "fremdes Ticket"),
    ]
    grouped = group_messages(rows, {1: ticket}, ["@abasoft.de"],
                             ["starface@tele-x.abasoft-gmbh.de"])
    assert list(grouped) == [1]
    mails = grouped[1]["mails"]
    assert [(mail["origin"], mail["sender"]) for mail in mails] == [
        (True, "kunde@tele-x.abasoft-gmbh.de"), (False, "praxis@example.de")]
    voicemails = grouped[1]["voicemails"]
    assert [(v["origin"], v["text"]) for v in voicemails] == [
        (True, "Erster Anruf zum Labor."),
        (False, "Zweiter Anruf, Labor geht nicht.")]


def test_form_header_is_compacted_for_display_only():
    ticket = {"id": 1, "created": NOW, "content": FORM_MAIL}
    rows = [_mail(1, 10, FORM_MAIL, "kunde@tele-x.abasoft-gmbh.de"),
            _mail(1, 11, FORM_MAIL.replace("Laborwerte", "Immer noch: Labor"),
                  "kunde@tele-x.abasoft-gmbh.de")]
    grouped = group_messages(rows, {1: ticket})
    # Ursprungsmail wird trotz Formularkopf erkannt
    assert [mail["origin"] for mail in grouped[1]["mails"]] == [True, False]
    prompt = build_shortlist_prompt(
        dict(NEW_TICKET, content=FORM_MAIL), [dict(CANDIDATES[0], id=1)],
        grouped)
    assert "Kundenkürzel" not in prompt and "Nachricht:" not in prompt
    assert ("+ 1 weitere Mail der Praxis, zuletzt: Name: Frau R. Immer "
            "noch: Labor werden nicht übernommen") in prompt
    assert "Name: Frau R." in prompt.split("Offene Tickets")[0]


def test_shortlist_prompt_shows_latest_further_message():
    candidate = dict(CANDIDATES[0], created=NOW - 48 * HOUR)
    rows = [_mail(260140, 1, "Erste Nachfrage."),
            _mail(260140, 2, "Wir warten immer noch auf Rückmeldung!"),
            _voicemail(260140, 5, NOW - 47 * HOUR, "Dritter Anruf: ePA.")]
    messages = group_messages(rows, {260140: candidate})
    prompt = build_shortlist_prompt(NEW_TICKET, [candidate], messages)
    assert ("    + 2 weitere Mails der Praxis, zuletzt: Wir warten immer "
            "noch auf Rückmeldung!") in prompt
    assert ("    + 1 weitere Sprachnachricht, zuletzt am 24.11.2026 13:00: "
            "Dritter Anruf: ePA.") in prompt


def test_detail_prompt_lists_messages_but_not_origin_mail():
    candidate = dict(CANDIDATES[0], created=NOW - 48 * HOUR,
                     content="Formular: ePA lädt keine Dokumente.")
    dated = dict(_mail(260140, 3, "Jetzt mit Datum."), date=NOW - HOUR)
    rows = [_mail(260140, 1, "Formular: ePA lädt keine Dokumente."),
            _mail(260140, 2, "Geht immer noch nicht."), dated,
            _voicemail(260140, 5, NOW - 48 * HOUR + 60, "ePA lädt nicht.")]
    messages = group_messages(rows, {260140: candidate})
    prompt = build_detail_prompt(NEW_TICKET, [candidate], {}, 3, 3, messages,
                                 now=NOW)
    block = prompt.split("Offenes Ticket #260140")[1]
    assert ("Weitere Mails der Praxis:\n"
            "- von praxis@example.de: Geht immer noch nicht.\n"
            "- 26.11.2026 11:00 von praxis@example.de: Jetzt mit Datum.") \
        in block
    assert block.count("ePA lädt keine Dokumente.") == 1   # nur Beschreibung
    # die erste Sprachnachricht bleibt - die Beschreibung fasst sie nur zusammen
    assert "Sprachnachrichten (Transkripte):\n- 24.11.2026 12:01: " \
           "ePA lädt nicht." in block


def test_series_points_to_oldest_ticket(tmp_path):
    llm = FakeLlm()
    title = "WEIKLE-Veeam:[Failed] Backup Policy: Tekathen PC"
    ticket = dict(NEW_TICKET, title=title, created=NOW)
    candidates = [dict(CANDIDATES[0], id=260140 - index, title=title,
                       created=NOW - (index + 1) * HOUR)
                  for index in range(4)] + CANDIDATES[1:]
    checker, client, state, _ = _checker(tmp_path, llm, ticket=ticket,
                                         candidates=candidates)
    checker.check_ticket(260148)
    assert llm.calls == []
    _, comment_title, body, _ = client.comments[0]
    assert comment_title == "Potenzielles Duplikat von Ticket #260137"
    assert body == ('"%s"\n\nBeide Tickets tragen denselben Betreff. Es ist '
                    'das 5. offene Ticket mit diesem Betreff seit 26.11.2026 '
                    '08:00.' % title)


def test_empty_voicemail_candidate_kept_when_later_call_has_content(tmp_path):
    llm = FakeLlm(_shortlist())
    candidate = dict(CANDIDATES[0], title="Sprachnachricht ohne Inhalt",
                     created=NOW - 5 * HOUR)
    rows = [_voicemail(260140, 1, NOW - 5 * HOUR + 60,
                       "(keine Sprache erkannt)"),
            _voicemail(260140, 2, NOW - HOUR, "KIM geht seit heute nicht.")]
    checker, _, _, _ = _checker(tmp_path, llm, candidates=[candidate],
                                messages=rows)
    checker.check_ticket(260148)
    assert len(llm.calls) == 1
    assert "KIM geht seit heute nicht." in llm.calls[0][1]


def test_messages_outage_counts_as_failed_attempt(tmp_path):
    llm = FakeLlm()
    checker, _, state, _ = _checker(tmp_path, llm)
    checker.messages_lookup = lambda ticket_ids: None
    checker.check_ticket(260148)
    assert llm.calls == []
    assert state.failed_tickets("dup:") == [260148]


def test_parse_shortlist_accepts_only_offered_ids():
    answer = json.dumps({"kandidaten": [
        {"ticket_id": 260140, "einschaetzung": "duplikat",
         "begruendung": "  Beide melden\nePA-Ausfall. "},
        {"ticket_id": 999999, "einschaetzung": "duplikat",    # erfunden
         "begruendung": "x"},
        {"ticket_id": 260120, "einschaetzung": "unklar",
         "begruendung": "Drucker, aber anderer Raum?"},
        {"ticket_id": 260140, "einschaetzung": "unklar",      # doppelt
         "begruendung": "x"},
        {"ticket_id": 260101, "einschaetzung": "vielleicht",  # unbekannt
         "begruendung": "x"},
    ]})
    sure, unclear = parse_shortlist(answer, {260140, 260120, 260101})
    assert sure == [(260140, "Beide melden ePA-Ausfall.")]
    assert unclear == [(260120, "Drucker, aber anderer Raum?")]
    # fehlt die Begründung (lokales Modell ohne Schema), zählt der Treffer
    answer = json.dumps({"kandidaten": [
        {"ticket_id": 260140, "einschaetzung": "duplikat"}]})
    assert parse_shortlist(answer, {260140}) == ([(260140, "")], [])


def test_parse_shortlist_rejects_garbage():
    assert parse_shortlist("kein JSON", {1}) is None
    assert parse_shortlist('{"etwas": []}', {1}) is None
    assert parse_shortlist('```json\n{"kandidaten": []}\n```', {1}) == ([], [])


def test_parse_verdict_accepts_only_offered_ids():
    answer = json.dumps({"duplikate": [
        {"ticket_id": 260120, "begruendung": "derselbe Drucker"},
        {"ticket_id": 5, "begruendung": "erfunden"}]})
    assert parse_verdict(answer, {260120}) == [(260120, "derselbe Drucker")]
    assert parse_verdict("nix", {260120}) is None


def test_comment_follows_template_with_reasons():
    # Vorlage: Kommentar in Ticket 260976
    title, body = build_comment([(CANDIDATES[0], "Grund eins.")])
    assert title == "Potenzielles Duplikat von Ticket #260140"
    assert body == '"ePA gestört"\n\nGrund eins.'

    title, body = build_comment([(CANDIDATES[0], "Grund eins."),
                                 (CANDIDATES[1], "Grund zwei."),
                                 (CANDIDATES[2], "")])
    assert title == "Potenzielles Duplikat von Ticket #260140"
    assert body.split("\n") == [
        '"ePA gestört"',
        "",
        "Grund eins.",
        "",
        "-" * 100,
        "Potenzielles Duplikat von Ticket #260120",
        '"Nadeldrucker druckt nicht"',
        "",
        "Grund zwei.",
        "",
        "-" * 100,
        "Potenzielles Duplikat von Ticket #260101",
        '"Konnektor-Zertifikate"',      # ohne Begründung kein Absatz
    ]


# -- Gesamtablauf mit Mocks --------------------------------------------------

class FakeClient:
    def __init__(self, tags=None, comment_error=None, access_error=None):
        self.tags = list(tags or [])
        self.comment_error = comment_error
        self.access_error = access_error
        self.comments = []
        self.added_tags = []

    def get_ticket(self, ticket_id):
        if self.access_error:
            raise self.access_error
        return {"id": ticket_id}

    def post_comment(self, ticket_id, title, content, internal=True):
        if self.comment_error:
            raise self.comment_error
        self.comments.append((ticket_id, title, content, internal))

    def get_ticket_tags(self, ticket_id):
        return [{"id": tag_id} for tag_id in self.tags]

    def add_ticket_tag(self, ticket_id, tag_id):
        self.added_tags.append((ticket_id, tag_id))


class FakeLlm:
    """Liefert die vorbereiteten Antworten der Reihe nach und merkt sich
    die Aufrufe (System-Prompt, Nutzer-Prompt, Schema)."""

    def __init__(self, *answers, error=None):
        self.answers = list(answers)
        self.error = error
        self.calls = []

    def complete(self, system, user, schema=None):
        self.calls.append((system, user, schema))
        if self.error:
            raise self.error
        return self.answers.pop(0)


def _shortlist(**verdicts):
    return json.dumps({"kandidaten": [
        {"ticket_id": int(ticket_id), "einschaetzung": verdict,
         "begruendung": "Sichtung %s." % ticket_id}
        for ticket_id, verdict in verdicts.items()]})


def _verdict(*ticket_ids):
    return json.dumps({"duplikate": [
        {"ticket_id": ticket_id, "begruendung": "passt"}
        for ticket_id in ticket_ids]})


def _checker(tmp_path, llm, ticket=NEW_TICKET, candidates=CANDIDATES,
             services=None, client=None, dry_run=False, messages=None,
             now=NOW):
    cfg = Config()
    cfg.state.db_path = str(tmp_path / "state.db")
    cfg.dry_run = dry_run
    state = State(cfg.state.db_path)
    client = client or FakeClient()
    holder = {"ticket": ticket}
    checker = DuplicateChecker(
        cfg=cfg, client=client, llm=llm, state=state,
        ticket_lookup=lambda ticket_id: holder["ticket"],
        candidates_lookup=lambda company_id, exclude_id: candidates,
        services_lookup=lambda ticket_ids: services or [],
        messages_lookup=lambda ticket_ids: (messages if messages is not None
                                            else []),
        clock=lambda: now)
    return checker, client, state, holder


def test_no_candidates_means_no_llm_call(tmp_path):
    llm = FakeLlm()
    checker, client, state, _ = _checker(tmp_path, llm, candidates=[])
    checker.check_ticket(260148)
    assert llm.calls == []
    assert client.comments == [] and client.added_tags == []
    assert state.is_done(item_key(260148))


def test_exact_title_match_needs_no_llm(tmp_path):
    llm = FakeLlm()
    candidates = CANDIDATES + [
        {"id": 260090, "company_id": 554, "status": "neu", "created": 0,
         "title": "kim und ePA gehen nicht  -> RR 07141 121210",
         "content": "Anruf von Frau Meier."}]
    checker, client, state, _ = _checker(tmp_path, llm,
                                         candidates=candidates)
    checker.check_ticket(260148)
    assert llm.calls == []
    assert len(client.comments) == 1
    ticket_id, title, body, internal = client.comments[0]
    assert (ticket_id, internal) == (260148, True)
    assert title == "Potenzielles Duplikat von Ticket #260090"
    assert body == ('"kim und ePA gehen nicht -> RR 07141 121210"'
                    "\n\nBeide Tickets tragen denselben Betreff.")
    assert client.added_tags == [(260148, 181)]
    assert state.is_done(item_key(260148))


def test_mentioned_ticket_number_needs_no_llm(tmp_path):
    llm = FakeLlm()
    reply = dict(NEW_TICKET, title="AW: [abasoft-Ticket #260120] Drucker",
                 content="Der Drucker geht immer noch nicht.")
    checker, client, state, _ = _checker(tmp_path, llm, ticket=reply)
    checker.check_ticket(260148)
    assert llm.calls == []
    assert client.comments[0][1] == "Potenzielles Duplikat von Ticket #260120"
    assert client.comments[0][2].endswith(
        "Das neue Ticket nennt die Ticketnummer #260120.")
    assert client.added_tags == [(260148, 181)]
    assert state.is_done(item_key(260148))


def test_mentioned_ticket_comes_before_same_title(tmp_path):
    llm = FakeLlm()
    ticket = dict(NEW_TICKET, title="ePA gestört",
                  content="siehe auch Ticket 260101")
    checker, client, _, _ = _checker(tmp_path, llm, ticket=ticket)
    checker.check_ticket(260148)
    assert llm.calls == []
    _, title, body, _ = client.comments[0]
    assert title.endswith("#260101")        # ausdrücklich genannt
    assert body.split("\n") == [
        '"Konnektor-Zertifikate"',
        "",
        "Das neue Ticket nennt die Ticketnummer #260101.",
        "",
        "-" * 100,
        "Potenzielles Duplikat von Ticket #260140",   # gleicher Betreff
        '"ePA gestört"',
        "",
        "Beide Tickets tragen denselben Betreff.",
    ]


def test_sure_match_from_titles_tags_and_comments(tmp_path):
    llm = FakeLlm(_shortlist(**{"260140": "duplikat"}))
    checker, client, state, _ = _checker(tmp_path, llm)
    checker.check_ticket(260148)
    assert len(llm.calls) == 1                      # keine Nachprüfung
    assert llm.calls[0][2] == SHORTLIST_SCHEMA
    assert client.comments[0][2] == ('"ePA gestört"\n\n'
                                     "Sichtung 260140.")
    assert client.added_tags == [(260148, 181)]
    assert state.is_done(item_key(260148))


def test_no_match_writes_nothing_but_is_done(tmp_path):
    llm = FakeLlm(_shortlist())
    checker, client, state, _ = _checker(tmp_path, llm)
    checker.check_ticket(260148)
    assert client.comments == [] and client.added_tags == []
    assert state.is_done(item_key(260148))


def test_unclear_candidates_get_detail_check(tmp_path):
    llm = FakeLlm(_shortlist(**{"260140": "unklar", "260120": "unklar"}),
                  _verdict(260140))
    services = [{"ticket_id": 260140, "date": 0,
                 "text": "KIM-Anmeldung schlägt fehl"},
                {"ticket_id": 260140, "date": 0, "text": "   "},
                {"ticket_id": 260140, "date": 0,
                 "text": "[Automatische Protokollierung des Ticket-E-Mail-"
                         "Versandes]\nEmpfänger: x@example.de\n---\nHallo"},
                {"ticket_id": 260140, "date": 0,
                 "text": "Automatische Rückantwort"}]
    checker, client, state, _ = _checker(tmp_path, llm, services=services)
    checker.check_ticket(260148)
    assert len(llm.calls) == 2
    _, detail_prompt, schema = llm.calls[1]
    assert schema == VERDICT_SCHEMA
    assert "KIM-Anmeldung schlägt fehl" in detail_prompt
    assert "Protokollierung" not in detail_prompt
    assert "Rückantwort" not in detail_prompt
    assert "Offenes Ticket #260120" in detail_prompt
    assert "#260101" not in detail_prompt           # war nicht unklar
    _, title, body, _ = client.comments[0]
    assert title == "Potenzielles Duplikat von Ticket #260140"
    assert body == '"ePA gestört"\n\npasst'  # nur der bestätigte Treffer


def test_detail_check_rejects_all(tmp_path):
    llm = FakeLlm(_shortlist(**{"260140": "unklar"}), _verdict())
    checker, client, state, _ = _checker(tmp_path, llm)
    checker.check_ticket(260148)
    assert client.comments == [] and client.added_tags == []
    assert state.is_done(item_key(260148))


def test_comment_names_at_most_three_matches(tmp_path):
    candidates = [dict(CANDIDATES[0], id=260100 + index,
                       title="Störung %d" % index) for index in range(5)]
    llm = FakeLlm(_shortlist(**{str(candidate["id"]): "duplikat"
                                for candidate in candidates}))
    checker, client, _, _ = _checker(tmp_path, llm, candidates=candidates)
    checker.check_ticket(260148)
    _, title, body, _ = client.comments[0]
    assert title == "Potenzielles Duplikat von Ticket #260100"
    assert [line for line in body.splitlines() if line.startswith('"')] == [
        '"Störung 0"', '"Störung 1"', '"Störung 2"']


def test_excluded_company_stays_open(tmp_path):
    llm = FakeLlm()
    internal = dict(NEW_TICKET, company_id=100000)
    checker, client, state, _ = _checker(tmp_path, llm, ticket=internal)
    checker.check_ticket(260148)
    assert llm.calls == [] and client.comments == []
    # nicht erledigt: wandert das Ticket später zu einem Kunden, wird geprüft
    assert not state.is_done(item_key(260148))


def test_ticket_without_company_stays_open(tmp_path):
    llm = FakeLlm()
    checker, client, state, _ = _checker(
        tmp_path, llm, ticket=dict(NEW_TICKET, company_id=0))
    checker.check_ticket(260148)
    assert llm.calls == []
    assert not state.is_done(item_key(260148))


def test_generic_starface_title_waits_for_transcription(tmp_path):
    llm = FakeLlm(_shortlist(**{"260140": "duplikat"}))
    waiting = dict(NEW_TICKET, title=STARFACE_SUBJECT)
    checker, client, state, holder = _checker(tmp_path, llm, ticket=waiting)
    checker.check_ticket(260148)
    assert llm.calls == [] and client.comments == []
    assert not state.is_done(item_key(260148))

    # nach der Transkription trägt das Ticket seinen echten Betreff
    holder["ticket"] = NEW_TICKET
    checker.check_ticket(260148)
    assert len(client.comments) == 1
    assert state.is_done(item_key(260148))


def test_empty_voicemail_is_not_checkable(tmp_path):
    llm = FakeLlm()
    empty = dict(NEW_TICKET, title="Sprachnachricht ohne Inhalt")
    candidates = [dict(CANDIDATES[0], title="Sprachnachricht ohne Inhalt")]
    checker, client, state, _ = _checker(tmp_path, llm, ticket=empty,
                                         candidates=candidates)
    checker.check_ticket(260148)
    assert llm.calls == [] and client.comments == []
    assert state.is_done(item_key(260148))


def test_uninformative_candidates_are_ignored(tmp_path):
    llm = FakeLlm()
    candidates = [dict(CANDIDATES[0], title="Sprachnachricht ohne Inhalt"),
                  dict(CANDIDATES[1], title=STARFACE_SUBJECT)]
    checker, client, state, _ = _checker(tmp_path, llm,
                                         candidates=candidates)
    checker.check_ticket(260148)
    assert llm.calls == []                          # nichts zu vergleichen
    assert state.is_done(item_key(260148))


def test_done_ticket_is_not_checked_again(tmp_path):
    llm = FakeLlm(_shortlist(**{"260140": "duplikat"}),
                  _shortlist(**{"260140": "duplikat"}))
    checker, client, _, _ = _checker(tmp_path, llm)
    checker.check_ticket(260148)
    checker.check_ticket(260148)
    assert len(llm.calls) == 1 and len(client.comments) == 1
    # der Einzellauf (force) prüft trotzdem
    checker.check_ticket(260148, force=True)
    assert len(llm.calls) == 2


def test_existing_tag_is_not_added_twice(tmp_path):
    llm = FakeLlm(_shortlist(**{"260140": "duplikat"}))
    checker, client, _, _ = _checker(tmp_path, llm,
                                     client=FakeClient(tags=[53, 181]))
    checker.check_ticket(260148)
    assert len(client.comments) == 1
    assert client.added_tags == []


def test_dry_run_writes_nothing(tmp_path):
    llm = FakeLlm(_shortlist(**{"260140": "duplikat"}))
    checker, client, state, _ = _checker(tmp_path, llm, dry_run=True)
    checker.check_ticket(260148)
    assert len(llm.calls) == 1
    assert client.comments == [] and client.added_tags == []
    assert not state.is_done(item_key(260148))


def test_llm_error_counts_as_failed_attempt(tmp_path):
    llm = FakeLlm(error=LlmError("Quota"))
    checker, client, state, _ = _checker(tmp_path, llm)
    checker.check_ticket(260148)                    # wirft nicht
    assert client.comments == []
    assert not state.is_done(item_key(260148))
    assert state.attempts(item_key(260148)) == 1
    assert state.failed_tickets("dup:") == [260148]


def test_unreadable_llm_answer_counts_as_failed_attempt(tmp_path):
    llm = FakeLlm("Das kann ich nicht beurteilen.")
    checker, _, state, _ = _checker(tmp_path, llm)
    checker.check_ticket(260148)
    assert state.attempts(item_key(260148)) == 1


def test_database_outage_counts_as_failed_attempt(tmp_path):
    llm = FakeLlm()
    checker, _, state, _ = _checker(tmp_path, llm, ticket=None)
    checker.check_ticket(260148)
    assert state.attempts(item_key(260148)) == 1


FORBIDDEN = TanssApiError(
    'POST /api/v1/tickets/260148/comments -> HTTP 403: '
    '{"error":{"text":"FORBIDDEN_NO_ENTITY_ACCESS"}}', status=403)


def test_ticket_without_api_access_costs_no_llm_call_and_no_retry(tmp_path):
    # Ticket einer Abteilung, die der API-Benutzer nicht sehen darf
    llm = FakeLlm(_shortlist(**{"260140": "duplikat"}))
    checker, client, state, _ = _checker(
        tmp_path, llm, client=FakeClient(access_error=FORBIDDEN))
    checker.check_ticket(260148)
    assert llm.calls == [] and client.comments == []
    assert state.is_done(item_key(260148))          # kein neuer Versuch
    assert state.failed_tickets("dup:") == []


def test_forbidden_comment_is_final_too(tmp_path):
    # Lesen erlaubt, Kommentieren nicht
    llm = FakeLlm(_shortlist(**{"260140": "duplikat"}))
    checker, client, state, _ = _checker(
        tmp_path, llm, client=FakeClient(comment_error=FORBIDDEN))
    checker.check_ticket(260148)
    assert client.added_tags == []
    assert state.is_done(item_key(260148))
    assert state.failed_tickets("dup:") == []


def test_other_api_errors_are_retried(tmp_path):
    llm = FakeLlm()
    outage = TanssApiError("GET /api/v1/tickets/260148 -> HTTP 502",
                           status=502)
    checker, _, state, _ = _checker(
        tmp_path, llm, client=FakeClient(access_error=outage))
    checker.check_ticket(260148)
    assert llm.calls == []
    assert not state.is_done(item_key(260148))
    assert state.failed_tickets("dup:") == [260148]


def test_no_access_check_without_candidates(tmp_path):
    # ohne Vergleichstickets gibt es nichts zu schreiben - also auch
    # keinen API-Aufruf und keine Warnung
    llm = FakeLlm()
    checker, _, state, _ = _checker(
        tmp_path, llm, candidates=[],
        client=FakeClient(access_error=FORBIDDEN))
    checker.check_ticket(260148)
    assert state.is_done(item_key(260148))


def test_deferred_after_failure_clears_failed_entry(tmp_path):
    # Erst scheitert das LLM, dann wandert das Ticket zur eigenen Firma:
    # der Fehl-Eintrag muss weg, sonst käme es endlos wieder in die Queue.
    llm = FakeLlm(error=LlmError("Quota"))
    checker, _, state, holder = _checker(tmp_path, llm)
    checker.check_ticket(260148)
    assert state.failed_tickets("dup:") == [260148]
    holder["ticket"] = dict(NEW_TICKET, company_id=100000)
    checker.check_ticket(260148)
    assert state.failed_tickets("dup:") == []
    assert not state.is_done(item_key(260148))


def test_deleted_ticket_is_done(tmp_path):
    llm = FakeLlm()
    checker, client, state, _ = _checker(tmp_path, llm, ticket={})
    checker.check_ticket(260148)
    assert llm.calls == [] and client.comments == []
    assert state.is_done(item_key(260148))


def test_inactive_without_llm(tmp_path):
    checker, client, state, _ = _checker(tmp_path, None)
    assert not checker.active
    checker.check_ticket(260148)
    assert client.comments == []
    assert not state.is_done(item_key(260148))


def test_failed_tickets_respects_prefix_age_and_attempts(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.mark_failed("dup:1", 1, "x")
    state.mark_failed("doc:2", 2, "x")              # anderes Präfix
    for _ in range(3):
        state.mark_failed("dup:3", 3, "x")          # aufgegeben
    assert state.failed_tickets("dup:") == [1]
    assert state.failed_tickets("dup:", min_age_seconds=600) == []
    state.forget("dup:1")
    assert state.failed_tickets("dup:") == []

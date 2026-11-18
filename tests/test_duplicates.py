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
Letzte Änderung: 2026-11-16
"""

import json

from tanss_triage.config import Config
from tanss_triage.duplicates import (
    SHORTLIST_SCHEMA, VERDICT_SCHEMA, DuplicateChecker,
    build_comment, build_detail_prompt, build_shortlist_prompt, clip_text,
    exact_title_matches, item_key, mentioned_ticket_matches, normalize_title,
    parse_shortlist, parse_verdict, select_services)
from tanss_triage.llm import LlmError
from tanss_triage.state import State
from tanss_triage.tanss_client import TanssApiError

STARFACE_SUBJECT = ("Sie haben eine Sprachnachricht von  00497432994360 "
                    "in Zentrale Überlauf erhalten")

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


def test_parse_shortlist_accepts_only_offered_ids():
    answer = json.dumps({"kandidaten": [
        {"ticket_id": 260140, "einschaetzung": "duplikat"},
        {"ticket_id": 999999, "einschaetzung": "duplikat"},   # erfunden
        {"ticket_id": 260120, "einschaetzung": "unklar"},
        {"ticket_id": 260140, "einschaetzung": "unklar"},     # doppelt
        {"ticket_id": 260101, "einschaetzung": "vielleicht"},  # unbekannt
    ]})
    sure, unclear = parse_shortlist(answer, {260140, 260120, 260101})
    assert sure == [260140]
    assert unclear == [260120]


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


def test_comment_title_names_first_match_and_body_lists_the_rest():
    title, body = build_comment(CANDIDATES[:1])
    assert title == "Potenzielles Duplikat von Ticket #260140"
    assert body == '"ePA gestört" #260140'

    title, body = build_comment(CANDIDATES)
    assert title == "Potenzielles Duplikat von Ticket #260140"
    assert body.splitlines() == [
        '"ePA gestört" #260140',
        "",
        "Potenzielles Duplikat von Ticket #260120",
        '"Nadeldrucker druckt nicht" #260120',
        "",
        "Potenzielles Duplikat von Ticket #260101",
        '"Konnektor-Zertifikate" #260101',
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
        {"ticket_id": int(ticket_id), "einschaetzung": verdict}
        for ticket_id, verdict in verdicts.items()]})


def _verdict(*ticket_ids):
    return json.dumps({"duplikate": [
        {"ticket_id": ticket_id, "begruendung": "passt"}
        for ticket_id in ticket_ids]})


def _checker(tmp_path, llm, ticket=NEW_TICKET, candidates=CANDIDATES,
             services=None, client=None, dry_run=False):
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
        services_lookup=lambda ticket_ids: services or [])
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
    assert body.endswith('" #260090')
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
    assert body.splitlines()[2:] == [       # danach: gleicher Betreff
        "Potenzielles Duplikat von Ticket #260140", '"ePA gestört" #260140']


def test_sure_match_from_titles_tags_and_comments(tmp_path):
    llm = FakeLlm(_shortlist(**{"260140": "duplikat"}))
    checker, client, state, _ = _checker(tmp_path, llm)
    checker.check_ticket(260148)
    assert len(llm.calls) == 1                      # keine Nachprüfung
    assert llm.calls[0][2] == SHORTLIST_SCHEMA
    assert "#260140" in client.comments[0][2]
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
                {"ticket_id": 260140, "date": 0, "text": "   "}]
    checker, client, state, _ = _checker(tmp_path, llm, services=services)
    checker.check_ticket(260148)
    assert len(llm.calls) == 2
    _, detail_prompt, schema = llm.calls[1]
    assert schema == VERDICT_SCHEMA
    assert "KIM-Anmeldung schlägt fehl" in detail_prompt
    assert "Offenes Ticket #260120" in detail_prompt
    assert "#260101" not in detail_prompt           # war nicht unklar
    body = client.comments[0][2]
    assert "#260140" in body and "#260120" not in body


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
        '"Störung 0" #260100', '"Störung 1" #260101', '"Störung 2" #260102']


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

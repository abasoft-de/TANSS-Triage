"""
Usage: pytest tests/test_processor.py

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: test_processor.py
Beschreibung: Prüft die Hilfsfunktionen des Prozessors (Audio-Filter,
              Starface-Erkennung, Anrufer-Extraktion, Überschreib-Schutz)
              und den Gesamtablauf mit gemocktem TANSS-Client, Whisper
              und LLM.
Letzte Änderung: 2026-09-10
"""

from tanss_triage.config import Config
from tanss_triage.processor import (
    MARKER_PREFIX, Processor, extract_caller_info, find_audio_documents,
    find_starface_mail, history_has_marker, should_replace_content,
    should_replace_title, comment_marker)
from tanss_triage.state import State
from tanss_triage.transcriber import Transcript

STARFACE_SUBJECT = ("Sie haben eine Sprachnachricht von  00497432994360 "
                    "in Zentrale Überlauf erhalten")
STARFACE_SUBJECT_WITH_NAME = (
    "Sie haben eine Sprachnachricht von STABRA (Praxis Stark) "
    "004971359390790 in Zentrale Überlauf erhalten")
STARFACE_BODY = ("WARNUNG: EXTERNE NACHRICHT ...\nSie haben am 10.09.26 um "
                 "10:15 Uhr eine Sprachmitteilung von 00497432994360 "
                 "auf Ihre Voicemail-Box Zentrale Überlauf der "
                 "STARFACE-Telefonanlage erhalten.")


# -- Hilfsfunktionen ---------------------------------------------------------

def test_audio_filter_accepts_extension_and_mime():
    documents = [
        {"id": 1, "fileName": "voicemail-2026-07-22_15-32.wav"},
        {"id": 2, "fileName": "SF_M_IMG_0", "mimeType": "image/png"},
        {"id": 3, "fileName": "anruf.MP3"},
        {"id": 4, "fileName": "aufnahme.bin", "mimeType": "audio/ogg"},
        {"id": 5, "fileName": "rechnung.pdf", "mimeType": "application/pdf"},
    ]
    hits = find_audio_documents(documents, ["wav", "mp3"])
    assert [document["id"] for document in hits] == [1, 3, 4]


def test_starface_mail_detection():
    history = {"mails": [
        {"inbound": False, "senderEMail": "starface@tele-x.abasoft-gmbh.de"},
        {"inbound": True, "senderEMail": "kunde@praxis.de"},
        {"inbound": True,
         "senderEMail": "STARFACE@tele-x.abasoft-gmbh.de",
         "subject": STARFACE_SUBJECT},
    ]}
    mail = find_starface_mail(history,
                              ["starface@tele-x.abasoft-gmbh.de"])
    assert mail is not None
    assert mail["subject"] == STARFACE_SUBJECT
    assert find_starface_mail(history, ["anderes@muster.de"]) is None
    assert find_starface_mail({}, ["x"]) is None


def test_caller_from_subject():
    number, box = extract_caller_info({"subject": STARFACE_SUBJECT})
    assert number == "00497432994360"
    assert box == "Zentrale Überlauf"


def test_caller_from_subject_with_name():
    number, box = extract_caller_info(
        {"subject": STARFACE_SUBJECT_WITH_NAME})
    assert number == "004971359390790"


def test_caller_with_duplicated_number():
    # Starface nennt die Nummer teils doppelt (Name = Nummer) - das darf
    # nicht zu einer verschmolzenen Doppelnummer werden.
    number, box = extract_caller_info({"subject": (
        "Sie haben eine Sprachnachricht von Anwendung 004915786757169 "
        "004915786757169 in Zentrale Nacht erhalten")})
    assert number == "004915786757169"
    assert box == "Zentrale Nacht"


def test_caller_number_with_separators():
    from tanss_triage.processor import extract_phone_number
    assert extract_phone_number("Zentrale 06154/6006-0") == "0615460060"
    assert extract_phone_number("+49 30 1234567") == "1234567"
    assert extract_phone_number("kein Anschluss") == ""


def test_caller_from_body_when_subject_empty():
    number, box = extract_caller_info({"subject": "",
                                       "bodyPlain": STARFACE_BODY})
    assert number == "00497432994360"
    assert box == "Zentrale Überlauf"


def test_overwrite_protection():
    pattern = "^Sie haben eine Sprachnachricht"
    assert should_replace_title(STARFACE_SUBJECT, pattern)
    assert not should_replace_title("Fax defekt bei Dr. Klein", pattern)
    boiler = ["WARNUNG: EXTERNE NACHRICHT", "STARFACE"]
    assert should_replace_content(STARFACE_BODY, boiler)
    assert should_replace_content("... ihre Starface-Anlage ...", boiler)
    assert not should_replace_content("Kunde meldet Druckerproblem.", boiler)


def test_marker_detection():
    history = {"comments": [
        {"title": "x",
         "content": comment_marker("doc:77") + "\nTranskript ..."}]}
    assert history_has_marker(history, "doc:77")
    assert not history_has_marker(history, "doc:78")
    assert not history_has_marker(history, "mail:77:x.wav")


# -- Gesamtablauf mit Mocks --------------------------------------------------

class FakeClient:
    def __init__(self, documents, history, ticket, identify=None):
        self.documents = documents
        self.history = history
        self.ticket = ticket
        self.identify = identify or {"fromPhoneNrInfos":
                                     {"foundType": "NONE", "items": []}}
        self.comments = []
        self.updates = []

    def get_documents(self, ticket_id):
        return self.documents

    def get_ticket_history(self, ticket_id):
        return self.history

    def get_ticket(self, ticket_id):
        return dict(self.ticket)

    def download_document(self, ticket_id, document_id, path):
        with open(path, "wb") as handle:
            handle.write(b"RIFF")
        return 4

    def post_comment(self, ticket_id, title, content, internal=True):
        self.comments.append((ticket_id, title, content, internal))

    def update_ticket(self, ticket_id, ticket):
        self.updates.append((ticket_id, ticket))

    def identify_phone_number(self, number):
        return self.identify

    def get_company_employees(self, company_id):
        return [{"id": 7, "name": "Duft, Petra"}]

    def get_mail(self, mail_id):
        return self.mail_details.get(mail_id, {})

    def download_mail_attachment(self, mail_id, attachment, path):
        with open(path, "wb") as handle:
            handle.write(b"RIFF")
        return 4

    mail_details = {}


class FakeTranscriber:
    def transcribe(self, path):
        return Transcript(text="Hallo, hier Frau Duft, das Fax geht nicht.",
                          duration=25.0, language="de")


class FakeLlm:
    def complete(self, system, user):
        return ('{"betreff": "Faxversand gestört", '
                '"beschreibung": "Fax geht nicht.\\nAnrufer: Frau Duft", '
                '"melder_id": 7}')


def _config(tmp_path):
    cfg = Config()
    cfg.state.db_path = str(tmp_path / "state.db")
    return cfg


def _processor(cfg, client, llm=None, mail_lookup=None, labels=None):
    return Processor(cfg=cfg, client=client,
                     transcriber=FakeTranscriber(), llm=llm,
                     state=State(cfg.state.db_path),
                     is_multi_company=lambda _id: False,
                     mail_attachments_lookup=mail_lookup,
                     labels_lookup=(lambda c, e: labels) if labels else None)


STARFACE_HISTORY = {"mails": [{
    "inbound": True,
    "senderEMail": "starface@tele-x.abasoft-gmbh.de",
    "subject": STARFACE_SUBJECT,
    "bodyPlain": STARFACE_BODY,
}], "comments": []}

STARFACE_TICKET = {"id": 4711, "title": STARFACE_SUBJECT,
                   "content": STARFACE_BODY, "companyId": 100000,
                   "remitterId": 0, "statusId": 1000}

AUDIO_DOCUMENTS = [{"id": 55, "fileName": "voicemail-2026-09-10_10-15.wav",
                    "mimeType": "audio/x-wav"}]

IDENTIFY_EMPLOYEE = {"fromPhoneNrInfos": {
    "foundType": "EMPLOYEE",
    "items": [{"type": "EMPLOYEE", "id": 7, "name": "Duft, Petra",
               "companyId": 94, "charsLeftOut": 0}]}}


def test_starface_ticket_full_flow(tmp_path):
    client = FakeClient(AUDIO_DOCUMENTS, STARFACE_HISTORY, STARFACE_TICKET,
                        identify=IDENTIFY_EMPLOYEE)
    cfg = _config(tmp_path)
    processor = _processor(cfg, client, llm=FakeLlm())
    processor.process_ticket(4711)

    assert len(client.comments) == 1
    _, title, body, internal = client.comments[0]
    assert title == "Transkript: voicemail-2026-09-10_10-15.wav"
    assert "Frau Duft, das Fax geht nicht" in body
    assert MARKER_PREFIX not in body       # kein Marker mehr (Vorgabe)
    assert "Datei:" not in body
    # Ohne DB-Labels greifen die Fallbacks aus der identify-Antwort
    assert "Zuordnung: Firma 94 | Duft, Petra" in body
    assert internal is True

    assert len(client.updates) == 1
    _, update = client.updates[0]
    assert update["title"] == "Faxversand gestört"
    assert update["content"].startswith("Fax geht nicht.")
    assert update["companyId"] == 94
    assert update["remitterId"] == 7

    # zweiter Lauf: Idempotenz über den State
    processor.process_ticket(4711)
    assert len(client.comments) == 1


def test_manually_edited_title_is_kept(tmp_path):
    ticket = dict(STARFACE_TICKET, title="Fax defekt (manuell angepasst)")
    client = FakeClient(AUDIO_DOCUMENTS, STARFACE_HISTORY, ticket,
                        identify=IDENTIFY_EMPLOYEE)
    processor = _processor(_config(tmp_path), client, llm=FakeLlm())
    processor.process_ticket(4711)

    _, update = client.updates[0]
    assert update["title"] == "Fax defekt (manuell angepasst)"
    assert update["content"].startswith("Fax geht nicht.")   # Boilerplate weg


def test_non_starface_only_gets_comment(tmp_path):
    history = {"mails": [{"inbound": True, "senderEMail": "kunde@praxis.de",
                          "subject": "Mail mit Diktat"}], "comments": []}
    ticket = {"id": 4712, "title": "Diktat", "content": "siehe Anhang",
              "companyId": 94}
    client = FakeClient(AUDIO_DOCUMENTS, history, ticket)
    processor = _processor(_config(tmp_path), client, llm=FakeLlm())
    processor.process_ticket(4712)

    assert len(client.comments) == 1       # Transkript ja ...
    assert client.updates == []            # ... Ticket bleibt unangetastet
    assert "Zuordnung:" not in client.comments[0][2]


def test_comment_format_with_db_labels(tmp_path):
    client = FakeClient(AUDIO_DOCUMENTS, STARFACE_HISTORY, STARFACE_TICKET,
                        identify=IDENTIFY_EMPLOYEE)
    processor = _processor(_config(tmp_path), client, llm=None,
                           labels=("ABASTU",
                                   "Herr Dr. med. Sascha Orlik (Arzt)"))
    processor.process_ticket(4711)

    _, title, body, _ = client.comments[0]
    assert title == "Transkript: voicemail-2026-09-10_10-15.wav"
    lines = body.split("\n")
    assert lines[0] == ("Zuordnung: ABASTU | "
                        "Herr Dr. med. Sascha Orlik (Arzt)")
    assert lines[1] == ""
    assert lines[2].startswith("Hallo, hier Frau Duft")
    assert len(lines) == 3                 # kein Marker, keine Datei-Zeile


def test_comment_company_only_shows_kubez(tmp_path):
    identify = {"fromPhoneNrInfos": {
        "foundType": "COMPANY",
        "items": [{"type": "COMPANY", "id": 94, "name": "abasoft",
                   "charsLeftOut": 3}]}}
    client = FakeClient(AUDIO_DOCUMENTS, STARFACE_HISTORY, STARFACE_TICKET,
                        identify=identify)
    processor = _processor(_config(tmp_path), client, llm=None,
                           labels=("ABASTU", None))
    processor.process_ticket(4711)

    body = client.comments[0][2]
    assert body.split("\n")[0] == "Zuordnung: ABASTU"


def test_comment_unknown_number_shows_reason(tmp_path):
    client = FakeClient(AUDIO_DOCUMENTS, STARFACE_HISTORY, STARFACE_TICKET)
    processor = _processor(_config(tmp_path), client, llm=None)
    processor.process_ticket(4711)

    body = client.comments[0][2]
    assert body.split("\n")[0] == ("Zuordnung: Rufnummer in TANSS nicht "
                                   "bekannt - keine automatische Zuordnung.")


def test_without_llm_assignment_still_happens(tmp_path):
    client = FakeClient(AUDIO_DOCUMENTS, STARFACE_HISTORY, STARFACE_TICKET,
                        identify=IDENTIFY_EMPLOYEE)
    processor = _processor(_config(tmp_path), client, llm=None)
    processor.process_ticket(4711)

    assert len(client.comments) == 1
    _, update = client.updates[0]
    assert update["title"] == STARFACE_SUBJECT       # kein LLM, kein Rewrite
    assert update["companyId"] == 94
    assert update["remitterId"] == 7


def test_dry_run_writes_nothing(tmp_path):
    client = FakeClient(AUDIO_DOCUMENTS, STARFACE_HISTORY, STARFACE_TICKET,
                        identify=IDENTIFY_EMPLOYEE)
    cfg = _config(tmp_path)
    cfg.dry_run = True
    processor = _processor(cfg, client, llm=FakeLlm())
    processor.process_ticket(4711)

    assert client.comments == []
    assert client.updates == []
    # dry-run merkt sich nichts - der scharfe Lauf soll später verarbeiten
    assert not processor.state.is_done("doc:55")


def test_marker_in_ticket_prevents_duplicates(tmp_path):
    history = {"mails": STARFACE_HISTORY["mails"],
               "comments": [{"title": "Transkript",
                             "content": comment_marker("doc:55")}]}
    client = FakeClient(AUDIO_DOCUMENTS, history, STARFACE_TICKET)
    processor = _processor(_config(tmp_path), client, llm=None)
    processor.process_ticket(4711)

    assert client.comments == []
    assert processor.state.is_done("doc:55")     # State nachgetragen


MAIL_HISTORY = {"mails": [dict(STARFACE_HISTORY["mails"][0], id=356324)],
                "comments": []}

MAIL_ATTACHMENT_ROWS = [
    {"filename": "SF_M_IMG_0", "filenameDB": "att_356324_SF_M_IMG_0",
     "verzeichnis": "350000", "extension": ""},
    {"filename": "voicemail-2026-09-15_08-00.wav",
     "filenameDB": "att_356324_voicemail-2026-09-15_08-00.wav",
     "verzeichnis": "350000", "extension": "wav"},
]


def test_mail_attachment_from_storage(tmp_path):
    # Kein Ticket-Dokument - die WAV hängt an der Mail und liegt im Storage
    storage = tmp_path / "storage" / "350000"
    storage.mkdir(parents=True)
    (storage / "att_356324_voicemail-2026-09-15_08-00.wav").write_bytes(
        b"RIFF")

    cfg = _config(tmp_path)
    cfg.storage.mail_attachments_dir = str(tmp_path / "storage")
    client = FakeClient([], MAIL_HISTORY, STARFACE_TICKET)
    processor = _processor(cfg, client, llm=None,
                           mail_lookup=lambda mid: MAIL_ATTACHMENT_ROWS)
    processor.process_ticket(4711)

    assert len(client.comments) == 1
    _, title, body, _ = client.comments[0]
    assert title == "Transkript: voicemail-2026-09-15_08-00.wav"
    assert "Frau Duft, das Fax geht nicht" in body
    assert processor.state.is_done(
        "mail:356324:voicemail-2026-09-15_08-00.wav")


def test_mail_attachment_api_fallback(tmp_path):
    # DB nicht verfügbar (Lookup liefert None) -> Anhang über die API laden
    client = FakeClient([], MAIL_HISTORY, STARFACE_TICKET)
    client.mail_details = {356324: {"attachments": [
        {"filename": "voicemail-2026-09-15_08-00.wav",
         "url": "/api/v1/util/files/abc"}]}}
    processor = _processor(_config(tmp_path), client, llm=None,
                           mail_lookup=lambda mid: None)
    processor.process_ticket(4711)

    assert len(client.comments) == 1
    _, title, body, _ = client.comments[0]
    assert title == "Transkript: voicemail-2026-09-15_08-00.wav"
    assert "Frau Duft, das Fax geht nicht" in body
    assert processor.state.is_done(
        "mail:356324:voicemail-2026-09-15_08-00.wav")


def test_missing_storage_file_is_recorded_as_failure(tmp_path):
    cfg = _config(tmp_path)
    cfg.storage.mail_attachments_dir = str(tmp_path / "storage")  # leer
    client = FakeClient([], MAIL_HISTORY, STARFACE_TICKET)
    processor = _processor(cfg, client, llm=None,
                           mail_lookup=lambda mid: MAIL_ATTACHMENT_ROWS)
    processor.process_ticket(4711)

    assert client.comments == []
    assert processor.state.attempts(
        "mail:356324:voicemail-2026-09-15_08-00.wav") == 1


def test_without_audio_nothing_happens(tmp_path):
    client = FakeClient([{"id": 9, "fileName": "brief.pdf"}],
                        STARFACE_HISTORY, STARFACE_TICKET)
    processor = _processor(_config(tmp_path), client, llm=FakeLlm())
    processor.process_ticket(4711)
    assert client.comments == []
    assert client.updates == []

"""
Usage: pytest tests/test_processor.py

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: test_processor.py
Beschreibung: Prueft die Hilfsfunktionen des Prozessors (Audio-Filter,
              Starface-Erkennung, Anrufer-Extraktion, Ueberschreib-Schutz)
              und den Gesamtablauf mit gemocktem TANSS-Client, Whisper
              und LLM.
Letzte Aenderung: 2026-09-10
"""

from tanss_triage.config import Config
from tanss_triage.processor import (
    Processor, extract_caller_info, find_audio_documents, find_starface_mail,
    history_has_marker, should_replace_content, should_replace_title,
    comment_marker)
from tanss_triage.state import State
from tanss_triage.transcriber import Transcript

STARFACE_SUBJECT = ("Sie haben eine Sprachnachricht von  00497432994360 "
                    "in Zentrale Überlauf erhalten")
STARFACE_SUBJECT_MIT_NAME = (
    "Sie haben eine Sprachnachricht von STABRA (Praxis Stark) "
    "004971359390790 in Zentrale Überlauf erhalten")
STARFACE_BODY = ("WARNUNG: EXTERNE NACHRICHT ...\nSie haben am 10.09.26 um "
                 "10:15 Uhr eine Sprachmitteilung von 00497432994360 "
                 "auf Ihre Voicemail-Box Zentrale Überlauf der "
                 "STARFACE-Telefonanlage erhalten.")


# -- Hilfsfunktionen ---------------------------------------------------------

def test_audio_filter_nimmt_endung_und_mime():
    documents = [
        {"id": 1, "fileName": "voicemail-2026-07-22_15-32.wav"},
        {"id": 2, "fileName": "SF_M_IMG_0", "mimeType": "image/png"},
        {"id": 3, "fileName": "anruf.MP3"},
        {"id": 4, "fileName": "aufnahme.bin", "mimeType": "audio/ogg"},
        {"id": 5, "fileName": "rechnung.pdf", "mimeType": "application/pdf"},
    ]
    hits = find_audio_documents(documents, ["wav", "mp3"])
    assert [document["id"] for document in hits] == [1, 3, 4]


def test_starface_mail_erkennung():
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


def test_anrufer_aus_betreff():
    number, box = extract_caller_info({"subject": STARFACE_SUBJECT})
    assert number == "00497432994360"
    assert box == "Zentrale Überlauf"


def test_anrufer_aus_betreff_mit_namen():
    number, box = extract_caller_info(
        {"subject": STARFACE_SUBJECT_MIT_NAME})
    assert number == "004971359390790"


def test_anrufer_aus_body_wenn_betreff_leer():
    number, box = extract_caller_info({"subject": "",
                                       "bodyPlain": STARFACE_BODY})
    assert number == "00497432994360"
    assert box == "Zentrale Überlauf"


def test_ueberschreib_schutz():
    pattern = "^Sie haben eine Sprachnachricht"
    assert should_replace_title(STARFACE_SUBJECT, pattern)
    assert not should_replace_title("Fax defekt bei Dr. Klein", pattern)
    boiler = ["WARNUNG: EXTERNE NACHRICHT", "STARFACE"]
    assert should_replace_content(STARFACE_BODY, boiler)
    assert should_replace_content("... ihre Starface-Anlage ...", boiler)
    assert not should_replace_content("Kunde meldet Druckerproblem.", boiler)


def test_marker_erkennung():
    history = {"comments": [
        {"title": "x", "content": comment_marker(77) + "\nTranskript ..."}]}
    assert history_has_marker(history, 77)
    assert not history_has_marker(history, 78)


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


class FakeTranscriber:
    def transcribe(self, path):
        return Transcript(text="Hallo, hier Frau Duft, das Fax geht nicht.",
                          duration=25.0, language="de")


class FakeLlm:
    def complete(self, system, user):
        return ('{"betreff": "Fax defekt - Praxis Duft", '
                '"beschreibung": "Fax geht nicht.\\nAnrufer: Frau Duft", '
                '"melder_id": 7}')


def _config(tmp_path):
    cfg = Config()
    cfg.state.db_path = str(tmp_path / "state.db")
    return cfg


def _processor(cfg, client, llm=None):
    return Processor(cfg=cfg, client=client,
                     transcriber=FakeTranscriber(), llm=llm,
                     state=State(cfg.state.db_path),
                     is_multi_company=lambda _id: False)


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


def test_starface_ticket_komplett(tmp_path):
    client = FakeClient(AUDIO_DOCUMENTS, STARFACE_HISTORY, STARFACE_TICKET,
                        identify=IDENTIFY_EMPLOYEE)
    cfg = _config(tmp_path)
    processor = _processor(cfg, client, llm=FakeLlm())
    processor.process_ticket(4711)

    assert len(client.comments) == 1
    _, title, body, internal = client.comments[0]
    assert "voicemail-2026-09-10_10-15.wav" in title
    assert "Frau Duft, das Fax geht nicht" in body
    assert comment_marker(55) in body
    assert internal is True

    assert len(client.updates) == 1
    _, update = client.updates[0]
    assert update["title"] == "Fax defekt - Praxis Duft"
    assert update["content"].startswith("Fax geht nicht.")
    assert update["companyId"] == 94
    assert update["remitterId"] == 7

    # zweiter Lauf: Idempotenz ueber den State
    processor.process_ticket(4711)
    assert len(client.comments) == 1


def test_manuell_angepasster_betreff_bleibt(tmp_path):
    ticket = dict(STARFACE_TICKET, title="Fax defekt (manuell angepasst)")
    client = FakeClient(AUDIO_DOCUMENTS, STARFACE_HISTORY, ticket,
                        identify=IDENTIFY_EMPLOYEE)
    processor = _processor(_config(tmp_path), client, llm=FakeLlm())
    processor.process_ticket(4711)

    _, update = client.updates[0]
    assert update["title"] == "Fax defekt (manuell angepasst)"
    assert update["content"].startswith("Fax geht nicht.")   # Boilerplate weg


def test_nicht_starface_nur_kommentar(tmp_path):
    history = {"mails": [{"inbound": True, "senderEMail": "kunde@praxis.de",
                          "subject": "Mail mit Diktat"}], "comments": []}
    ticket = {"id": 4712, "title": "Diktat", "content": "siehe Anhang",
              "companyId": 94}
    client = FakeClient(AUDIO_DOCUMENTS, history, ticket)
    processor = _processor(_config(tmp_path), client, llm=FakeLlm())
    processor.process_ticket(4712)

    assert len(client.comments) == 1       # Transkript ja ...
    assert client.updates == []            # ... Ticket bleibt unangetastet


def test_ohne_llm_nur_zuordnung(tmp_path):
    client = FakeClient(AUDIO_DOCUMENTS, STARFACE_HISTORY, STARFACE_TICKET,
                        identify=IDENTIFY_EMPLOYEE)
    processor = _processor(_config(tmp_path), client, llm=None)
    processor.process_ticket(4711)

    assert len(client.comments) == 1
    _, update = client.updates[0]
    assert update["title"] == STARFACE_SUBJECT       # kein LLM, kein Rewrite
    assert update["companyId"] == 94
    assert update["remitterId"] == 7


def test_dry_run_schreibt_nichts(tmp_path):
    client = FakeClient(AUDIO_DOCUMENTS, STARFACE_HISTORY, STARFACE_TICKET,
                        identify=IDENTIFY_EMPLOYEE)
    cfg = _config(tmp_path)
    cfg.dry_run = True
    processor = _processor(cfg, client, llm=FakeLlm())
    processor.process_ticket(4711)

    assert client.comments == []
    assert client.updates == []
    # dry-run merkt sich nichts - der scharfe Lauf soll spaeter verarbeiten
    assert not processor.state.is_done(55)


def test_marker_im_ticket_verhindert_doppelung(tmp_path):
    history = {"mails": STARFACE_HISTORY["mails"],
               "comments": [{"title": "Transkript",
                             "content": comment_marker(55)}]}
    client = FakeClient(AUDIO_DOCUMENTS, history, STARFACE_TICKET)
    processor = _processor(_config(tmp_path), client, llm=None)
    processor.process_ticket(4711)

    assert client.comments == []
    assert processor.state.is_done(55)     # State nachgetragen


def test_ohne_audio_passiert_nichts(tmp_path):
    client = FakeClient([{"id": 9, "fileName": "brief.pdf"}],
                        STARFACE_HISTORY, STARFACE_TICKET)
    processor = _processor(_config(tmp_path), client, llm=FakeLlm())
    processor.process_ticket(4711)
    assert client.comments == []
    assert client.updates == []

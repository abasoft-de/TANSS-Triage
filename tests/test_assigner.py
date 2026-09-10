"""
Usage: pytest tests/test_assigner.py

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: test_assigner.py
Beschreibung: Prueft die Zuordnungsregeln gegen nachgebaute Antworten von
              POST /api/v1/phoneCalls/identify.
Letzte Aenderung: 2026-09-10
"""

from tanss_triage.assigner import decide_assignment


def _identify(found_type, items):
    return {"fromPhoneNrInfos": {"foundType": found_type, "items": items,
                                 "result": items[0] if items else None}}


def _einzelfirma(_employee_id):
    return False


def test_firma_eindeutig():
    content = _identify("COMPANY", [
        {"type": "COMPANY", "id": 654, "name": "Schaal", "charsLeftOut": 3}])
    assignment = decide_assignment(content, _einzelfirma)
    assert assignment.company_id == 654
    assert assignment.remitter_id is None


def test_mehrere_firmen_keine_zuordnung():
    content = _identify("COMPANY", [
        {"type": "COMPANY", "id": 654, "charsLeftOut": 3},
        {"type": "COMPANY", "id": 655, "charsLeftOut": 2}])
    assignment = decide_assignment(content, _einzelfirma)
    assert not assignment.has_change


def test_mitarbeiter_mit_durchwahl_wird_melder():
    content = _identify("EMPLOYEE", [
        {"type": "EMPLOYEE", "id": 42, "name": "Duft, Petra",
         "companyId": 94, "charsLeftOut": 0}])
    assignment = decide_assignment(content, _einzelfirma)
    assert assignment.company_id == 94
    assert assignment.remitter_id == 42


def test_firmennummer_beim_mitarbeiter_nur_firma():
    # Dieselbe Nummer matcht Mitarbeiter UND Firma exakt -> Zentrale,
    # also nur die Firma zuweisen.
    content = _identify("EMPLOYEE", [
        {"type": "EMPLOYEE", "id": 42, "companyId": 94, "charsLeftOut": 0},
        {"type": "COMPANY", "id": 94, "charsLeftOut": 0}])
    assignment = decide_assignment(content, _einzelfirma)
    assert assignment.company_id == 94
    assert assignment.remitter_id is None


def test_mitarbeiter_nur_per_durchwahlschnitt_kein_melder():
    content = _identify("EMPLOYEE", [
        {"type": "EMPLOYEE", "id": 42, "companyId": 94, "charsLeftOut": 3}])
    assignment = decide_assignment(content, _einzelfirma)
    assert assignment.company_id == 94
    assert assignment.remitter_id is None


def test_mehrere_mitarbeiter_gleiche_firma_nur_firma():
    content = _identify("EMPLOYEE", [
        {"type": "EMPLOYEE", "id": 42, "companyId": 94, "charsLeftOut": 0},
        {"type": "EMPLOYEE", "id": 43, "companyId": 94, "charsLeftOut": 0}])
    assignment = decide_assignment(content, _einzelfirma)
    assert assignment.company_id == 94
    assert assignment.remitter_id is None


def test_mehrere_mitarbeiter_verschiedene_firmen_nichts():
    content = _identify("EMPLOYEE", [
        {"type": "EMPLOYEE", "id": 42, "companyId": 94, "charsLeftOut": 0},
        {"type": "EMPLOYEE", "id": 43, "companyId": 95, "charsLeftOut": 0}])
    assignment = decide_assignment(content, _einzelfirma)
    assert not assignment.has_change


def test_mitarbeiter_in_mehreren_firmen_nichts():
    content = _identify("EMPLOYEE", [
        {"type": "EMPLOYEE", "id": 42, "companyId": 94, "charsLeftOut": 0}])
    assignment = decide_assignment(content, lambda _id: True)
    assert not assignment.has_change
    assert "mehreren Firmen" in assignment.note


def test_db_nicht_erreichbar_failsafe():
    content = _identify("EMPLOYEE", [
        {"type": "EMPLOYEE", "id": 42, "companyId": 94, "charsLeftOut": 0}])
    assignment = decide_assignment(content, lambda _id: None)
    assert not assignment.has_change


def test_assign_remitter_abschaltbar():
    content = _identify("EMPLOYEE", [
        {"type": "EMPLOYEE", "id": 42, "companyId": 94, "charsLeftOut": 0}])
    assignment = decide_assignment(content, _einzelfirma,
                                   assign_remitter=False)
    assert assignment.company_id == 94
    assert assignment.remitter_id is None


def test_unbekannte_nummer_nichts():
    assignment = decide_assignment(_identify("NONE", []), _einzelfirma)
    assert not assignment.has_change
    assignment = decide_assignment(_identify("TOO_SHORT", []), _einzelfirma)
    assert not assignment.has_change


def test_inaktiver_mitarbeiter_nur_firma():
    content = _identify("EMPLOYEE_INACTIVE", [
        {"type": "EMPLOYEE_INACTIVE", "id": 42, "companyId": 94,
         "charsLeftOut": 0}])
    assignment = decide_assignment(content, _einzelfirma)
    assert assignment.company_id == 94
    assert assignment.remitter_id is None

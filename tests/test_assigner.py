"""
Usage: pytest tests/test_assigner.py

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: test_assigner.py
Beschreibung: Prüft die Zuordnungsregeln gegen nachgebaute Antworten von
              POST /api/v1/phoneCalls/identify.
Letzte Änderung: 2026-09-10
"""

from tanss_triage.assigner import decide_assignment


def _identify(found_type, items):
    return {"fromPhoneNrInfos": {"foundType": found_type, "items": items,
                                 "result": items[0] if items else None}}


def _single_company(_employee_id):
    return False


def test_unique_company_is_assigned():
    content = _identify("COMPANY", [
        {"type": "COMPANY", "id": 654, "name": "Schaal", "charsLeftOut": 3}])
    assignment = decide_assignment(content, _single_company)
    assert assignment.company_id == 654
    assert assignment.remitter_id is None


def test_multiple_companies_no_assignment():
    content = _identify("COMPANY", [
        {"type": "COMPANY", "id": 654, "charsLeftOut": 3},
        {"type": "COMPANY", "id": 655, "charsLeftOut": 2}])
    assignment = decide_assignment(content, _single_company)
    assert not assignment.has_change


def test_employee_with_direct_number_becomes_remitter():
    content = _identify("EMPLOYEE", [
        {"type": "EMPLOYEE", "id": 42, "name": "Duft, Petra",
         "companyId": 94, "charsLeftOut": 0}])
    assignment = decide_assignment(content, _single_company)
    assert assignment.company_id == 94
    assert assignment.remitter_id == 42


def test_company_number_on_employee_assigns_company_only():
    # Dieselbe Nummer matcht Mitarbeiter UND Firma exakt -> Zentrale,
    # also nur die Firma zuweisen.
    content = _identify("EMPLOYEE", [
        {"type": "EMPLOYEE", "id": 42, "companyId": 94, "charsLeftOut": 0},
        {"type": "COMPANY", "id": 94, "charsLeftOut": 0}])
    assignment = decide_assignment(content, _single_company)
    assert assignment.company_id == 94
    assert assignment.remitter_id is None


def test_employee_matched_by_truncation_no_remitter():
    content = _identify("EMPLOYEE", [
        {"type": "EMPLOYEE", "id": 42, "companyId": 94, "charsLeftOut": 3}])
    assignment = decide_assignment(content, _single_company)
    assert assignment.company_id == 94
    assert assignment.remitter_id is None


def test_multiple_employees_same_company_assigns_company_only():
    content = _identify("EMPLOYEE", [
        {"type": "EMPLOYEE", "id": 42, "companyId": 94, "charsLeftOut": 0},
        {"type": "EMPLOYEE", "id": 43, "companyId": 94, "charsLeftOut": 0}])
    assignment = decide_assignment(content, _single_company)
    assert assignment.company_id == 94
    assert assignment.remitter_id is None


def test_multiple_employees_different_companies_no_assignment():
    content = _identify("EMPLOYEE", [
        {"type": "EMPLOYEE", "id": 42, "companyId": 94, "charsLeftOut": 0},
        {"type": "EMPLOYEE", "id": 43, "companyId": 95, "charsLeftOut": 0}])
    assignment = decide_assignment(content, _single_company)
    assert not assignment.has_change


def test_employee_in_multiple_companies_no_assignment():
    content = _identify("EMPLOYEE", [
        {"type": "EMPLOYEE", "id": 42, "companyId": 94, "charsLeftOut": 0}])
    assignment = decide_assignment(content, lambda _id: True)
    assert not assignment.has_change
    assert "mehreren Firmen" in assignment.note


def test_db_unreachable_is_failsafe():
    content = _identify("EMPLOYEE", [
        {"type": "EMPLOYEE", "id": 42, "companyId": 94, "charsLeftOut": 0}])
    assignment = decide_assignment(content, lambda _id: None)
    assert not assignment.has_change


def test_assign_remitter_can_be_disabled():
    content = _identify("EMPLOYEE", [
        {"type": "EMPLOYEE", "id": 42, "companyId": 94, "charsLeftOut": 0}])
    assignment = decide_assignment(content, _single_company,
                                   assign_remitter=False)
    assert assignment.company_id == 94
    assert assignment.remitter_id is None


def test_unknown_number_no_assignment():
    assignment = decide_assignment(_identify("NONE", []), _single_company)
    assert not assignment.has_change
    assignment = decide_assignment(_identify("TOO_SHORT", []),
                                   _single_company)
    assert not assignment.has_change


def test_inactive_employee_assigns_company_only():
    content = _identify("EMPLOYEE_INACTIVE", [
        {"type": "EMPLOYEE_INACTIVE", "id": 42, "companyId": 94,
         "charsLeftOut": 0}])
    assignment = decide_assignment(content, _single_company)
    assert assignment.company_id == 94
    assert assignment.remitter_id is None

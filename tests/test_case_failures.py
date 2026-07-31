"""A case ICOS will not hand over must cost one row, not the whole run.

Napier pulls each case as three pages, and a page that never comes back raises
out of case_bundle. That call used to sit outside the per-case guard, so one
sick case ended the run and staff got no workbook at all. Refusing problem
report pages made that reachable: before, such a page came back instantly and
quietly became a wrong row.

Several cases failing in a row is a different thing. That is the site being
down, and walking the rest of the list to discover it one retry budget at a
time helps nobody, so the run stops and builds the CRS from what it has.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import case_parser
import icos_sessions
import tasks
from icos import IcosUnavailable

KEY = '1900-01-01 TESTER, PAT Q'


def ids(count):
    return ['01311 FECR00000%d' % n for n in range(1, count + 1)]


class FakeJob:
    def __init__(self):
        self.id = 'abcdef0123456789'
        self.kind = 'crs'
        self.progress = []
        self.result = None

    def log(self, message):
        self.progress.append({'message': message})


class StubClient:
    """Serves case pages, failing whichever case ids it was told to fail."""

    logged_in = True

    def __init__(self, unavailable):
        self.unavailable = set(unavailable)
        self.asked = []

    def set_alert(self, alert):
        pass

    def case_bundle(self, case_id):
        self.asked.append(case_id)
        if case_id in self.unavailable:
            raise IcosUnavailable("Iowa Courts Online did not return this case "
                                  "after 4 minutes of retrying")
        return b'<summary>', b'<charges>', b'<financials>'

    def logoff(self):
        pass


def run(monkeypatch, case_ids, unavailable=()):
    """Drive crs_task directly; parsing and workbook building are stubbed."""
    stub = StubClient(unavailable)
    monkeypatch.setattr(icos_sessions, 'claim', lambda token: stub)
    # Alerting has its own tests, and none of these should try to send mail.
    monkeypatch.setattr(tasks.alerts, 'record', lambda *a, **k: None)
    monkeypatch.setattr(tasks.alerts, 'digest', lambda *a, **k: None)
    monkeypatch.setattr(case_parser, 'parse_case_summary',
                        lambda body, case: case.update(county='DUBUQUE'))
    monkeypatch.setattr(case_parser, 'parse_case_charges',
                        lambda body, case: case.update(charges=[]))
    monkeypatch.setattr(case_parser, 'parse_case_financials',
                        lambda body, case: case.update(financials=[],
                                                       total_due='$0.00'))
    written = []

    def fake_build(cases, name, dob, lite):
        written.extend(case['id'] for case in cases)
        return os.path.join(tasks.tmp_dir, 'stub.xlsx')

    monkeypatch.setattr(tasks, 'build_workbook', fake_build)

    job = FakeJob()
    tasks.crs_task(job, 'tok', [KEY], {KEY: list(case_ids)},
                   'TESTER, PAT Q', '01/01/1900', False)
    return job, stub, written


def test_a_case_icos_will_not_give_up_costs_one_row_not_the_run(monkeypatch):
    case_ids = ids(3)
    job, stub, written = run(monkeypatch, case_ids, unavailable=[case_ids[1]])

    assert written == [case_ids[0], case_ids[2]]
    assert job.result['failed_cases'] == [case_ids[1]]
    # The run carried on past the bad one rather than dying on it.
    assert stub.asked == case_ids


def test_the_failed_case_is_named_for_staff(monkeypatch):
    case_ids = ids(3)
    job, _, _ = run(monkeypatch, case_ids, unavailable=[case_ids[1]])
    assert any(case_ids[1] in line['message'] for line in job.progress)


def test_failures_that_are_not_consecutive_are_not_an_outage(monkeypatch):
    """Three bad cases scattered through a list is three bad cases. Only a run
    of them back to back means the site itself has gone."""
    case_ids = ids(7)
    bad = [case_ids[0], case_ids[2], case_ids[4]]
    _, stub, written = run(monkeypatch, case_ids, unavailable=bad)

    assert stub.asked == case_ids
    assert written == [case_ids[1], case_ids[3], case_ids[5], case_ids[6]]


def test_icos_going_down_mid_run_still_yields_the_cases_already_pulled(monkeypatch):
    case_ids = ids(8)
    job, stub, written = run(monkeypatch, case_ids, unavailable=case_ids[2:])

    assert written == case_ids[:2]          # the two that came back
    assert stub.asked == case_ids[:5]       # stopped after three in a row
    assert job.result['failed_cases'] == case_ids[2:]   # none quietly dropped
    assert any('stopped responding' in line['message'] for line in job.progress)


def test_a_run_where_no_case_comes_back_still_fails(monkeypatch):
    """A partial workbook beats an error page, but an empty one does not."""
    case_ids = ids(4)
    with pytest.raises(ValueError):
        run(monkeypatch, case_ids, unavailable=case_ids)

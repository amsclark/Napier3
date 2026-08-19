"""Stopping a run that is already going.

A clinic list can sit four minutes on one case Iowa Courts will not give up, so
there has to be a way out. Killing the thread is not it: the ESA account Iowa
Legal Aid shares stays locked for about a quarter of an hour by a session
nobody released. So the run is asked to stop, stops at its next check, and
unwinds through the same finally that logs off.

Every name here is invented and every case number is 00000-shaped. The
repository is public and a real Iowa case number attached to a real name is a
person.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['NAPIER_DISABLE_BACKGROUND'] = '1'

import app as app_module
import icos_sessions
import jobs
import tasks
from icos import STOPPED_MESSAGE, IcosStopped

JANE = ['00000 FECR000000', '00000 SRCR000000']
JOHN = ['00000 SMCR000000']


def pick(name, case_ids, last):
    return {'def_name': name, 'def_dob': '01/01/1900', 'case_ids': case_ids,
            'person': {'first': 'Pat', 'middle': '', 'last': last}}


class StubClient:
    """Cancels the job partway through, standing in for the staffer's button."""

    logged_in = True

    def __init__(self, cancel_on=None):
        self.cancel_on = cancel_on      # the case id they give up on
        self.job = None
        self.pulled = []
        self.searched = []
        self.logged_off = False
        self.should_stop = lambda: False

    def set_alert(self, alert):
        pass

    def set_log(self, log):
        pass

    def set_stop_check(self, should_stop):
        self.should_stop = should_stop

    def search(self, first, middle, last):
        self.searched.append(last)
        return b'<html></html>'

    def case_bundle(self, case_id):
        self.pulled.append(case_id)
        if case_id == self.cancel_on:
            self.job.cancel()
        return b'<summary>', b'<charges>', b'<financials>'

    def logoff(self):
        self.logged_off = True


@pytest.fixture(autouse=True)
def fake_icos(monkeypatch, tmp_path):
    monkeypatch.setattr(tasks.case_parser, 'parse_case_summary',
                        lambda html, case: case.update(county='DUBUQUE'))
    monkeypatch.setattr(tasks.case_parser, 'parse_case_charges',
                        lambda html, case: case.update(charges=[]))
    monkeypatch.setattr(tasks.case_parser, 'parse_case_financials',
                        lambda html, case: case.update(financials=[],
                                                       total_due='$0.00'))
    monkeypatch.setattr(tasks, 'build_workbook',
                        lambda cases, name, dob, lite, failed=(), filed_as=None: (str(tmp_path / 'w.xlsx'), {}, {'balance': '$0.00', 'monthly': None, 'months': 12}, []))
    yield


def run_until_cancelled(monkeypatch, picks, cancel_on):
    job = jobs.Job('batch_crs')
    stub = StubClient(cancel_on=cancel_on)
    stub.job = job
    monkeypatch.setattr(icos_sessions, 'claim', lambda token: stub)
    with pytest.raises(IcosStopped) as caught:
        tasks.batch_crs_task(job, 'tok', picks, False)
    return job, stub, caught.value


class TestStoppingTheRun:
    def test_it_stops_pulling_cases(self, monkeypatch):
        """The whole point. Before this the only button on the page logged the
        browser out and left the run going."""
        _, stub, _ = run_until_cancelled(
            monkeypatch, [pick('DOE, JANE', JANE, 'Doe')], JANE[0])
        assert stub.pulled == [JANE[0]]

    def test_it_does_not_carry_on_to_the_next_client(self, monkeypatch):
        """A stop is an IcosError so it unwinds like one, which means every
        handler that drops a case or a name has to let it past. Miss one and
        stopping a run reads as a single bad case and the list carries on."""
        _, stub, _ = run_until_cancelled(
            monkeypatch,
            [pick('DOE, JANE', JANE, 'Doe'), pick('ROE, JOHN', JOHN, 'Roe')],
            JANE[0])
        assert stub.pulled == [JANE[0]]
        assert stub.searched == ['Doe']       # the second client never started

    def test_it_still_logs_the_shared_account_off(self, monkeypatch):
        """The reason for asking rather than killing. An ESA session nobody
        released locks the account Iowa Legal Aid shares for about fifteen
        minutes, so the next person to search cannot."""
        _, stub, _ = run_until_cancelled(
            monkeypatch, [pick('DOE, JANE', JANE, 'Doe')], JANE[0])
        assert stub.logged_off is True

    def test_it_says_the_account_is_free(self, monkeypatch):
        _, _, stopped = run_until_cancelled(
            monkeypatch, [pick('DOE, JANE', JANE, 'Doe')], JANE[0])
        assert stopped.message == STOPPED_MESSAGE
        assert 'signed out' in stopped.message

    def test_a_stop_does_not_page_anybody(self):
        """jobs.py emails about anything that raises without a .message,
        because that is a bug staff cannot act on. A staffer stopping their own
        run is neither, so it has to arrive carrying its own wording."""
        def stop_at_once(job):
            raise IcosStopped(STOPPED_MESSAGE)

        job = jobs.start('batch_crs', stop_at_once)
        for _ in range(500):
            if job.status in (jobs.DONE, jobs.FAILED):
                break
            import time
            time.sleep(0.01)
        assert job.status == jobs.FAILED
        assert job.error == STOPPED_MESSAGE


def a_running_job():
    """A job the routes can actually find.

    jobs.start is what puts a job in the registry, and it also runs it. These
    tests want one sitting there, so it goes in by hand. Skipping this makes
    every route below answer 410 for the wrong reason, and the test that says
    another browser cannot stop a run would pass without ever reaching the
    check it is about.
    """
    job = jobs.Job('batch_crs')
    job.status = jobs.RUNNING
    with jobs._jobs_lock:
        jobs._jobs[job.id] = job
    return job


class TestTheButton:
    @pytest.fixture
    def client(self):
        app_module.app.config['TESTING'] = True
        app_module.app.secret_key = 'test'
        return app_module.app.test_client()

    def test_it_asks_the_run_to_stop(self, client):
        job = a_running_job()
        with client.session_transaction() as browser:
            browser['job_ids'] = [job.id]
        assert client.post('/job/%s/cancel' % job.id).status_code == 200
        assert job.cancelled is True

    def test_another_browser_cannot_stop_it(self, client):
        job = a_running_job()
        assert client.post('/job/%s/cancel' % job.id).status_code == 410
        assert job.cancelled is False

    def test_a_link_cannot_stop_it(self, client):
        """POST because a link gets followed by things that are not the
        staffer, and the run this ends is holding an ESA account."""
        job = a_running_job()
        with client.session_transaction() as browser:
            browser['job_ids'] = [job.id]
        assert client.get('/job/%s/cancel' % job.id).status_code == 405
        assert job.cancelled is False

    def test_starting_over_stops_what_is_running(self, client):
        """The button redirects here afterwards, and staff reach it on their
        own. Either way the old run must not keep the shared account while the
        new one queues behind it."""
        job = a_running_job()
        with client.session_transaction() as browser:
            browser['job_ids'] = [job.id]
        assert client.get('/logout').status_code == 302
        assert job.cancelled is True

    def test_starting_over_leaves_a_finished_run_alone(self, client):
        job = a_running_job()
        job.status = jobs.DONE
        with client.session_transaction() as browser:
            browser['job_ids'] = [job.id]
        client.get('/logout')
        assert job.cancelled is False

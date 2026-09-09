"""A search nobody is waiting for must not take the shared Iowa Courts account.

From 9 September 2026. A staffer's search hit a locked ILA account, so she
started another, and another. Each new one got its own job and its own progress
page; the two she left behind kept polling ESA for the full fifteen minute
budget with nobody watching. One of them signed in at the end of it, took the
account for a search that was already answered, and held it until the idle
reaper let go ten minutes later. Other staff were locked out for that.

Two things were missing. Starting a search did not stop the caller's earlier
one, although signing out already did exactly that. And a run waiting for an
account had no way to notice that its progress page had gone.

The give-up is deliberately narrow: it ends the wait for an account and nothing
else. A run that has begun pulling cases holds work that cannot be got back
without pulling it again, so it finishes whether or not anybody is watching,
and the uncollected-workbook alert hands the file over afterwards.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['NAPIER_DISABLE_BACKGROUND'] = '1'

import accounts
import flask
import icos
import jobs
from icos import ABANDONED_MESSAGE, IcosAccountLocked, IcosClient, IcosStopped
from reader import OK, FetchResult
from test_accounts import CONCURRENT_PAGE, FakeReader, LOGIN_OK

import app as app_module


class Clock:
    def __init__(self):
        self.now = 0.0
        self.slept = []

    def sleep(self, seconds):
        self.now += seconds
        self.slept.append(seconds)

    def monotonic(self):
        return self.now


def build(watched, budget=16 * 60, pages=40):
    """A client that meets a locked account, told whether anybody is waiting."""
    clock = Clock()
    alerted = []
    client = IcosClient(log=lambda m: None,
                        reader=FakeReader([FetchResult(OK, LOGIN_OK)]
                                          + [FetchResult(OK, CONCURRENT_PAGE)] * pages),
                        sleep=clock.sleep, monotonic=clock.monotonic,
                        alert=lambda failure, **fields: alerted.append(failure),
                        concurrent_budget_seconds=budget)
    client.set_wanted_check(lambda: watched)
    return client, clock, alerted


# -- the wait for a locked account ----------------------------------------

class TestGivingUpTheWait:
    def test_an_abandoned_wait_ends_at_the_first_check(self):
        client, clock, _ = build(watched=False)
        with pytest.raises(IcosStopped) as caught:
            client.login('ILA07', 'secret')
        assert caught.value.message == ABANDONED_MESSAGE
        assert clock.slept == [icos.CONCURRENT_INTERVAL]

    def test_a_watched_wait_runs_the_whole_budget(self):
        """The partition. Somebody at their desk still gets the fifteen minutes
        ESA needs to release a lock held outside Napier."""
        client, clock, alerted = build(watched=True, budget=300)
        with pytest.raises(IcosAccountLocked):
            client.login('ILA07', 'secret')
        assert sum(clock.slept) >= 300
        assert alerted == [icos.alerts.CONCURRENT_EXHAUSTED]

    def test_nobody_is_emailed_about_a_search_nobody_wanted(self):
        """IcosStopped carries a message, so jobs.start fails the job quietly.
        An abandoned search letting go is housekeeping, not a failure."""
        client, _, alerted = build(watched=False)
        with pytest.raises(IcosStopped):
            client.login('ILA07', 'secret')
        assert alerted == []

    def test_it_also_ends_the_wait_in_the_queue(self):
        """The other wait. This one is behind Napier's own earlier runs rather
        than behind ESA, and it costs the same account."""
        accounts.take_ticket('ILA07')           # somebody who asked first
        client, clock, _ = build(watched=False)
        with pytest.raises(IcosStopped) as caught:
            client.login('ILA07', 'secret')
        assert caught.value.message == ABANDONED_MESSAGE
        assert clock.slept == []

    def test_the_account_is_left_free(self):
        accounts.take_ticket('ILA07')
        client, _, _ = build(watched=False)
        with pytest.raises(IcosStopped):
            client.login('ILA07', 'secret')
        assert accounts.describe('ILA07') is None


# -- how a job knows anybody is there -------------------------------------

class TestKnowingSomebodyIsThere:
    def test_a_new_job_is_watched(self):
        """It has the full grace period before its first poll, rather than
        being abandoned in the instant it was made."""
        assert jobs.Job('search').is_watched() is True

    def test_a_job_nobody_has_asked_about_goes_unwatched(self):
        job = jobs.Job('search')
        assert job.is_watched(now=job.created_at + jobs.UNWATCHED_AFTER + 1) is False

    def test_a_poll_keeps_it_watched(self):
        job = jobs.Job('search')
        later = job.created_at + jobs.UNWATCHED_AFTER + 1
        job.watched(now=later)
        assert job.is_watched(now=later + 1) is True


# -- starting another search stops the one before it ----------------------

def a_running_job(kind='search'):
    job = jobs.Job(kind)
    job.status = jobs.RUNNING
    with jobs._jobs_lock:
        jobs._jobs[job.id] = job
    return job


class TestStartingAnotherSearch:
    @pytest.fixture(autouse=True)
    def _configured(self):
        app_module.app.config['TESTING'] = True
        app_module.app.secret_key = 'test'

    def test_it_stops_the_run_this_browser_left_behind(self):
        job = a_running_job()
        with app_module.app.test_request_context('/'):
            flask.session['job_ids'] = [job.id]
            assert app_module.stop_earlier_runs() == 1
        assert job.cancelled is True

    def test_it_leaves_a_finished_run_alone(self):
        job = a_running_job()
        job.status = jobs.DONE
        with app_module.app.test_request_context('/'):
            flask.session['job_ids'] = [job.id]
            assert app_module.stop_earlier_runs() == 0
        assert job.cancelled is False

    def test_it_leaves_another_browser_alone(self):
        job = a_running_job()
        with app_module.app.test_request_context('/'):
            flask.session['job_ids'] = []
            assert app_module.stop_earlier_runs() == 0
        assert job.cancelled is False

    def test_an_uncollected_workbook_is_still_offered_afterwards(self):
        """Unlike signing out. Starting another search is not a reason to
        forget a file the staffer has not taken yet."""
        job = a_running_job('crs')
        with app_module.app.test_request_context('/'):
            flask.session['job_ids'] = [job.id]
            app_module.stop_earlier_runs()
            assert flask.session['job_ids'] == [job.id]


class TestThePollIsTheEvidence:
    def test_asking_how_it_is_getting_on_counts_as_waiting(self):
        app_module.app.config['TESTING'] = True
        app_module.app.secret_key = 'test'
        job = a_running_job()
        job.watched_at = job.created_at - 3600
        client = app_module.app.test_client()
        with client.session_transaction() as browser:
            browser['job_ids'] = [job.id]
        assert client.get('/job/%s' % job.id).status_code == 200
        assert job.is_watched() is True

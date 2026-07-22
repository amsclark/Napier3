"""Job engine and ICOS session store."""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import icos_sessions
import jobs
from icos import IcosBadCredentials


def wait_for(job, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if job.status in (jobs.DONE, jobs.FAILED):
            return job
        time.sleep(0.01)
    raise AssertionError("job did not finish: %s" % job.status)


def test_job_reports_progress_and_result():
    def task(job):
        job.log("step one")
        job.log("step two")
        job.result = {"ok": True}
        return "/somewhere"

    job = wait_for(jobs.start('test', task))
    assert job.status == jobs.DONE
    assert job.result == {"ok": True}
    assert job.next_url == "/somewhere"
    state = job.to_dict()
    assert state["progress"] == ["step one", "step two"]
    assert state["message"] == "step two"
    assert state["done"] is True


def test_expected_failures_keep_their_message():
    def task(job):
        raise IcosBadCredentials("Iowa Courts Online did not accept that user ID.")

    job = wait_for(jobs.start('test', task))
    assert job.status == jobs.FAILED
    assert job.error == "Iowa Courts Online did not accept that user ID."


def test_unexpected_failures_do_not_leak_internals():
    def task(job):
        raise KeyError("cookies")

    job = wait_for(jobs.start('test', task))
    assert job.status == jobs.FAILED
    assert "cookies" not in job.error
    assert "went wrong" in job.error


def test_janitor_evicts_only_stale_jobs():
    fresh = wait_for(jobs.start('test', lambda job: None))
    stale = wait_for(jobs.start('test', lambda job: None))
    stale.updated_at = time.time() - jobs.RETENTION_SECONDS - 1

    jobs._janitor_pass()

    assert jobs.get(fresh.id) is not None
    assert jobs.get(stale.id) is None


class FakeClient:
    def __init__(self):
        self.logged_off = False

    def logoff(self):
        self.logged_off = True


def test_session_round_trip():
    client = FakeClient()
    token = icos_sessions.put(client)
    assert icos_sessions.get(token) is client
    assert icos_sessions.get("nonsense") is None


def test_close_logs_off():
    client = FakeClient()
    token = icos_sessions.put(client)
    icos_sessions.close(token)
    assert client.logged_off is True
    assert icos_sessions.get(token) is None


def test_close_tolerates_unknown_token():
    icos_sessions.close(None)
    icos_sessions.close("nonsense")


def test_reaper_logs_off_abandoned_sessions():
    # An abandoned results page used to leave the ESA account locked for about
    # fifteen minutes, which is what made shared accounts collide.
    abandoned = FakeClient()
    active = FakeClient()
    stale_token = icos_sessions.put(abandoned)
    active_token = icos_sessions.put(active)
    icos_sessions._sessions[stale_token]["last_used"] = \
        time.time() - icos_sessions.IDLE_TIMEOUT - 1

    icos_sessions._reap()

    assert abandoned.logged_off is True
    assert active.logged_off is False
    assert icos_sessions.get(stale_token) is None
    assert icos_sessions.get(active_token) is active
    icos_sessions.close(active_token)


def test_activity_defers_the_reaper():
    client = FakeClient()
    token = icos_sessions.put(client)
    icos_sessions._sessions[token]["last_used"] = \
        time.time() - icos_sessions.IDLE_TIMEOUT - 1
    icos_sessions.get(token)  # staff came back

    icos_sessions._reap()

    assert client.logged_off is False
    icos_sessions.close(token)

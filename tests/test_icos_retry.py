"""Retry behaviour: the three failure modes need three different responses."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import icos
from icos import IcosAccountLocked, IcosBadCredentials, IcosClient, IcosUnavailable
from reader import EMPTY, OK, TIMEOUT, FetchResult

LOGIN_OK = b"x" * 28000
CONCURRENT_PAGE = b"<html>Concurrent Login Error: A user is already logged on</html>"
BAD_CREDS_PAGE = b"<html>The userID or password could not be validated</html>"
RESULTS_PAGE = b"<html><table>results</table></html>"


class FakeReader:
    """Replays a scripted sequence of outcomes and records what was asked for."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def fetch_once(self, url, data=None, timeout=8):
        name = url.rsplit("/", 1)[-1].split("?")[0]
        self.calls.append(name)
        outcome = self.script.pop(0) if self.script else FetchResult(OK, RESULTS_PAGE)
        return outcome

    def init_request(self):
        return "https://icos/ESAWebApp/ESALogin.jsp", None

    def login_request(self, username, password):
        assert password, "login must actually carry the password"
        return "https://icos/ESAWebApp/EUACustomLoginServlet", "userid=" + username

    def logoff_request(self):
        return "https://icos/ESAWebApp/EPALogout", "logoffButton=Logoff"

    def search_request(self, first, middle, last):
        return "https://icos/ESAWebApp/TrialCaseSearchResultServlet", "last=" + last

    def case_summary_request(self, case_id):
        return "https://icos/ESAWebApp/TViewCaseCivil?caseid=" + case_id, None

    def case_charges_request(self):
        return "https://icos/ESAWebApp/TViewCharges", None

    def case_financials_request(self):
        return "https://icos/ESAWebApp/TViewFinancials", None


class Clock:
    """Virtual time: sleeps advance the clock instead of blocking."""

    def __init__(self):
        self.now = 0.0
        self.slept = []

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def monotonic(self):
        return self.now


def build(script, **kwargs):
    clock = Clock()
    messages = []
    client = IcosClient(log=messages.append, reader=FakeReader(script),
                        sleep=clock.sleep, monotonic=clock.monotonic,
                        budget_seconds=kwargs.pop('budget_seconds', 45 * 60),
                        concurrent_budget_seconds=kwargs.pop('concurrent_budget_seconds',
                                                             16 * 60))
    return client, clock, messages


def test_retries_through_a_cold_stall_then_succeeds():
    client, clock, messages = build([
        FetchResult(TIMEOUT), FetchResult(TIMEOUT), FetchResult(OK, RESULTS_PAGE),
    ])
    assert client.search("PAT", "", "TESTER") == RESULTS_PAGE
    assert clock.slept == [2, 5]  # backoff, not a tight loop
    assert any("retrying" in m.lower() for m in messages)


def test_empty_response_is_retried_not_surfaced():
    client, _, _ = build([
        FetchResult(EMPTY), FetchResult(EMPTY), FetchResult(OK, RESULTS_PAGE),
    ])
    # The old code gave up after two empties and showed staff an error page.
    assert client.search("PAT", "", "TESTER") == RESULTS_PAGE


def test_gives_up_inside_the_budget():
    client, clock, _ = build([FetchResult(TIMEOUT)] * 200, budget_seconds=120)
    with pytest.raises(IcosUnavailable) as excinfo:
        client.search("PAT", "", "TESTER")
    assert clock.now <= 120
    assert "try again later" in excinfo.value.message.lower()


def test_outage_message_escalates():
    client, _, messages = build(
        [FetchResult(TIMEOUT)] * 12 + [FetchResult(OK, RESULTS_PAGE)])
    client.search("PAT", "", "TESTER")
    assert any("outage" in m.lower() and "saved" in m.lower() for m in messages)


def test_bad_password_fails_immediately():
    client, clock, _ = build([
        FetchResult(OK, LOGIN_OK),          # ESALogin.jsp
        FetchResult(OK, BAD_CREDS_PAGE),    # login attempt
    ])
    with pytest.raises(IcosBadCredentials):
        client.login("ILATEST", "wrong")
    assert clock.slept == []  # no point retrying a wrong password


def test_concurrent_login_waits_and_then_succeeds():
    client, clock, messages = build([
        FetchResult(OK, LOGIN_OK),
        FetchResult(OK, CONCURRENT_PAGE),
        FetchResult(OK, CONCURRENT_PAGE),
        FetchResult(OK, LOGIN_OK),
    ])
    client.login("ILATEST", "secret")
    assert client.logged_in
    assert clock.slept == [icos.CONCURRENT_INTERVAL] * 2
    assert any("already logged in" in m.lower() for m in messages)


def test_concurrent_login_message_is_not_repeated():
    client, _, messages = build(
        [FetchResult(OK, LOGIN_OK)] + [FetchResult(OK, CONCURRENT_PAGE)] * 3
        + [FetchResult(OK, LOGIN_OK)])
    client.login("ILATEST", "secret")
    assert sum("already logged in" in m.lower() for m in messages) == 1


def test_concurrent_login_gives_up_after_the_lock_window():
    client, clock, _ = build(
        [FetchResult(OK, LOGIN_OK)] + [FetchResult(OK, CONCURRENT_PAGE)] * 100,
        concurrent_budget_seconds=16 * 60)
    with pytest.raises(IcosAccountLocked) as excinfo:
        client.login("ILATEST", "secret")
    assert clock.now <= 16 * 60
    assert "your own Iowa Courts account" in excinfo.value.message


def test_case_pages_are_fetched_in_icos_order():
    client, _, _ = build([])
    client.case_bundle("01311 FECR000000")
    assert client.reader.calls == ["TViewCaseCivil", "TViewCharges", "TViewFinancials"]


def test_logoff_is_silent_when_never_logged_in():
    client, _, _ = build([])
    client.logoff()
    assert client.reader.calls == []


def test_logoff_releases_the_session():
    client, _, _ = build([FetchResult(OK, LOGIN_OK), FetchResult(OK, LOGIN_OK)])
    client.login("ILATEST", "secret")
    client.logoff()
    assert "EPALogout" in client.reader.calls
    assert client.logged_in is False


def test_logoff_never_raises():
    client, _, _ = build([FetchResult(OK, LOGIN_OK), FetchResult(OK, LOGIN_OK)])
    client.login("ILATEST", "secret")

    def boom(*args, **kwargs):
        raise RuntimeError("network gone")

    client.reader.fetch_once = boom
    client.logoff()  # cleanup paths must not explode
    assert client.logged_in is False


def test_no_credentials_are_logged():
    client, _, messages = build([FetchResult(OK, LOGIN_OK), FetchResult(OK, LOGIN_OK)])
    client.login("ILATEST", "hunter2")
    assert not any("hunter2" in m for m in messages)

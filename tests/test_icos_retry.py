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

CASE_ID = "01311  FECR000000"
CASE_PAGE = (b"<html>Trial Court Case Summary Title:&nbsp;STATE VS TESTER, PAT Q "
             b"Case: 01311  FECR000000 (SYNTHETIC) Disposition Status</html>")

# The shape of the real thing, with the wording that identifies it. ICOS serves
# this with HTTP 200, under the heading of whatever case was selected last.
PROBLEM_REPORT_PAGE = (
    b"<html>Trial Court Case Summary Title:&nbsp;STATE VS TESTER, SAM "
    b"Case: 01311  FECR111111 (SYNTHETIC) Problem Report: There was a "
    b"communication problem. Possible Cause: The Web server may be too busy. "
    b"No Disposition records were found.</html>")

# What ICOS actually served for charges and financials on 45 of 45 cases in the
# July capture when it was degrading. It carries no problem report wording at
# all, so the marker check waves it through. It wears the heading of whatever
# case was selected last, it never echoes the case that was asked for, and it
# lists nothing. Accepting it writes the case down with no charges, which
# reports a conviction as a non-conviction.
STUB_CASE_PAGE = (
    b"<html>Trial Court Case Summary Title:&nbsp;STATE VS TESTER, SAM "
    b"Case: 01311  FECR111111 (SYNTHETIC) Charges Financial Summary</html>")


class FakeReader:
    """Replays a scripted sequence of outcomes and records what was asked for."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.credentials = []

    def fetch_once(self, url, data=None, timeout=8):
        name = url.rsplit("/", 1)[-1].split("?")[0]
        self.calls.append(name)
        outcome = self.script.pop(0) if self.script else FetchResult(OK, RESULTS_PAGE)
        return outcome

    def init_request(self):
        return "https://icos/ESAWebApp/ESALogin.jsp", None

    def login_request(self, username, password):
        assert password, "login must actually carry the password"
        self.credentials.append((username, password))
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
                                                             16 * 60),
                        case_budget_seconds=kwargs.pop('case_budget_seconds', 4 * 60))
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


def test_unmarked_rejection_is_not_mistaken_for_success():
    # ESA has answered a bad user ID with a short page carrying no rejection
    # message. Believing that login would send the search into a full retry
    # budget against a session that was never established.
    client, _, _ = build([
        FetchResult(OK, LOGIN_OK),
        FetchResult(OK, b"<html>ESA Login</html>"),
    ])
    with pytest.raises(IcosBadCredentials) as excinfo:
        client.login("ILA99", "dummy")
    said = excinfo.value.message.lower()
    # ESA did not say why, so neither should we. Naming the password sends
    # staff off to reset one that was never wrong.
    assert "check the user id and password" in said
    assert "still be signed in" in said
    assert client.logged_in is False


def test_a_password_with_a_stray_space_gets_one_more_try():
    """Phone keyboards and autofill add a trailing space, and ESA answers that
    with the same unmarked page it uses for a wrong password. Staff were being
    sent to reset a password that worked."""
    client, _, _ = build([
        FetchResult(OK, LOGIN_OK),                # ESALogin.jsp
        FetchResult(OK, b"<html>ESA Login</html>"),  # rejected as typed
        FetchResult(OK, LOGIN_OK),                # accepted once trimmed
    ])
    client.login("ILA99", "correcthorse ")
    assert client.logged_in is True
    assert client.reader.credentials == [("ILA99", "correcthorse "),
                                         ("ILA99", "correcthorse")]


def test_the_trimmed_retry_happens_only_once():
    client, _, _ = build([
        FetchResult(OK, LOGIN_OK),
        FetchResult(OK, b"<html>ESA Login</html>"),
        FetchResult(OK, b"<html>ESA Login</html>"),
    ])
    with pytest.raises(IcosBadCredentials):
        client.login("ILA99", "wrong ")
    assert len(client.reader.credentials) == 2


def test_a_clean_password_is_not_retried():
    client, _, _ = build([
        FetchResult(OK, LOGIN_OK),
        FetchResult(OK, b"<html>ESA Login</html>"),
    ])
    with pytest.raises(IcosBadCredentials):
        client.login("ILA99", "wrong")
    assert len(client.reader.credentials) == 1


def test_the_login_page_text_is_logged_without_the_user_id(capsys):
    """Only the length of this page was ever recorded, which is why a wrong
    user ID and an account still signed in elsewhere look identical."""
    client, _, _ = build([
        FetchResult(OK, LOGIN_OK),
        FetchResult(OK, b"<html><body>Sign on failed for ILA99</body></html>"),
    ])
    with pytest.raises(IcosBadCredentials):
        client.login("ILA99", "dummy")
    logged = capsys.readouterr().out
    assert "Sign on failed for" in logged
    assert "ILA99" not in logged


def test_full_size_login_page_is_accepted():
    client, _, _ = build([FetchResult(OK, LOGIN_OK), FetchResult(OK, LOGIN_OK)])
    client.login("ILATEST", "secret")
    assert client.logged_in is True


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
    client, _, _ = build([FetchResult(OK, CASE_PAGE)] * 3)
    client.case_bundle(CASE_ID)
    assert client.reader.calls == ["TViewCaseCivil", "TViewCharges", "TViewFinancials"]


def test_a_problem_report_page_is_never_taken_for_a_case():
    """ICOS returns this with HTTP 200 when its data source is unreachable.

    Seen live in July 2026. The page carries the heading of whichever case was
    selected last and lists no charges and no money, so it parses cleanly as a
    civil case with nothing owed. Accepting it puts a wrong row in the CRS,
    which is worse than the case failing, so it is retried like any other bad
    response and eventually surfaces as an outage.
    """
    client, _, _ = build([FetchResult(OK, PROBLEM_REPORT_PAGE)] * 200,
                         case_budget_seconds=120)
    with pytest.raises(IcosUnavailable):
        client.case_bundle(CASE_ID)


def test_a_problem_report_at_login_is_not_a_bad_password():
    """It is 3404 bytes, well under MIN_SIGNED_IN_BYTES, so without a check of
    its own it lands on the size fallback and staff are told to check
    credentials that were never wrong."""
    client, _, _ = build([FetchResult(OK, PROBLEM_REPORT_PAGE)] * 200,
                         budget_seconds=120)
    with pytest.raises(IcosUnavailable):
        client.login("ILA00", "password")


def test_a_problem_report_on_a_search_is_not_an_empty_record():
    """The same page can come back for a search, where it has no rows and so
    parses as a search that matched nobody. "No Iowa record" is the answer a
    CRS is built to give, and staff have no way to tell a real one from this,
    so it must fail loudly instead of quietly clearing somebody."""
    client, _, _ = build([FetchResult(OK, PROBLEM_REPORT_PAGE)] * 200,
                         budget_seconds=120)
    with pytest.raises(IcosUnavailable):
        client.search("PAT", "", "TESTER")


def test_a_search_problem_report_that_clears_is_just_a_retry():
    client, _, _ = build([FetchResult(OK, PROBLEM_REPORT_PAGE),
                          FetchResult(OK, RESULTS_PAGE)])
    assert client.search("PAT", "", "TESTER") == RESULTS_PAGE


def test_a_problem_report_that_clears_is_just_a_retry():
    client, _, _ = build([FetchResult(OK, PROBLEM_REPORT_PAGE),
                          FetchResult(OK, CASE_PAGE),       # summary, second go
                          FetchResult(OK, CASE_PAGE),       # charges
                          FetchResult(OK, CASE_PAGE)])      # financials
    summary, charges, financials = client.case_bundle(CASE_ID)
    assert summary == CASE_PAGE


def test_the_page_for_a_different_case_is_rejected():
    """ICOS keys the case views off the last selection, so the wrong case can
    come back looking entirely healthy. Reporting one case's charges under
    another case's number is the quietest way to get a CRS wrong."""
    other = CASE_PAGE.replace(b"FECR000000", b"FECR999999")
    client, _, _ = build([FetchResult(OK, other)] * 200, case_budget_seconds=120)
    with pytest.raises(IcosUnavailable):
        client.case_bundle(CASE_ID)


def test_an_empty_stub_is_not_a_case_with_no_charges():
    """The summary can arrive healthy and ICOS degrade before the charges
    fetch. The stub that comes back carries no problem report wording, so only
    the case number tells it apart from a real case that genuinely has no
    charges, and those two mean opposite things on a criminal record."""
    client, _, _ = build([FetchResult(OK, CASE_PAGE)] +
                         [FetchResult(OK, STUB_CASE_PAGE)] * 200,
                         case_budget_seconds=120)
    with pytest.raises(IcosUnavailable):
        client.case_bundle(CASE_ID)


def test_financials_must_also_prove_which_case_they_belong_to():
    """Court debt decides whether a record can be expunged at all, so money
    from the wrong case is as bad as charges from the wrong case."""
    client, _, _ = build([FetchResult(OK, CASE_PAGE),
                          FetchResult(OK, CASE_PAGE)] +
                         [FetchResult(OK, STUB_CASE_PAGE)] * 200,
                         case_budget_seconds=120)
    with pytest.raises(IcosUnavailable):
        client.case_bundle(CASE_ID)


def test_a_stub_that_clears_is_just_a_retry():
    client, _, _ = build([FetchResult(OK, CASE_PAGE),
                          FetchResult(OK, STUB_CASE_PAGE),
                          FetchResult(OK, CASE_PAGE),
                          FetchResult(OK, CASE_PAGE)])
    summary, charges, financials = client.case_bundle(CASE_ID)
    assert charges == CASE_PAGE and financials == CASE_PAGE


def test_a_stuck_case_gives_up_long_before_a_stuck_search():
    """A search is the whole job and worth waiting out. A case is one row of
    many, with staff watching a progress bar and the rest of the list still to
    pull, so the same dead script must cost far less time."""
    case_client, case_clock, _ = build([FetchResult(TIMEOUT)] * 500)
    with pytest.raises(IcosUnavailable):
        case_client.case_bundle(CASE_ID)

    search_client, search_clock, _ = build([FetchResult(TIMEOUT)] * 500)
    with pytest.raises(IcosUnavailable):
        search_client.search("PAT", "", "TESTER")

    assert case_clock.now < search_clock.now / 5
    assert case_clock.now <= 4 * 60


def test_a_search_keeps_its_own_budget():
    """The case budget must not have quietly shortened the search too."""
    client, clock, _ = build([FetchResult(TIMEOUT)] * 500, budget_seconds=30 * 60)
    with pytest.raises(IcosUnavailable):
        client.search("PAT", "", "TESTER")
    assert clock.now > 20 * 60


def test_icos_spacing_of_a_case_number_does_not_matter():
    """The search lists '01311  FECR000000'; the case page spaces it its own
    way. Napier must not treat that as the wrong case."""
    respaced = CASE_PAGE.replace(b"01311  FECR000000", b"01311\r\n\tFECR000000")
    client, _, _ = build([FetchResult(OK, respaced)] * 3)
    summary, _, _ = client.case_bundle(CASE_ID)
    assert summary == respaced


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

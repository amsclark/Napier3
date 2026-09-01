"""Retry behaviour: the three failure modes need three different responses."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import icos
from icos import (IcosAccountLocked, IcosBadCredentials, IcosClient,
                  IcosStopped, IcosUnavailable)
import alerts
from reader import EMPTY, ERROR, OK, TIMEOUT, FetchResult

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
        self.urls = []
        self.credentials = []

    def fetch_once(self, url, data=None, timeout=8):
        name = url.rsplit("/", 1)[-1].split("?")[0]
        self.calls.append(name)
        self.urls.append(url)
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
# -- stopping ---------------------------------------------------------------


def test_a_stop_ends_the_wait_rather_than_riding_out_the_budget():
    """The reason the check lives in here and not only between cases: the whole
    reason somebody reaches for stop is that a wait is in progress. A stalled
    search is waited out for forty-five minutes and a stalled case for four,
    which is far too long to make a staffer sit through once they have decided
    to stop."""
    client, clock, _ = build([FetchResult(TIMEOUT)] * 200)
    asked = []

    def should_stop():
        asked.append(1)
        return len(asked) > 3      # they give up three attempts in

    client.set_stop_check(should_stop)
    with pytest.raises(IcosStopped):
        client.search("PAT", "", "TESTER")
    assert clock.now < 60          # seconds, against a budget of forty-five minutes


def test_a_stopped_run_can_still_log_off():
    """The property the whole design rests on. logoff goes straight at
    fetch_once instead of through the retry loop, so the stop check can never
    stand between a cancelled run and releasing the account Iowa Legal Aid
    shares. Route logoff through _retry and this fails."""
    client, _, _ = build([FetchResult(OK, LOGIN_OK), FetchResult(OK, LOGIN_OK)])
    client.login("ILATEST", "secret")
    client.set_stop_check(lambda: True)
    client.logoff()
    assert "EPALogout" in client.reader.calls
    assert client.logged_in is False


class TestWhatTheAlertSays:
    """Which of five different things went wrong.

    Every failed attempt used to reach the inbox as "unusable response from
    ICOS" with a size field, including the ones where nothing came back at all
    and the size was 0b. Five causes, one subject line, and because
    alerts.record only emails the first of each class per run, whichever
    happened first silenced the other four.

    That is not a tidiness complaint. On 2026-08-01 a clinic batch stopped at
    1 of 67 cases and the only email about why said "unusable response, 0b",
    which is what a timeout looks like. The digest for the same run also listed
    a 3407 byte reply carrying a 200, filed under that same subject line. Which
    of the five that body was cannot be established from what was sent, and that
    is the defect: the email that would have named it had gone out about
    something else.

    A court site that is down and a session that has lost its place also look
    identical from outside, and only one of them is Iowa's fault.
    """

    def _first_alert(self, script):
        seen = []
        client, _, _ = build(script, case_budget_seconds=20)
        client.set_alert(lambda failure, **fields: seen.append((failure, fields)))
        client.logged_in = True          # alerts are held until after login
        with pytest.raises(IcosUnavailable):
            client.case_bundle(CASE_ID)
        assert seen, 'a failing case must alert at all before this proves anything'
        return seen[0]

    def test_a_timeout_is_not_called_an_unusable_response(self):
        failure, fields = self._first_alert([FetchResult(TIMEOUT)] * 50)
        assert failure == alerts.NO_ANSWER
        assert 'timeout' in fields['reason']
        # 0b is not a fact about a response that never arrived, and printing it
        # is what made this indistinguishable from a page that came back wrong.
        assert 'response size' not in fields

    def test_an_empty_body_says_so(self):
        """The live shape: ICOS answers a case request with 200 and nothing in
        it when the session never selected that case through a search."""
        failure, fields = self._first_alert([FetchResult(EMPTY)] * 50)
        assert failure == alerts.NO_ANSWER
        assert 'empty body' in fields['reason']

    def test_a_transport_error_carries_what_the_transport_said(self):
        failure, fields = self._first_alert(
            [FetchResult(ERROR, b'', '503', 0.1, 'HTTP Error 503')] * 50)
        assert failure == alerts.NO_ANSWER
        assert '503' in fields['reason']

    def test_a_problem_report_page_is_named_as_one(self):
        failure, fields = self._first_alert([FetchResult(OK, PROBLEM_REPORT_PAGE)] * 50)
        assert failure == alerts.BAD_RESPONSE
        assert 'problem report' in fields['reason']
        assert fields['response size'] != '0b'   # this one did arrive

    def test_a_page_for_the_wrong_case_is_named_as_one(self):
        """The dangerous one. It carries no problem report wording, wears some
        other case's heading and lists nothing, so accepting it writes a
        conviction down as a non-conviction."""
        failure, fields = self._first_alert([FetchResult(OK, STUB_CASE_PAGE)] * 50)
        assert failure == alerts.BAD_RESPONSE
        assert 'different case' in fields['reason']

    def test_the_five_causes_do_not_collapse_into_each_other(self):
        """The property that matters, stated as one assertion.

        alerts.record dedupes on the failure class and on nothing else, so two
        causes sharing a class means hearing about one of them and never the
        other. Distinct (class, reason) pairs are what keeps that from
        happening.
        """
        scripts = [
            [FetchResult(TIMEOUT)] * 50,
            [FetchResult(EMPTY)] * 50,
            [FetchResult(ERROR, b'', '503', 0.1, 'HTTP Error 503')] * 50,
            [FetchResult(OK, PROBLEM_REPORT_PAGE)] * 50,
            [FetchResult(OK, STUB_CASE_PAGE)] * 50,
        ]
        seen = [self._first_alert(script) for script in scripts]
        pairs = {(failure, fields['reason']) for failure, fields in seen}
        assert len(pairs) == len(scripts), pairs

    def test_the_giving_up_email_says_what_it_gave_up_on(self):
        """RETRY_EXHAUSTED used to end on "Last result: unusable response",
        which is the same sentence for all five."""
        seen = []
        client, _, _ = build([FetchResult(OK, PROBLEM_REPORT_PAGE)] * 50,
                             case_budget_seconds=20)
        client.set_alert(lambda failure, **fields: seen.append((failure, fields)))
        client.logged_in = True
        with pytest.raises(IcosUnavailable):
            client.case_bundle(CASE_ID)
        gave_up = [fields for failure, fields in seen
                   if failure == alerts.RETRY_EXHAUSTED]
        assert gave_up, 'a case that ran out its budget must say so'
        assert 'problem report' in gave_up[0]['note']


class TestWhatTheRunIsToldToBelieve:
    """Whether ICOS said it was down, or merely failed to answer.

    The run's outage counter stops a clinic list after six refused cases,
    which is about twenty three minutes, and that price buys the difference
    between one client's sealed cases and a dead court site. When ICOS has
    already answered that question by serving its problem report page, the
    price is not owed, so the flag has to reach the caller. It travels on the
    exception because that is the only thing the caller gets.
    """

    def _bundle_failure(self, script):
        client, _, _ = build(script, case_budget_seconds=20)
        client.logged_in = True
        with pytest.raises(IcosUnavailable) as caught:
            client.case_bundle(CASE_ID)
        return caught.value

    def test_a_problem_report_page_reaches_the_caller_as_one(self):
        failure = self._bundle_failure([FetchResult(OK, PROBLEM_REPORT_PAGE)] * 50)
        assert failure.court_site_down is True

    def test_a_timeout_does_not_claim_ICOS_said_anything(self):
        """It is the same outage from the outside and a different one to
        anybody diagnosing it, which is the whole reason for the flag."""
        failure = self._bundle_failure([FetchResult(TIMEOUT)] * 50)
        assert failure.court_site_down is False

    def test_nor_does_a_page_for_the_wrong_case(self):
        failure = self._bundle_failure([FetchResult(OK, STUB_CASE_PAGE)] * 50)
        assert failure.court_site_down is False

    def test_a_search_carries_it_too(self):
        """A refused name costs the 45 minute search budget rather than a
        case's four, so this is worth more on the search side than the case
        side."""
        client, _, _ = build([FetchResult(OK, PROBLEM_REPORT_PAGE)] * 50,
                             budget_seconds=20)
        client.logged_in = True
        with pytest.raises(IcosUnavailable) as caught:
            client.search('PAT', '', 'DOE')
        assert caught.value.court_site_down is True

    def test_the_default_is_no_claim(self):
        """Every other raise site in the app builds one of these by hand."""
        assert IcosUnavailable('anything').court_site_down is False


class TestARefusedCaseWhileTheSiteIsUp:
    """ICOS answering one case with its problem report page while it answers
    everything else is not an outage, and waiting out the case budget on it
    is four minutes staff spend reading "did not respond" and restarting.

    Seen live on 2026-09-01: one Dubuque juvenile case, seven problem report
    pages in under four minutes, three restarts, fifteen alert emails.
    """

    GOOD = "01311  FECR000000"
    BAD = "01311  JVJV000001"
    BAD_PAGE = (b"<html>Trial Court Case Summary Title:&nbsp;STATE VS TESTER, PAT Q "
                b"Case: 01311  JVJV000001 (SYNTHETIC) Disposition Status</html>")

    def after_one_good_case(self, then, **kwargs):
        # A whole case first so the client has something known-good to ask
        # about, then whatever the test wants for the next one.
        client, clock, messages = build(
            [FetchResult(OK, CASE_PAGE)] * 3 + list(then), **kwargs)
        client.case_bundle(self.GOOD)
        clock.slept[:] = []
        start = clock.now
        return client, clock, messages, start

    def test_three_problem_pages_then_a_probe_that_answers_ends_the_wait(self):
        client, clock, _, start = self.after_one_good_case(
            [FetchResult(OK, PROBLEM_REPORT_PAGE)] * 3
            + [FetchResult(OK, CASE_PAGE)]          # the probe of the good case
            + [FetchResult(OK, PROBLEM_REPORT_PAGE)] * 200)
        with pytest.raises(icos.IcosCaseRefused) as raised:
            client.case_bundle(self.BAD)
        # Two backoffs, not the budget.
        assert clock.now - start < 30
        assert raised.value.court_site_down is False
        assert "would not serve this case" in raised.value.message
        assert "3 times" in raised.value.message
        assert client.given_up == 1
        # The probe asked for the case ICOS had already served, once.
        assert sum(self.GOOD in u for u in client.reader.urls) == 1 + 1

    def test_the_probe_failing_too_means_the_site_is_down(self):
        client, clock, _, start = self.after_one_good_case(
            [FetchResult(OK, PROBLEM_REPORT_PAGE)] * 200, case_budget_seconds=120)
        with pytest.raises(IcosUnavailable) as raised:
            client.case_bundle(self.BAD)
        assert not isinstance(raised.value, icos.IcosCaseRefused)
        assert raised.value.court_site_down is True
        # The whole budget, as before this change.
        assert clock.now - start >= 60

    def test_the_probe_is_asked_only_once(self):
        client, _, _, _ = self.after_one_good_case(
            [FetchResult(OK, PROBLEM_REPORT_PAGE)] * 200, case_budget_seconds=120)
        with pytest.raises(IcosUnavailable):
            client.case_bundle(self.BAD)
        # One to pull it, one to probe it, and no more however long the
        # bad case is retried after the probe said the site was down.
        assert sum(self.GOOD in u for u in client.reader.urls) == 1 + 1

    def test_with_nothing_known_good_the_budget_runs_as_before(self):
        client, clock, _ = build([FetchResult(OK, PROBLEM_REPORT_PAGE)] * 200,
                                 case_budget_seconds=120)
        with pytest.raises(IcosUnavailable) as raised:
            client.case_bundle(self.BAD)
        assert not isinstance(raised.value, icos.IcosCaseRefused)
        assert clock.now >= 60
        assert client.reader.calls.count("TViewCaseCivil") >= 5

    def test_a_blip_that_clears_is_not_probed(self):
        client, _, _, _ = self.after_one_good_case(
            [FetchResult(OK, PROBLEM_REPORT_PAGE)] * 2
            + [FetchResult(OK, self.BAD_PAGE)] * 3)
        summary, _, _ = client.case_bundle(self.BAD)
        assert summary == self.BAD_PAGE
        assert client.given_up == 0

    def test_problem_pages_broken_by_a_timeout_start_the_count_again(self):
        client, _, _, _ = self.after_one_good_case(
            [FetchResult(OK, PROBLEM_REPORT_PAGE)] * 2
            + [FetchResult(TIMEOUT)]
            + [FetchResult(OK, PROBLEM_REPORT_PAGE)] * 2
            + [FetchResult(OK, self.BAD_PAGE)] * 3)
        summary, _, _ = client.case_bundle(self.BAD)
        assert summary == self.BAD_PAGE
        assert client.given_up == 0

    def test_the_refusal_is_a_plain_unavailable_to_the_run(self):
        # The run's per-case handler catches IcosError; the outage counter
        # reads court_site_down. Both have to keep working unchanged.
        err = icos.IcosCaseRefused("x")
        assert isinstance(err, IcosUnavailable)
        assert isinstance(err, icos.IcosError)
        assert err.court_site_down is False

    def test_charges_and_financials_are_never_probed(self):
        # A probe re-selects a different case on the ICOS side. The summary
        # request names its case so the next retry sets that right; charges
        # and financials do not, so a probe there would be answered by the
        # wrong case's pages.
        client, _, _, _ = self.after_one_good_case(
            [FetchResult(OK, self.BAD_PAGE)]
            + [FetchResult(OK, PROBLEM_REPORT_PAGE)] * 200, case_budget_seconds=120)
        with pytest.raises(IcosUnavailable) as raised:
            client.case_bundle(self.BAD)
        assert not isinstance(raised.value, icos.IcosCaseRefused)
        assert sum(self.GOOD in u for u in client.reader.urls) == 1


class TestWhatTheProgressPageSays:
    """The progress line was read as the site being down, and acted on."""

    def test_a_problem_report_page_is_named_as_an_answer(self):
        client, _, messages = build([FetchResult(OK, PROBLEM_REPORT_PAGE),
                                     FetchResult(OK, CASE_PAGE),
                                     FetchResult(OK, CASE_PAGE),
                                     FetchResult(OK, CASE_PAGE)])
        client.case_bundle(CASE_ID)
        line = [m for m in messages if "Retrying" in m][0]
        assert "answered with its own error page" in line
        assert icos.PROBLEM_REPORT_MARKER in line
        assert "did not respond" not in line

    def test_a_wrong_case_page_is_named_as_an_answer(self):
        client, _, messages = build([FetchResult(OK, STUB_CASE_PAGE),
                                     FetchResult(OK, CASE_PAGE),
                                     FetchResult(OK, CASE_PAGE),
                                     FetchResult(OK, CASE_PAGE)])
        client.case_bundle(CASE_ID)
        line = [m for m in messages if "Retrying" in m][0]
        assert "wrong page" in line
        assert "did not respond" not in line

    def test_silence_is_still_called_silence(self):
        client, _, messages = build([FetchResult(TIMEOUT),
                                     FetchResult(OK, RESULTS_PAGE)])
        client.search("PAT", "", "TESTER")
        assert any("did not respond" in m for m in messages)

    def test_a_case_being_retried_says_not_to_restart(self):
        client, _, messages = build([FetchResult(OK, PROBLEM_REPORT_PAGE)] * 3
                                    + [FetchResult(OK, CASE_PAGE)] * 3)
        client.case_bundle(CASE_ID)
        retries = [m for m in messages if "Retrying" in m]
        # Not on the first retry, which is often one slow request.
        assert icos.CARRY_ON_NOTE not in retries[0]
        assert icos.CARRY_ON_NOTE in retries[1]
        assert "Stopping and starting again will not help" in retries[1]

    def test_a_search_is_not_told_it_will_be_skipped(self):
        # A search that will not answer ends the job. Promising to carry on
        # without it would be a lie.
        client, _, messages = build([FetchResult(OK, PROBLEM_REPORT_PAGE)] * 4
                                    + [FetchResult(OK, RESULTS_PAGE)])
        client.search("PAT", "", "TESTER")
        assert not any(icos.CARRY_ON_NOTE in m for m in messages)

    def test_the_probe_says_what_it_learned(self):
        client, _, messages = build([FetchResult(OK, CASE_PAGE)] * 3
                                    + [FetchResult(OK, PROBLEM_REPORT_PAGE)] * 3
                                    + [FetchResult(OK, CASE_PAGE)])
        client.case_bundle(CASE_ID)
        with pytest.raises(icos.IcosCaseRefused):
            client.case_bundle("01311  JVJV000001")
        assert any("it is up and will not serve this one" in m for m in messages)

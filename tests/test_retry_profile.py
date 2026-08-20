"""Counting how hard Iowa Courts made Napier work, so the budgets stop guessing.

A case Napier gives up on costs four minutes. Six in a row is the outage cap,
so an outage costs about 23 minutes of a clinic before the run stops. Whether
that four minutes is worth spending has never been measured: nobody knows
whether a case that has failed five times ever comes back on the sixth, or
whether the answer was settled in the first ten seconds and the rest is Napier
waiting to be sure.

Measured against the real site on 2026-08-01, 314 requests answered first time
and the slowest took 1.31s against an 8 second timeout, so there is nothing to
learn from a healthy day. The only useful sample is a bad one, and bad days are
exactly when nobody is taking notes. So Napier takes them.

Silent when there is nothing to say, because a line that appears after every
run is a line nobody reads.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from icos import IcosUnavailable
from reader import EMPTY, OK, TIMEOUT, FetchResult
from test_icos_retry import CASE_ID, CASE_PAGE, RESULTS_PAGE, build


class TestWhenItStaysQuiet:
    def test_a_run_where_everything_worked_says_nothing(self):
        client, _, _ = build([FetchResult(OK, RESULTS_PAGE)])
        client.search("PAT", "", "TESTER")
        assert client.retry_summary() is None

    def test_and_puts_nothing_in_the_progress_log(self):
        client, _, messages = build([FetchResult(OK, RESULTS_PAGE)])
        client.search("PAT", "", "TESTER")
        client.logoff()
        assert not [m for m in messages if "answered" in m]

    def test_a_session_that_did_nothing_at_all_says_nothing(self):
        client, _, _ = build([])
        client.logoff()
        assert client.retry_summary() is None


class TestWhatItCounts:
    def test_a_request_that_needed_a_second_go(self):
        client, _, _ = build([FetchResult(TIMEOUT), FetchResult(OK, RESULTS_PAGE)])
        client.search("PAT", "", "TESTER")

        assert client.landed_on == {2: 1}
        assert "1 on try 2" in client.retry_summary()

    def test_it_names_the_attempt_it_landed_on(self):
        client, _, _ = build([FetchResult(TIMEOUT), FetchResult(EMPTY),
                              FetchResult(TIMEOUT), FetchResult(OK, RESULTS_PAGE)])
        client.search("PAT", "", "TESTER")

        assert client.landed_on == {4: 1}
        assert "1 on try 4" in client.retry_summary()

    def test_the_ones_that_worked_first_time_are_counted_too(self):
        """Three retries out of four requests and three out of four hundred are
        different afternoons, and the line has to tell them apart."""
        client, _, _ = build([FetchResult(OK, CASE_PAGE),
                              FetchResult(OK, CASE_PAGE),
                              FetchResult(TIMEOUT), FetchResult(OK, CASE_PAGE)])
        client.case_bundle(CASE_ID)

        assert client.landed_on == {1: 2, 2: 1}
        assert "Iowa Courts answered 2 first time" in client.retry_summary()

    def test_the_line_says_it_is_counting_requests_not_cases(self):
        """A case costs three requests, so the bare number reads as cases."""
        client, _, _ = build([FetchResult(OK, CASE_PAGE),
                              FetchResult(TIMEOUT), FetchResult(OK, CASE_PAGE),
                              FetchResult(OK, CASE_PAGE)])
        client.case_bundle(CASE_ID)

        assert client.retry_summary().startswith("Of the page requests")

    def test_a_whole_bundle_that_worked_is_still_quiet(self):
        client, _, _ = build([FetchResult(OK, CASE_PAGE)] * 3)
        client.case_bundle(CASE_ID)

        assert client.landed_on == {1: 3}
        assert client.retry_summary() is None


class TestTheOnesItNeverGot:
    def test_a_case_given_up_on_is_counted(self):
        client, _, _ = build([FetchResult(TIMEOUT)] * 6, case_budget_seconds=3)
        with pytest.raises(IcosUnavailable):
            client.case_bundle(CASE_ID)

        assert client.given_up == 1
        assert "never answered 1" in client.retry_summary()

    def test_a_run_that_lost_cases_but_never_retried_still_reports(self):
        """Nothing landed late, so the attempt tally is empty. A run that gave
        up on three cases is still the most interesting run of the week."""
        client, _, _ = build([FetchResult(TIMEOUT)] * 6, case_budget_seconds=3)
        with pytest.raises(IcosUnavailable):
            client.case_bundle(CASE_ID)

        assert not [n for n in client.landed_on if n > 1]
        assert client.retry_summary() is not None


class TestHowItGetsOut:
    def test_logging_off_writes_it_to_the_progress_log(self):
        """Which is what alert emails carry, so the run that went badly arrives
        already saying how."""
        client, _, messages = build([FetchResult(TIMEOUT),
                                     FetchResult(OK, RESULTS_PAGE)])
        client.search("PAT", "", "TESTER")
        client.logoff()

        assert any("on try 2" in m for m in messages)

    def test_a_session_that_never_signed_in_still_reports(self):
        """logoff() returns early when there is no session to release, so the
        line has to be written before that check. A run that spent its whole
        life failing never reaches the logged-in branch to tell anyone."""
        client, _, messages = build([FetchResult(TIMEOUT)] * 6,
                                    case_budget_seconds=3)
        with pytest.raises(IcosUnavailable):
            client.case_bundle(CASE_ID)
        assert not client.logged_in

        client.logoff()
        assert any("never answered" in m for m in messages)

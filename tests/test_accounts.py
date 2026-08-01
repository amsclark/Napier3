"""Telling a staffer what is holding the account, instead of making them guess.

Iowa Courts allows one session per ILA account and offers no force-logoff, so
the second person to sign in on a shared login waits the lock out. Napier used
to answer that with a shrug: an account is signed in somewhere else, try again
in about fifteen minutes. Most of the time the somewhere else was Napier, in
this process, on a run it had known the account of since it signed in.

No client name appears here. An ESA user ID names an office, not a person, and
the staffer being told already typed it.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import accounts
import alerts
import icos
from icos import IcosAccountLocked, IcosClient
from reader import OK, FetchResult

LOGIN_OK = b"x" * 28000
CONCURRENT_PAGE = b"<html>Concurrent Login Error: A user is already logged on</html>"


class FakeReader:
    """Just enough ESA to sign in and sign out."""

    def __init__(self, script, logoff_raises=False):
        self.script = list(script)
        self.calls = []
        self.logoff_raises = logoff_raises

    def fetch_once(self, url, data=None, timeout=8):
        name = url.rsplit("/", 1)[-1].split("?")[0]
        self.calls.append(name)
        if name == "EPALogout" and self.logoff_raises:
            raise OSError("connection reset")
        return self.script.pop(0) if self.script else FetchResult(OK, LOGIN_OK)

    def init_request(self):
        return "https://icos/ESAWebApp/ESALogin.jsp", None

    def login_request(self, username, password):
        return "https://icos/ESAWebApp/EUACustomLoginServlet", "userid=" + username

    def logoff_request(self):
        return "https://icos/ESAWebApp/EPALogout", "logoffButton=Logoff"


class Clock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds

    def monotonic(self):
        return self.now


def build(script, logoff_raises=False, **kwargs):
    clock = Clock()
    messages = []
    alerted = []
    client = IcosClient(log=messages.append,
                        reader=FakeReader(script, logoff_raises),
                        sleep=clock.sleep, monotonic=clock.monotonic,
                        alert=lambda failure, **fields: alerted.append((failure, fields)),
                        concurrent_budget_seconds=kwargs.pop(
                            'concurrent_budget_seconds', 16 * 60))
    return client, messages, alerted


def signed_in(username="ILA07"):
    """A client holding the account, the way a live search job holds one."""
    client, _, _ = build([FetchResult(OK, LOGIN_OK), FetchResult(OK, LOGIN_OK)])
    client.login(username, "secret")
    return client


# -- the registry ---------------------------------------------------------

class TestTheRegistry:
    def test_an_account_nobody_holds_has_no_holder(self):
        assert accounts.holder("ILA07") is None
        assert accounts.describe("ILA07") is None

    def test_a_held_account_reports_who_and_since_when(self):
        accounts.hold("ILA07", now=1000.0)
        entry = accounts.holder("ILA07", now=1360.0)
        assert entry["account"] == "ILA07"
        assert entry["seconds"] == 360.0

    def test_releasing_gives_the_account_back(self):
        handle = accounts.hold("ILA07")
        accounts.release(handle)
        assert accounts.holder("ILA07") is None

    def test_releasing_twice_is_not_an_error(self):
        """logoff runs in cleanup paths that can be reached more than once, and
        an exception raised in one of those strands the ESA session."""
        handle = accounts.hold("ILA07")
        accounts.release(handle)
        accounts.release(handle)
        accounts.release(None)
        assert accounts.holder("ILA07") is None

    def test_one_run_letting_go_does_not_release_another(self):
        """Two runs overlap on one account while the first logs off. Keyed by
        account instead of by handle, the second run's entry disappears with
        the first one's and the next staffer is told nothing has the account
        while a run is still using it."""
        first = accounts.hold("ILA07", now=1000.0)
        accounts.hold("ILA07", now=1010.0)
        accounts.release(first)
        assert accounts.holder("ILA07") is not None

    def test_the_oldest_run_is_the_one_named(self):
        """The older session is the one ICOS actually let in. A newer entry is
        somebody who has just been refused, and naming them sends the staffer
        to a run that is not holding anything."""
        accounts.hold("ILA07", now=2000.0)
        accounts.hold("ILA07", now=1000.0)
        assert accounts.holder("ILA07", now=2000.0)["seconds"] == 1000.0

    def test_accounts_are_matched_however_they_were_typed(self):
        accounts.hold(" ila07 ")
        assert accounts.holder("ILA07") is not None

    def test_a_different_account_is_not_a_collision(self):
        accounts.hold("ILA07")
        assert accounts.holder("ILA04") is None

    def test_an_empty_user_id_never_matches(self):
        """A client that never signed in has username None, and None matching
        None would report every failed sign in as holding the account."""
        accounts.hold("")
        assert accounts.holder("") is None
        assert accounts.holder(None) is None


class TestHowLongItSays:
    @pytest.mark.parametrize("seconds,expected", [
        (0, "less than a minute ago"),
        (59, "less than a minute ago"),
        (60, "a minute ago"),
        (119, "a minute ago"),
        (360, "6 minutes ago"),
        (3600, "1 hour ago"),
        (9000, "2 hours ago"),
    ])
    def test_it_reads_like_a_sentence(self, seconds, expected):
        assert accounts.describe_wait(seconds) == expected


# -- the client ------------------------------------------------------------

class TestSigningInTakesTheAccount:
    def test_a_signed_in_run_holds_its_account(self):
        signed_in("ILA07")
        assert accounts.holder("ILA07") is not None

    def test_signing_off_gives_it_back(self):
        client = signed_in("ILA07")
        client.logoff()
        assert accounts.holder("ILA07") is None

    def test_a_failed_sign_off_still_gives_it_back(self):
        """ESA may well still be holding the session, but no Napier run is. The
        entry exists to send the next staffer to a run they can go and stop,
        and sending them to one that already ended is worse than saying
        nothing."""
        client, _, _ = build([FetchResult(OK, LOGIN_OK), FetchResult(OK, LOGIN_OK)],
                             logoff_raises=True)
        client.login("ILA07", "secret")
        client.logoff()
        assert accounts.holder("ILA07") is None

    def test_a_refused_sign_in_takes_nothing(self):
        """Registered only once ESA hands back the search screen. Point the
        next staffer at a session that was never opened and they wait on
        nothing."""
        client, _, _ = build([FetchResult(OK, LOGIN_OK),
                              FetchResult(OK, CONCURRENT_PAGE),
                              FetchResult(OK, CONCURRENT_PAGE)],
                             concurrent_budget_seconds=100)
        with pytest.raises(IcosAccountLocked):
            client.login("ILA07", "secret")
        assert accounts.holder("ILA07") is None


class TestWhatTheSecondStafferIsTold:
    def test_the_collision_is_named_before_icos_is_even_asked(self):
        """The point of the whole thing. The wait is already certain by the
        time the account is held, so the sentence is worth more now than it is
        seventy-five seconds into a sixteen minute wait."""
        accounts.hold("ILA07", now=0.0)
        client, messages, _ = build([FetchResult(OK, LOGIN_OK),
                                     FetchResult(OK, LOGIN_OK)])
        client.login("ILA07", "secret")

        assert "already signed in to Iowa Courts as ILA07" in messages[1]
        # Before the connection, not after ESA refuses: the first line is the
        # connecting notice and this is the second thing said.
        assert "Connecting" in messages[0]

    def test_it_says_which_account_and_how_long(self):
        import time
        accounts.hold("ILA07", now=time.time() - 360)
        client, messages, _ = build([FetchResult(OK, LOGIN_OK),
                                     FetchResult(OK, LOGIN_OK)])
        client.login("ILA07", "secret")
        assert "ILA07" in messages[1]
        assert "6 minutes ago" in messages[1]

    def test_a_free_account_is_not_mentioned_at_all(self):
        client, messages, _ = build([FetchResult(OK, LOGIN_OK),
                                     FetchResult(OK, LOGIN_OK)])
        client.login("ILA07", "secret")
        assert not any("already signed in" in m for m in messages)

    def test_somebody_elses_account_is_not_a_collision(self):
        accounts.hold("ILA04")
        client, messages, _ = build([FetchResult(OK, LOGIN_OK),
                                     FetchResult(OK, LOGIN_OK)])
        client.login("ILA07", "secret")
        assert not any("already signed in" in m for m in messages)

    def test_a_lock_napier_is_not_holding_says_so(self):
        """The honest answer when the registry is empty. Somebody is signed in
        to Iowa Courts outside Napier, or a restart lost the run, and neither
        is something to invent a Napier run for."""
        client, messages, _ = build([FetchResult(OK, LOGIN_OK),
                                     FetchResult(OK, CONCURRENT_PAGE),
                                     FetchResult(OK, LOGIN_OK)])
        client.login("ILA07", "secret")

        waiting = [m for m in messages if "already logged in" in m]
        assert len(waiting) == 1
        assert "not by Napier" in waiting[0]

    def test_the_collision_is_not_read_out_twice(self):
        """Named once before connecting, then ESA refuses and the wait starts.
        Repeating the same paragraph reads as the page stuck, not as
        progress."""
        accounts.hold("ILA07", now=0.0)
        client, messages, _ = build([FetchResult(OK, LOGIN_OK),
                                     FetchResult(OK, CONCURRENT_PAGE),
                                     FetchResult(OK, LOGIN_OK)])
        client.login("ILA07", "secret")

        assert sum("already signed in to Iowa Courts as" in m for m in messages) == 1
        assert any("Waiting for that run to finish" in m for m in messages)


class TestWhenTheWaitRunsOut:
    def build_locked(self, hold_it):
        # From empty, so calling this twice in one test does not have the first
        # scenario's account still held during the second.
        accounts._reset()
        if hold_it:
            accounts.hold("ILA07", now=0.0)
        client, messages, alerted = build(
            [FetchResult(OK, LOGIN_OK)] + [FetchResult(OK, CONCURRENT_PAGE)] * 100,
            concurrent_budget_seconds=200)
        with pytest.raises(IcosAccountLocked) as caught:
            client.login("ILA07", "secret")
        return caught.value, messages, alerted

    def test_napiers_own_run_gets_advice_that_can_be_acted_on(self):
        """There is a stop button on that run's progress page. Telling somebody
        to come back in a few minutes when the answer is a colleague two desks
        away is the whole gap this closes."""
        error, _, _ = self.build_locked(hold_it=True)
        assert "another Napier run" in error.message
        assert "stop it" in error.message

    def test_a_lock_from_outside_napier_keeps_the_old_advice(self):
        error, _, _ = self.build_locked(hold_it=False)
        assert "still logged in from another session" in error.message
        assert "Napier" not in error.message

    def test_the_alert_says_which_of_the_two_it_was(self):
        _, _, ours = self.build_locked(hold_it=True)
        _, _, theirs = self.build_locked(hold_it=False)

        assert ours[-1][0] == alerts.CONCURRENT_EXHAUSTED
        assert "Napier's own run" in ours[-1][1]['note']
        assert "outside Napier" in theirs[-1][1]['note']

    def test_an_account_taken_after_the_wait_began_is_still_named(self):
        """Nothing held it when this sign in started, so there was nothing to
        say up front. The holder is asked for again when the wait runs out
        rather than reusing that answer, or a run that started in between is
        invisible for the whole sixteen minutes and the advice at the end of it
        is the wrong advice.
        """
        client, _, _ = build(
            [FetchResult(OK, LOGIN_OK)] + [FetchResult(OK, CONCURRENT_PAGE)] * 100,
            concurrent_budget_seconds=200)
        original_sleep = client._sleep
        taken = []

        def take_the_account_mid_wait(seconds):
            if not taken:
                taken.append(accounts.hold("ILA07", now=0.0))
            original_sleep(seconds)

        client._sleep = take_the_account_mid_wait
        with pytest.raises(IcosAccountLocked) as caught:
            client.login("ILA07", "secret")

        assert "another Napier run" in caught.value.message

    def test_the_alert_still_names_the_family_and_not_the_account(self):
        """Article 1.2 keeps credentials out of what Napier transmits. The
        staffer on the page may be told ILA07 because they typed it; an inbox
        is not the staffer."""
        _, _, alerted = self.build_locked(hold_it=True)
        fields = alerted[-1][1]
        assert fields['account'] == 'ILA##'
        assert 'ILA07' not in repr(fields)

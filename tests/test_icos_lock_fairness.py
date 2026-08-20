"""Who gets a shared Iowa Courts login next, and what we say when nobody did.

Both halves of this file come from one incident on 19 August 2026. Four
searches were started on one ILA login inside forty-five seconds. Three of them
took the account in turn and finished. The fourth waited the full fifteen
minutes and died, and the alert it sent said somebody was signed in to Iowa
Courts outside Napier, which was not true: Napier had held that account for
almost the whole wait with its own jobs.

So there were two bugs wearing one coat. Nothing decided the order, so the job
that asked first could lose to three that asked later; and the question "is
Napier holding this account" was asked once, at the end, by which time the
answer had changed.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import accounts
import alerts
import icos
from icos import IcosAccountLocked, IcosClient, IcosStopped
from reader import OK, FetchResult
from test_accounts import CONCURRENT_PAGE, FakeReader, LOGIN_OK


class Clock:
    """A clock that can be given something to do while it sleeps.

    The account registry changes underneath a waiting job in real life -- that
    is the whole subject here -- and the only moment a single threaded test can
    change it is during a sleep.
    """

    def __init__(self, on_sleep=None):
        self.now = 0.0
        self.slept = []
        self._on_sleep = on_sleep

    def sleep(self, seconds):
        self.now += seconds
        self.slept.append(seconds)
        if self._on_sleep is not None:
            self._on_sleep(len(self.slept))

    def monotonic(self):
        return self.now


def build(script, on_sleep=None, budget=16 * 60, should_stop=None):
    clock = Clock(on_sleep)
    messages = []
    alerted = []
    client = IcosClient(log=messages.append,
                        reader=FakeReader(script),
                        sleep=clock.sleep, monotonic=clock.monotonic,
                        alert=lambda failure, **fields: alerted.append(
                            (failure, fields)),
                        concurrent_budget_seconds=budget)
    if should_stop is not None:
        client.set_stop_check(should_stop)
    return client, messages, alerted, clock


def queue_waits(clock):
    """Just the waits spent standing in line, not the ones spent asking ESA."""
    return [s for s in clock.slept if s == icos.QUEUE_INTERVAL]


# -- the waiting line itself ----------------------------------------------

class TestTheWaitingLine:
    def test_a_job_on_its_own_is_at_the_head(self):
        ticket = accounts.take_ticket('ILA07')
        assert accounts.ahead_of('ILA07', ticket) == 0

    def test_a_second_job_stands_behind_the_first(self):
        first = accounts.take_ticket('ILA07')
        second = accounts.take_ticket('ILA07')
        assert accounts.ahead_of('ILA07', first) == 0
        assert accounts.ahead_of('ILA07', second) == 1

    def test_a_different_account_is_a_different_line(self):
        accounts.take_ticket('ILA07')
        other = accounts.take_ticket('ILA09')
        assert accounts.ahead_of('ILA09', other) == 0

    def test_the_same_account_typed_differently_is_one_line(self):
        """Staff type their user id by hand, so the line has to key the way the
        rest of the registry does or two spellings become two lines and the
        ordering is back to a race."""
        first = accounts.take_ticket(' ila07 ')
        second = accounts.take_ticket('ILA07')
        assert accounts.ahead_of('ILA07', second) == 1
        accounts.drop_ticket(first)
        assert accounts.ahead_of('ILA07', second) == 0

    def test_dropping_the_head_moves_everybody_up(self):
        first = accounts.take_ticket('ILA07')
        second = accounts.take_ticket('ILA07')
        third = accounts.take_ticket('ILA07')
        accounts.drop_ticket(first)
        assert accounts.ahead_of('ILA07', second) == 0
        assert accounts.ahead_of('ILA07', third) == 1

    def test_tickets_taken_in_the_same_instant_still_have_an_order(self):
        """Four searches submitted together can share a clock reading. Ordering
        on the timestamp would leave two of them both believing they were
        first, which is the race this exists to end."""
        first = accounts.take_ticket('ILA07', now=1000.0)
        second = accounts.take_ticket('ILA07', now=1000.0)
        third = accounts.take_ticket('ILA07', now=1000.0)
        assert [accounts.ahead_of('ILA07', t, now=1000.0)
                for t in (first, second, third)] == [0, 1, 2]

    def test_an_abandoned_ticket_does_not_wedge_the_line(self):
        """A thread that dies between take_ticket and its finally would
        otherwise hold up everybody behind it forever, which is worse than the
        race it replaced."""
        accounts.take_ticket('ILA07', now=0.0)
        mine = accounts.take_ticket('ILA07', now=0.0)
        stale = accounts.STALE_TICKET_SECONDS + 1
        assert accounts.ahead_of('ILA07', mine, now=stale) == 0

    def test_an_unknown_ticket_is_not_made_to_wait(self):
        accounts.take_ticket('ILA07')
        assert accounts.ahead_of('ILA07', 'never-issued') == 0

    def test_dropping_twice_is_harmless(self):
        ticket = accounts.take_ticket('ILA07')
        accounts.drop_ticket(ticket)
        accounts.drop_ticket(ticket)
        accounts.drop_ticket(None)


# -- the order is kept ----------------------------------------------------

class TestSigningInWaitsItsTurn:
    def test_a_job_on_its_own_does_not_wait_at_all(self):
        client, _, _, clock = build([FetchResult(OK, LOGIN_OK),
                                     FetchResult(OK, LOGIN_OK)])
        client.login('ILA07', 'secret')
        assert client.logged_in
        assert queue_waits(clock) == []

    def test_a_job_behind_another_waits_for_it(self):
        """The regression. Before the line existed this signed in immediately,
        raced the job that arrived first, and could win."""
        earlier = accounts.take_ticket('ILA07')
        client, messages, _, clock = build(
            [FetchResult(OK, LOGIN_OK), FetchResult(OK, LOGIN_OK)],
            on_sleep=lambda n: accounts.drop_ticket(earlier) if n == 2 else None)

        client.login('ILA07', 'secret')

        assert client.logged_in
        assert queue_waits(clock) == [icos.QUEUE_INTERVAL] * 2

    def test_it_says_how_many_are_ahead(self):
        earlier = accounts.take_ticket('ILA07')
        client, messages, _, _ = build(
            [FetchResult(OK, LOGIN_OK), FetchResult(OK, LOGIN_OK)],
            on_sleep=lambda n: accounts.drop_ticket(earlier))
        client.login('ILA07', 'secret')

        waiting = [m for m in messages if 'earlier' in m]
        assert waiting
        assert '1 earlier search' in waiting[0]
        assert 'is still running' in waiting[0]

    def test_it_counts_more_than_one_correctly(self):
        first = accounts.take_ticket('ILA07')
        second = accounts.take_ticket('ILA07')
        dropped = []

        def release(n):
            for ticket in (first, second):
                if ticket not in dropped:
                    accounts.drop_ticket(ticket)
                    dropped.append(ticket)
                    return

        client, messages, _, _ = build([FetchResult(OK, LOGIN_OK),
                                        FetchResult(OK, LOGIN_OK)],
                                       on_sleep=release)
        client.login('ILA07', 'secret')

        waiting = [m for m in messages if 'earlier' in m]
        assert '2 earlier searches' in waiting[0]
        assert 'are still running' in waiting[0]
        # It counts down rather than repeating itself.
        assert '1 earlier search' in waiting[1]

    def test_a_line_on_another_account_is_not_ours(self):
        accounts.take_ticket('ILA09')
        client, _, _, clock = build([FetchResult(OK, LOGIN_OK),
                                     FetchResult(OK, LOGIN_OK)])
        client.login('ILA07', 'secret')
        assert client.logged_in
        assert queue_waits(clock) == []

    def test_stopping_works_while_standing_in_line(self):
        """Sixteen minutes is far too long to make somebody sit through once
        they have decided to stop."""
        accounts.take_ticket('ILA07')
        client, _, _, _ = build([FetchResult(OK, LOGIN_OK)],
                                should_stop=lambda: True)
        with pytest.raises(IcosStopped):
            client.login('ILA07', 'secret')

    def test_the_ticket_is_given_back_even_when_the_login_fails(self):
        """Otherwise one failed job holds up every job behind it until the
        stale sweep, which is fifteen minutes of nothing for everybody."""
        client, _, _, _ = build([FetchResult(OK, LOGIN_OK),
                                 FetchResult(OK, CONCURRENT_PAGE),
                                 FetchResult(OK, CONCURRENT_PAGE)],
                                budget=100)
        with pytest.raises(IcosAccountLocked):
            client.login('ILA07', 'secret')

        after = accounts.take_ticket('ILA07')
        assert accounts.ahead_of('ILA07', after) == 0

    def test_the_ticket_is_given_back_once_signed_in(self):
        """A ticket is intent, not possession. Holding one for the length of
        the session would stop the next job from even being told it is
        waiting, which is what the registry is for."""
        client, _, _, _ = build([FetchResult(OK, LOGIN_OK),
                                 FetchResult(OK, LOGIN_OK)])
        client.login('ILA07', 'secret')

        after = accounts.take_ticket('ILA07')
        assert accounts.ahead_of('ILA07', after) == 0
        assert accounts.holder('ILA07') is not None


class TestGivingUpInTheLine:
    def test_it_gives_up_if_the_line_never_moves(self):
        accounts.take_ticket('ILA07')
        client, _, alerted, _ = build([FetchResult(OK, LOGIN_OK)], budget=100)

        with pytest.raises(IcosAccountLocked) as caught:
            client.login('ILA07', 'secret')

        assert 'run this search again' in str(caught.value)
        assert 'one session per account' in str(caught.value)

    def test_it_does_not_blame_an_outsider_for_our_own_queue(self):
        accounts.take_ticket('ILA07')
        client, _, alerted, _ = build([FetchResult(OK, LOGIN_OK)], budget=100)

        with pytest.raises(IcosAccountLocked):
            client.login('ILA07', 'secret')

        failure, fields = alerted[0]
        assert failure == alerts.CONCURRENT_EXHAUSTED
        assert 'outside Napier' not in fields['note']
        assert 'Not an outside session' in fields['note']
        assert 'never got a turn' in fields['note']

    def test_the_alert_still_keeps_the_account_to_its_family(self):
        accounts.take_ticket('ILA07')
        client, _, alerted, _ = build([FetchResult(OK, LOGIN_OK)], budget=100)
        with pytest.raises(IcosAccountLocked):
            client.login('ILA07', 'secret')

        assert alerted[0][1]['account'] == 'ILA##'


# -- what the alert is allowed to conclude --------------------------------

class TestTheHolderQuestionIsAskedAcrossTheWholeWait:
    """19 August, exactly. Napier held the account for most of the wait and let
    it go before the budget ran out, and the end-of-wait sample found an empty
    registry and blamed a stranger."""

    def _timed_out_with_a_hold_that_ends_midway(self):
        handle = accounts.hold('ILA07', now=0.0)
        client, _, alerted, _ = build(
            [FetchResult(OK, LOGIN_OK),
             FetchResult(OK, CONCURRENT_PAGE),
             FetchResult(OK, CONCURRENT_PAGE)],
            on_sleep=lambda n: accounts.release(handle),
            budget=100)
        with pytest.raises(IcosAccountLocked) as caught:
            client.login('ILA07', 'secret')
        return alerted[0][1]['note'], str(caught.value)

    def test_the_alert_does_not_invent_an_outside_session(self):
        note, _ = self._timed_out_with_a_hold_that_ends_midway()
        assert 'signed in to Iowa Courts outside Napier' not in note

    def test_the_alert_says_it_was_napier(self):
        note, _ = self._timed_out_with_a_hold_that_ends_midway()
        assert "Napier's own runs held the account" in note
        assert 'Not an outside session' in note

    def test_the_staffer_is_not_sent_after_a_phantom_colleague(self):
        """The old message told her to use a different Iowa Courts account. She
        has one account, and the collision was her own searches."""
        _, told = self._timed_out_with_a_hold_that_ends_midway()
        assert 'use your own Iowa Courts account' not in told
        assert 'run this search again' in told

    def test_an_account_napier_never_held_still_reads_as_an_outsider(self):
        """The partition. Fixing the false accusation must not cost the true
        one: a staffer signed in to ICOS in a browser tab is a real thing that
        happens and the alert has to keep saying so."""
        client, _, alerted, _ = build([FetchResult(OK, LOGIN_OK),
                                       FetchResult(OK, CONCURRENT_PAGE),
                                       FetchResult(OK, CONCURRENT_PAGE)],
                                      budget=100)
        with pytest.raises(IcosAccountLocked):
            client.login('ILA07', 'secret')

        note = alerted[0][1]['note']
        assert 'Somebody is signed in to Iowa Courts outside Napier' in note
        assert 'at any point in the wait' in note

    def test_a_hold_that_lasts_the_whole_wait_still_names_two_staff(self):
        """The third case, unchanged: the other run is still going, so there is
        somebody who can stop it."""
        accounts.hold('ILA07', now=0.0)
        client, _, alerted, _ = build([FetchResult(OK, LOGIN_OK),
                                       FetchResult(OK, CONCURRENT_PAGE),
                                       FetchResult(OK, CONCURRENT_PAGE)],
                                      budget=100)
        with pytest.raises(IcosAccountLocked) as caught:
            client.login('ILA07', 'secret')

        assert 'Two staff on one login' in alerted[0][1]['note']
        assert 'stop it from their own progress page' in str(caught.value)

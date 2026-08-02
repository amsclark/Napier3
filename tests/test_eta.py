"""Telling staff how much longer a run has.

A staffer with a client in front of them and a bar crawling across the screen
is asking one question, and it is not what percentage is done. It is whether to
wait or to go and do something else for five minutes. Napier already knows: it
is on case 12 of 77 and it has been timing every case it pulled.

Measured against the real site on 2026-08-01, a case takes about half a second
and the middle 99 percent land inside a second of each other, so the rate is
steady enough to predict from. What is not steady is a case Iowa Courts stalls
on, which is why nothing here is an average.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jobs


class Clock:
    """A clock the test moves by hand, so none of this sleeps."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def tick(self, seconds):
        self.now += seconds
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(jobs.time, 'time', fake)
    return fake


def pulling(job, count, total=70):
    job.log("Pulling case %d of %d..." % (count + 1, total), count=count, total=total)


def run_at(clock, seconds_each, cases, total=70):
    """A job that has pulled some cases, each taking the same time."""
    job = jobs.Job('crs')
    for n in range(cases):
        pulling(job, n, total)
        clock.tick(seconds_each)
    return job


class TestWhenItWillNotGuess:
    def test_a_search_has_nothing_to_measure(self, clock):
        """A search does not know how many names it is about to find, so it
        sets no total and must not be given a made up one."""
        job = jobs.Job('search')
        job.log("Searching Iowa Courts Online...")
        clock.tick(30)
        assert job.to_dict()['seconds_left'] is None

    def test_two_cases_is_not_a_rate(self, clock):
        job = run_at(clock, 0.5, 2)
        assert job.to_dict()['seconds_left'] is None

    def test_three_cases_is(self, clock):
        job = run_at(clock, 0.5, 4)
        assert job.to_dict()['seconds_left'] is not None

    def test_a_finished_run_is_not_still_counting_down(self, clock):
        job = jobs.Job('crs')
        for n in range(6):
            pulling(job, n, total=6)
            clock.tick(0.5)
        job.log("Building the CRS workbook...", count=6, total=6)
        assert job.to_dict()['seconds_left'] is None

    def test_a_repeated_line_is_not_a_case(self, clock):
        """Two log lines at the same count are one unit of work, not two taking
        no time each. Counting them would halve every estimate."""
        job = jobs.Job('crs')
        for n in range(4):
            pulling(job, n)
            job.log("Iowa Courts is taking a while on this one...",
                    count=n, total=70)
            clock.tick(2.0)

        left = job.to_dict()['seconds_left']
        assert 130 < left < 140   # 67 cases still to go at two seconds each


class TestWhatItSays:
    def test_it_is_the_cases_left_times_what_a_case_costs(self, clock):
        job = run_at(clock, 0.5, 10, total=70)
        # 60 left, half a second each.
        assert job.to_dict()['seconds_left'] == 30

    def test_a_slower_site_gives_a_longer_estimate(self, clock):
        quick = run_at(clock, 0.5, 10, total=70)
        slow = run_at(clock, 3.0, 10, total=70)
        assert slow.to_dict()['seconds_left'] > quick.to_dict()['seconds_left']

    def test_the_estimate_falls_as_the_run_goes_on(self, clock):
        job = run_at(clock, 0.5, 10, total=70)
        early = job.to_dict()['seconds_left']
        for n in range(10, 40):
            pulling(job, n)
            clock.tick(0.5)
        assert job.to_dict()['seconds_left'] < early * 0.6


class TestTheStalledCase:
    def test_one_four_minute_case_does_not_rewrite_the_run(self, clock):
        """The whole reason this is a median. Nine cases at half a second and
        one that Iowa Courts sat on for four minutes averages out to twenty
        four seconds a case, which would tell a staffer with a two minute run
        left to expect twenty four minutes."""
        job = jobs.Job('crs')
        for n in range(10):
            pulling(job, n)
            clock.tick(240 if n == 4 else 0.5)
        pulling(job, 10)

        left = job.to_dict()['seconds_left']
        assert left < 60          # 59 cases at about half a second
        assert left > 25

    def test_a_stall_happening_now_makes_the_estimate_climb(self, clock):
        """A bar that has not moved in three minutes under an estimate that has
        not moved either is the page calling itself a liar."""
        job = run_at(clock, 0.5, 10, total=70)
        settled = job.to_dict()['seconds_left']
        clock.tick(180)

        assert job.to_dict()['seconds_left'] > settled + 170

    def test_and_drops_back_once_the_case_comes_through(self, clock):
        job = run_at(clock, 1.0, 10, total=70)
        settled = job.to_dict()['seconds_left']
        clock.tick(180)
        pulling(job, 10)

        # One bad case among ten, so the middle one is unchanged and the only
        # difference is the case that is no longer outstanding.
        assert job.to_dict()['seconds_left'] < settled

    def test_a_site_that_has_gone_slow_is_believed(self, clock):
        """A stall is one case. Twenty slow ones in a row is the site, and the
        estimate has to follow it up rather than keep quoting this morning."""
        job = run_at(clock, 0.5, 10, total=70)
        quick = job.to_dict()['seconds_left']
        for n in range(10, 40):
            pulling(job, n)
            clock.tick(4.0)

        assert job.to_dict()['seconds_left'] > quick


class TestWhatItKeeps:
    def test_it_does_not_remember_the_whole_run(self, clock):
        job = run_at(clock, 0.5, 60, total=200)
        assert len(job.marks) <= jobs.UNITS_TIMED

    def test_which_is_why_it_can_follow_the_site_down(self, clock):
        """Sixty quick cases then twenty slow ones, and the estimate is about
        the slow ones. Keeping every mark would leave the quick ones outvoting
        the site's current behaviour for the rest of the run."""
        job = run_at(clock, 0.5, 60, total=200)
        for n in range(60, 85):
            pulling(job, n, total=200)
            clock.tick(4.0)

        left = job.to_dict()['seconds_left']
        assert left > 4 * (200 - 85) * 0.9


class TestThePageGetsIt:
    def test_the_poll_carries_it(self, clock):
        job = run_at(clock, 0.5, 10, total=70)
        assert 'seconds_left' in job.to_dict()

    def test_it_is_a_whole_number_of_seconds(self, clock):
        job = run_at(clock, 0.37, 10, total=70)
        left = job.to_dict()['seconds_left']
        assert isinstance(left, int)

"""When Iowa Courts goes down, the whole run stops, not each client in turn.

The count of cases refused back to back used to be per client and per _pull_cases
call, so a clinic list of twenty names discovered the site was down twenty
separate times. Each discovery cost three cases at four minutes of retrying
apiece, plus a re-search carrying the forty-five minute search budget, and the
staffer watched it happen with nothing to show at the end.

One count for the run fixes that: the first client's cases prove the site is
gone and the rest of the list is marked and skipped without another request.
The cap moved from three to six at the same time, because three sealed cases in
a row is something a real client can have and losing nineteen other clients to
one person's bad patch is the worse failure.

Every name here is invented and every case number is 00000-shaped. This
repository is public.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import icos_sessions
import tasks
from icos import IcosError, IcosUnavailable


def ids(prefix, count):
    return ['00000 %s00000%d' % (prefix, n) for n in range(1, count + 1)]


class FakeJob:
    kind = 'batch_crs'
    cancelled = False

    def __init__(self):
        self.id = 'abcdef0123456789'
        self.progress = []
        self.result = None

    def log(self, message, count=None, total=None):
        self.progress.append(message)

    def said(self, fragment):
        return [line for line in self.progress if fragment in line]


class StubClient:
    """Answers searches and case requests, refusing whatever it was told to."""

    logged_in = True

    def __init__(self, dead_cases=(), dead_searches=(), court_site_down=False):
        self.dead_cases = set(dead_cases)
        self.dead_searches = set(dead_searches)
        # ICOS refusing with its own problem report page rather than simply
        # not answering. The client hands that out to the run as a flag on the
        # exception, so a stub that cannot set it cannot exercise the path.
        self.court_site_down = court_site_down
        self.searched = []
        self.asked = []

    def set_alert(self, alert):
        pass

    def set_log(self, log):
        pass

    def set_stop_check(self, should_stop):
        pass

    def login(self, username, password):
        pass

    def search(self, first, middle, last):
        self.searched.append(last)
        if last in self.dead_searches:
            if self.court_site_down:
                raise IcosUnavailable("Iowa Courts Online did not respond",
                                      court_site_down=True)
            raise IcosError("Iowa Courts did not answer.")
        return b'<results>'

    def case_bundle(self, case_id):
        self.asked.append(case_id)
        if case_id in self.dead_cases:
            raise IcosUnavailable("Iowa Courts Online did not return this case "
                                  "after 4 minutes of retrying",
                                  court_site_down=self.court_site_down)
        return b'<summary>', b'<charges>', b'<financials>'

    def logoff(self):
        pass


WRITTEN = []


def _fake_build(cases, name, dob, lite, failed=()):
    WRITTEN.append({'name': name, 'ids': [case['id'] for case in cases],
                   'failed': list(failed)})
    path = os.path.join(tasks.tmp_dir, 'test_outage.xlsx')
    with open(path, 'wb') as handle:
        handle.write(b'PK\x03\x04 stub workbook')
    return path, {}, {'balance': '$0.00', 'monthly': None, 'months': 12}


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    WRITTEN[:] = []
    monkeypatch.setattr(tasks.alerts, 'record', lambda *a, **k: None)
    monkeypatch.setattr(tasks.alerts, 'digest', lambda *a, **k: None)
    monkeypatch.setattr(tasks.case_parser, 'parse_case_summary',
                        lambda body, case: case.update(county='DUBUQUE'))
    monkeypatch.setattr(tasks.case_parser, 'parse_case_charges',
                        lambda body, case: case.update(charges=[]))
    monkeypatch.setattr(tasks.case_parser, 'parse_case_financials',
                        lambda body, case: case.update(financials=[],
                                                       total_due='$0.00'))
    monkeypatch.setattr(tasks, 'build_workbook', _fake_build)
    yield
    WRITTEN[:] = []


def person(surname):
    return {'first': 'PAT', 'middle': '', 'last': surname}


def pick(surname, case_ids):
    return {'def_name': '%s, PAT Q' % surname, 'def_dob': '01/01/1900',
            'person': person(surname), 'case_ids': list(case_ids)}


def run_list(monkeypatch, picks, dead_cases=(), dead_searches=(),
             court_site_down=False):
    stub = StubClient(dead_cases, dead_searches, court_site_down)
    monkeypatch.setattr(icos_sessions, 'claim', lambda token: stub)
    job = FakeJob()
    try:
        tasks.batch_crs_task(job, 'tok', picks, False)
    except ValueError:
        pass
    return job, stub


def run_retry(monkeypatch, entries, dead_cases=(), dead_searches=()):
    stub = StubClient(dead_cases, dead_searches)
    monkeypatch.setattr(tasks, 'IcosClient',
                        lambda log=None, alert=None, **kw: stub)
    job = FakeJob()
    payload = {'kind': 'batch_crs', 'is_lite': False, 'clients': entries}
    try:
        tasks.retry_task(job, 'ILATEST', 'secret', payload)
    except ValueError:
        pass
    return job, stub


class TestTheCounterItself:

    def test_a_fresh_one_is_not_an_outage(self):
        assert not tasks.Outage().over

    def test_it_trips_on_the_last_of_its_threshold_and_not_before(self):
        outage = tasks.Outage(threshold=3)
        assert outage.failed() is False
        assert outage.failed() is False
        assert outage.failed() is True
        assert outage.over

    def test_one_case_coming_back_clears_what_came_before(self):
        outage = tasks.Outage(threshold=3)
        outage.failed()
        outage.failed()
        outage.worked()
        assert outage.failed() is False
        assert not outage.over

    def test_the_default_is_six(self):
        """Named here because moving it is a decision about how long staff
        watch a dead site, not a tuning knob."""
        assert tasks.Outage().threshold == 6
        assert tasks.CASES_FAILED_IN_A_ROW_IS_AN_OUTAGE == 6


class TestWhenICOSSaysItItself:
    """Six is the price of not knowing. It is not owed when ICOS has said so.

    A problem report page is the court site reporting that its own data source
    is unreachable. Napier already told that apart from a request that never
    came back, but only in the alert: the run's counter treated the two the
    same and spent six cases at four minutes apiece, about twenty three
    minutes, rediscovering what the very first response had said in words. On
    the search side, where a name carries the forty-five minute budget, three
    of them is an hour and a half.

    Six exists to protect one client's sealed cases from ending the list for
    the nineteen names behind them. Sealed cases cannot produce a problem
    report page, so nothing about that is given up here.
    """

    def test_two_refusals_ICOS_declared_are_enough(self):
        outage = tasks.Outage()
        assert outage.failed(declared=True) is False
        assert outage.failed(declared=True) is True
        assert outage.over

    def test_a_refusal_that_says_nothing_still_costs_the_full_six(self):
        """The whole point of splitting them. If an ordinary refusal tripped at
        two, one client's bad patch would end a clinic list."""
        outage = tasks.Outage()
        for _ in range(5):
            assert outage.failed() is False
        assert not outage.over
        assert outage.failed() is True

    def test_a_case_coming_back_clears_the_declared_count_too(self):
        """Otherwise a site that blinked twice across a whole morning would
        stop a run that was working fine in between."""
        outage = tasks.Outage()
        outage.failed(declared=True)
        outage.worked()
        assert outage.failed(declared=True) is False
        assert not outage.over

    def test_the_number_is_named_and_is_below_both_others(self):
        """Named here because moving it is a decision about how long staff
        watch a site that has already said it is down."""
        assert tasks.ICOS_DECLARED_ITSELF_DOWN_IS_AN_OUTAGE == 2
        assert (tasks.ICOS_DECLARED_ITSELF_DOWN_IS_AN_OUTAGE
                < tasks.SEARCHES_FAILED_IN_A_ROW_IS_AN_OUTAGE
                < tasks.CASES_FAILED_IN_A_ROW_IS_AN_OUTAGE)

    def test_it_applies_to_the_search_counter_at_the_same_two(self):
        """A refused name costs the 45 minute search budget, so this saves more
        here than it does on cases."""
        searches = tasks.Outage(
            threshold=tasks.SEARCHES_FAILED_IN_A_ROW_IS_AN_OUTAGE)
        assert searches.failed(declared=True) is False
        assert searches.failed(declared=True) is True

    def test_a_clinic_list_stops_after_two_cases_not_six(self, monkeypatch):
        dead = ids('FECR', 8)
        job, stub = run_list(monkeypatch,
                             [pick('DOE', dead), pick('ROE', ids('SRCR', 4))],
                             dead_cases=dead, court_site_down=True)

        assert stub.asked == dead[:2], stub.asked
        assert stub.searched == ['DOE']

    def test_the_same_list_without_the_page_still_costs_six(self, monkeypatch):
        """The control. Same stub, same refusals, one flag different."""
        dead = ids('FECR', 8)
        job, stub = run_list(monkeypatch,
                             [pick('DOE', dead), pick('ROE', ids('SRCR', 4))],
                             dead_cases=dead, court_site_down=False)

        assert stub.asked == dead[:6], stub.asked

    def test_staff_are_told_the_court_site_said_so(self, monkeypatch):
        """A staffer watching a run die wants to know whether it is their
        account, their password or their laptop. When ICOS has answered that
        question, passing the answer on costs nothing."""
        dead = ids('FECR', 8)
        job, _ = run_list(monkeypatch,
                          [pick('DOE', dead), pick('ROE', ids('SRCR', 4))],
                          dead_cases=dead, court_site_down=True)

        assert job.said('reported that its own system is unavailable')
        assert not job.said('Iowa Courts stopped responding')

    def test_a_client_nobody_reached_says_the_same_thing(self, monkeypatch):
        """The finish page outlives the progress log, so the row for a client
        who was never pulled has to carry it too."""
        doe, roe = ids('FECR', 8), ids('SRCR', 4)
        job, _ = run_list(monkeypatch, [pick('DOE', doe), pick('ROE', roe)],
                          dead_cases=doe[2:], court_site_down=True)

        skipped = job.result['clients'][1]
        assert skipped['failed'] == roe
        assert 'its own system was unavailable' in skipped['error'], skipped

    def test_a_batch_search_stops_after_two_names(self, monkeypatch):
        names = ['DOE', 'ROE', 'POE', 'MOE']
        stub = StubClient(dead_searches=names, court_site_down=True)
        monkeypatch.setattr(tasks, 'IcosClient',
                            lambda log=None, alert=None, **kw: stub)
        monkeypatch.setattr(tasks.icos_sessions, 'put', lambda client: 'tok')
        monkeypatch.setattr(tasks.case_parser, 'parse_search',
                            lambda body: ([], False))
        job = FakeJob()
        tasks.batch_search_task(job, 'ILATEST', 'secret',
                               [person(n) for n in names])

        assert stub.searched == names[:2], stub.searched
        assert job.said('reported that its own system is unavailable')


class TestAClinicListMeetingADeadSite:

    def test_the_rest_of_the_list_is_not_pulled(self, monkeypatch):
        first = ids('FECR', 6)
        rest = [pick('ROE', ids('SRCR', 4)), pick('POE', ids('SMCR', 4))]
        job, stub = run_list(monkeypatch, [pick('DOE', first)] + rest,
                             dead_cases=first + ids('SRCR', 4) + ids('SMCR', 4))

        # Six refusals on the first client is the whole budget, so nobody
        # behind them is asked for at all.
        assert stub.asked == first
        assert job.said('stopped responding')

    def test_the_rest_of_the_list_is_not_searched_either(self, monkeypatch):
        """The re-search is the expensive part. It carries the 45 minute search
        budget, so skipping the cases while still asking ICOS for every
        remaining name would give back none of what this saves."""
        first = ids('FECR', 6)
        job, stub = run_list(monkeypatch,
                             [pick('DOE', first), pick('ROE', ids('SRCR', 4))],
                             dead_cases=first)

        assert stub.searched == ['DOE']

    def test_the_skipped_clients_are_named_as_missing(self, monkeypatch):
        """The finish page lists every case that is not in the workbook. A
        client nobody got as far as has all of theirs missing, and saying so is
        the difference between a short list and a short list that says so."""
        first = ids('FECR', 8)
        later = ids('SRCR', 4)
        job, _ = run_list(monkeypatch,
                          [pick('DOE', first), pick('ROE', later)],
                          dead_cases=first[2:])

        assert job.result is not None
        skipped = job.result['clients'][1]
        assert skipped['failed'] == later
        assert skipped['written'] == 0
        assert 'stopped responding' in skipped['error']

    def test_a_client_who_came_back_is_still_built(self, monkeypatch):
        """The list stopping is not the list being thrown away."""
        good = ids('FECR', 2)
        dead = ids('SRCR', 6)
        job, _ = run_list(monkeypatch,
                          [pick('DOE', good), pick('ROE', dead),
                           pick('POE', ids('SMCR', 3))],
                          dead_cases=dead)

        assert [entry['ids'] for entry in WRITTEN] == [good]
        assert job.result['clients'][0]['written'] == 2

    def test_what_was_skipped_can_be_tried_again(self, monkeypatch):
        """A skipped client is a client with failures, which is what the retry
        button reads. Marking them as skipped without that would leave staff
        the hand lookup this whole feature exists to avoid."""
        good = ids('FECR', 2)
        dead = ids('SRCR', 6)
        later = ids('SMCR', 3)
        job, _ = run_list(monkeypatch,
                          [pick('DOE', good), pick('ROE', dead),
                           pick('POE', later)],
                          dead_cases=dead)

        payload = job.result['retry']
        assert payload is not None
        missing = {entry['def_name']: entry['failed']
                   for entry in payload['clients']}
        assert missing['ROE, PAT Q'] == dead
        assert missing['POE, PAT Q'] == later

    def test_the_list_is_told_once_and_not_per_client(self, monkeypatch):
        dead = ids('FECR', 6)
        job, _ = run_list(monkeypatch,
                          [pick('DOE', dead), pick('ROE', ids('SRCR', 2)),
                           pick('POE', ids('SMCR', 2)),
                           pick('MOE', ids('CVCV', 2))],
                          dead_cases=dead)

        assert len(job.said('rest of the list')) == 1

    def test_each_client_workbook_is_told_its_own_missing_cases(self, monkeypatch):
        """A clinic list builds twenty files that go twenty different places.
        Handing every one of them the whole run's missing cases would put
        another client's case numbers in a workbook, and handing them none
        leaves each file quietly short."""
        doe, roe = ids('FECR', 3), ids('SRCR', 3)
        job, _ = run_list(monkeypatch, [pick('DOE', doe), pick('ROE', roe)],
                          dead_cases=[doe[1], roe[2]])

        assert [entry['failed'] for entry in WRITTEN] == [[doe[1]], [roe[2]]]

    def test_a_bumpy_list_still_finishes(self, monkeypatch):
        """Two refusals apiece across four clients is eight failures and no
        outage, because none of them ran six deep with nothing in between.
        Under a per client count this was already true; under a shared one it
        has to stay true, or every long list ends early."""
        picks, dead = [], []
        for surname, prefix in [('DOE', 'FECR'), ('ROE', 'SRCR'),
                                ('POE', 'SMCR'), ('MOE', 'CVCV')]:
            case_ids = ids(prefix, 4)
            dead.extend(case_ids[:2])
            picks.append(pick(surname, case_ids))
        job, stub = run_list(monkeypatch, picks, dead_cases=dead)

        assert stub.searched == ['DOE', 'ROE', 'POE', 'MOE']
        assert len(stub.asked) == 16
        assert len(WRITTEN) == 4

    def test_refusals_carry_across_the_boundary_between_clients(self, monkeypatch):
        """Three dead at the end of one client and three at the start of the
        next is six in a row. A per client count never sees it; that is the
        whole reason this moved."""
        doe = ids('FECR', 4)
        roe = ids('SRCR', 4)
        poe = ids('SMCR', 3)
        job, stub = run_list(
            monkeypatch,
            [pick('DOE', doe), pick('ROE', roe), pick('POE', poe)],
            dead_cases=doe[1:] + roe[:3])

        # DOE's first case came back and cleared the count. The three after it
        # plus ROE's first three make six, so ROE's fourth is never asked for
        # and POE is not searched.
        assert stub.asked == doe + roe[:3]
        assert stub.searched == ['DOE', 'ROE']


class TestTheDoorTheOutageComesInFirst:
    """A client's turn on a clinic list starts with the re-search.

    So a site that is properly down refuses the re-search before it ever gets
    asked for a case, and a count of refused cases alone never gets going. The
    run then spends the 45 minute search budget on every remaining name, which
    is the exact bill the case count exists to stop.
    """

    def test_three_names_refused_in_a_row_ends_the_list(self, monkeypatch):
        good = ids('FECR', 2)
        picks = [pick('DOE', good)] + [pick(surname, ids(prefix, 3))
                                       for surname, prefix
                                       in [('ROE', 'SRCR'), ('POE', 'SMCR'),
                                           ('MOE', 'CVCV'), ('NOE', 'AGCR'),
                                           ('LOE', 'OWCR')]]
        job, stub = run_list(monkeypatch, picks,
                             dead_searches=['ROE', 'POE', 'MOE', 'NOE', 'LOE'])

        assert stub.searched == ['DOE', 'ROE', 'POE', 'MOE']
        assert job.said('rest of the list was not pulled')

    def test_a_name_answering_in_between_clears_the_count(self, monkeypatch):
        """Two names Iowa Courts will not answer either side of one it will is
        not an outage. It is what a clinic list has always looked like."""
        picks = [pick(surname, ids(prefix, 2)) for surname, prefix
                 in [('DOE', 'FECR'), ('ROE', 'SRCR'), ('POE', 'SMCR'),
                     ('MOE', 'CVCV'), ('NOE', 'AGCR')]]
        job, stub = run_list(monkeypatch, picks,
                             dead_searches=['DOE', 'ROE', 'MOE', 'NOE'])

        assert stub.searched == ['DOE', 'ROE', 'POE', 'MOE', 'NOE']
        assert not job.said('rest of the list was not pulled')

    def retry_entries(self, surnames):
        entries = []
        for surname, prefix in surnames:
            case_ids = ids(prefix, 2)
            entries.append(tasks._retry_entry(
                '%s, PAT Q' % surname, '01/01/1900', person(surname),
                case_ids, [{'id': case_ids[0]}], case_ids[1:]))
        return entries

    def test_a_retry_stops_on_them_too(self, monkeypatch):
        entries = self.retry_entries([('DOE', 'FECR'), ('ROE', 'SRCR'),
                                      ('POE', 'SMCR'), ('MOE', 'CVCV'),
                                      ('NOE', 'AGCR')])
        job, stub = run_retry(monkeypatch, entries,
                              dead_searches=['DOE', 'ROE', 'POE', 'MOE', 'NOE'])

        assert stub.searched == ['DOE', 'ROE', 'POE']
        assert job.said('not tried again')

    def test_a_retry_clears_the_count_on_a_name_that_answers(self, monkeypatch):
        """Same rule as the first run. A retry is often started because Iowa
        Courts was flaky, so names that answer in between are the normal case
        and must not be spent toward the cap."""
        entries = self.retry_entries([('DOE', 'FECR'), ('ROE', 'SRCR'),
                                      ('POE', 'SMCR'), ('MOE', 'CVCV'),
                                      ('NOE', 'AGCR'), ('LOE', 'OWCR')])
        job, stub = run_retry(monkeypatch, entries,
                              dead_searches=['DOE', 'POE', 'MOE', 'NOE', 'LOE'])

        # ROE answering puts the count back to nought, so POE, MOE and NOE are
        # the three in a row and NOE is still reached.
        assert stub.searched == ['DOE', 'ROE', 'POE', 'MOE', 'NOE']
        assert job.said('not tried again')


class TestTheSearchSideCount:
    """Names are counted on their own and still stop at three.

    A name Iowa Courts will not answer costs the 45 minute search budget rather
    than a case's four minutes, so three of them is already most of an
    afternoon. They also do not come in runs the way one client's sealed cases
    do, which is the reason cases moved to six and this did not.
    """

    def run_search(self, monkeypatch, people, dead_searches=()):
        stub = StubClient(dead_searches=dead_searches)
        monkeypatch.setattr(tasks, 'IcosClient',
                            lambda log=None, alert=None, **kw: stub)
        monkeypatch.setattr(tasks.icos_sessions, 'put', lambda client: 'tok')
        monkeypatch.setattr(tasks.case_parser, 'parse_search',
                            lambda body: ([], False))
        job = FakeJob()
        tasks.batch_search_task(job, 'ILATEST', 'secret', people)
        return job, stub

    def test_three_names_in_a_row_ends_the_list(self, monkeypatch):
        names = ['DOE', 'ROE', 'POE', 'MOE', 'NOE']
        job, stub = self.run_search(monkeypatch, [person(n) for n in names],
                                    dead_searches=names)

        assert stub.searched == names[:3]
        assert job.said('rest of the list was not searched')

    def test_two_names_in_a_row_does_not(self, monkeypatch):
        names = ['DOE', 'ROE', 'POE', 'MOE']
        job, stub = self.run_search(monkeypatch, [person(n) for n in names],
                                    dead_searches=names[:2])

        assert stub.searched == names
        assert not job.said('rest of the list was not searched')

    def test_the_two_counts_are_separate_numbers(self, monkeypatch):
        assert (tasks.SEARCHES_FAILED_IN_A_ROW_IS_AN_OUTAGE
                != tasks.CASES_FAILED_IN_A_ROW_IS_AN_OUTAGE)
        assert tasks.SEARCHES_FAILED_IN_A_ROW_IS_AN_OUTAGE == 3


class TestARetryMeetingADeadSite:

    def entries(self):
        doe, roe, poe = ids('FECR', 8), ids('SRCR', 4), ids('SMCR', 4)
        return [
            tasks._retry_entry('DOE, PAT Q', '01/01/1900', person('DOE'),
                               doe, [{'id': doe[0]}], doe[1:]),
            tasks._retry_entry('ROE, PAT Q', '01/01/1900', person('ROE'),
                               roe, [], roe),
            tasks._retry_entry('POE, PAT Q', '01/01/1900', person('POE'),
                               poe, [], poe),
        ]

    def test_the_rest_of_the_missing_cases_are_left_alone(self, monkeypatch):
        entries = self.entries()
        dead = entries[0]['failed'] + entries[1]['failed'] + entries[2]['failed']
        job, stub = run_retry(monkeypatch, entries, dead_cases=dead)

        assert stub.asked == entries[0]['failed'][:6]
        assert stub.searched == ['DOE']
        assert job.said('not tried again')

    def test_what_the_first_run_got_is_still_rebuilt(self, monkeypatch):
        """A retry that meets a dead site must still hand back the workbook the
        first run earned. Otherwise pressing the button is a way to lose work."""
        entries = self.entries()
        dead = entries[0]['failed'] + entries[1]['failed'] + entries[2]['failed']
        job, _ = run_retry(monkeypatch, entries, dead_cases=dead)

        assert [entry['ids'] for entry in WRITTEN] == [[entries[0]['case_ids'][0]]]

    def test_the_rebuilt_workbook_is_told_what_is_still_missing(self, monkeypatch):
        """A retry rebuilds the file from everything ever recovered, so the
        line it carried from the first run is stale the moment it is rebuilt.
        Recovering one of three has to leave the other two named, and a retry
        that recovered everything has to stop saying anything is missing."""
        entries = self.entries()
        dead = entries[0]['failed'] + entries[1]['failed'] + entries[2]['failed']
        run_retry(monkeypatch, entries, dead_cases=dead)

        assert [entry['failed'] for entry in WRITTEN] == [entries[0]['failed']]

    def test_and_says_nothing_is_missing_once_it_all_came_back(self, monkeypatch):
        entries = self.entries()
        run_retry(monkeypatch, entries[:1])

        assert [entry['failed'] for entry in WRITTEN] == [[]]

    def test_the_skipped_ones_are_still_offered_a_third_go(self, monkeypatch):
        entries = self.entries()
        dead = entries[0]['failed'] + entries[1]['failed'] + entries[2]['failed']
        job, _ = run_retry(monkeypatch, entries, dead_cases=dead)

        missing = {entry['def_name']: entry['failed']
                   for entry in job.result['retry']['clients']}
        assert missing['ROE, PAT Q'] == entries[1]['case_ids']
        assert missing['POE, PAT Q'] == entries[2]['case_ids']

    def test_a_client_skipped_with_nothing_saved_says_why(self, monkeypatch):
        entries = self.entries()
        dead = entries[0]['failed'] + entries[1]['failed'] + entries[2]['failed']
        job, _ = run_retry(monkeypatch, entries, dead_cases=dead)

        assert 'stopped responding' in job.result['clients'][1]['error']

    def test_a_retry_that_works_is_not_stopped(self, monkeypatch):
        entries = self.entries()
        job, stub = run_retry(monkeypatch, entries)

        assert stub.searched == ['DOE', 'ROE', 'POE']
        assert len(WRITTEN) == 3
        assert not job.said('not tried again')

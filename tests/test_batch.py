"""A clinic list end to end: paste, search, pick, build, download.

Wired through the real routes with a fake court site, because the throughput
this exists to fix is spread across four of them and one background job, and
any of them can drop a client on the floor without anything raising.

The names are invented and every case number is 00000-shaped. The repository is
public and a real Iowa case number attached to a real name is a person.
"""

import os
import sys
import time
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['NAPIER_DISABLE_BACKGROUND'] = '1'

import app as app_module
import jobs
import tasks
from icos import IcosError

ROSTER = "Doe, Jane\nRoe, John"


def results_for(surname, given, cases, dob="01/01/1900"):
    rows = "".join(
        '<tr><td>%s</td><td></td><td>STATE VS %s</td><td>%s, %s</td>'
        '<td>%s</td><td>DEFENDANT</td></tr>' % (case, surname, surname, given,
                                                dob)
        for case in cases)
    return ("<html><table>"
            "<tr><td>Case ID</td><td></td><td>Title</td><td>Name</td>"
            "<td>DOB</td><td>Role</td></tr>%s</table></html>" % rows).encode()


# One client with two cases, one with one, so a wrong slice of the case list
# shows up as a count rather than passing quietly.
PEOPLE = {
    'DOE': results_for('DOE', 'JANE', ['00000 FECR000000', '00000 SRCR000000']),
    'ROE': results_for('ROE', 'JOHN', ['00000 SMCR000000']),
}


class FakeClient:
    """Stands in for IcosClient and records the session lifecycle."""

    instances = []
    search_error = None
    fail_searches = ()      # 1-based positions in the list that Iowa Courts refuses
    # A line the real client writes from inside its retry loop. Opt-in, because
    # only the test that cares where those notices land wants the noise.
    retry_notice = None

    def __init__(self, log=None, alert=None, **kwargs):
        self.log = log or (lambda m, **kw: None)
        self.alert = alert
        self.should_stop = lambda: False
        self.logged_off = False
        self.searched = []
        self.cases = []
        # Searches and case pulls in the order they happened. Kept together
        # because the thing worth asserting is which came before which.
        self.trace = []
        self.fail_cases = []
        FakeClient.instances.append(self)

    def set_alert(self, alert):
        self.alert = alert

    def set_log(self, log):
        # Recorded rather than ignored: the retry notices going to the job
        # nobody is watching is exactly the bug this stub has to be able to see.
        self.log = log or (lambda m, **kw: None)

    def set_stop_check(self, should_stop):
        self.should_stop = should_stop or (lambda: False)

    def login(self, username, password):
        self.log("Signed in to Iowa Courts Online.")

    def search(self, first, middle, last):
        self.searched.append((first, middle, last))
        self.trace.append(('search', last))
        if FakeClient.search_error:
            raise FakeClient.search_error
        if len(self.searched) in FakeClient.fail_searches:
            raise IcosError("Iowa Courts did not answer.")
        return PEOPLE.get(last.upper(), results_for(last.upper(), 'X', []))

    def case_bundle(self, case_id):
        self.cases.append(case_id)
        self.trace.append(('case', case_id))
        if FakeClient.retry_notice:
            self.log(FakeClient.retry_notice)
        if case_id in self.fail_cases:
            raise IcosError("Iowa Courts did not answer for this case.")
        return b"<summary>", b"<charges>", b"<financials>"

    def logoff(self):
        self.logged_off = True


@pytest.fixture(autouse=True)
def fake_icos(monkeypatch):
    FakeClient.instances = []
    FakeClient.search_error = None
    FakeClient.fail_searches = ()
    FakeClient.retry_notice = None
    monkeypatch.setattr(tasks, 'IcosClient', FakeClient)
    monkeypatch.setattr(tasks.case_parser, 'parse_case_summary',
                        lambda html, case: case.update(county='DUBUQUE'))
    monkeypatch.setattr(tasks.case_parser, 'parse_case_charges',
                        lambda html, case: case.update(charges=[]))
    monkeypatch.setattr(tasks.case_parser, 'parse_case_financials',
                        lambda html, case: case.update(financials=[],
                                                       total_due='$0.00'))
    monkeypatch.setattr(tasks, 'build_workbook',
                        lambda cases, name, dob, lite: (_stub_workbook(name), {}))
    yield


def _stub_workbook(name):
    path = os.path.join(tasks.tmp_dir,
                        'test_stub_%s.xlsx' % name.split(',')[0].strip())
    with open(path, 'wb') as f:
        f.write(b'PK\x03\x04 stub workbook')
    return path


@pytest.fixture
def client():
    app_module.app.config['TESTING'] = True
    app_module.app.secret_key = 'test'
    return app_module.app.test_client()


def await_job(client, job_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get('/job/' + job_id).get_json()
        if state['done']:
            return state
        time.sleep(0.01)
    raise AssertionError("job never finished")


def run_batch(client, roster=ROSTER, username='ILATEST', lite=False):
    form = {'username': username, 'password': 'secret', 'roster': roster}
    if lite:
        form['isLiteBatch'] = 'on'
    response = client.post('/batch', data=form)
    if response.status_code != 302:
        return None, response
    job_id = response.headers['Location'].rsplit('/', 1)[-1]
    return job_id, await_job(client, job_id)


def pick_all(client, search_job_id, state=None):
    """Tick every match the search came back with, the way staff would."""
    client.get('/roster/' + search_job_id)
    result = jobs.get(search_job_id).result
    picks = [{'client': index, 'keys': entry['keys']}
             for index, entry in enumerate(result['clients']) if entry['keys']]
    return client.post('/batch-crs', json={'search_job_id': search_job_id,
                                           'picks': picks})


def build_a_list(client, roster=ROSTER):
    search_job_id, _ = run_batch(client, roster)
    response = pick_all(client, search_job_id)
    job_id = response.get_json()['job_id']
    return job_id, await_job(client, job_id)


class TestSearchingTheList:
    def test_the_whole_list_is_searched_on_one_sign_in(self, client):
        """The point of the feature. Twenty clients used to be twenty sign ins
        queueing for one shared Iowa Courts account."""
        search_job_id, state = run_batch(client)
        assert state['status'] == 'done'
        assert len(FakeClient.instances) == 1
        assert FakeClient.instances[0].searched == [
            ('Jane', '', 'Doe'), ('John', '', 'Roe')]

    def test_it_hands_back_a_page_at_once(self, client):
        started = time.time()
        response = client.post('/batch', data={
            'username': 'ILATEST', 'password': 'secret', 'roster': ROSTER})
        assert time.time() - started < 1
        assert '/progress/' in response.headers['Location']

    def test_the_roster_page_lists_every_client_and_their_matches(self, client):
        search_job_id, _ = run_batch(client)
        page = client.get('/roster/' + search_job_id).get_data(as_text=True)
        assert 'DOE, JANE' in page
        assert 'ROE, JOHN' in page
        assert '2 cases' in page
        assert '1 case' in page

    def test_no_client_name_reaches_the_progress_log(self, client):
        """The log is quoted into alert email and these names are privileged.
        A position in the list is not."""
        _, state = run_batch(client)
        joined = " ".join(state['progress']).upper()
        assert 'JANE' not in joined
        assert 'DOE' not in joined
        assert 'name 1 of 2' in " ".join(state['progress'])

    def test_a_name_iowa_courts_will_not_answer_does_not_end_the_list(self, client):
        FakeClient.fail_searches = (1,)  # the first name, not the second
        search_job_id, state = run_batch(client, "Doe, Jane\nRoe, John")
        assert state['status'] == 'done'
        entries = jobs.get(search_job_id).result['clients']
        assert entries[0]['error']
        assert entries[1]['keys']  # the rest of the clinic is still in the room

    def test_a_client_with_no_cases_says_so_rather_than_showing_blank(self, client):
        search_job_id, _ = run_batch(client, "Nobody, Sam")
        page = client.get('/roster/' + search_job_id).get_data(as_text=True)
        assert 'no cases under that name' in page

    def test_an_unreadable_line_is_reported_on_the_roster_page(self, client):
        search_job_id, _ = run_batch(client, "Client Name\nDoe, Jane")
        page = client.get('/roster/' + search_job_id).get_data(as_text=True)
        assert 'not searched' in page
        assert 'Client Name' in page

    def test_an_empty_list_is_refused_before_anyone_signs_in(self, client):
        _, response = run_batch(client, "\n\n")
        assert response.status_code == 200
        assert 'no names in that list' in response.get_data(as_text=True)
        assert FakeClient.instances == []

    def test_a_list_longer_than_the_cap_is_refused_with_the_number(self, client):
        long_list = "\n".join("Doe%d, Jane" % n for n in range(50))
        _, response = run_batch(client, long_list)
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert '50 names' in body
        assert 'Split the list' in body
        assert FakeClient.instances == []

    def test_a_non_legal_aid_user_id_is_refused(self, client):
        _, response = run_batch(client, username='someoneelse')
        assert 'not an Iowa Legal Aid' in response.get_data(as_text=True)
        assert FakeClient.instances == []

    def test_a_failed_search_leaves_no_session_holding_the_account(self, client):
        FakeClient.search_error = RuntimeError("boom")
        _, state = run_batch(client)
        # Every name failed, so the run gave up, and the shared account has to
        # come back either way.
        assert FakeClient.instances[0].logged_off is True


class TestBuildingTheWorkbooks:
    def test_every_picked_client_gets_a_workbook(self, client):
        job_id, state = build_a_list(client)
        assert state['status'] == 'done'
        assert state['next_url'] == '/batch-done/%s' % job_id
        built = jobs.get(job_id).result['clients']
        assert [record['name'] for record in built] == ['DOE, JANE', 'ROE, JOHN']
        assert [record['written'] for record in built] == [2, 1]

    def test_the_cases_pulled_are_that_client_s_own(self, client):
        build_a_list(client)
        assert FakeClient.instances[0].cases == [
            '00000 FECR000000', '00000 SRCR000000', '00000 SMCR000000']

    def test_the_bar_counts_cases_across_the_whole_list(self, client):
        _, state = build_a_list(client)
        assert state['total'] == 3
        # The third case belongs to the second client; without the running
        # offset the bar would slide back to 1 of 3 when that client started.
        assert any('Pulling case 3 of 3' in line for line in state['progress'])

    def test_one_client_failing_costs_only_that_client(self, client):
        search_job_id, _ = run_batch(client)
        client.get('/roster/' + search_job_id)
        FakeClient.instances[0].fail_cases = ['00000 SMCR000000']
        response = pick_all(client, search_job_id)
        state = await_job(client, response.get_json()['job_id'])

        assert state['status'] == 'done'
        built = jobs.get(response.get_json()['job_id']).result['clients']
        assert built[0]['written'] == 2       # the clinic still gets this one
        assert built[1]['file'] is None
        assert built[1]['error']

    def test_a_case_that_would_not_come_off_is_named_on_the_finish_page(self, client):
        search_job_id, _ = run_batch(client)
        client.get('/roster/' + search_job_id)
        FakeClient.instances[0].fail_cases = ['00000 SRCR000000']
        response = pick_all(client, search_job_id)
        job_id = response.get_json()['job_id']
        await_job(client, job_id)

        page = client.get('/batch-done/' + job_id).get_data(as_text=True)
        assert '00000 SRCR000000' in page
        assert '1 of 2 cases' in page

    def test_a_client_left_unticked_is_skipped(self, client):
        search_job_id, _ = run_batch(client)
        client.get('/roster/' + search_job_id)
        entry = jobs.get(search_job_id).result['clients'][0]
        response = client.post('/batch-crs', json={
            'search_job_id': search_job_id,
            'picks': [{'client': 0, 'keys': entry['keys']}]})
        job_id = response.get_json()['job_id']
        await_job(client, job_id)
        assert len(jobs.get(job_id).result['clients']) == 1

    def test_the_run_always_releases_the_shared_account(self, client):
        build_a_list(client)
        assert FakeClient.instances[0].logged_off is True

    def test_a_list_where_nothing_could_be_built_fails_plainly(self, client):
        search_job_id, _ = run_batch(client)
        client.get('/roster/' + search_job_id)
        FakeClient.instances[0].fail_cases = [
            '00000 FECR000000', '00000 SRCR000000', '00000 SMCR000000']
        response = pick_all(client, search_job_id)
        state = await_job(client, response.get_json()['job_id'])
        assert state['status'] == 'failed'


class TestPickingIsChecked:
    def test_a_defendant_nobody_picked_off_the_page_is_ignored(self, client):
        """The browser sends the keys. A key the search never returned would put
        a stranger's convictions in a client's workbook."""
        search_job_id, _ = run_batch(client)
        client.get('/roster/' + search_job_id)
        response = client.post('/batch-crs', json={
            'search_job_id': search_job_id,
            'picks': [{'client': 0, 'keys': ['1900-01-01 SOMEONE, ELSE']}]})
        assert response.status_code == 400

    def test_a_client_index_off_the_end_is_refused(self, client):
        search_job_id, _ = run_batch(client)
        client.get('/roster/' + search_job_id)
        response = client.post('/batch-crs', json={
            'search_job_id': search_job_id,
            'picks': [{'client': 99, 'keys': ['x']}]})
        assert response.status_code == 400

    def test_nothing_ticked_is_refused(self, client):
        search_job_id, _ = run_batch(client)
        client.get('/roster/' + search_job_id)
        response = client.post('/batch-crs', json={
            'search_job_id': search_job_id, 'picks': []})
        assert response.status_code == 400

    def test_another_browser_cannot_start_a_run_off_this_list(self, client):
        search_job_id, _ = run_batch(client)
        client.get('/roster/' + search_job_id)
        other = app_module.app.test_client()
        assert other.post('/batch-crs', json={
            'search_job_id': search_job_id, 'picks': []}).status_code == 410

    def test_another_browser_cannot_read_the_roster_page(self, client):
        search_job_id, _ = run_batch(client)
        other = app_module.app.test_client()
        page = other.get('/roster/' + search_job_id).get_data(as_text=True)
        assert 'DOE, JANE' not in page


class TestCollectingTheFiles:
    def test_the_zip_holds_one_workbook_per_client(self, client):
        job_id, _ = build_a_list(client)
        response = client.get('/batch/%s/download' % job_id)
        assert response.status_code == 200

        path = jobs.get(job_id).result['file']
        with zipfile.ZipFile(path) as bundle:
            entries = bundle.namelist()
        assert len(entries) == 2
        # Numbered, because two clients on one clinic list can share a name and
        # a zip that quietly holds one of them is how the wrong person ends up
        # sitting there.
        assert entries[0].startswith('01_')
        assert entries[1].startswith('02_')
        assert 'DOE' in entries[0]

    def test_one_client_can_be_downloaded_on_their_own(self, client):
        job_id, _ = build_a_list(client)
        response = client.get('/batch/%s/download/1' % job_id)
        assert response.status_code == 200
        assert 'ROE' in response.headers['Content-Disposition']

    def test_taking_one_file_does_not_end_the_errand(self, client):
        """The list is the errand. A staffer who grabs one client and closes the
        tab should still be chased about the other nineteen."""
        job_id, _ = build_a_list(client)
        client.get('/batch/%s/download/0' % job_id)
        assert jobs.get(job_id).collected is False
        assert 'workbook waiting' in client.get('/').get_data(as_text=True)

    def test_taking_the_zip_ends_it(self, client):
        job_id, _ = build_a_list(client)
        client.get('/batch/%s/download' % job_id)
        assert jobs.get(job_id).collected is True
        assert 'workbook waiting' not in client.get('/').get_data(as_text=True)

    def test_an_uncollected_clinic_list_is_alerted_on_like_any_workbook(self, client):
        job_id, _ = build_a_list(client)
        job = jobs.get(job_id)
        job.updated_at -= jobs.UNCOLLECTED_AFTER + 1
        assert jobs._uncollected_pass() == 1

    def test_the_start_page_offers_the_list_back(self, client):
        job_id, _ = build_a_list(client)
        page = client.get('/').get_data(as_text=True)
        assert '/batch-done/%s' % job_id in page

    def test_a_download_path_outside_tmp_is_refused(self, client):
        job_id, _ = build_a_list(client)
        jobs.get(job_id).result['file'] = '/etc/passwd'
        body = client.get('/batch/%s/download' % job_id).get_data(as_text=True)
        assert 'invalid file' in body

    def test_a_client_download_path_outside_tmp_is_refused(self, client):
        job_id, _ = build_a_list(client)
        jobs.get(job_id).result['clients'][0]['file'] = '/etc/passwd'
        body = client.get('/batch/%s/download/0' % job_id).get_data(as_text=True)
        assert 'invalid file' in body

    def test_another_browser_cannot_take_the_files(self, client):
        job_id, _ = build_a_list(client)
        other = app_module.app.test_client()
        assert other.get('/batch/%s/download' % job_id).status_code == 200
        assert 'Napier restarted' in other.get(
            '/batch/%s/download' % job_id).get_data(as_text=True)


class TestLite:
    def test_the_lite_choice_applies_to_every_client(self, client):
        search_job_id, _ = run_batch(client, lite=True)
        response = pick_all(client, search_job_id)
        job_id = response.get_json()['job_id']
        await_job(client, job_id)
        assert jobs.get(job_id).result['is_lite'] is True
        with zipfile.ZipFile(jobs.get(job_id).result['file']) as bundle:
            assert all('Lite' in name for name in bundle.namelist())


class TestNamesStayOffTheBrowser:
    def test_a_line_napier_could_not_read_is_not_kept_in_the_session(self, client):
        """A rejected line can hold part of a client's name. The session cookie
        is a store on a machine other people use, so it lives on the job."""
        run_batch(client, ", Jane Q Public\nRoe, John")
        with client.session_transaction() as browser_session:
            held = " ".join(str(value) for value in browser_session.values())
        assert held                      # there is a session, so this is a real look
        assert 'Public' not in held

    def test_it_still_reaches_the_page(self, client):
        search_job_id, _ = run_batch(client, "Client Name\nDoe, Jane")
        assert 'Client Name' in client.get(
            '/roster/' + search_job_id).get_data(as_text=True)


class TestEachClientsCasesComeFromTheirOwnSearch:
    """The failure that made a real clinic run look hung.

    ICOS decides which case a case request means from whatever it answered
    last, not from the case number in the request. A two-phase run searches
    every name and then pulls every case, so by pull time ICOS is standing on
    the last name searched and every earlier client's cases come back as a
    stub: right heading, no charges, no money. The validators refuse it, the
    case retries for its full four minute budget, and the progress page sits on
    "Pulling case 1 of 67" with nothing under it.
    """

    def test_a_clients_search_is_repeated_immediately_before_their_cases(self, client):
        search_job_id, _ = run_batch(client)
        response = pick_all(client, search_job_id)
        await_job(client, response.get_json()['job_id'])
        assert FakeClient.instances[0].trace == [
            ('search', 'Doe'), ('search', 'Roe'),          # the roster search
            ('search', 'Doe'), ('case', '00000 FECR000000'),
            ('case', '00000 SRCR000000'),
            ('search', 'Roe'), ('case', '00000 SMCR000000'),
        ]

    def test_the_search_terms_come_off_the_search_job_and_not_the_browser(self, client):
        """A name posted by the browser would be somebody the staffer never saw
        on the roster page, and their cases would go in a client's file."""
        search_job_id, _ = run_batch(client)
        client.get('/roster/' + search_job_id)
        result = jobs.get(search_job_id).result
        picks = [{'client': index, 'keys': entry['keys'], 'person':
                  {'first': 'Someone', 'middle': '', 'last': 'Else'}}
                 for index, entry in enumerate(result['clients']) if entry['keys']]
        response = client.post('/batch-crs', json={'search_job_id': search_job_id,
                                                   'picks': picks})
        await_job(client, response.get_json()['job_id'])
        trace = FakeClient.instances[0].trace
        assert ('search', 'Else') not in trace
        # And the re-search did happen, or the line above passes by never
        # having searched anybody a second time at all.
        assert trace.count(('search', 'Doe')) == 2

    def test_a_name_that_will_not_answer_twice_costs_that_client_and_not_the_list(
            self, client):
        search_job_id, _ = run_batch(client)
        FakeClient.fail_searches = (3,)    # Doe's re-search, not the roster pass
        response = pick_all(client, search_job_id)
        job_id = response.get_json()['job_id']
        state = await_job(client, job_id)
        assert state['status'] == 'done'
        built = jobs.get(job_id).result['clients']
        assert 'a second time' in built[0]['error']
        assert built[1]['written'] == 1     # the rest of the clinic still gets theirs

    def test_the_count_still_reaches_the_end_when_a_client_is_skipped(self, client):
        """The bar measures against every case on the list, so a skipped client
        has to be counted past or a finished run reads as stalled at 1 of 3."""
        search_job_id, _ = run_batch(client)
        FakeClient.fail_searches = (3,)
        response = pick_all(client, search_job_id)
        state = await_job(client, response.get_json()['job_id'])
        assert state['count'] == state['total'] == 3


class TestTheProgressPageShowsTheRunItIsWatching:
    def test_retry_notices_land_in_the_job_the_staffer_is_watching(self, client):
        """The second half of the hang. The CRS run rebound alerting to itself
        but not the log, so "Iowa Courts is slow, retrying" was written into
        the search job, which no page is showing by then. Four minutes of
        retrying looked like four minutes of nothing."""
        FakeClient.retry_notice = "Iowa Courts is slow, retrying (attempt 6)..."
        search_job_id, _ = run_batch(client)
        response = pick_all(client, search_job_id)
        crs_job_id = response.get_json()['job_id']
        await_job(client, crs_job_id)

        crs = " ".join(p["message"] for p in jobs.get(crs_job_id).progress)
        search = " ".join(p["message"] for p in jobs.get(search_job_id).progress)
        assert "retrying (attempt 6)" in crs
        assert "retrying (attempt 6)" not in search

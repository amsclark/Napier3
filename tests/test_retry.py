"""Going back for the cases Iowa Courts would not give up.

A run that comes back four cases short used to leave two options: look those
four up on Iowa Courts by hand, or run the whole thing again and spend another
sign in and another twenty minutes re-pulling the sixty-three that worked. On a
clinic list the second option also means re-queueing for the shared account
while somebody else waits.

So the finish page offers a third. Sign in again, ask for only what is missing,
and rebuild the workbook from what came back plus what came back last time.

The names here are invented and every case number is 00000-shaped. The
repository is public and a real Iowa case number attached to a real name is a
person.
"""

import os
import sys
import time
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['NAPIER_DISABLE_BACKGROUND'] = '1'

import app as app_module
import icos_sessions
import jobs
import tasks
from icos import IcosError

DOE_CASES = ['00000 FECR000000', '00000 SRCR000000', '00000 SMCR000000']
ROE_CASES = ['00000 CVCV000000']


def results_for(surname, given, cases, dob="01/01/1900"):
    rows = "".join(
        '<tr><td>%s</td><td></td><td>STATE VS %s</td><td>%s, %s</td>'
        '<td>%s</td><td>DEFENDANT</td></tr>' % (case, surname, surname, given, dob)
        for case in cases)
    return ("<html><table>"
            "<tr><td>Case ID</td><td></td><td>Title</td><td>Name</td>"
            "<td>DOB</td><td>Role</td></tr>%s</table></html>" % rows).encode()


PEOPLE = {
    'DOE': results_for('DOE', 'JANE', DOE_CASES),
    'ROE': results_for('ROE', 'JOHN', ROE_CASES),
}


class FakeClient:
    """Stands in for IcosClient, and lets a test change its mind mid-scenario.

    fail_cases is on the class rather than the instance because the whole point
    of these tests is a second run behaving differently from the first, and the
    second run builds its own client.
    """

    instances = []
    fail_cases = set()
    fail_searches = set()   # surnames Iowa Courts will not answer for
    login_error = None

    def __init__(self, log=None, alert=None, **kwargs):
        self.log = log or (lambda m, **kw: None)
        self.alert = alert
        self.should_stop = lambda: False
        self.logged_in = False
        self.logged_off = False
        self.searched = []
        self.cases = []
        self.trace = []
        FakeClient.instances.append(self)

    def set_alert(self, alert):
        self.alert = alert

    def set_log(self, log):
        self.log = log or (lambda m, **kw: None)

    def set_stop_check(self, should_stop):
        self.should_stop = should_stop or (lambda: False)

    def login(self, username, password):
        if FakeClient.login_error:
            raise FakeClient.login_error
        self.logged_in = True
        self.log("Signed in to Iowa Courts Online.")

    def search(self, first, middle, last):
        self.searched.append((first, middle, last))
        self.trace.append(('search', last.upper()))
        if last.upper() in FakeClient.fail_searches:
            raise IcosError("Iowa Courts did not answer.")
        return PEOPLE.get(last.upper(), results_for(last.upper(), 'X', []))

    def case_bundle(self, case_id):
        self.cases.append(case_id)
        self.trace.append(('case', case_id))
        if case_id in FakeClient.fail_cases:
            raise IcosError("Iowa Courts did not answer for this case.")
        return b"<summary>", b"<charges>", b"<financials>"

    def logoff(self):
        self.logged_off = True
        self.logged_in = False


# Keyed the way build_workbook keys it: the ICOS wording paired with whether the
# row it landed on came out with a code in column G. True here because an
# unreadable count is coded OTH, which is the reporting these tests are about.
UNKNOWN = {}


@pytest.fixture(autouse=True)
def fake_icos(monkeypatch):
    FakeClient.instances = []
    FakeClient.fail_cases = set()
    FakeClient.fail_searches = set()
    FakeClient.login_error = None
    UNKNOWN.clear()
    monkeypatch.setattr(tasks, 'IcosClient', FakeClient)
    monkeypatch.setattr(tasks.case_parser, 'parse_case_summary',
                        lambda html, case: case.update(county='DUBUQUE'))
    monkeypatch.setattr(tasks.case_parser, 'parse_case_charges',
                        lambda html, case: case.update(charges=[]))
    monkeypatch.setattr(tasks.case_parser, 'parse_case_financials',
                        lambda html, case: case.update(financials=[],
                                                       total_due='$0.00'))
    # Records what went into each workbook, which is the thing every test here
    # is really asking about: did the rebuild keep what the first run got.
    monkeypatch.setattr(tasks, 'build_workbook', _fake_build)
    yield


WRITTEN = []


def _fake_build(cases, name, dob, lite, failed=()):
    # failed is recorded because a rebuilt workbook that still does not name
    # what is missing from it is the whole point of this going in.
    WRITTEN.append({'name': name, 'ids': [case['id'] for case in cases],
                   'failed': list(failed)})
    path = os.path.join(tasks.tmp_dir,
                        'test_retry_%s.xlsx' % name.split(',')[0].strip())
    with open(path, 'wb') as f:
        f.write(b'PK\x03\x04 stub workbook')
    return path, dict(UNKNOWN), {'balance': '$0.00', 'monthly': None, 'months': 12}


@pytest.fixture(autouse=True)
def _empty_written():
    WRITTEN[:] = []
    yield
    WRITTEN[:] = []


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


def run_one(client, surname='Doe', given='Jane'):
    """Search for one client and build their CRS. Returns the CRS job id."""
    response = client.post('/search', data={
        'username': 'ILATEST', 'password': 'secret',
        'firstname': given, 'middlename': '', 'lastname': surname})
    search_id = response.headers['Location'].rsplit('/', 1)[-1]
    await_job(client, search_id)
    client.get('/results/' + search_id)
    result = jobs.get(search_id).result
    response = client.post('/crs-job', json={'search_job_id': search_id,
                                             'keys': result['keys']})
    job_id = response.get_json()['job_id']
    await_job(client, job_id)
    return search_id, job_id


def run_list(client, roster="Doe, Jane\nRoe, John"):
    """Search a clinic list and build all of it. Returns the CRS job id."""
    response = client.post('/batch', data={
        'username': 'ILATEST', 'password': 'secret', 'roster': roster})
    search_id = response.headers['Location'].rsplit('/', 1)[-1]
    await_job(client, search_id)
    client.get('/roster/' + search_id)
    entries = jobs.get(search_id).result['clients']
    picks = [{'client': index, 'keys': entry['keys']}
             for index, entry in enumerate(entries) if entry['keys']]
    response = client.post('/batch-crs', json={'search_job_id': search_id,
                                               'picks': picks})
    job_id = response.get_json()['job_id']
    await_job(client, job_id)
    return search_id, job_id


def retry(client, job_id, username='ILATEST', password='secret', **extra):
    form = {'username': username, 'password': password}
    form.update(extra)
    return client.post('/retry/' + job_id, data=form)


def follow_retry(client, job_id, **kwargs):
    """Post the retry form and wait for the run it starts. Returns the new job."""
    response = retry(client, job_id, **kwargs)
    assert response.status_code == 302, response.get_data(as_text=True)[:400]
    new_id = response.headers['Location'].rsplit('/', 1)[-1]
    return new_id, await_job(client, new_id)


class TestWhatTheFinishPageOffers:
    def test_a_short_run_offers_another_go(self, client):
        FakeClient.fail_cases = {DOE_CASES[1]}
        _, job_id = run_one(client)
        page = client.get('/done/' + job_id).get_data(as_text=True)
        assert '/retry/' + job_id in page
        assert 'Try that case again' in page

    def test_a_complete_run_offers_nothing(self, client):
        """Nothing failed, so there is nothing to go back for and no reason to
        put a second sign in in front of somebody who is finished."""
        _, job_id = run_one(client)
        page = client.get('/done/' + job_id).get_data(as_text=True)
        assert '/retry/' not in page

    def test_a_short_clinic_list_offers_another_go(self, client):
        FakeClient.fail_cases = {DOE_CASES[0], ROE_CASES[0]}
        _, job_id = run_list(client)
        page = client.get('/batch-done/' + job_id).get_data(as_text=True)
        assert '/retry/' + job_id in page
        assert 'Try those 2 cases again' in page

    def test_a_run_with_no_way_back_to_icos_offers_nothing(self, client):
        """A CRS job started before Napier kept the search terms cannot put the
        search back in front of ICOS, and pulling cases without re-selecting is
        how the wrong client's case ends up in somebody's workbook. So it says
        nothing rather than offering a button that would do that."""
        FakeClient.fail_cases = {DOE_CASES[1]}
        response = client.post('/search', data={
            'username': 'ILATEST', 'password': 'secret',
            'firstname': 'Jane', 'middlename': '', 'lastname': 'Doe'})
        search_id = response.headers['Location'].rsplit('/', 1)[-1]
        await_job(client, search_id)
        client.get('/results/' + search_id)
        result = jobs.get(search_id).result
        result.pop('person')            # the older job shape
        response = client.post('/crs-job', json={'search_job_id': search_id,
                                                 'keys': result['keys']})
        job_id = response.get_json()['job_id']
        await_job(client, job_id)

        assert jobs.get(job_id).result['retry'] is None
        page = client.get('/done/' + job_id).get_data(as_text=True)
        assert '/retry/' not in page
        assert 'missing from this workbook' in page   # still says so

    def test_the_case_data_it_carries_never_reaches_the_browser(self, client):
        """The payload holds whole parsed cases and the client's search terms.
        It lives in the dyno for the two hours the job does and goes no further:
        to_dict leaves result alone, and the form posts a sign in and nothing
        else."""
        FakeClient.fail_cases = {DOE_CASES[1]}
        _, job_id = run_one(client)
        assert 'result' not in client.get('/job/' + job_id).get_json()
        page = client.get('/done/' + job_id).get_data(as_text=True)
        form = page[page.index('id="retry-form"'):]
        assert 'hidden' not in form[:form.index('</form>')]
        assert DOE_CASES[0] not in page      # the ones that worked


class TestGoingBackForThem:
    def test_the_missing_case_is_recovered_and_the_rest_are_not_re_pulled(self, client):
        FakeClient.fail_cases = {DOE_CASES[1]}
        _, job_id = run_one(client)
        FakeClient.fail_cases = set()

        new_id, state = follow_retry(client, job_id)
        assert state['status'] == 'done'
        pulled = FakeClient.instances[-1].cases
        assert pulled == [DOE_CASES[1]]
        assert jobs.get(new_id).result['failed_cases'] == []

    def test_the_recovered_case_goes_back_where_it_belongs(self, client):
        """Appended to the end, a recovered case reads as the most recent one.
        The workbook is ordered the way the search was."""
        FakeClient.fail_cases = {DOE_CASES[0]}
        _, job_id = run_one(client)
        FakeClient.fail_cases = set()
        follow_retry(client, job_id)
        assert WRITTEN[-1]['ids'] == DOE_CASES

    def test_the_search_is_put_back_in_front_of_icos_first(self, client):
        """ICOS decides which case a case request means from whatever it
        answered last. Asking for a case without re-searching is how a stub
        comes back with the right heading and none of the charges."""
        FakeClient.fail_cases = {DOE_CASES[1]}
        _, job_id = run_one(client)
        FakeClient.fail_cases = set()
        follow_retry(client, job_id)
        assert FakeClient.instances[-1].trace == [
            ('search', 'DOE'), ('case', DOE_CASES[1])]

    def test_it_signs_in_and_signs_out_again(self, client):
        """The run this is started from logged its session off on the way out,
        which is what keeps the shared account usable, so a retry has to bring
        its own. And has to give it back."""
        FakeClient.fail_cases = {DOE_CASES[1]}
        _, job_id = run_one(client)
        before = len(FakeClient.instances)
        FakeClient.fail_cases = set()
        follow_retry(client, job_id)

        fresh = FakeClient.instances[-1]
        assert len(FakeClient.instances) == before + 1
        assert fresh.logged_in is False and fresh.logged_off is True

    def test_a_failed_sign_in_leaves_the_first_workbook_alone(self, client):
        from icos import IcosBadCredentials
        FakeClient.fail_cases = {DOE_CASES[1]}
        _, job_id = run_one(client)
        first = dict(jobs.get(job_id).result)

        FakeClient.login_error = IcosBadCredentials(
            "Iowa Courts Online did not accept that user ID or password.")
        _, state = follow_retry(client, job_id)
        assert state['status'] == 'failed'
        assert 'did not accept' in state['error']
        # Still downloadable, still saying what is missing.
        assert jobs.get(job_id).result['failed_cases'] == first['failed_cases']
        assert client.get('/job/%s/download' % job_id).status_code == 200

    def test_a_retry_that_is_still_short_offers_another_go(self, client):
        """Iowa Courts having a bad ten minutes is the ordinary case. Coming
        back twice is not a reason to send somebody to the court website."""
        FakeClient.fail_cases = {DOE_CASES[1], DOE_CASES[2]}
        _, job_id = run_one(client)
        FakeClient.fail_cases = {DOE_CASES[2]}

        new_id, _ = follow_retry(client, job_id)
        result = jobs.get(new_id).result
        assert result['failed_cases'] == [DOE_CASES[2]]
        assert result['retry'] is not None
        page = client.get('/done/' + new_id).get_data(as_text=True)
        assert '/retry/' + new_id in page

        # And the third go recovers it, on top of what the second one got.
        FakeClient.fail_cases = set()
        third_id, _ = follow_retry(client, new_id)
        assert jobs.get(third_id).result['failed_cases'] == []
        assert WRITTEN[-1]['ids'] == DOE_CASES

    def test_a_name_icos_will_not_answer_keeps_what_was_already_pulled(self, client):
        """The re-search failing is the one way a retry can come back with
        nothing. The workbook it rebuilds still has to be the whole workbook."""
        FakeClient.fail_cases = {DOE_CASES[1]}
        _, job_id = run_one(client)
        FakeClient.fail_cases = set()
        FakeClient.fail_searches = {'DOE'}

        new_id, state = follow_retry(client, job_id)
        assert state['status'] == 'done'
        result = jobs.get(new_id).result
        assert result['failed_cases'] == [DOE_CASES[1]]
        assert WRITTEN[-1]['ids'] == [DOE_CASES[0], DOE_CASES[2]]
        assert result['written_cases'] == 2

    def test_the_counts_on_the_finish_page_are_the_whole_workbook(self, client):
        FakeClient.fail_cases = {DOE_CASES[1]}
        _, job_id = run_one(client)
        FakeClient.fail_cases = set()
        new_id, _ = follow_retry(client, job_id)

        result = jobs.get(new_id).result
        assert (result['written_cases'], result['requested_cases']) == (3, 3)
        page = client.get('/done/' + new_id).get_data(as_text=True)
        assert '3 cases' in page
        assert 'missing from this workbook' not in page

    def test_a_disposition_already_reported_is_not_reported_again(self, client):
        """Every unknown disposition emails somebody, and the map is only
        updated once. Telling them again about the same one on every rebuild is
        how alerting stops being read."""
        FakeClient.fail_cases = {DOE_CASES[1]}
        UNKNOWN.update({('HELD IN ABEYANCE', True): list(DOE_CASES)})
        _, job_id = run_one(client)
        FakeClient.fail_cases = set()
        new_id, state = follow_retry(client, job_id)

        reported = [line for line in state['progress']
                    if 'HELD IN ABEYANCE' in line]
        assert len(reported) == 1
        assert DOE_CASES[1] in reported[0]
        assert DOE_CASES[0] not in reported[0]

    def test_a_disposition_with_nothing_new_under_it_is_not_mentioned(
            self, client, monkeypatch):
        """Filtering the case numbers is not enough. A wording left holding an
        empty list still reads out as "recorded X on 0 cases" and still emails,
        which is the same nag with the evidence taken out of it."""
        FakeClient.fail_cases = {DOE_CASES[1]}
        UNKNOWN.update({('HELD IN ABEYANCE', True): [DOE_CASES[0], DOE_CASES[2]]})
        _, job_id = run_one(client)
        FakeClient.fail_cases = set()

        sent = []
        monkeypatch.setattr(tasks.alerts, 'record',
                            lambda *a, **k: sent.append(k.get('disposition')))
        _, state = follow_retry(client, job_id)
        assert not any('HELD IN ABEYANCE' in line for line in state['progress'])
        assert 'HELD IN ABEYANCE' not in sent


class TestAWholeClinicList:
    def test_one_zip_comes_back_with_every_client_in_it(self, client):
        """Not a second partial zip to keep straight alongside the first. The
        staffer asked for a clinic list, so a retry produces a clinic list."""
        FakeClient.fail_cases = {DOE_CASES[1]}
        _, job_id = run_list(client)
        FakeClient.fail_cases = set()
        new_id, _ = follow_retry(client, job_id)

        response = client.get('/batch/%s/download' % new_id)
        assert response.status_code == 200
        with open(jobs.get(new_id).result['file'], 'rb') as bundle:
            names = zipfile.ZipFile(bundle).namelist()
        assert len(names) == 2
        assert any('DOE' in name for name in names)
        assert any('ROE' in name for name in names)

    def test_only_the_missing_cases_are_asked_for(self, client):
        FakeClient.fail_cases = {DOE_CASES[1]}
        _, job_id = run_list(client)
        FakeClient.fail_cases = set()
        follow_retry(client, job_id)

        fresh = FakeClient.instances[-1]
        assert fresh.cases == [DOE_CASES[1]]
        # The client who had nothing missing is not re-searched either.
        assert fresh.searched == [('Jane', '', 'Doe')]

    def test_the_client_who_had_nothing_missing_keeps_their_workbook(self, client):
        FakeClient.fail_cases = {DOE_CASES[1]}
        _, job_id = run_list(client)
        FakeClient.fail_cases = set()
        new_id, _ = follow_retry(client, job_id)

        clients = jobs.get(new_id).result['clients']
        by_name = {record['name']: record for record in clients}
        assert by_name['DOE, JANE']['written'] == 3
        assert by_name['ROE, JOHN']['written'] == 1
        assert all(record['file'] for record in clients)

    def test_a_client_with_no_workbook_at_all_can_still_be_recovered(self, client):
        """Every one of their cases failed, so the first run had no file for
        them and said so. The retry has to be able to give them one."""
        FakeClient.fail_cases = set(DOE_CASES)
        _, job_id = run_list(client)
        first = {record['name']: record
                 for record in jobs.get(job_id).result['clients']}
        assert first['DOE, JANE']['file'] is None

        FakeClient.fail_cases = set()
        new_id, _ = follow_retry(client, job_id)
        after = {record['name']: record
                 for record in jobs.get(new_id).result['clients']}
        assert after['DOE, JANE']['written'] == 3
        assert after['DOE, JANE']['file']


class TestWhoCanAskForOne:
    def test_a_job_this_browser_did_not_start_is_refused(self, client):
        FakeClient.fail_cases = {DOE_CASES[1]}
        _, job_id = run_one(client)

        stranger = app_module.app.test_client()
        response = stranger.post('/retry/' + job_id,
                                 data={'username': 'ILATEST',
                                       'password': 'secret'})
        assert response.status_code == 200
        assert 'Napier restarted' in response.get_data(as_text=True)
        # Nothing was started on the strength of it.
        assert all(not instance.logged_in
                   for instance in FakeClient.instances[1:])

    def test_a_user_id_that_is_not_iowa_legal_aid_is_turned_back(self, client):
        FakeClient.fail_cases = {DOE_CASES[1]}
        _, job_id = run_one(client)
        before = len(FakeClient.instances)

        response = retry(client, job_id, username='someone')
        page = response.get_data(as_text=True)
        assert 'not an Iowa Legal Aid' in page
        assert len(FakeClient.instances) == before
        # Back on the finish page with the offer still open, not on a dead end.
        assert '/retry/' + job_id in page
        assert '/job/%s/download' % job_id in page

    def test_a_search_left_open_is_let_go_before_signing_in_again(self, client):
        """Iowa Courts allows one session per account. A search still open in
        another tab is holding the same one the retry is about to sign in on,
        so leaving it be means the retry collides with the staffer's own
        session and waits the concurrent-login budget out for nothing."""
        FakeClient.fail_cases = {DOE_CASES[1]}
        _, job_id = run_one(client)
        FakeClient.fail_cases = set()

        held = FakeClient()
        with client.session_transaction() as browser:
            browser['icos_token'] = icos_sessions.put(held)
        follow_retry(client, job_id)
        assert held.logged_off is True

    def test_a_rebuild_that_produces_no_file_fails_rather_than_saying_nothing(
            self, client, monkeypatch):
        """A zip of nothing still downloads, and a finish page saying zero
        workbooks reads like a run that was never going to work. The staffer
        still has the first run, so the honest answer is that this one broke."""
        FakeClient.fail_cases = {DOE_CASES[1]}
        _, job_id = run_list(client)
        FakeClient.fail_cases = set()

        def explode(*args, **kwargs):
            raise RuntimeError("no space left on device")

        monkeypatch.setattr(tasks, 'build_workbook', explode)
        monkeypatch.setattr(tasks.alerts, 'record', lambda *a, **k: None)
        _, state = follow_retry(client, job_id)
        assert state['status'] == 'failed'
        # And the run it was started from is still whole.
        assert client.get('/batch/%s/download' % job_id).status_code == 200

    def test_a_run_with_nothing_left_to_try_says_so(self, client):
        _, job_id = run_one(client)
        response = retry(client, job_id)
        assert 'nothing on this run left to try again' in \
            response.get_data(as_text=True)

    def test_the_browser_cannot_name_the_cases_to_pull(self, client):
        """What gets asked for comes off the run, not off the form. Otherwise
        the form is a way to pull any case in Iowa on Napier's sign in."""
        FakeClient.fail_cases = {DOE_CASES[1]}
        _, job_id = run_one(client)
        FakeClient.fail_cases = set()

        follow_retry(client, job_id, case_ids='00000 FECR999999')
        assert FakeClient.instances[-1].cases == [DOE_CASES[1]]

    def test_a_rejected_retry_does_not_re_download_the_workbook(self, client):
        """The finish page saves the file on its own when a run lands on it.
        Reached by a refused form post, nothing has been rebuilt, and saving
        the same file again is not an answer to being told the user ID is
        wrong."""
        FakeClient.fail_cases = {DOE_CASES[1]}
        _, job_id = run_one(client)
        response = retry(client, job_id, username='someone')
        assert 'id="grab"' not in response.get_data(as_text=True)
        assert 'id="grab"' in client.get('/done/' + job_id).get_data(as_text=True)


class TestTheHelpersUnderneath:
    def test_a_run_with_nothing_missing_has_no_payload(self):
        entry = tasks._retry_entry('DOE, JANE', '01/01/1900',
                                   {'first': 'Jane', 'middle': '', 'last': 'Doe'},
                                   DOE_CASES, [], [])
        assert tasks._retry_payload('crs', False, [entry]) is None

    def test_a_missing_case_with_no_search_behind_it_has_no_payload(self):
        entry = tasks._retry_entry('DOE, JANE', '01/01/1900', None,
                                   DOE_CASES, [], [DOE_CASES[0]])
        assert tasks._retry_payload('crs', False, [entry]) is None

    def test_one_client_with_no_way_back_costs_the_whole_list_its_payload(self):
        """Rebuilding a clinic list rebuilds all of it, so a client who cannot
        be re-searched would come back with an empty workbook rather than the
        one they already have."""
        with_terms = tasks._retry_entry(
            'DOE, JANE', '01/01/1900',
            {'first': 'Jane', 'middle': '', 'last': 'Doe'},
            DOE_CASES, [], [DOE_CASES[0]])
        without = tasks._retry_entry('ROE, JOHN', '01/01/1900', None,
                                     ROE_CASES, [], [ROE_CASES[0]])
        assert tasks._retry_payload('batch_crs', False, [with_terms]) is not None
        assert tasks._retry_payload('batch_crs', False,
                                    [with_terms, without]) is None

    def test_a_client_with_nothing_missing_does_not_block_the_payload(self):
        """No failures means no re-search, so no search terms are needed."""
        short = tasks._retry_entry(
            'DOE, JANE', '01/01/1900',
            {'first': 'Jane', 'middle': '', 'last': 'Doe'},
            DOE_CASES, [], [DOE_CASES[0]])
        complete = tasks._retry_entry('ROE, JOHN', '01/01/1900', None,
                                      ROE_CASES, [{'id': ROE_CASES[0]}], [])
        assert tasks._retry_payload('batch_crs', False,
                                    [short, complete]) is not None

    def test_merging_keeps_the_order_the_search_came_back_in(self):
        entry = tasks._retry_entry('DOE, JANE', '01/01/1900', None, DOE_CASES,
                                   [{'id': DOE_CASES[0]}, {'id': DOE_CASES[2]}],
                                   [DOE_CASES[1]])
        merged = tasks._merged_cases(entry, [{'id': DOE_CASES[1]}])
        assert [case['id'] for case in merged] == DOE_CASES

    def test_a_case_read_twice_is_only_in_the_workbook_once(self):
        entry = tasks._retry_entry('DOE, JANE', '01/01/1900', None, DOE_CASES,
                                   [{'id': DOE_CASES[0], 'county': 'OLD'}], [])
        merged = tasks._merged_cases(entry, [{'id': DOE_CASES[0],
                                              'county': 'NEW'}])
        assert len(merged) == 1
        # The newer read wins, because it is the one that just came off ICOS.
        assert merged[0]['county'] == 'NEW'

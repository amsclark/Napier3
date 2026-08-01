"""The staff-facing flow: search, results, CRS, download.

Exercised through the real routes with a fake court site, so the parts that
matter to staff are covered: nothing blocks a request past Heroku's 30 second
limit, the ESA session is always released, and a restarted dyno says so.
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['NAPIER_DISABLE_BACKGROUND'] = '1'

import app as app_module
import icos_sessions
import jobs
import tasks
from icos import IcosBadCredentials, IcosError

SEARCH_RESULTS = b"""
<html><table>
<tr><td>Case ID</td><td></td><td>Title</td><td>Name</td><td>DOB</td><td>Role</td></tr>
<tr><td>01311 FECR000000</td><td></td><td>STATE VS TESTER</td><td>TESTER, PAT Q</td><td>01/01/1900</td><td>DEFENDANT</td></tr>
<tr><td>01311 SRCR012345</td><td></td><td>STATE VS TESTER</td><td>TESTER, PAT Q</td><td>01/01/1900</td><td>DEFENDANT</td></tr>
<tr><td>01311 CVCV009999</td><td></td><td>SMITH VS TESTER</td><td>TESTER, PAT Q</td><td>01/01/1900</td><td>ATTORNEY</td></tr>
</table></html>
"""


class FakeClient:
    """Stands in for IcosClient; records the session lifecycle."""

    instances = []
    login_error = None
    search_error = None
    search_results = None

    def __init__(self, log=None, alert=None, **kwargs):
        self.log = log or (lambda m: None)
        self.alert = alert
        self.should_stop = lambda: False
        self.logged_in = False
        self.logged_off = False
        self.cases = []
        self.fail_cases = []
        FakeClient.instances.append(self)

    def set_alert(self, alert):
        # The CRS job inherits the search job's session and rebinds alerting
        # to itself; a stub that skipped this would hide a break in that.
        self.alert = alert

    def set_log(self, log):
        # Same rebinding, and the one staff can actually see. Left unbound, the
        # retry notices go on being written into the search job while the person
        # waiting watches the CRS job say nothing.
        self.log = log or (lambda m: None)

    def set_stop_check(self, should_stop):
        self.should_stop = should_stop or (lambda: False)

    def login(self, username, password):
        if FakeClient.login_error:
            raise FakeClient.login_error
        self.logged_in = True
        self.log("Signed in to Iowa Courts Online.")

    def search(self, first, middle, last):
        if FakeClient.search_error:
            raise FakeClient.search_error
        if FakeClient.search_results is not None:
            return FakeClient.search_results
        return SEARCH_RESULTS

    def case_bundle(self, case_id):
        self.cases.append(case_id)
        if case_id in self.fail_cases:
            raise IcosError("Iowa Courts did not answer for this case.")
        return b"<summary>", b"<charges>", b"<financials>"

    def logoff(self):
        self.logged_off = True
        self.logged_in = False


@pytest.fixture(autouse=True)
def fake_icos(monkeypatch):
    FakeClient.instances = []
    FakeClient.login_error = None
    FakeClient.search_error = None
    FakeClient.search_results = None
    monkeypatch.setattr(tasks, 'IcosClient', FakeClient)
    # Case parsing and workbook building have their own tests; here we care
    # about the flow around them.
    monkeypatch.setattr(tasks.case_parser, 'parse_case_summary',
                        lambda html, case: case.update(county='DUBUQUE'))
    monkeypatch.setattr(tasks.case_parser, 'parse_case_charges',
                        lambda html, case: case.update(charges=[]))
    monkeypatch.setattr(tasks.case_parser, 'parse_case_financials',
                        lambda html, case: case.update(financials=[], total_due='$0.00'))
    monkeypatch.setattr(tasks, 'build_workbook',
                        lambda cases, name, dob, lite: (_write_stub_workbook(), {}))
    yield


def _write_stub_workbook():
    path = os.path.join(tasks.tmp_dir, 'test_stub_CRS.xlsx')
    with open(path, 'wb') as f:
        f.write(b'PK\x03\x04 stub workbook')
    return path


@pytest.fixture
def client():
    app_module.app.config['TESTING'] = True
    app_module.app.secret_key = 'test'
    return app_module.app.test_client()


def run_search(client, first="PAT", last="TESTER", username="ILATEST"):
    response = client.post('/search', data={
        'username': username, 'password': 'secret',
        'firstname': first, 'middlename': '', 'lastname': last,
    })
    assert response.status_code == 302
    job_id = response.headers['Location'].rsplit('/', 1)[-1]
    return job_id, await_job(client, job_id)


def await_job(client, job_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get('/job/' + job_id).get_json()
        if state['done']:
            return state
        time.sleep(0.01)
    raise AssertionError("job never finished")


def test_search_returns_immediately_and_finishes_in_the_background(client):
    started = time.time()
    response = client.post('/search', data={
        'username': 'ILATEST', 'password': 'secret',
        'firstname': 'PAT', 'middlename': '', 'lastname': 'TESTER',
    })
    # The old flow held the request open for the whole search and tripped
    # Heroku's 30 second limit; this one must hand back a page at once.
    assert time.time() - started < 1
    assert response.status_code == 302
    assert '/progress/' in response.headers['Location']


def test_search_finds_cases_and_drops_non_party_roles(client):
    job_id, state = run_search(client)
    assert state['status'] == 'done'

    page = client.get('/results/' + job_id)
    body = page.get_data(as_text=True)
    assert 'TESTER, PAT Q' in body
    assert '2 cases' in body  # the ATTORNEY row is excluded


def test_search_session_is_held_for_the_results_page(client):
    job_id, _ = run_search(client)
    client.get('/results/' + job_id)
    search_client = FakeClient.instances[0]
    # Still logged in: staff have not picked defendants yet. The reaper closes
    # it if they never come back.
    assert search_client.logged_off is False


def test_bad_password_fails_the_job_with_a_plain_message(client):
    FakeClient.login_error = IcosBadCredentials(
        "Iowa Courts Online did not accept that user ID or password.")
    _, state = run_search(client)
    assert state['status'] == 'failed'
    assert 'did not accept' in state['error']
    assert FakeClient.instances[0].logged_off is True  # no session left behind


def test_failed_search_leaves_no_open_session(client):
    FakeClient.search_error = RuntimeError("boom")
    _, state = run_search(client)
    assert state['status'] == 'failed'
    assert FakeClient.instances[0].logged_off is True


def test_non_legal_aid_username_is_rejected(client):
    response = client.post('/search', data={
        'username': 'someoneelse', 'password': 'secret',
        'firstname': 'PAT', 'middlename': '', 'lastname': 'TESTER',
    })
    assert response.status_code == 200
    assert 'not an Iowa Legal Aid' in response.get_data(as_text=True)


def test_crs_job_pulls_cases_and_offers_a_download(client):
    search_job_id, _ = run_search(client)
    client.get('/results/' + search_job_id)

    response = client.post('/crs-job', json={
        'search_job_id': search_job_id,
        'keys': ['1900-01-01 TESTER, PAT Q'],
    })
    assert response.status_code == 200
    crs_job_id = response.get_json()['job_id']
    state = await_job(client, crs_job_id)
    assert state['status'] == 'done'
    assert state['next_url'] == '/done/%s' % crs_job_id

    search_client = FakeClient.instances[0]
    assert search_client.cases == ['01311 FECR000000', '01311 SRCR012345']
    # The workbook is built server-side, so closing the laptop no longer costs
    # staff the run.
    assert any('Pulling case 1 of 2' in line for line in state['progress'])
    assert state['total'] == 2

    finished = client.get(state['next_url'])
    assert finished.status_code == 200
    page = finished.get_data(as_text=True)
    assert '/job/%s/download' % crs_job_id in page

    download = client.get('/job/%s/download' % crs_job_id)
    assert download.status_code == 200
    assert 'TESTER' in download.headers['Content-Disposition']


def test_a_user_id_typed_on_a_phone_still_signs_in(client):
    """Autofill and phone keyboards put a space around the user ID. It used to
    ride through to ICOS, which answered like a wrong password."""
    response = client.post('/search', data={
        'username': '  ILA01  ', 'password': 'secret',
        'firstname': 'PAT', 'middlename': '', 'lastname': 'TESTER',
    })
    assert response.status_code == 302
    assert '/progress/' in response.headers['Location']


def test_the_finish_page_names_the_cases_missing_from_the_workbook(client):
    """A case Iowa Courts would not give up is left out of the file. Staff have
    to be told which one, or they will read a short summary as a complete one."""
    search_job_id, _ = run_search(client)
    client.get('/results/' + search_job_id)
    FakeClient.instances[0].fail_cases = ['01311 SRCR012345']

    response = client.post('/crs-job', json={
        'search_job_id': search_job_id,
        'keys': ['1900-01-01 TESTER, PAT Q'],
    })
    state = await_job(client, response.get_json()['job_id'])
    assert state['status'] == 'done'

    page = client.get(state['next_url']).get_data(as_text=True)
    assert '01311 SRCR012345' in page
    assert 'missing from this workbook' in page


def test_the_finish_page_needs_the_session_that_made_the_job(client):
    search_job_id, _ = run_search(client)
    client.get('/results/' + search_job_id)
    response = client.post('/crs-job', json={
        'search_job_id': search_job_id,
        'keys': ['1900-01-01 TESTER, PAT Q'],
    })
    job_id = response.get_json()['job_id']
    await_job(client, job_id)

    client.get('/logout')
    page = client.get('/done/' + job_id).get_data(as_text=True)
    assert 'workbook is ready' not in page


def test_crs_job_always_releases_the_session(client):
    search_job_id, _ = run_search(client)
    client.get('/results/' + search_job_id)
    response = client.post('/crs-job', json={
        'search_job_id': search_job_id,
        'keys': ['1900-01-01 TESTER, PAT Q'],
    })
    await_job(client, response.get_json()['job_id'])
    assert FakeClient.instances[0].logged_off is True


def test_crs_job_needs_a_selection(client):
    search_job_id, _ = run_search(client)
    client.get('/results/' + search_job_id)
    response = client.post('/crs-job',
                           json={'search_job_id': search_job_id, 'keys': []})
    assert response.status_code == 400


def test_another_browser_cannot_read_someone_elses_job(client):
    job_id, _ = run_search(client)
    other = app_module.app.test_client()
    assert other.get('/job/' + job_id).status_code == 410
    assert 'run the search again' in other.get('/results/' + job_id).get_data(as_text=True)


def test_restart_is_reported_plainly(client):
    job_id, _ = run_search(client)
    jobs._jobs.clear()  # what a dyno restart looks like to the browser
    state = client.get('/job/' + job_id).get_json()
    assert state['done'] is True
    assert 'run the search again' in state['error']


def test_logout_releases_the_session(client):
    search_job_id, _ = run_search(client)
    client.get('/results/' + search_job_id)
    client.get('/logout')
    assert FakeClient.instances[0].logged_off is True


def test_download_rejects_a_path_outside_tmp(client):
    search_job_id, _ = run_search(client)
    client.get('/results/' + search_job_id)
    response = client.post('/crs-job', json={
        'search_job_id': search_job_id,
        'keys': ['1900-01-01 TESTER, PAT Q'],
    })
    crs_job_id = response.get_json()['job_id']
    await_job(client, crs_job_id)

    jobs.get(crs_job_id).result['file'] = '/etc/passwd'
    assert 'invalid file' in client.get('/job/%s/download' % crs_job_id).get_data(as_text=True)


def build_a_workbook(client):
    search_job_id, _ = run_search(client)
    client.get('/results/' + search_job_id)
    response = client.post('/crs-job', json={
        'search_job_id': search_job_id,
        'keys': ['1900-01-01 TESTER, PAT Q'],
    })
    crs_job_id = response.get_json()['job_id']
    await_job(client, crs_job_id)
    return crs_job_id


def test_a_workbook_survives_the_page_that_was_watching_it(client):
    """A phone that loses signal ends the run on screen while it finishes on the
    server. Without a way back to the file, the only option was to sign in and
    pull every case a second time."""
    crs_job_id = build_a_workbook(client)

    page = client.get('/').get_data(as_text=True)
    assert 'workbook waiting' in page
    assert '/done/%s' % crs_job_id in page


def test_the_offer_goes_away_once_the_file_has_been_collected(client):
    crs_job_id = build_a_workbook(client)
    client.get('/job/%s/download' % crs_job_id)
    assert 'workbook waiting' not in client.get('/').get_data(as_text=True)


def test_the_offer_is_made_on_the_page_that_says_the_search_is_gone(client):
    """The dyno-restart page is where a staffer lands after the thing they were
    watching died, which is exactly when the workbook is worth mentioning."""
    crs_job_id = build_a_workbook(client)
    page = client.get('/progress/nosuchjob').get_data(as_text=True)
    assert 'workbook waiting' in page
    assert '/done/%s' % crs_job_id in page


def test_one_browser_is_never_offered_another_s_workbook(client):
    build_a_workbook(client)
    other = app_module.app.test_client()
    assert 'workbook waiting' not in other.get('/').get_data(as_text=True)


def test_a_search_that_matched_nobody_says_so(client):
    """An empty grid is what a half broken search looks like too.

    Staff had no way to tell "Iowa Courts has nothing under that name" from
    "Napier lost the results", so the safe reading was to doubt the tool.
    """
    FakeClient.search_results = b"<html><table></table></html>"
    job_id, state = run_search(client)
    assert state['status'] == 'done'

    body = client.get('/results/' + job_id).get_data(as_text=True)
    assert 'no cases under that name' in body
    # The picking form has nothing to pick, so it is not offered.
    assert 'Create the CRS' not in body
    assert 'Start a new search' in body


def test_a_truncated_search_warns_on_the_results_page(client):
    """The whole point of detecting it is that this banner reaches staff.

    Wired end to end because the parser flag, the job result, the route and
    the template are four separate places it can be dropped.
    """
    FakeClient.search_results = SEARCH_RESULTS.replace(
        b"</table>",
        b"</table><font size=\"2\">\r\n\t\tYour query returned more than 200 "
        b"records.\r\n\t\t</font>")
    job_id, state = run_search(client)
    assert state['status'] == 'done'

    body = client.get('/results/' + job_id).get_data(as_text=True)
    assert 'stopped counting at 200 records' in body
    # It is a warning about the list, not a replacement for it.
    assert 'TESTER, PAT Q' in body


def test_an_ordinary_search_carries_no_truncation_warning(client):
    job_id, state = run_search(client)
    body = client.get('/results/' + job_id).get_data(as_text=True)
    assert 'stopped counting' not in body

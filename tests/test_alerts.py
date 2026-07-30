"""Alerting: does a failure nobody is watching actually reach someone.

These tests point the Mailgun endpoint at a real local HTTP server and assert on
the request that arrives on it, rather than on a mock having been called. A mock
proves the code called what we told it to call. It does not prove an email
leaves the process, which is the only thing this feature is for.

The other thing under test here is what must never be in one. The search subject
is privileged under Article 5, so an alert may carry a case number, which is
court public record and without which a parse failure is not actionable, and may
not carry a name, a date of birth, or a credential.
"""

import base64
import os
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ['NAPIER_DISABLE_BACKGROUND'] = '1'

import alerts
import icos
import tasks
from icos import IcosClient, IcosAccountLocked, IcosUnavailable
from reader import FetchResult, OK, EMPTY


class Mailgun(BaseHTTPRequestHandler):
    received = []
    status = 200

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        fields = urllib.parse.parse_qs(self.rfile.read(length).decode('utf-8'))
        Mailgun.received.append({
            'path': self.path,
            'auth': self.headers.get('Authorization'),
            'to': fields.get('to', [''])[0],
            'from': fields.get('from', [''])[0],
            'subject': fields.get('subject', [''])[0],
            'text': fields.get('text', [''])[0],
        })
        self.send_response(Mailgun.status)
        self.end_headers()
        self.wfile.write(b'{"id":"ok"}')

    def log_message(self, *args):
        pass


@pytest.fixture
def mailbox(monkeypatch):
    """A real listening socket standing in for api.mailgun.net."""
    Mailgun.received = []
    Mailgun.status = 200
    server = HTTPServer(('127.0.0.1', 0), Mailgun)
    # serve_forever polls at half a second by default, which would be half a
    # second of teardown on every test in this file.
    threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01},
                     daemon=True).start()
    monkeypatch.setattr(alerts, 'MAILGUN_ENDPOINT',
                        'http://127.0.0.1:%d/v3/%%s/messages' % server.server_port)
    monkeypatch.setenv('MAILGUN_DOMAIN', 'mg.example.org')
    monkeypatch.setenv('MAILGUN_API_KEY', 'key-test')
    monkeypatch.setenv('ALERT_EMAIL_TO', 'alerts@example.org')
    alerts.reset()
    yield Mailgun.received
    server.shutdown()
    alerts.reset()


def send(thread):
    """Wait for one alert's daemon thread, so assertions are not racing it."""
    assert thread is not None, "expected an alert to be sent"
    thread.join(timeout=5)
    assert not thread.is_alive()


def settle(timeout=5):
    """Wait out every in-flight send.

    Alerts fired from inside IcosClient are dispatched to a daemon thread whose
    handle the caller throws away, so asserting on the mailbox straight after
    is a race. Every send thread is started synchronously inside record(), so by
    the time the client call has returned they all exist and can be joined.
    """
    for thread in threading.enumerate():
        if thread.name == 'alert':
            thread.join(timeout)


class FakeJob:
    def __init__(self, kind='search'):
        self.id = 'abcdef0123456789'
        self.kind = kind
        self.progress = [{'message': 'Connecting to Iowa Courts Online...'},
                         {'message': 'Iowa Courts is slow, retrying (attempt 4)...'}]

    def log(self, message):
        self.progress.append({'message': message})


# -- delivery -------------------------------------------------------------


def test_an_alert_actually_leaves_the_process(mailbox):
    send(alerts.record('job1234', 'search', alerts.RETRY_EXHAUSTED,
                       endpoint='TrialCaseSearchResultServlet', attempts=9))
    assert len(mailbox) == 1
    assert mailbox[0]['path'] == '/v3/mg.example.org/messages'
    assert mailbox[0]['to'] == 'alerts@example.org'
    assert alerts.RETRY_EXHAUSTED in mailbox[0]['subject']
    assert 'TrialCaseSearchResultServlet' in mailbox[0]['text']
    assert 'job1234' in mailbox[0]['text']


def test_it_authenticates_the_way_mailgun_expects(mailbox):
    send(alerts.record('job1234', 'search', alerts.JOB_FAILED))
    scheme, _, token = mailbox[0]['auth'].partition(' ')
    assert scheme == 'Basic'
    assert base64.b64decode(token).decode() == 'api:key-test'


def test_the_from_address_defaults_into_the_sending_domain(mailbox):
    send(alerts.record('job1234', 'search', alerts.JOB_FAILED))
    assert 'napier@mg.example.org' in mailbox[0]['from']


def test_an_explicit_from_address_wins(mailbox, monkeypatch):
    monkeypatch.setenv('ALERT_EMAIL_FROM', 'Napier <napier@clarkmc.example>')
    send(alerts.record('job1234', 'search', alerts.JOB_FAILED))
    assert mailbox[0]['from'] == 'Napier <napier@clarkmc.example>'


@pytest.mark.parametrize('missing', ['MAILGUN_DOMAIN', 'MAILGUN_API_KEY',
                                     'ALERT_EMAIL_TO'])
def test_incomplete_config_is_a_logged_no_op(mailbox, monkeypatch, missing):
    monkeypatch.delenv(missing)
    send(alerts.record('job1234', 'search', alerts.JOB_FAILED))
    assert mailbox == []


def test_a_rejecting_mailgun_does_not_take_the_caller_down(mailbox):
    Mailgun.status = 500
    send(alerts.record('job1234', 'search', alerts.JOB_FAILED))
    # Delivery failed, the caller lived, and the event is still on the digest.
    send(alerts.digest('job1234', 'search'))


def test_an_unreachable_mailgun_does_not_take_the_caller_down(mailbox, monkeypatch):
    monkeypatch.setattr(alerts, 'MAILGUN_ENDPOINT', 'http://127.0.0.1:1/v3/%s/messages')
    send(alerts.record('job1234', 'search', alerts.JOB_FAILED))


# -- rate limiting --------------------------------------------------------


def test_one_email_per_job_per_failure_class(mailbox):
    send(alerts.record('job1234', 'search', alerts.BAD_RESPONSE, attempts=1))
    for attempt in range(2, 40):
        assert alerts.record('job1234', 'search', alerts.BAD_RESPONSE,
                             attempts=attempt) is None
    # A 45-minute budget at escalating backoff is dozens of attempts.
    assert len(mailbox) == 1


def test_a_different_class_on_the_same_job_still_gets_through(mailbox):
    send(alerts.record('job1234', 'search', alerts.BAD_RESPONSE))
    send(alerts.record('job1234', 'search', alerts.RETRY_EXHAUSTED))
    assert len(mailbox) == 2


def test_a_second_job_inside_the_floor_is_suppressed(mailbox):
    send(alerts.record('job1234', 'search', alerts.BAD_RESPONSE, now=1000.0))
    # A clinic morning puts several staff behind the same broken ICOS.
    assert alerts.record('job5678', 'search', alerts.BAD_RESPONSE,
                         now=1000.0 + 60) is None
    assert len(mailbox) == 1


def test_a_second_job_past_the_floor_gets_through(mailbox):
    send(alerts.record('job1234', 'search', alerts.BAD_RESPONSE, now=1000.0))
    send(alerts.record('job5678', 'search', alerts.BAD_RESPONSE,
                       now=1000.0 + alerts.CLASS_FLOOR_SECONDS + 1))
    assert len(mailbox) == 2


# -- the end-of-job digest ------------------------------------------------


def test_a_clean_job_sends_no_digest(mailbox):
    assert alerts.digest('job1234', 'search') is None
    assert mailbox == []


def test_the_digest_carries_events_that_were_never_emailed(mailbox):
    send(alerts.record('job1234', 'search', alerts.BAD_RESPONSE, attempts=1))
    for attempt in range(2, 6):
        alerts.record('job1234', 'search', alerts.BAD_RESPONSE, attempts=attempt)
    send(alerts.digest('job1234', 'search',
                       ['Iowa Courts is slow, retrying (attempt 4)...']))
    body = mailbox[-1]['text']
    # Suppressing an email must not lose the event.
    assert '5 problems' in mailbox[-1]['subject']
    assert 'attempts 5' in body
    assert 'retrying (attempt 4)' in body


def test_the_digest_is_sent_once(mailbox):
    alerts.record('job1234', 'search', alerts.JOB_FAILED)
    alerts.digest('job1234', 'search')
    assert alerts.digest('job1234', 'search') is None
    settle()


# -- what may and may not be in an alert ----------------------------------


def test_an_esa_account_is_reported_as_a_family_not_an_account(mailbox):
    assert alerts.username_prefix('ILA04') == 'ILA##'
    assert alerts.username_prefix('ila01') == 'ILA##'
    assert alerts.username_prefix('drakelegalclinic7') == 'drakelegalclinic'
    assert alerts.username_prefix('') == 'unknown'
    send(alerts.record('job1234', 'search', alerts.CONCURRENT_EXHAUSTED,
                       account=alerts.username_prefix('ILA04')))
    assert 'ILA##' in mailbox[0]['text']
    assert 'ILA04' not in mailbox[0]['text']


def _parser_that_quotes_the_case(row):
    # Shaped like the real thing: the message is built from a value, so what
    # ends up in the traceback's source line is this literal and not the row.
    raise ValueError('unreadable charge row: %s' % row)


def test_a_traceback_keeps_its_frames_and_drops_its_message(mailbox):
    """A parser that dies on a case usually quotes that case back."""
    # Held in a variable so the calling frame's source line does not contain it
    # either. A traceback shows source, and source is ours; it never shows the
    # values that flowed through it, which is the whole basis of this guard.
    row = 'TESTER, PAT Q 01/01/1900 FECR000000'
    try:
        _parser_that_quotes_the_case(row)
    except ValueError as e:
        text = alerts.safe_traceback(e)
    assert 'ValueError' in text
    assert 'test_alerts.py' in text                     # the frame survives
    assert 'unreadable charge row' in text              # so does our source line
    assert 'TESTER' not in text                         # the value does not
    assert '01/01/1900' not in text


def test_an_exception_we_authored_keeps_its_message(mailbox):
    # IcosError messages are written by us for staff and carry no case data,
    # and they are the single most useful line in an alert.
    try:
        raise IcosAccountLocked('This Iowa Courts account is still logged in.')
    except IcosAccountLocked as e:
        text = alerts.safe_traceback(e)
    assert 'still logged in' in text


def test_a_case_number_is_allowed_through(mailbox):
    # Court public record, and a parse-failure alert without it is not
    # actionable. Flagged in the plan as a call to confirm with Sandi.
    send(alerts.record('job1234', 'crs', alerts.PARSE_FAILURE,
                       case='01311 FECR000000'))
    assert '01311 FECR000000' in mailbox[0]['text']


def test_no_password_can_reach_an_alert(mailbox):
    client = IcosClient(reader=_reader_that('concurrent'),
                        concurrent_budget_seconds=1, sleep=lambda s: None,
                        monotonic=_clock(), alert=alerts.emitter(FakeJob()))
    with pytest.raises(IcosAccountLocked):
        client.login('ILA04', 'hunter2-not-a-real-password')
    settle()
    assert mailbox, "expected the lockout alert, otherwise this proves nothing"
    for message in mailbox:
        assert 'hunter2' not in message['text']
        assert 'ILA04' not in message['text']


# -- the classifier hooks -------------------------------------------------


def _clock():
    ticks = iter(range(0, 100000, 30))
    return lambda: next(ticks)


def _reader_that(mode, ok_after=None):
    """A Reader stub that returns a scripted outcome for every fetch."""
    login_ok = (b'x' * 30000)
    concurrent = b'Concurrent Login Error' + b'x' * 3000

    class Stub:
        def __init__(self):
            self.calls = 0

        def fetch_once(self, url, data=None, timeout=8):
            self.calls += 1
            if mode == 'empty':
                if ok_after is not None and self.calls > ok_after:
                    return FetchResult(OK, login_ok, 200, 0.1)
                return FetchResult(EMPTY, b'', 200, 0.1)
            if mode == 'concurrent':
                if 'EUACustomLoginServlet' in url:
                    return FetchResult(OK, concurrent, 200, 0.1)
                return FetchResult(OK, login_ok, 200, 0.1)
            return FetchResult(OK, login_ok, 200, 0.1)

        def init_request(self):
            return 'https://x/ESAWebApp/ESALogin.jsp', None

        def login_request(self, username, password):
            return 'https://x/ESAWebApp/EUACustomLoginServlet', b'd'

        def search_request(self, *a):
            return 'https://x/ESAWebApp/TrialCaseSearchResultServlet', b'd'

    return Stub()


def test_running_out_of_retry_budget_emails_the_timeline(mailbox):
    client = IcosClient(reader=_reader_that('empty'), budget_seconds=200,
                        sleep=lambda s: None, monotonic=_clock(),
                        alert=alerts.emitter(FakeJob()))
    with pytest.raises(IcosUnavailable):
        client.login('ILA04', 'pw')
    settle()
    subjects = [m['subject'] for m in mailbox]
    assert any(alerts.RETRY_EXHAUSTED in s for s in subjects)
    body = [m for m in mailbox if alerts.RETRY_EXHAUSTED in m['subject']][0]['text']
    assert 'ESALogin.jsp' in body
    assert 'backoff:' in body


def test_a_run_that_recovers_late_is_an_early_warning(mailbox):
    # Nobody complained, because it worked. That is exactly when we want to know.
    client = IcosClient(reader=_reader_that('empty', ok_after=4),
                        budget_seconds=10000, sleep=lambda s: None,
                        monotonic=_clock(), alert=alerts.emitter(FakeJob()))
    client._retry('search', client.reader.init_request)
    settle()
    assert any(alerts.SLOW_RECOVERY in m['subject'] for m in mailbox)


def test_a_run_that_recovers_immediately_emails_nobody(mailbox):
    client = IcosClient(reader=_reader_that('ok'), sleep=lambda s: None,
                        monotonic=_clock(), alert=alerts.emitter(FakeJob()))
    client._retry('search', client.reader.init_request)
    settle()
    assert mailbox == []


def test_a_locked_account_emails_once_the_wait_gives_up(mailbox):
    client = IcosClient(reader=_reader_that('concurrent'),
                        concurrent_budget_seconds=1, sleep=lambda s: None,
                        monotonic=_clock(), alert=alerts.emitter(FakeJob()))
    with pytest.raises(IcosAccountLocked):
        client.login('ILA04', 'pw')
    settle()
    assert any(alerts.CONCURRENT_EXHAUSTED in m['subject'] for m in mailbox)


def test_a_bad_password_emails_nobody(mailbox):
    # High volume, always a typo, no diagnostic value.
    from icos import IcosBadCredentials

    class Stub(object):
        def fetch_once(self, url, data=None, timeout=8):
            if 'EUACustomLoginServlet' in url:
                return FetchResult(
                    OK, b'The userID or password could not be validated'
                        + b'x' * 9000, 200, 0.1)
            return FetchResult(OK, b'x' * 30000, 200, 0.1)

        def init_request(self):
            return 'https://x/ESAWebApp/ESALogin.jsp', None

        def login_request(self, username, password):
            return 'https://x/ESAWebApp/EUACustomLoginServlet', b'd'

    client = IcosClient(reader=Stub(), sleep=lambda s: None, monotonic=_clock(),
                        alert=alerts.emitter(FakeJob()))
    with pytest.raises(IcosBadCredentials):
        client.login('ILA04', 'wrong')
    settle()
    assert mailbox == []


def test_the_client_can_be_repointed_at_another_job(mailbox):
    # The CRS job inherits the search job's live session.
    client = IcosClient(reader=_reader_that('concurrent'),
                        concurrent_budget_seconds=1, sleep=lambda s: None,
                        monotonic=_clock(), alert=alerts.emitter(FakeJob('search')))
    crs_job = FakeJob('crs')
    crs_job.id = '99887766aabbccdd'
    client.set_alert(alerts.emitter(crs_job))
    with pytest.raises(IcosAccountLocked):
        client.login('ILA04', 'pw')
    settle()
    assert '99887766' in mailbox[0]['text']


# -- the web hook ---------------------------------------------------------


def test_a_bug_in_a_request_path_emails_somebody(mailbox):
    import app as app_module
    from werkzeug.exceptions import NotFound

    # Registered against the real app, so this fails if the hook is ever
    # dropped rather than only if the function body changes.
    handlers = app_module.app.error_handler_spec[None][None]
    assert handlers[Exception] is app_module.unhandled

    with app_module.app.test_request_context('/job/abc123/download'):
        try:
            raise RuntimeError('boom')
        except RuntimeError as e:
            # Re-raised so Flask still produces its own 500 and still logs the
            # traceback; the alert is a side channel, not a replacement.
            with pytest.raises(RuntimeError):
                app_module.unhandled(e)
    settle()
    assert alerts.UNHANDLED in mailbox[0]['subject']
    assert '/job/abc123/download' in mailbox[0]['text']
    assert 'RuntimeError' in mailbox[0]['text']

    # A 404 is routing, not breakage.
    with app_module.app.test_request_context('/nope'):
        assert isinstance(app_module.unhandled(NotFound()), NotFound)
    settle()
    assert len(mailbox) == 1


# -- a case that will not parse -------------------------------------------


def test_a_case_that_will_not_parse_alerts_with_the_case_and_not_the_person(
        mailbox, monkeypatch):
    import case_parser
    import icos_sessions

    class Stub:
        logged_in = True

        def set_alert(self, alert):
            self.alert = alert

        def case_bundle(self, case_id):
            return b'<summary>', b'<charges>', b'<financials>'

        def logoff(self):
            pass

    def explode(body, case):
        # The shape the real parser fails in: the offending row is a value.
        row = 'TESTER, PAT Q 01/01/1900'
        raise ValueError('unreadable charge row: %s' % row)

    monkeypatch.setattr(icos_sessions, 'get', lambda token: Stub())
    monkeypatch.setattr(icos_sessions, 'close', lambda token: None)
    monkeypatch.setattr(case_parser, 'parse_case_summary', explode)

    job = FakeJob('crs')
    with pytest.raises(ValueError):
        tasks.crs_task(job, 'tok', ['1900-01-01 TESTER, PAT Q'],
                       {'1900-01-01 TESTER, PAT Q': ['01311 FECR000000']},
                       'TESTER, PAT Q', '01/01/1900', False)
    settle()

    # The failure and the digest, each on its own send thread, so arrival order
    # is whichever socket finished first.
    assert len(mailbox) == 2
    by_subject = {m['subject']: m for m in mailbox}
    failure = [m for s, m in by_subject.items() if alerts.PARSE_FAILURE in s]
    assert len(failure) == 1
    assert '01311 FECR000000' in failure[0]['text']  # public record, needed
    assert 'ValueError' in failure[0]['text']
    assert any('1 problem' in s for s in by_subject)
    for message in mailbox:
        assert 'TESTER' not in message['text']      # privileged, never sent
        assert '01/01/1900' not in message['text']

"""Alerting: does a failure nobody is watching actually reach someone.

These tests run a real HTTP server and assert on the request that arrives on it,
rather than on a mock having been called. A mock proves the code called what we
told it to call. It does not prove an alert leaves the process, which is the
only thing this feature is for.
"""

import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ['NAPIER_DISABLE_BACKGROUND'] = '1'

import alerts
import app as app_module
import jobs


class Collector(BaseHTTPRequestHandler):
    received = []
    status = 200

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        Collector.received.append({
            'body': self.rfile.read(length).decode('utf-8'),
            'title': self.headers.get('Title'),
            'priority': self.headers.get('Priority'),
            'tags': self.headers.get('Tags'),
        })
        self.send_response(Collector.status)
        self.end_headers()
        self.wfile.write(b'ok')

    def log_message(self, *args):
        pass


@pytest.fixture
def endpoint(monkeypatch):
    """A real listening socket standing in for the ntfy topic."""
    Collector.received = []
    Collector.status = 200
    server = HTTPServer(('127.0.0.1', 0), Collector)
    # serve_forever polls at half a second by default, which is half a second
    # of teardown on every test in this file.
    threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01},
                     daemon=True).start()
    monkeypatch.setenv(alerts.ALERT_URL_ENV,
                       'http://127.0.0.1:%d/napier' % server.server_port)
    alerts.reset()
    yield Collector.received
    server.shutdown()
    alerts.reset()


def test_an_alert_actually_leaves_the_process(endpoint):
    assert alerts.raise_alert('t', 'ICOS is down', 'Nothing is answering.') is True
    assert len(endpoint) == 1
    assert endpoint[0]['title'] == 'ICOS is down'
    assert endpoint[0]['body'] == 'Nothing is answering.'
    assert endpoint[0]['priority'] == 'high'


def test_a_repeat_within_the_hour_is_not_sent_again(endpoint):
    alerts.raise_alert('t', 'ICOS is down', 'first')
    for _ in range(20):
        alerts.raise_alert('t', 'ICOS is down', 'again')
    # A 20-second keepalive loop would otherwise send 180 pages an hour.
    assert len(endpoint) == 1


def test_a_lasting_failure_reminds_once_the_cooldown_passes(endpoint, monkeypatch):
    alerts.raise_alert('t', 'ICOS is down', 'first')
    monkeypatch.setattr(alerts, 'REMINDER_SECONDS', 0)
    alerts.raise_alert('t', 'ICOS is down', 'still going')
    assert len(endpoint) == 2
    assert endpoint[1]['title'] == 'ICOS is down (still)'


def test_recovery_is_announced_only_if_something_broke(endpoint):
    assert alerts.clear_alert('t', 'all clear', 'nothing was wrong') is False
    assert endpoint == []
    alerts.raise_alert('t', 'ICOS is down', 'broken')
    assert alerts.clear_alert('t', 'ICOS is back', 'recovered') is True
    assert len(endpoint) == 2
    assert endpoint[1]['title'] == 'ICOS is back'
    assert endpoint[1]['priority'] == 'default'


def test_recovery_rearms_the_alert(endpoint):
    alerts.raise_alert('t', 'down', 'a')
    alerts.clear_alert('t', 'up', 'b')
    # Second outage inside the cooldown window must still page.
    assert alerts.raise_alert('t', 'down', 'c') is True
    assert len(endpoint) == 3


def test_an_invalid_priority_is_downgraded_rather_than_dropped(endpoint):
    # ntfy accepts six priority words and silently 400s on anything else, so a
    # typo here would mean an outage page that never arrives.
    alerts.raise_alert('t', 'ICOS is down', 'body', priority='critical')
    assert endpoint[0]['priority'] == 'default'


@pytest.mark.parametrize('priority', alerts.PRIORITIES)
def test_every_documented_priority_is_passed_through(endpoint, priority):
    alerts.reset()
    alerts.raise_alert('p-%s' % priority, 't', 'b', priority=priority)
    assert endpoint[-1]['priority'] == priority


def test_no_endpoint_configured_is_a_silent_no_op(monkeypatch):
    monkeypatch.delenv(alerts.ALERT_URL_ENV, raising=False)
    alerts.reset()
    assert alerts.raise_alert('t', 'title', 'body') is False


def test_a_dead_endpoint_does_not_take_the_caller_down(monkeypatch):
    monkeypatch.setenv(alerts.ALERT_URL_ENV, 'http://127.0.0.1:1/nope')
    alerts.reset()
    assert alerts.raise_alert('t', 'title', 'body') is False
    # The condition still counts as firing, so recovery is still announced.
    assert alerts.is_firing('t')


def test_a_rejecting_endpoint_does_not_take_the_caller_down(endpoint):
    Collector.status = 500
    assert alerts.raise_alert('t', 'title', 'body') is False


# --- the two conditions actually wired up -------------------------------


@pytest.fixture
def keepalive(monkeypatch):
    """Drive the keepalive cycle with a scripted sequence of ping outcomes."""
    outcomes = []

    def fake_ping(timeout=6):
        return 0.1, outcomes.pop(0) if outcomes else True

    monkeypatch.setattr(app_module, '_keepalive_ping', fake_ping)
    return outcomes


def test_icos_going_cold_pages_someone(endpoint, keepalive):
    keepalive.extend([False] * (app_module.KEEPALIVE_BURST + 1))
    app_module._keepalive_cycle({'ok': True, 'logged_at': 0.0, 'cold_since': None})
    assert len(endpoint) == 1
    assert 'cannot reach ICOS' in endpoint[0]['title']
    assert endpoint[0]['priority'] == 'urgent'


def test_a_ping_that_recovers_inside_the_burst_pages_nobody(endpoint, keepalive):
    keepalive.extend([False, False, True])
    app_module._keepalive_cycle({'ok': True, 'logged_at': 0.0, 'cold_since': None})
    assert endpoint == []


def test_icos_coming_back_says_so_and_says_how_long(endpoint, keepalive):
    state = {'ok': True, 'logged_at': 0.0, 'cold_since': None}
    keepalive.extend([False] * (app_module.KEEPALIVE_BURST + 1))
    app_module._keepalive_cycle(state, now=1000.0)
    app_module._keepalive_cycle(state, now=1000.0 + 25 * 60)
    assert len(endpoint) == 2
    assert 'reach ICOS again' in endpoint[1]['title']
    assert '25 minutes' in endpoint[1]['body']
    assert state['cold_since'] is None


def test_a_healthy_keepalive_stops_logging_every_ping(endpoint, keepalive, capsys):
    state = {'ok': None, 'logged_at': 0.0, 'cold_since': None}
    now = 10000.0
    for _ in range(30):
        app_module._keepalive_cycle(state, now=now)
        now += app_module.KEEPALIVE_SECS
    lines = [l for l in capsys.readouterr().out.splitlines() if 'KEEPALIVE ok' in l]
    # Ten minutes of pings. One line for the first success, none after, because
    # a line every 20 seconds buries the JOB and ICOS lines in the same log.
    assert len(lines) == 1


def test_a_healthy_keepalive_still_says_so_periodically(endpoint, keepalive):
    state = {'ok': True, 'logged_at': 1000.0, 'cold_since': None}
    app_module._keepalive_cycle(state, now=1000.0 + app_module.KEEPALIVE_LOG_SECS)
    assert state['logged_at'] == 1000.0 + app_module.KEEPALIVE_LOG_SECS


def test_a_crashing_job_pages_someone(endpoint):
    def explode(job):
        raise ValueError('TESTER, PAT Q had an unparseable charge row')

    job = jobs.start('search', explode)
    for _ in range(200):
        if job.status == jobs.FAILED:
            break
        time.sleep(0.01)
    assert job.status == jobs.FAILED
    assert len(endpoint) == 1
    assert endpoint[0]['title'] == 'Napier search job crashed'
    assert 'ValueError' in endpoint[0]['body']


def test_a_crash_alert_carries_no_case_data(endpoint):
    """The endpoint is a third-party service and the payload is court records.

    An exception raised while parsing a case usually quotes that case, so the
    alert reports the exception type and nothing else from it.
    """
    def explode(job):
        raise ValueError('TESTER, PAT Q 01311 FECR000000 01/01/1900')

    job = jobs.start('crs', explode)
    for _ in range(200):
        if job.status == jobs.FAILED:
            break
        time.sleep(0.01)
    assert job.status == jobs.FAILED
    sent = endpoint[0]['title'] + endpoint[0]['body']
    for leak in ('TESTER', 'PAT Q', 'FECR000000', '01/01/1900'):
        assert leak not in sent


def test_a_handled_failure_pages_nobody(endpoint):
    """Failures already phrased for staff are the app working, not breaking."""
    class Handled(Exception):
        message = 'That user ID or password was not accepted by Iowa Courts.'

    def refuse(job):
        raise Handled()

    job = jobs.start('search', refuse)
    for _ in range(200):
        if job.status == jobs.FAILED:
            break
        time.sleep(0.01)
    assert job.status == jobs.FAILED
    assert endpoint == []

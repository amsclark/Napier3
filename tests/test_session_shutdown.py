"""Heroku restarts the dyno on every deploy and cycles it about once a day.

The session store lives in memory, so a restart forgets whatever was in it
without telling Iowa Courts. ICOS keeps holding that session, and because the
ESA account is shared, the next staffer to search is told the account is
already logged in somewhere else and waits it out. Nobody deployed anything
from their point of view. The reaper cannot help, since the reaper dies with
the process.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import icos_sessions


class FakeClient:
    def __init__(self, explodes=False):
        self.logged_off = False
        self.explodes = explodes

    def logoff(self):
        if self.explodes:
            raise RuntimeError("ICOS refused the logoff")
        self.logged_off = True


def setup_function():
    icos_sessions._sessions.clear()


def test_shutdown_logs_off_everything_it_is_holding():
    held = [FakeClient(), FakeClient(), FakeClient()]
    for client in held:
        icos_sessions.put(client)

    assert icos_sessions.close_all() == 3
    assert all(client.logged_off for client in held)
    assert icos_sessions._sessions == {}


def test_one_refused_logoff_does_not_strand_the_rest():
    """Shutdown gets a few seconds. A session that will not close is exactly
    the kind that is already broken, and letting it take the others down would
    leave the account locked for the reason we are trying to avoid."""
    good, bad = FakeClient(), FakeClient(explodes=True)
    icos_sessions.put(bad)
    icos_sessions.put(good)

    icos_sessions.close_all()
    assert good.logged_off
    assert icos_sessions._sessions == {}


def test_shutdown_with_nothing_held_is_quiet():
    assert icos_sessions.close_all() == 0


def test_sigterm_releases_sessions_before_the_server_starts_shutting_down():
    """Measured on Heroku: registering this on atexit is too late. Gunicorn
    takes its full thirty second graceful timeout, Heroku SIGKILLs at exactly
    that mark, and the handler runs in the same instant the process dies. It
    printed but never got the logoff request out, and the account stayed
    locked. SIGTERM arrives thirty seconds earlier, which is the whole budget.
    """
    import signal

    client = FakeClient()
    icos_sessions.put(client)
    served = []
    previous = signal.signal(signal.SIGTERM, lambda *a: served.append('gunicorn'))
    try:
        icos_sessions.install_shutdown_hooks()
        handler = signal.getsignal(signal.SIGTERM)
        handler(signal.SIGTERM, None)
    finally:
        signal.signal(signal.SIGTERM, previous)

    assert client.logged_off, "the session must be released on SIGTERM"
    assert served == ['gunicorn'], "gunicorn's own shutdown must still happen"
